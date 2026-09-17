# -*- coding: utf-8 -*-
import math
import re
import uuid
from html import escape

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_round


FURNITURE_STAGE_SELECTION = [
    ('priming', 'التقديم'),
    ('painting', 'تصنيع دهانات'),
    ('carpentry', 'تجميع'),
    ('bases', 'القواعد'),
    ('finishing', 'تجهيز'),
    ('tailoring', 'تفصيل'),
    ('upholstery', 'كسوه'),
    ('packaging', 'التغليف'),
]

# Historical models keep accepting the old code so completed warehouse/cost
# audit rows stay readable.  New routes, selectors and stage orders must use
# ``FURNITURE_STAGE_SELECTION`` above, which deliberately excludes sewing.
FURNITURE_LEGACY_STAGE_SELECTION = [
    *FURNITURE_STAGE_SELECTION,
    ('sewing', 'الخياطة (سجل قديم داخل التفصيل الآن)'),
]

FURNITURE_STAGE_UOM_SELECTION = [
    ('unit', 'الوحدة'),
    ('meter', 'المتر'),
    ('cubic_meter', 'متر مكعب'),
    ('cm', 'السم'),
    ('kg', 'الكيلو جرام'),
    ('gram', 'بالجرام'),
    ('liter', 'اللتر'),
    ('roll', 'لفة'),
    ('bolt', 'توب'),
    ('sheet', 'فرخ'),
    ('board', 'لوح'),
    ('box', 'علبة'),
]

FURNITURE_MATERIAL_QUANTITY_MODE_SELECTION = [
    ('scaled', 'نسبي مع المقاس'),
    ('fixed', 'كمية ثابتة'),
]

FURNITURE_STAGE_UOM_XMLIDS = {
    'unit': 'furniture_mrp.furniture_uom_unit',
    'meter': 'furniture_mrp.furniture_bom_uom_meter',
    'cubic_meter': 'furniture_mrp.furniture_bom_uom_cubic_meter',
    'cm': 'furniture_mrp.furniture_bom_uom_cm',
    'kg': 'furniture_mrp.furniture_bom_uom_kgm',
    'gram': 'furniture_mrp.furniture_bom_uom_gram',
    'liter': 'furniture_mrp.furniture_bom_uom_litre',
    'roll': 'furniture_mrp.furniture_uom_roll',
    'bolt': 'furniture_mrp.furniture_uom_bolt',
    'sheet': 'furniture_mrp.furniture_uom_sheet',
    'board': 'furniture_mrp.furniture_uom_board',
    'box': 'furniture_mrp.furniture_uom_box',
}

FURNITURE_STAGE_FIELD_MAP = {
    'priming': ('use_priming', 'priming_order_id', 'priming_state'),
    'painting': ('use_painting', 'painting_order_id', 'painting_state'),
    'carpentry': ('use_carpentry', 'carpentry_order_id', 'carpentry_state'),
    'bases': ('use_bases', 'bases_order_id', 'bases_state'),
    'finishing': ('use_finishing', 'finishing_order_id', 'finishing_state'),
    'tailoring': ('use_tailoring', 'tailoring_order_id', 'tailoring_state'),
    'upholstery': ('use_upholstery', 'upholstery_order_id', 'upholstery_state'),
    'packaging': ('use_packaging', 'packaging_order_id', 'packaging_state'),
}

# These departments prepare/consume their own materials in parallel with the
# physical furniture body.  Completing them records operational progress and
# cost, but must never relocate or duplicate the finished furniture product.
FURNITURE_MATERIAL_ONLY_STAGE_CODES = frozenset({
    'painting',
    'tailoring',
    # Kept only so archived standalone sewing orders remain safe to inspect.
    # ``sewing`` is no longer part of the active production route.
    'sewing',
})


def _normalize_uom_for_product(product, uom):
    if product and uom and uom.category_id != product.uom_id.category_id:
        return product.uom_id
    return uom


def _furniture_bom_uom_for_product(product, preferred_uom=False):
    if not product:
        return preferred_uom
    product_category = product.uom_id.category_id
    for candidate in (preferred_uom, product.uom_po_id, product.uom_id):
        if (
            candidate
            and candidate.category_id == product_category
            and candidate.furniture_mrp_bom_uom
        ):
            return candidate
    return product.env['uom.uom'].search([
        ('furniture_mrp_bom_uom', '=', True),
        ('category_id', '=', product_category.id),
    ], order='id', limit=1)


def _normalize_purchase_uom_for_product(product, uom=False):
    if not product:
        return uom
    furniture_uom = _furniture_bom_uom_for_product(product, uom)
    if furniture_uom:
        return furniture_uom
    fallback_uom = product.uom_po_id or product.uom_id
    if fallback_uom.category_id != product.uom_id.category_id:
        fallback_uom = product.uom_id
    if uom and uom.category_id == product.uom_id.category_id:
        return uom
    return fallback_uom


class FurnitureMrpProductionOrder(models.Model):
    _name = 'furniture.mrp.production'
    _description = 'أمر التشغيل الأسبوعي - مصنع الأثاث'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'
    _rec_name = 'name'

    # ─── الحقول الأساسية ──────────────────────────────────────────────────────
    name = fields.Char(
        string='رقم أمر التشغيل الأسبوعي',
        readonly=True, default='جديد', copy=False, tracking=True,
    )
    product_id = fields.Many2one(
        'product.product', string='منتج الخطة الأسبوعية',
        domain="[('furniture_has_active_normal_recipe', '=', True), ('furniture_dimension_source_product_id', '=', False)]",
        tracking=True,
    )
    furniture_order_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        tracking=True,
        copy=True,
        help='الموديل المطلوب إنتاجه؛ لكل منتج وموديل ريسيبي تصنيع مستقل.',
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري (الشركة)',
        domain=[('is_company', '=', True)],
        tracking=True,
        copy=True,
        index=True,
        ondelete='restrict',
        help='المشتري الموحد لكل أصناف أمر الإنتاج.',
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        tracking=True,
        copy=True,
        index=True,
        ondelete='restrict',
        help='المستفيد الموحد لكل أصناف أمر الإنتاج.',
    )
    product_dimension_label = fields.Char(
        string='المقاس',
        related='product_id.furniture_dimension_label',
        readonly=True,
    )
    product_base_name = fields.Char(
        string='اسم المنتج الأساسي',
        copy=False,
    )
    product_template_id = fields.Many2one(
        'product.template', related='product_id.product_tmpl_id', store=True,
    )
    product_qty = fields.Float(
        string='الكمية', default=1.0, required=True, tracking=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', related='product_id.uom_id', store=True,
    )
    bom_width_cm = fields.Float(
        string='عرض الريسيبي (سم)', related='bom_id.furniture_width_cm', readonly=True,
    )
    bom_depth_cm = fields.Float(
        string='عمق الريسيبي (سم)', related='bom_id.furniture_depth_cm', readonly=True,
    )
    bom_height_cm = fields.Float(
        string='ارتفاع الريسيبي (سم)', related='bom_id.furniture_height_cm', readonly=True,
    )
    width_cm = fields.Float(string='العرض (سم)', tracking=True)
    depth_cm = fields.Float(string='العمق (سم)', tracking=True)
    height_cm = fields.Float(string='الارتفاع (سم)', tracking=True)
    dimension_factor = fields.Float(
        string='معامل المقاس',
        compute='_compute_dimension_factor',
        digits=(16, 3),
    )
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )
    responsible_id = fields.Many2one(
        'res.users', string='مدير الإنتاج',
        default=lambda self: self.env.user, tracking=True,
    )

    # ─── BoM والمكونات ────────────────────────────────────────────────────────
    bom_id = fields.Many2one(
        'mrp.bom', string='ريسيبي التشغيل',
        domain="[('furniture_product_id', '=', product_id), ('furniture_model_id', '=', furniture_order_model_id), ('type', '=', 'normal')]",
        tracking=True,
    )
    production_line_ids = fields.One2many(
        'furniture.mrp.production.line', 'production_id',
        string='أصناف أمر التشغيل',
        domain=[('active', '=', True)],
    )
    production_line_count = fields.Integer(
        string='عدد الأصناف',
        compute='_compute_production_line_count',
    )
    buyer_partner_ids = fields.Many2many(
        'res.partner',
        'furniture_mrp_production_buyer_partner_rel',
        'production_id',
        'partner_id',
        string='المشترون',
        compute='_compute_customer_partner_ids',
        store=True,
        copy=False,
        readonly=True,
    )
    beneficiary_partner_ids = fields.Many2many(
        'res.partner',
        'furniture_mrp_production_beneficiary_partner_rel',
        'production_id',
        'partner_id',
        string='المستفيدون',
        compute='_compute_customer_partner_ids',
        store=True,
        copy=False,
        readonly=True,
    )
    stage_plan_mode = fields.Selection([
        ('recipe', 'حسب الريسيبي'),
        ('custom', 'اختيار هذا الأسبوع'),
    ], string='مراحل أمر الأسبوع', default='recipe', tracking=True)

    stage_plan_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='الصنف المطلوب تعديل مراحله',
        domain="[('production_id', '=', id)]",
        copy=False,
    )
    material_display_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='الصنف المطلوب عرض خاماته',
        domain="[('production_id', '=', id), ('active', '=', True)]",
        copy=False,
        help='اختار صنفًا من أمر الإنتاج لعرض خامات ريسيبيه فقط داخل تبويب مكونات الإنتاج.',
    )
    stage_plan_line_summary = fields.Char(
        string='مراحل الصنف الحالية',
        related='stage_plan_line_id.stage_summary',
        readonly=True,
    )
    line_use_priming = fields.Boolean(string='التقديم', copy=False)
    line_use_painting = fields.Boolean(string='تصنيع دهانات', copy=False)
    line_use_carpentry = fields.Boolean(string='تجميع', copy=False)
    line_use_bases = fields.Boolean(string='القواعد', copy=False)
    line_use_finishing = fields.Boolean(string='تجهيز', copy=False)
    line_use_tailoring = fields.Boolean(string='تفصيل', copy=False)
    line_use_sewing = fields.Boolean(string='الخياطة', copy=False)
    line_use_upholstery = fields.Boolean(string='كسوه', copy=False)
    line_use_packaging = fields.Boolean(string='التغليف', copy=False)
    dimension_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='الصنف المطلوب تعديل مقاساته',
        domain="[('production_id', '=', id)]",
        copy=False,
    )
    dimension_line_bom_width_cm = fields.Float(
        string='عرض الريسيبي (سم)',
        related='dimension_line_id.bom_width_cm',
        readonly=True,
    )
    dimension_line_bom_depth_cm = fields.Float(
        string='عمق الريسيبي (سم)',
        related='dimension_line_id.bom_depth_cm',
        readonly=True,
    )
    dimension_line_bom_height_cm = fields.Float(
        string='ارتفاع الريسيبي (سم)',
        related='dimension_line_id.bom_height_cm',
        readonly=True,
    )
    dimension_line_width_cm = fields.Float(string='العرض (سم)', copy=False)
    dimension_line_depth_cm = fields.Float(string='العمق (سم)', copy=False)
    dimension_line_height_cm = fields.Float(string='الارتفاع (سم)', copy=False)
    dimension_line_factor = fields.Float(
        string='معامل المقاس',
        compute='_compute_dimension_line_factor',
        digits=(16, 3),
    )
    use_priming = fields.Boolean(string='التقديم', default=True, tracking=True)
    use_painting = fields.Boolean(string='تصنيع دهانات', default=True, tracking=True)
    use_carpentry = fields.Boolean(string='تجميع', default=True, tracking=True)
    use_bases = fields.Boolean(string='القواعد', default=True, tracking=True)
    use_finishing = fields.Boolean(string='تجهيز', default=True, tracking=True)
    use_tailoring = fields.Boolean(string='تفصيل', default=True, tracking=True)
    use_sewing = fields.Boolean(
        string='الخياطة (مرحلة مستقلة قديمة)',
        default=False,
        tracking=True,
        help='حقل توافق للسجلات القديمة؛ الخياطة أصبحت مرحلة داخل التفصيل.',
    )
    use_upholstery = fields.Boolean(string='كسوه', default=True, tracking=True)
    use_packaging = fields.Boolean(string='التغليف', default=True, tracking=True)
    material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='مكونات الإنتاج',
    )
    # Use a distinct, server-filtered one2many field for every stage in the
    # production form.  View domains on an existing one2many only constrain
    # candidate rows on the client; they do not reliably trim the already read
    # relation when ``material_display_line_id`` changes.  Computing these
    # fields also avoids sharing one x2many data point between nine widgets.
    priming_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات التقديم',
        compute='_compute_stage_material_display_lines',
    )
    painting_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات تصنيع دهانات',
        compute='_compute_stage_material_display_lines',
    )
    carpentry_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات التجميع',
        compute='_compute_stage_material_display_lines',
    )
    bases_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات القواعد',
        compute='_compute_stage_material_display_lines',
    )
    finishing_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات التجهيز',
        compute='_compute_stage_material_display_lines',
    )
    tailoring_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات التفصيل',
        compute='_compute_stage_material_display_lines',
    )
    sewing_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات الخياطة',
        compute='_compute_stage_material_display_lines',
    )
    upholstery_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات الكسوة',
        compute='_compute_stage_material_display_lines',
    )
    packaging_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_id',
        string='خامات التغليف',
        compute='_compute_stage_material_display_lines',
    )
    material_availability = fields.Selection([
        ('none', 'لم يُفحص'),
        ('available', 'متاح ✅'),
        ('partial', 'متاح جزئياً ⚠️'),
        ('unavailable', 'غير متاح ❌'),
    ], string='توفر المواد', default='none', compute='_compute_material_availability', store=True)

    # ─── مواقع المخزون ────────────────────────────────────────────────────────
    location_src_id = fields.Many2one(
        'stock.location', string='مخزن المواد الخام',
        default=lambda self: self.env.ref('stock.stock_location_stock', raise_if_not_found=False),
    )
    location_wip_id = fields.Many2one(
        'stock.location', string='موقع WIP (صالة التصنيع)',
        default=lambda self: self.env.ref('furniture_mrp.location_furniture_wip', raise_if_not_found=False),
    )
    location_priming_id = fields.Many2one(
        'stock.location', string='مخزن التقديم',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_priming', raise_if_not_found=False),
    )
    location_priming_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع التقديم',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_priming_wip', raise_if_not_found=False),
    )
    location_painting_id = fields.Many2one(
        'stock.location', string='مخزن تصنيع دهانات',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_painting', raise_if_not_found=False),
    )
    location_painting_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع دهانات',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_painting_wip', raise_if_not_found=False),
    )
    location_carpentry_id = fields.Many2one(
        'stock.location', string='مخزن تجميع',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_carpentry', raise_if_not_found=False),
    )
    location_carpentry_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع تجميع',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_carpentry_wip', raise_if_not_found=False),
    )
    location_bases_id = fields.Many2one(
        'stock.location', string='مخزن القواعد',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_bases', raise_if_not_found=False),
    )
    location_bases_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع القواعد',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_bases_wip', raise_if_not_found=False),
    )
    location_upholstery_id = fields.Many2one(
        'stock.location', string='مخزن كسوه',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_upholstery', raise_if_not_found=False),
    )
    location_upholstery_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع كسوه',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_upholstery_wip', raise_if_not_found=False),
    )
    location_finishing_id = fields.Many2one(
        'stock.location', string='مخزن تجهيز',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_finishing', raise_if_not_found=False),
    )
    location_finishing_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع تجهيز',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_finishing_wip', raise_if_not_found=False),
    )
    location_tailoring_id = fields.Many2one(
        'stock.location', string='مخزن تفصيل',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_tailoring', raise_if_not_found=False),
    )
    location_tailoring_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع تفصيل',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_tailoring_wip', raise_if_not_found=False),
    )
    location_sewing_id = fields.Many2one(
        'stock.location', string='مخزن الخياطة (قديم)',
        help='مرجع تاريخي فقط؛ الخياطة أصبحت داخل مرحلة التفصيل.',
    )
    location_sewing_wip_id = fields.Many2one(
        'stock.location', string='صالة تصنيع الخياطة (قديمة)',
        help='مرجع تاريخي فقط؛ لا يُستخدم في أوامر الإنتاج الجديدة.',
    )
    location_packaging_id = fields.Many2one(
        'stock.location', string='مخزن/منطقة التغليف',
        default=lambda self: self.env.ref('furniture_mrp.location_stage_packaging', raise_if_not_found=False),
    )
    location_dest_id = fields.Many2one(
        'stock.location', string='بضاعة تم إنتاجها',
        default=lambda self: self.env.ref('furniture_mrp.location_finished_goods', raise_if_not_found=False),
    )
    stock_move_ids = fields.Many2many(
        'stock.move',
        string='حركات المخزون',
        compute='_compute_stock_move_ids',
    )

    # ─── الحساب التحليلي (Overhead) ──────────────────────────────────────────
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='الحساب التحليلي (Overhead)',
        tracking=True,
        help='يُستخدم لربط مصروفات الخزينة بأمر التشغيل الأسبوعي لحساب تكلفة الـ Overhead',
    )

    # ─── التواريخ ─────────────────────────────────────────────────────────────
    date_planned_start = fields.Datetime(string='تاريخ البدء المخطط', tracking=True)
    date_planned_finish = fields.Datetime(string='تاريخ الانتهاء المخطط', tracking=True)
    date_start = fields.Datetime(string='تاريخ البدء الفعلي', readonly=True)
    date_finish = fields.Datetime(string='تاريخ الانتهاء الفعلي', readonly=True)

    # ─── الحالة (8 مراحل + WIP) ───────────────────────────────────────────────
    state = fields.Selection([
        ('draft',       'مسودة'),
        ('confirmed',   'مؤكد - جاهز للإنتاج'),
        ('in_production', 'جاري التصنيع'),
        ('priming',     '1⃣ التقديم'),
        ('painting',    '2⃣ تصنيع دهانات'),
        ('carpentry',   '3⃣ تجميع'),
        ('bases',       '4⃣ القواعد'),
        ('finishing',   '5⃣ تجهيز'),
        ('tailoring',   '6⃣ تفصيل'),
        ('upholstery',  '7⃣ كسوه'),
        ('packaging',   '8⃣ التغليف'),
        ('done',        '✅ منتهي - أمر الأسبوع مغلق'),
        ('cancelled',   'ملغي'),
    ], string='الحالة', default='draft', tracking=True, index=True)

    priority = fields.Selection([
        ('0', 'عادي'), ('1', 'مهم'), ('2', 'عاجل'),
    ], default='0', tracking=True)
    dashboard_sequence = fields.Integer(
        string='ترتيب الأهمية في لوحة التحكم',
        default=10,
        copy=False,
        index=True,
        help=(
            'يحفظ الترتيب اليدوي لأوامر الإنتاج داخل لوحة التحكم فقط، '
            'ولا يغير ترتيب قائمة أوامر التشغيل الأسبوعية.'
        ),
    )

    # ─── ربط أوامر الأقسام ───────────────────────────────────────────────────
    priming_order_id   = fields.Many2one('furniture.mrp.priming',    string='أمر التقديم',   readonly=True, copy=False)
    painting_order_id  = fields.Many2one('furniture.mrp.painting',   string='أمر تصنيع دهانات', readonly=True, copy=False)
    carpentry_order_id = fields.Many2one('furniture.mrp.carpentry',  string='أمر تجميع',         readonly=True, copy=False)
    bases_order_id     = fields.Many2one('furniture.mrp.bases',      string='أمر القواعد',        readonly=True, copy=False)
    finishing_order_id = fields.Many2one('furniture.mrp.finishing',  string='أمر تجهيز',         readonly=True, copy=False)
    tailoring_order_id = fields.Many2one('furniture.mrp.tailoring',  string='أمر تفصيل',         readonly=True, copy=False)
    sewing_order_id    = fields.Many2one('furniture.mrp.sewing',     string='أمر الخياطة',        readonly=True, copy=False)
    upholstery_order_id= fields.Many2one('furniture.mrp.upholstery', string='أمر كسوه',          readonly=True, copy=False)
    packaging_order_id = fields.Many2one('furniture.mrp.packaging',  string='أمر التغليف',       readonly=True, copy=False)

    # ─── حالات الأقسام ───────────────────────────────────────────────────────
    priming_state    = fields.Selection(related='priming_order_id.state',    store=True, string='حالة التقديم')
    painting_state   = fields.Selection(related='painting_order_id.state',   store=True, string='حالة تصنيع دهانات')
    carpentry_state  = fields.Selection(related='carpentry_order_id.state',  store=True, string='حالة تجميع')
    bases_state      = fields.Selection(related='bases_order_id.state',      store=True, string='حالة القواعد')
    finishing_state  = fields.Selection(related='finishing_order_id.state',  store=True, string='حالة تجهيز')
    tailoring_state  = fields.Selection(related='tailoring_order_id.state',  store=True, string='حالة تفصيل')
    tailoring_display_state = fields.Selection(
        related='tailoring_order_id.tailoring_display_state',
        string='حالة عرض التفصيل',
        readonly=True,
    )
    sewing_state     = fields.Selection(related='sewing_order_id.state',     store=True, string='حالة الخياطة')
    upholstery_state = fields.Selection(related='upholstery_order_id.state', store=True, string='حالة كسوه')
    packaging_state  = fields.Selection(related='packaging_order_id.state',  store=True, string='حالة التغليف')
    painting_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    carpentry_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    bases_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    finishing_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    tailoring_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    sewing_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    upholstery_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    packaging_transfer_ready = fields.Boolean(compute='_compute_stage_transfer_ready_flags')
    can_start_priming = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_painting = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_carpentry = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_bases = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_finishing = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_tailoring = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_sewing = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_upholstery = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_start_packaging = fields.Boolean(compute='_compute_can_start_stage_flags')
    can_choose_start_stage = fields.Boolean(
        string='يمكن اختيار مرحلة البداية',
        compute='_compute_can_start_stage_flags',
    )
    can_mark_done = fields.Boolean(
        string='جاهز لإغلاق أمر الأسبوع',
        compute='_compute_can_mark_done',
        store=True,
    )
    can_transfer_finished_product = fields.Boolean(
        string='جاهز للتحويل للمخزن التام',
        compute='_compute_can_transfer_finished_product',
    )
    finished_transfer_done = fields.Boolean(
        string='تم التحويل للمخزن التام',
        readonly=True,
        copy=False,
    )
    finished_transfer_line_ids = fields.One2many(
        'furniture.mrp.finished.transfer.line',
        'production_id',
        string='المنتجات الجاهزة للتحويل للمخزن التام',
        copy=False,
    )
    carryover_line_ids = fields.One2many(
        'furniture.mrp.carryover.line',
        'production_id',
        string='الشغل القديم المكمل مع الأمر',
        copy=False,
    )
    stage_cost_entry_ids = fields.One2many(
        'furniture.mrp.stage.cost.entry',
        'production_id',
        string='سجل التكلفة الفعلية للمراحل',
        copy=False,
    )

    # ─── ربط MPS ──────────────────────────────────────────────────────────────
    mps_id = fields.Many2one('furniture.mrp.mps', string='الخطة الأسبوعية (MPS)', ondelete='set null', copy=False)
    mps_line_id = fields.Many2one(
        'furniture.mrp.mps.line',
        string='سطر الخطة الأسبوعية',
        ondelete='set null',
        copy=False,
    )

    # ─── التكاليف ─────────────────────────────────────────────────────────────
    material_cost  = fields.Float(string='خامات معتمدة حتى الآن', compute='_compute_costs', store=True, digits=(16, 2))
    labor_cost     = fields.Float(string='عمالة معتمدة حتى الآن', compute='_compute_costs', store=True, digits=(16, 2))
    overhead_cost  = fields.Float(string='تكلفة Overhead', compute='_compute_costs', store=True, digits=(16, 2))
    total_cost     = fields.Float(string='إجمالي التكلفة الفعلية', compute='_compute_costs', store=True, digits=(16, 2))
    cost_per_unit  = fields.Float(
        string='متوسط تكلفة الوحدة الحالية',
        help='إجمالي التكلفة الفعلية المعتمدة ÷ كمية الدفعات التي اجتازت الجودة.',
        compute='_compute_costs',
        store=True,
        digits=(16, 2),
    )
    actual_costed_quantity = fields.Float(
        string='الكمية المعتمدة بالتكلفة',
        compute='_compute_costs',
        store=True,
        digits=(16, 3),
    )
    cost_distribution_html = fields.Html(
        string='توزيع التكلفة على أصناف الإنتاج',
        compute='_compute_cost_distribution_html',
        sanitize=False,
        readonly=True,
    )

    # ─── التقدم ───────────────────────────────────────────────────────────────
    progress = fields.Integer(string='نسبة التقدم (%)', compute='_compute_progress', store=True)
    stage_completion_summary = fields.Char(
        string='ملخص المراحل',
        compute='_compute_stage_summary',
    )
    stage_current_stage = fields.Char(
        string='المرحلة الحالية/التالية',
        compute='_compute_stage_summary',
    )
    header_product_summary = fields.Text(
        string='ملخص الأصناف',
        compute='_compute_header_product_summary',
    )
    header_stage_summary = fields.Char(
        string='ملخص الأقسام والمراحل',
        compute='_compute_header_stage_summary',
    )
    stage_completed_stages = fields.Text(
        string='المراحل المكتملة',
        compute='_compute_stage_summary',
    )
    stage_pending_stages = fields.Text(
        string='المراحل المتبقية',
        compute='_compute_stage_summary',
    )

    # ─── ملاحظات ──────────────────────────────────────────────────────────────
    notes = fields.Html(string='ملاحظات')
    color = fields.Integer(default=0)
    purchase_order_ids = fields.Many2many(
        'purchase.order', string='طلبات الشراء',
        relation='furniture_mrp_purchase_rel', column1='production_id', column2='purchase_id',
    )
    purchase_count = fields.Integer(compute='_compute_purchase_count', string='طلبات الشراء')
    missing_vendor_purchase_order_id = fields.Many2one(
        'purchase.order',
        string='طلب الشراء بدون مورد',
        copy=False,
        readonly=True,
    )

    # ─── Computed ─────────────────────────────────────────────────────────────
    @api.depends(
        'state',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'priming_state', 'painting_state', 'carpentry_state', 'bases_state',
        'finishing_state', 'tailoring_state', 'sewing_state', 'upholstery_state', 'packaging_state',
    )
    def _compute_progress(self):
        pmap = {
            'draft': 0, 'confirmed': 5, 'in_production': 10, 'priming': 18, 'painting': 28,
            'carpentry': 38, 'bases': 48, 'finishing': 58, 'tailoring': 72,
            'upholstery': 86, 'packaging': 95, 'done': 100, 'cancelled': 0,
        }
        for rec in self:
            if rec.state == 'in_production':
                stage_states = [state for _code, _order, state in rec._required_stage_infos()]
                stage_count = len(stage_states) or 1
                done_count = len([state for state in stage_states if state == 'done'])
                started_count = len([state for state in stage_states if state])
                done_step = 85 / stage_count
                rec.progress = min(95, int(10 + done_count * done_step + max(0, started_count - done_count) * 2))
            else:
                rec.progress = pmap.get(rec.state, 0)

    @api.depends(
        'state',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'priming_order_id', 'painting_order_id', 'carpentry_order_id', 'bases_order_id',
        'finishing_order_id', 'tailoring_order_id', 'sewing_order_id', 'upholstery_order_id', 'packaging_order_id',
        'priming_state', 'painting_state', 'carpentry_state', 'bases_state',
        'finishing_state', 'tailoring_state', 'sewing_state', 'upholstery_state', 'packaging_state',
    )
    def _compute_can_mark_done(self):
        for rec in self:
            rec.can_mark_done = (
                rec.state not in ('draft', 'done', 'cancelled')
                and not rec._has_running_stage_orders()
            )

    @api.depends(
        'state',
        'finished_transfer_done',
        'finished_transfer_line_ids.state',
        'finished_transfer_line_ids.qty',
        'finished_transfer_line_ids.product_id',
        'finished_transfer_line_ids.product_uom_id',
        'finished_transfer_line_ids.source_kind',
        'finished_transfer_line_ids.source_production_line_id',
        'carryover_line_ids.state',
        'carryover_line_ids.current_stage',
        'carryover_line_ids.product_id',
        'carryover_line_ids.qty',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'priming_order_id', 'painting_order_id', 'carpentry_order_id', 'bases_order_id',
        'finishing_order_id', 'tailoring_order_id', 'sewing_order_id', 'upholstery_order_id', 'packaging_order_id',
        'priming_state', 'painting_state', 'carpentry_state', 'bases_state',
        'finishing_state', 'tailoring_state', 'sewing_state', 'upholstery_state', 'packaging_state',
    )
    def _compute_can_transfer_finished_product(self):
        for rec in self:
            if rec.state in ('draft', 'confirmed', 'cancelled'):
                rec.can_transfer_finished_product = False
                continue
            runtime_cache = {}
            regular_ready = rec._get_finished_transfer_current_payloads(runtime_cache=runtime_cache)
            carryover_ready = []
            if not regular_ready:
                carryover_ready = rec._get_finished_transfer_carryover_payloads(
                    current_payloads=regular_ready,
                    runtime_cache=runtime_cache,
                )
            rec.can_transfer_finished_product = bool(regular_ready or carryover_ready)

    @api.depends(
        'use_painting', 'use_carpentry', 'use_bases', 'use_finishing',
        'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'painting_order_id.state', 'carpentry_order_id.state', 'bases_order_id.state', 'finishing_order_id.state',
        'tailoring_order_id.state', 'sewing_order_id.state', 'upholstery_order_id.state', 'packaging_order_id.state',
        'stock_move_ids.state', 'stock_move_ids.location_id', 'stock_move_ids.location_dest_id',
        'stock_move_ids.product_id',
    )
    def _compute_stage_transfer_ready_flags(self):
        for rec in self:
            rec.painting_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.painting')
            rec.carpentry_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.carpentry')
            rec.bases_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.bases')
            rec.finishing_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.finishing')
            rec.tailoring_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.tailoring')
            rec.sewing_transfer_ready = False
            rec.upholstery_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.upholstery')
            rec.packaging_transfer_ready = rec._has_manual_transfer_for_stage('furniture.mrp.packaging')

    @api.depends(
        'state',
        'production_line_ids.active',
        'production_line_ids.product_id',
        'production_line_ids.product_qty',
        'production_line_ids.first_stage_started',
        'production_line_ids.first_stage_started_stage',
        'production_line_ids.planned_start_stage',
        'production_line_ids.use_priming', 'production_line_ids.use_painting',
        'production_line_ids.use_carpentry', 'production_line_ids.use_bases', 'production_line_ids.use_finishing',
        'production_line_ids.use_tailoring', 'production_line_ids.use_sewing', 'production_line_ids.use_upholstery',
        'production_line_ids.use_packaging',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'priming_order_id', 'painting_order_id', 'carpentry_order_id', 'bases_order_id',
        'finishing_order_id', 'tailoring_order_id', 'sewing_order_id', 'upholstery_order_id', 'packaging_order_id',
    )
    def _compute_can_start_stage_flags(self):
        for rec in self:
            rec.can_start_priming = rec._can_start_stage_code('priming')
            rec.can_start_painting = rec._can_start_stage_code('painting')
            rec.can_start_carpentry = rec._can_start_stage_code('carpentry')
            rec.can_start_bases = rec._can_start_stage_code('bases')
            rec.can_start_finishing = rec._can_start_stage_code('finishing')
            rec.can_start_tailoring = rec._can_start_stage_code('tailoring')
            rec.can_start_sewing = False
            rec.can_start_upholstery = rec._can_start_stage_code('upholstery')
            rec.can_start_packaging = rec._can_start_stage_code('packaging')
            rec.can_choose_start_stage = bool(rec._get_startable_stage_codes())

    @api.depends('production_line_ids', 'production_line_ids.active')
    def _compute_production_line_count(self):
        for rec in self:
            rec.production_line_count = len(rec.production_line_ids)

    @api.depends(
        'production_line_ids.active',
        'production_line_ids.buyer_partner_id',
        'production_line_ids.beneficiary_partner_id',
    )
    def _compute_customer_partner_ids(self):
        for rec in self:
            active_lines = rec.production_line_ids.filtered('active')
            rec.buyer_partner_ids = active_lines.mapped('buyer_partner_id')
            rec.beneficiary_partner_ids = active_lines.mapped('beneficiary_partner_id')

    @api.depends(
        'product_id', 'bom_id',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'priming_state', 'painting_state', 'carpentry_state', 'bases_state',
        'finishing_state', 'tailoring_state', 'sewing_state', 'upholstery_state', 'packaging_state',
    )
    def _compute_stage_summary(self):
        for rec in self:
            payload = rec._stage_completion_payload()
            total = payload['total'] or 0
            done = payload['done'] or 0
            product_name = rec._get_dimension_display_name()
            stage_word = _('مرحلة') if total == 1 else _('مراحل')
            rec.stage_completion_summary = _('%s - مكتمل %s من %s %s') % (product_name, done, total, stage_word)
            rec.stage_current_stage = payload['current'] or _('لا توجد مرحلة حالية')
            rec.stage_completed_stages = ', '.join(payload['completed']) if payload['completed'] else _('لا توجد مراحل مكتملة بعد')
            rec.stage_pending_stages = ', '.join(payload['pending']) if payload['pending'] else _('لا توجد مراحل متبقية')

    @api.depends(
        'production_line_ids',
        'production_line_ids.active',
        'production_line_ids.product_id',
        'production_line_ids.product_qty',
        'production_line_ids.width_cm',
        'production_line_ids.depth_cm',
        'production_line_ids.height_cm',
        'production_line_ids.use_priming',
        'production_line_ids.use_painting',
        'production_line_ids.use_carpentry',
        'production_line_ids.use_bases',
        'production_line_ids.use_finishing',
        'production_line_ids.use_tailoring',
        'production_line_ids.use_sewing',
        'production_line_ids.use_upholstery',
        'production_line_ids.use_packaging',
        'priming_order_id.completed_production_line_ids_data',
        'painting_order_id.completed_production_line_ids_data',
        'carpentry_order_id.completed_production_line_ids_data',
        'bases_order_id.completed_production_line_ids_data',
        'finishing_order_id.completed_production_line_ids_data',
        'tailoring_order_id.completed_production_line_ids_data',
        'sewing_order_id.completed_production_line_ids_data',
        'upholstery_order_id.completed_production_line_ids_data',
        'packaging_order_id.completed_production_line_ids_data',
        'product_id',
        'product_qty',
    )
    def _compute_header_product_summary(self):
        for rec in self:
            if rec.production_line_ids:
                lines = rec._get_sorted_production_lines().filtered(
                    lambda line: line.product_id and rec._line_has_remaining_stage_work(line)
                )
                rec.header_product_summary = (
                    rec._build_production_lines_summary(lines)
                    or (rec.product_id.display_name if rec.product_id else False)
                )
            else:
                rec.header_product_summary = rec._get_dimension_display_name() if rec.product_id else False

    def _build_production_lines_summary(self, production_lines):
        self.ensure_one()
        totals_by_label = {}
        ordered_labels = []
        labels = []
        for line in production_lines:
            if not line.product_id:
                continue
            label = self._get_production_line_text_label(line)
            if label not in totals_by_label:
                totals_by_label[label] = 0.0
                ordered_labels.append(label)
            totals_by_label[label] += line.product_qty or 0.0
        for label in ordered_labels:
            labels.append('%s × %s' % (label, self._format_dimension_value(totals_by_label[label])))
        return '\n'.join(labels)

    def _line_has_remaining_stage_work(self, line):
        self.ensure_one()
        if not line or line.production_id != self:
            return False
        selected_stage_codes = line._selected_stage_codes()
        if not selected_stage_codes:
            return True
        for stage_code in selected_stage_codes:
            stage_order = self._stage_order_record(stage_code)
            if not stage_order:
                return True
            # Header summaries are computed during ordinary production reads.
            # A stage-scoped supervisor must not gain access to another
            # stage's order just to render that harmless summary; conservatively
            # keep the line visible as remaining when that order is hidden.
            if not stage_order.has_access('read'):
                return True
            completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
            if line not in completed_lines:
                return True
        return False

    @api.depends(
        'state',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'priming_order_id', 'painting_order_id', 'carpentry_order_id', 'bases_order_id',
        'finishing_order_id', 'tailoring_order_id', 'sewing_order_id', 'upholstery_order_id', 'packaging_order_id',
        'priming_state', 'painting_state', 'carpentry_state', 'bases_state',
        'finishing_state', 'tailoring_state', 'sewing_state', 'upholstery_state', 'packaging_state',
    )
    def _compute_header_stage_summary(self):
        for rec in self:
            payload = rec._stage_completion_payload()
            total = payload['total'] or 0
            done = payload['done'] or 0
            current = payload['current'] or _('لا توجد مرحلة حالية')
            if total:
                rec.header_stage_summary = _('%s - مكتمل %s من %s مراحل') % (current, done, total)
            else:
                rec.header_stage_summary = current

    @api.depends('material_line_ids.qty_available', 'material_line_ids.qty_needed')
    def _compute_material_availability(self):
        for rec in self:
            lines = rec.material_line_ids
            if not lines:
                rec.material_availability = 'none'
                continue
            available = all(l.qty_available >= l.qty_needed for l in lines)
            none_available = all(l.qty_available == 0 for l in lines)
            if available:
                rec.material_availability = 'available'
            elif none_available:
                rec.material_availability = 'unavailable'
            else:
                rec.material_availability = 'partial'

    @api.depends(
        'material_display_line_id',
        'material_line_ids',
        'material_line_ids.stage',
        'material_line_ids.production_line_id',
    )
    def _compute_stage_material_display_lines(self):
        """Expose only the selected product line's recipe rows in each tab."""
        field_by_stage = {
            'priming': 'priming_material_line_ids',
            'painting': 'painting_material_line_ids',
            'carpentry': 'carpentry_material_line_ids',
            'bases': 'bases_material_line_ids',
            'finishing': 'finishing_material_line_ids',
            'tailoring': 'tailoring_material_line_ids',
            'sewing': 'sewing_material_line_ids',
            'upholstery': 'upholstery_material_line_ids',
            'packaging': 'packaging_material_line_ids',
        }
        empty_lines = self.env['furniture.mrp.material.line']
        for rec in self:
            selected_line = rec.material_display_line_id
            selected_materials = (
                rec.material_line_ids.filtered(
                    lambda material: material.production_line_id == selected_line
                )
                if selected_line
                else empty_lines
            )
            materials_by_stage = {
                stage: empty_lines
                for stage in field_by_stage
            }
            for material in selected_materials:
                if material.stage in materials_by_stage:
                    materials_by_stage[material.stage] |= material
            for stage, field_name in field_by_stage.items():
                rec[field_name] = materials_by_stage[stage]

    def _get_purchase_material_unit_cost(self, product, uom=False):
        self.ensure_one()
        if not product:
            return 0.0

        product = product.with_company(self.company_id)
        uom = _normalize_purchase_uom_for_product(product, uom or product.uom_id)
        stock_move_model = self.env['stock.move']
        if 'purchase_line_id' not in stock_move_model._fields:
            base_cost = product.standard_price or 0.0
            if uom and uom != product.uom_id:
                return product.uom_id._compute_price(base_cost, uom)
            return base_cost

        purchase_moves = self.env['stock.move'].sudo().search([
            ('purchase_line_id', '!=', False),
            ('product_id', '=', product.id),
            ('company_id', '=', self.company_id.id),
            ('state', '=', 'done'),
            ('location_id.usage', '=', 'supplier'),
            ('location_dest_id.usage', '!=', 'supplier'),
        ], order='date desc, id desc')

        total_qty = 0.0
        total_value = 0.0
        for move in purchase_moves:
            if move.purchase_line_id:
                move.purchase_line_id.sudo()._furniture_normalize_product_uom()
            move_prices = move._get_price_unit() or {}
            if not move_prices:
                continue
            if product.lot_valuated:
                lot_qty_map = {}
                for move_line in move.move_line_ids:
                    if not move_line.lot_id:
                        continue
                    lot_qty_map[move_line.lot_id.id] = lot_qty_map.get(move_line.lot_id.id, 0.0) + move_line.quantity_product_uom
                for lot, unit_cost in move_prices.items():
                    lot_id = getattr(lot, 'id', False)
                    lot_qty = lot_qty_map.get(lot_id, 0.0) or move.product_qty or 0.0
                    if not lot_qty:
                        continue
                    total_qty += lot_qty
                    total_value += unit_cost * lot_qty
            else:
                unit_cost = next(iter(move_prices.values()), 0.0)
                qty = move.product_qty or 0.0
                if not qty:
                    continue
                total_qty += qty
                total_value += unit_cost * qty

        base_cost = (total_value / total_qty) if total_qty else (product.standard_price or 0.0)
        if uom and uom != product.uom_id:
            base_cost = product.uom_id._compute_price(base_cost, uom)
        return base_cost

    def _record_stage_costs(self, stage_order, stage_code, production_lines=False, carryover_payloads=False):
        """Freeze actual batch costs once quality accepts a stage."""
        self.ensure_one()
        if not stage_order or not stage_code:
            return self.env['furniture.mrp.stage.cost.entry']

        CostEntry = self.env['furniture.mrp.stage.cost.entry'].sudo()
        batches = []
        seen_line_ids = set()
        finished_product_cache = {}
        for line in (production_lines or self.env['furniture.mrp.production.line']).exists():
            if not line.product_id or not line.product_qty or line.id in seen_line_ids:
                continue
            product_key = (
                line.product_id.id,
                line.furniture_order_model_id.id,
                round(line.width_cm or 0.0, 6),
                round(line.depth_cm or 0.0, 6),
                round(line.height_cm or 0.0, 6),
            )
            if product_key not in finished_product_cache:
                finished_product_cache[product_key] = (
                    self._get_or_create_dimensioned_finished_product_for_line(line)
                    or line.product_id
                )
            product = finished_product_cache[product_key]
            batches.append({
                'line': line,
                'product': product,
                'quantity': line.product_qty,
                'uom': line.product_uom_id or product.uom_id,
                'carryover_lines': self.env['furniture.mrp.carryover.line'],
            })
            seen_line_ids.add(line.id)
        for payload in carryover_payloads or []:
            line = payload.get('source_production_line')
            product = payload.get('product')
            quantity = payload.get('qty') or 0.0
            if not line or not product or quantity <= 0 or line.id in seen_line_ids:
                continue
            batches.append({
                'line': line,
                'product': product,
                'quantity': quantity,
                'uom': payload.get('uom') or product.uom_id,
                'carryover_lines': payload.get('line_ids') or self.env['furniture.mrp.carryover.line'],
            })
            seen_line_ids.add(line.id)

        existing_cost_line_ids = set(CostEntry.search([
            ('production_line_id', 'in', [batch['line'].id for batch in batches]),
            ('stage', '=', stage_code),
        ]).mapped('production_line_id').ids)
        batches = [
            batch for batch in batches
            if batch['line'].id not in existing_cost_line_ids
        ]
        if not batches:
            return CostEntry

        existing_stage_labor = sum(CostEntry.search([
            ('stage_order_model', '=', stage_order._name),
            ('stage_order_res_id', '=', stage_order.id),
        ]).mapped('stage_labor_cost'))
        labor_to_allocate = max((stage_order.total_labor_cost or 0.0) - existing_stage_labor, 0.0)
        total_batch_qty = sum(batch['quantity'] for batch in batches)
        # Freeze every previous-stage snapshot before any current-stage entry
        # exists.  Otherwise the first Kit piece created in this loop can be
        # mistaken for inherited previous cost by the following pieces.
        previous_snapshot_by_line_id = self._get_production_line_cost_snapshots(
            self.env['furniture.mrp.production.line'].browse([
                batch['line'].id for batch in batches
            ]),
        )
        stage_material_lines = self.material_line_ids.filtered(lambda material_line: (
            material_line.stage == stage_code
            and material_line.product_id
            and material_line.qty_needed > 0
        ))
        material_lines_by_production_line_id = {}
        material_lines_by_carryover_line_id = {}
        for material_line in stage_material_lines:
            if material_line.production_line_id:
                material_lines_by_production_line_id.setdefault(
                    material_line.production_line_id.id,
                    self.env['furniture.mrp.material.line'],
                )
                material_lines_by_production_line_id[
                    material_line.production_line_id.id
                ] |= material_line
            if material_line.carryover_line_id:
                material_lines_by_carryover_line_id.setdefault(
                    material_line.carryover_line_id.id,
                    self.env['furniture.mrp.material.line'],
                )
                material_lines_by_carryover_line_id[
                    material_line.carryover_line_id.id
                ] |= material_line
        material_unit_cost_cache = {}
        accepted_at = fields.Datetime.now()
        create_vals_list = []
        for batch in batches:
            line = batch['line']
            quantity = batch['quantity']
            previous_snapshot = previous_snapshot_by_line_id[line.id]
            previous_material_unit = previous_snapshot['material_unit']
            previous_labor_unit = previous_snapshot['labor_unit']
            previous_material = previous_material_unit * quantity
            previous_labor = previous_labor_unit * quantity

            material_lines = material_lines_by_production_line_id.get(
                line.id,
                self.env['furniture.mrp.material.line'],
            )
            for carryover_line in batch['carryover_lines']:
                material_lines |= material_lines_by_carryover_line_id.get(
                    carryover_line.id,
                    self.env['furniture.mrp.material.line'],
                )
            stage_material = 0.0
            for material_line in material_lines:
                material_uom = material_line.product_uom_id or material_line.product_id.uom_id
                cost_key = (material_line.product_id.id, material_uom.id)
                if cost_key not in material_unit_cost_cache:
                    material_unit_cost_cache[cost_key] = self._get_purchase_material_unit_cost(
                        material_line.product_id,
                        material_uom,
                    )
                material_cost_qty = (
                    material_line.warehouse_received_qty
                    if material_line.warehouse_receipt_confirmed
                    else material_line.qty_needed
                )
                stage_material += (
                    material_unit_cost_cache[cost_key]
                    * material_cost_qty
                )
            stage_labor = (
                labor_to_allocate * quantity / total_batch_qty
                if total_batch_qty else 0.0
            )
            create_vals_list.append({
                'production_id': self.id,
                'production_line_id': line.id,
                'stage': stage_code,
                'stage_order_model': stage_order._name,
                'stage_order_res_id': stage_order.id,
                'stage_order_name': stage_order.name,
                'product_id': batch['product'].id,
                'quantity': quantity,
                'product_uom_id': batch['uom'].id,
                'previous_material_cost': previous_material,
                'previous_labor_cost': previous_labor,
                'stage_material_cost': stage_material,
                'stage_labor_cost': stage_labor,
                'cumulative_material_cost': previous_material + stage_material,
                'cumulative_labor_cost': previous_labor + stage_labor,
                'accepted_at': accepted_at,
                'accepted_by_id': self.env.user.id,
            })
        return CostEntry.create(create_vals_list)

    def action_view_stage_cost_entries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('تفصيل التكلفة حسب المنتج والمرحلة'),
            'res_model': 'furniture.mrp.stage.cost.entry',
            'view_mode': 'list,pivot,graph,form',
            'domain': [('production_id', '=', self.id)],
            'context': {
                'default_production_id': self.id,
                'search_default_group_product': 1,
            },
        }

    def action_print_cost_report(self):
        """Print the approved, work-to-date production cost report."""
        self.ensure_one()
        return self.env.ref(
            'furniture_mrp.action_report_furniture_mrp_cost_details'
        ).report_action(self, config=False)

    def get_cost_report_stage_rows(self):
        """Return one report row per selected or created production stage."""
        self.ensure_one()
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        rows = []
        for stage_code, (use_field, order_field, _state_field) in FURNITURE_STAGE_FIELD_MAP.items():
            stage_order = self[order_field]
            if not self[use_field] and not stage_order:
                continue
            entries = self.stage_cost_entry_ids.filtered(lambda entry: entry.stage == stage_code)
            quantity = sum(entries.mapped('quantity'))
            stage_material = sum(entries.mapped('stage_material_cost'))
            stage_labor = sum(entries.mapped('stage_labor_cost'))
            carried_material = sum(entries.mapped('previous_material_cost'))
            carried_labor = sum(entries.mapped('previous_labor_cost'))
            cumulative = sum(entries.mapped('cumulative_total_cost'))
            state_label = _('لم تبدأ')
            quality_label = '-'
            if stage_order:
                state_selection = dict(stage_order._fields['state'].selection)
                quality_selection = dict(stage_order._fields['quality_check'].selection)
                state_label = state_selection.get(stage_order.state, stage_order.state or '')
                quality_label = quality_selection.get(
                    stage_order.quality_check,
                    stage_order.quality_check or '-',
                )
            rows.append({
                'code': stage_code,
                'label': stage_labels.get(stage_code, stage_code),
                'order': stage_order,
                'state_label': state_label,
                'quality_label': quality_label,
                'quantity': quantity,
                'carried_material': carried_material,
                'carried_labor': carried_labor,
                'stage_material': stage_material,
                'stage_labor': stage_labor,
                'stage_total': stage_material + stage_labor,
                'cumulative_total': cumulative,
                'unit_cost': cumulative / quantity if quantity else 0.0,
                'labor_hours': stage_order.total_labor_hours if stage_order else 0.0,
                'waiting_hours': stage_order.waiting_labor_hours if stage_order else 0.0,
                'waiting_cost': stage_order.waiting_labor_cost if stage_order else 0.0,
                'workers': ', '.join(stage_order.worker_user_ids.mapped('name')) if stage_order else '',
            })
        return rows

    def get_cost_report_product_rows(self):
        """Return one concise weighted row per product/model/dimension/customer."""
        self.ensure_one()
        grouped_rows = {}
        entries = self.stage_cost_entry_ids.sorted(
            self._stage_cost_entry_sort_key,
            reverse=True,
        )
        for line in self._get_sorted_production_lines():
            if not line.product_id or float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) <= 0:
                continue
            family = self._get_production_line_cost_family(line)
            latest = entries.filtered(lambda entry: entry.production_line_id in family)[:1]
            snapshot = self._get_production_line_cost_snapshot(line)
            completed_codes = self._production_line_completed_stage_codes(line)
            selected_codes = line._selected_stage_codes()
            product = latest.product_id if latest else line.product_id
            dimension = latest.dimension_label if latest else line.dimension_label
            furniture_model = line.furniture_order_model_id or product.furniture_model_id
            buyer = line.buyer_partner_id
            beneficiary = line.beneficiary_partner_id
            key = (
                product.id,
                furniture_model.id,
                dimension or '',
                buyer.id,
                beneficiary.id,
            )
            row = grouped_rows.setdefault(key, {
                'line': line,
                'entry': latest,
                'product': product,
                'model': furniture_model,
                'dimension': dimension,
                'buyer': buyer,
                'beneficiary': beneficiary,
                'quantity': 0.0,
                'planned_quantity': 0.0,
                'completed_count': 0,
                'stage_count': 0,
                'material_cost': 0.0,
                'labor_cost': 0.0,
                'overhead_cost': 0.0,
                'total_cost': 0.0,
                'unit_cost': 0.0,
            })
            row['planned_quantity'] += line.product_qty or 0.0
            row['quantity'] += line.product_qty if snapshot['has_cost'] else 0.0
            row['completed_count'] = max(row['completed_count'], len(completed_codes))
            row['stage_count'] = max(row['stage_count'], len(selected_codes))
            row['material_cost'] += snapshot['material']
            row['labor_cost'] += snapshot['labor']

        rows = list(grouped_rows.values())
        total_costed_quantity = sum(row['quantity'] for row in rows)
        overhead_per_unit = (
            (self.overhead_cost or 0.0) / total_costed_quantity
            if total_costed_quantity else 0.0
        )
        for row in rows:
            row['overhead_cost'] = overhead_per_unit * row['quantity']
            row['total_cost'] = (
                row['material_cost'] + row['labor_cost'] + row['overhead_cost']
            )
            row['unit_cost'] = (
                row['total_cost'] / row['quantity'] if row['quantity'] else 0.0
            )
        return rows

    @api.depends(
        'stage_cost_entry_ids.stage_material_cost',
        'stage_cost_entry_ids.stage_labor_cost',
        'stage_cost_entry_ids.cumulative_material_cost',
        'stage_cost_entry_ids.cumulative_labor_cost',
        'stage_cost_entry_ids.quantity',
        'stage_cost_entry_ids.product_id',
        'stage_cost_entry_ids.production_line_id',
        'stage_cost_entry_ids.accepted_at',
        'production_line_ids.active',
        'production_line_ids.sequence',
        'production_line_ids.product_id',
        'production_line_ids.product_qty',
        'production_line_ids.furniture_order_model_id',
        'production_line_ids.buyer_partner_id',
        'production_line_ids.beneficiary_partner_id',
        'production_line_ids.width_cm',
        'production_line_ids.depth_cm',
        'production_line_ids.height_cm',
        'production_line_ids.inherited_material_cost_per_unit',
        'production_line_ids.inherited_labor_cost_per_unit',
        'overhead_cost',
    )
    def _compute_cost_distribution_html(self):
        """Render a live distribution from quality-approved production costs.

        The legacy costing screen rebuilt material cost from stock moves and
        divided it by the single header quantity.  Weekly furniture orders can
        contain several products and split Kit pieces, so the approved stage
        ledger and ``get_cost_report_product_rows`` are the only quantity-safe
        source of truth.
        """
        for production in self:
            rows = production.get_cost_report_product_rows()
            if not rows:
                production.cost_distribution_html = (
                    '<div class="o_furniture_cost_distribution_empty">'
                    '<i class="fa fa-info-circle"></i>'
                    '<div><strong>%s</strong><span>%s</span></div>'
                    '</div>'
                ) % (
                    escape(_('لا توجد تكلفة إنتاج معتمدة حتى الآن.')),
                    escape(_(
                        'تظهر الأرقام تلقائيًا بعد قبول الجودة لأول دفعة في أي مرحلة.'
                    )),
                )
                continue

            currency = production.company_id.currency_id
            currency_symbol = escape(currency.symbol or currency.name or '')
            body_rows = []
            for row in rows:
                completed_count = row['completed_count'] or 0
                stage_count = row['stage_count'] or 0
                if stage_count and completed_count >= stage_count:
                    progress_class = 'done'
                    progress_label = _('مكتمل')
                elif completed_count:
                    progress_class = 'progress'
                    progress_label = _('جاري')
                else:
                    progress_class = 'pending'
                    progress_label = _('لم يبدأ')

                customer_parts = [
                    partner.display_name
                    for partner in (row['buyer'], row['beneficiary'])
                    if partner
                ]
                customer_label = ' / '.join(customer_parts) or '-'
                model_label = row['model'].display_name if row['model'] else '-'
                body_rows.append(
                    '<tr>'
                    '<td><div class="o_furniture_cost_product">'
                    '<span class="fa fa-cube"></span><strong>%s</strong>'
                    '<small>%s</small></div></td>'
                    '<td>%s</td>'
                    '<td class="o_furniture_cost_number">%s</td>'
                    '<td class="o_furniture_cost-number-strong">%s</td>'
                    '<td><span class="o_furniture_cost_progress '
                    'o_furniture_cost_progress--%s">%s · %s/%s</span></td>'
                    '<td class="o_furniture_cost_money">%s <small>%s</small></td>'
                    '<td class="o_furniture_cost_money">%s <small>%s</small></td>'
                    '<td class="o_furniture_cost_money">%s <small>%s</small></td>'
                    '<td class="o_furniture_cost_money o_furniture_cost_total">%s <small>%s</small></td>'
                    '<td class="o_furniture_cost_money o_furniture_cost_unit">%s <small>%s</small></td>'
                    '</tr>' % (
                        escape(row['product'].display_name),
                        escape(model_label),
                        escape(customer_label),
                        escape('{:,.3f}'.format(row['planned_quantity'] or 0.0)),
                        escape('{:,.3f}'.format(row['quantity'] or 0.0)),
                        progress_class,
                        escape(progress_label),
                        completed_count,
                        stage_count,
                        escape('{:,.2f}'.format(row['material_cost'] or 0.0)),
                        currency_symbol,
                        escape('{:,.2f}'.format(row['labor_cost'] or 0.0)),
                        currency_symbol,
                        escape('{:,.2f}'.format(row['overhead_cost'] or 0.0)),
                        currency_symbol,
                        escape('{:,.2f}'.format(row['total_cost'] or 0.0)),
                        currency_symbol,
                        escape('{:,.2f}'.format(row['unit_cost'] or 0.0)),
                        currency_symbol,
                    )
                )

            total_planned_quantity = sum(
                row['planned_quantity'] or 0.0 for row in rows
            )
            total_costed_quantity = sum(
                row['quantity'] or 0.0 for row in rows
            )
            total_material_cost = sum(
                row['material_cost'] or 0.0 for row in rows
            )
            total_labor_cost = sum(
                row['labor_cost'] or 0.0 for row in rows
            )
            total_overhead_cost = sum(
                row['overhead_cost'] or 0.0 for row in rows
            )
            distributed_total = sum(
                row['total_cost'] or 0.0 for row in rows
            )
            distributed_unit_cost = (
                distributed_total / total_costed_quantity
                if total_costed_quantity else 0.0
            )
            footer_html = (
                '<tfoot><tr>'
                '<td colspan="2"><strong>%s</strong></td>'
                '<td class="o_furniture_cost_number">%s</td>'
                '<td class="o_furniture_cost-number-strong">%s</td>'
                '<td></td>'
                '<td class="o_furniture_cost_money">%s <small>%s</small></td>'
                '<td class="o_furniture_cost_money">%s <small>%s</small></td>'
                '<td class="o_furniture_cost_money">%s <small>%s</small></td>'
                '<td class="o_furniture_cost_money o_furniture_cost_total">%s <small>%s</small></td>'
                '<td class="o_furniture_cost_money o_furniture_cost_unit">%s <small>%s</small></td>'
                '</tr></tfoot>'
            ) % (
                escape(_('إجمالي التوزيع')),
                escape('{:,.3f}'.format(total_planned_quantity)),
                escape('{:,.3f}'.format(total_costed_quantity)),
                escape('{:,.2f}'.format(total_material_cost)),
                currency_symbol,
                escape('{:,.2f}'.format(total_labor_cost)),
                currency_symbol,
                escape('{:,.2f}'.format(total_overhead_cost)),
                currency_symbol,
                escape('{:,.2f}'.format(distributed_total)),
                currency_symbol,
                escape('{:,.2f}'.format(distributed_unit_cost)),
                currency_symbol,
            )

            production.cost_distribution_html = (
                '<section class="o_furniture_cost_distribution">'
                '<div class="o_furniture_cost_distribution_header">'
                '<div><span>%s</span><strong>%s</strong></div>'
                '<div class="o_furniture_cost_distribution_source">'
                '<i class="fa fa-check-circle"></i>%s</div></div>'
                '<div class="table-responsive">'
                '<table class="o_furniture_cost_distribution_table">'
                '<thead><tr>'
                '<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>'
                '<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>'
                '</tr></thead><tbody>%s</tbody>%s</table></div></section>'
            ) % (
                escape(_('توزيع التكلفة على الإنتاج الفعلي')),
                escape(_('كل صنف يأخذ تكلفته المعتمدة وكمية دفعاته الصحيحة')),
                escape(_('المصدر: قبول الجودة بالمراحل')),
                escape(_('الصنف / الموديل')),
                escape(_('المشتري / المستفيد')),
                escape(_('المخطط')),
                escape(_('المعتمد')),
                escape(_('تقدم المراحل')),
                escape(_('الخامات')),
                escape(_('العمالة')),
                escape(_('Overhead')),
                escape(_('إجمالي الصنف')),
                escape(_('تكلفة الوحدة')),
                ''.join(body_rows),
                footer_html,
            )

    def get_cost_report_entries(self):
        """Return ledger entries ordered by route, then acceptance time."""
        self.ensure_one()
        stage_rank = {
            stage_code: index
            for index, (stage_code, _label) in enumerate(FURNITURE_STAGE_SELECTION)
        }
        return self.stage_cost_entry_ids.sorted(lambda entry: (
            stage_rank.get(entry.stage, 99),
            entry.accepted_at or fields.Datetime.now(),
            self._stable_record_sort_id(entry),
        ))

    @api.depends(
        'stage_cost_entry_ids.stage_material_cost',
        'stage_cost_entry_ids.stage_labor_cost',
        'stage_cost_entry_ids.quantity',
        'stage_cost_entry_ids.production_line_id',
        'stage_cost_entry_ids.accepted_at',
        'production_line_ids.active',
        'production_line_ids.product_qty',
        'production_line_ids.inherited_material_cost_per_unit',
        'production_line_ids.inherited_labor_cost_per_unit',
        'analytic_account_id',
    )
    def _compute_costs(self):
        for rec in self:
            entries = rec.stage_cost_entry_ids
            mat = sum(entries.mapped('stage_material_cost'))
            lab = sum(entries.mapped('stage_labor_cost'))
            ovh = 0.0
            if rec.analytic_account_id:
                analytic_lines = self.env['account.analytic.line'].search([
                    ('account_id', '=', rec.analytic_account_id.id),
                    ('amount', '<', 0),
                ])
                ovh = abs(sum(analytic_lines.mapped('amount')))

            total = mat + lab + ovh
            rec.material_cost = mat
            rec.labor_cost = lab
            rec.overhead_cost = ovh
            rec.total_cost = total
            actual_qty = 0.0
            covered_line_ids = set()
            for production_line in rec._get_sorted_production_lines():
                family = rec._get_production_line_cost_family(production_line)
                covered_line_ids.update(family.ids)
                family_entries = entries.filtered(lambda entry: entry.production_line_id in family)
                if family_entries or production_line.inherited_material_cost_per_unit or production_line.inherited_labor_cost_per_unit:
                    actual_qty += production_line.product_qty or 0.0
            external_entries = entries.filtered(
                lambda entry: entry.production_line_id.id not in covered_line_ids
            ).sorted(rec._stage_cost_entry_sort_key, reverse=True)
            latest_external_by_line = {}
            for entry in external_entries:
                latest_external_by_line.setdefault(entry.production_line_id.id, entry)
            actual_qty += sum(entry.quantity for entry in latest_external_by_line.values())
            rec.actual_costed_quantity = actual_qty
            rec.cost_per_unit = total / actual_qty if actual_qty else 0.0

    def _compute_purchase_count(self):
        for rec in self:
            rec.purchase_count = len(rec.purchase_order_ids)

    def _compute_stock_move_ids(self):
        StockMove = self.env['stock.move'].sudo()
        for rec in self:
            moves = rec.material_line_ids.mapped('move_id')
            if rec.name and rec.name != 'جديد':
                moves |= StockMove.search([('origin', '=', rec.name), ('state', '!=', 'cancel')])
            rec.stock_move_ids = moves

    @api.model
    def _get_stage_parent_location(self):
        warehouse = self.env.ref('stock.warehouse0', raise_if_not_found=False)
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search([], limit=1)
        if warehouse and warehouse.view_location_id:
            return warehouse.view_location_id
        return self.env.ref('stock.stock_location_locations', raise_if_not_found=False)

    @api.model
    def _setup_stage_location_hierarchy(self):
        parent_location = self._get_stage_parent_location()
        if not parent_location:
            return False
        stage_locations = {
            'furniture_mrp.location_furniture_wip': 'WIP - تحت التشغيل',
            'furniture_mrp.location_material_handover': 'عهدة خامات بانتظار استلام الإنتاج',
            'furniture_mrp.location_stage_priming_wip': 'صالة تصنيع التقديم',
            'furniture_mrp.location_stage_priming': 'مخزن مرحلة التقديم',
            'furniture_mrp.location_stage_painting_wip': 'صالة تصنيع دهانات',
            'furniture_mrp.location_stage_painting': 'مخزن مرحلة تصنيع دهانات',
            'furniture_mrp.location_stage_carpentry_wip': 'صالة تصنيع تجميع',
            'furniture_mrp.location_stage_carpentry': 'مخزن مرحلة تجميع',
            'furniture_mrp.location_stage_bases_wip': 'صالة تصنيع القواعد',
            'furniture_mrp.location_stage_bases': 'مخزن مرحلة القواعد',
            'furniture_mrp.location_stage_finishing_wip': 'صالة تصنيع تجهيز',
            'furniture_mrp.location_stage_finishing': 'مخزن مرحلة تجهيز',
            'furniture_mrp.location_stage_tailoring_wip': 'صالة تصنيع تفصيل',
            'furniture_mrp.location_stage_tailoring': 'مخزن مرحلة تفصيل',
            'furniture_mrp.location_stage_upholstery_wip': 'صالة تصنيع كسوه',
            'furniture_mrp.location_stage_upholstery': 'مخزن مرحلة كسوه',
            'furniture_mrp.location_stage_packaging': 'مخزن/منطقة التغليف',
            'furniture_mrp.location_finished_goods': 'بضاعة تم إنتاجها',
        }
        for xmlid, name in stage_locations.items():
            location = self.env.ref(xmlid, raise_if_not_found=False)
            vals = {}
            if location and location.location_id != parent_location:
                vals['location_id'] = parent_location.id
            if location and location.name != name:
                vals['name'] = name
            if vals:
                location.sudo().write(vals)
        return True

    def _ensure_stage_locations(self):
        self._setup_stage_location_hierarchy()
        defaults = {
            'location_wip_id': 'furniture_mrp.location_furniture_wip',
            'location_priming_wip_id': 'furniture_mrp.location_stage_priming_wip',
            'location_priming_id': 'furniture_mrp.location_stage_priming',
            'location_painting_wip_id': 'furniture_mrp.location_stage_painting_wip',
            'location_painting_id': 'furniture_mrp.location_stage_painting',
            'location_carpentry_wip_id': 'furniture_mrp.location_stage_carpentry_wip',
            'location_carpentry_id': 'furniture_mrp.location_stage_carpentry',
            'location_bases_wip_id': 'furniture_mrp.location_stage_bases_wip',
            'location_bases_id': 'furniture_mrp.location_stage_bases',
            'location_finishing_wip_id': 'furniture_mrp.location_stage_finishing_wip',
            'location_finishing_id': 'furniture_mrp.location_stage_finishing',
            'location_tailoring_wip_id': 'furniture_mrp.location_stage_tailoring_wip',
            'location_tailoring_id': 'furniture_mrp.location_stage_tailoring',
            'location_upholstery_wip_id': 'furniture_mrp.location_stage_upholstery_wip',
            'location_upholstery_id': 'furniture_mrp.location_stage_upholstery',
            'location_packaging_id': 'furniture_mrp.location_stage_packaging',
            'location_dest_id': 'furniture_mrp.location_finished_goods',
        }
        generic_stock_location = self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        for rec in self:
            vals = {}
            for field_name, xmlid in defaults.items():
                needs_default = not rec[field_name]
                if field_name == 'location_dest_id' and generic_stock_location:
                    needs_default = needs_default or rec[field_name] == generic_stock_location
                if needs_default:
                    location = self.env.ref(xmlid, raise_if_not_found=False)
                    if location:
                        vals[field_name] = location.id
            if vals:
                rec.write(vals)

    def _ensure_stage_locations_for_runtime(self, runtime_cache=None):
        """Run the idempotent location repair once inside one calculation."""
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = ('stage_locations_ensured', tuple(self.ids))
        if cache_key not in runtime_cache:
            self._ensure_stage_locations()
            runtime_cache[cache_key] = True
        return runtime_cache

    def _get_wip_location(self):
        self.ensure_one()
        location = self.location_wip_id or self.env.ref(
            'furniture_mrp.location_furniture_wip',
            raise_if_not_found=False,
        )
        if not location:
            location = self.env['stock.location'].search([('usage', '=', 'production')], limit=1)
        if not location:
            location = self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        if not location:
            raise UserError(_('يجب تحديد موقع WIP قبل تنفيذ حركات المخزون.'))
        if not self.location_wip_id:
            self.location_wip_id = location.id
        return location

    def _get_production_location(self):
        self.ensure_one()
        location = self.env['stock.location'].search([('usage', '=', 'production')], limit=1)
        if not location:
            location = self._get_wip_location()
        return location

    def action_repair_stock_moves(self):
        for rec in self:
            rec._ensure_no_material_receipt_waiting()
            rec._ensure_stage_locations()
            for move in rec.stock_move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
                quantity = move.product_uom_qty or rec.product_qty
                rec._finalize_stock_move(move, quantity)
            rec._ensure_raw_materials_at_first_stage()
            rec.message_post(body=_('تم تصحيح حركات المخزون وتحويل الحركات المتوقعة إلى حركات منفذة.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم التصحيح'),
                'message': _('تم تصحيح حركات المخزون لهذا الأمر.'),
                'type': 'success',
            },
        }

    # ─── ORM ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        stage_fields = set(self._stage_use_field_names())
        next_sequence_by_company = {}
        for vals in vals_list:
            if vals.get('use_sewing'):
                vals['use_tailoring'] = True
            vals['use_sewing'] = False
            if vals.get('line_use_sewing'):
                vals['line_use_tailoring'] = True
            vals['line_use_sewing'] = False
            if 'dashboard_sequence' not in vals:
                company_id = vals.get('company_id') or self.env.company.id
                if company_id not in next_sequence_by_company:
                    last_dashboard_order = self.sudo().search(
                        [('company_id', '=', company_id)],
                        order='dashboard_sequence desc, id desc',
                        limit=1,
                    )
                    next_sequence_by_company[company_id] = (
                        (last_dashboard_order.dashboard_sequence or 0) + 10
                    )
                vals['dashboard_sequence'] = next_sequence_by_company[company_id]
                next_sequence_by_company[company_id] += 10
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = self.env['ir.sequence'].next_by_code('furniture.mrp.production') or 'جديد'
            if vals.get('product_base_name'):
                vals['product_base_name'] = self._normalize_product_base_name(vals['product_base_name'])
            if vals.get('product_id') and not vals.get('product_base_name'):
                product = self.env['product.product'].browse(vals['product_id'])
                vals['product_base_name'] = self._normalize_product_base_name(product.product_tmpl_id.name or product.name)
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            rec._sync_order_common_data_to_lines(set(vals))
            if vals.get('stage_plan_mode', 'recipe') == 'recipe' and not (stage_fields & set(vals)):
                rec.with_context(furniture_skip_stage_plan_sync=True)._sync_stage_plan_from_recipe()
        return records

    def write(self, vals):
        vals = dict(vals)
        if vals.get('use_sewing'):
            vals['use_tailoring'] = True
        if 'use_sewing' in vals:
            vals['use_sewing'] = False
        if vals.get('line_use_sewing'):
            vals['line_use_tailoring'] = True
        if 'line_use_sewing' in vals:
            vals['line_use_sewing'] = False
        stage_fields = set(self._stage_use_field_names())
        editor_fields = set(self._stage_line_editor_field_map())
        dimension_editor_fields = set(self._dimension_line_editor_field_map())
        if vals.get('stage_plan_line_id') and not (editor_fields & set(vals)):
            line = self.env['furniture.mrp.production.line'].browse(vals['stage_plan_line_id'])
            vals.update(self._stage_line_editor_vals_from_line(line))
        if vals.get('dimension_line_id') and not (dimension_editor_fields & set(vals)):
            line = self.env['furniture.mrp.production.line'].browse(vals['dimension_line_id'])
            vals.update(self._dimension_line_editor_vals_from_line(line))
        sync_after_write = (
            not self.env.context.get('furniture_skip_stage_plan_sync')
            and not (stage_fields & set(vals))
            and (
                vals.get('stage_plan_mode') == 'recipe'
                or 'bom_id' in vals
                or 'production_line_ids' in vals
            )
        )
        if vals.get('product_base_name'):
            vals['product_base_name'] = self._normalize_product_base_name(vals['product_base_name'])
        if vals.get('product_id') and not vals.get('product_base_name'):
            product = self.env['product.product'].browse(vals['product_id'])
            vals['product_base_name'] = self._normalize_product_base_name(product.product_tmpl_id.name or product.name)
        result = super().write(vals)
        self._sync_order_common_data_to_lines(set(vals))
        if sync_after_write:
            self.filtered(lambda rec: rec.stage_plan_mode == 'recipe').with_context(
                furniture_skip_stage_plan_sync=True
            )._sync_stage_plan_from_recipe()
        if editor_fields & set(vals):
            self._apply_stage_line_editor_to_line()
        if dimension_editor_fields & set(vals):
            self._apply_dimension_line_editor_to_line()
        material_refresh_fields = stage_fields | {
            'stage_plan_mode', 'bom_id', 'production_line_ids',
            'product_qty', 'width_cm', 'depth_cm', 'height_cm',
        }
        if (
            not self.env.context.get('furniture_skip_material_refresh')
            and material_refresh_fields & set(vals)
        ):
            self.filtered(lambda rec: rec.state in ('draft', 'confirmed')).with_context(
                furniture_skip_material_refresh=True
            )._refresh_material_lines_for_stage_plan()
        return result

    def _sync_order_common_data_to_lines(self, changed_fields=None):
        """Keep the model and customer data owned by the production header."""
        if self.env.context.get('furniture_skip_order_common_sync'):
            return
        common_fields = {
            'furniture_order_model_id',
            'buyer_partner_id',
            'beneficiary_partner_id',
        }
        fields_to_sync = common_fields & set(changed_fields or common_fields)
        if not fields_to_sync:
            return
        for production in self:
            lines = production.production_line_ids.filtered('active')
            if not lines:
                continue
            line_vals = {
                field_name: production[field_name].id or False
                for field_name in fields_to_sync
            }
            lines.with_context(
                furniture_skip_order_common_sync=True,
            ).write(line_vals)

    @api.model
    def _stage_use_field_names(self):
        return [field_names[0] for _stage_code, field_names in FURNITURE_STAGE_FIELD_MAP.items()]

    @api.model
    def _stage_selection_vals(self, stage_codes):
        active_stage_codes = set(stage_codes)
        vals = {
            use_field: stage_code in active_stage_codes
            for stage_code, (use_field, _order_field, _state_field) in FURNITURE_STAGE_FIELD_MAP.items()
        }
        vals['use_sewing'] = False
        return vals

    @api.model
    def _stage_line_editor_field_map(self):
        return {
            'line_use_priming': 'use_priming',
            'line_use_painting': 'use_painting',
            'line_use_carpentry': 'use_carpentry',
            'line_use_bases': 'use_bases',
            'line_use_finishing': 'use_finishing',
            'line_use_tailoring': 'use_tailoring',
            'line_use_upholstery': 'use_upholstery',
            'line_use_packaging': 'use_packaging',
        }

    def _stage_line_editor_vals_from_line(self, line):
        editor_map = self._stage_line_editor_field_map()
        return {
            editor_field: bool(line and line[line_field])
            for editor_field, line_field in editor_map.items()
        }

    def _apply_stage_line_editor_to_line(self):
        editor_map = self._stage_line_editor_field_map()
        for rec in self:
            line = rec.stage_plan_line_id
            if not line:
                continue
            vals = {
                line_field: rec[editor_field]
                for editor_field, line_field in editor_map.items()
            }
            vals['stage_selection_initialized'] = True
            line.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            ).write(vals)
            rec.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            )._sync_stage_plan_from_recipe()
            if rec.state in ('draft', 'confirmed'):
                rec.with_context(furniture_skip_material_refresh=True)._refresh_material_lines_for_stage_plan()

    @api.model
    def _dimension_line_editor_field_map(self):
        return {
            'dimension_line_width_cm': 'width_cm',
            'dimension_line_depth_cm': 'depth_cm',
            'dimension_line_height_cm': 'height_cm',
        }

    def _dimension_line_editor_vals_from_line(self, line):
        editor_map = self._dimension_line_editor_field_map()
        return {
            editor_field: line[line_field] if line else 0.0
            for editor_field, line_field in editor_map.items()
        }

    @api.depends(
        'dimension_line_id',
        'dimension_line_bom_width_cm',
        'dimension_line_bom_depth_cm',
        'dimension_line_bom_height_cm',
        'dimension_line_width_cm',
        'dimension_line_depth_cm',
        'dimension_line_height_cm',
    )
    def _compute_dimension_line_factor(self):
        for rec in self:
            rec.dimension_line_factor = rec._get_dimension_line_factor()

    def _get_dimension_line_factor(self):
        self.ensure_one()
        ratios = []
        for base_value, actual_value in (
            (self.dimension_line_bom_width_cm, self.dimension_line_width_cm),
            (self.dimension_line_bom_depth_cm, self.dimension_line_depth_cm),
            (self.dimension_line_bom_height_cm, self.dimension_line_height_cm),
        ):
            if base_value and actual_value:
                ratios.append(actual_value / base_value)
        return sum(ratios) / len(ratios) if ratios else 1.0

    def _apply_dimension_line_editor_to_line(self):
        editor_map = self._dimension_line_editor_field_map()
        for rec in self:
            line = rec.dimension_line_id
            if not line:
                continue
            vals = {}
            for editor_field, line_field in editor_map.items():
                value = rec[editor_field] or 0.0
                if value < 0:
                    raise ValidationError(_('المقاسات لا يمكن أن تكون أقل من صفر.'))
                vals[line_field] = value
            line.with_context(furniture_skip_material_refresh=True).write(vals)
            if rec.state in ('draft', 'confirmed'):
                rec.with_context(furniture_skip_material_refresh=True)._refresh_material_lines_for_stage_plan()

    @api.onchange('stage_plan_line_id')
    def _onchange_stage_plan_line_id(self):
        for rec in self:
            rec.update(rec._stage_line_editor_vals_from_line(rec.stage_plan_line_id))

    @api.onchange('dimension_line_id')
    def _onchange_dimension_line_id(self):
        for rec in self:
            rec.update(rec._dimension_line_editor_vals_from_line(rec.dimension_line_id))

    @api.onchange(
        'line_use_priming', 'line_use_painting', 'line_use_carpentry', 'line_use_bases',
        'line_use_finishing', 'line_use_tailoring', 'line_use_sewing', 'line_use_upholstery',
        'line_use_packaging',
    )
    def _onchange_stage_line_editor(self):
        # Keep the editor responsive; the actual line update happens on save/apply.
        return

    @api.onchange('dimension_line_width_cm', 'dimension_line_depth_cm', 'dimension_line_height_cm')
    def _onchange_dimension_line_editor(self):
        # Keep the factor responsive; the actual line update happens on save/apply.
        return

    def _recipe_stage_codes(self):
        self.ensure_one()
        stage_codes = set()
        for line in self.production_line_ids:
            stage_codes.update(line._selected_stage_codes())
        if not stage_codes and self.bom_id:
            stage_codes.update(self.bom_id._get_active_stage_codes())
        if not stage_codes:
            stage_codes.update(stage_code for stage_code, _label in FURNITURE_STAGE_SELECTION)
        return [stage_code for stage_code, _label in FURNITURE_STAGE_SELECTION if stage_code in stage_codes]

    def _sync_stage_plan_from_recipe(self):
        for rec in self:
            rec.update(rec._stage_selection_vals(rec._recipe_stage_codes()))

    @api.constrains(
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
    )
    def _check_weekly_stage_selection(self):
        for rec in self:
            if not any(rec[field_name] for field_name in rec._stage_use_field_names()):
                raise ValidationError(_('اختار مرحلة واحدة على الأقل لأمر التشغيل الأسبوعي.'))

    @api.onchange(
        'stage_plan_mode',
        'bom_id',
        'production_line_ids',
    )
    def _onchange_weekly_stage_plan_source(self):
        for rec in self:
            if rec.stage_plan_mode == 'recipe':
                rec._sync_stage_plan_from_recipe()
            # A running order already has consumed/moved material rows. Rebuilding
            # the full one2many during an inline product addition would erase that
            # history. The newly saved line appends only its own BoM rows instead.
            if rec.state == 'in_production':
                continue
            rec._refresh_material_lines_for_stage_plan()

    @api.onchange(
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
    )
    def _onchange_weekly_stage_flags(self):
        for rec in self:
            if rec.stage_plan_mode == 'recipe':
                rec.stage_plan_mode = 'custom'
            rec._refresh_material_lines_for_stage_plan()

    def action_load_bom_stages(self):
        for rec in self:
            rec.production_line_ids.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            )._sync_stage_plan_from_bom()
            vals = rec._stage_selection_vals(rec._recipe_stage_codes())
            vals['stage_plan_mode'] = 'recipe'
            rec.with_context(furniture_skip_stage_plan_sync=True).write(vals)
        return True

    def action_load_selected_line_bom_stages(self):
        for rec in self:
            if not rec.stage_plan_line_id:
                raise UserError(_('اختار الصنف الأول من القائمة.'))
            rec.stage_plan_line_id.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            )._sync_stage_plan_from_bom()
            rec.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            )._sync_stage_plan_from_recipe()
            if rec.state in ('draft', 'confirmed'):
                rec.with_context(furniture_skip_material_refresh=True)._refresh_material_lines_for_stage_plan()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم تحديث مراحل الصنف'),
                'message': _('تم نسخ مراحل الريسيبي للصنف المختار.'),
                'type': 'success',
            },
        }

    def action_apply_selected_line_stages(self):
        for rec in self:
            if not rec.stage_plan_line_id:
                raise UserError(_('اختار الصنف الأول من القائمة.'))
            if not any(rec[field_name] for field_name in rec._stage_line_editor_field_map()):
                raise UserError(_('اختار مرحلة واحدة على الأقل للصنف المختار.'))
            rec._apply_stage_line_editor_to_line()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم تطبيق مراحل الصنف'),
                'message': _('تم تحديث مراحل الصنف المختار وخاماته.'),
                'type': 'success',
            },
        }

    def action_load_selected_line_bom_dimensions(self):
        for rec in self:
            line = rec.dimension_line_id
            if not line:
                raise UserError(_('اختار الصنف المطلوب تعديل مقاساته الأول.'))
            rec.write({
                'dimension_line_width_cm': line.bom_width_cm or 0.0,
                'dimension_line_depth_cm': line.bom_depth_cm or 0.0,
                'dimension_line_height_cm': line.bom_height_cm or 0.0,
            })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم نسخ مقاسات الريسيبي'),
                'message': _('تم تحديث مقاسات الصنف المختار وإعادة حساب خاماته.'),
                'type': 'success',
            },
        }

    def action_apply_selected_line_dimensions(self):
        for rec in self:
            if not rec.dimension_line_id:
                raise UserError(_('اختار الصنف المطلوب تعديل مقاساته الأول.'))
            rec._apply_dimension_line_editor_to_line()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم تطبيق مقاسات الصنف'),
                'message': _('تم تحديث مقاسات الصنف المختار ومعامل الخامات الخاص به.'),
                'type': 'success',
            },
        }

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            rec.bom_id = False
            rec.furniture_order_model_id = False
            if not rec.product_id:
                continue
            if not rec.product_base_name:
                rec.product_base_name = self._normalize_product_base_name(
                    rec.product_id.product_tmpl_id.name or rec.product_id.name
                )

    @api.onchange('furniture_order_model_id')
    def _onchange_furniture_order_model_id(self):
        for rec in self:
            rec.bom_id = False
            if not rec.product_id or not rec.furniture_order_model_id:
                continue
            bom = rec._find_bom_for_product(
                rec.product_id,
                rec.furniture_order_model_id,
            )
            if not bom:
                return {
                    'warning': {
                        'title': _('لا يوجد ريسيبي للموديل'),
                        'message': _(
                            'أنشئ ريسيبي للمنتج %(product)s والموديل %(model)s أولًا.'
                        ) % {
                            'product': rec.product_id.display_name,
                            'model': rec.furniture_order_model_id.display_name,
                        },
                    }
                }
            rec.bom_id = bom

    @api.onchange('bom_id')
    def _onchange_bom_id(self):
        """تحميل مكونات BoM عند تغيير قائمة المواد"""
        if self.bom_id:
            self.furniture_order_model_id = self.bom_id.furniture_model_id
        if self.bom_id and not self.production_line_ids:
            self.width_cm = self.bom_id.furniture_width_cm
            self.depth_cm = self.bom_id.furniture_depth_cm
            self.height_cm = self.bom_id.furniture_height_cm
        if self.stage_plan_mode == 'recipe':
            self._sync_stage_plan_from_recipe()
        self._refresh_material_lines_for_stage_plan()

    @api.depends('bom_width_cm', 'bom_depth_cm', 'bom_height_cm', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_factor(self):
        for rec in self:
            rec.dimension_factor = rec._get_dimension_factor()

    @api.onchange('product_qty', 'width_cm', 'depth_cm', 'height_cm')
    def _onchange_quantity_or_dimensions(self):
        for rec in self:
            rec._refresh_material_lines_for_stage_plan()

    def _get_dimension_factor(self):
        self.ensure_one()
        ratios = []
        dimension_pairs = (
            (self.bom_width_cm, self.width_cm),
            (self.bom_depth_cm, self.depth_cm),
            (self.bom_height_cm, self.height_cm),
        )
        for base_value, actual_value in dimension_pairs:
            if base_value and actual_value:
                ratios.append(actual_value / base_value)
        return sum(ratios) / len(ratios) if ratios else 1.0

    def _required_stage_codes(self):
        self.ensure_one()
        return [
            stage_code
            for stage_code, (use_field, _order_field, _state_field) in FURNITURE_STAGE_FIELD_MAP.items()
            if self[use_field]
        ]

    def _is_material_only_stage(self, stage_code):
        return stage_code in FURNITURE_MATERIAL_ONLY_STAGE_CODES

    def _physical_stage_codes(self, stage_codes=None):
        self.ensure_one()
        return [
            stage_code
            for stage_code in (
                self._required_stage_codes()
                if stage_codes is None else stage_codes
            )
            if not self._is_material_only_stage(stage_code)
        ]

    def _required_stage_infos(self):
        self.ensure_one()
        infos = []
        for stage_code in self._required_stage_codes():
            field_names = FURNITURE_STAGE_FIELD_MAP.get(stage_code)
            if not field_names:
                continue
            _use_field, order_field, state_field = field_names
            infos.append((stage_code, self[order_field], self[state_field]))
        return infos

    def _production_line_matches_product(self, line, product):
        self.ensure_one()
        if not line or not product:
            return False
        dimension_label = self._get_production_line_dimension_label(line)
        line_source_product = line.product_id.furniture_dimension_source_product_id or line.product_id
        if product.furniture_dimension_label and not dimension_label:
            return False
        if dimension_label and not product.furniture_dimension_label:
            return False
        if line.product_id == product:
            return True
        if dimension_label:
            if (
                product.furniture_dimension_label == dimension_label
                and (
                    product.furniture_dimension_source_product_id == line_source_product
                    or self._normalize_product_base_name(product.product_tmpl_id.name or product.name)
                    == self._get_production_line_display_name(line)
                )
            ):
                return True
            legacy_name = self._get_production_line_text_label(line)
            if product.name == legacy_name or product.product_tmpl_id.name == legacy_name:
                return True
        line_name = self._get_production_line_display_name(line)
        return (
            self._normalize_product_base_name(product.name) == line_name
            or self._normalize_product_base_name(product.product_tmpl_id.name) == line_name
        )

    def _stage_codes_for_product(self, product=False, production_line=False):
        self.ensure_one()
        if production_line:
            return production_line._selected_stage_codes()
        if product:
            matching_lines = self.production_line_ids.filtered(
                lambda line: self._production_line_matches_product(line, product)
            )
            if matching_lines:
                return [
                    stage_code
                    for stage_code, _field_names in FURNITURE_STAGE_FIELD_MAP.items()
                    if any(stage_code in line._selected_stage_codes() for line in matching_lines)
                ]
        if product and self.product_id == product:
            return self._required_stage_codes()
        bom = self._find_bom_for_stage_product(product) if product else False
        return bom._get_active_stage_codes() if bom else []

    def _stage_payload_matches_product_route(self, stage_code, payload):
        self.ensure_one()
        product = payload.get('product') if payload else False
        if not product:
            return False
        source_production = payload.get('source_production') if payload else False
        source_line = payload.get('source_production_line') if payload else False
        stage_codes = self._carryover_route_stage_codes(
            product,
            source_production=source_production,
            production_line=source_line,
        )
        return not stage_codes or stage_code in stage_codes

    def _stage_payload_can_transfer_to_stage(self, source_stage, target_stage, payload):
        self.ensure_one()
        product = payload.get('product') if payload else False
        if not product or not source_stage or not target_stage:
            return False
        if source_stage == target_stage:
            return False
        if (
            self._is_material_only_stage(source_stage)
            or self._is_material_only_stage(target_stage)
        ):
            return False
        source_production = payload.get('source_production') if payload else False
        source_line = payload.get('source_production_line') if payload else False
        stage_codes = self._carryover_route_stage_codes(
            product,
            source_production=source_production,
            production_line=source_line,
        )
        # The production-level route is the union of its line routes.  Intersect
        # with it as a safety net for legacy lines whose BoM stages were
        # accidentally restored after the user had unchecked them.
        if source_production and source_production.exists():
            production_stage_codes = source_production._required_stage_codes()
            if production_stage_codes:
                stage_codes = [
                    stage_code for stage_code in stage_codes
                    if stage_code in production_stage_codes
                ]
        if not stage_codes:
            return False
        if source_stage not in stage_codes or target_stage not in stage_codes:
            return False
        if source_line and source_line.exists():
            line_production = source_line.production_id
            completed_stage_codes = line_production._production_line_completed_stage_codes(
                source_line,
                product=product,
            )
            if target_stage in completed_stage_codes:
                return False
        return True

    def _can_start_stage_code(self, stage_code):
        self.ensure_one()
        field_names = FURNITURE_STAGE_FIELD_MAP.get(stage_code)
        if not field_names:
            return False
        use_field, order_field, _state_field = field_names
        stage_order = self[order_field]
        if stage_order:
            pending_lines = self._get_stage_pending_start_line_candidates(
                stage_order,
                stage_code,
            )
            if (
                self._is_material_only_stage(stage_code)
                and pending_lines
                and stage_order.state == 'done'
            ):
                stage_order.sudo().write({
                    'state': 'pending',
                    'quality_check': 'pending',
                    'date_finish': False,
                })
            # Heal stale stage orders left pending after their final accepted batch.
            # A stage may stay pending only when a route line or physical WIP batch
            # still exists; otherwise the dashboard would expose a phantom start.
            if (
                stage_order.state == 'pending'
                and not pending_lines
                and not self._get_stage_incomplete_line_candidates(stage_code)
                and not self._get_stage_work_location_payloads(stage_code)
            ):
                stage_order.sudo().write({
                    'state': 'done',
                    'quality_check': 'pass',
                    'date_finish': stage_order.date_finish or fields.Datetime.now(),
                })
            return False
        if self.state not in ('confirmed', 'in_production') or not self[use_field]:
            return False
        if self._is_material_only_stage(stage_code):
            return bool(self._get_material_only_stage_start_line_candidates(stage_code))
        if self._get_first_stage_start_line_candidates(stage_code):
            return True
        return any(
            self._stage_payload_belongs_to_production(payload)
            and self._stage_payload_matches_product_route(stage_code, payload)
            for payload in self._get_stage_work_location_payloads(stage_code)
        )

    def _stage_payload_belongs_to_production(self, payload):
        """Keep shared stage-hall stock scoped to this weekly order."""
        self.ensure_one()
        source_line = payload.get('source_production_line') if payload else False
        source_production = payload.get('source_production') if payload else False
        return bool(
            source_production == self
            or (source_line and source_line.production_id == self)
        )

    def _production_line_start_stage_code(self, production_line):
        """Return the real entry stage for one batch, not the route's list order."""
        self.ensure_one()
        if not production_line:
            return False
        if production_line.first_stage_started_stage:
            return production_line.first_stage_started_stage
        selected_stages = production_line._selected_stage_codes()
        physical_stages = self._physical_stage_codes(selected_stages)
        if (
            production_line.planned_start_stage
            and production_line.planned_start_stage in physical_stages
        ):
            return production_line.planned_start_stage
        return physical_stages[0] if physical_stages else False

    def _get_material_only_stage_start_line_candidates(
        self,
        stage_code,
        stage_order=False,
    ):
        """Return route lines that may run without finished-product hall stock."""
        self.ensure_one()
        candidates = self.env['furniture.mrp.production.line']
        if not self._is_material_only_stage(stage_code):
            return candidates

        excluded_lines = self.env['furniture.mrp.production.line']
        if stage_order:
            excluded_lines = (
                stage_order._get_stage_line_ids_data(
                    'completed_production_line_ids_data'
                )
                | stage_order._get_stage_line_ids_data(
                    'quality_production_line_ids_data'
                )
                | stage_order._get_stage_line_ids_data(
                    'active_production_line_ids_data'
                )
            )
        for line in self._get_sorted_production_lines():
            if (
                line in excluded_lines
                or not line.product_id
                or float_compare(
                    line.product_qty or 0.0,
                    0.0,
                    precision_digits=3,
                ) <= 0
                or stage_code not in line._selected_stage_codes()
                or self._production_line_stage_done(line, stage_code)
            ):
                continue
            candidates |= line
        return candidates

    def _get_start_stage_selector_line_candidates(self, stage_code):
        """Unstarted batches that may explicitly enter production at ``stage_code``."""
        self.ensure_one()
        candidates = self.env['furniture.mrp.production.line']
        if not stage_code:
            return candidates
        if self._is_material_only_stage(stage_code):
            return self._get_material_only_stage_start_line_candidates(
                stage_code,
                stage_order=self._stage_order_record(stage_code),
            )
        for line in self._get_sorted_production_lines():
            if (
                line.first_stage_started
                or not line.product_id
                or float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) <= 0
            ):
                continue
            selected_stages = line._selected_stage_codes()
            if stage_code not in selected_stages:
                continue
            if (
                line.planned_start_stage in selected_stages
                and not self._is_material_only_stage(line.planned_start_stage)
                and line.planned_start_stage != stage_code
            ):
                continue
            candidates |= line
        return candidates

    def _get_startable_stage_codes(self):
        """Return every selected stage that can be chosen as a batch entry point."""
        self.ensure_one()
        if self.state not in ('confirmed', 'in_production'):
            return []
        startable_codes = []
        for stage_code, _stage_label in FURNITURE_STAGE_SELECTION:
            use_field, order_field, _state_field = FURNITURE_STAGE_FIELD_MAP[stage_code]
            if not self[use_field] or self[order_field]:
                continue
            selectable_lines = self._get_start_stage_selector_line_candidates(stage_code)
            owned_work = any(
                self._stage_payload_belongs_to_production(payload)
                and self._stage_payload_matches_product_route(stage_code, payload)
                for payload in self._get_stage_work_location_payloads(stage_code)
            )
            if selectable_lines or owned_work:
                startable_codes.append(stage_code)
        return startable_codes

    def _check_start_stage_selector_access(self):
        """The entry-stage decision belongs to production management."""
        if self.env.is_superuser():
            return
        user = self.env.user
        if not (
            user.has_group('furniture_mrp.group_furniture_mrp_manager')
            or user.has_group('furniture_mrp.group_furniture_mrp_supervisor')
        ):
            raise AccessError(_(
                'اختيار مرحلة بداية أمر التصنيع متاح لمدير المصنع '
                'أو مشرف الإنتاج فقط.'
            ))

    def _ensure_stage_start_available(self, stage_code):
        self.ensure_one()
        field_names = FURNITURE_STAGE_FIELD_MAP.get(stage_code)
        if field_names and self.id:
            use_field, order_field, _state_field = field_names
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
                [self.id],
            )
            self.invalidate_recordset(['state', use_field, order_field])
            self.production_line_ids.invalidate_recordset([
                'active', 'product_id', 'product_qty',
                'first_stage_started', 'first_stage_started_stage',
                'planned_start_stage', use_field,
            ])
        if not self._can_start_stage_code(stage_code):
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code)
            raise UserError(_('لا يوجد شغل جاهز لبدء مرحلة %s في أمر التشغيل الحالي.') % stage_label)

    def _all_stage_order_infos(self):
        self.ensure_one()
        infos = []
        for stage_code, (_use_field, order_field, state_field) in FURNITURE_STAGE_FIELD_MAP.items():
            order = self[order_field]
            if order:
                infos.append((stage_code, order, self[state_field]))
        return infos

    def _selected_stage_orders_done(self):
        self.ensure_one()
        stage_infos = self._required_stage_infos()
        return bool(stage_infos) and all(order and order.state == 'done' for _code, order, _state in stage_infos)

    def _has_running_stage_orders(self):
        self.ensure_one()
        return any(
            order.state in ('in_progress', 'quality_check')
            for _code, order, _state in self._all_stage_order_infos()
        )

    def _ensure_weekly_order_can_close(self):
        self.ensure_one()
        if self._has_running_stage_orders():
            raise UserError(_('لا يمكن إغلاق أمر الأسبوع وفيه مرحلة لسه شغالة أو تحت فحص الجودة. اقفل المرحلة الأول عشان البضاعة ترجع مخزن المرحلة.'))

    def _get_finished_transfer_current_payloads(self, runtime_cache=None):
        self.ensure_one()
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        self._ensure_stage_locations_for_runtime(runtime_cache)
        if self.finished_transfer_done and self._all_current_output_lines_finished(runtime_cache=runtime_cache):
            return []

        payload_by_key = {}
        line_specs = []
        transferred_qty_by_line_key = {}
        transferred_qty_by_legacy_key = {}
        for transfer_line in self.finished_transfer_line_ids.filtered(
            lambda line: line.source_kind == 'current' and line.state == 'transferred' and line.product_id
        ):
            product = transfer_line.product_id
            product_uom = product.uom_id
            qty = self._quantity_in_product_uom(
                product,
                transfer_line.qty,
                transfer_line.product_uom_id or product_uom,
            )
            if transfer_line.source_production_line_id:
                key = (product.id, product_uom.id, transfer_line.source_production_line_id.id)
                transferred_qty_by_line_key[key] = transferred_qty_by_line_key.get(key, 0.0) + qty
            else:
                key = (product.id, product_uom.id)
                transferred_qty_by_legacy_key[key] = transferred_qty_by_legacy_key.get(key, 0.0) + qty
        started_lines = self.production_line_ids.filtered('first_stage_started')
        if self.production_line_ids and started_lines:
            for line in started_lines:
                for spec in self._get_finished_output_specs_from_lines(line, ensure_storable=False):
                    product = spec['product']
                    product_uom = product.uom_id
                    qty = self._quantity_in_product_uom(product, spec['qty'], spec['uom'])
                    line_key = (product.id, product_uom.id, line.id)
                    transferred_qty = transferred_qty_by_line_key.get(line_key, 0.0)
                    if float_compare(transferred_qty, 0.0, precision_digits=3) > 0:
                        consumed_qty = min(qty, transferred_qty)
                        qty -= consumed_qty
                        transferred_qty_by_line_key[line_key] = transferred_qty - consumed_qty
                    legacy_key = (product.id, product_uom.id)
                    transferred_qty = transferred_qty_by_legacy_key.get(legacy_key, 0.0)
                    if float_compare(qty, 0.0, precision_digits=3) > 0 and float_compare(transferred_qty, 0.0, precision_digits=3) > 0:
                        consumed_qty = min(qty, transferred_qty)
                        qty -= consumed_qty
                        transferred_qty_by_legacy_key[legacy_key] = transferred_qty - consumed_qty
                    if float_compare(qty, 0.0, precision_digits=3) <= 0:
                        continue
                    source_stage_code, source_location = self._finished_source_for_completed_line(
                        line,
                        spec['product'],
                        runtime_cache=runtime_cache,
                    )
                    if not source_stage_code or not source_location:
                        continue
                    spec = dict(spec)
                    spec['qty'] = qty
                    spec['uom'] = product_uom
                    spec['source_production_line'] = line
                    line_specs.append((spec, source_stage_code, source_location))
        else:
            if not self._selected_stage_orders_done():
                return []
            active_stage_codes = self._required_stage_codes()
            source_stage_code = active_stage_codes[-1] if active_stage_codes else False
            source_location = self._stage_storage_location(source_stage_code) if source_stage_code else self._get_production_location()
            line_specs = [
                (spec, source_stage_code, source_location)
                for spec in self._get_finished_output_specs(ensure_storable=False)
            ]

        reserved_qty_by_stock_key = {}
        for spec, source_stage_code, source_location in line_specs:
            if not source_location:
                continue
            product = spec['product']
            qty = self._quantity_in_product_uom(product, spec['qty'], spec['uom'])
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            source_line = spec.get('source_production_line')
            stock_key = (source_location.id, product.id, source_line.id if source_line else False)
            available_qty = self._stage_location_product_qty_for_line(
                source_location,
                product,
                source_line,
                source_stage_code,
                runtime_cache=runtime_cache,
            )
            available_qty -= reserved_qty_by_stock_key.get(stock_key, 0.0)
            if float_compare(
                available_qty,
                qty,
                precision_digits=3,
            ) < 0:
                continue
            uom = spec['uom'] or product.uom_id
            key = (source_stage_code, source_location.id, product.id, uom.id, source_line.id if source_line else False)
            payload = payload_by_key.setdefault(key, {
                'kind': 'current',
                'source_stage': source_stage_code,
                'source_location': source_location,
                'source_origin': self.name,
                'source_production': self,
                'source_production_line': source_line,
                'product': product,
                'uom': uom,
                'qty': 0.0,
                'label': spec['label'] or product.display_name,
            })
            payload['qty'] += qty
            reserved_qty_by_stock_key[stock_key] = reserved_qty_by_stock_key.get(stock_key, 0.0) + qty
        return list(payload_by_key.values())

    def _all_current_output_lines_finished(self, runtime_cache=None):
        self.ensure_one()
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        output_lines = self.production_line_ids.filtered(
            lambda line: line.product_id and float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) > 0
        )
        if output_lines:
            for line in output_lines:
                if not line.first_stage_started:
                    return False
                specs = self._get_finished_output_specs_from_lines(line, ensure_storable=False)
                if not specs:
                    return False
                for spec in specs:
                    if not self._production_line_all_selected_stages_done(
                        line,
                        product=spec['product'],
                        runtime_cache=runtime_cache,
                    ):
                        return False
            return True
        return self._selected_stage_orders_done()

    def _get_finished_transfer_carryover_payloads(
        self,
        include_legacy=True,
        current_payloads=None,
        runtime_cache=None,
    ):
        self.ensure_one()
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        self._ensure_stage_locations_for_runtime(runtime_cache)
        payload_by_key = {}
        reserved_qty_by_key = {}
        if current_payloads is None:
            current_payloads = self._get_finished_transfer_current_payloads(runtime_cache=runtime_cache)
        for current_payload in current_payloads:
            product = current_payload.get('product')
            source_location = current_payload.get('source_location')
            uom = current_payload.get('uom') or (product.uom_id if product else False)
            source_stage = current_payload.get('source_stage')
            if not product or not source_location or not uom or not source_stage:
                continue
            source_line = current_payload.get('source_production_line')
            reserved_key = (source_stage, source_location.id, product.id, uom.id, source_line.id if source_line else False)
            reserved_qty_by_key[reserved_key] = (
                reserved_qty_by_key.get(reserved_key, 0.0)
                + (current_payload.get('qty') or 0.0)
            )
        ready_lines = self.carryover_line_ids.filtered(
            lambda line: (
                line.state == 'stage_done'
                and line.is_final_stage
                and line.product_id
                and line.qty > 0
            )
        )
        for line in ready_lines:
            source_location = self._stage_storage_location(line.current_stage)
            if not source_location:
                continue
            uom = line.product_uom_id or line.product_id.uom_id
            reserved_key = (line.current_stage, source_location.id, line.product_id.id, uom.id, line.source_production_line_id.id)
            physical_qty = self._stage_location_product_qty(source_location, line.product_id)
            available_qty = physical_qty - reserved_qty_by_key.get(reserved_key, 0.0)
            qty = min(
                self._quantity_in_product_uom(line.product_id, line.qty, line.product_uom_id),
                max(available_qty, 0.0),
            )
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            key = (line.current_stage, line.product_id.id, uom.id, line.source_production_line_id.id)
            payload = payload_by_key.setdefault(key, {
                'kind': 'carryover',
                'source_stage': line.current_stage,
                'source_location': source_location,
                'source_origin': line.source_origin or (line.source_production_id.name if line.source_production_id else self.name),
                'source_production': line.source_production_id,
                'source_production_line': line.source_production_line_id,
                'product': line.product_id,
                'uom': uom,
                'qty': 0.0,
                'label': line.product_id.display_name,
                'line_ids': self.env['furniture.mrp.carryover.line'],
                'legacy': False,
            })
            payload['qty'] += qty
            payload['line_ids'] |= line
            reserved_qty_by_key[reserved_key] = reserved_qty_by_key.get(reserved_key, 0.0) + qty
            if not payload['source_origin'] and line.source_origin:
                payload['source_origin'] = line.source_origin

        if include_legacy:
            for legacy_payload in self._get_legacy_finished_carryover_payloads(
                reserved_qty_by_key=reserved_qty_by_key,
                current_payloads=current_payloads,
                runtime_cache=runtime_cache,
            ):
                key = (
                    legacy_payload['stage_code'],
                    legacy_payload['product'].id,
                    legacy_payload['uom'].id,
                    legacy_payload.get('source_production_line').id if legacy_payload.get('source_production_line') else False,
                    'legacy',
                )
                payload = payload_by_key.setdefault(key, {
                    'kind': 'carryover',
                    'source_stage': legacy_payload['stage_code'],
                    'source_location': legacy_payload['source_location'],
                    'source_origin': legacy_payload.get('source_origin') or self.name,
                    'source_production': legacy_payload.get('source_production'),
                    'source_production_line': legacy_payload.get('source_production_line'),
                    'product': legacy_payload['product'],
                    'uom': legacy_payload['uom'],
                    'qty': 0.0,
                    'label': legacy_payload['product'].display_name,
                    'line_ids': self.env['furniture.mrp.carryover.line'],
                    'legacy': True,
                })
                payload['qty'] += legacy_payload['qty'] or 0.0

        return [
            payload for payload in payload_by_key.values()
            if float_compare(payload['qty'], 0.0, precision_digits=3) > 0
        ]

    def _get_finished_transfer_payloads(self, include_legacy=True, runtime_cache=None):
        self.ensure_one()
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        current_payloads = self._get_finished_transfer_current_payloads(runtime_cache=runtime_cache)
        return current_payloads + self._get_finished_transfer_carryover_payloads(
            include_legacy=include_legacy,
            current_payloads=current_payloads,
            runtime_cache=runtime_cache,
        )

    def _sync_finished_transfer_lines(self):
        """تجهيز/تحديث السطور الجاهزة للتحويل للمخزن التام."""
        self.ensure_one()
        runtime_cache = {}
        candidate_payloads = self._get_finished_transfer_payloads(
            include_legacy=True,
            runtime_cache=runtime_cache,
        )
        matched_lines = self.env['furniture.mrp.finished.transfer.line']
        ready_line_by_key = {}
        for line in self.finished_transfer_line_ids.filtered(lambda item: item.state != 'transferred'):
            key = (
                line.source_kind,
                line.source_stage,
                line.product_id.id,
                line.product_uom_id.id,
                line.source_production_line_id.id,
            )
            ready_line_by_key.setdefault(key, line)
        create_vals_list = []

        for payload in candidate_payloads:
            source_kind = payload['kind']
            source_stage = payload['source_stage']
            source_line = payload.get('source_production_line') or self.env['furniture.mrp.production.line']
            product = payload['product']
            uom = payload['uom'] or product.uom_id
            line_key = (
                source_kind,
                source_stage,
                product.id,
                uom.id,
                source_line.id,
            )
            existing_line = ready_line_by_key.get(line_key)
            vals = {
                'production_id': self.id,
                'source_kind': source_kind,
                'source_stage': source_stage,
                'source_origin': payload.get('source_origin') or False,
                'source_production_id': payload.get('source_production').id if payload.get('source_production') else False,
                'source_production_line_id': source_line.id if source_line else False,
                'source_location_id': payload.get('source_location').id if payload.get('source_location') else False,
                'product_id': product.id,
                'product_uom_id': uom.id,
                'qty': payload['qty'],
            }
            if existing_line:
                needs_write = (
                    existing_line.production_id.id != vals['production_id']
                    or existing_line.source_origin != vals['source_origin']
                    or existing_line.source_production_id.id != vals['source_production_id']
                    or existing_line.source_location_id.id != vals['source_location_id']
                    or float_compare(existing_line.qty, vals['qty'], precision_digits=6) != 0
                )
                if needs_write:
                    existing_line.write(vals)
                matched_lines |= existing_line
            else:
                create_vals_list.append(vals)

        if create_vals_list:
            matched_lines |= self.env['furniture.mrp.finished.transfer.line'].create(create_vals_list)

        stale_ready_lines = self.finished_transfer_line_ids.filtered(
            lambda line: line.state == 'ready' and line not in matched_lines
        )
        if stale_ready_lines:
            stale_ready_lines.unlink()

        current_ready_lines = self.finished_transfer_line_ids.filtered(
            lambda line: line.state == 'ready' and line.source_kind == 'current'
        )
        all_current_finished = (
            self._selected_stage_orders_done()
            and self._all_current_output_lines_finished(runtime_cache=runtime_cache)
        )
        target_finished_transfer_done = bool(all_current_finished and not current_ready_lines)
        if self.finished_transfer_done != target_finished_transfer_done:
            self.write({'finished_transfer_done': target_finished_transfer_done})

        return matched_lines

    def _open_finished_product_transfer_wizard(self):
        self.ensure_one()
        self._sync_finished_transfer_lines()
        ready_lines = self.finished_transfer_line_ids.filtered(lambda line: line.state == 'ready' and line.qty > 0)
        if not ready_lines:
            raise UserError(_('لا يوجد منتج جاهز للتحويل للمخزن التام حالياً.'))

        grouped_ready_lines = {}
        for line in ready_lines:
            if line.source_kind == 'current' and line.source_production_line_id:
                family_root = self._production_line_display_family_origin(
                    line.source_production_line_id,
                )
                key = (
                    line.source_kind,
                    line.source_stage,
                    line.source_location_id.id,
                    line.product_id.id,
                    line.product_uom_id.id,
                    family_root.id,
                )
            else:
                key = ('single', line.id)
            grouped_ready_lines.setdefault(
                key,
                self.env['furniture.mrp.finished.transfer.line'],
            )
            grouped_ready_lines[key] |= line

        wizard = self.env['furniture.mrp.finished.transfer.wizard'].create({
            'production_id': self.id,
            'line_ids': [(0, 0, {
                'selected': True,
                'transfer_line_id': grouped_lines[:1].id,
                'transfer_line_ids': [(6, 0, grouped_lines.ids)],
            }) for grouped_lines in grouped_ready_lines.values()],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('تحويل المنتج للمخزن التام'),
            'res_model': 'furniture.mrp.finished.transfer.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _carryover_route_stage_codes(self, product, source_production=False, production_line=False):
        self.ensure_one()
        if production_line and production_line.exists():
            stage_codes = production_line._selected_stage_codes()
            if stage_codes:
                return stage_codes
        if source_production and source_production.exists():
            stage_codes = source_production._stage_codes_for_product(product)
            if stage_codes:
                return stage_codes
        stage_codes = self._stage_codes_for_product(product)
        if stage_codes:
            return stage_codes
        bom = self._find_bom_for_stage_product(product)
        if bom:
            stage_codes = bom._get_active_stage_codes()
            if stage_codes:
                return stage_codes
        return []

    def _production_line_carryover_history(self, production_line, product=False, runtime_cache=None):
        self.ensure_one()
        if not production_line:
            return self.env['furniture.mrp.carryover.line']
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        product_id = product.id if product else False
        cache_key = ('production_line_carryover_history', self.id, production_line.id, product_id)
        if cache_key in runtime_cache:
            return runtime_cache[cache_key]
        domain = [('state', '!=', 'finished_transferred')]
        if product:
            domain.append(('product_id', '=', product.id))
        candidates_key = ('carryover_candidates', product_id)
        if candidates_key not in runtime_cache:
            runtime_cache[candidates_key] = self.env['furniture.mrp.carryover.line'].sudo().search(
                domain,
                order='id asc',
            )
        carryover_lines = runtime_cache[candidates_key]
        matched_lines = self.env['furniture.mrp.carryover.line']
        for carryover_line in carryover_lines:
            source_line = carryover_line.source_production_line_id
            if not source_line and carryover_line.production_id:
                source_line = carryover_line.production_id._find_stage_product_source_line(
                    carryover_line.product_id,
                    carryover_line.current_stage,
                    source_production=carryover_line.source_production_id,
                    final_only=False,
                )
            if source_line == production_line:
                matched_lines |= carryover_line
        runtime_cache[cache_key] = matched_lines
        return matched_lines

    def _production_line_completed_stage_codes(
        self,
        production_line,
        product=False,
        runtime_cache=None,
        stage_codes=None,
    ):
        self.ensure_one()
        if not production_line:
            return set()
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        stage_code_scope = (
            tuple(sorted(set(stage_codes)))
            if stage_codes is not None
            else None
        )
        cache_key = (
            'production_line_completed_stage_codes',
            self.id,
            production_line.id,
            product.id if product else False,
            stage_code_scope,
        )
        if cache_key in runtime_cache:
            return set(runtime_cache[cache_key])
        completed = set()
        route_codes = production_line._selected_stage_codes()
        if stage_code_scope is not None:
            route_codes = [
                stage_code
                for stage_code in route_codes
                if stage_code in stage_code_scope
            ]
        carryover_lines = self._production_line_carryover_history(
            production_line,
            product=product,
            runtime_cache=runtime_cache,
        )
        related_productions = production_line.production_id | carryover_lines.mapped('production_id')

        for stage_code in route_codes:
            stage_carryovers = carryover_lines.filtered(
                lambda line: line.current_stage == stage_code and line.state in ('stage_done', 'finished_transferred')
            )
            if stage_carryovers:
                completed.add(stage_code)
                continue
            for production in related_productions:
                stage_order = production._stage_order_record(stage_code)
                if not stage_order:
                    continue
                completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
                if production_line in completed_lines:
                    completed.add(stage_code)
                    break
        runtime_cache[cache_key] = frozenset(completed)
        return completed

    def _production_line_all_selected_stages_done(self, production_line, product=False, runtime_cache=None):
        self.ensure_one()
        route_codes = production_line._selected_stage_codes() if production_line else []
        if not route_codes:
            return False
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = (
            'production_line_all_selected_stages_done',
            self.id,
            production_line.id,
            product.id if product else False,
        )
        if cache_key not in runtime_cache:
            completed_stage_codes = self._production_line_completed_stage_codes(
                production_line,
                product=product,
                runtime_cache=runtime_cache,
            )
            runtime_cache[cache_key] = set(route_codes).issubset(completed_stage_codes)
        return runtime_cache[cache_key]

    def _finished_source_for_completed_line(self, production_line, product, runtime_cache=None):
        self.ensure_one()
        if not production_line or not product:
            return (False, False)
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = ('finished_source_for_completed_line', self.id, production_line.id, product.id)
        if cache_key in runtime_cache:
            return runtime_cache[cache_key]
        if not self._production_line_all_selected_stages_done(
            production_line,
            product=product,
            runtime_cache=runtime_cache,
        ):
            runtime_cache[cache_key] = (False, False)
            return (False, False)
        for stage_code in reversed(production_line._selected_stage_codes()):
            source_location = self._stage_storage_location(stage_code)
            source_qty = (
                self._stage_location_product_qty_for_line(
                    source_location,
                    product,
                    production_line,
                    stage_code,
                    runtime_cache=runtime_cache,
                )
                if source_location else 0.0
            )
            if float_compare(source_qty, 0.0, precision_digits=3) <= 0:
                source_qty = self._stage_location_product_qty(source_location, product) if source_location else 0.0
                source_qty -= self._unfinished_line_qty_waiting_in_stage(
                    production_line,
                    product,
                    stage_code,
                )
            if (
                source_location
                and float_compare(
                    source_qty,
                    0.0,
                    precision_digits=3,
                ) > 0
            ):
                runtime_cache[cache_key] = (stage_code, source_location)
                return runtime_cache[cache_key]
        runtime_cache[cache_key] = (False, False)
        return (False, False)

    def _unfinished_line_qty_waiting_in_stage(self, completed_line, product, stage_code):
        self.ensure_one()
        if not completed_line or not product or not stage_code:
            return 0.0
        reserved_qty = 0.0
        waiting_lines = self.production_line_ids.filtered(
            lambda line: (
                line != completed_line
                and line.first_stage_started
                and self._production_line_matches_product(line, product)
                and stage_code in self._production_line_completed_stage_codes(line, product=product)
                and not self._production_line_all_selected_stages_done(line, product=product)
            )
        )
        for line in waiting_lines:
            for spec in self._get_finished_output_specs_from_lines(line, ensure_storable=False):
                if spec['product'] != product:
                    continue
                reserved_qty += self._quantity_in_product_uom(product, spec['qty'], spec['uom'])
        return reserved_qty

    def _is_final_stage_for_product(self, product, stage_code, source_production=False, production_line=False):
        self.ensure_one()
        if production_line and production_line.exists():
            stage_codes = production_line._selected_stage_codes()
            return (
                bool(stage_codes)
                and stage_code in stage_codes
                and self._production_line_all_selected_stages_done(production_line, product=product)
            )
        if source_production and source_production.exists():
            source_line = self._find_stage_product_source_line(
                product,
                stage_code,
                source_production=source_production,
                final_only=False,
            )
            if source_line:
                return self._is_final_stage_for_product(
                    product,
                    stage_code,
                    production_line=source_line,
                )
            stage_codes = source_production._stage_codes_for_product(product)
            return bool(stage_codes) and stage_code in stage_codes and not source_production.production_line_ids and source_production._selected_stage_orders_done()
        matching_lines = self.production_line_ids.filtered(
            lambda line: self._production_line_matches_product(line, product)
        )
        if matching_lines:
            return any(
                stage_code in line._selected_stage_codes()
                and self._production_line_all_selected_stages_done(line, product=product)
                for line in matching_lines
            )
        current_stage_codes = self._stage_codes_for_product(product)
        if current_stage_codes and stage_code in current_stage_codes:
            return not self.production_line_ids and self._selected_stage_orders_done()
        stage_codes = self._carryover_route_stage_codes(product, source_production=source_production)
        if not stage_codes:
            return False
        return stage_code in stage_codes and not self.production_line_ids and self._selected_stage_orders_done()

    def _has_finished_carryover_to_transfer(self):
        self.ensure_one()
        return bool(self._get_finished_transfer_carryover_payloads())

    def _stage_storage_location_code_map(self):
        self.ensure_one()
        return {
            location.id: stage_code
            for stage_code, _field_info in FURNITURE_STAGE_FIELD_MAP.items()
            for location in (self._stage_storage_location(stage_code),)
            if location
        }

    def _stock_origin_productions_for_product(self, product):
        self.ensure_one()
        if not product:
            return self.env['furniture.mrp.production']
        moves = self.env['stock.move'].sudo().search([
            ('product_id', '=', product.id),
            ('origin', '!=', False),
            ('state', '=', 'done'),
        ], order='date desc, id desc')
        origin_names = []
        for move in moves:
            if move.origin and move.origin not in origin_names:
                origin_names.append(move.origin)
        if not origin_names:
            return self.env['furniture.mrp.production']
        productions = self.env['furniture.mrp.production'].sudo().search([('name', 'in', origin_names)])
        production_by_name = {production.name: production for production in productions}
        ordered_productions = self.env['furniture.mrp.production']
        for origin_name in origin_names:
            production = production_by_name.get(origin_name)
            if production:
                ordered_productions |= production
        return ordered_productions

    def _source_line_matches_product_stage(self, source_line, product, stage_code, final_only=False):
        self.ensure_one()
        if not source_line or not product or not stage_code:
            return False
        source_production = source_line.production_id
        if not source_production or not source_production._production_line_matches_product(source_line, product):
            return False
        stage_codes = source_line._selected_stage_codes()
        if not stage_codes or stage_code not in stage_codes:
            return False
        if not final_only:
            return True
        if not self._production_line_all_selected_stages_done(source_line, product=product):
            return False
        return bool(self._production_line_carryover_history(source_line, product=product).filtered(
            lambda line: line.current_stage == stage_code and line.state == 'stage_done'
        ))

    def _find_stage_product_source_line(self, product, stage_code, source_production=False, final_only=False):
        self.ensure_one()
        if not product or not stage_code:
            return self.env['furniture.mrp.production.line']
        productions = source_production if source_production else self._stock_origin_productions_for_product(product)
        for production in productions.exists():
            matching_lines = production._get_sorted_production_lines().filtered(
                lambda line: self._source_line_matches_product_stage(line, product, stage_code, final_only=final_only)
            )
            if matching_lines:
                return matching_lines[0]
            carryover_lines = production.carryover_line_ids.filtered(
                lambda line: line.product_id == product
            )
            for carryover_line in carryover_lines.sorted(lambda line: line.id):
                source_line = carryover_line.source_production_line_id
                if (
                    not source_line
                    and carryover_line.source_production_id
                    and carryover_line.source_production_id != production
                ):
                    source_line = self._find_stage_product_source_line(
                        product,
                        stage_code,
                        source_production=carryover_line.source_production_id,
                        final_only=final_only,
                    )
                if self._source_line_matches_product_stage(source_line, product, stage_code, final_only=final_only):
                    return source_line
        return self.env['furniture.mrp.production.line']

    def _find_stage_product_source_production(self, product, stage_code, final_only=False):
        self.ensure_one()
        if not product or not stage_code:
            return self.env['furniture.mrp.production']
        source_line = self._find_stage_product_source_line(product, stage_code, final_only=final_only)
        if source_line:
            return source_line.production_id
        productions = self._stock_origin_productions_for_product(product)
        for production in productions.exists():
            stage_codes = production._stage_codes_for_product(product)
            if not stage_codes or stage_code not in stage_codes:
                continue
            if final_only and (production.production_line_ids or not production._selected_stage_orders_done()):
                continue
            return production
        return self.env['furniture.mrp.production']

    def _has_stage_stock_history(self, product, source_location):
        self.ensure_one()
        if not product or not source_location:
            return False
        moves = self.env['stock.move'].sudo().search([
            ('product_id', '=', product.id),
            ('location_dest_id', '=', source_location.id),
            ('state', '=', 'done'),
        ], order='date desc, id desc', limit=20)
        stage_move_markers = (
            'استلام المنتج شبه النهائي',
            'تحويل المنتج شبه النهائي',
            'متابعة الشغل القديم',
        )
        return any(
            any(marker in (move.name or '') for marker in stage_move_markers)
            for move in moves
        )

    def _get_legacy_finished_carryover_payloads(
        self,
        reserved_qty_by_key=None,
        current_payloads=None,
        runtime_cache=None,
    ):
        """يلتقط الشغل القديم الجاهز الموجود فعلاً في مخازن المراحل."""
        self.ensure_one()
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        self._ensure_stage_locations_for_runtime(runtime_cache)
        payloads = []
        reserved_qty_by_key = dict(reserved_qty_by_key or {})
        if current_payloads is None:
            current_payloads = self._get_finished_transfer_current_payloads(runtime_cache=runtime_cache)
        for payload in current_payloads:
            source_location = payload.get('source_location')
            product = payload.get('product')
            uom = payload.get('uom') or (product.uom_id if product else False)
            if not source_location or not product or not uom:
                continue
            source_line = payload.get('source_production_line')
            reserved_key = (
                payload.get('source_stage'),
                source_location.id,
                product.id,
                uom.id,
                source_line.id if source_line else False,
            )
            reserved_qty_by_key[reserved_key] = reserved_qty_by_key.get(reserved_key, 0.0) + (payload.get('qty') or 0.0)

        location_stage_map = self._stage_storage_location_code_map()
        if not location_stage_map:
            return payloads

        non_final_stage_lines = self.carryover_line_ids.filtered(
            lambda line: (
                line.state == 'stage_done'
                and not line.is_final_stage
                and line.product_id
                and line.qty > 0
                and line.current_stage
            )
        )
        for line in non_final_stage_lines:
            source_location = self._stage_storage_location(line.current_stage)
            if not source_location or source_location.id not in location_stage_map:
                continue
            uom = line.product_uom_id or line.product_id.uom_id
            reserved_key = (line.current_stage, source_location.id, line.product_id.id, uom.id, line.source_production_line_id.id)
            reserved_qty_by_key[reserved_key] = (
                reserved_qty_by_key.get(reserved_key, 0.0)
                + self._quantity_in_product_uom(line.product_id, line.qty, uom)
            )

        quant_domain = [
            ('location_id', 'in', list(location_stage_map)),
            ('quantity', '>', 0),
        ]
        if self.company_id:
            quant_domain.append(('company_id', '=', self.company_id.id))
        quants = self.env['stock.quant'].sudo().search(quant_domain, order='location_id asc, product_id asc, id asc')
        stock_by_key = {}
        for quant in quants:
            product = quant.product_id
            source_location = quant.location_id
            stage_code = location_stage_map.get(source_location.id)
            if not product or not stage_code:
                continue
            if not self._has_stage_stock_history(product, source_location):
                continue
            uom = product.uom_id
            entries = self._stage_location_stock_entries(
                source_location,
                product,
                quant.quantity or 0.0,
                stage_code,
                runtime_cache=runtime_cache,
            )
            for entry in entries:
                source_line = entry.get('source_production_line')
                source_production = entry.get('source_production')
                if source_line:
                    if not self._source_line_matches_product_stage(
                        source_line,
                        product,
                        stage_code,
                        final_only=True,
                    ):
                        continue
                    source_production = source_line.production_id
                elif not source_production:
                    source_production = self._find_stage_product_source_production(product, stage_code, final_only=True)
                    if not source_production and not self._is_final_stage_for_product(product, stage_code):
                        continue
                key = (
                    stage_code,
                    source_location.id,
                    product.id,
                    uom.id,
                    source_line.id if source_line else False,
                    source_production.id if source_production else False,
                )
                stock_payload = stock_by_key.setdefault(key, {
                    'stage_code': stage_code,
                    'source_location': source_location,
                    'source_production': source_production,
                    'source_production_line': source_line,
                    'product': product,
                    'uom': uom,
                    'qty': 0.0,
                })
                stock_payload['qty'] += entry.get('qty') or 0.0

        for stock_payload in stock_by_key.values():
            product = stock_payload['product']
            source_location = stock_payload['source_location']
            stage_code = stock_payload['stage_code']
            source_production = stock_payload.get('source_production')
            # A leftover quantity that resolves to this same weekly order is
            # never "old work".  Classifying it as legacy carry-over bypasses
            # the exact current-line completion/cost checks and can value a
            # whole remainder with the representative line's unit cost.  Keep
            # it pending as current work until its missing lineage is fixed.
            if source_production == self:
                continue
            if not source_production and not self._is_final_stage_for_product(product, stage_code):
                continue
            uom = stock_payload['uom']
            source_line = stock_payload.get('source_production_line')
            reserved_key = (stage_code, source_location.id, product.id, uom.id, source_line.id if source_line else False)
            qty = (stock_payload['qty'] or 0.0) - reserved_qty_by_key.get(reserved_key, 0.0)
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            source_origin = source_production.name if source_production else False
            payloads.append({
                'stage_code': stage_code,
                'source_location': source_location,
                'source_origin': source_origin,
                'source_production': source_production,
                'source_production_line': source_line,
                'product': product,
                'uom': uom,
                'qty': qty,
            })
            reserved_qty_by_key[reserved_key] = reserved_qty_by_key.get(reserved_key, 0.0) + qty
        return payloads

    def _stage_completion_payload(self):
        self.ensure_one()
        stage_label_map = dict(FURNITURE_STAGE_SELECTION)
        completed = []
        in_progress = []
        pending = []
        for stage_code, _order, state in self._required_stage_infos():
            label = stage_label_map.get(stage_code, stage_code)
            if state == 'done':
                completed.append(label)
            elif state in ('in_progress', 'quality_check'):
                in_progress.append(label)
            else:
                pending.append(label)
        total = len(completed) + len(in_progress) + len(pending)
        current = (
            in_progress[0]
            if in_progress
            else (pending[0] if pending else (completed[-1] if completed else False))
        )
        return {
            'total': total,
            'done': len(completed),
            'completed': completed,
            'pending': pending,
            'current': current,
        }

    def _clean_stage_display_name(self, name):
        name = ' '.join((name or '').split()).strip()
        if not name:
            return name
        suffix_pattern = re.compile(r'(?:\s+(?:تم\s+)?مرحلة\s+\d+\s+من\s+\d+)+\s*$')
        copy_suffix_pattern = re.compile(r'\s*\(new\)\s*\d*\s*$')
        while True:
            cleaned = suffix_pattern.sub('', name).strip()
            cleaned = copy_suffix_pattern.sub('', cleaned).strip()
            cleaned = ' '.join(cleaned.split())
            if cleaned == name:
                return cleaned
            name = cleaned

    def _strip_dimension_blocks(self, name):
        """Remove any dimension-like blocks like 40×60×80 from a display name."""
        name = ' '.join((name or '').split()).strip()
        if not name:
            return name
        dimension_pattern = re.compile(r'(?:\d+(?:\.\d+)?\s*[×x*]\s*){2}\d+(?:\.\d+)?')
        cleaned = dimension_pattern.sub(' ', name)
        cleaned = ' '.join(cleaned.split()).strip()
        return cleaned

    def _normalize_product_base_name(self, name):
        cleaned = self._clean_stage_display_name(name)
        cleaned = self._strip_dimension_blocks(cleaned)
        return cleaned

    def _format_dimension_value(self, value):
        value = value or 0.0
        if float_compare(value, round(value), precision_digits=3) == 0:
            return str(int(round(value)))
        text = f'{value:.3f}'.rstrip('0').rstrip('.')
        return text or '0'

    def _get_dimension_suffix(self):
        self.ensure_one()
        if not self.bom_id:
            return ''
        actual_dims = [
            self.width_cm or 0.0,
            self.depth_cm or 0.0,
            self.height_cm or 0.0,
        ]
        bom_dims = [
            self.bom_width_cm or 0.0,
            self.bom_depth_cm or 0.0,
            self.bom_height_cm or 0.0,
        ]
        if not any(
            float_compare(actual, base, precision_digits=3) != 0
            for actual, base in zip(actual_dims, bom_dims)
        ):
            return ''
        formatted = '×'.join(self._format_dimension_value(value) for value in actual_dims)
        return f' {formatted}'

    def _get_dimension_label(self):
        self.ensure_one()
        return (self._get_dimension_suffix() or '').strip()

    def _get_dimension_display_name(self):
        self.ensure_one()
        base_name = self._normalize_product_base_name(
            self.product_base_name or self.product_id.product_tmpl_id.name or self.product_id.name or self.name or _('المنتج')
        )
        return base_name

    def _get_dimension_text_label(self):
        self.ensure_one()
        base_name = self._get_dimension_display_name()
        dimension_label = self._get_dimension_label()
        tags = [value for value in (
            self.furniture_order_model_id.name,
            dimension_label,
        ) if value]
        return '%s %s' % (base_name, ' '.join('[%s]' % value for value in tags)) if tags else base_name

    def _normalize_dimensioned_product_identity(
        self, product, base_name, dimension_label, source_product=False, furniture_model=False,
    ):
        self.ensure_one()
        if not product:
            return product
        base_name = self._normalize_product_base_name(base_name or product.product_tmpl_id.name or product.name)
        vals = {}
        if base_name and product.product_tmpl_id.name != base_name:
            vals['name'] = base_name
        if dimension_label and product.furniture_dimension_label != dimension_label:
            vals['furniture_dimension_label'] = dimension_label
        if source_product and product.furniture_dimension_source_product_id != source_product:
            vals['furniture_dimension_source_product_id'] = source_product.id
        if vals:
            product.sudo().write(vals)
        if base_name and product.product_tmpl_id and product.product_tmpl_id.name != base_name:
            product.product_tmpl_id.sudo().write({'name': base_name})
        if furniture_model and product.furniture_model_id != furniture_model:
            product.product_tmpl_id.with_context(
                furniture_skip_bom_classification_sync=True,
            ).sudo().write({
                'furniture_model_id': furniture_model.id,
                'furniture_family_id': furniture_model.family_id.id,
            })
        return product

    def _copy_dimensioned_finished_product(
        self, source_product, base_name, dimension_label, furniture_model=False,
    ):
        """Copy the base product, then write variant-only dimension fields.

        product.product.copy() also copies the product.template, so variant-only
        fields must not be passed in the copy defaults.
        """
        self.ensure_one()
        if not source_product:
            return False
        new_product = source_product.copy({
            'name': self._normalize_product_base_name(base_name or source_product.display_name),
            'default_code': False,
            'active': True,
        })
        new_product.sudo().write({
            'furniture_dimension_label': dimension_label or False,
            'furniture_dimension_source_product_id': source_product.id,
        })
        if furniture_model:
            new_product.product_tmpl_id.with_context(
                furniture_skip_bom_classification_sync=True,
            ).sudo().write({
                'furniture_model_id': furniture_model.id,
                'furniture_family_id': furniture_model.family_id.id,
            })
        return new_product

    def _get_or_create_dimensioned_finished_product(self):
        self.ensure_one()
        dimension_label = self._get_dimension_label()
        furniture_model = self.furniture_order_model_id
        if not dimension_label and not furniture_model:
            return self.product_id

        base_name = self._get_dimension_display_name()
        legacy_name = f'{base_name} {dimension_label}'
        Product = self.env['product.product'].with_context(active_test=False)
        source_product = self.product_id.furniture_dimension_source_product_id or self.product_id
        existing_product = Product.search([
            ('furniture_dimension_source_product_id', '=', source_product.id),
            ('furniture_dimension_label', '=', dimension_label or False),
            ('furniture_model_id', '=', furniture_model.id if furniture_model else False),
        ], limit=1)
        if not existing_product:
            existing_product = Product.search([
                ('furniture_dimension_label', '=', dimension_label or False),
                ('furniture_model_id', '=', furniture_model.id if furniture_model else False),
                '|',
                ('name', '=', base_name),
                ('product_tmpl_id.name', '=', base_name),
            ], limit=1)
        if not existing_product:
            existing_product = Product.search([
                ('furniture_dimension_label', '=', dimension_label or False),
                ('furniture_model_id', '=', furniture_model.id if furniture_model else False),
                '|',
                ('name', '=', legacy_name),
                ('product_tmpl_id.name', '=', legacy_name),
            ], limit=1)
        if existing_product:
            if not existing_product.active:
                existing_product.sudo().write({'active': True})
            return self._normalize_dimensioned_product_identity(
                existing_product,
                base_name,
                dimension_label,
                source_product=source_product,
                furniture_model=furniture_model,
            )

        if not source_product:
            return False
        return self._copy_dimensioned_finished_product(
            source_product, base_name, dimension_label, furniture_model=furniture_model,
        )

    def _find_dimensioned_product_for_line(self, line):
        self.ensure_one()
        if not line or not line.product_id:
            return False
        dimension_label = self._get_production_line_dimension_label(line)
        furniture_model = line.furniture_order_model_id
        if not dimension_label and not furniture_model:
            return line.product_id
        base_name = self._get_production_line_display_name(line)
        legacy_name = f'{base_name} {dimension_label}'
        source_product = line.product_id.furniture_dimension_source_product_id or line.product_id
        Product = self.env['product.product'].with_context(active_test=False)
        existing_product = Product.search([
            ('furniture_dimension_source_product_id', '=', source_product.id),
            ('furniture_dimension_label', '=', dimension_label or False),
            ('furniture_model_id', '=', furniture_model.id if furniture_model else False),
        ], limit=1)
        if existing_product:
            return existing_product
        existing_product = Product.search([
            ('furniture_dimension_label', '=', dimension_label or False),
            ('furniture_model_id', '=', furniture_model.id if furniture_model else False),
            '|',
            ('name', '=', base_name),
            ('product_tmpl_id.name', '=', base_name),
        ], limit=1)
        if existing_product:
            return existing_product
        return Product.search([
            ('furniture_dimension_label', '=', dimension_label or False),
            ('furniture_model_id', '=', furniture_model.id if furniture_model else False),
            '|',
            ('name', '=', legacy_name),
            ('product_tmpl_id.name', '=', legacy_name),
        ], limit=1)

    def _ensure_dimensioned_product_variant(self):
        """Create and assign a dedicated finished-product variant when dimensions differ.

        We keep the same product record throughout all stages so stage quants,
        manual transfers, and the final finished-goods move all point to the
        exact same product variant.
        """
        self.ensure_one()
        if not self._get_dimension_suffix() and not self.furniture_order_model_id:
            return self.product_id

        finished_product = self._get_or_create_dimensioned_finished_product()
        if finished_product and self.product_id != finished_product:
            self.sudo().write({'product_id': finished_product.id})
        return self.product_id

    def _get_sorted_production_lines(self):
        self.ensure_one()
        return self.production_line_ids.sorted(self._production_line_sort_key)

    def _production_line_display_family_origin(self, production_line):
        """Return the original commercial row behind technical Kit pieces.

        Kit identity is deliberately separate from cost inheritance.  Legacy
        locked plans created before that separation still fall back to the old
        cost-origin link until their data migration runs.
        """
        self.ensure_one()
        if not production_line:
            return self.env['furniture.mrp.production.line']
        if production_line.kit_family_origin_line_id:
            return production_line.kit_family_origin_line_id
        if self.kit_plan_locked and production_line.cost_origin_line_id:
            return production_line.cost_origin_line_id
        return production_line

    def _group_production_lines_by_display_family(self, production_lines):
        """Group only the supplied live candidates; never re-add started pieces."""
        self.ensure_one()
        Line = self.env['furniture.mrp.production.line']
        groups = {}
        for line in production_lines.exists().sorted(self._production_line_sort_key):
            root = self._production_line_display_family_origin(line) or line
            key = (
                root.id or line.id,
                line.product_id.id,
                line.product_uom_id.id,
                line.bom_id.id,
                line.furniture_order_model_id.id,
                round(line.width_cm or 0.0, 6),
                round(line.depth_cm or 0.0, 6),
                round(line.height_cm or 0.0, 6),
                tuple(line._selected_stage_codes()),
                self._production_line_start_stage_code(line),
            )
            group = groups.setdefault(key, {
                'root': root,
                'representative': Line,
                'lines': Line,
                'quantity': 0.0,
            })
            group['lines'] |= line
            group['quantity'] += line.product_qty or 0.0
            if not group['representative'] or line == root:
                group['representative'] = line
        return list(groups.values())

    @api.model
    def _stable_record_sort_id(self, record):
        """Return an integer key for saved records and onchange-only NewId rows."""
        origin_id = record._origin.id if record._origin else False
        if isinstance(origin_id, int):
            return origin_id
        return record.id if isinstance(record.id, int) else 0

    def _stage_cost_entry_sort_key(self, entry):
        """Keep cost ledgers sortable while the production form is still virtual."""
        return (
            entry.accepted_at or fields.Datetime.now(),
            self._stable_record_sort_id(entry),
        )

    def _production_line_sort_key(self, line):
        """Keep onchange-only NewId rows sortable before the order is saved."""
        return (line.sequence or 0, self._stable_record_sort_id(line))

    def _get_production_line_cost_family(self, production_line):
        """Return the visible batch and its archived, equivalent split batches."""
        self.ensure_one()
        if not production_line:
            return self.env['furniture.mrp.production.line']
        root = production_line
        visited = set()
        while root.consolidated_into_line_id and root.id not in visited:
            visited.add(root.id)
            root = root.consolidated_into_line_id
        Line = self.env['furniture.mrp.production.line'].with_context(active_test=False)
        return Line.search([
            '|',
            ('id', '=', root.id),
            ('consolidated_into_line_id', '=', root.id),
        ])

    def _get_production_line_cost_snapshots(self, production_lines):
        """Bulk variant of the cumulative-cost snapshot calculation."""
        lines = production_lines.exists()
        if not lines:
            return {}

        production_ids = set(lines.mapped('production_id').ids)
        pending_origins = lines.mapped('cost_origin_line_id')
        seen_origin_ids = set()
        while pending_origins:
            pending_origins = pending_origins.filtered(
                lambda origin: origin.id not in seen_origin_ids
            )
            if not pending_origins:
                break
            seen_origin_ids.update(pending_origins.ids)
            production_ids.update(pending_origins.mapped('production_id').ids)
            pending_origins = pending_origins.mapped('cost_origin_line_id')

        Line = self.env['furniture.mrp.production.line'].with_context(active_test=False)
        all_lines = Line.search([('production_id', 'in', list(production_ids))])
        line_by_id = {line.id: line for line in all_lines}

        consolidated_root_id_by_line_id = {}
        for line in all_lines:
            root = line
            visited = set()
            while root.consolidated_into_line_id and root.id not in visited:
                visited.add(root.id)
                root = root.consolidated_into_line_id
            consolidated_root_id_by_line_id[line.id] = root.id
        family_line_ids_by_root_id = {}
        for line in all_lines:
            root_id = consolidated_root_id_by_line_id[line.id]
            family_line_ids_by_root_id.setdefault(root_id, []).append(line.id)

        entries = self.env['furniture.mrp.stage.cost.entry'].sudo().search([
            ('production_line_id', 'in', all_lines.ids),
        ], order='accepted_at desc, id desc')
        latest_entry_by_line_id = {}
        for entry in entries:
            latest_entry_by_line_id.setdefault(entry.production_line_id.id, entry)

        snapshot_by_line_id = {}
        resolving = set()

        def _snapshot(line):
            if line.id in snapshot_by_line_id:
                return snapshot_by_line_id[line.id]
            line_qty = line.product_qty or 0.0
            empty = {
                'material_unit': 0.0,
                'labor_unit': 0.0,
                'material': 0.0,
                'labor': 0.0,
                'total': 0.0,
                'quantity': line_qty,
                'has_cost': False,
            }
            if line.id in resolving:
                return empty
            resolving.add(line.id)

            own_latest = latest_entry_by_line_id.get(line.id)
            if own_latest and float_compare(
                own_latest.quantity or 0.0,
                line_qty,
                precision_digits=3,
            ) >= 0:
                entry_qty = own_latest.quantity or line_qty
                material_unit = own_latest.cumulative_material_cost / entry_qty if entry_qty else 0.0
                labor_unit = own_latest.cumulative_labor_cost / entry_qty if entry_qty else 0.0
                has_cost = True
            else:
                root_id = consolidated_root_id_by_line_id.get(line.id, line.id)
                family_entries = [
                    latest_entry_by_line_id[family_line_id]
                    for family_line_id in family_line_ids_by_root_id.get(root_id, [line.id])
                    if family_line_id in latest_entry_by_line_id
                ]
                if family_entries:
                    costed_qty = sum(entry.quantity or 0.0 for entry in family_entries)
                    material_unit = (
                        sum(entry.cumulative_material_cost for entry in family_entries) / costed_qty
                        if costed_qty else 0.0
                    )
                    labor_unit = (
                        sum(entry.cumulative_labor_cost for entry in family_entries) / costed_qty
                        if costed_qty else 0.0
                    )
                    has_cost = True
                elif line.inherited_material_cost_per_unit or line.inherited_labor_cost_per_unit:
                    material_unit = line.inherited_material_cost_per_unit or 0.0
                    labor_unit = line.inherited_labor_cost_per_unit or 0.0
                    has_cost = True
                elif line.cost_origin_line_id:
                    origin = line_by_id.get(line.cost_origin_line_id.id) or line.cost_origin_line_id
                    origin_snapshot = _snapshot(origin)
                    material_unit = origin_snapshot['material_unit']
                    labor_unit = origin_snapshot['labor_unit']
                    has_cost = origin_snapshot['has_cost']
                else:
                    material_unit = 0.0
                    labor_unit = 0.0
                    has_cost = False

            resolving.discard(line.id)
            material = material_unit * line_qty
            labor = labor_unit * line_qty
            snapshot = {
                'material_unit': material_unit,
                'labor_unit': labor_unit,
                'material': material,
                'labor': labor,
                'total': material + labor,
                'quantity': line_qty,
                'has_cost': has_cost,
            }
            snapshot_by_line_id[line.id] = snapshot
            return snapshot

        return {line.id: _snapshot(line) for line in lines}

    def _get_production_line_cost_snapshot(self, production_line, visited_line_ids=None):
        """Return a quantity-safe cumulative unit cost for one visible batch."""
        self.ensure_one()
        empty = {
            'material_unit': 0.0,
            'labor_unit': 0.0,
            'material': 0.0,
            'labor': 0.0,
            'total': 0.0,
            'quantity': production_line.product_qty if production_line else 0.0,
            'has_cost': False,
        }
        if not production_line:
            return empty

        visited_line_ids = set(visited_line_ids or [])
        if production_line.id in visited_line_ids:
            return empty
        visited_line_ids.add(production_line.id)

        family = self._get_production_line_cost_family(production_line)
        entries = self.env['furniture.mrp.stage.cost.entry'].sudo().search([
            ('production_line_id', 'in', family.ids),
        ], order='accepted_at desc, id desc')
        own_latest = entries.filtered(
            lambda entry: entry.production_line_id == production_line
        )[:1]
        line_qty = production_line.product_qty or 0.0
        if own_latest and float_compare(
            own_latest.quantity or 0.0,
            line_qty,
            precision_digits=3,
        ) >= 0:
            entry_qty = own_latest.quantity or line_qty
            material_unit = own_latest.cumulative_material_cost / entry_qty if entry_qty else 0.0
            labor_unit = own_latest.cumulative_labor_cost / entry_qty if entry_qty else 0.0
            has_cost = True
        elif entries:
            latest_by_line = {}
            for entry in entries:
                latest_by_line.setdefault(entry.production_line_id.id, entry)
            latest_entries = self.env['furniture.mrp.stage.cost.entry'].browse(
                [entry.id for entry in latest_by_line.values()]
            )
            costed_qty = sum(latest_entries.mapped('quantity'))
            material_unit = (
                sum(latest_entries.mapped('cumulative_material_cost')) / costed_qty
                if costed_qty else 0.0
            )
            labor_unit = (
                sum(latest_entries.mapped('cumulative_labor_cost')) / costed_qty
                if costed_qty else 0.0
            )
            has_cost = True
        elif (
            production_line.inherited_material_cost_per_unit
            or production_line.inherited_labor_cost_per_unit
        ):
            material_unit = production_line.inherited_material_cost_per_unit or 0.0
            labor_unit = production_line.inherited_labor_cost_per_unit or 0.0
            has_cost = True
        elif production_line.cost_origin_line_id:
            origin = production_line.cost_origin_line_id
            origin_snapshot = origin.production_id._get_production_line_cost_snapshot(
                origin,
                visited_line_ids=visited_line_ids,
            )
            material_unit = origin_snapshot['material_unit']
            labor_unit = origin_snapshot['labor_unit']
            has_cost = origin_snapshot['has_cost']
        else:
            material_unit = 0.0
            labor_unit = 0.0
            has_cost = False

        material = material_unit * line_qty
        labor = labor_unit * line_qty
        return {
            'material_unit': material_unit,
            'labor_unit': labor_unit,
            'material': material,
            'labor': labor,
            'total': material + labor,
            'quantity': line_qty,
            'has_cost': has_cost,
        }

    def _get_equivalent_production_line_groups(self):
        """Find physically and operationally identical visible split batches."""
        self.ensure_one()
        Line = self.env['furniture.mrp.production.line'].with_context(active_test=False)
        lines = Line.search([
            ('production_id', '=', self.id),
            ('active', '=', True),
        ], order='sequence, id')
        if len(lines) < 2:
            return []

        stage_status = {line.id: [] for line in lines}
        busy_line_ids = set()
        for stage_code, _label in FURNITURE_STAGE_SELECTION:
            stage_order = self._stage_order_record(stage_code)
            first_lines = stage_order.first_stage_production_line_ids if stage_order else Line
            active_lines = (
                stage_order._get_stage_line_ids_data('active_production_line_ids_data')
                if stage_order else Line
            )
            quality_lines = (
                stage_order._get_stage_line_ids_data('quality_production_line_ids_data')
                if stage_order else Line
            )
            completed_lines = (
                stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
                if stage_order else Line
            )
            busy_line_ids.update((active_lines | quality_lines).ids)
            for line in lines:
                stage_status[line.id].append((
                    line in first_lines,
                    line in active_lines,
                    line in quality_lines,
                    line in completed_lines,
                ))

        latest_location_by_line = {}
        stock_moves = self.env['stock.move'].sudo().search([
            ('furniture_source_production_line_id', 'in', lines.ids),
            ('state', '=', 'done'),
        ], order='id')
        for move in stock_moves:
            latest_location_by_line[move.furniture_source_production_line_id.id] = (
                move.location_dest_id.id,
                move.product_id.id,
            )

        ready_transfer_by_line = {line.id: [] for line in lines}
        ready_transfers = self.env['furniture.mrp.finished.transfer.line'].sudo().search([
            ('source_production_line_id', 'in', lines.ids),
            ('state', '=', 'ready'),
        ])
        for transfer in ready_transfers:
            ready_transfer_by_line[transfer.source_production_line_id.id].append((
                transfer.production_id.id,
                transfer.source_stage,
                transfer.source_location_id.id,
                transfer.product_id.id,
                transfer.product_uom_id.id,
            ))

        carryover_by_line = {line.id: [] for line in lines}
        carryover_lines = self.env['furniture.mrp.carryover.line'].sudo().search([
            ('source_production_line_id', 'in', lines.ids),
            ('state', 'in', ('selected', 'started', 'stage_done')),
        ])
        for carryover in carryover_lines:
            carryover_by_line[carryover.source_production_line_id.id].append((
                carryover.production_id.id,
                carryover.current_stage,
                carryover.state,
                carryover.product_id.id,
                carryover.product_uom_id.id,
            ))

        grouped = {}
        for line in lines:
            # Running or quality-check batches stay separate so labor and quality
            # actions remain tied to the exact batch being processed.
            if line.id in busy_line_ids:
                continue
            signature = (
                line.product_id.id,
                line.furniture_order_model_id.id,
                line.kit_bom_id.id,
                line.kit_instance_number or 0,
                line.buyer_partner_id.id,
                line.beneficiary_partner_id.id,
                line.batch_image_token or False,
                line.kit_piece_note or False,
                line.bom_id.id,
                line.product_uom_id.id,
                round(line.width_cm or 0.0, 3),
                round(line.depth_cm or 0.0, 3),
                round(line.height_cm or 0.0, 3),
                tuple(line._selected_stage_codes()),
                bool(line.first_stage_started),
                line.first_stage_started_stage or False,
                line.planned_start_stage or False,
                tuple(stage_status[line.id]),
                latest_location_by_line.get(line.id, (False, False)),
                tuple(sorted(ready_transfer_by_line[line.id])),
                tuple(sorted(carryover_by_line[line.id])),
            )
            grouped.setdefault(signature, self.env['furniture.mrp.production.line'])
            grouped[signature] |= line
        return [group for group in grouped.values() if len(group) > 1]

    def _replace_consolidated_line_references(self, survivor, absorbed_lines):
        self.ensure_one()
        Line = self.env['furniture.mrp.production.line'].with_context(active_test=False).sudo()
        survivor = Line.browse(survivor.id)
        absorbed_lines = Line.browse(absorbed_lines.ids).exists() - survivor
        if not absorbed_lines:
            return

        for stage_code, _label in FURNITURE_STAGE_SELECTION:
            stage_order = self._stage_order_record(stage_code)
            if not stage_order:
                continue
            stage_order = stage_order.sudo().with_context(furniture_skip_line_consolidation=True)
            if stage_order.first_stage_production_line_ids & absorbed_lines:
                replacement = (
                    stage_order.first_stage_production_line_ids - absorbed_lines
                ) | survivor
                stage_order.write({'first_stage_production_line_ids': [(6, 0, replacement.ids)]})
            for field_name in (
                'active_production_line_ids_data',
                'quality_production_line_ids_data',
                'completed_production_line_ids_data',
            ):
                referenced = stage_order._get_stage_line_ids_data(field_name)
                if referenced & absorbed_lines:
                    stage_order._set_stage_line_ids_data(
                        field_name,
                        (referenced - absorbed_lines) | survivor,
                    )

        production_vals = {}
        if self.stage_plan_line_id in absorbed_lines:
            production_vals['stage_plan_line_id'] = survivor.id
        if self.dimension_line_id in absorbed_lines:
            production_vals['dimension_line_id'] = survivor.id
        if production_vals:
            self.with_context(furniture_skip_line_consolidation=True).write(production_vals)

        self.env['furniture.mrp.material.line'].sudo().search([
            ('production_line_id', 'in', absorbed_lines.ids),
        ]).write({'production_line_id': survivor.id})
        self.env['furniture.mrp.carryover.line'].sudo().search([
            ('source_production_line_id', 'in', absorbed_lines.ids),
        ]).write({'source_production_line_id': survivor.id})
        self.env['furniture.mrp.finished.transfer.line'].sudo().search([
            ('source_production_line_id', 'in', absorbed_lines.ids),
        ]).write({'source_production_line_id': survivor.id})
        self.env['stock.move'].sudo().search([
            ('furniture_source_production_line_id', 'in', absorbed_lines.ids),
        ]).write({'furniture_source_production_line_id': survivor.id})
        self.env['furniture.mrp.production.line'].with_context(active_test=False).sudo().search([
            ('cost_origin_line_id', 'in', absorbed_lines.ids),
            ('id', '!=', survivor.id),
        ]).write({'cost_origin_line_id': survivor.id})

    def _consolidate_equivalent_production_lines(self):
        """Archive duplicate split rows once their route and physical state match."""
        if self.env.context.get('furniture_skip_line_consolidation'):
            return 0
        consolidated_count = 0
        Line = self.env['furniture.mrp.production.line'].with_context(active_test=False).sudo()
        for production in self:
            # An explicit Kit plan intentionally keeps one physical row per
            # piece so each sofa/chair can carry its own image.  Merging two
            # equal component rows inside the same Kit would destroy that
            # piece-level preview and image identity.
            if production.kit_plan_locked:
                continue
            for group in production._get_equivalent_production_line_groups():
                group = Line.browse(group.ids)
                survivor = group.sorted(lambda line: (
                    bool(line.consolidated_into_line_id),
                    bool(line.cost_origin_line_id),
                    line.sequence,
                    line.id,
                ))[:1]
                absorbed_roots = group - survivor
                absorbed_family = absorbed_roots
                absorbed_family |= Line.search([
                    ('consolidated_into_line_id', 'in', absorbed_roots.ids),
                ])
                combined_qty = sum(group.mapped('product_qty'))
                production._replace_consolidated_line_references(survivor, absorbed_family)
                Line.search([
                    ('consolidated_into_line_id', 'in', absorbed_roots.ids),
                ]).write({'consolidated_into_line_id': survivor.id})
                absorbed_roots.with_context(
                    furniture_skip_line_consolidation=True,
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_mps_replan=True,
                ).write({
                    'active': False,
                    'consolidated_into_line_id': survivor.id,
                })
                survivor.with_context(
                    furniture_skip_line_consolidation=True,
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_mps_replan=True,
                ).write({
                    'product_qty': combined_qty,
                    'sequence': min(group.mapped('sequence') or [survivor.sequence]),
                })
                consolidated_count += len(absorbed_roots)
        return consolidated_count

    def _copy_completed_stage_progress_to_split_line(self, source_line, split_line, completed_stage_codes, exclude_stage_code=False):
        """When a started batch is split, keep the new batch tied to the same finished history."""
        self.ensure_one()
        if not source_line or not split_line:
            return
        for stage_code in completed_stage_codes or []:
            if exclude_stage_code and stage_code == exclude_stage_code:
                continue
            stage_order = self._stage_order_record(stage_code)
            if not stage_order:
                continue
            completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data') | split_line
            stage_order._set_stage_line_ids_data('completed_production_line_ids_data', completed_lines)
            if source_line in stage_order.first_stage_production_line_ids:
                first_stage_lines = stage_order.first_stage_production_line_ids | split_line
                stage_order.write({'first_stage_production_line_ids': [(6, 0, first_stage_lines.ids)]})

    def _production_line_stage_done(self, production_line, stage_code, product=False):
        self.ensure_one()
        if not production_line or not stage_code:
            return False
        return stage_code in self._production_line_completed_stage_codes(
            production_line,
            product=product,
            stage_codes=(stage_code,),
        )

    def _get_stage_incomplete_line_candidates(self, stage_code):
        self.ensure_one()
        if not stage_code:
            return self.env['furniture.mrp.production.line']
        candidates = self.env['furniture.mrp.production.line']
        for line in self._get_sorted_production_lines():
            if not line.product_id or float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) <= 0:
                continue
            if stage_code not in line._selected_stage_codes():
                continue
            if not self._production_line_stage_done(line, stage_code):
                candidates |= line
        return candidates

    def _get_production_line_dimension_values(self, line):
        """Return the dimensions that should define this line's finished product.

        In old/single-line records, users sometimes filled the header dimensions
        before the first line existed. For multi-line orders, each line must stay
        independent so one custom sofa does not rename/group the whole quantity.
        """
        self.ensure_one()
        line_actual = [
            line.width_cm or 0.0,
            line.depth_cm or 0.0,
            line.height_cm or 0.0,
        ]
        if (
            len(self.production_line_ids) == 1
            and not any(line_actual)
            and any((self.width_cm or 0.0, self.depth_cm or 0.0, self.height_cm or 0.0))
        ):
            line_actual = [
                self.width_cm or 0.0,
                self.depth_cm or 0.0,
                self.height_cm or 0.0,
            ]
        bom_dims = [
            line.bom_width_cm or 0.0,
            line.bom_depth_cm or 0.0,
            line.bom_height_cm or 0.0,
        ]
        return line_actual, bom_dims

    def _get_production_line_dimension_suffix(self, line):
        self.ensure_one()
        if not line or not line.bom_id:
            return ''
        actual_dims, bom_dims = self._get_production_line_dimension_values(line)
        if not any(
            float_compare(actual, base, precision_digits=3) != 0
            for actual, base in zip(actual_dims, bom_dims)
        ):
            return ''
        formatted = '×'.join(self._format_dimension_value(value) for value in actual_dims)
        return f' {formatted}'

    def _get_production_line_dimension_label(self, line):
        self.ensure_one()
        return (self._get_production_line_dimension_suffix(line) or '').strip()

    def _get_production_line_display_name(self, line):
        self.ensure_one()
        base_name = self._normalize_product_base_name(
            line.product_id.product_tmpl_id.name or line.product_id.name or self.name or _('المنتج')
        )
        return base_name

    def _get_production_line_text_label(self, line):
        self.ensure_one()
        base_name = self._get_production_line_display_name(line)
        dimension_label = self._get_production_line_dimension_label(line)
        tags = [value for value in (
            line.furniture_order_model_id.name,
            dimension_label,
        ) if value]
        return '%s %s' % (base_name, ' '.join('[%s]' % value for value in tags)) if tags else base_name

    def _get_production_lines_display_list(self, production_lines):
        self.ensure_one()
        return [
            self._get_production_line_text_label(line)
            for line in production_lines
            if line.product_id
        ]

    def _get_or_create_dimensioned_finished_product_for_line(self, line):
        self.ensure_one()
        if not line or not line.product_id:
            return False
        dimension_label = self._get_production_line_dimension_label(line)
        furniture_model = line.furniture_order_model_id
        if not dimension_label and not furniture_model:
            return line.product_id
        base_name = self._get_production_line_display_name(line)
        existing_product = self._find_dimensioned_product_for_line(line)
        if existing_product:
            if not existing_product.active:
                existing_product.sudo().write({'active': True})
            return self._normalize_dimensioned_product_identity(
                existing_product,
                base_name,
                dimension_label,
                source_product=line.product_id.furniture_dimension_source_product_id or line.product_id,
                furniture_model=furniture_model,
            )
        source_product = line.product_id.furniture_dimension_source_product_id or line.product_id
        return self._copy_dimensioned_finished_product(
            source_product,
            base_name,
            dimension_label,
            furniture_model=furniture_model,
        )

    def _get_finished_output_specs_from_lines(self, production_lines, ensure_storable=True):
        self.ensure_one()
        specs_by_product = {}
        ordered_specs = []
        empty_lines = self.env['furniture.mrp.production.line']
        for line in production_lines:
            if not line.product_id or line.product_qty <= 0:
                continue
            finished_product = self._get_or_create_dimensioned_finished_product_for_line(line) or line.product_id
            if ensure_storable:
                self._ensure_stock_product_is_storable(finished_product, finished_product.uom_id)
            spec = specs_by_product.get(finished_product.id)
            if not spec:
                spec = {
                    'product': finished_product,
                    'qty': 0.0,
                    'uom': finished_product.uom_id or line.product_uom_id,
                    'label': self._get_production_line_text_label(line),
                    'production_lines': empty_lines,
                }
                specs_by_product[finished_product.id] = spec
                ordered_specs.append(spec)
            spec['qty'] += line.product_qty
            spec['production_lines'] |= line
        return ordered_specs

    def _get_finished_output_specs(self, ensure_storable=True):
        self.ensure_one()
        if self.production_line_ids:
            ordered_specs = self._get_finished_output_specs_from_lines(
                self._get_sorted_production_lines(),
                ensure_storable=ensure_storable,
            )
            if ordered_specs:
                return ordered_specs

        finished_product = self._get_or_create_dimensioned_finished_product() or self.product_id
        if not finished_product:
            return []
        if ensure_storable:
            self._ensure_stock_product_is_storable(finished_product, finished_product.uom_id)
        return [{
            'product': finished_product,
            'qty': self.product_qty,
            'uom': finished_product.uom_id or self.product_uom_id,
            'label': self._get_dimension_text_label(),
        }]

    def _get_started_finished_output_specs(self, ensure_storable=False):
        self.ensure_one()
        started_lines = self.production_line_ids.filtered('first_stage_started')
        if self.production_line_ids and started_lines:
            return self._get_finished_output_specs_from_lines(started_lines, ensure_storable=ensure_storable)
        return self._get_finished_output_specs(ensure_storable=ensure_storable)

    def _get_stage_stock_product(self):
        self.ensure_one()
        specs = self._get_finished_output_specs(ensure_storable=False)
        product = specs[0]['product'] if specs else self.product_id
        if product:
            self._ensure_stock_product_is_storable(product, product.uom_id)
        return product

    def _get_stage_stock_product_candidates(self):
        self.ensure_one()
        products = self.env['product.product']
        for spec in self._get_finished_output_specs(ensure_storable=False):
            product = spec['product']
            if product and product not in products:
                products |= product
        for product in (self.product_id,):
            if product and product not in products:
                products |= product
        return products

    def _get_stage_product_name(self):
        self.ensure_one()
        payload = self._stage_completion_payload()
        display_name = self._get_dimension_display_name()
        if not payload['total'] or self.state == 'done':
            return display_name
        return _('%s تم مرحلة %s من %s') % (display_name, payload['done'] or 0, payload['total'])

    def _sync_stage_product_name(self):
        self.ensure_one()
        if not self.product_id or not self.product_id.product_tmpl_id:
            return
        if not self.product_base_name:
            base_name = self._normalize_product_base_name(self.product_id.product_tmpl_id.name or self.product_id.name)
            if base_name:
                self.sudo().write({'product_base_name': base_name})
        base_name = self._normalize_product_base_name(
            self.product_base_name or self.product_id.product_tmpl_id.name or self.product_id.name
        )
        if self.product_base_name != base_name and base_name:
            self.sudo().write({'product_base_name': base_name})

    def _restore_finished_product_name(self):
        self.ensure_one()
        if not self.product_id or not self.product_id.product_tmpl_id:
            return
        base_name = self._normalize_product_base_name(
            self.product_base_name or self.product_id.product_tmpl_id.name or self.product_id.name or _('المنتج')
        )
        if base_name and self.product_base_name != base_name:
            self.sudo().write({'product_base_name': base_name})

    def _ensure_stage_transfer_ready(self, stage_model):
        self.ensure_one()
        stage_code = self._stage_model_to_code(stage_model)
        if self._is_material_only_stage(stage_code):
            return
        if not self._has_manual_transfer_for_stage(stage_model):
            if not stage_code or stage_code == 'priming':
                return
            if self._has_stage_work_location_stock(stage_model):
                return
            active_stages = self._physical_stage_codes()
            stage_index = active_stages.index(stage_code)
            previous_stage = active_stages[stage_index - 1]
            previous_label = dict(FURNITURE_STAGE_SELECTION).get(previous_stage, previous_stage)
            current_label = dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code)
            raise UserError(_('لازم تنقل المنتج يدويًا من مخزن %s إلى صالة %s قبل بدء مرحلة %s.') % (
                previous_label, current_label, current_label,
            ))

    def _has_manual_transfer_for_stage(self, stage_model):
        self.ensure_one()
        stage_code = self._stage_model_to_code(stage_model)
        if not stage_code:
            return False
        if self._is_material_only_stage(stage_code):
            return True
        active_stages = self._physical_stage_codes()
        if stage_code not in active_stages:
            return False
        stage_index = active_stages.index(stage_code)
        if stage_index <= 0:
            return True

        previous_stage = active_stages[stage_index - 1]
        previous_order = self._stage_order_record(previous_stage)
        if not previous_order or previous_order.state != 'done':
            return False

        source_location = self._stage_storage_location(previous_stage)
        target_location = self._stage_work_location(stage_code)
        if not source_location or not target_location:
            return False

        if self._has_stage_work_location_stock(stage_model):
            return True

        for spec in self._get_finished_output_specs(ensure_storable=False):
            transfer_moves = self.env['stock.move'].sudo().search([
                ('origin', '=', self.name),
                ('product_id', '=', spec['product'].id),
                ('location_id', '=', source_location.id),
                ('location_dest_id', '=', target_location.id),
                ('state', '=', 'done'),
            ])
            moved_qty = sum(
                self._quantity_in_product_uom(
                    spec['product'],
                    move.quantity or move.product_uom_qty,
                    move.product_uom,
                )
                for move in transfer_moves
            )
            required_qty = self._quantity_in_product_uom(spec['product'], spec['qty'], spec['uom'])
            if float_compare(moved_qty, required_qty, precision_digits=3) < 0:
                return False
        return True

    def _has_stage_work_location_stock(self, stage_model):
        """السماح ببدء المرحلة إذا كان المنتج موجودًا بالفعل في صالة المرحلة.

        ده بيفك القفل عن ربط البدء بحركة تخص نفس أمر التشغيل فقط، وبيخلّي
        المستخدم يكمل على القطع اللي وصلت للصالة فعلًا حتى لو جهّت من أمر
        تصنيع سابق أو من تحويل يدوي قبل كده.
        """
        self.ensure_one()
        stage_code = self._stage_model_to_code(stage_model)
        if not stage_code:
            return False
        target_location = self._stage_work_location(stage_code)
        if not target_location:
            return False
        specs = self._get_finished_output_specs(ensure_storable=False)
        if not specs:
            return False
        for spec in specs:
            available_qty = self._stage_location_product_qty(target_location, spec['product'])
            required_qty = self._quantity_in_product_uom(spec['product'], spec['qty'], spec['uom'])
            if float_compare(available_qty, required_qty, precision_digits=3) < 0:
                return False
        return True

    def _ensure_stage_required(self, stage_code):
        self.ensure_one()
        if stage_code not in self._required_stage_codes():
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code)
            raise UserError(_('مرحلة %s غير مختارة في مراحل أمر التشغيل لهذا الأسبوع.') % stage_label)

    def _prepare_material_line_command(
        self,
        product,
        uom,
        qty,
        stage=False,
        bom_line=False,
        production_line=False,
        quantity_mode='scaled',
        tailoring_recipe_material_kind=False,
    ):
        uom = self._get_stock_uom_for_product(product, uom or product.uom_id)
        vals = {
            'product_id': product.id,
            'product_uom_id': uom.id,
            'qty_needed': qty,
            'stage': stage,
            'quantity_mode': quantity_mode or 'scaled',
        }
        if bom_line:
            vals['bom_line_id'] = bom_line.id
        if tailoring_recipe_material_kind:
            vals['tailoring_recipe_material_kind'] = (
                tailoring_recipe_material_kind
            )
        if production_line and production_line.id:
            vals['production_line_id'] = production_line.id
        return (0, 0, vals)

    def _prepare_material_line_commands_for_stage_plan(self):
        self.ensure_one()
        if self.production_line_ids:
            return self._prepare_material_lines_from_production_lines()
        if self.bom_id:
            source_product = self.product_id or self.bom_id.furniture_product_id
            if source_product:
                resolved_bom = self._find_bom_for_product(
                    source_product,
                    self.furniture_order_model_id,
                )
                if not resolved_bom and self.furniture_order_model_id:
                    raise UserError(_(
                        'لا توجد ريسيبي للصنف %(product)s مع الموديل %(model)s.'
                    ) % {
                        'product': source_product.display_name,
                        'model': self.furniture_order_model_id.display_name,
                    })
                if not resolved_bom:
                    raise UserError(_(
                        'حدد الموديل قبل تحميل خامات ريسيبي المنتج.'
                    ))
                return self._prepare_material_lines_from_bom(resolved_bom)
            return self._prepare_material_lines_from_bom(self.bom_id)
        return []

    def _refresh_material_lines_for_stage_plan(self):
        for rec in self:
            rec.material_line_ids = [(5, 0, 0)] + rec._prepare_material_line_commands_for_stage_plan()

    def _prepare_material_lines_from_bom(self, bom):
        return self._prepare_material_lines_from_bom_with_factor(
            bom,
            self.product_qty,
            dimension_factor=self._get_dimension_factor(),
        )

    def _prepare_material_lines_from_bom_with_factor(
        self,
        bom,
        output_qty,
        dimension_factor=1.0,
        production_line=False,
        active_stage_codes=None,
    ):
        def _with_order_overrides(prepared_lines):
            if not production_line:
                return prepared_lines
            prepared_lines = production_line._apply_material_qty_overrides_to_commands(
                prepared_lines,
            )
            return production_line._apply_tailoring_material_allocations_to_commands(
                prepared_lines,
                active_stage_codes=active_stages,
            )

        lines = []
        active_stages = set(active_stage_codes if active_stage_codes is not None else self._required_stage_codes())
        for stage_line in bom.furniture_stage_material_line_ids:
            stage_code = 'tailoring' if stage_line.stage == 'sewing' else stage_line.stage
            if stage_code not in active_stages:
                continue
            lines.append(self._prepare_material_line_command(
                stage_line.product_id,
                stage_line.product_uom_id,
                stage_line._get_required_quantity(output_qty, dimension_factor),
                stage=stage_code,
                production_line=production_line,
                quantity_mode=stage_line.quantity_mode,
                tailoring_recipe_material_kind=(
                    stage_line._tailoring_setup_material_kind()
                ),
            ))
        if lines:
            return _with_order_overrides(lines)
        for stage in bom.furniture_stage_ids:
            stage_code = 'tailoring' if stage.stage == 'sewing' else stage.stage
            if stage_code not in active_stages:
                continue
            for stage_line in stage.line_ids:
                lines.append(self._prepare_material_line_command(
                    stage_line.product_id,
                    stage_line.product_uom_id,
                    stage_line._get_required_quantity(output_qty, dimension_factor),
                    stage=stage_code,
                    production_line=production_line,
                    quantity_mode=stage_line.quantity_mode,
                    tailoring_recipe_material_kind=(
                        stage_line._tailoring_setup_material_kind()
                    ),
                ))
        if lines:
            return _with_order_overrides(lines)
        for bom_line in bom.bom_line_ids:
            stage_code = (
                'tailoring'
                if bom_line.furniture_stage == 'sewing'
                else bom_line.furniture_stage
            )
            if stage_code and stage_code not in active_stages:
                continue
            lines.append(self._prepare_material_line_command(
                bom_line.product_id,
                bom_line.product_uom_id,
                bom_line._get_furniture_required_quantity(output_qty, dimension_factor),
                stage=stage_code,
                bom_line=bom_line,
                production_line=production_line,
                quantity_mode=bom_line.furniture_quantity_mode,
                tailoring_recipe_material_kind=(
                    bom_line.product_id._furniture_tailoring_material_kind()
                ),
            ))
        return _with_order_overrides(lines)

    def _find_bom_for_product(self, product, furniture_model=False):
        if not product:
            return self.env['mrp.bom']
        furniture_model = furniture_model or product.furniture_model_id
        # Operational stage supervisors must be able to calculate the exact
        # recipe materials without receiving general BoM menu/read access.
        # This private resolver is always constrained to the production's
        # active company below, so elevate only the internal catalogue lookup.
        Bom = self.env['mrp.bom'].sudo()
        company = self.company_id or self.env.company
        return Bom._find_furniture_production_recipe(
            product,
            furniture_model,
            company=company,
        )

    def _find_bom_for_production_line(self, line):
        """Resolve a line without discarding an archived recipe snapshot.

        Replacing the recipe catalogue archives old BoMs, but confirmed and
        running production lines must continue to consume exactly the recipe
        they were created with.  A valid inactive assignment is therefore an
        immutable historic snapshot; only lines without one follow the active
        catalogue resolver.
        """
        self.ensure_one()
        if not line or not line.product_id:
            return self.env['mrp.bom']
        # A confirmed line may point at an archived immutable recipe snapshot.
        # Read that snapshot internally for material calculation while keeping
        # the supervisor's normal UI and RPC permissions unchanged.
        assigned_bom = line.bom_id.sudo()
        source_product = (
            line.product_id.furniture_dimension_source_product_id
            or line.product_id
        )
        recipe_model = assigned_bom and (
            assigned_bom.furniture_model_id
            or assigned_bom.furniture_recipe_model_id
        )
        company = self.company_id or self.env.company
        if (
            assigned_bom
            and not assigned_bom.active
            and assigned_bom.type == 'normal'
            and assigned_bom.furniture_product_id == source_product
            and recipe_model == line.furniture_order_model_id
            and assigned_bom.company_id in (self.env['res.company'], company)
        ):
            return assigned_bom
        return self._find_bom_for_product(
            line.product_id,
            line.furniture_order_model_id,
        )

    def _find_bom_for_stage_product(self, product):
        self.ensure_one()
        furniture_model = product.furniture_model_id if product else False
        if product and product.furniture_dimension_source_product_id:
            source_bom = self._find_bom_for_product(
                product.furniture_dimension_source_product_id,
                furniture_model,
            )
            if source_bom:
                return source_bom
        return self._find_bom_for_product(product, furniture_model)

    def _product_dimension_factor_from_name(self, product, bom):
        self.ensure_one()
        if not product or not bom:
            return 1.0
        match = re.search(
            r'(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)',
            product.furniture_dimension_label or product.display_name or product.name or '',
        )
        if not match:
            return 1.0
        actual_dims = [float(value) for value in match.groups()]
        bom_dims = [
            bom.furniture_width_cm or 0.0,
            bom.furniture_depth_cm or 0.0,
            bom.furniture_height_cm or 0.0,
        ]
        ratios = [
            actual / base
            for actual, base in zip(actual_dims, bom_dims)
            if actual and base
        ]
        return sum(ratios) / len(ratios) if ratios else 1.0

    def _prepare_material_lines_for_product_stage(self, product, qty, stage_code, production_line=False):
        self.ensure_one()
        def _with_order_overrides(prepared_lines):
            if not production_line:
                return prepared_lines
            prepared_lines = production_line._apply_material_qty_overrides_to_commands(
                prepared_lines,
            )
            return production_line._apply_tailoring_material_allocations_to_commands(
                prepared_lines,
                active_stage_codes=[stage_code],
            )

        if production_line:
            bom = self._find_bom_for_production_line(production_line)
            if not bom and not production_line.furniture_order_model_id:
                raise UserError(_(
                    'حدد الموديل أولًا للصنف: %s'
                ) % production_line.product_id.display_name)
            if not bom and production_line.furniture_order_model_id:
                raise UserError(_(
                    'لا توجد ريسيبي للصنف %(product)s مع الموديل %(model)s.'
                ) % {
                    'product': production_line.product_id.display_name,
                    'model': production_line.furniture_order_model_id.display_name,
                })
        else:
            bom = self._find_bom_for_stage_product(product)
        if not bom:
            return []
        dimension_factor = (
            production_line._get_dimension_factor()
            if production_line
            else self._product_dimension_factor_from_name(product, bom)
        )
        lines = []
        for stage_line in bom.furniture_stage_material_line_ids.filtered(lambda item: item.stage == stage_code):
            lines.append(self._prepare_material_line_command(
                stage_line.product_id,
                stage_line.product_uom_id,
                stage_line._get_required_quantity(qty, dimension_factor),
                stage=stage_code,
                production_line=production_line,
                quantity_mode=stage_line.quantity_mode,
                tailoring_recipe_material_kind=(
                    stage_line._tailoring_setup_material_kind()
                ),
            ))
        if lines:
            return _with_order_overrides(lines)
        for stage in bom.furniture_stage_ids.filtered(lambda item: item.stage == stage_code):
            for stage_line in stage.line_ids:
                lines.append(self._prepare_material_line_command(
                    stage_line.product_id,
                    stage_line.product_uom_id,
                    stage_line._get_required_quantity(qty, dimension_factor),
                    stage=stage_code,
                    production_line=production_line,
                    quantity_mode=stage_line.quantity_mode,
                    tailoring_recipe_material_kind=(
                        stage_line._tailoring_setup_material_kind()
                    ),
                ))
        if lines:
            return _with_order_overrides(lines)
        for bom_line in bom.bom_line_ids.filtered(lambda item: item.furniture_stage == stage_code):
            lines.append(self._prepare_material_line_command(
                bom_line.product_id,
                bom_line.product_uom_id,
                bom_line._get_furniture_required_quantity(qty, dimension_factor),
                stage=stage_code,
                bom_line=bom_line,
                production_line=production_line,
                quantity_mode=bom_line.furniture_quantity_mode,
                tailoring_recipe_material_kind=(
                    bom_line.product_id._furniture_tailoring_material_kind()
                ),
            ))
        return _with_order_overrides(lines)

    def _open_stage_requested_materials_preview(
        self, stage_code, product, quantity, production_line=False, product_label=False,
        source_wizard_line=False, material_overrides=False,
        prepared_material_commands=None,
    ):
        """Show the exact raw-material request generated for one wizard row."""
        self.ensure_one()
        if not product or float_compare(quantity or 0.0, 0.0, precision_digits=3) <= 0:
            raise UserError(_('حدد كمية أكبر من صفر لعرض الخامات المطلوبة.'))

        commands = (
            self._prepare_material_lines_for_product_stage(
                product,
                quantity,
                stage_code,
                production_line=production_line or False,
            )
            if prepared_material_commands is None
            else list(prepared_material_commands)
        )
        buckets = {}
        for command in commands:
            if not isinstance(command, (list, tuple)) or len(command) < 3 or command[0] != 0:
                continue
            vals = command[2] or {}
            material = self.env['product.product'].browse(vals.get('product_id')).exists()
            if not material:
                continue
            uom = self.env['uom.uom'].browse(vals.get('product_uom_id')).exists() or material.uom_id
            qty_needed = vals.get('qty_needed') or 0.0
            if float_compare(qty_needed, 0.0, precision_digits=3) <= 0:
                continue
            key = (material.id, uom.id)
            bucket = buckets.setdefault(key, {
                'product_id': material.id,
                'product_uom_id': uom.id,
                'requested_qty': 0.0,
            })
            bucket['requested_qty'] += qty_needed

        if material_overrides:
            for override in material_overrides:
                material = self.env['product.product'].browse(override.get('product_id')).exists()
                if not material:
                    continue
                uom = self.env['uom.uom'].browse(override.get('product_uom_id')).exists() or material.uom_id
                key = (material.id, uom.id)
                bucket = buckets.setdefault(key, {
                    'product_id': material.id,
                    'product_uom_id': uom.id,
                    'requested_qty': 0.0,
                })
                bucket['requested_qty'] = max(override.get('qty_needed') or 0.0, 0.0)

        if not buckets:
            raise UserError(_(
                'لا توجد خامات معرفة للصنف %s في مرحلة %s.'
            ) % (
                product_label or product.display_name,
                dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code),
            ))

        source_location = self.location_src_id or self.env.ref(
            'stock.stock_location_stock', raise_if_not_found=False,
        )
        preview_lines = []
        for bucket in buckets.values():
            material = self.env['product.product'].browse(bucket['product_id'])
            available_qty = (
                self._stage_location_product_qty(source_location, material, self.company_id)
                if source_location else 0.0
            )
            preview_lines.append((0, 0, {
                **bucket,
                'available_qty': available_qty,
            }))

        preview = self.env['furniture.mrp.stage.material.preview.wizard'].create({
            'production_id': self.id,
            'stage_code': stage_code,
            'product_id': product.id,
            'product_label': product_label or product.display_name,
            'output_qty': quantity,
            'output_uom_id': product.uom_id.id,
            'source_wizard_line_model': source_wizard_line._name if source_wizard_line else False,
            'source_wizard_line_id': source_wizard_line.id if source_wizard_line else False,
            'line_ids': preview_lines,
        })
        view = self.env.ref('furniture_mrp.view_furniture_mrp_stage_material_preview_wizard_form')
        return {
            'type': 'ir.actions.act_window',
            'name': _('الخامات المطلوبة للصنف'),
            'res_model': preview._name,
            'res_id': preview.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
        }

    def _prepare_material_override_commands(self, stage_code, overrides, production_line=False):
        self.ensure_one()
        commands = []
        for override in overrides or []:
            product = self.env['product.product'].browse(override.get('product_id')).exists()
            if not product:
                continue
            uom = self.env['uom.uom'].browse(override.get('product_uom_id')).exists() or product.uom_id
            qty = max(override.get('qty_needed') or 0.0, 0.0)
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            commands.append(self._prepare_material_line_command(
                product,
                uom,
                qty,
                stage=stage_code,
                production_line=production_line or False,
                quantity_mode='fixed',
            ))
        return commands

    def _apply_stage_material_overrides(self, stage_code, wizard_lines, production_lines=False):
        """Apply per-product wizard quantities to the material lines actually moved/costed."""
        self.ensure_one()
        production_lines = production_lines.exists() if production_lines else self.production_line_ids
        for wizard_line in wizard_lines:
            overrides = wizard_line.material_override_json if 'material_override_json' in wizard_line._fields else False
            if not overrides:
                continue
            production_line = (
                wizard_line.production_line_id
                if 'production_line_id' in wizard_line._fields
                else wizard_line.source_production_line_id
            )
            if (
                not production_line
                or production_line.production_id != self
                or production_line not in production_lines
            ):
                continue

            target_lines = self.material_line_ids.filtered(lambda line: (
                line.production_line_id == production_line
                and line.stage == stage_code
                and line.product_id
            ))
            override_by_key = {}
            for override in overrides:
                product = self.env['product.product'].browse(override.get('product_id')).exists()
                if not product:
                    continue
                uom = self.env['uom.uom'].browse(override.get('product_uom_id')).exists() or product.uom_id
                uom = self._get_stock_uom_for_product(product, uom)
                override_by_key[(product.id, uom.id)] = max(override.get('qty_needed') or 0.0, 0.0)

            handled_keys = set()
            for material_line in target_lines.sorted('id'):
                key = (material_line.product_id.id, material_line.product_uom_id.id)
                qty = override_by_key.get(key, 0.0)
                material_line.write({
                    'qty_needed': qty if key not in handled_keys else 0.0,
                    'quantity_mode': 'fixed',
                })
                handled_keys.add(key)

            for key, qty in override_by_key.items():
                if key in handled_keys or float_compare(qty, 0.0, precision_digits=3) <= 0:
                    continue
                product = self.env['product.product'].browse(key[0])
                uom = self.env['uom.uom'].browse(key[1])
                self.env['furniture.mrp.material.line'].create({
                    'production_id': self.id,
                    'production_line_id': production_line.id,
                    'product_id': product.id,
                    'product_uom_id': uom.id,
                    'qty_needed': qty,
                    'stage': stage_code,
                    'quantity_mode': 'fixed',
                })

    def _prepare_production_lines_for_confirm(self):
        self.ensure_one()
        lines = self._get_sorted_production_lines()
        if not lines:
            return
        single_line = len(lines) == 1
        for line in lines:
            vals = {}
            bom = self._find_bom_for_production_line(line)
            if not bom:
                if not line.furniture_order_model_id:
                    raise UserError(_('حدد الموديل أولًا للصنف: %s') % line.product_id.display_name)
                raise UserError(_(
                    'لا توجد ريسيبي للصنف %(product)s مع الموديل %(model)s.'
                ) % {
                    'product': line.product_id.display_name,
                    'model': line.furniture_order_model_id.display_name,
                })
            if line.bom_id != bom:
                vals['bom_id'] = bom.id
            for field_name, bom_value in (
                ('width_cm', bom.furniture_width_cm),
                ('depth_cm', bom.furniture_depth_cm),
                ('height_cm', bom.furniture_height_cm),
            ):
                if line[field_name]:
                    continue
                header_value = self[field_name] if single_line else 0.0
                if header_value:
                    vals[field_name] = header_value
                elif bom_value:
                    vals[field_name] = bom_value
            if vals:
                line.write(vals)

        first_line = lines[0]
        header_vals = {}
        if first_line.product_id and self.product_id != first_line.product_id:
            header_vals['product_id'] = first_line.product_id.id
        if first_line.bom_id and self.bom_id != first_line.bom_id:
            header_vals['bom_id'] = first_line.bom_id.id
        if self.furniture_order_model_id != first_line.furniture_order_model_id:
            header_vals['furniture_order_model_id'] = first_line.furniture_order_model_id.id
        if self.product_qty != first_line.product_qty:
            header_vals['product_qty'] = first_line.product_qty
        for field_name in ('width_cm', 'depth_cm', 'height_cm'):
            if self[field_name] != first_line[field_name]:
                header_vals[field_name] = first_line[field_name]
        if first_line.product_id and not self.product_base_name:
            header_vals['product_base_name'] = self._normalize_product_base_name(
                first_line.product_id.product_tmpl_id.name or first_line.product_id.name
            )
        if header_vals:
            self.write(header_vals)

    def _prepare_material_lines_from_production_lines(self):
        self.ensure_one()
        commands = []
        for line in self._get_sorted_production_lines():
            if not line.product_id:
                continue
            bom = self._find_bom_for_production_line(line)
            if not bom:
                if not line.furniture_order_model_id:
                    raise UserError(_('حدد الموديل أولًا للصنف: %s') % line.product_id.display_name)
                raise UserError(_(
                    'لا توجد ريسيبي للصنف %(product)s مع الموديل %(model)s.'
                ) % {
                    'product': line.product_id.display_name,
                    'model': line.furniture_order_model_id.display_name,
                })
            if line.bom_id != bom:
                line.bom_id = bom.id
            line_stage_codes = line._selected_stage_codes()
            commands.extend(self._prepare_material_lines_from_bom_with_factor(
                bom,
                line.product_qty,
                dimension_factor=line._get_dimension_factor(),
                production_line=line,
                active_stage_codes=line_stage_codes,
            ))
        return commands

    def _get_stock_uom_for_product(self, product, uom=False):
        self.ensure_one()
        if not product:
            return uom
        if product and uom and uom.category_id == product.uom_id.category_id:
            return uom
        return product.uom_id

    def _ensure_stock_product_is_storable(self, product, uom=False):
        if not product or product.product_tmpl_id.is_storable:
            if not uom or uom.category_id == product.uom_id.category_id:
                return
        product_tmpl = product.product_tmpl_id
        vals = {'type': 'consu', 'is_storable': True}
        try:
            product_tmpl.sudo().write(vals)
            product_tmpl.invalidate_recordset(['type', 'is_storable'])
        except UserError:
            self.env.cr.execute(
                """
                UPDATE product_template
                   SET type = 'consu',
                       is_storable = TRUE
                 WHERE id = %s
                """,
                [product_tmpl.id],
            )
            product_tmpl.invalidate_recordset(['type', 'is_storable'])

    def _get_purchase_uom_for_product(self, product, uom=False):
        self.ensure_one()
        return _normalize_purchase_uom_for_product(product, uom)

    def _get_vendor_for_material(self, product, fallback_vendor=False):
        if not product or not product.product_tmpl_id:
            return False
        return product.product_tmpl_id.furniture_supplier_id

    def _get_missing_material_purchase_lines(self):
        self.ensure_one()
        missing = []
        for line in self.material_line_ids:
            self._ensure_stock_product_is_storable(line.product_id, line.product_uom_id)
            line._compute_availability()
            shortage = line.qty_needed - line.qty_available
            if float_compare(shortage, 0.0, precision_digits=3) <= 0:
                continue
            purchase_uom = self._get_purchase_uom_for_product(line.product_id, line.product_uom_id)
            missing.append({
                'line': line,
                'shortage': shortage,
                'vendor': self._get_vendor_for_material(line.product_id),
                'order_line': (0, 0, {
                    'product_id': line.product_id.id,
                    'product_uom': purchase_uom.id,
                    'product_qty': shortage,
                    'price_unit': self._get_purchase_material_unit_cost(line.product_id, purchase_uom),
                    'name': line.product_id.display_name,
                    'date_planned': self.date_planned_start or fields.Datetime.now(),
                }),
            })
        return missing

    def _upsert_missing_vendor_purchase_order(self, order_lines):
        self.ensure_one()
        purchase_order = self.missing_vendor_purchase_order_id
        if purchase_order and purchase_order.state in ('draft', 'sent') and not purchase_order.partner_id:
            purchase_order.write({
                'origin': self.name,
                'order_line': [(5, 0, 0)] + order_lines,
            })
        else:
            purchase_order = self.env['purchase.order'].create({
                'partner_id': False,
                'origin': self.name,
                'order_line': order_lines,
            })
        self.write({'missing_vendor_purchase_order_id': purchase_order.id})
        self.purchase_order_ids = [(4, purchase_order.id)]
        return purchase_order

    # ─── إجراءات الحالة الرئيسية ─────────────────────────────────────────────
    def action_confirm(self):
        """تأكيد أمر التشغيل الأسبوعي وتحميل مكونات BoM"""
        non_draft_orders = self.filtered(lambda production: production.state != 'draft')
        if non_draft_orders:
            raise UserError(_('يمكن تأكيد أوامر المسودة فقط.'))
        missing_planned_dates = self.filtered(
            lambda production: (
                not production.date_planned_start
                or not production.date_planned_finish
            )
        )
        if missing_planned_dates:
            production = missing_planned_dates[0]
            missing_field_labels = []
            if not production.date_planned_start:
                missing_field_labels.append(_('تاريخ البدء المخطط'))
            if not production.date_planned_finish:
                missing_field_labels.append(_('تاريخ الانتهاء المخطط'))
            raise ValidationError(_(
                'لا يمكن تأكيد أمر الإنتاج %(order)s قبل إدخال: %(fields)s.'
            ) % {
                'order': production.display_name,
                'fields': '، '.join(missing_field_labels),
            })
        for rec in self:
            if not rec.production_line_ids and not self.env.context.get('furniture_skip_empty_order_warning'):
                return rec._open_empty_order_confirmation_wizard()
            rec._normalize_order_stock_moves()
            has_product_lines = bool(rec.production_line_ids)
            if has_product_lines:
                rec._prepare_production_lines_for_confirm()
                rec._consolidate_equivalent_production_lines()
            if not rec._required_stage_codes():
                raise UserError(_('اختار مرحلة واحدة على الأقل لأمر التشغيل الأسبوعي قبل التأكيد.'))
            if rec.bom_id and not rec.width_cm and rec.bom_width_cm:
                rec.width_cm = rec.bom_width_cm
            if rec.bom_id and not rec.depth_cm and rec.bom_depth_cm:
                rec.depth_cm = rec.bom_depth_cm
            if rec.bom_id and not rec.height_cm and rec.bom_height_cm:
                rec.height_cm = rec.bom_height_cm
            # في أوامر متعددة الأصناف، المنتج النهائي يتحسب لكل سطر وحده.
            if not has_product_lines and rec.product_id and not self.env['stock.move'].sudo().search([('origin', '=', rec.name), ('state', '!=', 'cancel')], limit=1):
                rec._ensure_dimensioned_product_variant()
            if rec.product_id:
                rec._sync_stage_product_name()
            # حمّل خامات المراحل المختارة في أمر الأسبوع فقط.
            rec._refresh_material_lines_for_stage_plan()
            for line in rec.material_line_ids:
                rec._ensure_stock_product_is_storable(line.product_id, line.product_uom_id)
            rec.write({
                'state': 'confirmed',
                'stage_plan_line_id': False,
                'dimension_line_id': False,
            })
            rec.message_post(body=_('✅ تم تأكيد أمر التشغيل الأسبوعي - بانتظار فحص توفر المواد'))
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _open_empty_order_confirmation_wizard(self):
        self.ensure_one()
        wizard = self.env['furniture.mrp.empty.confirm.wizard'].create({
            'production_id': self.id,
            'warning_message': _(
                'أمر التشغيل ده لسه فاضي من أصناف أمر الإنتاج. '
                'لو ضغطت OK هيتم التأكيد عادي، ولو Cancel هترجع للفورم من غير أي تغيير.'
            ),
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_empty_confirm_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('تأكيد أمر تشغيل فارغ'),
            'res_model': 'furniture.mrp.empty.confirm.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            **({'view_id': view.id, 'views': [(view.id, 'form')]} if view else {}),
        }

    def action_check_materials(self):
        """فحص توفر الخامات وإنشاء طلبات شراء للمواد الناقصة"""
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError(_('يجب تأكيد الأمر أولاً.'))
        missing = self._get_missing_material_purchase_lines()
        if not missing:
            self.write({
                'missing_vendor_purchase_order_id': False,
            })
            self.message_post(body=_('✅ جميع المواد متوفرة في المخزن - يمكن بدء الإنتاج'))
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'title': _('المواد متوفرة'), 'message': _('جميع خامات الإنتاج متوفرة.'), 'type': 'success'}}
        missing_vendor_lines = []
        lines_by_vendor = {}
        for item in missing:
            vendor = item['vendor']
            if vendor:
                lines_by_vendor.setdefault(vendor, []).append(item['order_line'])
            else:
                missing_vendor_lines.append(item)

        purchase_orders = self.env['purchase.order']
        for vendor, order_lines in lines_by_vendor.items():
            purchase_orders |= self.env['purchase.order'].create({
                'partner_id': vendor.id,
                'origin': self.name,
                'order_line': order_lines,
            })

        if missing_vendor_lines:
            blank_po = self._upsert_missing_vendor_purchase_order([
                item['order_line'] for item in missing_vendor_lines
            ])
            purchase_orders |= blank_po
            self.message_post(body=_(
                '🛒 تم تجهيز طلب شراء بدون مورد للخامات الناقصة. '
                'استخدم الزر الموجود أسفل قسم توفر المواد لفتحه.'
            ))
            if purchase_orders:
                self.purchase_order_ids = [(4, po.id) for po in purchase_orders]
            return {'type': 'ir.actions.client', 'tag': 'reload'}

        self.write({'missing_vendor_purchase_order_id': False})
        self.purchase_order_ids = [(4, po.id) for po in purchase_orders]
        self.message_post(
            body=_('🛒 تم إنشاء %d طلب شراء لـ %d مواد ناقصة') % (len(purchase_orders), len(missing))
        )
        if len(purchase_orders) > 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('طلبات الشراء'),
                'res_model': 'purchase.order',
                'domain': [('id', 'in', purchase_orders.ids)],
                'view_mode': 'list,form',
            }
        po = purchase_orders[:1]
        return {
            'type': 'ir.actions.act_window',
            'name': _('طلب الشراء'),
            'res_model': 'purchase.order',
            'res_id': po.id,
            'view_mode': 'form',
        }

    def _create_first_production_line(self, product, product_qty=1.0, bom=None,
                                      furniture_model=None,
                                      width_cm=None, depth_cm=None, height_cm=None,
                                      buyer_partner=None, beneficiary_partner=None):
        """إنشاء أول صنف في أمر التشغيل مع تعبئة بياناته الأساسية"""
        self.ensure_one()
        if not product:
            raise UserError(_('لازم تختار صنف صالح.'))

        candidate_bom = bom or self.bom_id
        furniture_model = (
            furniture_model
            or (candidate_bom.furniture_model_id if candidate_bom else False)
            or self.furniture_order_model_id
        )
        exact_bom = self._find_bom_for_product(product, furniture_model)
        bom = exact_bom or (candidate_bom if not furniture_model else False)
        furniture_model = furniture_model or (bom.furniture_model_id if bom else False)
        if not furniture_model:
            raise UserError(_('اختار الموديل قبل إنشاء الصنف الأول.'))
        if not bom:
            raise UserError(_(
                'لا توجد ريسيبي للصنف %(product)s مع الموديل %(model)s.'
            ) % {
                'product': product.display_name,
                'model': furniture_model.display_name,
            })
        source_product = product.furniture_dimension_source_product_id or product
        if bom.furniture_product_id != source_product or (
            bom.furniture_model_id and bom.furniture_model_id != furniture_model
        ):
            raise UserError(_('الريسيبي المختارة لا تطابق المنتج والموديل.'))
        width_cm = width_cm if width_cm not in (None, False) else (bom.furniture_width_cm if bom else self.width_cm)
        depth_cm = depth_cm if depth_cm not in (None, False) else (bom.furniture_depth_cm if bom else self.depth_cm)
        height_cm = height_cm if height_cm not in (None, False) else (bom.furniture_height_cm if bom else self.height_cm)
        product_qty = product_qty or self.product_qty or 1.0

        self.env['furniture.mrp.production.line'].create({
            'production_id': self.id,
            'sequence': 10,
            'product_id': product.id,
            'furniture_order_model_id': furniture_model.id,
            'buyer_partner_id': buyer_partner.id if buyer_partner else False,
            'beneficiary_partner_id': beneficiary_partner.id if beneficiary_partner else False,
            'product_qty': product_qty,
            'bom_id': bom.id if bom else False,
            'width_cm': width_cm or 0.0,
            'depth_cm': depth_cm or 0.0,
            'height_cm': height_cm or 0.0,
        })
        header_vals = {}
        if not self.product_id:
            header_vals['product_id'] = product.id
        if bom and not self.bom_id:
            header_vals['bom_id'] = bom.id
        if not self.furniture_order_model_id:
            header_vals['furniture_order_model_id'] = furniture_model.id
        if not self.product_base_name:
            header_vals['product_base_name'] = self._normalize_product_base_name(
                product.product_tmpl_id.name or product.name
            )
        if header_vals:
            self.write(header_vals)
        self.message_post(body=_('🧾 تم إنشاء أول صنف في أمر التشغيل: %s') % product.display_name)
        return True

    def action_quick_add_first_production_line(self):
        """Open the model-first batch editor for the first production lines."""
        self.ensure_one()
        if self.production_line_ids:
            return {'type': 'ir.actions.client', 'tag': 'reload'}
        return self.action_open_product_batch_wizard()

    def action_open_product_batch_wizard(self):
        """Add several finished products for one furniture model in one pass."""
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(_('لا يمكن إضافة أصناف إلى أمر تشغيل منتهي أو ملغي.'))

        existing_models = self.production_line_ids.mapped('furniture_order_model_id')
        existing_models = existing_models.filtered(lambda model: model)
        if len(existing_models) > 1:
            raise UserError(_(
                'أمر التشغيل يحتوي بالفعل على أكثر من موديل. وحّد الموديلات أولًا قبل الإضافة الجماعية.'
            ))
        default_model = existing_models[:1] or self.furniture_order_model_id
        wizard = self.env['furniture.mrp.production.first.line.wizard'].create({
            'production_id': self.id,
            'furniture_order_model_id': default_model.id if default_model else False,
            'buyer_partner_id': self.buyer_partner_id.id or False,
            'beneficiary_partner_id': self.beneficiary_partner_id.id or False,
        })
        wizard_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_first_line_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('إضافة أصناف للموديل'),
            'res_model': 'furniture.mrp.production.first.line.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            **({'view_id': wizard_view.id, 'views': [(wizard_view.id, 'form')]} if wizard_view else {}),
        }

    def action_consume_materials(self):
        """زر قديم: السحب أصبح من زر بدء كل مرحلة حسب خاماتها."""
        self.ensure_one()
        raise UserError(_('السحب يتم الآن من زر بدء كل مرحلة حسب خامات القسم المحددة في مكونات الإنتاج.'))

    # ─── انتقالات المراحل ────────────────────────────────────────────────────
    def _finalize_stock_move(self, move, quantity):
        move = move.sudo()
        if move.state == 'done':
            return move
        move_uom = self._get_stock_uom_for_product(move.product_id, move.product_uom)
        if move.product_uom != move_uom:
            move.write({'product_uom': move_uom.id})
        if move.state == 'draft':
            move._action_confirm()
        move._action_assign()
        move.quantity = quantity
        move.picked = True
        move._action_done()
        return move

    def _create_internal_moves_batch(self, move_specs, defer_done=False):
        """Create traceable moves and optionally leave them reserved for acceptance."""
        self.ensure_one()
        move_specs = list(move_specs or [])
        if not move_specs:
            return []

        prepared_specs = []
        required_qty_by_key = {}
        availability_record_by_key = {}
        for spec in move_specs:
            source_location = spec.get('source_location')
            dest_location = spec.get('dest_location')
            product = spec.get('product') or self.product_id
            quantity = spec.get('quantity')
            quantity = self.product_qty if quantity is None else quantity
            uom = spec.get('uom') or self.product_uom_id or product.uom_id
            if not source_location or not dest_location:
                raise UserError(_('يجب تحديد مخازن المراحل قبل تنفيذ التحويل.'))
            self._ensure_stock_product_is_storable(product, uom)
            stock_uom = self._get_stock_uom_for_product(product, uom)
            move_vals = {
                'name': f"{self.name} - {spec.get('label') or product.display_name}",
                'product_id': product.id,
                'product_uom': stock_uom.id,
                'product_uom_qty': quantity,
                'location_id': source_location.id,
                'location_dest_id': dest_location.id,
                'origin': self.name,
            }
            source_line = spec.get('source_production_line')
            if source_line:
                move_vals['furniture_source_production_line_id'] = source_line.id
            source_lines = spec.get('source_production_lines')
            if source_lines:
                move_vals['furniture_source_production_line_ids'] = [
                    (6, 0, source_lines.ids),
                ]
            origin_returned_move = spec.get('origin_returned_move')
            if origin_returned_move:
                move_vals['origin_returned_move_id'] = origin_returned_move.id
            if spec.get('price_unit') is not None:
                move_vals['price_unit'] = spec['price_unit']
            prepared_specs.append((spec, move_vals, quantity))

            if source_location.usage != 'production':
                availability_key = (source_location.id, product.id)
                required_qty_by_key[availability_key] = (
                    required_qty_by_key.get(availability_key, 0.0)
                    + self._quantity_in_product_uom(product, quantity, stock_uom)
                )
                availability_record_by_key[availability_key] = (source_location, product)

        for availability_key, required_qty in required_qty_by_key.items():
            source_location, product = availability_record_by_key[availability_key]
            available_qty = self._stage_location_product_qty(source_location, product)
            if float_compare(available_qty, required_qty, precision_digits=3) < 0:
                raise UserError(_(
                    'لا يوجد رصيد كافي من %s في %s. المتاح %s والمطلوب %s.'
                ) % (
                    product.display_name,
                    source_location.display_name,
                    self._format_dimension_value(available_qty),
                    self._format_dimension_value(required_qty),
                ))

        moves = self.env['stock.move'].sudo().create([
            move_vals for _spec, move_vals, _quantity in prepared_specs
        ])
        draft_moves = moves.filtered(lambda move: move.state == 'draft')
        if draft_moves:
            draft_moves._action_confirm(merge=False)
        moves._action_assign()
        if defer_done:
            unavailable = moves.filtered(lambda move: move.state != 'assigned')
            if unavailable:
                raise UserError(_(
                    'تعذر حجز كامل كمية تحويل المرحلة قبل إرسالها للمشرف.'
                ))
            return [
                (spec, move)
                for move, (spec, _move_vals, _quantity)
                in zip(moves, prepared_specs)
            ]
        for move, (_spec, _move_vals, quantity) in zip(moves, prepared_specs):
            move.quantity = quantity
        moves.write({'picked': True})
        moves._action_done()
        return [
            (spec, move)
            for move, (spec, _move_vals, _quantity) in zip(moves, prepared_specs)
        ]

    def _normalize_order_stock_moves(self):
        self.ensure_one()
        moves = self.env['stock.move'].sudo().search([
            ('origin', '=', self.name),
            ('state', 'in', ('draft', 'confirmed')),
        ])
        for move in moves:
            move_uom = self._get_stock_uom_for_product(move.product_id, move.product_uom)
            if move.product_uom != move_uom:
                move.write({'product_uom': move_uom.id})
        return moves

    def _receive_wip_product_at_first_stage(self):
        self.ensure_one()
        self._ensure_stage_locations()
        moves = self.env['stock.move']
        for line in self._stage_material_lines('furniture.mrp.priming'):
            existing_move = self.env['stock.move'].sudo().search([
                ('origin', '=', self.name),
                ('product_id', '=', line.product_id.id),
                ('location_dest_id', '=', self.location_priming_wip_id.id),
                ('state', '!=', 'cancel'),
            ], limit=1)
            if existing_move:
                moves |= self._finalize_stock_move(existing_move, line.qty_needed)
                if not line.move_id:
                    line.move_id = existing_move.id
                continue
            move = self._create_internal_move(
                self.location_src_id or self.env.ref('stock.stock_location_stock'),
                self.location_priming_wip_id,
                _('سحب خامات أمر التشغيل الأسبوعي إلى صالة تصنيع التقديم'),
                product=line.product_id,
                quantity=line.qty_needed,
                uom=line.product_uom_id,
            )
            moves |= move
            if not line.move_id:
                line.move_id = move.id
        if moves:
            self.message_post(body=_('📦 تم إدخال الخامات إلى صالة تصنيع التقديم'))
        return moves

    def _ensure_raw_materials_at_first_stage(self):
        self.ensure_one()
        self._ensure_no_material_receipt_waiting()
        self._ensure_stage_locations()
        for line in self._stage_material_lines('furniture.mrp.priming'):
            first_stage_move = self.env['stock.move'].sudo().search([
                ('origin', '=', self.name),
                ('product_id', '=', line.product_id.id),
                ('location_dest_id', '=', self.location_priming_wip_id.id),
                ('state', '=', 'done'),
            ], limit=1)
            if first_stage_move:
                continue
            source_location = line.move_id.location_dest_id if line.move_id else self._get_wip_location()
            if not source_location:
                source_location = self._get_wip_location()
            self._create_internal_move(
                source_location,
                self.location_priming_wip_id,
                _('تصحيح دخول الخامات إلى صالة تصنيع التقديم'),
                move_type='stage_transfer',
                product=line.product_id,
                quantity=line.qty_needed,
                uom=line.product_uom_id,
            )

    def _ensure_no_material_receipt_waiting(self):
        """Never let technical repair bypass the physical handover gate."""
        self.ensure_one()
        waiting_labels = []
        advance_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', self.id),
            ('state', '=', 'issued'),
            ('receipt_confirmed', '=', False),
        ])
        if advance_stages:
            stage_labels = dict(FURNITURE_STAGE_SELECTION)
            waiting_labels.extend(
                stage_labels.get(stage.stage_code, stage.stage_code)
                for stage in advance_stages
            )

        StoreRequest = self.env['furniture.mrp.store.request'].sudo()
        if 'receipt_confirmed' in StoreRequest._fields:
            store_requests = StoreRequest.search([
                ('production_id', '=', self.id),
                ('state', '=', 'approved'),
                ('receipt_confirmed', '=', False),
            ])
            waiting_labels.extend(store_requests.mapped('stage_code'))

        if waiting_labels:
            raise UserError(_(
                'لا يمكن إصلاح أو تحريك خامات أمر الإنتاج بينما توجد خامات '
                'في عهدة التسليم بانتظار استلام مسؤول الإنتاج: %s'
            ) % '، '.join(dict.fromkeys(waiting_labels)))
        return True

    def _create_internal_move(
        self,
        source_location,
        dest_location,
        label,
        move_type='stage_transfer',
        product=None,
        quantity=None,
        uom=None,
        source_production_line=False,
        price_unit=None,
    ):
        self.ensure_one()
        self._ensure_stage_locations()
        if not source_location or not dest_location:
            raise UserError(_('يجب تحديد مخازن المراحل قبل تنفيذ التحويل.'))
        product = product or self.product_id
        self._ensure_stock_product_is_storable(product, uom)
        quantity = quantity if quantity is not None else self.product_qty
        uom = uom or self.product_uom_id
        uom = self._get_stock_uom_for_product(product, uom)
        move_vals = {
            'name': f'{self.name} - {label}',
            'product_id': product.id,
            'product_uom': uom.id,
            'product_uom_qty': quantity,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': self.name,
        }
        if source_production_line:
            move_vals['furniture_source_production_line_id'] = source_production_line.id
        if price_unit is not None:
            move_vals['price_unit'] = price_unit
        move = self.env['stock.move'].sudo().create(move_vals)
        return self._finalize_stock_move(move, quantity)

    def _stage_model_to_code(self, stage_model):
        return {
            'furniture.mrp.priming': 'priming',
            'furniture.mrp.painting': 'painting',
            'furniture.mrp.carpentry': 'carpentry',
            'furniture.mrp.bases': 'bases',
            'furniture.mrp.finishing': 'finishing',
            'furniture.mrp.tailoring': 'tailoring',
            'furniture.mrp.sewing': 'sewing',
            'furniture.mrp.upholstery': 'upholstery',
            'furniture.mrp.packaging': 'packaging',
        }.get(stage_model)

    def _stage_material_lines(self, stage_model):
        stage_code = self._stage_model_to_code(stage_model)
        return self.material_line_ids.filtered(
            lambda l: l.product_id and l.qty_needed and l.stage == stage_code
        )

    def _stage_model_from_code(self, stage_code):
        return {
            'priming': 'furniture.mrp.priming',
            'painting': 'furniture.mrp.painting',
            'carpentry': 'furniture.mrp.carpentry',
            'bases': 'furniture.mrp.bases',
            'finishing': 'furniture.mrp.finishing',
            'tailoring': 'furniture.mrp.tailoring',
            'sewing': 'furniture.mrp.sewing',
            'upholstery': 'furniture.mrp.upholstery',
            'packaging': 'furniture.mrp.packaging',
        }.get(stage_code)

    def _stage_storage_location(self, stage_code):
        self.ensure_one()
        return {
            'priming': self.location_priming_id,
            'painting': self.location_painting_id,
            'carpentry': self.location_carpentry_id,
            'bases': self.location_bases_id,
            'finishing': self.location_finishing_id,
            'tailoring': self.location_tailoring_id,
            'sewing': self.location_sewing_id,
            'upholstery': self.location_upholstery_id,
            'packaging': self.location_packaging_id,
        }.get(stage_code)

    def _stage_work_location(self, stage_code):
        self.ensure_one()
        return {
            'priming': self.location_priming_wip_id,
            'painting': self.location_painting_wip_id,
            'carpentry': self.location_carpentry_wip_id,
            'bases': self.location_bases_wip_id,
            'finishing': self.location_finishing_wip_id,
            'tailoring': self.location_tailoring_wip_id,
            'sewing': self.location_sewing_wip_id,
            'upholstery': self.location_upholstery_wip_id,
            'packaging': self.location_packaging_id,
        }.get(stage_code)

    def _next_physical_stage_for_line(self, production_line, source_stage):
        """Return the next real hall in this exact line's selected route."""
        self.ensure_one()
        production_line = production_line.exists()
        if not production_line or production_line.production_id != self:
            return False
        route = self._physical_stage_codes(
            production_line._selected_stage_codes()
        )
        if source_stage not in route:
            return False
        source_index = route.index(source_stage)
        return route[source_index + 1] if source_index + 1 < len(route) else False

    def _auto_transfer_completed_stage_lines(
        self, source_stage, production_lines,
    ):
        """Reserve accepted outputs until the next-stage supervisor accepts.

        Quality approval moves output into the current stage store.  The next
        move is only assigned here; its stock is moved into the next hall by
        the destination supervisor's explicit acceptance.
        """
        self.ensure_one()
        production_lines = production_lines.exists()
        if (
            not production_lines
            or self._is_material_only_stage(source_stage)
        ):
            return self.env['stock.move']

        source_order = self._stage_order_record(source_stage)
        if not source_order:
            return self.env['stock.move']
        completed_lines = source_order._get_stage_line_ids_data(
            'completed_production_line_ids_data'
        )
        eligible_lines = production_lines & completed_lines
        if not eligible_lines:
            return self.env['stock.move']

        self._ensure_stage_locations()
        source_location = self._stage_storage_location(source_stage)
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        move_specs = []
        target_codes = set()
        target_by_line_id = {}
        for production_line in eligible_lines.sorted('id'):
            target_stage = self._next_physical_stage_for_line(
                production_line, source_stage,
            )
            if not target_stage:
                continue
            target_by_line_id[production_line.id] = target_stage

        # Use the physical stock payload written by stage completion instead
        # of rebuilding an output product from the production line.  Body and
        # cover orders deliberately replace the final product with a technical
        # lane-WIP product (FWIP-*).  Rebuilding the spec here used to look for
        # the final product, leave the real WIP in the source store, and make
        # the completed line disappear from the next-stage dashboard.
        storage_payloads = self._get_stage_storage_content_payloads(
            source_stage,
        )
        for payload in storage_payloads:
            production_line = payload.get('source_production_line')
            if (
                not production_line
                or production_line.id not in target_by_line_id
                or production_line.production_id != self
                or payload.get('source_production') != self
            ):
                continue
            target_stage = target_by_line_id[production_line.id]
            target_location = self._stage_work_location(target_stage)
            if not source_location or not target_location:
                raise UserError(_(
                    'مخزن المرحلة الحالية أو صالة المرحلة التالية غير محدد.'
                ))
            source_label = stage_labels.get(source_stage, source_stage)
            target_label = stage_labels.get(target_stage, target_stage)
            product = payload['product']
            quantity = self._quantity_in_product_uom(
                product,
                payload.get('qty') or 0.0,
                payload.get('uom') or product.uom_id,
            )
            if float_compare(quantity, 0.0, precision_digits=3) <= 0:
                continue
            move_specs.append({
                'source_location': source_location,
                'dest_location': target_location,
                'label': _(
                    'تحويل المنتج شبه النهائي تلقائيًا من %s إلى %s'
                ) % (source_label, target_label),
                'product': product,
                'quantity': quantity,
                'uom': product.uom_id,
                'source_production_line': production_line,
                'source_production_lines': production_line,
                'target_stage': target_stage,
            })
            target_codes.add(target_stage)

        moves = self.env['stock.move']
        StageHandoff = self.env[
            'furniture.mrp.stage.transfer.handoff'
        ].sudo()
        for target_stage in target_codes:
            target_specs = [
                spec for spec in move_specs
                if spec['target_stage'] == target_stage
            ]
            target_lines = self.env['furniture.mrp.production.line']
            for spec in target_specs:
                target_lines |= spec['source_production_line']
            already_pending_lines = StageHandoff.search([
                ('production_id', '=', self.id),
                ('source_stage', '=', source_stage),
                ('target_stage', '=', target_stage),
                ('state', '=', 'pending'),
                ('production_line_ids', 'in', target_lines.ids),
            ]).mapped('production_line_ids')
            target_specs = [
                spec for spec in target_specs
                if spec['source_production_line'] not in already_pending_lines
            ]
            if not target_specs:
                continue
            handoff = StageHandoff._create_pending_transfer(
                self,
                source_stage,
                target_stage,
                target_specs,
            )
            moves |= handoff.move_ids
        return moves

    def _stage_order_record(self, stage_code):
        self.ensure_one()
        field_names = FURNITURE_STAGE_FIELD_MAP.get(stage_code)
        return self[field_names[1]] if field_names else False

    def _stage_location_product_qty(self, location, product, company=None):
        self.ensure_one()
        if not location or not product:
            return 0.0
        quant_domain = [
            ('location_id', '=', location.id),
            ('product_id', '=', product.id),
        ]
        company = company or self.company_id
        if company:
            quant_domain.append(('company_id', '=', company.id))
        return sum(self.env['stock.quant'].sudo().search(quant_domain).mapped('quantity'))

    def _get_stage_work_carryover_payloads(self, stage_code, subtract_current_specs=False):
        """يرجع الشغل القديم الموجود فعلاً في صالة المرحلة.

        بنعتمد على حركات "تحويل المنتج شبه النهائي" بدل اسم أمر التشغيل، عشان
        القطع تفضل قابلة للاكتشاف حتى لو أمر التشغيل القديم اتمسح بعد التحويل.
        """
        self.ensure_one()
        work_location = self._stage_work_location(stage_code)
        if not work_location:
            return []

        move_domain = [
            ('location_dest_id', '=', work_location.id),
            ('state', '=', 'done'),
        ]
        if self.company_id:
            move_domain.append(('company_id', '=', self.company_id.id))
        candidate_moves = self.env['stock.move'].sudo().search(move_domain, order='date desc, id desc')
        representative_move_by_product = {}
        candidate_products = self.env['product.product']
        for move in candidate_moves:
            if not move.product_id or 'تحويل المنتج شبه النهائي' not in (move.name or ''):
                continue
            candidate_products |= move.product_id
            representative_move_by_product.setdefault(move.product_id.id, move)
        if not candidate_products:
            return []

        quant_domain = [
            ('location_id', '=', work_location.id),
            ('product_id', 'in', candidate_products.ids),
        ]
        if self.company_id:
            quant_domain.append(('company_id', '=', self.company_id.id))

        payload_by_key = {}
        for quant in self.env['stock.quant'].sudo().search(quant_domain, order='product_id asc, id asc'):
            if not quant.product_id or float_compare(quant.quantity or 0.0, 0.0, precision_digits=3) <= 0:
                continue
            representative_move = representative_move_by_product.get(quant.product_id.id)
            entries = self._stage_location_stock_entries(
                work_location,
                quant.product_id,
                quant.quantity or 0.0,
                stage_code,
            )
            for entry in entries:
                source_production = entry.get('source_production')
                source_line = entry.get('source_production_line')
                if (
                    stage_code in ('upholstery', 'packaging')
                    and source_line
                    and self._production_line_stage_done(
                        source_line, stage_code,
                        product=quant.product_id,
                    )
                ):
                    # Upholstery and packaging both use one canonical hall as
                    # work and output storage.  Once a line completed the
                    # current stage, its new output may remain in that hall but
                    # must not be offered as another batch of work.
                    continue
                key = (quant.product_id.id, source_line.id if source_line else False)
                payload = payload_by_key.setdefault(key, {
                    'product': quant.product_id,
                    'qty': 0.0,
                    'uom': quant.product_id.uom_id,
                    'label': quant.product_id.display_name,
                    'source_origin': source_production.name if source_production else (representative_move.origin if representative_move else False),
                    'source_production': source_production,
                    'source_production_line': source_line,
                })
                payload['qty'] += entry.get('qty') or 0.0

        if subtract_current_specs:
            for spec in self._get_finished_output_specs(ensure_storable=False):
                remaining_qty = self._quantity_in_product_uom(spec['product'], spec['qty'], spec['uom'])
                for payload in payload_by_key.values():
                    if payload['product'] != spec['product'] or float_compare(remaining_qty, 0.0, precision_digits=3) <= 0:
                        continue
                    deducted_qty = min(payload['qty'], remaining_qty)
                    payload['qty'] -= deducted_qty
                    remaining_qty -= deducted_qty

        payloads = [
            payload for payload in payload_by_key.values()
            if float_compare(payload['qty'], 0.0, precision_digits=3) > 0
        ]
        return sorted(
            payloads,
            key=lambda payload: (payload['label'] or payload['product'].display_name or '', payload['product'].id),
        )

    def _stage_work_raw_material_balance(self, stage_code, work_location):
        """Return raw-material balance and its historical incoming moves.

        A single ``stock.quant`` can contain both a semi-finished product and
        raw material when they happen to use the same product record.  The UI
        must subtract only the raw share, not hide the complete quant based on
        whichever incoming move happened most recently.
        """
        self.ensure_one()
        if not stage_code or not work_location:
            return {}, self.env['stock.move']

        material_domain = [
            ('stage', '=', stage_code),
            ('move_id.state', '=', 'done'),
            ('move_id.location_dest_id', '=', work_location.id),
        ]
        if self.company_id:
            material_domain.append(
                ('production_id.company_id', '=', self.company_id.id)
            )
        material_lines = self.env['furniture.mrp.material.line'].sudo().search(
            material_domain,
        )
        incoming_domain = [
            ('location_dest_id', '=', work_location.id),
            ('state', '=', 'done'),
        ]
        if self.company_id:
            incoming_domain.append(('company_id', '=', self.company_id.id))
        incoming_moves = self.env['stock.move'].sudo().search(incoming_domain)
        raw_incoming_moves = material_lines.mapped('move_id') & incoming_moves
        if incoming_moves:
            normal_receipt_lines = self.env[
                'furniture.mrp.store.request.line'
            ].sudo().search([
                ('receipt_move_ids', 'in', incoming_moves.ids),
            ])
            advance_receipt_lines = self.env[
                'furniture.mrp.advance.material.release.line'
            ].sudo().search([
                ('receipt_move_ids', 'in', incoming_moves.ids),
            ])
            raw_incoming_moves |= (
                normal_receipt_lines.mapped('receipt_move_ids')
                | advance_receipt_lines.mapped('receipt_move_ids')
            ) & incoming_moves
            raw_incoming_moves |= incoming_moves.filtered(lambda move: (
                'استلام إنتاج فعلي' in (move.name or '')
                or 'سحب خامات' in (move.name or '')
                or move.furniture_mrp_move_type == 'raw_material'
            ))

        outgoing_domain = [
            ('location_id', '=', work_location.id),
            ('location_dest_id', '!=', work_location.id),
            ('state', '=', 'done'),
        ]
        if self.company_id:
            outgoing_domain.append(('company_id', '=', self.company_id.id))
        outgoing_moves = self.env['stock.move'].sudo().search(outgoing_domain)
        linked_consumption_moves = self.env[
            'furniture.mrp.material.line'
        ].sudo().search([
            ('move_id', 'in', outgoing_moves.ids),
        ]).mapped('move_id')
        raw_outgoing_moves = linked_consumption_moves | outgoing_moves.filtered(
            lambda move: (
                move.location_dest_id.usage == 'production'
                and (
                    'سحب خامات' in (move.name or '')
                    or 'استهلاك خامات' in (move.name or '')
                    or move.furniture_mrp_move_type in (
                        'raw_material', 'final_consumption',
                    )
                )
            )
        )

        raw_qty_by_product = {}
        for move in raw_incoming_moves:
            product = move.product_id
            raw_qty_by_product[product.id] = (
                raw_qty_by_product.get(product.id, 0.0)
                + self._quantity_in_product_uom(
                    product,
                    move.quantity or move.product_uom_qty,
                    move.product_uom,
                )
            )
        for move in raw_outgoing_moves:
            product = move.product_id
            raw_qty_by_product[product.id] = max(
                raw_qty_by_product.get(product.id, 0.0)
                - self._quantity_in_product_uom(
                    product,
                    move.quantity or move.product_uom_qty,
                    move.product_uom,
                ),
                0.0,
            )
        return raw_qty_by_product, raw_incoming_moves

    def _get_stage_work_location_payloads(self, stage_code):
        """يرجع كل المنتجات الموجودة فعلاً في صالة المرحلة فقط."""
        self.ensure_one()
        work_location = self._stage_work_location(stage_code)
        if not work_location:
            return []

        quant_domain = [
            ('location_id', '=', work_location.id),
        ]
        if self.company_id:
            quant_domain.append(('company_id', '=', self.company_id.id))

        move_domain = [
            ('location_dest_id', '=', work_location.id),
            ('state', '=', 'done'),
        ]
        if self.company_id:
            move_domain.append(('company_id', '=', self.company_id.id))
        representative_move_by_product = {}
        for move in self.env['stock.move'].sudo().search(move_domain, order='date desc, id desc'):
            if move.product_id:
                representative_move_by_product.setdefault(move.product_id.id, move)

        raw_qty_by_product, raw_incoming_moves = (
            self._stage_work_raw_material_balance(stage_code, work_location)
        )

        payload_by_key = {}
        for quant in self.env['stock.quant'].sudo().search(quant_domain, order='product_id asc, id asc'):
            if not quant.product_id or float_compare(quant.quantity or 0.0, 0.0, precision_digits=3) <= 0:
                continue
            representative_move = representative_move_by_product.get(quant.product_id.id)
            visible_qty = max(
                (quant.quantity or 0.0)
                - raw_qty_by_product.get(quant.product_id.id, 0.0),
                0.0,
            )
            if float_compare(
                visible_qty,
                0.0,
                precision_rounding=quant.product_id.uom_id.rounding or 0.001,
            ) <= 0:
                continue
            entries = self._stage_location_stock_entries(
                work_location,
                quant.product_id,
                visible_qty,
                stage_code,
                excluded_incoming_move_ids=raw_incoming_moves.ids,
            )
            for entry in entries:
                source_production = entry.get('source_production')
                source_line = entry.get('source_production_line')
                if (
                    stage_code in ('upholstery', 'packaging')
                    and source_line
                    and self._production_line_stage_done(
                        source_line, stage_code,
                        product=quant.product_id,
                    )
                ):
                    # A completed upholstery/packaging output can remain in
                    # the shared hall, but it is no longer startable WIP.
                    continue
                key = (quant.product_id.id, source_line.id if source_line else False)
                payload = payload_by_key.setdefault(key, {
                    'product': quant.product_id,
                    'qty': 0.0,
                    'uom': quant.product_id.uom_id,
                    'label': quant.product_id.display_name,
                    'source_origin': source_production.name if source_production else (representative_move.origin if representative_move else False),
                    'source_production': source_production,
                    'source_production_line': source_line,
                })
                payload['qty'] += entry.get('qty') or 0.0

        payloads = [
            payload for payload in payload_by_key.values()
            if float_compare(payload['qty'], 0.0, precision_digits=3) > 0
        ]
        return sorted(
            payloads,
            key=lambda payload: (payload['label'] or payload['product'].display_name or '', payload['product'].id),
        )

    def _get_first_stage_start_line_candidates(self, stage_code):
        self.ensure_one()
        if not stage_code or self._is_material_only_stage(stage_code):
            return self.env['furniture.mrp.production.line']
        candidates = self.env['furniture.mrp.production.line']
        for line in self._get_sorted_production_lines():
            if line.first_stage_started or not line.product_id or line.product_qty <= 0:
                continue
            selected_stages = line._selected_stage_codes()
            if (
                stage_code in selected_stages
                and self._production_line_start_stage_code(line) == stage_code
            ):
                candidates |= line
        return candidates

    def _get_stage_pending_start_line_candidates(self, stage_order, stage_code):
        self.ensure_one()
        if self._is_material_only_stage(stage_code):
            return self._get_material_only_stage_start_line_candidates(
                stage_code,
                stage_order=stage_order,
            )
        candidates = self._get_first_stage_start_line_candidates(stage_code)
        if not stage_code:
            return candidates

        excluded_lines = self.env['furniture.mrp.production.line']
        if stage_order:
            excluded_lines = (
                stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
                | stage_order._get_stage_line_ids_data('quality_production_line_ids_data')
                | stage_order._get_stage_line_ids_data('active_production_line_ids_data')
            )

        for payload in self._get_stage_work_location_payloads(stage_code):
            source_line = payload.get('source_production_line')
            if (
                source_line
                and source_line.production_id == self
                and source_line not in excluded_lines
                and stage_code in source_line._selected_stage_codes()
            ):
                candidates |= source_line
        return candidates

    def _reopen_stage_order_for_pending_start(self, stage_order, stage_code):
        self.ensure_one()
        if not stage_order or not stage_code:
            return self.env['furniture.mrp.production.line']
        pending_lines = self._get_stage_pending_start_line_candidates(stage_order, stage_code)
        # Reopening an existing stage order reserves any newly appended,
        # unstarted batch for that same entry stage.  Without this persisted
        # choice the generic selector could plan the very same batch into a
        # second stage while the reopened order is already waiting for it.
        pending_lines.filtered(lambda line: (
            not line.first_stage_started
            and self._production_line_start_stage_code(line) == stage_code
            and line.planned_start_stage != stage_code
        )).write({'planned_start_stage': stage_code})
        if pending_lines and stage_order.state == 'done':
            stage_order.write({
                'state': 'pending',
                'quality_check': 'pending',
                'date_finish': False,
            })
        return pending_lines

    def _open_first_stage_start_wizard(self, stage_order, stage_code, production_lines):
        self.ensure_one()
        if not stage_order or not production_lines:
            return False
        grouped_lines = self._group_production_lines_by_display_family(production_lines)
        wizard = self.env['furniture.mrp.first.stage.start.wizard'].create({
            'production_id': self.id,
            'stage_code': stage_code,
            'stage_order_model': stage_order._name,
            'stage_order_res_id': stage_order.id,
            'line_ids': [(0, 0, {
                'production_line_id': group['representative'].id,
                'technical_production_line_ids': [(6, 0, group['lines'].ids)],
                'selected': True,
                'qty_to_start': group['quantity'],
            }) for group in grouped_lines],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_first_stage_start_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('بدء أول مرحلة من المخزن'),
            'res_model': 'furniture.mrp.first.stage.start.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'view_id': view.id,
            'views': [(view.id, 'form')],
            'target': 'new',
            'context': dict(self.env.context, form_view_initial_mode='edit'),
        }

    def _get_stage_quality_candidate_lines(self, stage_order, stage_code):
        self.ensure_one()
        if not stage_order or not stage_code:
            return self.env['furniture.mrp.production.line']
        completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
        quality_lines = stage_order._get_stage_line_ids_data('quality_production_line_ids_data')
        active_lines = stage_order._get_stage_line_ids_data('active_production_line_ids_data')
        if active_lines:
            return active_lines.filtered(lambda line: stage_code in line._selected_stage_codes()) - completed_lines - quality_lines

        # Never infer quality candidates from the whole production order. Only
        # lines physically attributed to this stage hall may reach quality.
        candidates = self.env['furniture.mrp.production.line']
        for payload in self._get_stage_work_location_payloads(stage_code):
            source_line = payload.get('source_production_line')
            if (
                source_line
                and source_line.production_id == self
                and stage_code in source_line._selected_stage_codes()
            ):
                candidates |= source_line
        return candidates - completed_lines - quality_lines

    def _stage_quality_group_header_label(
        self,
        kit_bom,
        instance_number,
        furniture_model,
        loose=False,
    ):
        """Return the persisted Kit label used by the quality selector.

        Quality must never rebuild or guess Kit identities from quantities.
        The label is therefore based only on the Kit metadata already stored
        on each physical production-line piece.
        """
        self.ensure_one()
        model_label = furniture_model.name if furniture_model else _('بدون موديل')
        if loose:
            return _('منتجات منفردة — %s') % model_label
        kit_product = kit_bom.furniture_product_id or kit_bom.product_id
        kit_label = (
            kit_product.display_name
            if kit_product else kit_bom.product_tmpl_id.display_name or _('Kit')
        )
        return _('%s %s') % (kit_label, instance_number)

    def _prepare_stage_quality_send_line_commands(self, stage_code, production_lines):
        """Prepare a display plan without touching production or stock.

        Tailoring and upholstery use one persistent production line for every
        physical Kit component.  The generic quality selector used to flatten
        those pieces, hiding the saved Kit identity.  Header rows restore that
        identity visually while every selectable child remains tied one-to-one
        to its original production line.
        """
        self.ensure_one()
        production_lines = production_lines.exists().filtered(
            lambda line: line.active and line.production_id == self
        )
        ordered_lines = production_lines.sorted(self._production_line_sort_key)
        if stage_code not in ('tailoring', 'sewing', 'upholstery'):
            commands = []
            sequence = 10
            for group in self._group_production_lines_by_display_family(ordered_lines):
                commands.append((0, 0, {
                    'sequence': sequence,
                    'production_line_id': group['representative'].id,
                    'technical_production_line_ids': [(6, 0, group['lines'].ids)],
                    'selected': True,
                    'qty_to_send': group['quantity'],
                }))
                sequence += 10
            return commands

        groups = {}
        for line in ordered_lines:
            furniture_model = (
                line.furniture_order_model_id
                or line.product_id.furniture_model_id
            )
            if line.kit_bom_id and line.kit_instance_number:
                token = 'quality:kit:%s:%s:%s' % (
                    self.id,
                    line.kit_bom_id.id,
                    line.kit_instance_number,
                )
                group = groups.setdefault(token, {
                    'token': token,
                    'kit_bom': line.kit_bom_id,
                    'instance_number': line.kit_instance_number,
                    'model': furniture_model,
                    'buyer': line.buyer_partner_id,
                    'beneficiary': line.beneficiary_partner_id,
                    'loose': False,
                    'lines': self.env['furniture.mrp.production.line'],
                })
            else:
                model_id = furniture_model.id if furniture_model else 0
                token = 'quality:loose:%s:%s' % (self.id, model_id)
                group = groups.setdefault(token, {
                    'token': token,
                    'kit_bom': self.env['mrp.bom'],
                    'instance_number': 0,
                    'model': furniture_model,
                    'buyer': self.env['res.partner'],
                    'beneficiary': self.env['res.partner'],
                    'loose': True,
                    'lines': self.env['furniture.mrp.production.line'],
                })
            group['lines'] |= line

        ordered_groups = sorted(groups.values(), key=lambda group: (
            group['loose'],
            (
                (
                    group['kit_bom'].furniture_product_id
                    or group['kit_bom'].product_id
                    or group['kit_bom'].product_tmpl_id
                ).display_name
                if group['kit_bom'] else _('منتجات منفردة')
            ).casefold(),
            group['instance_number'],
            (group['model'].name or _('بدون موديل')).casefold(),
        ))

        commands = []
        sequence = 10
        for group in ordered_groups:
            commands.append((0, 0, {
                'sequence': sequence,
                'is_model_header': True,
                'selected': False,
                'model_group_label': self._stage_quality_group_header_label(
                    group['kit_bom'],
                    group['instance_number'],
                    group['model'],
                    loose=group['loose'],
                ),
                'kit_group_token': group['token'],
                'kit_bom_id': group['kit_bom'].id if group['kit_bom'] else False,
                'kit_instance_number': group['instance_number'],
                'furniture_model_id': group['model'].id if group['model'] else False,
                'buyer_partner_id': group['buyer'].id,
                'beneficiary_partner_id': group['beneficiary'].id,
            }))
            sequence += 10
            for line in group['lines'].sorted(self._production_line_sort_key):
                furniture_model = (
                    line.furniture_order_model_id
                    or line.product_id.furniture_model_id
                )
                commands.append((0, 0, {
                    'sequence': sequence,
                    'production_line_id': line.id,
                    'selected': True,
                    'kit_group_token': group['token'],
                    'kit_bom_id': line.kit_bom_id.id,
                    'kit_instance_number': line.kit_instance_number,
                    'furniture_model_id': furniture_model.id if furniture_model else False,
                    'buyer_partner_id': line.buyer_partner_id.id,
                    'beneficiary_partner_id': line.beneficiary_partner_id.id,
                }))
                sequence += 10
        return commands

    def _open_stage_quality_send_wizard(self, stage_order, stage_code, production_lines):
        self.ensure_one()
        if not stage_order or not production_lines:
            return False
        line_commands = self._prepare_stage_quality_send_line_commands(
            stage_code,
            production_lines,
        )
        wizard = self.env['furniture.mrp.stage.quality.send.wizard'].create({
            'production_id': self.id,
            'stage_code': stage_code,
            'stage_order_model': stage_order._name,
            'stage_order_res_id': stage_order.id,
            'line_ids': line_commands,
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_quality_send_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('اختيار أصناف الجودة'),
            'res_model': 'furniture.mrp.stage.quality.send.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': dict(self.env.context, form_view_initial_mode='edit'),
            **({'view_id': view.id, 'views': [(view.id, 'form')]} if view else {}),
        }

    def _get_or_create_first_stage_material_lines(self, production_lines, stage_code):
        self.ensure_one()
        material_lines = self.material_line_ids.filtered(
            lambda line: (
                line.production_line_id in production_lines
                and line.stage == stage_code
                and line.product_id
                and line.qty_needed
            )
        )
        missing_lines = production_lines - material_lines.mapped('production_line_id')
        material_commands = []
        for production_line in missing_lines:
            bom = self._find_bom_for_product(
                production_line.product_id,
                production_line.furniture_order_model_id,
            )
            if not bom:
                if not production_line.furniture_order_model_id:
                    raise UserError(_(
                        'حدد الموديل أولًا للصنف: %s'
                    ) % production_line.product_id.display_name)
                raise UserError(_('لا توجد BoM للصنف %s لتجهيز خامات أول مرحلة.') % production_line.product_id.display_name)
            material_commands.extend(self._prepare_material_lines_from_bom_with_factor(
                bom,
                production_line.product_qty,
                dimension_factor=production_line._get_dimension_factor(),
                production_line=production_line,
                active_stage_codes=[stage_code],
            ))
        if material_commands:
            existing_lines = self.material_line_ids
            self.write({'material_line_ids': material_commands})
            material_lines |= self.material_line_ids - existing_lines
        return material_lines.filtered(
            lambda line: (
                line.production_line_id in production_lines
                and line.stage == stage_code
                and line.product_id
                and line.qty_needed
            )
        )

    def _start_first_stage_lines_from_stock(self, stage_order, stage_code, wizard_lines):
        self.ensure_one()
        selected_wizard_lines = wizard_lines.filtered(lambda line: line.selected and line.production_line_id)
        if not selected_wizard_lines:
            raise UserError(_('اختار صنف واحد على الأقل يبدأ أول مرحلة من المخزن.'))
        if selected_wizard_lines.filtered('technical_production_line_ids'):
            expanded_vals = []
            for displayed_line in selected_wizard_lines:
                expanded_vals.extend([{
                    **payload,
                    'wizard_id': displayed_line.wizard_id.id,
                } for payload in displayed_line._technical_allocation_payloads()])
            selected_wizard_lines = self.env[
                'furniture.mrp.first.stage.start.wizard.line'
            ].create(expanded_vals)
        production_lines = selected_wizard_lines.mapped('production_line_id')
        invalid_lines = self.env['furniture.mrp.production.line']
        split_occurred = False
        for line in production_lines:
            selected_stages = line._selected_stage_codes()
            if (
                line.first_stage_started
                or stage_code not in selected_stages
                or self._production_line_start_stage_code(line) != stage_code
            ):
                invalid_lines |= line
                continue
            wizard_line = selected_wizard_lines.filtered(lambda item: item.production_line_id == line)[:1]
            qty_to_start = wizard_line.qty_to_start if wizard_line else line.product_qty
            if float_compare(qty_to_start or 0.0, 0.0, precision_digits=3) <= 0:
                invalid_lines |= line
                continue
            if float_compare(qty_to_start, line.product_qty, precision_digits=3) > 0:
                invalid_lines |= line
                continue
            if float_compare(qty_to_start, line.product_qty, precision_digits=3) < 0:
                line._split_for_partial_first_stage(qty_to_start)
                split_occurred = True
        if invalid_lines:
            raise UserError(_('بعض الأصناف المختارة ليست جاهزة لبدء أول مرحلة هنا: %s') % ', '.join(
                invalid_lines.mapped('display_name') or invalid_lines.mapped('product_id.display_name')
            ))
        if split_occurred:
            self._refresh_material_lines_for_stage_plan()
        material_lines = self._get_or_create_first_stage_material_lines(production_lines, stage_code)
        self._apply_stage_material_overrides(stage_code, selected_wizard_lines, production_lines)
        material_lines = self.material_line_ids.filtered(lambda line: (
            line.production_line_id in production_lines
            and line.stage == stage_code
            and line.product_id
            and line.qty_needed > 0
        ))
        if material_lines:
            self._move_materials_to_stage_wip(stage_order._name, material_lines=material_lines)
        combined_lines = stage_order.first_stage_production_line_ids | production_lines
        stage_order.write({'first_stage_production_line_ids': [(6, 0, combined_lines.ids)]})
        stage_order._add_stage_active_lines(production_lines)
        production_lines.write({
            'first_stage_started': True,
            'first_stage_started_stage': stage_code,
            'planned_start_stage': stage_code,
        })
        self.message_post(body=_('▶️ تم بدء أول مرحلة من المخزن للأصناف: %s') % ', '.join(
            production_lines.mapped('display_name') or production_lines.mapped('product_id.display_name')
        ))
        return material_lines

    def _open_stage_start_carryover_wizard(self, stage_order, stage_code, payloads):
        self.ensure_one()
        if not stage_order or not payloads:
            return False
        line_commands = self._prepare_stage_start_carryover_line_commands(payloads)
        wizard = self.env['furniture.mrp.stage.start.carryover.wizard'].create({
            'production_id': self.id,
            'stage_code': stage_code,
            'stage_order_model': stage_order._name,
            'stage_order_res_id': stage_order.id,
            'line_ids': line_commands,
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_start_carryover_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('اختيار منتجات صالة المرحلة'),
            'res_model': 'furniture.mrp.stage.start.carryover.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'view_id': view.id,
            'views': [(view.id, 'form')],
            'target': 'new',
            'context': dict(self.env.context, form_view_initial_mode='edit'),
        }

    def _prepare_stage_start_carryover_line_commands(self, payloads):
        """Show one administrative row while retaining every physical source.

        A saved Kit deliberately creates one technical production line per
        physical piece.  The stage-hall selector does not need to repeat those
        pieces: it groups them by their original commercial production row and
        stores the exact hall allocations in JSON for execution/approval.
        """
        self.ensure_one()
        groups = {}
        legacy_index = 0
        for payload in payloads or []:
            product = payload.get('product')
            if not product:
                continue
            uom = payload.get('uom') or product.uom_id
            source_line = payload.get('source_production_line')
            source_production = payload.get('source_production') or (
                source_line.production_id if source_line else False
            )
            if source_line and source_production:
                family_root = source_production._production_line_display_family_origin(
                    source_line
                ) or source_line
                key = (
                    'family',
                    source_production.id,
                    family_root.id,
                    product.id,
                    uom.id if uom else False,
                    payload.get('source_origin') or '',
                )
            else:
                # Legacy stock without a production-line identity must not be
                # mixed with another origin merely because the product matches.
                legacy_index += 1
                family_root = self.env['furniture.mrp.production.line']
                key = ('legacy', legacy_index)
            group = groups.setdefault(key, {
                'product': product,
                'uom': uom,
                'source_origin': payload.get('source_origin') or False,
                'source_production': source_production,
                'family_root': family_root,
                'representative': self.env['furniture.mrp.production.line'],
                'technical_lines': self.env['furniture.mrp.production.line'],
                'qty': 0.0,
                'allocation_by_key': {},
            })
            qty = payload.get('qty') or 0.0
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            group['qty'] += qty
            allocation_key = (
                product.id,
                uom.id if uom else product.uom_id.id,
                source_production.id if source_production else False,
                source_line.id if source_line else False,
                payload.get('source_origin') or '',
            )
            allocation = group['allocation_by_key'].setdefault(allocation_key, {
                'product_id': product.id,
                'product_uom_id': uom.id if uom else product.uom_id.id,
                'qty_in_work_location': 0.0,
                'source_origin': payload.get('source_origin') or False,
                'source_production_id': source_production.id if source_production else False,
                'source_production_line_id': source_line.id if source_line else False,
            })
            allocation['qty_in_work_location'] += qty
            if source_line:
                group['technical_lines'] |= source_line
                if not group['representative'] or source_line == family_root:
                    group['representative'] = source_line

        commands = []
        for group in groups.values():
            if float_compare(group['qty'], 0.0, precision_digits=3) <= 0:
                continue
            representative = group['representative'] or group['technical_lines'][:1]
            allocations = sorted(group['allocation_by_key'].values(), key=lambda allocation: (
                self.env['furniture.mrp.production.line'].browse(
                    allocation.get('source_production_line_id')
                ).sequence or 0,
                self.env['furniture.mrp.production.line'].browse(
                    allocation.get('source_production_line_id')
                ).kit_instance_number or 0,
                allocation.get('source_production_line_id') or 0,
            ))
            commands.append((0, 0, {
                'product_id': group['product'].id,
                'product_uom_id': (group['uom'] or group['product'].uom_id).id,
                'qty_in_work_location': group['qty'],
                'qty_to_start': group['qty'],
                'selected': True,
                'source_origin': group['source_origin'],
                'source_production_id': (
                    group['source_production'].id
                    if group['source_production'] else False
                ),
                'source_production_line_id': representative.id or False,
                'technical_source_production_line_ids': [
                    (6, 0, group['technical_lines'].ids),
                ],
                'technical_allocation_json': allocations,
            }))
        return commands

    def _get_stage_start_current_lines(self, stage_code, wizard_lines, selected_only=True, stage_order=False):
        self.ensure_one()
        if not stage_code or not wizard_lines:
            return self.env['furniture.mrp.production.line']

        excluded_lines = self.env['furniture.mrp.production.line']
        if stage_order:
            excluded_lines = (
                stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
                | stage_order._get_stage_line_ids_data('quality_production_line_ids_data')
                | stage_order._get_stage_line_ids_data('active_production_line_ids_data')
            )

        qty_by_key = {}
        for wizard_line in wizard_lines:
            qty_to_start = wizard_line.qty_to_start if 'qty_to_start' in wizard_line._fields else wizard_line.qty_in_work_location
            if (
                selected_only
                and 'selected' in wizard_line._fields
                and not wizard_line.selected
            ):
                continue
            if (
                not wizard_line.product_id
                or qty_to_start <= 0
                or (wizard_line.source_production_id and wizard_line.source_production_id != self)
            ):
                continue
            uom = wizard_line.product_uom_id or wizard_line.product_id.uom_id
            source_line = (
                wizard_line.source_production_line_id
                if 'source_production_line_id' in wizard_line._fields
                else self.env['furniture.mrp.production.line']
            )
            # A partial run leaves the physical balance on the original inbound
            # move. Once that source line is active/completed, the balance belongs
            # to the next unsplit production line for the same product and route.
            usable_source_line = bool(
                source_line
                and source_line.production_id == self
                and source_line not in excluded_lines
                and source_line.first_stage_started
                and stage_code in source_line._selected_stage_codes()
            )
            key = (
                wizard_line.product_id.id,
                uom.id if uom else False,
                source_line.id if usable_source_line else False,
            )
            qty_by_key[key] = qty_by_key.get(key, 0.0) + (qty_to_start or 0.0)

        matched_lines = self.env['furniture.mrp.production.line']
        split_occurred = False
        for line in self._get_sorted_production_lines():
            if (
                line in excluded_lines
                or not line.first_stage_started
                or not line.product_id
                or line.product_qty <= 0
                or stage_code not in line._selected_stage_codes()
            ):
                continue
            # Match the product that physically travels through this order's
            # route.  Independent lane orders transform the saleable product
            # into an FWIP product; comparing the hall move against the final
            # product made valid priming→carpentry and bases→finishing
            # transfers look unrelated to their own production line.
            line_specs = self._get_finished_output_specs_from_lines(
                line,
                ensure_storable=False,
            )
            line_spec = line_specs[0] if line_specs else {}
            work_product = line_spec.get('product') or line.product_id
            uom = line_spec.get('uom') or work_product.uom_id or line.product_uom_id
            line_key = (work_product.id, uom.id if uom else False, line.id)
            legacy_key = (work_product.id, uom.id if uom else False, False)
            key = line_key if float_compare(qty_by_key.get(line_key, 0.0), 0.0, precision_digits=3) > 0 else legacy_key
            available_qty = qty_by_key.get(key, 0.0)
            if float_compare(available_qty, 0.0, precision_digits=3) <= 0:
                continue
            required_qty = self._quantity_in_product_uom(
                work_product,
                line.product_qty,
                line.product_uom_id or uom,
            )
            qty_to_start = min(available_qty, required_qty)
            if float_compare(qty_to_start, required_qty, precision_digits=3) < 0:
                completed_stage_codes = self._production_line_completed_stage_codes(line, product=finished_product)
                remaining_line = line._split_for_partial_quantity(qty_to_start, preserve_progress=True)
                self._copy_completed_stage_progress_to_split_line(
                    line,
                    remaining_line,
                    completed_stage_codes,
                    exclude_stage_code=stage_code,
                )
                split_occurred = True
                required_qty = qty_to_start
            matched_lines |= line
            qty_by_key[key] = max(available_qty - required_qty, 0.0)
        if split_occurred:
            self._refresh_material_lines_for_stage_plan()
        return matched_lines

    def _move_stage_materials_for_lines(self, stage_code, production_lines):
        self.ensure_one()
        stage_model = self._stage_model_from_code(stage_code)
        production_lines = production_lines.exists() if production_lines else self.env['furniture.mrp.production.line']
        if not stage_model or not production_lines:
            return self.env['stock.move']
        material_lines = self.material_line_ids.filtered(lambda line: (
            line.production_line_id in production_lines
            and line.stage == stage_code
            and line.product_id
            and line.qty_needed
        ))
        if not material_lines:
            return self.env['stock.move']
        return self._move_materials_to_stage_wip(stage_model, material_lines=material_lines)

    def _register_stage_carryover_lines(self, stage_code, wizard_lines, current_production_lines=False):
        self.ensure_one()
        created_lines = self.env['furniture.mrp.carryover.line']
        current_qty_by_key = {}
        current_qty_by_product_uom = {}
        if current_production_lines:
            for production_line in current_production_lines:
                for spec in self._get_finished_output_specs_from_lines(production_line, ensure_storable=False):
                    uom = spec.get('uom') or spec['product'].uom_id
                    key = (spec['product'].id, uom.id if uom else False, production_line.id)
                    pool_key = (spec['product'].id, uom.id if uom else False)
                    spec_qty = spec.get('qty') or 0.0
                    current_qty_by_key[key] = current_qty_by_key.get(key, 0.0) + spec_qty
                    current_qty_by_product_uom[pool_key] = current_qty_by_product_uom.get(pool_key, 0.0) + spec_qty

        for wizard_line in wizard_lines:
            qty_to_start = wizard_line.qty_to_start if 'qty_to_start' in wizard_line._fields else wizard_line.qty_in_work_location
            if (
                not wizard_line.product_id
                or ('selected' in wizard_line._fields and not wizard_line.selected)
                or qty_to_start <= 0
            ):
                continue
            if float_compare(qty_to_start, wizard_line.qty_in_work_location, precision_digits=3) > 0:
                raise UserError(_('كمية %s المختارة أكبر من الكمية الموجودة في الصالة.') % wizard_line.product_display_label)
            uom = wizard_line.product_uom_id or wizard_line.product_id.uom_id
            key = (wizard_line.product_id.id, uom.id if uom else False, wizard_line.source_production_line_id.id)
            legacy_key = (wizard_line.product_id.id, uom.id if uom else False, False)
            pool_key = (wizard_line.product_id.id, uom.id if uom else False)
            current_qty = current_qty_by_key.get(key, 0.0)
            if float_compare(current_qty, 0.0, precision_digits=3) <= 0:
                key = legacy_key
                current_qty = current_qty_by_key.get(key, 0.0)
            source_is_current = bool(
                wizard_line.source_production_id == self
                or (
                    wizard_line.source_production_line_id
                    and wizard_line.source_production_line_id.production_id == self
                )
            )
            use_product_pool = bool(
                source_is_current
                and float_compare(current_qty, 0.0, precision_digits=3) <= 0
            )
            if use_product_pool:
                current_qty = current_qty_by_product_uom.get(pool_key, 0.0)
            if float_compare(current_qty, 0.0, precision_digits=3) > 0:
                skipped_qty = min(qty_to_start, current_qty)
                qty_to_start -= skipped_qty
                current_qty_by_product_uom[pool_key] = max(
                    current_qty_by_product_uom.get(pool_key, 0.0) - skipped_qty,
                    0.0,
                )
                if use_product_pool:
                    qty_to_drain = skipped_qty
                    for current_key in list(current_qty_by_key):
                        if current_key[:2] != pool_key or float_compare(qty_to_drain, 0.0, precision_digits=3) <= 0:
                            continue
                        available_current_qty = current_qty_by_key.get(current_key, 0.0)
                        drained_qty = min(available_current_qty, qty_to_drain)
                        current_qty_by_key[current_key] = max(available_current_qty - drained_qty, 0.0)
                        qty_to_drain -= drained_qty
                else:
                    current_qty_by_key[key] = max(current_qty - skipped_qty, 0.0)
            if float_compare(qty_to_start, 0.0, precision_digits=3) <= 0:
                continue
            existing_line = self.carryover_line_ids.filtered(
                lambda line: (
                    line.state in ('selected', 'started')
                    and line.current_stage == stage_code
                    and line.product_id == wizard_line.product_id
                    and line.source_production_line_id == wizard_line.source_production_line_id
                )
            )[:1]
            vals = {
                'production_id': self.id,
                'source_production_id': wizard_line.source_production_id.id if wizard_line.source_production_id else False,
                'source_production_line_id': wizard_line.source_production_line_id.id if wizard_line.source_production_line_id else False,
                'source_origin': wizard_line.source_origin,
                'product_id': wizard_line.product_id.id,
                'product_uom_id': wizard_line.product_uom_id.id,
                'qty': qty_to_start,
                'current_stage': stage_code,
                'state': 'started',
            }
            if existing_line:
                existing_line.write({
                    'qty': qty_to_start,
                    'state': 'started',
                    'source_origin': existing_line.source_origin or wizard_line.source_origin,
                    'source_production_id': existing_line.source_production_id.id or vals['source_production_id'],
                    'source_production_line_id': existing_line.source_production_line_id.id or vals['source_production_line_id'],
                })
                carryover_line = existing_line
            else:
                carryover_line = self.env['furniture.mrp.carryover.line'].create(vals)
            created_lines |= carryover_line
            material_commands = (
                self._prepare_material_override_commands(
                    stage_code,
                    wizard_line.material_override_json,
                    production_line=wizard_line.source_production_line_id,
                )
                if (
                    'material_override_json' in wizard_line._fields
                    and wizard_line.material_override_json
                ) else self._prepare_material_lines_for_product_stage(
                    wizard_line.product_id,
                    qty_to_start,
                    stage_code,
                    production_line=wizard_line.source_production_line_id,
                )
            )
            if material_commands:
                existing_material_lines = self.material_line_ids
                self.write({'material_line_ids': material_commands})
                new_material_lines = self.material_line_ids - existing_material_lines
                if new_material_lines:
                    new_material_lines.write({'carryover_line_id': carryover_line.id})
            stage_model = self._stage_model_from_code(stage_code)
            carryover_material_lines = self.material_line_ids.filtered(lambda line: (
                line.carryover_line_id == carryover_line
                and line.stage == stage_code
                and line.product_id
                and line.qty_needed
            ))
            if stage_model and carryover_material_lines:
                self._move_materials_to_stage_wip(stage_model, material_lines=carryover_material_lines)
        return created_lines

    def _mark_carryover_stage_done(self, stage_code, payloads):
        self.ensure_one()
        if not payloads:
            return self.env['furniture.mrp.carryover.line']
        done_lines = self.env['furniture.mrp.carryover.line']
        for payload in payloads:
            product = payload.get('product')
            if not product:
                continue
            qty_left = payload.get('qty') or 0.0
            source_line = payload.get('source_production_line')
            payload_lines = (payload.get('line_ids') or self.env['furniture.mrp.carryover.line']).filtered(
                lambda line: line.state == 'started' and line.current_stage == stage_code and line.product_id == product
            )
            matching_lines = payload_lines or self.carryover_line_ids.filtered(
                lambda line: (
                    line.state == 'started'
                    and line.current_stage == stage_code
                    and line.product_id == product
                    and (not source_line or line.source_production_line_id == source_line)
                )
            )
            for line in matching_lines:
                if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                    break
                qty_left -= line.qty or 0.0
                done_lines |= line
        if done_lines:
            done_lines.write({'state': 'stage_done'})
        return done_lines

    def _get_registered_stage_carryover_payloads(self, stage_code):
        """يرجع الشغل القديم الذي اختاره المستخدم فعلاً عند بدء المرحلة.

        لا نعيد فحص كل الموجود في الصالة هنا حتى لا نخلط المنتج الحالي مع القديم
        عندما يكونان نفس الصنف ونفس المقاس.
        """
        self.ensure_one()
        payload_by_key = {}
        carryover_lines = self.carryover_line_ids.filtered(
            lambda line: (
                line.state == 'started'
                and line.current_stage == stage_code
                and line.product_id
                and line.qty > 0
            )
        )
        for line in carryover_lines:
            uom = line.product_uom_id or line.product_id.uom_id
            key = (line.product_id.id, uom.id, line.source_production_line_id.id)
            payload = payload_by_key.setdefault(key, {
                'product': line.product_id,
                'qty': 0.0,
                'uom': uom,
                'label': line.product_id.display_name,
                'line_ids': self.env['furniture.mrp.carryover.line'],
                'source_production_line': line.source_production_line_id,
            })
            payload['qty'] += line.qty or 0.0
            payload['line_ids'] |= line
        return [
            payload for payload in payload_by_key.values()
            if float_compare(payload['qty'], 0.0, precision_digits=3) > 0
        ]

    def _quantity_in_product_uom(self, product, quantity, uom=False):
        self.ensure_one()
        if not product or not uom or uom == product.uom_id:
            return quantity or 0.0
        if uom.category_id == product.uom_id.category_id:
            # Internal availability and aggregation must retain the source
            # precision.  Rounding here to the product's default UoM can turn
            # 0.061 m³ into 0.07 units before a stock move is even prepared.
            return uom._compute_quantity(
                quantity or 0.0, product.uom_id, round=False,
            )
        return quantity or 0.0

    def _ensure_location_has_qty(self, location, product, quantity, uom=False):
        self.ensure_one()
        if not location or not product:
            return 0.0
        if location.usage == 'production':
            return quantity or 0.0
        needed_qty = self._quantity_in_product_uom(product, quantity, uom)
        available_qty = self._stage_location_product_qty(location, product)
        if float_compare(available_qty, needed_qty, precision_digits=3) < 0:
            raise UserError(_(
                'لا يوجد رصيد كافي من %s في %s. المتاح %s والمطلوب %s.'
            ) % (
                product.display_name,
                location.display_name,
                self._format_dimension_value(available_qty),
                self._format_dimension_value(needed_qty),
            ))
        return available_qty

    def _available_stage_product(
        self,
        location,
        preferred_product=False,
        quantity=False,
        uom=False,
        available_qty_by_key=None,
        reserved_qty_by_key=None,
    ):
        self.ensure_one()
        needed_qty = quantity if quantity is not False else self.product_qty
        checked_product_ids = set()

        def _is_available(product):
            if not product or product.id in checked_product_ids:
                return False
            checked_product_ids.add(product.id)
            candidate_qty = self._quantity_in_product_uom(product, needed_qty, uom or product.uom_id)
            stock_key = (location.id, product.id)
            if available_qty_by_key is not None and reserved_qty_by_key is not None:
                if stock_key not in available_qty_by_key:
                    available_qty_by_key[stock_key] = self._stage_location_product_qty(location, product)
                available_qty = (
                    available_qty_by_key[stock_key]
                    - reserved_qty_by_key.get(stock_key, 0.0)
                )
            else:
                available_qty = self._stage_location_product_qty(location, product)
            if float_compare(
                available_qty,
                candidate_qty,
                precision_digits=3,
            ) >= 0:
                if available_qty_by_key is not None and reserved_qty_by_key is not None:
                    reserved_qty_by_key[stock_key] = (
                        reserved_qty_by_key.get(stock_key, 0.0) + candidate_qty
                    )
                return True
            return False

        # The exact product is the normal path. Resolve the comparatively
        # expensive legacy fallbacks only when that stock is genuinely short.
        if _is_available(preferred_product):
            return preferred_product
        for product in (self._get_stage_stock_product(), self.product_id):
            if _is_available(product):
                return product
        return False

    def _move_product_between_locations(
        self,
        source_location,
        dest_location,
        label,
        source_product,
        dest_product=False,
        quantity=False,
        uom=False,
        move_type='stage_transfer',
        source_production_line=False,
        dest_unit_cost=None,
    ):
        self.ensure_one()
        dest_product = dest_product or source_product
        quantity = quantity if quantity is not False else self.product_qty
        if not source_product or not dest_product:
            raise UserError(_('لا يوجد صنف مرتبط بحركة المخزون المطلوبة.'))
        if source_location == dest_location and source_product == dest_product:
            return self.env['stock.move']

        self._ensure_location_has_qty(source_location, source_product, quantity, uom or source_product.uom_id)
        if source_product == dest_product:
            return self._create_internal_move(
                source_location,
                dest_location,
                label,
                move_type=move_type,
                product=source_product,
                quantity=quantity,
                uom=uom or source_product.uom_id,
                source_production_line=source_production_line,
            )

        production_location = self._get_production_location()
        moves = self.env['stock.move']
        if source_location != production_location:
            moves |= self._create_internal_move(
                source_location,
                production_location,
                _('%s - تحويل الصنف القديم') % label,
                move_type=move_type,
                product=source_product,
                quantity=quantity,
                uom=uom or source_product.uom_id,
                source_production_line=source_production_line,
            )
        if dest_location != production_location:
            moves |= self._create_internal_move(
                production_location,
                dest_location,
                label,
                move_type=move_type,
                product=dest_product,
                quantity=quantity,
                uom=dest_product.uom_id,
                source_production_line=source_production_line,
                price_unit=dest_unit_cost,
            )
        return moves

    def _finished_cost_origin_line(self, production_line):
        """Return the original line that created the WIP FIFO quantity."""
        line = production_line
        visited = set()
        while line and line.cost_origin_line_id and line.id not in visited:
            visited.add(line.id)
            line = line.cost_origin_line_id
        return line

    def _finished_actual_unit_cost(self, production_line, product):
        """Actual cumulative material + labor cost for one finished unit."""
        if production_line:
            cost_production = production_line.production_id
            snapshot = cost_production._get_production_line_cost_snapshot(production_line)
            if snapshot.get('has_cost'):
                return max(
                    (snapshot.get('material_unit') or 0.0)
                    + (snapshot.get('labor_unit') or 0.0),
                    0.0,
                )
        return max(product.with_company(self.company_id).standard_price or 0.0, 0.0)

    def _finished_wip_receipt_move(self, product, production_line):
        """Find the production receipt that originally introduced this WIP quantity."""
        base_domain = [
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('location_id.usage', '=', 'production'),
            ('location_dest_id.usage', '=', 'internal'),
        ]
        candidate_lines = self.env['furniture.mrp.production.line']
        if production_line:
            candidate_lines |= production_line
            candidate_lines |= self._finished_cost_origin_line(production_line)
            candidate_lines |= self._production_line_display_family_origin(production_line)
        for candidate_line in candidate_lines:
            receipt_move = self.env['stock.move'].sudo().search(
                base_domain + [
                    ('furniture_source_production_line_id', '=', candidate_line.id),
                ],
                order='date, id',
                limit=1,
            )
            if receipt_move:
                return receipt_move
        if self.name:
            return self.env['stock.move'].sudo().search(
                base_domain + [('origin', '=', self.name)],
                order='date, id',
                limit=1,
            )
        return self.env['stock.move']

    def _finished_uncosted_fifo_layers(self, product, production_line, receipt_move=False):
        """Return FIFO quantity reserved for WIP and not assigned to a finished batch yet."""
        Layer = self.env['stock.valuation.layer'].sudo()
        candidates = Layer
        candidate_lines = self.env['furniture.mrp.production.line']
        if production_line:
            candidate_lines |= production_line
            candidate_lines |= self._finished_cost_origin_line(production_line)
            candidate_lines |= self._production_line_display_family_origin(production_line)
        for candidate_line in candidate_lines:
            candidates = Layer.search([
                ('product_id', '=', product.id),
                ('company_id', '=', self.company_id.id),
                ('remaining_qty', '>', 0),
                ('furniture_finished_move_id', '=', False),
                '|',
                '|',
                ('furniture_wip_origin_line_id', '=', candidate_line.id),
                ('stock_move_id.furniture_source_production_line_id', '=', candidate_line.id),
                ('stock_move_id.furniture_source_production_line_ids', 'in', [candidate_line.id]),
            ], order='create_date, id')
            if candidates:
                break
        if not candidates and receipt_move:
            candidates = Layer.search([
                ('product_id', '=', product.id),
                ('company_id', '=', self.company_id.id),
                ('remaining_qty', '>', 0),
                ('furniture_finished_move_id', '=', False),
                ('stock_move_id', '=', receipt_move.id),
            ], order='create_date, id')
        if not candidates:
            # Compatibility with layers rebuilt when the category costing method
            # was changed before this traceability field existed.
            candidates = Layer.search([
                ('product_id', '=', product.id),
                ('company_id', '=', self.company_id.id),
                ('remaining_qty', '>', 0),
                ('furniture_finished_move_id', '=', False),
                ('remaining_value', '=', 0.0),
                ('stock_move_id.origin', '=', self.name),
            ], order='create_date, id')
        return candidates

    def _assign_finished_fifo_cost(
        self,
        finished_move,
        production_line,
        quantity,
        unit_cost,
        validate_accounting=True,
        update_move_cost=True,
    ):
        """Split WIP quantity into a costed FIFO batch without changing physical stock.

        Stage locations and finished goods are internal locations, so their transfer
        does not normally create a valuation layer.  The WIP receipt layer is split:
        its oldest portion becomes this finished batch and the uncompleted balance is
        moved to a newer zero-cost layer.  This preserves true FIFO order and lets the
        standard sale_mrp COGS logic value Kit components from their production batch.
        """
        self.ensure_one()
        finished_move = finished_move.sudo()
        existing_domain = [
            ('furniture_finished_move_id', '=', finished_move.id),
        ]
        if production_line:
            existing_domain.append((
                'furniture_finished_production_line_id',
                '=',
                production_line.id,
            ))
        existing_layers = self.env['stock.valuation.layer'].sudo().search(existing_domain)
        if existing_layers:
            return existing_layers

        product = finished_move.product_id
        qty_left = self._quantity_in_product_uom(
            product,
            quantity,
            finished_move.product_uom,
        )
        if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
            return self.env['stock.valuation.layer']

        root_line = self._finished_cost_origin_line(production_line)
        receipt_move = self._finished_wip_receipt_move(product, production_line)
        candidates = self._finished_uncosted_fifo_layers(
            product,
            production_line,
            receipt_move=receipt_move,
        )
        available = sum(candidates.mapped('remaining_qty'))
        if float_compare(available, qty_left, precision_digits=3) < 0:
            raise UserError(_(
                'لا توجد طبقة FIFO كافية للصنف %s. المتاح %s والمطلوب %s.'
            ) % (product.display_name, available, qty_left))

        Layer = self.env['stock.valuation.layer'].sudo()
        allocated_layers = Layer
        currency = self.company_id.currency_id
        for candidate in candidates:
            if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                break
            candidate_qty = candidate.remaining_qty or 0.0
            if float_compare(candidate_qty, 0.0, precision_digits=3) <= 0:
                continue
            if float_compare(candidate.quantity, candidate.remaining_qty, precision_digits=3) != 0:
                raise UserError(_(
                    'طبقة FIFO للصنف %s استُهلك جزء منها قبل دخول المنتج للمخزن التام.'
                ) % product.display_name)

            allocated_qty = min(qty_left, candidate_qty)
            residual_qty = candidate_qty - allocated_qty
            old_unit_cost = (
                candidate.remaining_value / candidate_qty
                if candidate_qty else 0.0
            )
            old_allocated_value = currency.round(old_unit_cost * allocated_qty)
            target_value = currency.round(unit_cost * allocated_qty)
            adjustment_value = target_value - old_allocated_value
            receipt_for_accounting = candidate.stock_move_id or receipt_move
            if not receipt_for_accounting:
                raise UserError(_(
                    'تعذر تحديد حركة إنشاء طبقة تحت التشغيل للصنف %s.'
                ) % product.display_name)

            candidate.write({
                'quantity': allocated_qty,
                'unit_cost': old_unit_cost,
                'value': old_allocated_value,
                'remaining_qty': allocated_qty,
                'remaining_value': target_value,
                'stock_move_id': receipt_for_accounting.id,
                'furniture_wip_origin_line_id': root_line.id if root_line else False,
                'furniture_finished_production_line_id': production_line.id if production_line else False,
                'furniture_finished_move_id': finished_move.id,
            })
            allocated_layers |= candidate

            if float_compare(residual_qty, 0.0, precision_digits=3) > 0:
                Layer.create({
                    'company_id': self.company_id.id,
                    'product_id': product.id,
                    'quantity': residual_qty,
                    'unit_cost': old_unit_cost,
                    'value': currency.round(old_unit_cost * residual_qty),
                    'remaining_qty': residual_qty,
                    'remaining_value': currency.round(old_unit_cost * residual_qty),
                    'description': _('%s - رصيد تحت التشغيل المتبقي') % self.name,
                    'stock_move_id': receipt_for_accounting.id,
                    'furniture_wip_origin_line_id': root_line.id if root_line else False,
                })

            if not currency.is_zero(adjustment_value):
                adjustment = Layer.create({
                    'company_id': self.company_id.id,
                    'product_id': product.id,
                    'quantity': 0.0,
                    'unit_cost': 0.0,
                    'value': adjustment_value,
                    'remaining_qty': 0.0,
                    'remaining_value': 0.0,
                    'description': _('%s - اعتماد تكلفة دفعة الإنتاج التامة') % self.name,
                    'stock_move_id': receipt_for_accounting.id,
                    'stock_valuation_layer_id': candidate.id,
                    'furniture_wip_origin_line_id': root_line.id if root_line else False,
                    'furniture_finished_production_line_id': production_line.id if production_line else False,
                    'furniture_finished_move_id': finished_move.id,
                })
                if validate_accounting:
                    adjustment._validate_accounting_entries()
                allocated_layers |= adjustment

            qty_left -= allocated_qty

        if update_move_cost:
            finished_move.write({'furniture_actual_unit_cost': unit_cost})
        return allocated_layers

    @api.model
    def _repair_existing_finished_fifo_costs(self):
        """Backfill traceable finished moves created before FIFO batch costing."""
        finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        if not finished_location:
            return {'repaired': 0, 'skipped': 0}
        moves = self.env['stock.move'].sudo().search([
            ('state', '=', 'done'),
            ('location_id.usage', '=', 'internal'),
            ('location_dest_id', '=', finished_location.id),
            ('furniture_source_production_line_id', '!=', False),
            ('furniture_actual_unit_cost', '=', 0.0),
        ], order='date, id')
        repaired = 0
        skipped = 0
        for move in moves:
            source_line = move.furniture_source_production_line_id
            production = source_line.production_id
            snapshot = production._get_production_line_cost_snapshot(source_line)
            unit_cost = (
                (snapshot.get('material_unit') or 0.0)
                + (snapshot.get('labor_unit') or 0.0)
            )
            if not snapshot.get('has_cost') or float_compare(unit_cost, 0.0, precision_digits=4) <= 0:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    production._assign_finished_fifo_cost(
                        move,
                        source_line,
                        move.quantity or move.product_uom_qty,
                        unit_cost,
                    )
                repaired += 1
            except (UserError, ValidationError):
                skipped += 1
        return {'repaired': repaired, 'skipped': skipped}

    def _stage_stock_open_family_lines(self, source_line, product, stage_code, runtime_cache=None):
        """Resolve the live split batch that still owns stock in a stage store.

        The stock receipt for a stage can cover a production line before that
        line is split into completed and remaining batches.  The historical
        move keeps pointing to the original line, while the physical balance
        belongs to its unfinished descendants.  Returning those descendants
        prevents the transfer wizard from treating the balance as an already
        completed batch and hiding it from every valid target stage.
        """
        self.ensure_one()
        if not source_line or not source_line.exists() or not product or not stage_code:
            return self.env['furniture.mrp.production.line']
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = ('stage_stock_open_family_lines', self.id, source_line.id, product.id, stage_code)
        if cache_key in runtime_cache:
            return runtime_cache[cache_key]
        production = source_line.production_id
        display_root = production._production_line_display_family_origin(source_line)
        cost_root = production._finished_cost_origin_line(source_line)
        family_lines = production.production_line_ids.filtered(lambda line: (
            (
                production._production_line_display_family_origin(line) == display_root
                or production._finished_cost_origin_line(line) == cost_root
            )
            and production._production_line_matches_product(line, product)
            and stage_code in line._selected_stage_codes()
        ))
        open_lines = family_lines.filtered(lambda line: (
            stage_code in production._production_line_completed_stage_codes(
                line,
                product=product,
                runtime_cache=runtime_cache,
            )
            and not production._production_line_all_selected_stages_done(
                line,
                product=product,
                runtime_cache=runtime_cache,
            )
        ))
        runtime_cache[cache_key] = open_lines.sorted(self._production_line_sort_key)
        return runtime_cache[cache_key]

    def _stage_stock_line_product_qty(self, source_line, product, runtime_cache=None):
        self.ensure_one()
        if not source_line or not product:
            return 0.0
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = ('stage_stock_line_product_qty', self.id, source_line.id, product.id)
        if cache_key in runtime_cache:
            return runtime_cache[cache_key]
        qty = 0.0
        for spec in source_line.production_id._get_finished_output_specs_from_lines(
            source_line,
            ensure_storable=False,
        ):
            if spec.get('product') != product:
                continue
            qty += self._quantity_in_product_uom(
                product,
                spec.get('qty') or 0.0,
                spec.get('uom') or product.uom_id,
            )
        runtime_cache[cache_key] = qty
        return qty

    def _stage_stock_move_family_lines(
        self,
        move,
        product,
        stage_code,
        runtime_cache=None,
        fallback_source_lines=False,
    ):
        """Return the exact move lines first, then compatible split siblings.

        Quality receipts may deliberately group several physical production
        lines in one stock move.  Older moves only kept a representative M2O,
        while current moves also persist the exact M2M allocation ledger.  In
        both cases a later partial transfer must be able to resolve the whole
        display/cost family without losing the exact lines that are available.
        """
        self.ensure_one()
        if not move or not product or not stage_code:
            return self.env['furniture.mrp.production.line']
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = (
            'stage_stock_move_family_lines',
            self.id,
            move.id,
            product.id,
            stage_code,
            tuple((fallback_source_lines or self.env[
                'furniture.mrp.production.line'
            ]).ids),
        )
        if cache_key in runtime_cache:
            return runtime_cache[cache_key]

        exact_lines = (
            move.furniture_source_production_line_ids
            or move.furniture_source_production_line_id
            or fallback_source_lines
        ).exists().filtered(lambda line: (
            line.production_id
            and line.production_id._production_line_matches_product(line, product)
            and stage_code in line._selected_stage_codes()
        )).sorted(self._production_line_sort_key)

        family_lines = self.env['furniture.mrp.production.line']
        for exact_line in exact_lines:
            production = exact_line.production_id
            display_root = production._production_line_display_family_origin(exact_line)
            cost_root = production._finished_cost_origin_line(exact_line)
            family_lines |= production.production_line_ids.filtered(lambda line: (
                (
                    production._production_line_display_family_origin(line) == display_root
                    or production._finished_cost_origin_line(line) == cost_root
                )
                and production._production_line_matches_product(line, product)
                and stage_code in line._selected_stage_codes()
            ))

        # Recordset union preserves the exact allocation ledger at the front;
        # compatible siblings are only a fallback for grouped legacy moves or
        # descendants created by an operational quantity split.
        result = exact_lines | (family_lines - exact_lines).sorted(
            self._production_line_sort_key
        )
        runtime_cache[cache_key] = result
        return result

    def _stage_location_departed_qty_by_line(
        self,
        source_location,
        product,
        stage_code,
        candidate_lines,
        runtime_cache=None,
    ):
        """Allocate completed outgoing moves to their physical source lines.

        A quant is the *net* balance in a location.  Reconstructing its identity
        from incoming moves alone caused every reopened partial-transfer wizard
        to start again at Kit 1.  The second batch therefore moved the same Kit
        identities while the untouched Kits stayed permanently pending.  This
        ledger subtracts already departed quantities before incoming stock is
        distributed, keeping the remaining balance on the still-unmoved lines.
        """
        self.ensure_one()
        candidate_lines = candidate_lines.exists() if candidate_lines else self.env[
            'furniture.mrp.production.line'
        ]
        if not source_location or not product or not stage_code or not candidate_lines:
            return {}
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = (
            'stage_location_departed_qty_by_line',
            self.id,
            self.company_id.id,
            source_location.id,
            product.id,
            stage_code,
            tuple(candidate_lines.ids),
        )
        if cache_key in runtime_cache:
            return dict(runtime_cache[cache_key])

        move_domain = [
            ('product_id', '=', product.id),
            ('location_id', '=', source_location.id),
            ('location_dest_id', '!=', source_location.id),
            ('state', '=', 'done'),
            '|',
            ('furniture_source_production_line_id', 'in', candidate_lines.ids),
            ('furniture_source_production_line_ids', 'in', candidate_lines.ids),
        ]
        if self.company_id:
            move_domain.append(('company_id', '=', self.company_id.id))

        departed_qty_by_line = {}
        outgoing_moves = self.env['stock.move'].sudo().search(
            move_domain,
            order='date asc, id asc',
        )
        raw_consumption_move_ids = set(
            self.env['furniture.mrp.material.line'].sudo().search([
                ('move_id', 'in', outgoing_moves.ids),
            ]).mapped('move_id').ids
        )
        for move in outgoing_moves:
            if (
                move.id in raw_consumption_move_ids
                or (
                    move.location_dest_id.usage == 'production'
                    and (
                        'سحب خامات' in (move.name or '')
                        or 'استهلاك خامات' in (move.name or '')
                    )
                )
            ):
                continue
            qty_left = self._quantity_in_product_uom(
                product,
                move.quantity or move.product_uom_qty,
                move.product_uom,
            )
            if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                continue
            candidate_lines = self._stage_stock_move_family_lines(
                move,
                product,
                stage_code,
                runtime_cache=runtime_cache,
            )
            for candidate_line in candidate_lines:
                line_capacity = self._stage_stock_line_product_qty(
                    candidate_line,
                    product,
                    runtime_cache=runtime_cache,
                )
                line_available = max(
                    line_capacity - departed_qty_by_line.get(candidate_line.id, 0.0),
                    0.0,
                )
                allocated_qty = min(qty_left, line_available)
                if float_compare(allocated_qty, 0.0, precision_digits=3) <= 0:
                    continue
                departed_qty_by_line[candidate_line.id] = (
                    departed_qty_by_line.get(candidate_line.id, 0.0)
                    + allocated_qty
                )
                qty_left -= allocated_qty
                if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                    break

        runtime_cache[cache_key] = dict(departed_qty_by_line)
        return departed_qty_by_line

    def _stage_location_stock_entries(
        self,
        source_location,
        product,
        qty,
        stage_code,
        runtime_cache=None,
        excluded_incoming_move_ids=False,
    ):
        self.ensure_one()
        if not source_location or not product or float_compare(qty or 0.0, 0.0, precision_digits=3) <= 0:
            return []
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        excluded_incoming_move_ids = tuple(sorted(set(
            excluded_incoming_move_ids or []
        )))
        cache_key = (
            'stage_location_stock_entries',
            self.id,
            self.company_id.id,
            source_location.id,
            product.id,
            round(qty or 0.0, 6),
            stage_code,
            excluded_incoming_move_ids,
        )
        if cache_key in runtime_cache:
            return list(runtime_cache[cache_key])
        remaining_qty = qty or 0.0
        entries = []
        allocated_qty_by_line = {}
        Production = self.env['furniture.mrp.production'].sudo()
        move_domain = [
            ('product_id', '=', product.id),
            ('location_dest_id', '=', source_location.id),
            ('state', '=', 'done'),
        ]
        if self.company_id:
            move_domain.append(('company_id', '=', self.company_id.id))
        if excluded_incoming_move_ids:
            move_domain.append(('id', 'not in', list(excluded_incoming_move_ids)))
        incoming_moves = self.env['stock.move'].sudo().search(move_domain, order='date desc, id desc')

        # Resolve every incoming move once, then limit the outgoing-history
        # query to those exact/family lines.  Without this scope a frequently
        # used stage location would scan the product's entire move history on
        # every wizard opening.
        incoming_move_payloads = []
        relevant_candidate_lines = self.env['furniture.mrp.production.line']
        for move in incoming_moves:
            move_qty = self._quantity_in_product_uom(
                product,
                move.quantity or move.product_uom_qty,
                move.product_uom,
            )
            if float_compare(move_qty, 0.0, precision_digits=3) <= 0:
                continue
            move_source_lines = (
                move.furniture_source_production_line_ids
                or move.furniture_source_production_line_id
            )
            source_line = move_source_lines[:1]
            source_production = source_line.production_id if source_line else Production
            if not source_production and move.origin:
                source_production = Production.search([('name', '=', move.origin)], limit=1)
            if not source_line:
                source_line = self._find_stage_product_source_line(
                    product,
                    stage_code,
                    source_production=source_production,
                    final_only=False,
                )
            if not source_line:
                source_line = self._find_stage_product_source_line(
                    product,
                    stage_code,
                    final_only=False,
                )
            if source_line:
                source_production = source_line.production_id
            move_source_lines = move_source_lines or source_line
            candidate_lines = self._stage_stock_move_family_lines(
                move,
                product,
                stage_code,
                runtime_cache=runtime_cache,
                fallback_source_lines=move_source_lines,
            )
            relevant_candidate_lines |= candidate_lines
            incoming_move_payloads.append({
                'move': move,
                'move_qty': move_qty,
                'source_line': source_line,
                'source_production': source_production,
                'candidate_lines': candidate_lines,
            })

        departed_qty_by_line = self._stage_location_departed_qty_by_line(
            source_location,
            product,
            stage_code,
            relevant_candidate_lines,
            runtime_cache=runtime_cache,
        )
        for move_payload in incoming_move_payloads:
            if float_compare(remaining_qty, 0.0, precision_digits=3) <= 0:
                break
            move_qty = move_payload['move_qty']
            source_line = move_payload['source_line']
            source_production = move_payload['source_production']
            candidate_lines = move_payload['candidate_lines']
            move_qty_left = min(remaining_qty, move_qty)
            for open_line in candidate_lines:
                line_capacity = self._stage_stock_line_product_qty(
                    open_line,
                    product,
                    runtime_cache=runtime_cache,
                )
                line_available = max(
                    line_capacity
                    - departed_qty_by_line.get(open_line.id, 0.0)
                    - allocated_qty_by_line.get(open_line.id, 0.0),
                    0.0,
                )
                allocated_qty = min(move_qty_left, line_available)
                if float_compare(allocated_qty, 0.0, precision_digits=3) <= 0:
                    continue
                entries.append({
                    'product': product,
                    'qty': allocated_qty,
                    'uom': product.uom_id,
                    'source_production': open_line.production_id,
                    'source_production_line': open_line,
                })
                allocated_qty_by_line[open_line.id] = (
                    allocated_qty_by_line.get(open_line.id, 0.0) + allocated_qty
                )
                remaining_qty -= allocated_qty
                move_qty_left -= allocated_qty
                if float_compare(move_qty_left, 0.0, precision_digits=3) <= 0:
                    break
            if (
                float_compare(move_qty_left, 0.0, precision_digits=3) > 0
                and not candidate_lines
            ):
                entries.append({
                    'product': product,
                    'qty': move_qty_left,
                    'uom': product.uom_id,
                    'source_production': source_production,
                    'source_production_line': source_line,
                })
                remaining_qty -= move_qty_left

        if float_compare(remaining_qty, 0.0, precision_digits=3) > 0:
            source_production = self._find_stage_product_source_production(
                product,
                stage_code,
                final_only=False,
            )
            source_line = self._find_stage_product_source_line(
                product,
                stage_code,
                source_production=source_production,
                final_only=False,
            )
            entries.append({
                'product': product,
                'qty': remaining_qty,
                'uom': product.uom_id,
                'source_production': source_production,
                'source_production_line': source_line,
            })
        runtime_cache[cache_key] = tuple(entries)
        return entries

    def _stage_location_product_qty_for_line(
        self,
        source_location,
        product,
        source_line,
        stage_code,
        runtime_cache=None,
    ):
        self.ensure_one()
        if not source_location or not product:
            return 0.0
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        cache_key = (
            'stage_location_product_qty_for_line',
            self.id,
            source_location.id,
            product.id,
            source_line.id if source_line else False,
            stage_code,
        )
        if cache_key in runtime_cache:
            return runtime_cache[cache_key]
        total_qty = self._stage_location_product_qty(source_location, product)
        if not source_line:
            runtime_cache[cache_key] = total_qty
            return total_qty
        entries = self._stage_location_stock_entries(
            source_location,
            product,
            total_qty,
            stage_code,
            runtime_cache=runtime_cache,
        )
        runtime_cache[cache_key] = sum(
            entry.get('qty') or 0.0
            for entry in entries
            if entry.get('source_production_line') == source_line
        )
        return runtime_cache[cache_key]

    def _get_stage_storage_content_payloads(self, stage_code):
        self.ensure_one()
        source_location = self._stage_storage_location(stage_code)
        if not source_location:
            return []

        quant_domain = [
            ('location_id', '=', source_location.id),
        ]
        if self.company_id:
            quant_domain.append(('company_id', '=', self.company_id.id))

        grouped_quants = {}
        for quant in self.env['stock.quant'].sudo().search(quant_domain, order='product_id asc, id asc'):
            if not quant.product_id:
                continue
            entries = self._stage_location_stock_entries(
                source_location,
                quant.product_id,
                quant.quantity or 0.0,
                stage_code,
            )
            for entry in entries:
                source_production = entry.get('source_production')
                source_line = entry.get('source_production_line')
                key = (quant.product_id.id, source_line.id if source_line else False)
                bucket = grouped_quants.setdefault(key, {
                    'product': quant.product_id,
                    'qty': 0.0,
                    'uom': quant.product_id.uom_id,
                    'source_production': source_production,
                    'source_production_line': source_line,
                })
                bucket['qty'] += entry.get('qty') or 0.0
                continue
            if entries:
                continue
            source_production = self._find_stage_product_source_production(
                quant.product_id,
                stage_code,
                final_only=False,
            )
            source_line = self._find_stage_product_source_line(
                quant.product_id,
                stage_code,
                source_production=source_production,
                final_only=False,
            )
            key = (quant.product_id.id, source_line.id if source_line else False)
            bucket = grouped_quants.setdefault(key, {
                'product': quant.product_id,
                'qty': 0.0,
                'uom': quant.product_id.uom_id,
                'source_production': source_production,
                'source_production_line': source_line,
            })
            bucket['qty'] += quant.quantity or 0.0
        positive_payloads = [
            data for data in grouped_quants.values()
            if float_compare(data['qty'], 0.0, precision_digits=3) > 0
        ]
        return sorted(
            positive_payloads,
            key=lambda data: (data['product'].display_name or '', data['product'].id),
        )

    def _stage_storage_payloads_for_transfer(self, stage_code, payloads=False):
        self.ensure_one()
        return list(payloads if payloads is not False else self._get_stage_storage_content_payloads(stage_code))

    def _get_stage_storage_content_moves(self, stage_code, payloads=False):
        self.ensure_one()
        payloads = payloads if payloads is not False else self._get_stage_storage_content_payloads(stage_code)

        move_ids = []
        for data in payloads:
            representative_move = self.env['stock.move'].sudo().search([
                ('product_id', '=', data['product'].id),
                ('location_dest_id', '=', self._stage_storage_location(stage_code).id),
                ('state', '=', 'done'),
            ], order='id desc', limit=1)
            if representative_move:
                move_ids.append(representative_move.id)
        return self.env['stock.move'].browse(move_ids)

    def _material_line_current_location(self, line):
        self.ensure_one()
        if line.move_id and line.move_id.state == 'done':
            return line.move_id.location_dest_id
        return False

    def _material_line_required_stock_qty(self, line):
        """Quantity this material row may actually move after handover.

        A warehouse discrepancy must never be silently completed from Stock.
        Before a production receipt, the recipe quantity remains authoritative.
        Once the receipt is confirmed, only the quantity physically accepted by
        production is allowed into the hall and later into consumption.
        """
        self.ensure_one()
        line_qty = (
            line.warehouse_received_qty
            if line.warehouse_receipt_confirmed
            else line.qty_needed
        )
        return self._quantity_in_product_uom(
            line.product_id,
            line_qty,
            line.product_uom_id or line.product_id.uom_id,
        )

    def _allocate_received_product_qty(self, material_lines, received_product_qty):
        """Split one physical receipt without creating a rounding over-allocation.

        Technical material quantities are stored with three decimals.  Rounding
        every proportional share independently can make their stored total
        larger than the stock move (for example 0.280 becoming 0.281).  Round
        each saved share first and carry that exact rounded amount forward so
        the final row receives only the real remainder.
        """
        self.ensure_one()
        material_lines = material_lines.exists().sorted('id')
        if not material_lines:
            return {}
        products = material_lines.mapped('product_id')
        if len(products) != 1:
            raise UserError(_(
                'لا يمكن توزيع كمية استلام واحدة على أكثر من خامة.'
            ))
        product = products.ensure_one()
        received_product_qty = max(float(received_product_qty or 0.0), 0.0)
        required_by_line = {
            line.id: self._quantity_in_product_uom(
                product,
                line.qty_needed,
                line.product_uom_id or product.uom_id,
            )
            for line in material_lines
        }
        total_required = sum(required_by_line.values())
        remaining = received_product_qty
        allocations = {}
        for index, line in enumerate(material_lines):
            line_uom = line.product_uom_id or product.uom_id
            if index == len(material_lines) - 1:
                target_product_qty = remaining
            elif float_compare(
                total_required, 0.0, precision_digits=3,
            ) > 0:
                target_product_qty = (
                    received_product_qty
                    * required_by_line[line.id]
                    / total_required
                )
            else:
                target_product_qty = 0.0

            line_qty = product.uom_id._compute_quantity(
                target_product_qty, line_uom, round=False,
            )
            # The field itself is digits=(16, 3), even if the UoM permits a
            # finer value.  Use the coarser precision before reducing the
            # remaining physical quantity.
            allocation_rounding = max(line_uom.rounding or 0.001, 0.001)
            line_qty = float_round(
                line_qty, precision_rounding=allocation_rounding,
            )
            allocated_product_qty = line_uom._compute_quantity(
                line_qty, product.uom_id, round=False,
            )
            if float_compare(
                allocated_product_qty, remaining, precision_digits=3,
            ) > 0:
                line_qty = float_round(
                    product.uom_id._compute_quantity(
                        remaining, line_uom, round=False,
                    ),
                    precision_rounding=allocation_rounding,
                    rounding_method='DOWN',
                )
                allocated_product_qty = line_uom._compute_quantity(
                    line_qty, product.uom_id, round=False,
                )
            allocations[line.id] = max(line_qty, 0.0)
            remaining = max(remaining - allocated_product_qty, 0.0)
        return allocations

    def _set_material_line_move(self, line, move, allow_sudo=False):
        if line and move:
            target_line = line.sudo() if allow_sudo else line
            target_line.move_id = move.id

    def _material_line_already_consumed(self, line):
        self.ensure_one()
        move = line.move_id.sudo() if line and line.move_id else self.env['stock.move']
        return bool(
            move
            and move.state == 'done'
            and move.product_id == line.product_id
            and move.location_dest_id == self._get_production_location()
        )

    def _unassigned_material_lines(self):
        return self.material_line_ids.filtered(lambda l: l.product_id and l.qty_needed and not l.stage)

    def _move_materials_to_stage_wip(
        self, stage_model, material_lines=False,
        allow_material_line_link_sudo=False,
    ):
        self.ensure_one()
        self._ensure_stage_locations()
        stage_locations = {
            'furniture.mrp.priming': (
                self.location_priming_wip_id,
                _('سحب خامات التقديم إلى صالة تصنيع التقديم'),
            ),
            'furniture.mrp.painting': (
                self.location_painting_wip_id,
                _('سحب خامات تصنيع دهانات إلى صالة تصنيع دهانات'),
            ),
            'furniture.mrp.carpentry': (
                self.location_carpentry_wip_id,
                _('سحب خامات تجميع إلى صالة تصنيع تجميع'),
            ),
            'furniture.mrp.bases': (
                self.location_bases_wip_id,
                _('سحب خامات القواعد إلى صالة تصنيع القواعد'),
            ),
            'furniture.mrp.finishing': (
                self.location_finishing_wip_id,
                _('سحب خامات تجهيز إلى صالة تصنيع تجهيز'),
            ),
            'furniture.mrp.tailoring': (
                self.location_tailoring_wip_id,
                _('سحب خامات تفصيل إلى صالة تصنيع تفصيل'),
            ),
            'furniture.mrp.sewing': (
                self.location_sewing_wip_id,
                _('سحب خامات الخياطة إلى صالة تصنيع الخياطة'),
            ),
            'furniture.mrp.upholstery': (
                self.location_upholstery_wip_id,
                _('سحب خامات كسوه إلى صالة تصنيع كسوه'),
            ),
            'furniture.mrp.packaging': (
                self.location_packaging_id,
                _('سحب خامات التغليف إلى مخزن/منطقة التغليف'),
            ),
        }
        move_data = stage_locations.get(stage_model)
        if not move_data:
            return False

        dest_location, label = move_data
        source_location = self.location_src_id or self.env.ref('stock.stock_location_stock')
        moves = self.env['stock.move']
        lines_to_move = (material_lines or self._stage_material_lines(stage_model)).filtered(
            lambda line: line.product_id and line.qty_needed and not self._material_line_already_consumed(line)
        )
        store_request_id = self.env.context.get('furniture_store_request_id')
        if store_request_id:
            store_request = self.env['furniture.mrp.store.request'].browse(
                store_request_id,
            ).exists()
            if store_request:
                store_request._apply_receipt_to_material_lines(lines_to_move)
        required_qty_by_product = {}
        for line in lines_to_move:
            needed_qty = self._material_line_required_stock_qty(line)
            required_qty_by_product[line.product_id.id] = (
                required_qty_by_product.get(line.product_id.id, 0.0)
                + (needed_qty or 0.0)
            )
        available_qty_by_product = {
            product_id: self._stage_location_product_qty(
                dest_location,
                self.env['product.product'].browse(product_id),
                self.company_id,
            )
            for product_id in required_qty_by_product
        }
        pending_entries = []
        new_move_buckets = {}
        for line in lines_to_move:
            self._ensure_stock_product_is_storable(line.product_id, line.product_uom_id)
            needed_qty = self._material_line_required_stock_qty(line)
            if float_compare(needed_qty, 0.0, precision_digits=3) <= 0:
                continue
            existing_move = line.move_id.sudo()
            staged_qty = 0.0
            pending_move = self.env['stock.move']
            if existing_move and existing_move.state == 'done':
                if (
                    existing_move.product_id == line.product_id
                    and existing_move.location_dest_id == dest_location
                ):
                    staged_qty = self._quantity_in_product_uom(
                        line.product_id,
                        existing_move.quantity or existing_move.product_uom_qty,
                        existing_move.product_uom,
                    )
                else:
                    existing_move = self.env['stock.move']
            if existing_move and (
                existing_move.state == 'cancel'
                or existing_move.product_id != line.product_id
                or existing_move.location_id != source_location
                or existing_move.location_dest_id != dest_location
            ):
                existing_move = self.env['stock.move']
            if existing_move and existing_move.state != 'done':
                pending_move = existing_move
            missing_qty = needed_qty - max(staged_qty, 0.0)
            if (
                float_compare(missing_qty, 0.0, precision_digits=3) > 0
                and float_compare(staged_qty, 0.0, precision_digits=3) > 0
                and float_compare(
                    available_qty_by_product.get(line.product_id.id, 0.0),
                    required_qty_by_product.get(line.product_id.id, 0.0),
                    precision_digits=3,
                ) >= 0
            ):
                missing_qty = 0.0
            if float_compare(missing_qty, 0.0, precision_digits=3) > 0:
                if pending_move:
                    pending_entries.append((line, pending_move, missing_qty))
                else:
                    key = (
                        line.product_id.id,
                        line.product_id.uom_id.id,
                        source_location.id,
                        dest_location.id,
                    )
                    bucket = new_move_buckets.setdefault(key, {
                        'product': line.product_id,
                        'uom': line.product_id.uom_id,
                        'quantity': 0.0,
                        'material_lines': self.env['furniture.mrp.material.line'],
                    })
                    bucket['quantity'] += missing_qty
                    bucket['material_lines'] |= line
                continue
            linked_move = existing_move.filtered(
                lambda existing: existing.state == 'done' and existing.location_dest_id == dest_location
            )[:1]
            if linked_move:
                self._set_material_line_move(
                    line, linked_move,
                    allow_sudo=allow_material_line_link_sudo,
                )

        for line, pending_move, missing_qty in pending_entries:
            move = self._finalize_stock_move(pending_move, missing_qty)
            moves |= move
            self._set_material_line_move(
                line, move,
                allow_sudo=allow_material_line_link_sudo,
            )

        move_specs = [{
            'source_location': source_location,
            'dest_location': dest_location,
            'label': label,
            'product': bucket['product'],
            'quantity': bucket['quantity'],
            'uom': bucket['uom'],
            'material_lines': bucket['material_lines'],
            'source_production_lines': bucket['material_lines'].mapped(
                'production_line_id'
            ),
        } for bucket in new_move_buckets.values()]
        for move_spec, move in self._create_internal_moves_batch(move_specs):
            moves |= move
            lines_to_link = move_spec['material_lines']
            if allow_material_line_link_sudo:
                lines_to_link = lines_to_link.sudo()
            lines_to_link.write({'move_id': move.id})
        if lines_to_move:
            message_target = (
                self.sudo() if allow_material_line_link_sudo else self
            )
            message_target.message_post(body=_('📦 %s') % label)
        return moves

    def _move_stage_output(self, stage_model):
        return self._move_materials_to_stage_wip(stage_model)

    def _stage_output_move_already_done(
        self, output_spec, destination, source_line=False,
    ):
        """Return whether this exact stage output was already posted.

        Product-batch completion can be retried after its lane output has
        already been reserved or consumed by the following stage.  Reposting
        the same physical output would both duplicate stock and make the lane
        ledger try to replace its immutable ``ready_move_id``.  Count the
        existing done movements attributed to the same technical lines so the
        stock side of completion remains idempotent.
        """
        self.ensure_one()
        product = output_spec.get('product')
        uom = output_spec.get('uom') or (product and product.uom_id)
        required_qty = output_spec.get('qty') or 0.0
        source_lines = (
            output_spec.get('source_production_lines')
            or source_line
            or self.env['furniture.mrp.production.line']
        ).exists()
        if not product or not uom or not destination or not source_lines:
            return False

        completed_moves = self.env['stock.move'].sudo().search([
            ('state', '=', 'done'),
            ('origin', '=', self.name),
            ('product_id', '=', product.id),
            ('location_dest_id', '=', destination.id),
            '|',
            ('furniture_source_production_line_id', 'in', source_lines.ids),
            ('furniture_source_production_line_ids', 'in', source_lines.ids),
        ])
        completed_qty = 0.0
        for move in completed_moves:
            move_qty = move.product_uom._compute_quantity(move.quantity, uom)
            allocated_lines = move.furniture_source_production_line_ids.exists()
            if not allocated_lines:
                if move.furniture_source_production_line_id in source_lines:
                    completed_qty += move_qty
                continue
            matching_lines = allocated_lines & source_lines
            if not matching_lines:
                continue
            if matching_lines == allocated_lines:
                completed_qty += move_qty
                continue
            weights = {
                line.id: (line.product_uom_id or uom)._compute_quantity(
                    line.product_qty, uom,
                )
                for line in allocated_lines
            }
            total_weight = sum(weights.values())
            if total_weight:
                completed_qty += move_qty * sum(
                    weights.get(line.id, 0.0) for line in matching_lines
                ) / total_weight
        return float_compare(
            completed_qty,
            required_qty,
            precision_rounding=uom.rounding or 0.001,
        ) >= 0

    def _move_stage_work_to_stock(self, stage_model, production_lines=False):
        self.ensure_one()
        self._ensure_stage_locations()
        stage_locations = {
            'furniture.mrp.priming': (
                self.location_priming_wip_id,
                self.location_priming_id,
                _('استلام المنتج شبه النهائي بعد التقديم في مخزن التقديم'),
            ),
            'furniture.mrp.painting': (
                self.location_painting_wip_id,
                self.location_painting_id,
                _('استلام المنتج شبه النهائي بعد تصنيع الدهانات في مخزن تصنيع دهانات'),
            ),
            'furniture.mrp.carpentry': (
                self.location_carpentry_wip_id,
                self.location_carpentry_id,
                _('استلام المنتج شبه النهائي بعد التجميع في مخزن تجميع'),
            ),
            'furniture.mrp.bases': (
                self.location_bases_wip_id,
                self.location_bases_id,
                _('استلام المنتج شبه النهائي بعد القواعد في مخزن القواعد'),
            ),
            'furniture.mrp.finishing': (
                self.location_finishing_wip_id,
                self.location_finishing_id,
                _('استلام المنتج شبه النهائي بعد التجهيز في مخزن تجهيز'),
            ),
            'furniture.mrp.tailoring': (
                self.location_tailoring_wip_id,
                self.location_tailoring_id,
                _('استلام المنتج شبه النهائي بعد التفصيل في مخزن تفصيل'),
            ),
            'furniture.mrp.sewing': (
                self.location_sewing_wip_id,
                self.location_sewing_id,
                _('استلام المنتج شبه النهائي بعد الخياطة في مخزن الخياطة'),
            ),
            'furniture.mrp.upholstery': (
                self.location_upholstery_wip_id,
                self.location_upholstery_id,
                _('استلام المنتج شبه النهائي بعد الكسوه في مخزن كسوه'),
            ),
            'furniture.mrp.packaging': (
                self.location_packaging_id,
                self.location_packaging_id,
                _('استلام المنتج شبه النهائي بعد التغليف في مخزن التغليف'),
            ),
        }
        move_data = stage_locations.get(stage_model)
        if not move_data:
            return False

        source_location, dest_location, label = move_data
        self._sync_stage_product_name()
        stage_code = self._stage_model_to_code(stage_model)
        material_only_stage = self._is_material_only_stage(stage_code)
        stage_order = self._stage_order_record(stage_code) if stage_code else False
        selected_work_only = bool(stage_order and stage_order.selected_work_products_only)
        selected_production_lines = production_lines.exists() if production_lines else self.env['furniture.mrp.production.line']
        completed_production_lines = (
            stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
            if stage_order else self.env['furniture.mrp.production.line']
        )
        active_production_lines = (
            stage_order._get_stage_line_ids_data('active_production_line_ids_data') - completed_production_lines
            if stage_order else self.env['furniture.mrp.production.line']
        )
        first_stage_production_lines = (
            stage_order.first_stage_production_line_ids - completed_production_lines
            if stage_order else self.env['furniture.mrp.production.line']
        )
        first_stage_lines = (
            selected_production_lines
            or active_production_lines
            or first_stage_production_lines
        )
        has_current_output_lines = bool(
            selected_production_lines
            or active_production_lines
            or first_stage_production_lines
        )
        carryover_only = bool(
            stage_order
            and (stage_order.include_existing_work_products or selected_work_only)
            and not has_current_output_lines
        )
        active_stages = self._required_stage_codes()
        product_move = self.env['stock.move']
        pending_internal_move_specs = []
        available_stage_qty_by_key = {}
        reserved_stage_qty_by_key = {}
        if (
            not material_only_stage
            and not carryover_only
            and (
                not selected_work_only
                or selected_production_lines
                or active_production_lines
            )
        ):
            output_specs = []
            if first_stage_lines:
                for family_group in self._group_production_lines_by_display_family(first_stage_lines):
                    family_lines = family_group['lines']
                    source_line = family_group['root'] or family_group['representative']
                    for spec in self._get_finished_output_specs_from_lines(family_lines):
                        spec = dict(spec)
                        spec['source_production_line'] = source_line
                        spec['source_production_lines'] = spec.get('production_lines') or family_lines
                        output_specs.append(spec)
            else:
                output_specs = self._get_finished_output_specs()
            for spec in output_specs:
                target_product = spec['product']
                source_line = spec.get('source_production_line') or self.env['furniture.mrp.production.line']
                # A chosen entry stage owns no finished-product WIP yet.  Its
                # first output must always be created from the production
                # location.  Looking at aggregate shared-hall stock first could
                # otherwise consume an identical product that belongs to a
                # different weekly order and falsely attribute it to this line.
                is_line_entry_stage = bool(
                    source_line
                    and self._production_line_start_stage_code(source_line) == stage_code
                )
                is_legacy_first_stage = bool(
                    not source_line
                    and active_stages
                    and stage_code == active_stages[0]
                )
                if self._stage_output_move_already_done(
                    spec, dest_location, source_line=source_line,
                ):
                    continue
                source_product = self.env['product.product']
                if not (is_line_entry_stage or is_legacy_first_stage):
                    source_product = self._available_stage_product(
                        source_location,
                        target_product,
                        spec['qty'],
                        spec['uom'],
                        available_qty_by_key=available_stage_qty_by_key,
                        reserved_qty_by_key=reserved_stage_qty_by_key,
                    )
                if source_product:
                    if source_location == dest_location and source_product == target_product:
                        continue
                    if source_product == target_product:
                        pending_internal_move_specs.append({
                            'source_location': source_location,
                            'dest_location': dest_location,
                            'label': label,
                            'product': source_product,
                            'quantity': spec['qty'],
                            'uom': spec['uom'] or source_product.uom_id,
                            'source_production_line': source_line,
                            'source_production_lines': spec.get('source_production_lines'),
                            'result_bucket': 'product',
                        })
                    else:
                        product_move |= self._move_product_between_locations(
                            source_location,
                            dest_location,
                            label,
                            source_product,
                            target_product,
                            spec['qty'],
                            spec['uom'] or source_product.uom_id,
                            move_type='finished_product',
                            source_production_line=source_line,
                        )
                    continue
                if is_line_entry_stage or is_legacy_first_stage:
                    pending_internal_move_specs.append({
                        'source_location': self._get_production_location(),
                        'dest_location': dest_location,
                        'label': label,
                        'product': target_product,
                        'quantity': spec['qty'],
                        'uom': spec['uom'],
                        'source_production_line': source_line,
                        'source_production_lines': spec.get('source_production_lines'),
                        'result_bucket': 'product',
                    })
                    continue
                raise UserError(_(
                    'لا يوجد رصيد كافي من المنتج شبه النهائي %s في %s لاستلامه في مخزن المرحلة.'
                ) % (spec['label'], source_location.display_name))

        carryover_payloads = []
        carryover_lines_to_finish = self.env['furniture.mrp.carryover.line']
        if (
            not material_only_stage
            and stage_order
            and (stage_order.include_existing_work_products or selected_work_only)
            and source_location
            and dest_location
        ):
            carryover_payloads = self._get_registered_stage_carryover_payloads(stage_code)
            for payload in carryover_payloads:
                carryover_lines_to_finish |= payload.get('line_ids') or self.env['furniture.mrp.carryover.line']
            for payload in carryover_payloads:
                if source_location == dest_location:
                    continue
                pending_internal_move_specs.append({
                    'source_location': source_location,
                    'dest_location': dest_location,
                    'label': _('%s - متابعة الشغل القديم الموجود في الصالة') % label,
                    'product': payload['product'],
                    'quantity': payload['qty'],
                    'uom': payload['uom'],
                    'source_production_line': payload.get('source_production_line'),
                    'result_bucket': 'product',
                })

        production_location = self._get_production_location()
        material_moves = self.env['stock.move']
        material_line_candidates = self.material_line_ids.filtered(
            lambda l: l.product_id and l.qty_needed and l.stage == stage_code
        )
        if carryover_lines_to_finish:
            linked_material_lines = material_line_candidates.filtered(
                lambda line: line.carryover_line_id in carryover_lines_to_finish
            )
            if linked_material_lines:
                material_line_candidates = linked_material_lines
            elif first_stage_lines:
                material_line_candidates = material_line_candidates.filtered(lambda line: line.production_line_id in first_stage_lines)
        elif first_stage_lines:
            material_line_candidates = material_line_candidates.filtered(lambda line: line.production_line_id in first_stage_lines)
        if material_line_candidates:
            self._move_materials_to_stage_wip(stage_model, material_lines=material_line_candidates)
        material_consumption_buckets = {}
        for line in material_line_candidates:
            current_location = self._material_line_current_location(line)
            if current_location != source_location:
                continue
            existing_move = line.move_id.sudo()
            if existing_move and (
                existing_move.state in ('done', 'cancel')
                or existing_move.product_id != line.product_id
                or existing_move.location_id != source_location
                or existing_move.location_dest_id != production_location
            ):
                existing_move = self.env['stock.move']
            move = existing_move
            if existing_move:
                material_moves |= self._finalize_stock_move(
                    existing_move,
                    self._material_line_required_stock_qty(line),
                )
                self._set_material_line_move(line, existing_move)
            else:
                stock_uom = self._get_stock_uom_for_product(
                    line.product_id,
                    line.product_uom_id or line.product_id.uom_id,
                )
                key = (
                    source_location.id,
                    production_location.id,
                    line.product_id.id,
                    stock_uom.id,
                    line.stage,
                )
                bucket = material_consumption_buckets.setdefault(key, {
                    'source_location': source_location,
                    'dest_location': production_location,
                    'label': _('سحب خامات %s إلى الإنتاج') % dict(
                        FURNITURE_STAGE_SELECTION,
                    ).get(line.stage, line.stage),
                    'product': line.product_id,
                    'quantity': 0.0,
                    'uom': stock_uom,
                    'result_bucket': 'material',
                    'material_lines': self.env['furniture.mrp.material.line'],
                })
                bucket['quantity'] += self._material_line_required_stock_qty(line)
                bucket['material_lines'] |= line

        pending_internal_move_specs.extend(material_consumption_buckets.values())

        for move_spec, move in self._create_internal_moves_batch(pending_internal_move_specs):
            if move_spec.get('result_bucket') == 'material':
                material_moves |= move
                move_spec.get('material_lines').write({'move_id': move.id})
            else:
                product_move |= move

        for payload in carryover_payloads:
            payload['line_ids'].write({'state': 'stage_done'})

        message = (
            _('✅ تم استهلاك خامات %s بدون تحريك مكان المنتج النهائي')
            % dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code)
            if material_only_stage else
            _('📦 %s - %s') % (label, self._get_stage_product_name())
        )
        if carryover_payloads:
            message += '<br/>' + _('📌 تم كمان استكمال الشغل القديم الموجود في الصالة: %s') % (
                '، '.join(
                    '%s (%s %s)' % (
                        payload['label'],
                        self._format_dimension_value(payload['qty']),
                        payload['uom'].name if payload['uom'] else '',
                    )
                    for payload in carryover_payloads
                )
            )
        self.message_post(body=message)
        return product_move | material_moves

    def _create_stage_order(self, model_name, prefix, extra_vals=None):
        self.ensure_one()
        stage_code = self._stage_model_to_code(model_name)
        if stage_code and not self._is_material_only_stage(stage_code):
            reserved_lines = self._get_first_stage_start_line_candidates(stage_code)
            reserved_lines.filtered(
                lambda line: line.planned_start_stage != stage_code
            ).write({'planned_start_stage': stage_code})
        vals = {
            'production_order_id': self.id,
            'name': f'{prefix}/{self.name}',
            'state': 'pending',
        }
        if extra_vals:
            vals.update(extra_vals)
        return self.env[model_name].create(vals)

    def action_open_start_stage_selector(self):
        """Open one selector that delegates to the established stage actions."""
        self.ensure_one()
        self._check_start_stage_selector_access()
        if self.state not in ('confirmed', 'in_production'):
            raise UserError(_('يجب تأكيد أمر التشغيل أولًا قبل اختيار مرحلة البداية.'))
        startable_stage_codes = self._get_startable_stage_codes()
        if not startable_stage_codes:
            raise UserError(_(
                'لا توجد مرحلة جديدة جاهزة للبدء الآن. راجع مسارات الأصناف أو حوّل المنتج للمرحلة المطلوبة أولًا.'
            ))
        wizard_context = dict(
            self.env.context,
            default_production_id=self.id,
            furniture_start_stage_codes=startable_stage_codes,
            form_view_initial_mode='edit',
        )
        wizard = self.env['furniture.mrp.start.stage.wizard'].with_context(
            wizard_context
        ).create({
            'production_id': self.id,
            'stage_code': startable_stage_codes[0],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_start_stage_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('اختيار مرحلة بداية أمر التصنيع'),
            'res_model': 'furniture.mrp.start.stage.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': wizard_context,
            **({'view_id': view.id, 'views': [(view.id, 'form')]} if view else {}),
        }

    def action_start_priming(self):
        """المرحلة 1: التقديم"""
        for rec in self:
            rec._ensure_stage_required('priming')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.priming_order_id:
                raise UserError(_('مرحلة التقديم بدأت بالفعل.'))
            rec._ensure_stage_start_available('priming')
            order = rec._create_stage_order('furniture.mrp.priming', 'PRM')
            vals = {'state': 'in_production', 'priming_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('🎨 بدأت مرحلة التقديم: %s') % order.name)

    def action_start_painting(self):
        """المرحلة 2: تصنيع دهانات"""
        for rec in self:
            rec._ensure_stage_required('painting')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.painting_order_id:
                raise UserError(_('مرحلة تصنيع دهانات بدأت بالفعل.'))
            rec._ensure_stage_start_available('painting')
            order = rec._create_stage_order('furniture.mrp.painting', 'PNT', {
                'priming_order_id': rec.priming_order_id.id if rec.priming_order_id else False,
            })
            vals = {'state': 'in_production', 'painting_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('🖌️ بدأت مرحلة تصنيع دهانات: %s') % order.name)

    def action_start_carpentry(self):
        """المرحلة 3: تجميع"""
        for rec in self:
            rec._ensure_stage_required('carpentry')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.carpentry_order_id:
                raise UserError(_('مرحلة تجميع بدأت بالفعل.'))
            rec._ensure_stage_start_available('carpentry')
            order = rec._create_stage_order('furniture.mrp.carpentry', 'CRP', {
                'priming_order_id': rec.priming_order_id.id if rec.priming_order_id else False,
                'painting_order_id': rec.painting_order_id.id if rec.painting_order_id else False,
            })
            vals = {'state': 'in_production', 'carpentry_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('🪵 بدأت مرحلة تجميع: %s') % order.name)

    def action_start_finishing(self):
        """المرحلة 5: تجهيز"""
        for rec in self:
            rec._ensure_stage_required('finishing')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.finishing_order_id:
                raise UserError(_('مرحلة تجهيز بدأت بالفعل.'))
            rec._ensure_stage_start_available('finishing')
            order = rec._create_stage_order('furniture.mrp.finishing', 'FIN', {
                'carpentry_order_id': rec.carpentry_order_id.id if rec.carpentry_order_id else False,
                'bases_order_id': rec.bases_order_id.id if rec.bases_order_id else False,
            })
            vals = {'state': 'in_production', 'finishing_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('🔧 بدأت مرحلة تجهيز: %s') % order.name)

    def action_start_tailoring(self):
        """المرحلة 6: تفصيل"""
        for rec in self:
            rec._ensure_stage_required('tailoring')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.tailoring_order_id:
                raise UserError(_('مرحلة تفصيل بدأت بالفعل.'))
            rec._ensure_stage_start_available('tailoring')
            order = rec._create_stage_order('furniture.mrp.tailoring', 'TAL',
                                            {'finishing_order_id': rec.finishing_order_id.id if rec.finishing_order_id else False})
            vals = {'state': 'in_production', 'tailoring_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('✂️ بدأت مرحلة تفصيل: %s') % order.name)

    def action_start_bases(self):
        """المرحلة 4: القواعد"""
        for rec in self:
            rec._ensure_stage_required('bases')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.bases_order_id:
                raise UserError(_('مرحلة القواعد بدأت بالفعل.'))
            rec._ensure_stage_start_available('bases')
            order = rec._create_stage_order('furniture.mrp.bases', 'BAS', {
                'carpentry_order_id': rec.carpentry_order_id.id if rec.carpentry_order_id else False,
            })
            vals = {'state': 'in_production', 'bases_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('🧱 بدأت مرحلة القواعد: %s') % order.name)

    def action_start_sewing(self):
        """Compatibility guard: sewing now runs inside tailoring."""
        raise UserError(_(
            'الخياطة لم تعد مرحلة مستقلة. ابدأ مرحلة التفصيل، ثم نفّذ '
            'التفصيل والخياطة والتكاوي من داخل نفس أمر المرحلة.'
        ))

    def action_start_upholstery(self):
        """المرحلة 7: كسوه"""
        for rec in self:
            rec._ensure_stage_required('upholstery')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.upholstery_order_id:
                raise UserError(_('مرحلة كسوه بدأت بالفعل.'))
            rec._ensure_stage_start_available('upholstery')
            order = rec._create_stage_order('furniture.mrp.upholstery', 'UPH', {
                'carpentry_order_id': rec.carpentry_order_id.id if rec.carpentry_order_id else False,
                'tailoring_order_id': rec.tailoring_order_id.id if rec.tailoring_order_id else False,
            })
            vals = {'state': 'in_production', 'upholstery_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('🛋️ بدأت مرحلة كسوه: %s') % order.name)

    def action_start_packaging(self):
        """المرحلة 8: التغليف"""
        for rec in self:
            rec._ensure_stage_required('packaging')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.packaging_order_id:
                raise UserError(_('مرحلة التغليف بدأت بالفعل.'))
            rec._ensure_stage_start_available('packaging')
            order = rec._create_stage_order('furniture.mrp.packaging', 'PKG', {
                'upholstery_order_id': rec.upholstery_order_id.id if rec.upholstery_order_id else False,
            })
            vals = {'state': 'in_production', 'packaging_order_id': order.id}
            if not rec.date_start:
                vals['date_start'] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_('📦 بدأت مرحلة التغليف: %s') % order.name)

    def action_mark_done(self):
        """إغلاق أمر التشغيل الأسبوعي بدون إجبار المنتج على الوصول للمخزن التام."""
        for rec in self:
            rec._ensure_weekly_order_can_close()
            unassigned_lines = rec._unassigned_material_lines()
            if unassigned_lines:
                names = ', '.join(unassigned_lines.mapped('product_id.display_name'))
                raise UserError(_('لازم تحدد القسم/المرحلة للخامات دي قبل إغلاق أمر الأسبوع: %s') % names)
            rec._consolidate_equivalent_production_lines()
            rec.write({'state': 'done', 'date_finish': fields.Datetime.now()})
            rec.message_post(body=_(
                '🏁 تم إغلاق أمر التشغيل الأسبوعي. البضاعة اتسابَت في مخازن آخر المراحل المكتملة، '
                'وممكن تحولها للمخزن التام بزر منفصل لو احتجت.'
            ))

    def action_transfer_finished_product(self):
        """فتح وِيزارد اختيار المنتجات الجاهزة للتحويل للمخزن التام."""
        for rec in self:
            rec._consolidate_equivalent_production_lines()
            return rec._open_finished_product_transfer_wizard()

    def action_open_stage_transfer_wizard(self):
        self.ensure_one()
        self._consolidate_equivalent_production_lines()
        transfer_form = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_transfer_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('تحويل المنتج بين المراحل'),
            'res_model': 'furniture.mrp.stage.transfer.wizard',
            'view_mode': 'form',
            'view_id': transfer_form.id,
            'views': [(transfer_form.id, 'form')],
            'target': 'new',
            'context': {'default_production_id': self.id},
        }

    def _manual_transfer_stage_materials(
        self,
        source_stage,
        target_stage,
        transfer_move=None,
        transfer_qty=None,
        transfer_product=None,
        transfer_uom=None,
        source_production_line=False,
    ):
        self.ensure_one()
        self._ensure_stage_locations()
        if self.state in ('draft', 'done', 'cancelled'):
            raise UserError(_('التحويل بين المراحل متاح فقط بعد تأكيد أمر التشغيل الأسبوعي وقبل إنهائه.'))
        active_stages = self._required_stage_codes()
        if source_stage not in active_stages:
            raise UserError(_('مرحلة المصدر غير مختارة في قائمة المواد الخاصة بهذا المنتج.'))
        if target_stage not in active_stages:
            raise UserError(_('مرحلة الهدف غير مختارة في قائمة المواد الخاصة بهذا المنتج.'))
        if source_stage == target_stage:
            raise UserError(_('اختار مرحلتين مختلفتين للتحويل.'))

        source_label = dict(FURNITURE_STAGE_SELECTION).get(source_stage, source_stage)
        target_label = dict(FURNITURE_STAGE_SELECTION).get(target_stage, target_stage)

        source_location = self._stage_storage_location(source_stage)
        target_location = self._stage_work_location(target_stage)
        if not source_location or not target_location:
            raise UserError(_('لازم مخازن وصالات المراحل تكون محددة قبل التحويل.'))

        selected_move = transfer_move.sudo() if transfer_move else False
        if transfer_qty is not None:
            selected_qty = transfer_qty
        else:
            selected_qty = (
                selected_move.quantity
                or selected_move.product_uom_qty
                or self.product_qty
                if selected_move else self.product_qty
            )
        selected_product = transfer_product or (selected_move.product_id if selected_move else False)
        if not selected_product:
            selected_product = self._available_stage_product(source_location, self._get_stage_stock_product(), selected_qty)
        if not selected_product:
            raise UserError(_('لا يوجد رصيد متاح في مخزن %s للتحويل.') % source_label)
        selected_uom = transfer_uom or (
            selected_move.product_uom
            if selected_move and selected_move.product_uom
            else selected_product.uom_id
        )
        if selected_qty <= 0:
            raise UserError(_('الكمية المنقولة لازم تكون أكبر من صفر.'))
        source_line = source_production_line or self._find_stage_product_source_line(
            selected_product,
            source_stage,
            final_only=False,
        )
        route_payload = {
            'product': selected_product,
            'source_production': source_line.production_id if source_line else False,
            'source_production_line': source_line,
        }
        if not self._stage_payload_can_transfer_to_stage(source_stage, target_stage, route_payload):
            route_labels = dict(FURNITURE_STAGE_SELECTION)
            route_codes = self._carryover_route_stage_codes(
                selected_product,
                source_production=source_line.production_id if source_line else False,
                production_line=source_line,
            )
            allowed_labels = ', '.join(route_labels.get(code, code) for code in route_codes) or _('لا توجد مراحل محددة')
            raise UserError(_(
                'الصنف %s مساره لا يسمح بالتحويل من %s إلى %s. المراحل المسموحة لهذا الصنف: %s'
            ) % (
                selected_product.display_name,
                source_label,
                target_label,
                allowed_labels,
            ))
        if not self._source_stage_ready_for_manual_transfer(source_stage, source_location, selected_product):
            raise UserError(_('لازم مرحلة %s تكون منتهية ومقبولة جودة قبل التحويل منها.') % source_label)
        self._ensure_location_has_qty(source_location, selected_product, selected_qty, selected_uom)

        existing_move = self.env['stock.move'].sudo().search([
            ('origin', '=', self.name),
            ('product_id', '=', selected_product.id),
            ('location_id', '=', source_location.id),
            ('location_dest_id', '=', target_location.id),
            ('state', 'not in', ('done', 'cancel')),
        ], limit=1)
        label = _('تحويل المنتج شبه النهائي من %s إلى %s') % (source_label, target_label)
        if existing_move:
            move = self._finalize_stock_move(existing_move, selected_qty)
        else:
            move = self._create_internal_move(
                source_location,
                target_location,
                label,
                move_type='stage_transfer',
                product=selected_product,
                quantity=selected_qty,
                uom=selected_uom,
                source_production_line=source_line,
            )

        target_order = self._stage_order_record(target_stage)
        self._reopen_stage_order_for_pending_start(target_order, target_stage)
        self.message_post(body=_('🔁 تم تحويل المنتج شبه النهائي من %s إلى %s') % (source_label, target_label))
        return move

    def _manual_transfer_stage_materials_batch(
        self,
        source_stage,
        target_stage,
        transfer_specs,
    ):
        """Validate exact allocations once and execute their moves as a batch.

        Each technical production line still receives its own stock move.  We
        only batch create/confirm/assign/done, so FIFO, cost lineage and
        per-piece traceability remain intact while avoiding one full stock
        lifecycle per displayed piece.
        """
        self.ensure_one()
        transfer_specs = list(transfer_specs or [])
        if not transfer_specs:
            return self.env['stock.move']
        self._ensure_stage_locations()
        if self.state in ('draft', 'done', 'cancelled'):
            raise UserError(_(
                'التحويل بين المراحل متاح فقط بعد تأكيد أمر التشغيل الأسبوعي وقبل إنهائه.'
            ))
        active_stages = self._required_stage_codes()
        if source_stage not in active_stages:
            raise UserError(_('مرحلة المصدر غير مختارة في قائمة المواد الخاصة بهذا المنتج.'))
        if target_stage not in active_stages:
            raise UserError(_('مرحلة الهدف غير مختارة في قائمة المواد الخاصة بهذا المنتج.'))
        if source_stage == target_stage:
            raise UserError(_('اختار مرحلتين مختلفتين للتحويل.'))

        source_label = dict(FURNITURE_STAGE_SELECTION).get(source_stage, source_stage)
        target_label = dict(FURNITURE_STAGE_SELECTION).get(target_stage, target_stage)
        source_location = self._stage_storage_location(source_stage)
        target_location = self._stage_work_location(target_stage)
        if not source_location or not target_location:
            raise UserError(_('لازم مخازن وصالات المراحل تكون محددة قبل التحويل.'))

        ready_products = self.env['product.product']
        move_specs = []
        for transfer_spec in transfer_specs:
            product = transfer_spec.get('product')
            uom = transfer_spec.get('uom') or (product.uom_id if product else False)
            qty = transfer_spec.get('qty') or 0.0
            source_line = transfer_spec.get('source_production_line')
            if not product or float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            route_payload = {
                'product': product,
                'source_production': source_line.production_id if source_line else False,
                'source_production_line': source_line,
            }
            if not self._stage_payload_can_transfer_to_stage(
                source_stage,
                target_stage,
                route_payload,
            ):
                route_labels = dict(FURNITURE_STAGE_SELECTION)
                route_codes = self._carryover_route_stage_codes(
                    product,
                    source_production=source_line.production_id if source_line else False,
                    production_line=source_line,
                )
                allowed_labels = ', '.join(
                    route_labels.get(code, code) for code in route_codes
                ) or _('لا توجد مراحل محددة')
                raise UserError(_(
                    'الصنف %s مساره لا يسمح بالتحويل من %s إلى %s. '
                    'المراحل المسموحة لهذا الصنف: %s'
                ) % (
                    product.display_name,
                    source_label,
                    target_label,
                    allowed_labels,
                ))
            if product not in ready_products:
                if not self._source_stage_ready_for_manual_transfer(
                    source_stage,
                    source_location,
                    product,
                ):
                    raise UserError(_(
                        'لازم مرحلة %s تكون منتهية ومقبولة جودة قبل التحويل منها.'
                    ) % source_label)
                ready_products |= product
            move_specs.append({
                'source_location': source_location,
                'dest_location': target_location,
                'product': product,
                'quantity': qty,
                'uom': uom,
                'source_production_line': source_line,
                'source_production_lines': source_line,
                'label': _('تحويل المنتج شبه النهائي من %s إلى %s') % (
                    source_label,
                    target_label,
                ),
            })

        created = self._create_internal_moves_batch(move_specs)
        moves = self.env['stock.move']
        for _spec, move in created:
            moves |= move
        target_order = self._stage_order_record(target_stage)
        self._reopen_stage_order_for_pending_start(target_order, target_stage)
        self.message_post(body=_(
            '🔁 تم تحويل المنتجات شبه النهائية من %s إلى %s'
        ) % (source_label, target_label))
        return moves

    def _source_stage_ready_for_manual_transfer(self, source_stage, source_location, source_product):
        self.ensure_one()
        source_order = self._stage_order_record(source_stage)
        if source_order and source_order.state == 'done':
            return True
        if not source_product or not source_location:
            return False
        source_production = self._find_stage_product_source_production(
            source_product,
            source_stage,
            final_only=False,
        )
        if source_production:
            source_stage_order = source_production._stage_order_record(source_stage)
            if source_stage_order and source_stage_order.state == 'done':
                return True
        # مخازن المراحل مشتركة بين أوامر الإنتاج، فالشغل القديم الجاهز يثبت نفسه بحركة دخول مكتملة.
        return self._has_stage_stock_history(source_product, source_location)

    def _ensure_finished_product_is_storable(self):
        self.ensure_one()
        if self.production_line_ids:
            for spec in self._get_finished_output_specs(ensure_storable=False):
                self._ensure_stock_product_is_storable(spec['product'], spec['uom'])
            return
        product_tmpl = self.product_id.product_tmpl_id
        if product_tmpl.is_storable:
            return
        try:
            product_tmpl.write({'type': 'consu', 'is_storable': True})
            product_tmpl.invalidate_recordset(['type', 'is_storable'])
        except UserError:
            self.env.cr.execute(
                """
                UPDATE product_template
                   SET type = 'consu',
                       is_storable = TRUE
                 WHERE id = %s
                """,
                [product_tmpl.id],
            )
            product_tmpl.invalidate_recordset(['type', 'is_storable'])

    def _move_finished_product(self, specs=None):
        """استلام المنتج النهائي من آخر مرحلة إلى مخزن البضاعة النهائية."""
        self.ensure_one()
        self._ensure_stage_locations()
        self._ensure_finished_product_is_storable()
        active_stage_codes = self._required_stage_codes()
        default_source_stage_code = active_stage_codes[-1] if active_stage_codes else False
        default_source_location = self._stage_storage_location(default_source_stage_code) if default_source_stage_code else self._get_production_location()
        dest_location = (
            self.location_dest_id
            or self.env.ref('furniture_mrp.location_finished_goods', raise_if_not_found=False)
            or self.env.ref('stock.stock_location_stock')
        )
        self._sync_stage_product_name()
        label = _('استلام المنتج النهائي من آخر مرحلة')
        transfer_specs = list(specs or self._get_finished_output_specs())
        source_lines = self.env['furniture.mrp.production.line']
        for transfer_spec in transfer_specs:
            source_lines |= transfer_spec.get('source_production_line') or self.env[
                'furniture.mrp.production.line'
            ]
        cost_snapshots = self._get_production_line_cost_snapshots(source_lines)
        pending_finished_move_buckets = {}
        available_stage_qty_by_key = {}
        reserved_stage_qty_by_key = {}
        deferred_accounting_layers = self.env['stock.valuation.layer'].sudo()
        finished_moves = self.env['stock.move']
        for spec in transfer_specs:
            finished_product = spec['product']
            source_location = spec.get('source_location') or default_source_location
            source_line = spec.get('source_production_line') or self.env['furniture.mrp.production.line']
            snapshot = cost_snapshots.get(source_line.id, {}) if source_line else {}
            unit_cost = (
                max(
                    (snapshot.get('material_unit') or 0.0)
                    + (snapshot.get('labor_unit') or 0.0),
                    0.0,
                )
                if snapshot.get('has_cost')
                else max(
                    finished_product.with_company(self.company_id).standard_price or 0.0,
                    0.0,
                )
            )
            source_product = self._available_stage_product(
                source_location,
                finished_product,
                spec['qty'],
                spec['uom'],
                available_qty_by_key=available_stage_qty_by_key,
                reserved_qty_by_key=reserved_stage_qty_by_key,
            )
            if not source_product:
                raise UserError(_(
                    'لا يوجد رصيد كافي من المنتج شبه النهائي %s في %s لإرساله إلى مخزن البضاعة التامة.'
                ) % (spec['label'], source_location.display_name))
            if source_product == finished_product:
                family_root = (
                    self._production_line_display_family_origin(source_line)
                    if source_line else self.env['furniture.mrp.production.line']
                )
                keep_piece_move = bool(
                    source_product.tracking != 'none'
                    or source_product.lot_valuated
                )
                family_key = source_line.id if keep_piece_move and source_line else family_root.id
                key = (
                    source_location.id,
                    dest_location.id,
                    source_product.id,
                    source_product.uom_id.id,
                    family_key,
                )
                bucket = pending_finished_move_buckets.setdefault(key, {
                    'source_location': source_location,
                    'dest_location': dest_location,
                    'label': label,
                    'product': source_product,
                    'quantity': 0.0,
                    'uom': source_product.uom_id,
                    'source_production_line': family_root or source_line,
                    'source_production_lines': self.env['furniture.mrp.production.line'],
                    'allocations': [],
                })
                bucket['quantity'] += spec['qty']
                bucket['source_production_lines'] |= source_line
                bucket['allocations'].append({
                    'production_line': source_line,
                    'quantity': spec['qty'],
                    'unit_cost': unit_cost,
                    'finished_transfer_line': spec.get('finished_transfer_line'),
                })
                continue

            existing_move_domain = [
                ('origin', '=', self.name),
                ('product_id', '=', source_product.id),
                ('location_id', '=', source_location.id),
                ('location_dest_id', '=', dest_location.id),
                ('state', 'not in', ('done', 'cancel')),
            ]
            if source_line:
                existing_move_domain.append((
                    'furniture_source_production_line_id',
                    '=',
                    source_line.id,
                ))
            existing_move = self.env['stock.move'].sudo().search(existing_move_domain, limit=1)
            if existing_move:
                moves = self._finalize_stock_move(existing_move, spec['qty'])
            else:
                moves = self._move_product_between_locations(
                    source_location,
                    dest_location,
                    label,
                    source_product,
                    finished_product,
                    spec['qty'],
                    source_product.uom_id,
                    move_type='finished_product',
                    source_production_line=source_line,
                    dest_unit_cost=unit_cost,
                )
            finished_move = moves.filtered(
                lambda move: move.product_id == finished_product
                and move.location_dest_id == dest_location
                and move.state == 'done'
            )[-1:]
            if not finished_move:
                raise UserError(_('تعذر تحديد حركة دخول %s إلى المخزن التام.') % finished_product.display_name)
            finished_moves |= finished_move
            finished_move.write({'furniture_actual_unit_cost': unit_cost})
            finished_move.stock_valuation_layer_ids.write({
                'furniture_wip_origin_line_id': self._finished_cost_origin_line(source_line).id if source_line else False,
                'furniture_finished_production_line_id': source_line.id if source_line else False,
                'furniture_finished_move_id': finished_move.id,
            })
            if spec.get('finished_transfer_line'):
                spec['finished_transfer_line'].write({'finished_move_id': finished_move.id})

        pending_batch_specs = []
        completed_group_moves = []
        for bucket in pending_finished_move_buckets.values():
            existing_moves = self.env['stock.move'].sudo().search([
                ('origin', '=', self.name),
                ('product_id', '=', bucket['product'].id),
                ('location_id', '=', bucket['source_location'].id),
                ('location_dest_id', '=', dest_location.id),
                ('state', 'not in', ('done', 'cancel')),
            ])
            existing_move = existing_moves.filtered(lambda move: (
                move.furniture_source_production_line_id == bucket['source_production_line']
                or bool(move.furniture_source_production_line_ids & bucket['source_production_lines'])
            ))[:1]
            if existing_move:
                existing_move.write({
                    'furniture_source_production_line_ids': [
                        (6, 0, bucket['source_production_lines'].ids),
                    ],
                })
                completed_group_moves.append((
                    bucket,
                    self._finalize_stock_move(existing_move, bucket['quantity']),
                ))
            else:
                pending_batch_specs.append(bucket)

        completed_group_moves.extend(
            self._create_internal_moves_batch(pending_batch_specs)
        )
        for move_spec, finished_move in completed_group_moves:
            weighted_value = 0.0
            weighted_qty = 0.0
            allocation_transfer_lines = self.env['furniture.mrp.finished.transfer.line']
            for allocation in move_spec['allocations']:
                allocated_layers = self._assign_finished_fifo_cost(
                    finished_move,
                    allocation['production_line'],
                    allocation['quantity'],
                    allocation['unit_cost'],
                    validate_accounting=False,
                    update_move_cost=False,
                )
                deferred_accounting_layers |= allocated_layers.filtered(lambda layer: (
                    layer.stock_valuation_layer_id
                    and not layer.account_move_id
                    and not self.company_id.currency_id.is_zero(layer.value)
                ))
                weighted_value += allocation['unit_cost'] * allocation['quantity']
                weighted_qty += allocation['quantity']
                allocation_transfer_lines |= (
                    allocation.get('finished_transfer_line')
                    or self.env['furniture.mrp.finished.transfer.line']
                )
            finished_move.write({
                'furniture_actual_unit_cost': (
                    weighted_value / weighted_qty if weighted_qty else 0.0
                ),
            })
            if allocation_transfer_lines:
                allocation_transfer_lines.write({'finished_move_id': finished_move.id})
            finished_moves |= finished_move
        if deferred_accounting_layers:
            deferred_accounting_layers._validate_accounting_entries()
        self._restore_finished_product_name()
        return finished_moves

    def _move_finished_carryover_products(self, payloads=None):
        """تحويل الشغل القديم المكتمل بعد آخر مرحلة مختارة للمخزن التام."""
        self.ensure_one()
        self._ensure_stage_locations()
        dest_location = (
            self.location_dest_id
            or self.env.ref('furniture_mrp.location_finished_goods', raise_if_not_found=False)
            or self.env.ref('stock.stock_location_stock')
        )
        payloads = payloads if payloads is not None else self._get_finished_transfer_carryover_payloads()
        grouped_lines = {}
        for payload in payloads:
            source_location = payload.get('source_location')
            product = payload.get('product')
            uom = payload.get('uom') or (product.uom_id if product else False)
            if not source_location or not product or not uom:
                continue
            key = (
                payload.get('source_stage'),
                source_location.id,
                product.id,
                uom.id,
                payload.get('source_production_line').id if payload.get('source_production_line') else False,
            )
            grouped_lines.setdefault(key, {
                'source_location': source_location,
                'product': product,
                'uom': uom,
                'qty': 0.0,
                'line_ids': self.env['furniture.mrp.carryover.line'],
                'stage_code': payload.get('source_stage'),
                'legacy': bool(payload.get('legacy')),
                'source_production_line': payload.get('source_production_line'),
            })
            grouped_lines[key]['qty'] += payload.get('qty') or 0.0
            grouped_lines[key]['line_ids'] |= payload.get('line_ids') or self.env['furniture.mrp.carryover.line']
            grouped_lines[key]['legacy'] = grouped_lines[key]['legacy'] or bool(payload.get('legacy'))

        moves = self.env['stock.move']
        for payload in grouped_lines.values():
            product = payload['product']
            uom = payload['uom']
            qty = self._quantity_in_product_uom(product, payload['qty'], uom)
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            if float_compare(
                self._stage_location_product_qty(payload['source_location'], product),
                qty,
                precision_digits=3,
            ) < 0:
                raise UserError(_(
                    'لا يوجد رصيد كافي من الشغل القديم %s في %s للتحويل للمخزن التام.'
                ) % (product.display_name, payload['source_location'].display_name))
            unit_cost = self._finished_actual_unit_cost(
                payload.get('source_production_line'),
                product,
            )
            transfer_moves = self._move_product_between_locations(
                payload['source_location'],
                dest_location,
                _('تحويل الشغل القديم المكتمل للمخزن التام'),
                product,
                product,
                qty,
                product.uom_id,
                move_type='finished_product',
                source_production_line=payload.get('source_production_line'),
                dest_unit_cost=unit_cost,
            )
            moves |= transfer_moves
            finished_move = transfer_moves.filtered(
                lambda move: move.product_id == product
                and move.location_dest_id == dest_location
                and move.state == 'done'
            )[-1:]
            if finished_move:
                self._assign_finished_fifo_cost(
                    finished_move,
                    payload.get('source_production_line'),
                    qty,
                    unit_cost,
                )
            if payload['line_ids']:
                payload['line_ids'].write({'state': 'finished_transferred'})
            elif payload.get('legacy'):
                self.env['furniture.mrp.carryover.line'].create({
                    'production_id': self.id,
                    'source_origin': self.name,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'qty': qty,
                    'current_stage': payload['stage_code'],
                    'state': 'finished_transferred',
                })
        return moves

    def _clear_finished_piece_images(self, source_lines):
        """Forget temporary work images once their exact pieces reach finished stock.

        The image lives only on ``furniture.mrp.production.line``.  Product and
        product-template images are deliberately outside this method so the
        Inventory card can never be changed by the manufacturing workflow.
        """
        source_line_ids = source_lines.ids
        if not source_line_ids:
            return
        Line = self.env['furniture.mrp.production.line'].with_context(active_test=False).sudo()
        lines = Line.browse(source_line_ids).exists()
        FinishedLine = self.env['furniture.mrp.finished.transfer.line'].sudo()
        pending_source_line_ids = set(FinishedLine.search([
            ('source_production_line_id', 'in', lines.ids),
            ('state', '=', 'ready'),
        ]).mapped('source_production_line_id').ids)
        lines_to_clear = lines.filtered(lambda line: (
            line.id not in pending_source_line_ids
            and (line.batch_image_1920 or line.batch_image_token)
        ))
        if lines_to_clear:
            lines_to_clear.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
                furniture_skip_line_consolidation=True,
            ).write({
                'batch_image_1920': False,
                'batch_image_token': False,
            })

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(_('لا يمكن إلغاء أمر منتهٍ.'))
            rec.write({'state': 'cancelled'})
            rec.message_post(body=_('❌ تم إلغاء أمر التشغيل الأسبوعي'))

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError(_('لا يمكن إعادة هذا الأمر للمسودة.'))
            rec.write({'state': 'draft'})

    # ─── أزرار العرض ─────────────────────────────────────────────────────────
    def _action_view_stage(self, model, record_id, title):
        return {
            'type': 'ir.actions.act_window', 'name': title,
            'res_model': model, 'res_id': record_id,
            'view_mode': 'form', 'target': 'current',
        }

    def action_view_location_products(self):
        self.ensure_one()
        location_field = self.env.context.get('location_field')
        allowed_fields = {
            'location_src_id',
            'location_priming_wip_id',
            'location_priming_id',
            'location_painting_wip_id',
            'location_painting_id',
            'location_carpentry_wip_id',
            'location_carpentry_id',
            'location_bases_wip_id',
            'location_bases_id',
            'location_finishing_wip_id',
            'location_finishing_id',
            'location_tailoring_wip_id',
            'location_tailoring_id',
            'location_upholstery_wip_id',
            'location_upholstery_id',
            'location_packaging_id',
            'location_dest_id',
        }
        if location_field not in allowed_fields:
            raise UserError(_('لم يتم تحديد المخزن المطلوب عرضه.'))
        location = self[location_field]
        if not location:
            raise UserError(_('اختار المخزن الأول قبل عرض المنتجات.'))

        action = self.env.ref('stock.act_product_location_open').sudo().read()[0]
        action.update({
            'name': _('منتجات %s') % location.display_name,
            'view_mode': action.get('view_mode') or 'list,form',
            # Phantom BoM parents are virtual sales Kits. They do not own the
            # stock even though Odoo computes their availability from the
            # physical component quants.
            'domain': [('qty_available', '>', 0), ('is_kits', '=', False)],
            'context': {
                'active_id': location.id,
                'active_ids': [location.id],
                'active_model': 'stock.location',
                'location': location.id,
                'production_id': self.id,
                # Physical stock must remain visible even if the product master
                # was archived after production.
                'active_test': False,
                'search_default_real_stock_available': 1,
                'search_default_virtual_stock_available': 1,
                'search_default_virtual_stock_negative': 1,
                'search_default_real_stock_negative': 1,
                'create': False,
            },
        })
        return action

    def action_view_priming(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.priming', self.priming_order_id.id, 'أمر التقديم')

    def action_view_painting(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.painting', self.painting_order_id.id, 'أمر تصنيع دهانات')

    def action_view_carpentry(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.carpentry', self.carpentry_order_id.id, 'أمر تجميع')

    def action_view_bases(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.bases', self.bases_order_id.id, 'أمر القواعد')

    def action_view_upholstery(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.upholstery', self.upholstery_order_id.id, 'أمر كسوه')

    def action_view_finishing(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.finishing', self.finishing_order_id.id, 'أمر تجهيز')

    def action_view_tailoring(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.tailoring', self.tailoring_order_id.id, 'أمر تفصيل')

    def action_view_sewing(self):
        self.ensure_one()
        if self.tailoring_order_id:
            return self.action_view_tailoring()
        raise UserError(_('الخياطة أصبحت مرحلة داخل التفصيل، ولم يبدأ أمر التفصيل بعد.'))

    def action_view_packaging(self):
        self.ensure_one()
        return self._action_view_stage('furniture.mrp.packaging', self.packaging_order_id.id, 'أمر التغليف')

    def action_view_purchases(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': 'طلبات الشراء',
            'res_model': 'purchase.order',
            'domain': [('id', 'in', self.purchase_order_ids.ids)],
            'view_mode': 'list,form',
        }

    def action_open_missing_vendor_purchase_order(self):
        self.ensure_one()
        purchase_order = self.missing_vendor_purchase_order_id
        if not purchase_order:
            raise UserError(_('لا يوجد طلب شراء بدون مورد جاهز لفتحه.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('طلب الشراء بدون مورد'),
            'res_model': 'purchase.order',
            'res_id': purchase_order.id,
            'view_mode': 'form',
            'target': 'current',
        }


class FurnitureMrpMaterialLine(models.Model):
    """مكونات أمر التشغيل الأسبوعي (من BoM)"""
    _name = 'furniture.mrp.material.line'
    _description = 'مكونات الإنتاج'

    production_id = fields.Many2one('furniture.mrp.production', required=True, ondelete='cascade')
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر التشغيل',
        ondelete='cascade',
    )
    bom_line_id   = fields.Many2one('mrp.bom.line', string='سطر BoM', ondelete='set null')
    product_id    = fields.Many2one('product.product', string='المادة', required=True)
    product_uom_id= fields.Many2one('uom.uom', string='وحدة القياس', required=True)
    qty_needed    = fields.Float(string='الكمية المطلوبة', required=True, digits=(16, 6))
    bom_qty_editable = fields.Boolean(
        string='يمكن تعديل كمية الخامة في أمر الإنتاج',
        compute='_compute_bom_qty_editable',
    )
    stage         = fields.Selection(FURNITURE_STAGE_SELECTION, string='القسم/المرحلة')
    quantity_mode = fields.Selection(
        FURNITURE_MATERIAL_QUANTITY_MODE_SELECTION,
        string='حساب الكمية',
        required=True,
        default='scaled',
        readonly=True,
    )
    carryover_line_id = fields.Many2one(
        'furniture.mrp.carryover.line',
        string='سطر الشغل القديم',
        ondelete='cascade',
        index=True,
    )
    qty_available = fields.Float(string='المتوفر في المخزن', compute='_compute_availability', store=False, digits=(16, 3))
    qty_reserved  = fields.Float(string='محجوز', compute='_compute_availability', store=False, digits=(16, 3))
    availability  = fields.Selection([
        ('ok', '✅ متوفر'), ('partial', '⚠️ جزئي'), ('missing', '❌ ناقص'),
    ], compute='_compute_availability', store=False)
    move_id       = fields.Many2one('stock.move', string='حركة المخزون', readonly=True)
    warehouse_receipt_confirmed = fields.Boolean(
        string='تم تأكيد استلام المخزن', readonly=True, copy=False,
    )
    warehouse_received_qty = fields.Float(
        string='الكمية المستلمة فعليًا', readonly=True, copy=False,
        digits=(16, 3),
    )
    warehouse_receipt_stage_id = fields.Many2one(
        'furniture.mrp.advance.material.release.stage',
        string='مرحلة إذن الاستلام', readonly=True, copy=False,
        ondelete='set null', index=True,
    )
    purchase_unit_cost = fields.Float(
        string='متوسط سعر شراء الوحدة',
        compute='_compute_costs',
        store=True,
        digits=(16, 2),
    )
    material_cost = fields.Float(
        string='تكلفة الخامة',
        compute='_compute_costs',
        store=True,
        digits=(16, 2),
    )

    @api.depends(
        'production_id.state',
        'production_line_id',
        'move_id',
        'warehouse_receipt_confirmed',
        'warehouse_receipt_stage_id',
    )
    @api.depends_context('uid')
    def _compute_bom_qty_editable(self):
        role_allowed = bool(
            self.env.is_superuser()
            or self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_manager'
            )
            or self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_supervisor'
            )
        )
        locked_material_ids = set()
        persisted_lines = self.filtered(lambda line: isinstance(line.id, int))
        if persisted_lines:
            active_release_stages = self.env[
                'furniture.mrp.advance.material.release.stage'
            ].sudo().search([
                ('source_material_line_ids', 'in', persisted_lines.ids),
                ('state', 'in', ('pending', 'issued', 'started')),
            ])
            locked_material_ids = set(
                active_release_stages.mapped('source_material_line_ids').ids
            )
        for line in self:
            line.bom_qty_editable = bool(
                role_allowed
                and line.production_line_id
                and line.production_id.state in (
                    'draft', 'confirmed', 'in_production',
                )
                and not line.move_id
                and not line.warehouse_receipt_confirmed
                and not line.warehouse_receipt_stage_id
                and line.id not in locked_material_ids
            )

    def _bom_qty_override_key(self):
        self.ensure_one()
        if not self.production_line_id:
            return False
        base_key = (
            self.stage or False,
            self.product_id.id,
            self.product_uom_id.id,
        )
        matching_lines = self.production_line_id.material_line_ids.filtered(
            lambda line: (
                (line.stage or False),
                line.product_id.id,
                line.product_uom_id.id,
            ) == base_key
        ).sorted('id')
        occurrence = matching_lines.ids.index(self.id) if self.id in matching_lines.ids else 0
        return (*base_key, occurrence)

    def _check_bom_qty_manual_edit_allowed(self):
        for line in self:
            if not line.bom_qty_editable:
                raise UserError(_(
                    'لا يمكن تعديل كمية الخامة بعد إنشاء أو صرف إذن المرحلة. '
                    'عدّلها قبل طلب الخامات من المخزن.'
                ))

    def _furniture_normalize_uom_vals(self, vals):
        vals = dict(vals)
        product = self.env['product.product'].browse(vals.get('product_id')) if vals.get('product_id') else self.product_id[:1]
        uom = self.env['uom.uom'].browse(vals.get('product_uom_id')) if vals.get('product_uom_id') else self.product_uom_id[:1]
        tailoring_allocation = self.env[
            'furniture.mrp.tailoring.material.allocation'
        ].browse(vals.get('tailoring_allocation_id')).exists()
        if (
            tailoring_allocation
            and product == tailoring_allocation.product_id
            and uom == tailoring_allocation.product_uom_id
        ):
            # Manual tailoring allocations deliberately keep the exact unit
            # chosen by the manager (for example, meter instead of unit).
            return vals
        normalized_uom = _normalize_purchase_uom_for_product(product, uom)
        if product and normalized_uom and (not uom or normalized_uom != uom):
            vals['product_uom_id'] = normalized_uom.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._furniture_normalize_uom_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        manual_qty_edit = bool(
            self.env.context.get('furniture_bom_manual_qty_edit')
            and 'qty_needed' in vals
        )
        override_keys = []
        if manual_qty_edit:
            if float_compare(
                vals.get('qty_needed') or 0.0,
                0.0,
                precision_digits=3,
            ) < 0:
                raise ValidationError(_('كمية الخامة لا يمكن أن تكون سالبة.'))
            self._check_bom_qty_manual_edit_allowed()
            override_keys = [
                (line, line._bom_qty_override_key())
                for line in self
            ]
            vals['quantity_mode'] = 'fixed'

        if len(self) <= 1:
            result = super().write(self._furniture_normalize_uom_vals(vals))
        elif not ({'product_id', 'product_uom_id'} & set(vals)):
            result = super().write(vals)
        else:
            result = True
            for rec in self:
                result = super(FurnitureMrpMaterialLine, rec).write(
                    rec._furniture_normalize_uom_vals(vals)
                ) and result

        if manual_qty_edit:
            for line, override_key in override_keys:
                if override_key and line.production_line_id:
                    line.production_line_id._set_material_qty_override(
                        override_key,
                        line.qty_needed,
                    )
        return result

    def _get_available_qty_in_source_location(self):
        self.ensure_one()
        if not self.product_id:
            return 0.0
        if self.product_id.is_storable:
            return self.product_id.qty_available

        source_location = (
            self.production_id.location_src_id
            or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        )
        if not source_location:
            return 0.0
        done_moves = self.env['stock.move'].sudo().search([
            ('product_id', '=', self.product_id.id),
            ('state', '=', 'done'),
            '|',
            ('location_id', '=', source_location.id),
            ('location_dest_id', '=', source_location.id),
        ])
        incoming = sum(done_moves.filtered(
            lambda m: m.location_dest_id == source_location
        ).mapped('quantity'))
        outgoing = sum(done_moves.filtered(
            lambda m: m.location_id == source_location
        ).mapped('quantity'))
        return incoming - outgoing

    def _compute_availability(self):
        for line in self:
            available = line._get_available_qty_in_source_location()
            reserved  = line.product_id.virtual_available if line.product_id and line.product_id.is_storable else available
            line.qty_available = available
            line.qty_reserved  = reserved
            if available >= line.qty_needed:
                line.availability = 'ok'
            elif available > 0:
                line.availability = 'partial'
            else:
                line.availability = 'missing'

    @api.depends(
        'production_id', 'product_id', 'product_uom_id', 'qty_needed',
        'warehouse_receipt_confirmed', 'warehouse_received_qty',
    )
    def _compute_costs(self):
        for line in self:
            if not line.production_id or not line.product_id or not line.qty_needed:
                line.purchase_unit_cost = 0.0
                line.material_cost = 0.0
                continue
            unit_cost = line.production_id._get_purchase_material_unit_cost(
                line.product_id,
                line.product_uom_id or line.product_id.uom_id,
            )
            line.purchase_unit_cost = unit_cost
            cost_qty = (
                line.warehouse_received_qty
                if line.warehouse_receipt_confirmed
                else line.qty_needed
            )
            line.material_cost = unit_cost * cost_qty


class FurnitureMrpCarryoverLine(models.Model):
    """الشغل القديم الذي تم ضمه لأمر تشغيل أسبوعي جديد."""
    _name = 'furniture.mrp.carryover.line'
    _description = 'الشغل القديم المكمل مع أمر التشغيل'
    _order = 'id desc'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        ondelete='cascade',
    )
    source_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأصلي',
        ondelete='set null',
    )
    source_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر التشغيل الأصلي',
        ondelete='set null',
    )
    source_origin = fields.Char(string='مرجع الأمر الأصلي')
    product_id = fields.Many2one('product.product', string='الصنف', required=True)
    dimension_label = fields.Char(
        string='المقاس',
        related='product_id.furniture_dimension_label',
        readonly=True,
    )
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True)
    qty = fields.Float(string='الكمية', required=True, digits=(16, 3))
    current_stage = fields.Selection(
        FURNITURE_LEGACY_STAGE_SELECTION,
        string='المرحلة الحالية',
        required=True,
    )
    stage_label = fields.Char(
        string='اسم المرحلة',
        compute='_compute_stage_label',
    )
    state = fields.Selection([
        ('selected', 'مختار'),
        ('started', 'داخل المرحلة'),
        ('stage_done', 'في مخزن المرحلة'),
        ('finished_transferred', 'اتحول للمخزن التام'),
    ], string='الحالة', default='selected', required=True)
    is_final_stage = fields.Boolean(
        string='آخر مرحلة للمنتج',
        compute='_compute_is_final_stage',
    )

    @api.depends('current_stage')
    def _compute_stage_label(self):
        labels = dict(FURNITURE_LEGACY_STAGE_SELECTION)
        for rec in self:
            rec.stage_label = labels.get(rec.current_stage, rec.current_stage or '')

    @api.depends(
        'production_id',
        'product_id',
        'current_stage',
        'source_production_id',
        'source_production_line_id',
        'production_id.use_priming',
        'production_id.use_painting',
        'production_id.use_carpentry',
        'production_id.use_finishing',
        'production_id.use_tailoring',
        'production_id.use_upholstery',
        'production_id.use_packaging',
    )
    def _compute_is_final_stage(self):
        for rec in self:
            rec.is_final_stage = bool(
                rec.production_id
                and rec.product_id
                and rec.current_stage
                and rec.production_id._is_final_stage_for_product(
                    rec.product_id,
                    rec.current_stage,
                    source_production=rec.source_production_id,
                    production_line=rec.source_production_line_id,
                )
            )


class FurnitureMrpFinishedTransferLine(models.Model):
    """المنتجات الجاهزة للتحويل للمخزن التام."""
    _name = 'furniture.mrp.finished.transfer.line'
    _description = 'المنتجات الجاهزة للتحويل للمخزن التام'
    _order = 'state asc, id desc'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        ondelete='cascade',
    )
    source_kind = fields.Selection([
        ('current', 'أمر الأسبوع الحالي'),
        ('carryover', 'الشغل القديم'),
    ], string='نوع المصدر', required=True, index=True)
    source_stage = fields.Selection(
        FURNITURE_LEGACY_STAGE_SELECTION,
        string='المرحلة',
        required=True,
    )
    source_origin = fields.Char(string='مرجع المصدر')
    source_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر المصدر',
        ondelete='set null',
    )
    source_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر المصدر',
        ondelete='set null',
        index=True,
    )
    source_location_id = fields.Many2one(
        'stock.location',
        string='مخزن المصدر',
        readonly=True,
    )
    finished_move_id = fields.Many2one(
        'stock.move',
        string='حركة التحويل المجمعة للمخزن التام',
        readonly=True,
        copy=False,
        ondelete='set null',
        index=True,
    )
    carryover_line_ids = fields.Many2many(
        'furniture.mrp.carryover.line',
        'furniture_mrp_fin_trans_carry_rel',
        'finished_line_id',
        'carryover_line_id',
        string='أسطر الشغل القديم',
        readonly=True,
    )
    product_id = fields.Many2one('product.product', string='الصنف', required=True)
    dimension_label = fields.Char(
        string='المقاس',
        related='product_id.furniture_dimension_label',
        readonly=True,
    )
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True)
    qty = fields.Float(string='الكمية', required=True, digits=(16, 3))
    state = fields.Selection([
        ('ready', 'جاهز'),
        ('transferred', 'تم التحويل'),
    ], string='الحالة', default='ready', required=True, index=True)


class FurnitureMrpFinishedTransferWizard(models.TransientModel):
    _name = 'furniture.mrp.finished.transfer.wizard'
    _description = 'تحويل المنتجات الجاهزة للمخزن التام'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        readonly=True,
        ondelete='cascade',
    )
    line_ids = fields.One2many(
        'furniture.mrp.finished.transfer.wizard.line',
        'wizard_id',
        string='المنتجات الجاهزة للتحويل',
        copy=False,
    )

    def action_transfer_selected(self):
        self.ensure_one()
        selected_lines = self.line_ids.filtered(lambda line: line.selected and line.transfer_line_id and line.transfer_line_id.state == 'ready')
        if not selected_lines:
            raise UserError(_('اختار منتج واحد على الأقل للتحويل للمخزن التام.'))

        selected_transfer_lines = self.env['furniture.mrp.finished.transfer.line']
        for wizard_line in selected_lines:
            selected_transfer_lines |= (
                wizard_line.transfer_line_ids
                or wizard_line.transfer_line_id
            )
        selected_transfer_lines = selected_transfer_lines.filtered(
            lambda line: line.state == 'ready'
        )

        current_specs = []
        carryover_payloads = []
        completed_source_lines = selected_transfer_lines.mapped('source_production_line_id')
        for finished_line in selected_transfer_lines:
            if finished_line.source_kind == 'current':
                current_specs.append({
                    'product': finished_line.product_id,
                    'qty': finished_line.qty,
                    'uom': finished_line.product_uom_id,
                    'label': finished_line.product_id.display_name,
                    'source_stage': finished_line.source_stage,
                    'source_location': finished_line.source_location_id,
                    'source_production_line': finished_line.source_production_line_id,
                    'finished_transfer_line': finished_line,
                })
            else:
                carryover_payloads.append({
                    'source_stage': finished_line.source_stage,
                    'source_location': finished_line.source_location_id,
                    'source_origin': finished_line.source_origin,
                    'source_production': finished_line.source_production_id,
                    'source_production_line': finished_line.source_production_line_id,
                    'product': finished_line.product_id,
                    'uom': finished_line.product_uom_id,
                    'qty': finished_line.qty,
                    'line_ids': finished_line.carryover_line_ids,
                    'legacy': not bool(finished_line.carryover_line_ids),
                })

        if current_specs:
            self.production_id._move_finished_product(specs=current_specs)
            selected_transfer_lines.filtered(
                lambda line: line.source_kind == 'current'
            ).write({'state': 'transferred'})

        if carryover_payloads:
            self.production_id._move_finished_carryover_products(payloads=carryover_payloads)
            selected_transfer_lines.filtered(
                lambda line: line.source_kind == 'carryover'
            ).write({'state': 'transferred'})

        self.production_id._sync_finished_transfer_lines()
        self.production_id._clear_finished_piece_images(completed_source_lines)
        self.production_id._consolidate_equivalent_production_lines()
        return {
            'type': 'ir.actions.act_window',
            'name': _('أمر التشغيل الأسبوعي'),
            'res_model': 'furniture.mrp.production',
            'res_id': self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class FurnitureMrpFinishedTransferWizardLine(models.TransientModel):
    _name = 'furniture.mrp.finished.transfer.wizard.line'
    _description = 'سطر تحويل المنتج الجاهز للمخزن التام'

    wizard_id = fields.Many2one(
        'furniture.mrp.finished.transfer.wizard',
        string='الويزارد',
        required=True,
        ondelete='cascade',
    )
    selected = fields.Boolean(string='اختيار', default=True)
    transfer_line_id = fields.Many2one(
        'furniture.mrp.finished.transfer.line',
        string='سطر التحويل',
        required=True,
        ondelete='cascade',
    )
    transfer_line_ids = fields.Many2many(
        'furniture.mrp.finished.transfer.line',
        'furniture_finished_transfer_wizard_line_rel',
        'wizard_line_id',
        'transfer_line_id',
        string='توزيعات التحويل الفنية',
        readonly=True,
        copy=False,
    )
    source_kind = fields.Selection(related='transfer_line_id.source_kind', readonly=True)
    source_stage = fields.Selection(related='transfer_line_id.source_stage', readonly=True)
    source_origin = fields.Char(related='transfer_line_id.source_origin', readonly=True)
    source_location_id = fields.Many2one(related='transfer_line_id.source_location_id', readonly=True)
    source_production_line_id = fields.Many2one(related='transfer_line_id.source_production_line_id', readonly=True)
    product_id = fields.Many2one(related='transfer_line_id.product_id', readonly=True)
    dimension_label = fields.Char(related='transfer_line_id.dimension_label', readonly=True)
    product_uom_id = fields.Many2one(related='transfer_line_id.product_uom_id', readonly=True)
    qty = fields.Float(compute='_compute_qty', readonly=True, digits=(16, 3))
    state = fields.Selection(related='transfer_line_id.state', readonly=True)

    @api.depends('transfer_line_id.qty', 'transfer_line_ids.qty')
    def _compute_qty(self):
        for rec in self:
            transfer_lines = rec.transfer_line_ids or rec.transfer_line_id
            rec.qty = sum(transfer_lines.mapped('qty'))


class FurnitureMrpStageTransferWizard(models.TransientModel):
    _name = 'furniture.mrp.stage.transfer.wizard'
    _description = 'تحويل خامات أمر التشغيل الأسبوعي بين المراحل'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        readonly=True,
    )
    view_only = fields.Boolean(
        string='عرض محتويات المرحلة فقط',
        default=False,
        readonly=True,
        copy=False,
    )
    has_editable_first_stage_images = fields.Boolean(
        string='يمكن إضافة صور منتجات أول مرحلة',
        compute='_compute_has_editable_first_stage_images',
        readonly=True,
    )
    kit_grouping_active = fields.Boolean(
        string='عرض تقسيمة الأطقم المحفوظة',
        compute='_compute_kit_grouping_active',
        readonly=True,
        copy=False,
    )
    source_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='من مرحلة',
        required=True,
    )
    target_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='إلى مرحلة',
        required=True,
    )
    available_move_ids = fields.Many2many(
        'stock.move',
        string='محتويات المرحلة المصدر',
        copy=False,
    )
    line_ids = fields.One2many(
        'furniture.mrp.stage.transfer.wizard.line',
        'wizard_id',
        string='عناصر التحويل',
        copy=False,
    )
    source_content_summary = fields.Text(
        string='محتويات المرحلة المصدر',
        readonly=True,
    )
    prepared_source_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='مرحلة السطور المحضّرة',
        readonly=True,
        copy=False,
    )

    def _format_stage_transfer_label(self, product):
        self.ensure_one()
        if not product:
            return ''
        if self.production_id and self.production_id.production_line_ids:
            return product.display_name
        if self.production_id and product in self.production_id._get_stage_stock_product_candidates():
            return self.production_id._get_stage_product_name()
        return product.display_name

    def _uses_kit_grouping(self):
        self.ensure_one()
        special_stages = ('tailoring', 'sewing', 'upholstery')
        touches_special_stage = (
            self.source_stage in special_stages
            or self.target_stage in special_stages
        )
        # The worker viewer keeps its established Kit/image layout.  An
        # operational transfer may use that layout only for an explicitly
        # saved plan; otherwise it must stay compact and must never invent or
        # materialize a greedy Kit split while stock is being transferred.
        return bool(
            touches_special_stage
            and (
                self.view_only
                or (
                    self.production_id
                    and self.production_id.kit_plan_locked
                )
            )
        )

    @api.depends(
        'view_only',
        'source_stage',
        'target_stage',
        'production_id.kit_plan_locked',
    )
    def _compute_kit_grouping_active(self):
        for rec in self:
            rec.kit_grouping_active = rec._uses_kit_grouping()

    def _view_stage_content_payloads(self):
        """Return pieces assigned to this order in the current stage.

        Work locations are shared between weekly production orders.  The
        viewer must therefore keep current-order pieces and explicitly
        registered carry-over pieces only.  A first selected stage has no
        semi-finished-product quant before quality acceptance, so its planned
        and active production lines are added as virtual display payloads.
        """
        self.ensure_one()
        production = self.production_id
        stage_code = self.source_stage
        if not production or not stage_code:
            return []

        physical_payloads = production._get_stage_work_location_payloads(stage_code)
        payloads = []
        physical_remaining = {}
        for physical in physical_payloads:
            product = physical.get('product')
            source_line = physical.get('source_production_line')
            source_production = physical.get('source_production') or (
                source_line.production_id if source_line else False
            )
            key = (product.id if product else False, source_line.id if source_line else False)
            physical_remaining[key] = physical_remaining.get(key, 0.0) + (
                physical.get('qty') or 0.0
            )
            if source_production == production:
                payloads.append(dict(physical))
                physical_remaining[key] = 0.0

        # Old work becomes part of this weekly order only after the operator
        # explicitly selects it when starting the stage.
        for carryover in production._get_registered_stage_carryover_payloads(stage_code):
            product = carryover.get('product')
            source_line = carryover.get('source_production_line')
            key = (product.id if product else False, source_line.id if source_line else False)
            available_qty = physical_remaining.get(key, 0.0)
            if float_compare(available_qty, 0.0, precision_digits=3) <= 0:
                continue
            display_qty = min(carryover.get('qty') or 0.0, available_qty)
            if float_compare(display_qty, 0.0, precision_digits=3) <= 0:
                continue
            payload = dict(carryover)
            payload.update({
                'qty': display_qty,
                'source_production': (
                    source_line.production_id
                    if source_line else carryover.get('source_production')
                ),
            })
            payloads.append(payload)
            physical_remaining[key] = available_qty - display_qty

        # Painting or tailoring can be the very first selected stage.  In
        # that flow only its raw materials are moved to the hall; the finished
        # SKU does not exist as a quant until quality accepts it.  Display the
        # production-line batch itself, without ever treating a material quant
        # as a product and without creating a dimension/model product variant.
        stage_order = production._stage_order_record(stage_code)
        material_only_stage = production._is_material_only_stage(stage_code)
        completed_lines = self.env['furniture.mrp.production.line']
        pending_lines = self.env['furniture.mrp.production.line']
        requested_qty_by_line = None
        if not stage_order or stage_order.state == 'pending':
            pending_lines = (
                production._get_material_only_stage_start_line_candidates(
                    stage_code,
                    stage_order=stage_order,
                )
                if material_only_stage else
                production._get_first_stage_start_line_candidates(stage_code)
            )
            if stage_order:
                request_kinds = (
                    ('direct', 'first_stage')
                    if material_only_stage else
                    ('first_stage',)
                )
                request = self.env['furniture.mrp.store.request'].sudo().search([
                    ('production_id', '=', production.id),
                    ('stage_code', '=', stage_code),
                    ('stage_order_model', '=', stage_order._name),
                    ('stage_order_res_id', '=', stage_order.id),
                    ('request_kind', 'in', request_kinds),
                    ('state', 'in', ('pending', 'approved')),
                ], order='requested_at desc, id desc', limit=1)
                if request and request.request_kind == 'direct':
                    requested_line_ids = {
                        int(line_id)
                        for line_id in (
                            (request.payload_json or {}).get(
                                'production_line_ids',
                                [],
                            )
                        )
                        if str(line_id).isdigit()
                    }
                    if requested_line_ids:
                        pending_lines = pending_lines.filtered(
                            lambda line: line.id in requested_line_ids
                        )
                elif request:
                    requested_qty_by_line = {}
                    for line_values in (request.payload_json or {}).get('lines', []):
                        line_id = line_values.get('production_line_id')
                        qty_to_start = line_values.get('qty_to_start') or 0.0
                        if (
                            line_values.get('selected', True)
                            and line_id
                            and float_compare(qty_to_start, 0.0, precision_digits=3) > 0
                        ):
                            requested_qty_by_line[int(line_id)] = qty_to_start
                    pending_lines = pending_lines.filtered(
                        lambda line: line.id in requested_qty_by_line
                    )
        virtual_lines = pending_lines
        if stage_order:
            completed_lines = stage_order._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            )
            virtual_lines |= stage_order.first_stage_production_line_ids
            virtual_lines |= stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            )
            virtual_lines |= stage_order._get_stage_line_ids_data(
                'quality_production_line_ids_data'
            )
        represented_line_ids = {
            payload.get('source_production_line').id
            for payload in payloads
            if payload.get('source_production_line')
        }
        virtual_lines = (virtual_lines - completed_lines).filtered(lambda line: (
            line.id not in represented_line_ids
            and line.product_id
            and float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) > 0
            and stage_code in line._selected_stage_codes()
        ))
        for line in virtual_lines.sorted(production._production_line_sort_key):
            line_stage_codes = line._selected_stage_codes()
            first_material_stage = next((
                code
                for code in line_stage_codes
                if production._is_material_only_stage(code)
            ), False)
            display_product = production._find_dimensioned_product_for_line(line) or line.product_id
            display_qty = (
                min(requested_qty_by_line[line.id], line.product_qty)
                if requested_qty_by_line is not None and line.id in requested_qty_by_line
                else line.product_qty
            )
            payloads.append({
                'product': display_product,
                'qty': display_qty,
                'uom': display_product.uom_id or line.product_uom_id,
                'label': production._get_production_line_text_label(line),
                'source_origin': production.name,
                'source_production': production,
                'source_production_line': line,
                'virtual_first_stage': bool(
                    line_stage_codes
                    and (
                        production._production_line_start_stage_code(line) == stage_code
                        or (
                            material_only_stage
                            and first_material_stage == stage_code
                        )
                    )
                ),
            })

        return sorted(payloads, key=lambda payload: (
            payload.get('label') or (
                payload['product'].display_name if payload.get('product') else ''
            ),
            payload['product'].id if payload.get('product') else 0,
            payload.get('source_production_line').id
            if payload.get('source_production_line') else 0,
        ))

    def _stage_transfer_source_payloads(self):
        self.ensure_one()
        if self.view_only:
            return self._view_stage_content_payloads()
        if not self.production_id or not self.source_stage:
            return []
        return self.production_id._stage_storage_payloads_for_transfer(self.source_stage)

    @api.depends('view_only', 'source_stage', 'line_ids.first_stage_image_editable')
    def _compute_has_editable_first_stage_images(self):
        for rec in self:
            rec.has_editable_first_stage_images = bool(
                rec.view_only
                and rec.line_ids.filtered('first_stage_image_editable')
            )

    def _editable_first_stage_image_source_line_ids(self):
        """Return server-verified source lines whose product starts here.

        The browser flag is presentation only.  Rebuilding the payload list
        ensures an edited transient row cannot grant image-write access to a
        physical product transferred from an earlier stage.
        """
        self.ensure_one()
        if not self.view_only or self.source_stage not in ('tailoring', 'sewing', 'upholstery'):
            return set()
        return {
            payload['source_production_line'].id
            for payload in self._view_stage_content_payloads()
            if (
                payload.get('virtual_first_stage')
                and payload.get('source_production_line')
                and payload['source_production_line'].production_id == self.production_id
            )
        }

    def action_save_first_stage_images(self):
        """Persist images entered for products whose first stage is this hall."""
        self.ensure_one()
        # The stage viewer is read-only for supervisors.  Keep this legacy RPC
        # protected as well so an old/bookmarked transient cannot bypass the
        # compact viewer's manager-only setup contract.
        self.production_id._check_tailoring_material_setup_edit_access()
        stage_order = self.production_id._stage_order_record(self.source_stage)
        if not stage_order:
            raise UserError(_('أمر المرحلة لم يعد موجودًا. افتح الشاشة من جديد.'))
        stage_order._check_stage_operation_access()
        allowed_line_ids = self._editable_first_stage_image_source_line_ids()
        candidate_rows = self.line_ids.filtered(lambda row: (
            not row.is_model_header
            and row.first_stage_image_editable
            and row.source_production_line_id
        ))
        invalid_rows = candidate_rows.filtered(
            lambda row: row.source_production_line_id.id not in allowed_line_ids
        )
        if invalid_rows:
            raise UserError(_(
                'حالة المرحلة اتغيرت أثناء فتح الشاشة. اقفلها وافتحها من جديد قبل حفظ الصور.'
            ))
        if not candidate_rows:
            raise UserError(_(
                'إضافة الصور من هنا متاحة فقط للمنتجات التي تبدأ بهذه المرحلة مباشرة.'
            ))

        rows_by_source = {}
        for row in candidate_rows:
            rows_by_source.setdefault(
                row.source_production_line_id.id,
                self.env[row._name],
            )
            rows_by_source[row.source_production_line_id.id] |= row

        saved_count = 0
        for source_line_id, source_rows in rows_by_source.items():
            source_line = self.env['furniture.mrp.production.line'].browse(
                source_line_id
            ).exists()
            if not source_line or source_line.production_id != self.production_id:
                raise UserError(_(
                    'تعذر العثور على سطر أمر التصنيع المرتبط بالصورة. افتح الشاشة من جديد.'
                ))

            existing_image = source_line.batch_image_1920
            changed_images = []
            for row in source_rows:
                row_image = row.batch_image_1920
                if row_image == existing_image:
                    continue
                if not any(row_image == image for image in changed_images):
                    changed_images.append(row_image)
            if len(changed_images) > 1:
                raise UserError(_(
                    'السطر %s يمثل دفعة واحدة؛ اختار صورة واحدة فقط لنفس الدفعة.'
                ) % source_line.product_id.display_name)

            piece_image = changed_images[0] if changed_images else existing_image
            image_token = source_line.batch_image_token
            if piece_image != existing_image:
                image_token = uuid.uuid4().hex if piece_image else False
            elif piece_image and not image_token:
                image_token = uuid.uuid4().hex

            if piece_image != existing_image or image_token != source_line.batch_image_token:
                source_line.sudo().with_context(
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_line_consolidation=True,
                ).write({
                    'batch_image_1920': piece_image,
                    'batch_image_token': image_token,
                })
                saved_count += 1

        if not saved_count:
            raise UserError(_('اختار صورة جديدة أو احذف صورة موجودة قبل الحفظ.'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم حفظ الصور'),
                'message': _(
                    'تم حفظ صور %s من دفعات أمر التصنيع بدون تنفيذ أي حركة مخزون.'
                ) % saved_count,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    @api.model
    def _stage_transfer_product_key(self, product):
        if not product:
            return False
        return (product.furniture_dimension_source_product_id or product).id

    def _stage_transfer_payload_metadata(self, payload):
        self.ensure_one()
        source_line = payload.get('source_production_line')
        source_production = payload.get('source_production')
        model = (
            source_line.furniture_order_model_id
            if source_line and source_line.furniture_order_model_id
            else source_production.furniture_order_model_id
            if source_production and source_production.furniture_order_model_id
            else payload['product'].furniture_model_id
        )
        return {
            'model': model,
            'buyer': source_line.buyer_partner_id if source_line else self.env['res.partner'],
            'beneficiary': source_line.beneficiary_partner_id if source_line else self.env['res.partner'],
        }

    def _prepare_standard_grouped_transfer_lines(self, visible_payloads):
        """Collapse technical Kit pieces into their commercial display rows.

        Tailoring/upholstery deliberately use the separate Kit/image layout.
        Every other administrative transfer keeps the exact technical source
        lines in a hidden M2M while showing one quantity row per saved family.
        """
        self.ensure_one()
        groups = {}
        ungrouped_index = 0
        for payload in visible_payloads:
            product = payload.get('product')
            if not product:
                continue
            uom = payload.get('uom') or product.uom_id
            source_line = payload.get('source_production_line')
            source_production = payload.get('source_production') or (
                source_line.production_id if source_line else False
            )
            if source_line and source_production:
                family_root = source_production._production_line_display_family_origin(
                    source_line
                ) or source_line
                key = (
                    'family',
                    source_production.id,
                    family_root.id,
                    product.id,
                    uom.id if uom else False,
                )
            else:
                # A legacy physical balance without a production-line identity
                # must stay isolated; merging it would lose its audit origin.
                ungrouped_index += 1
                family_root = self.env['furniture.mrp.production.line']
                key = ('legacy', ungrouped_index)
            group = groups.setdefault(key, {
                'product': product,
                'uom': uom,
                'source_production': source_production,
                'family_root': family_root,
                'representative': self.env['furniture.mrp.production.line'],
                'technical_lines': self.env['furniture.mrp.production.line'],
                'qty': 0.0,
            })
            group['qty'] += payload.get('qty') or 0.0
            if source_line:
                group['technical_lines'] |= source_line
                if not group['representative'] or source_line == family_root:
                    group['representative'] = source_line

        commands = []
        summary_lines = []
        sequence = 10
        for group in groups.values():
            product = group['product']
            uom = group['uom'] or product.uom_id
            representative = group['representative']
            commands.append((0, 0, {
                'sequence': sequence,
                'product_id': product.id,
                'product_uom_id': uom.id,
                'selected': not self.view_only,
                'qty_to_transfer': group['qty'],
                'source_production_id': (
                    group['source_production'].id
                    if group['source_production'] else False
                ),
                'source_production_line_id': representative.id or False,
                'technical_source_production_line_ids': [
                    (6, 0, group['technical_lines'].ids),
                ],
                'buyer_partner_id': (
                    representative.buyer_partner_id.id if representative else False
                ),
                'beneficiary_partner_id': (
                    representative.beneficiary_partner_id.id if representative else False
                ),
                'kit_piece_note': (
                    representative.kit_piece_note if representative else False
                ),
            }))
            sequence += 10
            product_label = self._format_stage_transfer_label(product)
            if product.furniture_dimension_label:
                product_label = '%s [%s]' % (
                    product_label,
                    product.furniture_dimension_label,
                )
            summary_lines.append('%s | %s %s' % (
                product_label,
                group['qty'],
                uom.name if uom else '',
            ))
        return commands, summary_lines

    def _kit_component_requirements(self, kit_bom):
        """Return normalized component quantities for one physical Kit."""
        self.ensure_one()
        output_qty = kit_bom.product_qty or 1.0
        requirements = {}
        for bom_line in kit_bom.bom_line_ids.filtered(lambda line: line.product_id and line.product_qty > 0):
            product = bom_line.product_id
            uom = bom_line.product_uom_id or product.uom_id
            qty = uom._compute_quantity(bom_line.product_qty, product.uom_id) / output_qty
            if float_compare(qty, 0.0, precision_digits=6) <= 0:
                continue
            product_key = self._stage_transfer_product_key(product)
            bucket = requirements.setdefault(product_key, {
                'product': product,
                'qty': 0.0,
            })
            bucket['qty'] += qty
        return requirements

    def _kit_group_header_label(self, kit_bom, instance_number, furniture_model, loose=False):
        self.ensure_one()
        model_label = furniture_model.name if furniture_model else _('بدون موديل')
        if loose:
            return _('منتجات منفردة — %s') % model_label
        kit_product = kit_bom.furniture_product_id or kit_bom.product_id
        kit_label = (
            kit_product.display_name
            if kit_product else kit_bom.product_tmpl_id.display_name or _('Kit')
        )
        return _('%s %s') % (kit_label, instance_number)

    def _prepare_kit_grouped_transfer_lines(self, visible_payloads):
        """Split visible WIP quantities into complete phantom-Kit instances.

        Nothing stock-related is created here.  Each component remains its real
        finished SKU; this method only prepares a stable visual/allocation plan
        which is materialized on the production lines when transfer is executed.
        """
        self.ensure_one()
        items = []
        for payload in visible_payloads:
            metadata = self._stage_transfer_payload_metadata(payload)
            items.append({
                'payload': payload,
                'remaining_qty': payload.get('qty') or 0.0,
                'product_key': self._stage_transfer_product_key(payload.get('product')),
                **metadata,
            })

        groups = []
        unassigned_items = []
        existing_groups = {}
        for item in items:
            source_line = item['payload'].get('source_production_line')
            if source_line and source_line.kit_bom_id and source_line.kit_instance_number:
                token = 'kit:%s:%s:%s' % (
                    source_line.production_id.id,
                    source_line.kit_bom_id.id,
                    source_line.kit_instance_number,
                )
                group = existing_groups.setdefault(token, {
                    'token': token,
                    'kit_bom': source_line.kit_bom_id,
                    'instance_number': source_line.kit_instance_number,
                    'model': item['model'],
                    'buyer': item['buyer'],
                    'beneficiary': item['beneficiary'],
                    'allocations': [],
                    'loose': False,
                })
                group['allocations'].append((item, item['remaining_qty']))
                item['remaining_qty'] = 0.0
            else:
                unassigned_items.append(item)
        groups.extend(existing_groups.values())

        kit_domain = [
            ('type', '=', 'phantom'),
            ('bom_line_ids', '!=', False),
        ]
        if self.production_id.company_id:
            kit_domain += [
                '|',
                ('company_id', '=', False),
                ('company_id', '=', self.production_id.company_id.id),
            ]
        kit_boms = self.env['mrp.bom'].search(kit_domain, order='id asc')
        requirements_by_bom = {
            bom.id: self._kit_component_requirements(bom)
            for bom in kit_boms
        }
        requirements_by_bom = {
            bom_id: requirements
            for bom_id, requirements in requirements_by_bom.items()
            if requirements
        }

        buckets = {}
        for item in unassigned_items:
            key = (
                item['model'].id,
                item['buyer'].id,
                item['beneficiary'].id,
            )
            buckets.setdefault(key, []).append(item)

        existing_numbers = {}
        existing_kit_lines = self.env['furniture.mrp.production.line'].with_context(
            active_test=False,
        ).search([
            ('production_id', '=', self.production_id.id),
            ('kit_bom_id', '!=', False),
            ('kit_instance_number', '>', 0),
        ])
        for line in existing_kit_lines:
            existing_numbers[line.kit_bom_id.id] = max(
                existing_numbers.get(line.kit_bom_id.id, 0),
                line.kit_instance_number,
            )

        def _available_qty(bucket_items, product_key):
            return sum(
                item['remaining_qty']
                for item in bucket_items
                if item['product_key'] == product_key
            )

        def _allocate_qty(bucket_items, product_key, qty):
            allocations = []
            qty_left = qty
            for item in bucket_items:
                if item['product_key'] != product_key:
                    continue
                take_qty = min(item['remaining_qty'], qty_left)
                if float_compare(take_qty, 0.0, precision_digits=6) <= 0:
                    continue
                allocations.append((item, take_qty))
                item['remaining_qty'] -= take_qty
                qty_left -= take_qty
                if float_compare(qty_left, 0.0, precision_digits=6) <= 0:
                    break
            return allocations

        sorted_buckets = sorted(
            buckets.values(),
            key=lambda bucket_items: (
                (bucket_items[0]['model'].name or _('بدون موديل')).casefold(),
                (bucket_items[0]['buyer'].display_name or '').casefold(),
                (bucket_items[0]['beneficiary'].display_name or '').casefold(),
            ),
        )
        loose_groups_by_model = {}
        for bucket_items in sorted_buckets:
            model = bucket_items[0]['model']
            buyer = bucket_items[0]['buyer']
            beneficiary = bucket_items[0]['beneficiary']
            available_keys = {
                item['product_key']
                for item in bucket_items
                if float_compare(item['remaining_qty'], 0.0, precision_digits=6) > 0
            }
            candidate_boms = kit_boms.filtered(lambda bom: (
                bom.id in requirements_by_bom
                and model
                and bom.furniture_model_id == model
                and set(requirements_by_bom[bom.id]).issubset(available_keys)
            )).sorted(lambda bom: (
                -len(requirements_by_bom[bom.id]),
                (
                    (bom.furniture_product_id or bom.product_id).display_name
                    if (bom.furniture_product_id or bom.product_id)
                    else bom.product_tmpl_id.display_name
                ).casefold(),
                bom.id,
            ))
            # An explicitly saved production plan is authoritative.  Its
            # unassigned remainder must stay loose instead of being greedily
            # consumed by another overlapping Kit recipe.
            if self.production_id.kit_plan_locked:
                candidate_boms = self.env['mrp.bom']
            for kit_bom in candidate_boms:
                requirements = requirements_by_bom[kit_bom.id]
                possible_counts = [
                    _available_qty(bucket_items, product_key) / data['qty']
                    for product_key, data in requirements.items()
                    if data['qty'] > 0
                ]
                complete_count = int(math.floor(min(possible_counts) + 1e-7)) if possible_counts else 0
                for _kit_index in range(complete_count):
                    existing_numbers[kit_bom.id] = existing_numbers.get(kit_bom.id, 0) + 1
                    instance_number = existing_numbers[kit_bom.id]
                    allocations = []
                    for product_key, data in requirements.items():
                        allocations.extend(_allocate_qty(bucket_items, product_key, data['qty']))
                    groups.append({
                        'token': 'kit:%s:%s' % (kit_bom.id, instance_number),
                        'kit_bom': kit_bom,
                        'instance_number': instance_number,
                        'model': model,
                        'buyer': buyer,
                        'beneficiary': beneficiary,
                        'allocations': allocations,
                        'loose': False,
                    })

            remaining = [
                item for item in bucket_items
                if float_compare(item['remaining_qty'], 0.0, precision_digits=6) > 0
            ]
            if remaining:
                # Loose products are not a physical Kit, so group them visually
                # by model only.  Buyer/beneficiary still belong to each piece
                # and are populated from its own item below.
                model_key = model.id or 0
                loose_group = loose_groups_by_model.setdefault(model_key, {
                    'token': 'loose:%s' % model_key,
                    'kit_bom': self.env['mrp.bom'],
                    'instance_number': 0,
                    'model': model,
                    'buyer': self.env['res.partner'],
                    'beneficiary': self.env['res.partner'],
                    'allocations': [],
                    'loose': True,
                })
                loose_group['allocations'].extend(
                    (item, item['remaining_qty'])
                    for item in remaining
                )

        groups.extend(loose_groups_by_model.values())

        groups.sort(key=lambda group: (
            group['loose'],
            (
                (group['kit_bom'].furniture_product_id or group['kit_bom'].product_id).display_name
                if group['kit_bom'] else _('منتجات منفردة')
            ).casefold(),
            group['instance_number'],
            (group['model'].name or _('بدون موديل')).casefold(),
        ))

        line_commands = []
        summary_lines = []
        sequence = 10
        for group in groups:
            line_commands.append((0, 0, {
                'sequence': sequence,
                'is_model_header': True,
                'selected': False,
                'model_group_label': self._kit_group_header_label(
                    group['kit_bom'],
                    group['instance_number'],
                    group['model'],
                    loose=group['loose'],
                ),
                'kit_group_token': group['token'],
                'kit_bom_id': group['kit_bom'].id if group['kit_bom'] else False,
                'kit_instance_number': group['instance_number'],
                'buyer_partner_id': group['buyer'].id,
                'beneficiary_partner_id': group['beneficiary'].id,
            }))
            sequence += 10
            for item, allocated_qty in group['allocations']:
                payload = item['payload']
                product = payload['product']
                move_uom = payload.get('uom') or product.uom_id
                source_production = payload.get('source_production')
                source_line = payload.get('source_production_line')
                row_buyer = item['buyer'] if group['loose'] else group['buyer']
                row_beneficiary = (
                    item['beneficiary'] if group['loose'] else group['beneficiary']
                )
                line_commands.append((0, 0, {
                    'sequence': sequence,
                    'product_id': product.id,
                    'product_uom_id': move_uom.id,
                    'selected': not self.view_only,
                    'qty_to_transfer': allocated_qty,
                    'kit_component_qty': allocated_qty,
                    'kit_group_token': group['token'],
                    'kit_bom_id': group['kit_bom'].id if group['kit_bom'] else False,
                    'kit_instance_number': group['instance_number'],
                    'buyer_partner_id': row_buyer.id,
                    'beneficiary_partner_id': row_beneficiary.id,
                    # This is intentionally never populated from product.image_1920.
                    # It belongs to this exact production piece, not its master item.
                    'batch_image_1920': (
                        source_line.batch_image_1920
                        if source_line and source_line.batch_image_1920
                        else False
                    ),
                    'kit_piece_note': (
                        source_line.kit_piece_note if source_line else False
                    ),
                    'first_stage_image_editable': bool(
                        self.view_only and payload.get('virtual_first_stage')
                    ),
                    'source_production_id': source_production.id if source_production else False,
                    'source_production_line_id': source_line.id if source_line else False,
                }))
                sequence += 10
                product_label = self._format_stage_transfer_label(product)
                if product.furniture_dimension_label:
                    product_label = '%s [%s]' % (product_label, product.furniture_dimension_label)
                summary_lines.append('%s | %s %s' % (
                    product_label,
                    allocated_qty,
                    move_uom.name if move_uom else '',
                ))
        return line_commands, summary_lines

    def _prepare_stage_transfer_content_values(self):
        self.ensure_one()
        values = {
            'available_move_ids': [(5, 0, 0)],
            'line_ids': [(5, 0, 0)],
            'source_content_summary': False,
        }
        if not self.production_id or not self.source_stage:
            return values

        payloads = self._stage_transfer_source_payloads()
        moves = (
            self.env['stock.move']
            if self.view_only
            else self.production_id._get_stage_storage_content_moves(
                self.source_stage,
                payloads=payloads,
            )
        )
        line_commands = []
        summary_lines = []
        visible_payloads = []
        for payload in payloads:
            if (
                not self.view_only
                and self.target_stage
                and not self.production_id._stage_payload_can_transfer_to_stage(
                    self.source_stage,
                    self.target_stage,
                    payload,
                )
            ):
                continue
            visible_payloads.append(payload)

        if self._uses_kit_grouping():
            line_commands, summary_lines = self._prepare_kit_grouped_transfer_lines(visible_payloads)
        else:
            line_commands, summary_lines = self._prepare_standard_grouped_transfer_lines(
                visible_payloads
            )
        values.update({
            'available_move_ids': [(6, 0, moves.ids)],
            'line_ids': [(5, 0, 0)] + line_commands,
            'source_content_summary': '\n'.join(summary_lines) or False,
        })
        return values

    @api.model
    def _stage_transfer_line_state_key(self, line=False, values=False):
        """Return a stable key for retaining an edited transient row.

        The target-stage onchange rebuilds the visible rows to remove products
        whose route does not include that target.  Keep the operator's checkbox
        and quantity edits for rows that remain visible instead of silently
        restoring the default ``selected=True`` value.
        """
        values = values or {}
        if line:
            return (
                bool(line.is_model_header),
                line.kit_group_token or '',
                line.product_id.id,
                line.source_production_id.id,
                line.source_production_line_id.id,
                line.kit_bom_id.id,
                line.kit_instance_number or 0,
            )
        return (
            bool(values.get('is_model_header')),
            values.get('kit_group_token') or '',
            values.get('product_id') or False,
            values.get('source_production_id') or False,
            values.get('source_production_line_id') or False,
            values.get('kit_bom_id') or False,
            values.get('kit_instance_number') or 0,
        )

    def _stage_transfer_line_states(self):
        self.ensure_one()
        states = {}
        for line in self.line_ids:
            key = self._stage_transfer_line_state_key(line=line)
            states.setdefault(key, []).append({
                'selected': line.selected,
                'qty_to_transfer': line.qty_to_transfer,
                'buyer_partner_id': line.buyer_partner_id.id,
                'beneficiary_partner_id': line.beneficiary_partner_id.id,
                'batch_image_1920': line.batch_image_1920,
                'kit_piece_note': line.kit_piece_note,
            })
        return states

    def _restore_stage_transfer_line_states(self, values, states):
        self.ensure_one()
        if not states:
            return values
        for command in values.get('line_ids', []):
            if not command or command[0] != 0 or len(command) < 3:
                continue
            line_values = command[2]
            key = self._stage_transfer_line_state_key(values=line_values)
            matching_states = states.get(key)
            if not matching_states:
                continue
            previous = matching_states.pop(0)
            line_values.update(previous)
        return values

    @api.onchange('production_id', 'source_stage', 'target_stage')
    def _onchange_source_stage(self):
        for rec in self:
            # Preserve edits only while filtering the same source stage.  A real
            # source-stage change must prepare a fresh set of rows.
            states = (
                rec._stage_transfer_line_states()
                if rec.prepared_source_stage == rec.source_stage
                else {}
            )
            values = rec._prepare_stage_transfer_content_values()
            rec._restore_stage_transfer_line_states(values, states)
            rec.available_move_ids = values['available_move_ids']
            rec.line_ids = values['line_ids']
            rec.source_content_summary = values['source_content_summary']
            rec.prepared_source_stage = rec.source_stage

    def _restore_missing_line_sources(self):
        """Keep each edited quantity tied to the stock payload it came from."""
        for rec in self:
            payloads = rec._stage_transfer_source_payloads()
            if rec.target_stage and not rec.view_only:
                payloads = [
                    payload for payload in payloads
                    if rec.production_id._stage_payload_can_transfer_to_stage(
                        rec.source_stage,
                        rec.target_stage,
                        payload,
                    )
                ]
            used_payload_keys = set()
            for line in rec.line_ids:
                if line.source_production_line_id:
                    if not line.source_production_id:
                        line.source_production_id = line.source_production_line_id.production_id
                    used_payload_keys.add((
                        line.product_id.id,
                        line.source_production_line_id.id,
                    ))
                    continue
                candidates = []
                for payload in payloads:
                    product = payload.get('product')
                    source_line = payload.get('source_production_line')
                    payload_uom = payload.get('uom') or (product.uom_id if product else False)
                    payload_key = (product.id if product else False, source_line.id if source_line else False)
                    if (
                        product == line.product_id
                        and (not line.product_uom_id or payload_uom == line.product_uom_id)
                        and payload_key not in used_payload_keys
                    ):
                        candidates.append((payload_key, payload))
                if not candidates:
                    continue
                payload_key, payload = candidates[0]
                source_line = payload.get('source_production_line')
                source_production = payload.get('source_production') or (
                    source_line.production_id if source_line else False
                )
                line.write({
                    'source_production_id': source_production.id if source_production else False,
                    'source_production_line_id': source_line.id if source_line else False,
                })
                used_payload_keys.add(payload_key)

    @api.model_create_multi
    def create(self, vals_list):
        has_explicit_lines = ['line_ids' in vals for vals in vals_list]
        records = super().create(vals_list)
        for rec, preserve_lines in zip(records, has_explicit_lines):
            # The web client sends edited one2many rows while creating this
            # transient wizard. Never rebuild missing rows as selected: if the
            # browser did not submit them, action_transfer must fail closed.
            if rec.production_id and rec.source_stage and preserve_lines:
                rec._restore_missing_line_sources()
        return records

    def _propagate_transfer_group_partners(self):
        """Apply the customer selected on a Kit header to all its components."""
        self.ensure_one()
        headers = {
            line.kit_group_token: line
            for line in self.line_ids
            if line.is_model_header and line.kit_group_token
        }
        for line in self.line_ids.filtered(lambda item: not item.is_model_header):
            header = headers.get(line.kit_group_token)
            # Only a real Kit header owns customer data for its components.
            # A loose header can contain different customers under one model.
            if header and header.kit_bom_id:
                line.write({
                    'buyer_partner_id': header.buyer_partner_id.id,
                    'beneficiary_partner_id': header.beneficiary_partner_id.id,
                })

    def _split_transfer_source_line(self, source_line, qty_to_keep, product):
        self.ensure_one()
        production = source_line.production_id
        completed_stage_codes = production._production_line_completed_stage_codes(
            source_line,
            product=product,
        )
        remaining_line = source_line._split_for_partial_quantity(
            qty_to_keep,
            # Planning a Kit happens before production and must not turn a
            # visual piece split into cost inheritance.  A live stage
            # transfer, on the other hand, really does inherit progress.
            preserve_progress=(
                not self.kit_planner_mode
                or source_line.first_stage_started
            ),
        )
        production._copy_completed_stage_progress_to_split_line(
            source_line,
            remaining_line,
            completed_stage_codes,
        )
        return remaining_line

    def _materialize_kit_transfer_lines(self):
        """Persist one production-line batch for every displayed Kit component.

        The historical incoming stock move intentionally remains linked to the
        original line.  Rows that will stay in the source store are allocated
        first, so the existing stage-stock family resolver keeps the remaining
        physical quantity attached to the correct Kit after a partial transfer.
        """
        self.ensure_one()
        kit_rows = self.line_ids.filtered(lambda line: (
            not line.is_model_header
            and line.kit_bom_id
            and line.kit_instance_number
            and line.source_production_line_id
            and float_compare(line.kit_component_qty or 0.0, 0.0, precision_digits=3) > 0
        ))
        rows_by_source = {}
        for row in kit_rows:
            rows_by_source.setdefault(row.source_production_line_id.id, self.env[row._name])
            rows_by_source[row.source_production_line_id.id] |= row

        split_productions = self.env['furniture.mrp.production']

        def _retains_source_stock(row):
            return (
                not row.selected
                or float_compare(
                    row.qty_to_transfer or 0.0,
                    row.kit_component_qty or 0.0,
                    precision_digits=3,
                ) < 0
            )

        def _assign_row(row, production_line):
            vals = {
                'kit_bom_id': row.kit_bom_id.id,
                'kit_instance_number': row.kit_instance_number,
                'buyer_partner_id': row.buyer_partner_id.id,
                'beneficiary_partner_id': row.beneficiary_partner_id.id,
                'kit_piece_note': row.kit_piece_note or False,
            }
            if row.selected:
                piece_image = row.batch_image_1920
                existing_image = production_line.batch_image_1920
                image_token = production_line.batch_image_token
                if piece_image != existing_image:
                    image_token = uuid.uuid4().hex if piece_image else False
                elif piece_image and not image_token:
                    image_token = uuid.uuid4().hex
                vals.update({
                    'batch_image_1920': piece_image,
                    'batch_image_token': image_token,
                })
            production_line.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
                furniture_skip_line_consolidation=True,
            ).write(vals)
            row.write({
                'source_production_id': production_line.production_id.id,
                'source_production_line_id': production_line.id,
            })

        for source_line_id, source_rows in rows_by_source.items():
            source_line = self.env['furniture.mrp.production.line'].browse(source_line_id).exists()
            if not source_line:
                continue
            family_origin = source_line.kit_family_origin_line_id or source_line
            if source_line.kit_family_origin_line_id != family_origin:
                source_line.with_context(
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_line_consolidation=True,
                ).write({'kit_family_origin_line_id': family_origin.id})
            ordered_rows = source_rows.sorted(lambda row: (
                not _retains_source_stock(row),
                row.sequence,
                row.id,
            ))
            total_allocated = sum(ordered_rows.mapped('kit_component_qty'))
            comparison = float_compare(
                source_line.product_qty or 0.0,
                total_allocated,
                precision_digits=3,
            )
            if comparison < 0:
                raise UserError(_(
                    'تعذر تقسيم %s إلى الأطقم الظاهرة لأن كمية مكونات الأطقم أكبر من كمية سطر أمر الإنتاج.'
                ) % source_line.product_id.display_name)

            rows_left = ordered_rows
            pool_line = source_line
            if comparison > 0:
                residual_qty = source_line.product_qty - total_allocated
                pool_line = self._split_transfer_source_line(
                    source_line,
                    residual_qty,
                    ordered_rows[0].product_id,
                )
                split_productions |= source_line.production_id
            elif len(ordered_rows) > 1:
                keeper = ordered_rows[0]
                pool_line = self._split_transfer_source_line(
                    source_line,
                    keeper.kit_component_qty,
                    keeper.product_id,
                )
                _assign_row(keeper, source_line)
                rows_left = ordered_rows[1:]
                split_productions |= source_line.production_id

            for index, row in enumerate(rows_left):
                allocation_line = pool_line
                if index < len(rows_left) - 1:
                    pool_line = self._split_transfer_source_line(
                        allocation_line,
                        row.kit_component_qty,
                        row.product_id,
                    )
                    split_productions |= allocation_line.production_id
                _assign_row(row, allocation_line)

        for line in self.line_ids.filtered(lambda item: (
            not item.is_model_header and item.source_production_line_id
        )):
            # Loose products still keep the customer metadata even when they do
            # not form a complete Kit yet.
            if not line.kit_bom_id:
                vals = {
                    'buyer_partner_id': line.buyer_partner_id.id,
                    'beneficiary_partner_id': line.beneficiary_partner_id.id,
                    'kit_piece_note': line.kit_piece_note or False,
                }
                if line.selected:
                    piece_image = line.batch_image_1920
                    existing_image = line.source_production_line_id.batch_image_1920
                    image_token = line.source_production_line_id.batch_image_token
                    if piece_image != existing_image:
                        image_token = uuid.uuid4().hex if piece_image else False
                    elif piece_image and not image_token:
                        image_token = uuid.uuid4().hex
                    vals.update({
                        'batch_image_1920': piece_image,
                        'batch_image_token': image_token,
                    })
                line.source_production_line_id.with_context(
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_line_consolidation=True,
                ).write(vals)

        for production in split_productions:
            production._refresh_material_lines_for_stage_plan()

    def action_transfer(self):
        self.ensure_one()
        if self.view_only:
            raise UserError(_('شاشة محتويات المرحلة للعرض فقط ولا تنفذ أي تحويل.'))
        if not self.line_ids:
            raise UserError(_(
                'لم تصل اختيارات التحويل بشكل سليم. اقفل شاشة التحويل وافتحها من جديد؛ '
                'لم يتم نقل أي صنف.'
            ))
        self._propagate_transfer_group_partners()
        selected_lines = self.line_ids.filtered(
            lambda line: line.selected and line.product_id and line.qty_to_transfer and line.qty_to_transfer > 0
        )
        if not selected_lines:
            raise UserError(_('اختار عنصر واحد على الأقل من محتويات المرحلة المصدر.'))
        for line in selected_lines:
            if float_compare(line.qty_to_transfer, line.available_qty, precision_digits=3) > 0:
                raise UserError(_(
                    'الكمية المطلوبة من %s أكبر من الكمية المتاحة داخل المجموعة.'
                ) % line.product_id.display_name)

        if not self._uses_kit_grouping():
            for line in selected_lines:
                source_lines = (
                    line.technical_source_production_line_ids
                    or line.source_production_line_id
                )
                source_lines.with_context(
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_line_consolidation=True,
                ).write({
                    'buyer_partner_id': line.buyer_partner_id.id,
                    'beneficiary_partner_id': line.beneficiary_partner_id.id,
                })

        grouped_lines = {}
        current_payloads = self._stage_transfer_source_payloads()
        if self.target_stage and not self.view_only:
            current_payloads = [
                payload for payload in current_payloads
                if self.production_id._stage_payload_can_transfer_to_stage(
                    self.source_stage,
                    self.target_stage,
                    payload,
                )
            ]
        for line in selected_lines:
            technical_lines = line.technical_source_production_line_ids
            if technical_lines and not self._uses_kit_grouping():
                technical_line_ids = set(technical_lines._origin.ids)
                qty_left = line.qty_to_transfer
                matching_payloads = [
                    payload for payload in current_payloads
                    if (
                        payload.get('product') == line.product_id
                        and payload.get('source_production_line')
                        and payload['source_production_line'].id in technical_line_ids
                    )
                ]
                matching_payloads.sort(key=lambda payload: (
                    payload.get('source_production_line').sequence or 0,
                    payload.get('source_production_line').kit_instance_number or 0,
                    payload.get('source_production_line').id,
                ))
                for payload in matching_payloads:
                    if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                        break
                    payload_qty = payload.get('qty') or 0.0
                    allocated_qty = min(qty_left, payload_qty)
                    if float_compare(allocated_qty, 0.0, precision_digits=3) <= 0:
                        continue
                    source_line = payload.get('source_production_line')
                    key = (line.product_id.id, line.product_uom_id.id, source_line.id)
                    grouped_lines.setdefault(key, {
                        'product': line.product_id,
                        'uom': line.product_uom_id,
                        'qty': 0.0,
                        'source_production_line': source_line,
                    })
                    grouped_lines[key]['qty'] += allocated_qty
                    qty_left -= allocated_qty
                if float_compare(qty_left, 0.0, precision_digits=3) > 0:
                    raise UserError(_(
                        'الكمية المتاحة للصنف %s تغيرت. اقفل شاشة التحويل وافتحها من جديد.'
                    ) % line.product_id.display_name)
                continue
            key = (
                line.product_id.id,
                line.product_uom_id.id,
                line.source_production_line_id.id,
            )
            grouped_lines.setdefault(key, {
                'product': line.product_id,
                'uom': line.product_uom_id,
                'qty': 0.0,
                'source_production_line': line.source_production_line_id,
            })
            grouped_lines[key]['qty'] += line.qty_to_transfer
        self.production_id._manual_transfer_stage_materials_batch(
            self.source_stage,
            self.target_stage,
            grouped_lines.values(),
        )
        self.production_id._consolidate_equivalent_production_lines()
        return {
            'type': 'ir.actions.act_window',
            'name': _('أمر التشغيل الأسبوعي'),
            'res_model': 'furniture.mrp.production',
            'res_id': self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class FurnitureMrpStageTransferWizardLine(models.TransientModel):
    _name = 'furniture.mrp.stage.transfer.wizard.line'
    _description = 'عناصر التحويل بين المراحل'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.stage.transfer.wizard',
        string='الودجت',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    is_model_header = fields.Boolean(string='عنوان مجموعة الموديل', default=False, readonly=True)
    model_group_label = fields.Char(string='مجموعة الموديل', readonly=True)
    kit_group_token = fields.Char(string='مفتاح مجموعة الطقم', readonly=True)
    kit_bom_id = fields.Many2one(
        'mrp.bom',
        string='الطقم (Kit)',
        domain=[('type', '=', 'phantom')],
        readonly=True,
    )
    kit_instance_number = fields.Integer(string='رقم الطقم', readonly=True)
    kit_component_qty = fields.Float(
        string='كمية مكون الطقم',
        readonly=True,
        digits=(16, 3),
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري (الشركة)',
        domain=[('is_company', '=', True)],
        ondelete='restrict',
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        ondelete='restrict',
    )
    selected = fields.Boolean(string='اختيار', default=True)
    move_id = fields.Many2one('stock.move', string='الحركة', ondelete='cascade')
    move_origin = fields.Char(string='المرجع', related='move_id.origin', readonly=True)
    move_type = fields.Selection(related='move_id.furniture_mrp_move_type', string='نوع الحركة', readonly=True)
    product_id = fields.Many2one('product.product', string='الصنف')
    source_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر المصدر',
        readonly=True,
    )
    source_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر المصدر',
        readonly=True,
    )
    technical_source_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furniture_stage_transfer_wizard_technical_line_rel',
        'wizard_line_id',
        'production_line_id',
        string='سطور المصدر الفنية',
        readonly=True,
        copy=False,
    )
    product_display_label = fields.Char(
        string='الصنف',
        compute='_compute_product_display_label',
        readonly=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        compute='_compute_furniture_model_id',
        readonly=True,
    )
    batch_image_1920 = fields.Image(
        string='صورة القطعة',
        readonly=False,
        attachment=False,
        max_width=1920,
        max_height=1920,
        help=(
            'صورة مؤقتة لهذه القطعة داخل أمر التصنيع فقط؛ '
            'لا يتم نسخها إلى صورة المنتج في المخزون.'
        ),
    )
    kit_piece_note = fields.Text(
        string='ملاحظات القطعة',
        copy=False,
        help='ملاحظة مؤقتة تُحفظ على قطعة أمر الإنتاج عند اعتماد تقسيمة الأطقم.',
    )
    first_stage_image_editable = fields.Boolean(
        string='يمكن تعديل صورة منتج أول مرحلة',
        default=False,
        readonly=True,
        copy=False,
    )
    target_material_summary = fields.Html(
        string='خامات المرحلة',
        compute='_compute_target_material_summary',
        sanitize=True,
        readonly=True,
    )
    dimension_label = fields.Char(
        string='المقاس',
        compute='_compute_dimension_label',
        readonly=True,
    )
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة')
    available_qty = fields.Float(
        string='الكمية المتاحة',
        compute='_compute_available_qty',
        readonly=True,
        digits=(16, 3),
    )
    qty_to_transfer = fields.Float(
        string='الكمية المنقولة',
        digits=(16, 3),
    )

    @api.depends(
        'wizard_id.production_id',
        'wizard_id.source_stage',
        'wizard_id.view_only',
        'product_id',
        'source_production_id',
        'source_production_line_id',
        'technical_source_production_line_ids',
        'kit_component_qty',
    )
    def _compute_available_qty(self):
        for rec in self:
            if rec.wizard_id.kit_planner_mode:
                qty = rec.kit_component_qty or 0.0
                rec.available_qty = qty
                rec.qty_to_transfer = qty
                continue
            qty = 0.0
            if rec.wizard_id and rec.wizard_id.production_id and rec.product_id:
                production = rec.wizard_id.production_id
                payloads = rec.wizard_id._stage_transfer_source_payloads()
                technical_lines = rec.technical_source_production_line_ids
                technical_line_ids = set(technical_lines._origin.ids)
                source_line_ids = set(
                    rec.source_production_line_id._origin.ids
                )
                source_production_ids = set(
                    rec.source_production_id._origin.ids
                )
                for payload in payloads:
                    payload_product = payload.get('product')
                    if (
                        not payload_product
                        or payload_product.id != rec.product_id.id
                    ):
                        continue
                    source_line = payload.get('source_production_line')
                    source_production = payload.get('source_production')
                    if technical_line_ids and (
                        not source_line or source_line.id not in technical_line_ids
                    ):
                        continue
                    if (
                        not technical_line_ids
                        and source_line_ids
                        and (not source_line or source_line.id not in source_line_ids)
                    ):
                        continue
                    if (
                        not technical_line_ids
                        and not source_line_ids
                        and source_production_ids
                        and (
                            not source_production
                            or source_production.id not in source_production_ids
                        )
                    ):
                        continue
                    qty += payload.get('qty') or 0.0
            if float_compare(rec.kit_component_qty or 0.0, 0.0, precision_digits=3) > 0:
                qty = min(qty, rec.kit_component_qty)
            rec.available_qty = qty
            if not rec.qty_to_transfer or float_compare(rec.qty_to_transfer, qty, precision_digits=3) > 0:
                rec.qty_to_transfer = qty

    @api.depends(
        'wizard_id.production_id',
        'wizard_id.production_id.production_line_ids',
        'product_id',
        'wizard_id.source_stage',
        'is_model_header',
        'model_group_label',
    )
    def _compute_product_display_label(self):
        computed_labels = []
        stock_candidates_by_production = {}
        for rec in self:
            if rec.is_model_header:
                label = rec.model_group_label
            elif not rec.product_id:
                label = False
            elif rec.wizard_id and rec.wizard_id.production_id:
                production = rec.wizard_id.production_id
                if production.production_line_ids:
                    # Both candidate branches use the product display name for
                    # multi-line orders.  Avoid the candidate lookup entirely:
                    # it may materialize a missing dimension/model variant while
                    # the wizard is only being read for display.
                    label = rec.product_id.display_name
                else:
                    if production.id not in stock_candidates_by_production:
                        stock_candidates_by_production[
                            production.id
                        ] = production._get_stage_stock_product_candidates()
                    if rec.product_id in stock_candidates_by_production[production.id]:
                        label = (
                            production._get_dimension_display_name()
                            or rec.product_id.display_name
                        )
                    else:
                        label = rec.product_id.display_name
            else:
                label = rec.product_id.display_name
            computed_labels.append((rec, label))

        # Assign only after all candidate lookups finish.  Some legacy lookup
        # paths normalize product records and can invalidate dependent caches.
        for rec, label in computed_labels:
            rec.product_display_label = label

    @api.depends(
        'source_production_line_id.furniture_order_model_id',
        'source_production_id.furniture_order_model_id',
        'product_id.furniture_model_id',
    )
    def _compute_furniture_model_id(self):
        for rec in self:
            rec.furniture_model_id = (
                rec.source_production_line_id.furniture_order_model_id
                or rec.source_production_id.furniture_order_model_id
                or rec.product_id.furniture_model_id
            )

    @api.depends(
        'source_production_line_id.production_id',
        'source_production_line_id.width_cm',
        'source_production_line_id.depth_cm',
        'source_production_line_id.height_cm',
        'source_production_line_id.bom_id',
        'product_id.furniture_dimension_label',
    )
    def _compute_dimension_label(self):
        for rec in self:
            source_line = rec.source_production_line_id
            production = source_line.production_id if source_line else False
            rec.dimension_label = (
                production._get_production_line_dimension_label(source_line)
                if production else False
            ) or rec.product_id.furniture_dimension_label

    @api.depends(
        'wizard_id.target_stage',
        'wizard_id.source_stage',
        'wizard_id.view_only',
        'product_id',
        'qty_to_transfer',
        'source_production_id',
        'source_production_line_id',
    )
    def _compute_target_material_summary(self):
        for rec in self:
            rec.target_material_summary = False
            material_stage = (
                rec.wizard_id.source_stage
                if rec.wizard_id.view_only
                else rec.wizard_id.target_stage
            )
            if (
                rec.is_model_header
                or material_stage not in ('tailoring', 'sewing', 'upholstery')
                or not rec.product_id
                or float_compare(rec.qty_to_transfer or 0.0, 0.0, precision_digits=3) <= 0
            ):
                continue
            production = (
                rec.source_production_line_id.production_id
                or rec.source_production_id
                or rec.wizard_id.production_id
            )
            if not production:
                continue
            commands = production._prepare_material_lines_for_product_stage(
                rec.product_id,
                rec.qty_to_transfer,
                material_stage,
                production_line=rec.source_production_line_id or False,
            )
            material_buckets = {}
            for command in commands:
                if not isinstance(command, (list, tuple)) or len(command) < 3 or command[0] != 0:
                    continue
                vals = command[2] or {}
                material = self.env['product.product'].browse(vals.get('product_id')).exists()
                if not material:
                    continue
                uom = self.env['uom.uom'].browse(vals.get('product_uom_id')).exists() or material.uom_id
                qty_needed = vals.get('qty_needed') or 0.0
                if float_compare(qty_needed, 0.0, precision_digits=3) <= 0:
                    continue
                key = (material.id, uom.id)
                bucket = material_buckets.setdefault(key, {
                    'name': material.display_name,
                    'uom': uom.name,
                    'qty': 0.0,
                })
                bucket['qty'] += qty_needed
            if not material_buckets:
                rec.target_material_summary = (
                    '<span class="o_furniture_no_stage_materials">%s</span>'
                    % escape(_('لا توجد خامات معرفة لهذه المرحلة.'))
                )
                continue
            items = []
            for bucket in sorted(material_buckets.values(), key=lambda item: item['name'].casefold()):
                qty_label = ('%.3f' % bucket['qty']).rstrip('0').rstrip('.')
                items.append(
                    '<li><strong>%s</strong><span>%s %s</span></li>' % (
                        escape(bucket['name']),
                        escape(qty_label),
                        escape(bucket['uom'] or ''),
                    )
                )
            rec.target_material_summary = (
                '<ul class="o_furniture_stage_material_list">%s</ul>' % ''.join(items)
            )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.product_uom_id = rec.product_id.uom_id


class FurnitureMrpStartStageWizard(models.TransientModel):
    _name = 'furniture.mrp.start.stage.wizard'
    _description = 'اختيار المرحلة التي يبدأ بها أمر التشغيل'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    stage_code = fields.Selection(
        selection='_selection_startable_stage_codes',
        string='المرحلة المطلوب بدؤها',
        required=True,
    )

    @api.model
    def _selection_startable_stage_codes(self):
        allowed_codes = self.env.context.get('furniture_start_stage_codes')
        if allowed_codes is None:
            production_id = (
                self.env.context.get('default_production_id')
                or self.env.context.get('active_id')
            )
            production = self.env['furniture.mrp.production'].browse(
                production_id
            ).exists() if production_id else self.env['furniture.mrp.production']
            allowed_codes = (
                production._get_startable_stage_codes()
                if production and len(production) == 1 else False
            )
            if not production:
                return FURNITURE_STAGE_SELECTION
        allowed_codes = set(allowed_codes or [])
        return [
            (stage_code, stage_label)
            for stage_code, stage_label in FURNITURE_STAGE_SELECTION
            if stage_code in allowed_codes
        ]

    def action_start_selected_stage(self):
        self.ensure_one()
        production = self.production_id.exists()
        if not production:
            raise UserError(_('أمر التشغيل لم يعد موجودًا. افتح الشاشة من جديد.'))
        production._check_start_stage_selector_access()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [production.id],
        )
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production_line '
            'WHERE production_id = %s AND active IS TRUE FOR UPDATE',
            [production.id],
        )
        production.invalidate_recordset([
            'state',
            *[
                field_names[1]
                for field_names in FURNITURE_STAGE_FIELD_MAP.values()
            ],
        ])
        production.production_line_ids.invalidate_recordset([
            'active', 'product_id', 'product_qty',
            'first_stage_started', 'first_stage_started_stage',
            'planned_start_stage',
            *[
                field_names[0]
                for field_names in FURNITURE_STAGE_FIELD_MAP.values()
            ],
        ])
        if production.state not in ('confirmed', 'in_production'):
            raise UserError(_('يجب تأكيد أمر التشغيل أولًا قبل بدء المرحلة.'))
        startable_stage_codes = production._get_startable_stage_codes()
        if self.stage_code not in startable_stage_codes:
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(
                self.stage_code,
                self.stage_code or '',
            )
            raise UserError(_(
                'مرحلة %s لم تعد جاهزة للبدء. افتح اختيار المرحلة من جديد.'
            ) % stage_label)

        # Persist the decision per batch before delegating to the established
        # stage action.  This makes the later store-request and quality flow
        # recognize the selected stage as the real entry point.  The write and
        # stage creation share one transaction, so a failure rolls both back.
        if not production._is_material_only_stage(self.stage_code):
            selector_lines = production._get_start_stage_selector_line_candidates(
                self.stage_code
            )
            selector_lines.filtered(
                lambda line: (
                    not line.planned_start_stage
                    or line.planned_start_stage not in line._selected_stage_codes()
                )
            ).write({'planned_start_stage': self.stage_code})

        start_method_names = {
            stage_code: 'action_start_%s' % stage_code
            for stage_code, _stage_label in FURNITURE_STAGE_SELECTION
        }
        view_method_names = {
            stage_code: 'action_view_%s' % stage_code
            for stage_code, _stage_label in FURNITURE_STAGE_SELECTION
        }
        start_method = getattr(production, start_method_names.get(self.stage_code, ''), None)
        view_method = getattr(production, view_method_names.get(self.stage_code, ''), None)
        if not start_method or not view_method:
            raise UserError(_('تعذر ربط المرحلة المختارة بدورة التشغيل.'))
        result = start_method()
        if result:
            return result
        return view_method()


class FurnitureMrpFirstStageStartWizard(models.TransientModel):
    _name = 'furniture.mrp.first.stage.start.wizard'
    _description = 'اختيار أصناف تبدأ أول مرحلة من المخزن'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='المرحلة',
        required=True,
        readonly=True,
    )
    stage_order_model = fields.Char(required=True, readonly=True)
    stage_order_res_id = fields.Integer(required=True, readonly=True)
    request_only = fields.Boolean(
        string='طلب إذن فقط',
        default=lambda self: bool(self.env.context.get('furniture_store_request_only')),
        readonly=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.first.stage.start.wizard.line',
        'wizard_id',
        string='الأصناف التي لم تبدأ أول مرحلة',
        copy=False,
    )
    stage_label = fields.Char(
        string='اسم المرحلة',
        compute='_compute_stage_label',
        readonly=True,
    )

    @api.depends('stage_code')
    def _compute_stage_label(self):
        labels = dict(FURNITURE_STAGE_SELECTION)
        for rec in self:
            rec.stage_label = labels.get(rec.stage_code, rec.stage_code or '')

    def _get_stage_order(self):
        self.ensure_one()
        if not self.stage_order_model or not self.stage_order_res_id:
            return False
        return self.env[self.stage_order_model].browse(self.stage_order_res_id).exists()

    def action_start_selected_from_stock(self):
        self.ensure_one()
        stage_order = self._get_stage_order()
        if not stage_order:
            raise UserError(_('أمر المرحلة غير موجود أو اتمسح قبل بدء التشغيل.'))
        if not self.env.context.get('furniture_storekeeper_approval_bypass'):
            approval_action = self.env['furniture.mrp.store.request']._request_first_stage_start(self)
            if approval_action:
                return approval_action
        self.production_id._start_first_stage_lines_from_stock(stage_order, self.stage_code, self.line_ids)
        if stage_order.state == 'pending':
            stage_order.with_context(
                furniture_skip_stage_start_prompt=True,
                furniture_stage_start_mode='first_stage_selected',
            ).action_start()
        return {
            'type': 'ir.actions.act_window',
            'name': stage_order.name,
            'res_model': stage_order._name,
            'res_id': stage_order.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_submit_store_request(self):
        self.ensure_one()
        approval_action = self.env['furniture.mrp.store.request']._request_first_stage_start(self)
        if not approval_action:
            raise UserError(_('تعذر إنشاء إذن المخزن للمنتجات المختارة.'))
        if approval_action.get('tag') == 'display_notification':
            approval_action.setdefault('params', {})['next'] = {
                'type': 'ir.actions.act_window_close',
            }
        return approval_action


class FurnitureMrpFirstStageStartWizardLine(models.TransientModel):
    _name = 'furniture.mrp.first.stage.start.wizard.line'
    _description = 'سطر صنف يبدأ أول مرحلة من المخزن'

    wizard_id = fields.Many2one(
        'furniture.mrp.first.stage.start.wizard',
        string='الويزارد',
        required=True,
        ondelete='cascade',
    )
    selected = fields.Boolean(string='بدء', default=True)
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر التشغيل',
        required=True,
        readonly=True,
    )
    technical_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furniture_first_stage_wizard_technical_line_rel',
        'wizard_line_id',
        'production_line_id',
        string='سطور القطع الفنية',
        readonly=True,
        copy=False,
    )
    product_id = fields.Many2one(
        'product.product',
        string='الصنف',
        related='production_line_id.product_id',
        readonly=True,
    )
    product_display_label = fields.Char(
        string='الصنف',
        compute='_compute_product_display_label',
        readonly=True,
    )
    dimension_label = fields.Char(
        string='المقاس',
        related='production_line_id.dimension_label',
        readonly=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        compute='_compute_furniture_model_id',
        readonly=True,
    )
    product_qty = fields.Float(
        string='الكمية',
        compute='_compute_product_qty',
        readonly=True,
    )
    qty_to_start = fields.Float(
        string='كمية البدء',
        digits=(16, 3),
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='الوحدة',
        related='production_line_id.product_uom_id',
        readonly=True,
    )
    stage_summary = fields.Char(
        string='مراحل الصنف',
        related='production_line_id.stage_summary',
        readonly=True,
    )
    material_override_json = fields.Json(string='كميات الخامات المعدلة', copy=False)

    @api.depends(
        'production_line_id.product_qty',
        'technical_production_line_ids.product_qty',
    )
    def _compute_product_qty(self):
        for rec in self:
            technical_lines = rec.technical_production_line_ids or rec.production_line_id
            rec.product_qty = sum(technical_lines.mapped('product_qty'))

    def _technical_qty_allocations(self):
        """Expand one displayed family quantity into exact technical pieces."""
        self.ensure_one()
        technical_lines = (
            self.technical_production_line_ids
            or self.production_line_id
        ).exists().sorted(lambda line: (
            line.sequence or 0,
            line.kit_instance_number or 0,
            line.id,
        ))
        qty_left = self.qty_to_start or 0.0
        allocations = []
        for technical_line in technical_lines:
            if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                break
            allocated_qty = min(qty_left, technical_line.product_qty or 0.0)
            if float_compare(allocated_qty, 0.0, precision_digits=3) <= 0:
                continue
            allocations.append((technical_line, allocated_qty))
            qty_left -= allocated_qty
        if float_compare(qty_left, 0.0, precision_digits=3) > 0:
            raise ValidationError(_(
                'كمية البدء أكبر من إجمالي القطع المتاحة للصنف %s.'
            ) % self.product_display_label)
        return allocations

    def _technical_allocation_payloads(self):
        """Return backward-compatible per-piece values for execution/persistence."""
        self.ensure_one()
        allocations = self._technical_qty_allocations()
        overrides = self.material_override_json or []
        remaining_overrides = {
            index: max(override.get('qty_needed') or 0.0, 0.0)
            for index, override in enumerate(overrides)
        }
        total_qty = sum(allocation_qty for _line, allocation_qty in allocations)
        payloads = []
        for allocation_index, (technical_line, allocation_qty) in enumerate(allocations):
            distributed_overrides = []
            for override_index, override in enumerate(overrides):
                distributed = (
                    remaining_overrides[override_index]
                    if allocation_index == len(allocations) - 1
                    else (
                        max(override.get('qty_needed') or 0.0, 0.0)
                        * allocation_qty / total_qty
                        if total_qty else 0.0
                    )
                )
                remaining_overrides[override_index] -= distributed
                distributed_overrides.append({
                    **override,
                    'qty_needed': max(distributed, 0.0),
                })
            payloads.append({
                'production_line_id': technical_line.id,
                'selected': True,
                'qty_to_start': allocation_qty,
                'material_override_json': distributed_overrides or False,
            })
        return payloads

    @api.depends('production_line_id')
    def _compute_product_display_label(self):
        for rec in self:
            if rec.production_line_id and rec.wizard_id.production_id:
                rec.product_display_label = rec.wizard_id.production_id._get_production_line_display_name(rec.production_line_id)
            else:
                rec.product_display_label = rec.product_id.display_name if rec.product_id else False

    @api.depends(
        'production_line_id.furniture_order_model_id',
        'production_line_id.production_id.furniture_order_model_id',
        'product_id.furniture_model_id',
    )
    def _compute_furniture_model_id(self):
        for rec in self:
            rec.furniture_model_id = (
                rec.production_line_id.furniture_order_model_id
                or rec.production_line_id.production_id.furniture_order_model_id
                or rec.product_id.furniture_model_id
            )

    @api.onchange('selected', 'product_qty')
    def _onchange_selected_qty(self):
        for rec in self:
            if rec.selected and not rec.qty_to_start:
                rec.qty_to_start = rec.product_qty
            elif not rec.selected:
                rec.qty_to_start = 0.0

    def action_view_requested_materials(self):
        self.ensure_one()
        return self.wizard_id.production_id._open_stage_requested_materials_preview(
            self.wizard_id.stage_code,
            self.product_id,
            self.qty_to_start,
            production_line=self.production_line_id,
            product_label=self.product_display_label,
            source_wizard_line=self,
            material_overrides=self.material_override_json,
        )

    @api.constrains('selected', 'qty_to_start', 'product_qty')
    def _check_qty_to_start(self):
        for rec in self:
            if not rec.selected:
                continue
            if float_compare(rec.qty_to_start or 0.0, 0.0, precision_digits=3) <= 0:
                raise ValidationError(_('كمية البدء لازم تكون أكبر من صفر للصنف %s.') % rec.product_display_label)
            if float_compare(rec.qty_to_start, rec.product_qty, precision_digits=3) > 0:
                raise ValidationError(_('كمية البدء لا يمكن تكون أكبر من الكمية المطلوبة للصنف %s.') % rec.product_display_label)


class FurnitureMrpStageQualitySendWizard(models.TransientModel):
    _name = 'furniture.mrp.stage.quality.send.wizard'
    _description = 'اختيار أصناف المرحلة للإرسال للجودة'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='المرحلة',
        required=True,
        readonly=True,
    )
    stage_order_model = fields.Char(required=True, readonly=True)
    stage_order_res_id = fields.Integer(required=True, readonly=True)
    line_ids = fields.One2many(
        'furniture.mrp.stage.quality.send.wizard.line',
        'wizard_id',
        string='الأصناف الجاهزة للجودة',
        copy=False,
    )
    stage_label = fields.Char(
        string='اسم المرحلة',
        compute='_compute_stage_label',
        readonly=True,
    )

    @api.depends('stage_code')
    def _compute_stage_label(self):
        labels = dict(FURNITURE_STAGE_SELECTION)
        for rec in self:
            rec.stage_label = labels.get(rec.stage_code, rec.stage_code or '')

    def _get_stage_order(self):
        self.ensure_one()
        if not self.stage_order_model or not self.stage_order_res_id:
            return False
        return self.env[self.stage_order_model].browse(self.stage_order_res_id).exists()

    def action_send_selected_to_quality(self):
        self.ensure_one()
        stage_order = self._get_stage_order()
        if not stage_order:
            raise UserError(_('أمر المرحلة غير موجود أو اتمسح قبل إرسال الجودة.'))
        selected_rows = self.line_ids.filtered(
            lambda line: line.selected and line.production_line_id
        )
        selected_lines = self.env['furniture.mrp.production.line']
        for row in selected_rows:
            if self.stage_code in ('tailoring', 'sewing', 'upholstery'):
                selected_lines |= row.production_line_id
            else:
                selected_lines |= row._technical_lines_for_quality_qty()
        if not selected_lines:
            raise UserError(_('اختار صنف واحد على الأقل لإرساله للجودة.'))
        stage_order.with_context(furniture_skip_stage_quality_prompt=True)._send_selected_lines_to_quality(selected_lines)
        return {
            'type': 'ir.actions.act_window',
            'name': stage_order.name,
            'res_model': stage_order._name,
            'res_id': stage_order.id,
            'view_mode': 'form',
            'target': 'current',
        }


class FurnitureMrpStageQualitySendWizardLine(models.TransientModel):
    _name = 'furniture.mrp.stage.quality.send.wizard.line'
    _description = 'سطر صنف جاهز للإرسال للجودة'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.stage.quality.send.wizard',
        string='الويزارد',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    is_model_header = fields.Boolean(
        string='عنوان مجموعة الطقم',
        default=False,
        readonly=True,
    )
    model_group_label = fields.Char(string='مجموعة الطقم', readonly=True)
    kit_group_token = fields.Char(string='مفتاح مجموعة الطقم', readonly=True)
    kit_bom_id = fields.Many2one(
        'mrp.bom',
        string='الطقم (Kit)',
        domain=[('type', '=', 'phantom')],
        readonly=True,
    )
    kit_instance_number = fields.Integer(string='رقم الطقم', readonly=True)
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        readonly=True,
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري (الشركة)',
        readonly=True,
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        readonly=True,
    )
    selected = fields.Boolean(string='إرسال', default=True)
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر التشغيل',
        readonly=True,
    )
    technical_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furniture_quality_wizard_technical_line_rel',
        'wizard_line_id',
        'production_line_id',
        string='سطور القطع الفنية',
        readonly=True,
        copy=False,
    )
    product_id = fields.Many2one(
        'product.product',
        string='الصنف',
        related='production_line_id.product_id',
        readonly=True,
    )
    product_display_label = fields.Char(
        string='الصنف',
        compute='_compute_product_display_label',
        readonly=True,
    )
    dimension_label = fields.Char(
        string='المقاس',
        related='production_line_id.dimension_label',
        readonly=True,
    )
    product_qty = fields.Float(
        string='الكمية',
        compute='_compute_product_qty',
        readonly=True,
    )
    qty_to_send = fields.Float(
        string='كمية الإرسال',
        digits=(16, 3),
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='الوحدة',
        related='production_line_id.product_uom_id',
        readonly=True,
    )
    stage_summary = fields.Char(
        string='مراحل الصنف',
        related='production_line_id.stage_summary',
        readonly=True,
    )
    batch_image_1920 = fields.Image(
        string='صورة القطعة',
        related='production_line_id.batch_image_1920',
        readonly=True,
    )
    stage_material_summary = fields.Html(
        string='خامات المرحلة',
        compute='_compute_stage_material_summary',
        sanitize=True,
        readonly=True,
    )

    @api.depends(
        'production_line_id.product_qty',
        'technical_production_line_ids.product_qty',
    )
    def _compute_product_qty(self):
        for rec in self:
            lines = rec.technical_production_line_ids or rec.production_line_id
            rec.product_qty = sum(lines.mapped('product_qty'))

    def _technical_lines_for_quality_qty(self):
        """Resolve one displayed quantity to whole traceable technical batches."""
        self.ensure_one()
        technical_lines = (
            self.technical_production_line_ids
            or self.production_line_id
        ).exists().sorted(lambda line: (
            line.sequence or 0,
            line.kit_instance_number or 0,
            line.id,
        ))
        qty_left = self.qty_to_send or 0.0
        selected_lines = self.env['furniture.mrp.production.line']
        for technical_line in technical_lines:
            if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                break
            line_qty = technical_line.product_qty or 0.0
            if float_compare(line_qty, qty_left, precision_digits=3) > 0:
                continue
            selected_lines |= technical_line
            qty_left -= line_qty
        if float_compare(qty_left, 0.0, precision_digits=3) > 0:
            raise ValidationError(_(
                'كمية الإرسال للصنف %s لازم توافق مجموع دفعاته الفنية. '
                'اختار كمية كاملة من القطع الموجودة داخل هذا السطر.'
            ) % self.product_display_label)
        return selected_lines

    @api.onchange('selected', 'product_qty')
    def _onchange_selected_qty_to_send(self):
        for rec in self:
            if rec.wizard_id.stage_code in ('tailoring', 'sewing', 'upholstery'):
                continue
            if rec.selected and not rec.qty_to_send:
                rec.qty_to_send = rec.product_qty
            elif not rec.selected:
                rec.qty_to_send = 0.0

    @api.constrains('selected', 'qty_to_send')
    def _check_qty_to_send(self):
        for rec in self:
            if (
                rec.wizard_id.stage_code in ('tailoring', 'sewing', 'upholstery')
                or not rec.selected
            ):
                continue
            if float_compare(rec.qty_to_send or 0.0, 0.0, precision_digits=3) <= 0:
                raise ValidationError(_(
                    'كمية الإرسال لازم تكون أكبر من صفر للصنف %s.'
                ) % rec.product_display_label)
            if float_compare(
                rec.qty_to_send,
                rec.product_qty,
                precision_digits=3,
            ) > 0:
                raise ValidationError(_(
                    'كمية الإرسال لا يمكن تكون أكبر من الكمية الجاهزة للصنف %s.'
                ) % rec.product_display_label)

    @api.depends('production_line_id', 'is_model_header', 'model_group_label')
    def _compute_product_display_label(self):
        for rec in self:
            if rec.is_model_header:
                rec.product_display_label = rec.model_group_label
            elif rec.production_line_id and rec.wizard_id.production_id:
                rec.product_display_label = rec.wizard_id.production_id._get_production_line_display_name(rec.production_line_id)
            else:
                rec.product_display_label = False

    @api.depends(
        'production_line_id',
        'technical_production_line_ids',
        'wizard_id.stage_code',
        'is_model_header',
    )
    def _compute_stage_material_summary(self):
        for rec in self:
            rec.stage_material_summary = False
            if rec.is_model_header or not rec.production_line_id:
                continue
            production_lines = (
                rec.technical_production_line_ids
                or rec.production_line_id
            )
            material_lines = rec.production_line_id.production_id.material_line_ids.filtered(
                lambda line: (
                    line.production_line_id in production_lines
                    and line.stage == rec.wizard_id.stage_code
                    and line.product_id
                    and float_compare(
                        line.qty_needed or 0.0,
                        0.0,
                        precision_digits=3,
                    ) > 0
                )
            )
            buckets = {}
            for material_line in material_lines:
                uom = material_line.product_uom_id or material_line.product_id.uom_id
                key = (material_line.product_id.id, uom.id)
                bucket = buckets.setdefault(key, {
                    'name': material_line.product_id.display_name,
                    'uom': uom.name,
                    'qty': 0.0,
                })
                bucket['qty'] += material_line.qty_needed
            if not buckets:
                rec.stage_material_summary = (
                    '<span class="o_furniture_no_stage_materials">%s</span>'
                    % escape(_('لا توجد خامات معرفة لهذه المرحلة.'))
                )
                continue
            items = []
            for bucket in sorted(
                buckets.values(),
                key=lambda item: item['name'].casefold(),
            ):
                qty_label = ('%.3f' % bucket['qty']).rstrip('0').rstrip('.')
                items.append(
                    '<li><strong>%s</strong><span>%s %s</span></li>' % (
                        escape(bucket['name']),
                        escape(qty_label),
                        escape(bucket['uom'] or ''),
                    )
                )
            rec.stage_material_summary = (
                '<ul class="o_furniture_stage_material_list">%s</ul>'
                % ''.join(items)
            )

    @api.constrains('is_model_header', 'production_line_id')
    def _check_quality_piece_identity(self):
        for rec in self:
            if not rec.is_model_header and not rec.production_line_id:
                raise ValidationError(_(
                    'كل سطر قطعة في شاشة الجودة لازم يظل مرتبطًا بسطر أمر التشغيل الأصلي.'
                ))


class FurnitureMrpStageStartCarryoverWizard(models.TransientModel):
    _name = 'furniture.mrp.stage.start.carryover.wizard'
    _description = 'اختيار منتجات صالة المرحلة عند بدء التشغيل'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='المرحلة',
        required=True,
        readonly=True,
    )
    stage_order_model = fields.Char(required=True, readonly=True)
    stage_order_res_id = fields.Integer(required=True, readonly=True)
    request_only = fields.Boolean(
        string='طلب إذن فقط',
        default=lambda self: bool(self.env.context.get('furniture_store_request_only')),
        readonly=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.stage.start.carryover.wizard.line',
        'wizard_id',
        string='المنتجات الموجودة في الصالة',
        copy=False,
    )
    stage_label = fields.Char(
        string='اسم المرحلة',
        compute='_compute_stage_label',
        readonly=True,
    )

    @api.depends('stage_code')
    def _compute_stage_label(self):
        labels = dict(FURNITURE_STAGE_SELECTION)
        for rec in self:
            rec.stage_label = labels.get(rec.stage_code, rec.stage_code or '')

    def _get_stage_order(self):
        self.ensure_one()
        if not self.stage_order_model or not self.stage_order_res_id:
            return False
        return self.env[self.stage_order_model].browse(self.stage_order_res_id).exists()

    def _technical_execution_lines(self):
        """Expand grouped display rows into the legacy exact-source contract."""
        self.ensure_one()
        if not self.line_ids.filtered('technical_allocation_json'):
            return self.line_ids
        line_values = []
        for displayed_line in self.line_ids:
            line_values.extend(displayed_line._technical_allocation_payloads())
        technical_wizard = self.env[self._name].create({
            'production_id': self.production_id.id,
            'stage_code': self.stage_code,
            'stage_order_model': self.stage_order_model,
            'stage_order_res_id': self.stage_order_res_id,
            'request_only': self.request_only,
            'line_ids': [(0, 0, values) for values in line_values],
        })
        return technical_wizard.line_ids

    def _action_start_with_mode(self, mode):
        self.ensure_one()
        stage_order = self._get_stage_order()
        if not stage_order:
            raise UserError(_('أمر المرحلة غير موجود أو اتمسح قبل بدء التشغيل.'))
        if not self.env.context.get('furniture_storekeeper_approval_bypass'):
            approval_action = self.env['furniture.mrp.store.request']._request_stage_hall_start(self, mode)
            if approval_action:
                return approval_action
        execution_lines = self._technical_execution_lines()
        selected_wizard_lines = execution_lines.filtered(
            lambda line: line.selected and line.qty_to_start > 0
        )
        current_lines = self.production_id._get_stage_start_current_lines(
            self.stage_code,
            selected_wizard_lines if mode == 'selected_work' else execution_lines,
            selected_only=(mode == 'selected_work'),
            stage_order=stage_order,
        )
        if mode == 'current_only' and not current_lines:
            raise UserError(_('لا يوجد أي صنف من أمر التشغيل الحالي في صالة هذه المرحلة. اختار الشغل القديم أو اعمل تحويل للمنتج المطلوب أولاً.'))
        if current_lines:
            self.production_id._apply_stage_material_overrides(
                self.stage_code,
                selected_wizard_lines,
                current_lines,
            )
            stage_order._add_stage_active_lines(current_lines)
            self.production_id._move_stage_materials_for_lines(self.stage_code, current_lines)
        if mode in ('with_existing', 'selected_work'):
            if mode == 'selected_work' and not selected_wizard_lines:
                raise UserError(_('اختار منتج واحد على الأقل من صالة المرحلة قبل بدء التشغيل.'))
            self.production_id.with_context(
                furniture_move_selected_materials_to_stage=(mode == 'selected_work')
            )._register_stage_carryover_lines(
                self.stage_code,
                execution_lines,
                current_production_lines=current_lines,
            )
        if stage_order.state == 'pending':
            stage_order.with_context(
                furniture_skip_stage_start_prompt=True,
                furniture_stage_start_mode=mode,
                furniture_skip_material_move=True,
            ).action_start()
        return {
            'type': 'ir.actions.act_window',
            'name': stage_order.name,
            'res_model': stage_order._name,
            'res_id': stage_order.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_start_current_only(self):
        return self._action_start_with_mode('current_only')

    def action_start_with_existing(self):
        return self._action_start_with_mode('with_existing')

    def action_start_selected_work(self):
        return self._action_start_with_mode('selected_work')

    def action_submit_store_request(self):
        self.ensure_one()
        approval_action = self.env['furniture.mrp.store.request']._request_stage_hall_start(
            self, 'selected_work',
        )
        if not approval_action:
            raise UserError(_('تعذر إنشاء إذن المخزن للمنتجات المختارة.'))
        if approval_action.get('tag') == 'display_notification':
            approval_action.setdefault('params', {})['next'] = {
                'type': 'ir.actions.act_window_close',
            }
        return approval_action


class FurnitureMrpStageStartCarryoverWizardLine(models.TransientModel):
    _name = 'furniture.mrp.stage.start.carryover.wizard.line'
    _description = 'سطر منتج موجود في صالة المرحلة'

    wizard_id = fields.Many2one(
        'furniture.mrp.stage.start.carryover.wizard',
        string='الويزارد',
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one('product.product', string='الصنف', required=True, readonly=True)
    product_display_label = fields.Char(
        string='الصنف',
        compute='_compute_product_display_label',
        readonly=True,
    )
    dimension_label = fields.Char(
        string='المقاس',
        related='product_id.furniture_dimension_label',
        readonly=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        compute='_compute_furniture_model_id',
        readonly=True,
    )
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True, readonly=True)
    selected = fields.Boolean(string='تشغيل', default=True)
    source_origin = fields.Char(string='مرجع أمر التشغيل الأصلي', readonly=True)
    source_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأصلي',
        readonly=True,
    )
    source_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر التشغيل الأصلي',
        readonly=True,
    )
    technical_source_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furniture_stage_start_wizard_technical_line_rel',
        'wizard_line_id',
        'production_line_id',
        string='سطور المصدر الفنية',
        readonly=True,
        copy=False,
    )
    technical_allocation_json = fields.Json(
        string='توزيع كميات الصالة الفني',
        readonly=True,
        copy=False,
    )
    qty_in_work_location = fields.Float(
        string='الكمية الموجودة في الصالة',
        digits=(16, 3),
        readonly=True,
    )
    qty_to_start = fields.Float(
        string='كمية التشغيل',
        digits=(16, 3),
    )
    material_override_json = fields.Json(string='كميات الخامات المعدلة', copy=False)

    def _technical_allocation_payloads(self):
        """Allocate one visible quantity back to its exact physical sources."""
        self.ensure_one()
        allocations = list(self.technical_allocation_json or [])
        if not allocations:
            allocations = [{
                'product_id': self.product_id.id,
                'product_uom_id': self.product_uom_id.id,
                'qty_in_work_location': self.qty_in_work_location,
                'source_origin': self.source_origin or False,
                'source_production_id': self.source_production_id.id or False,
                'source_production_line_id': self.source_production_line_id.id or False,
            }]

        qty_left = self.qty_to_start or 0.0
        selected_allocations = []
        for allocation in allocations:
            if float_compare(qty_left, 0.0, precision_digits=3) <= 0:
                break
            available_qty = max(allocation.get('qty_in_work_location') or 0.0, 0.0)
            allocated_qty = min(qty_left, available_qty)
            if float_compare(allocated_qty, 0.0, precision_digits=3) <= 0:
                continue
            selected_allocations.append((allocation, allocated_qty))
            qty_left -= allocated_qty
        if float_compare(qty_left, 0.0, precision_digits=3) > 0:
            raise ValidationError(_(
                'كمية التشغيل أكبر من إجمالي الموجود في الصالة للصنف %s.'
            ) % self.product_display_label)

        overrides = self.material_override_json or []
        remaining_overrides = {
            index: max(override.get('qty_needed') or 0.0, 0.0)
            for index, override in enumerate(overrides)
        }
        total_qty = sum(qty for _allocation, qty in selected_allocations)
        payloads = []
        for allocation_index, (allocation, allocated_qty) in enumerate(selected_allocations):
            distributed_overrides = []
            for override_index, override in enumerate(overrides):
                distributed = (
                    remaining_overrides[override_index]
                    if allocation_index == len(selected_allocations) - 1
                    else (
                        max(override.get('qty_needed') or 0.0, 0.0)
                        * allocated_qty / total_qty
                        if total_qty else 0.0
                    )
                )
                remaining_overrides[override_index] -= distributed
                distributed_overrides.append({
                    **override,
                    'qty_needed': max(distributed, 0.0),
                })
            payloads.append({
                'product_id': allocation.get('product_id') or self.product_id.id,
                'product_uom_id': (
                    allocation.get('product_uom_id') or self.product_uom_id.id
                ),
                'qty_in_work_location': allocation.get('qty_in_work_location') or 0.0,
                'qty_to_start': allocated_qty,
                'selected': bool(self.selected),
                'source_origin': allocation.get('source_origin') or False,
                'source_production_id': allocation.get('source_production_id') or False,
                'source_production_line_id': (
                    allocation.get('source_production_line_id') or False
                ),
                'material_override_json': distributed_overrides or False,
            })
        return payloads

    @api.depends(
        'product_id',
        'source_production_id',
        'source_production_line_id',
    )
    def _compute_product_display_label(self):
        for rec in self:
            source_production = (
                rec.source_production_id
                or rec.source_production_line_id.production_id
            )
            if rec.source_production_line_id and source_production:
                rec.product_display_label = source_production._get_production_line_display_name(
                    rec.source_production_line_id
                )
            else:
                rec.product_display_label = rec.product_id.display_name if rec.product_id else False

    @api.depends(
        'source_production_line_id.furniture_order_model_id',
        'source_production_id.furniture_order_model_id',
        'product_id.furniture_model_id',
    )
    def _compute_furniture_model_id(self):
        for rec in self:
            rec.furniture_model_id = (
                rec.source_production_line_id.furniture_order_model_id
                or rec.source_production_id.furniture_order_model_id
                or rec.product_id.furniture_model_id
            )

    @api.onchange('selected', 'qty_in_work_location')
    def _onchange_selected_qty(self):
        for rec in self:
            if rec.selected and not rec.qty_to_start:
                rec.qty_to_start = rec.qty_in_work_location
            elif not rec.selected:
                rec.qty_to_start = 0.0

    def action_view_requested_materials(self):
        self.ensure_one()
        prepared_commands = []
        for allocation in self._technical_allocation_payloads():
            source_line_id = allocation.get('source_production_line_id')
            source_line = self.env['furniture.mrp.production.line'].browse(
                source_line_id
            ).exists()
            if source_line_id and not source_line:
                raise UserError(_(
                    'اتغيرت سطور أمر الإنتاج بعد فتح شاشة الصالة. اقفل الشاشة وافتحها من جديد.'
                ))
            allocation_product = self.env['product.product'].browse(
                allocation.get('product_id')
            ).exists() or self.product_id
            prepared_commands.extend(
                self.wizard_id.production_id._prepare_material_lines_for_product_stage(
                    allocation_product,
                    allocation.get('qty_to_start') or 0.0,
                    self.wizard_id.stage_code,
                    production_line=source_line,
                )
            )
        return self.wizard_id.production_id._open_stage_requested_materials_preview(
            self.wizard_id.stage_code,
            self.product_id,
            self.qty_to_start,
            production_line=self.source_production_line_id,
            product_label=self.product_display_label,
            source_wizard_line=self,
            material_overrides=self.material_override_json,
            prepared_material_commands=prepared_commands,
        )

    @api.constrains('selected', 'qty_to_start', 'qty_in_work_location')
    def _check_qty_to_start(self):
        for rec in self:
            if not rec.selected:
                continue
            if float_compare(rec.qty_to_start or 0.0, 0.0, precision_digits=3) <= 0:
                raise ValidationError(_('كمية التشغيل لازم تكون أكبر من صفر للصنف %s.') % rec.product_display_label)
            if float_compare(rec.qty_to_start, rec.qty_in_work_location, precision_digits=3) > 0:
                raise ValidationError(_('كمية التشغيل لا يمكن تكون أكبر من الموجود في الصالة للصنف %s.') % rec.product_display_label)


class FurnitureMrpStageMaterialPreviewWizard(models.TransientModel):
    _name = 'furniture.mrp.stage.material.preview.wizard'
    _description = 'معاينة خامات منتج قبل طلب إذن المخزن'

    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر التشغيل الأسبوعي', required=True, readonly=True,
    )
    stage_code = fields.Selection(FURNITURE_STAGE_SELECTION, string='المرحلة', required=True, readonly=True)
    product_id = fields.Many2one('product.product', string='المنتج', required=True, readonly=True)
    product_label = fields.Char(string='الصنف', readonly=True)
    output_qty = fields.Float(string='كمية المنتج المختارة', readonly=True, digits=(16, 3))
    output_uom_id = fields.Many2one('uom.uom', string='وحدة المنتج', readonly=True)
    source_wizard_line_model = fields.Char(readonly=True)
    source_wizard_line_id = fields.Integer(readonly=True)
    line_ids = fields.One2many(
        'furniture.mrp.stage.material.preview.wizard.line',
        'wizard_id',
        string='الخامات المطلوبة',
    )

    def action_apply_quantities(self):
        self.ensure_one()
        allowed_models = {
            'furniture.mrp.first.stage.start.wizard.line',
            'furniture.mrp.stage.start.carryover.wizard.line',
        }
        if self.source_wizard_line_model not in allowed_models or not self.source_wizard_line_id:
            raise UserError(_('تعذر العثور على سطر المنتج الأصلي لتثبيت الكميات.'))
        source_line = self.env[self.source_wizard_line_model].browse(self.source_wizard_line_id).exists()
        if not source_line:
            raise UserError(_('سطر المنتج الأصلي لم يعد موجودًا. افتح ويزارد المرحلة من جديد.'))
        overrides = [{
            'product_id': line.product_id.id,
            'product_uom_id': line.product_uom_id.id,
            'qty_needed': max(line.requested_qty or 0.0, 0.0),
        } for line in self.line_ids]
        source_line.write({'material_override_json': overrides})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم اعتماد كميات الخامات'),
                'message': _('سيتم إرسال وصرف الكميات المعدلة بدل كميات الريسيبي لهذا المنتج.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class FurnitureMrpStageMaterialPreviewWizardLine(models.TransientModel):
    _name = 'furniture.mrp.stage.material.preview.wizard.line'
    _description = 'سطر خامة في معاينة طلب المخزن'
    _order = 'product_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.stage.material.preview.wizard', required=True, ondelete='cascade',
    )
    product_id = fields.Many2one('product.product', string='الخامة', required=True, readonly=True)
    requested_qty = fields.Float(string='الكمية المطلوبة', digits=(16, 3))
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True, readonly=True)
    available_qty = fields.Float(string='المتاح حاليًا', readonly=True, digits=(16, 3))
    shortage_qty = fields.Float(string='العجز', compute='_compute_shortage', digits=(16, 3))

    @api.depends('requested_qty', 'available_qty')
    def _compute_shortage(self):
        for line in self:
            line.shortage_qty = max((line.requested_qty or 0.0) - (line.available_qty or 0.0), 0.0)

    @api.constrains('requested_qty')
    def _check_requested_qty(self):
        for line in self:
            if float_compare(line.requested_qty or 0.0, 0.0, precision_digits=3) < 0:
                raise ValidationError(_('كمية الخامة لا يمكن أن تكون أقل من صفر.'))


class FurnitureMrpProductionFirstLineWizard(models.TransientModel):
    _name = 'furniture.mrp.production.first.line.wizard'
    _description = 'إضافة عدة أصناف لموديل واحد داخل أمر التشغيل'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        readonly=True,
        ondelete='cascade',
    )
    create_new_production = fields.Boolean(
        string='إنشاء أمر إنتاج جديد',
        readonly=True,
        help='ينشئ أمر الإنتاج فقط عند تأكيد المنتجات، وليس عند فتح النافذة.',
    )
    product_id = fields.Many2one(
        'product.product',
        string='الصنف القديم',
        domain="[('furniture_has_active_normal_recipe', '=', True), ('furniture_dimension_source_product_id', '=', False)]",
        help='حقل توافق للإضافة القديمة بسطر واحد.',
    )
    furniture_order_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
    )
    model_locked = fields.Boolean(
        string='الموديل مثبت',
        compute='_compute_model_locked',
    )
    available_product_ids = fields.Many2many(
        'product.product',
        'furniture_mrp_batch_wizard_available_product_rel',
        'wizard_id',
        'product_id',
        string='المنتجات المتاحة للموديل',
        compute='_compute_available_product_ids',
    )
    selected_product_ids = fields.Many2many(
        'product.product',
        'furniture_mrp_batch_wizard_selected_product_rel',
        'wizard_id',
        'product_id',
        string='المنتجات النهائية',
        domain="[('id', 'in', available_product_ids)]",
    )
    line_ids = fields.One2many(
        'furniture.mrp.production.first.line.wizard.line',
        'wizard_id',
        string='الكميات والمقاسات',
    )
    product_qty = fields.Float(
        string='الكمية',
        default=1.0,
        required=True,
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري (الشركة)',
        domain=[('is_company', '=', True)],
        ondelete='restrict',
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        ondelete='restrict',
    )
    bom_id = fields.Many2one(
        'mrp.bom',
        string='الريسيبي',
        domain="[('furniture_product_id', '=', product_id), ('furniture_model_id', '=', furniture_order_model_id), ('type', '=', 'normal')]",
    )
    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')

    @api.depends(
        'production_id',
        'production_id.furniture_order_model_id',
        'production_id.production_line_ids.furniture_order_model_id',
    )
    def _compute_model_locked(self):
        for wizard in self:
            wizard.model_locked = bool(
                wizard.production_id.furniture_order_model_id
                or wizard.production_id.production_line_ids
            )

    @api.depends('furniture_order_model_id', 'production_id.company_id')
    def _compute_available_product_ids(self):
        Bom = self.env['mrp.bom']
        empty_products = self.env['product.product']
        for wizard in self:
            model = wizard.furniture_order_model_id
            if not model:
                wizard.available_product_ids = empty_products
                continue
            company = wizard.production_id.company_id or self.env.company
            recipes = Bom.search([
                ('active', '=', True),
                ('type', '=', 'normal'),
                ('furniture_product_id', '!=', False),
                ('company_id', 'in', [company.id, False]),
            ])
            candidate_products = recipes.mapped(
                'furniture_product_id'
            ).filtered(lambda product: (
                product.active
                and not product.furniture_dimension_source_product_id
            ))
            wizard.available_product_ids = candidate_products.filtered(
                lambda product: Bom._find_furniture_production_recipe(
                    product,
                    model,
                    company=company,
                )
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        production = self.env['furniture.mrp.production'].browse(res.get('production_id') or self.env.context.get('default_production_id'))
        if not production.exists():
            return res
        default_product = production.product_id or (production.bom_id.furniture_product_id if production.bom_id and production.bom_id.furniture_product_id else False)
        if default_product and not res.get('product_id'):
            res['product_id'] = default_product.id
        if not res.get('product_qty'):
            res['product_qty'] = production.product_qty or 1.0
        if production.bom_id and not res.get('bom_id'):
            res['bom_id'] = production.bom_id.id
        if production.furniture_order_model_id and not res.get('furniture_order_model_id'):
            res['furniture_order_model_id'] = production.furniture_order_model_id.id
        if production.buyer_partner_id and not res.get('buyer_partner_id'):
            res['buyer_partner_id'] = production.buyer_partner_id.id
        if production.beneficiary_partner_id and not res.get('beneficiary_partner_id'):
            res['beneficiary_partner_id'] = production.beneficiary_partner_id.id
        if not res.get('width_cm') and production.width_cm:
            res['width_cm'] = production.width_cm
        if not res.get('depth_cm') and production.depth_cm:
            res['depth_cm'] = production.depth_cm
        if not res.get('height_cm') and production.height_cm:
            res['height_cm'] = production.height_cm
        return res

    def _locked_model(self):
        self.ensure_one()
        if self.production_id.furniture_order_model_id:
            return self.production_id.furniture_order_model_id
        models = self.production_id.production_line_ids.mapped(
            'furniture_order_model_id'
        ).filtered(lambda model: model)
        return models[:1]

    @api.onchange('furniture_order_model_id')
    def _onchange_batch_furniture_order_model_id(self):
        for wizard in self:
            locked_model = wizard._locked_model()
            if locked_model and wizard.furniture_order_model_id != locked_model:
                wizard.furniture_order_model_id = locked_model
                return {
                    'warning': {
                        'title': _('الموديل مثبت لأمر التشغيل'),
                        'message': _(
                            'كل أصناف أمر التشغيل يجب أن تكون من موديل واحد: %s.'
                        ) % locked_model.display_name,
                    }
                }
            wizard.selected_product_ids = [(5, 0, 0)]
            wizard.line_ids = [(5, 0, 0)]

    @api.onchange('selected_product_ids')
    def _onchange_selected_product_ids(self):
        quick_order = {
            'كنبة كبيرة': 0,
            'كنبة صغيرة': 1,
            'فوتيه': 2,
            'شازلونج': 3,
        }
        for wizard in self:
            def persistent_record(record):
                """Return the database record behind an onchange NewId."""
                origin = record._origin
                return origin if origin and origin.id else record

            existing_by_product = {
                persistent_record(line.product_id).id: line
                for line in wizard.line_ids
                if line.product_id
            }
            commands = [(5, 0, 0)]
            products = sorted(
                (
                    (product, persistent_record(product))
                    for product in wizard.selected_product_ids
                ),
                key=lambda pair: (
                    quick_order.get((pair[0].name or '').strip(), 999),
                    pair[0].display_name or '',
                    pair[1].id,
                )
            )
            for product, source_product in products:
                bom = self.env['mrp.bom']._find_furniture_production_recipe(
                    source_product,
                    wizard.furniture_order_model_id,
                    company=wizard.production_id.company_id or self.env.company,
                )
                if not bom:
                    continue
                previous = existing_by_product.get(source_product.id)
                commands.append((0, 0, {
                    'product_id': source_product.id,
                    'bom_id': bom.id,
                    'product_qty': previous.product_qty if previous else 1.0,
                    'width_cm': (
                        previous.width_cm
                        if previous else (bom.furniture_width_cm or 0.0)
                    ),
                    'depth_cm': (
                        previous.depth_cm
                        if previous else (bom.furniture_depth_cm or 0.0)
                    ),
                    'height_cm': (
                        previous.height_cm
                        if previous else (bom.furniture_height_cm or 0.0)
                    ),
                    'dimensions_expanded': (
                        previous.dimensions_expanded if previous else False
                    ),
                }))
            wizard.line_ids = commands

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            rec.furniture_order_model_id = False
            rec.bom_id = False
            rec.width_cm = 0.0
            rec.depth_cm = 0.0
            rec.height_cm = 0.0
            if not rec.product_id:
                continue

    @api.onchange('product_id', 'furniture_order_model_id')
    def _onchange_furniture_order_model_id(self):
        for rec in self:
            rec.bom_id = False
            if not rec.product_id or not rec.furniture_order_model_id:
                continue
            bom = self.env['mrp.bom']._find_furniture_production_recipe(
                rec.product_id,
                rec.furniture_order_model_id,
                company=rec.production_id.company_id or self.env.company,
            )
            rec.bom_id = bom
            if bom:
                rec.width_cm = bom.furniture_width_cm or 0.0
                rec.depth_cm = bom.furniture_depth_cm or 0.0
                rec.height_cm = bom.furniture_height_cm or 0.0
            else:
                return {
                    'warning': {
                        'title': _('لا يوجد ريسيبي للموديل'),
                        'message': _(
                            'أنشئ ريسيبي للمنتج %(product)s والموديل %(model)s أولًا.'
                        ) % {
                            'product': rec.product_id.display_name,
                            'model': rec.furniture_order_model_id.display_name,
                        },
                    }
                }

    @api.onchange('bom_id')
    def _onchange_bom_id(self):
        for rec in self:
            if rec.bom_id:
                if rec.bom_id.furniture_model_id:
                    rec.furniture_order_model_id = rec.bom_id.furniture_model_id
                rec.width_cm = rec.bom_id.furniture_width_cm or 0.0
                rec.depth_cm = rec.bom_id.furniture_depth_cm or 0.0
                rec.height_cm = rec.bom_id.furniture_height_cm or 0.0

    def action_create_first_line(self):
        self.ensure_one()
        if not self.product_id:
            raise UserError(_('اختار الصنف الأول قبل الحفظ.'))
        if not self.furniture_order_model_id:
            raise UserError(_('اختار الموديل قبل الحفظ.'))
        if not self.bom_id:
            raise UserError(_('لا توجد ريسيبي مطابقة للمنتج والموديل المختارين.'))
        if self.product_qty <= 0:
            raise UserError(_('الكمية لازم تكون أكبر من صفر.'))
        self.production_id._create_first_production_line(
            self.product_id,
            product_qty=self.product_qty,
            bom=self.bom_id,
            furniture_model=self.furniture_order_model_id,
            width_cm=self.width_cm,
            depth_cm=self.depth_cm,
            height_cm=self.height_cm,
            buyer_partner=self.buyer_partner_id,
            beneficiary_partner=self.beneficiary_partner_id,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('أمر التشغيل الأسبوعي'),
            'res_model': 'furniture.mrp.production',
            'res_id': self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_lines(self):
        self.ensure_one()
        production = self.production_id.exists()
        if not production and not self.create_new_production:
            raise UserError(_('أمر التشغيل لم يعد موجودًا.'))
        if production and production.state in ('done', 'cancelled'):
            raise UserError(_('لا يمكن إضافة أصناف إلى أمر تشغيل منتهي أو ملغي.'))
        if not self.furniture_order_model_id:
            raise UserError(_('اختار الموديل أولًا.'))

        locked_model = self._locked_model()
        if locked_model and self.furniture_order_model_id != locked_model:
            raise UserError(_(
                'كل أصناف أمر التشغيل يجب أن تكون من موديل واحد: %s.'
            ) % locked_model.display_name)

        selected_lines = self.line_ids.filtered(
            lambda line: line.product_id in self.selected_product_ids
        )
        if not selected_lines:
            raise UserError(_('اختار منتجًا نهائيًا واحدًا على الأقل.'))

        if not production:
            production = self.env['furniture.mrp.production'].create({
                'company_id': self.env.company.id,
                'responsible_id': self.env.user.id,
                'stage_plan_mode': 'recipe',
            })
            self.production_id = production

        production.write({
            'furniture_order_model_id': self.furniture_order_model_id.id,
            'buyer_partner_id': self.buyer_partner_id.id or False,
            'beneficiary_partner_id': self.beneficiary_partner_id.id or False,
        })

        next_sequence = max(production.production_line_ids.mapped('sequence') or [0]) + 10
        create_values = []
        for line in selected_lines.sorted(lambda item: (item.sequence, item.id)):
            if float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) <= 0:
                raise UserError(_(
                    'كمية المنتج %s لازم تكون أكبر من صفر.'
                ) % line.product_id.display_name)
            resolved_bom = self.env['mrp.bom']._find_furniture_production_recipe(
                line.product_id,
                self.furniture_order_model_id,
                company=production.company_id or self.env.company,
            )
            if not resolved_bom:
                raise UserError(_(
                    'لا توجد ريسيبي للمنتج %(product)s مع الموديل %(model)s.'
                ) % {
                    'product': line.product_id.display_name,
                    'model': self.furniture_order_model_id.display_name,
                })
            create_values.append({
                'production_id': production.id,
                'sequence': next_sequence,
                'product_id': line.product_id.id,
                'furniture_order_model_id': self.furniture_order_model_id.id,
                'buyer_partner_id': self.buyer_partner_id.id or False,
                'beneficiary_partner_id': self.beneficiary_partner_id.id or False,
                'product_qty': line.product_qty,
                'bom_id': resolved_bom.id,
                'width_cm': line.width_cm or 0.0,
                'depth_cm': line.depth_cm or 0.0,
                'height_cm': line.height_cm or 0.0,
            })
            next_sequence += 10

        new_lines = self.env['furniture.mrp.production.line'].create(create_values)
        first_line = new_lines[:1]
        header_vals = {}
        if not production.product_id:
            header_vals['product_id'] = first_line.product_id.id
        if not production.bom_id:
            header_vals['bom_id'] = first_line.bom_id.id
        if not production.product_base_name:
            header_vals['product_base_name'] = production._normalize_product_base_name(
                first_line.product_id.product_tmpl_id.name or first_line.product_id.name
            )
        if header_vals:
            production.write(header_vals)
        production.message_post(body=_(
            '🧾 تمت إضافة %(count)s أصناف للموديل %(model)s: %(products)s'
        ) % {
            'count': len(new_lines),
            'model': self.furniture_order_model_id.display_name,
            'products': '، '.join(new_lines.mapped('product_id.display_name')),
        })
        action_context = dict(self.env.context, form_view_initial_mode='edit')
        action_context.pop('default_create_new_production', None)
        return {
            'type': 'ir.actions.act_window',
            'name': _('أمر التشغيل الأسبوعي'),
            'res_model': 'furniture.mrp.production',
            'res_id': production.id,
            'view_mode': 'form',
            'target': 'current',
            'context': action_context,
        }


class FurnitureMrpProductionFirstLineWizardLine(models.TransientModel):
    _name = 'furniture.mrp.production.first.line.wizard.line'
    _description = 'سطر منتج داخل الإضافة الجماعية لأمر التشغيل'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.production.first.line.wizard',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product',
        string='المنتج النهائي',
        required=True,
        readonly=True,
    )
    bom_id = fields.Many2one(
        'mrp.bom',
        string='الريسيبي',
        required=True,
        readonly=True,
    )
    product_qty = fields.Float(
        string='الكمية',
        default=1.0,
        required=True,
    )
    dimensions_expanded = fields.Boolean(
        string='المقاس',
        default=False,
    )
    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')
    dimension_label = fields.Char(
        string='المقاس الحالي',
        compute='_compute_dimension_label',
    )

    @api.depends('width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_label(self):
        for line in self:
            values = [line.width_cm, line.depth_cm, line.height_cm]
            if not any(values):
                line.dimension_label = _('المقاس')
                continue
            formatted = [
                str(int(value)) if float(value).is_integer() else str(value)
                for value in values
            ]
            line.dimension_label = _('%s × %s × %s سم') % tuple(formatted)

    @api.constrains('product_qty')
    def _check_product_qty(self):
        for line in self:
            if float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) <= 0:
                raise ValidationError(_('كمية كل منتج لازم تكون أكبر من صفر.'))


class FurnitureMrpEmptyConfirmWizard(models.TransientModel):
    _name = 'furniture.mrp.empty.confirm.wizard'
    _description = 'تأكيد أمر تشغيل فارغ'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    warning_message = fields.Text(string='رسالة التحذير', readonly=True)

    def action_confirm_anyway(self):
        self.ensure_one()
        if not self.production_id:
            raise UserError(_('أمر التشغيل غير موجود.'))
        return self.production_id.with_context(
            furniture_skip_empty_order_warning=True,
        ).action_confirm()


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    furniture_source_bom_id = fields.Many2one(
        'mrp.bom',
        string='نسخ من ريسيبي',
        copy=False,
        help='اختار ريسيبي قديم عشان ينسخ منه المقاسات وخامات مراحل التصنيع.',
    )
    furniture_product_id = fields.Many2one(
        'product.product',
        string='المنتج المصنّع',
        domain="[('type', 'in', ['consu', 'product']), ('furniture_dimension_source_product_id', '=', False)]",
        help='اختار المنتج اللي الوصفة دي بتصنعه، زي كنبة صغيرة أو كنبة كبيرة أو كرسي فوتيه.',
    )
    furniture_width_cm = fields.Float(string='العرض (سم)')
    furniture_depth_cm = fields.Float(string='العمق (سم)')
    furniture_height_cm = fields.Float(string='الارتفاع (سم)')
    use_priming = fields.Boolean(string='التقديم', default=True)
    use_painting = fields.Boolean(string='تصنيع دهانات', default=True)
    use_carpentry = fields.Boolean(string='تجميع', default=True)
    use_bases = fields.Boolean(string='القواعد', default=True)
    use_finishing = fields.Boolean(string='تجهيز', default=True)
    use_tailoring = fields.Boolean(string='تفصيل', default=True)
    use_sewing = fields.Boolean(
        string='الخياطة (مرحلة مستقلة قديمة)',
        default=False,
        help='حقل توافق فقط؛ خامات الخياطة أصبحت ضمن مرحلة التفصيل.',
    )
    use_upholstery = fields.Boolean(string='كسوه', default=True)
    use_packaging = fields.Boolean(string='التغليف', default=True)
    furniture_stage_ids = fields.One2many(
        'furniture.mrp.bom.stage',
        'bom_id',
        string='مراحل التصنيع',
        copy=True,
    )
    furniture_stage_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات مراحل التصنيع',
        copy=True,
    )
    priming_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات التقديم',
        domain=[('stage', '=', 'priming')],
        copy=True,
    )
    painting_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات تصنيع دهانات',
        domain=[('stage', '=', 'painting')],
        copy=True,
    )
    carpentry_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات تجميع',
        domain=[('stage', '=', 'carpentry')],
        copy=True,
    )
    bases_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات القواعد',
        domain=[('stage', '=', 'bases')],
        copy=True,
    )
    finishing_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات تجهيز',
        domain=[('stage', '=', 'finishing')],
        copy=True,
    )
    tailoring_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات تفصيل',
        domain=[('stage', '=', 'tailoring')],
        copy=True,
    )
    sewing_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات الخياطة',
        domain=[('stage', '=', 'sewing')],
        copy=True,
    )
    upholstery_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات كسوه',
        domain=[('stage', '=', 'upholstery')],
        copy=True,
    )
    packaging_material_line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'bom_id',
        string='خامات التغليف',
        domain=[('stage', '=', 'packaging')],
        copy=True,
    )

    def _clean_stage_display_name(self, name):
        name = ' '.join((name or '').split()).strip()
        if not name:
            return name
        suffix_pattern = re.compile(r'(?:\s+(?:تم\s+)?مرحلة\s+\d+\s+من\s+\d+)+\s*$')
        copy_suffix_pattern = re.compile(r'\s*\(new\)\s*\d*\s*$')
        while True:
            cleaned = suffix_pattern.sub('', name).strip()
            cleaned = copy_suffix_pattern.sub('', cleaned).strip()
            cleaned = ' '.join(cleaned.split())
            if cleaned == name:
                return cleaned
            name = cleaned

    def _get_copy_base_name(self, source_bom):
        source_bom.ensure_one()
        base_name = (
            source_bom.furniture_product_id.name
            or source_bom.product_tmpl_id.display_name
            or source_bom.code
            or _('ريسيبي')
        )
        return self._clean_copy_name(self._clean_stage_display_name(base_name))

    def _clean_copy_name(self, name):
        name = (name or '').replace('(new)', '').strip()
        name = ' '.join(name.split())
        if name.rsplit(' ', 1)[-1].isdigit():
            name = name.rsplit(' ', 1)[0].strip()
        return name or _('ريسيبي')

    def _get_copy_number(self, name, base_name):
        name = (name or '').replace('(new)', '').strip()
        name = ' '.join(name.split())
        if name == base_name:
            return 1
        if name.startswith(base_name + ' '):
            suffix = name[len(base_name) + 1:].strip()
            if suffix.isdigit():
                return int(suffix)
        return False

    def _get_next_copy_code(self, source_bom):
        base_name = self._get_copy_base_name(source_bom)
        existing_boms = self.search([
            '|', '|',
            ('code', '=ilike', '%s%%' % base_name),
            ('furniture_product_id.name', '=ilike', '%s%%' % base_name),
            ('product_tmpl_id.name', '=ilike', '%s%%' % base_name),
            ('id', 'not in', self.ids or [0]),
        ])
        existing_names = existing_boms.mapped('code')
        existing_names += existing_boms.mapped('furniture_product_id.name')
        existing_names += existing_boms.mapped('product_tmpl_id.display_name')
        used_numbers = {1}
        for name in existing_names:
            number = self._get_copy_number(name, base_name)
            if number:
                used_numbers.add(number)
        next_number = 2
        while next_number in used_numbers:
            next_number += 1
        return '%s %s' % (base_name, next_number)

    def _get_existing_copy_code(self, source_bom):
        self.ensure_one()
        base_name = self._get_copy_base_name(source_bom)
        code = (self.code or '').strip()
        if self._get_copy_number(code, base_name):
            return code
        return False

    def _get_or_create_copy_product(self, source_bom, copy_name):
        source_bom.ensure_one()
        source_product = source_bom.furniture_product_id or source_bom.product_id
        if not source_product and source_bom.product_tmpl_id:
            source_product = source_bom.product_tmpl_id.product_variant_id
        if not source_product:
            return False
        Product = self.env['product.product'].with_context(active_test=False)
        existing_product = Product.search([('name', '=', copy_name)], limit=1)
        if existing_product:
            return existing_product
        return source_product.copy({
            'name': copy_name,
            'default_code': False,
        })

    def _iter_source_material_values(self, source_bom):
        source_bom.ensure_one()
        stage_lines = source_bom.furniture_stage_material_line_ids.sorted(lambda l: (l.sequence, l.id))
        if stage_lines:
            for line in stage_lines:
                yield {
                    'sequence': line.sequence,
                    'stage': 'tailoring' if line.stage == 'sewing' else line.stage,
                    'product_id': line.product_id.id,
                    'product_qty': line.product_qty,
                    'quantity_mode': line.quantity_mode,
                    'product_uom_code': line.product_uom_code,
                    'product_uom_id': line.product_uom_id.id,
                    **({
                        'furniture_tailoring_material_kind':
                            line.furniture_tailoring_material_kind,
                    } if (
                        'furniture_tailoring_material_kind' in line._fields
                        and line.furniture_tailoring_material_kind
                    ) else {}),
                }
            return

        grouped_stage_lines = source_bom.furniture_stage_ids.mapped('line_ids').sorted(lambda l: (l.sequence, l.id))
        if grouped_stage_lines:
            for line in grouped_stage_lines:
                yield {
                    'sequence': line.sequence,
                    'stage': (
                        'tailoring'
                        if (line.stage or line.stage_id.stage) == 'sewing'
                        else (line.stage or line.stage_id.stage)
                    ),
                    'product_id': line.product_id.id,
                    'product_qty': line.product_qty,
                    'quantity_mode': line.quantity_mode,
                    'product_uom_code': line.product_uom_code,
                    'product_uom_id': line.product_uom_id.id,
                    **({
                        'furniture_tailoring_material_kind':
                            line.furniture_tailoring_material_kind,
                    } if (
                        'furniture_tailoring_material_kind' in line._fields
                        and line.furniture_tailoring_material_kind
                    ) else {}),
                }
            return

        for line in source_bom.bom_line_ids.sorted(lambda l: (l.sequence, l.id)):
            yield {
                'sequence': line.sequence,
                'stage': 'tailoring' if line.furniture_stage == 'sewing' else line.furniture_stage,
                'product_id': line.product_id.id,
                'product_qty': line.product_qty,
                'quantity_mode': line.furniture_quantity_mode,
                'product_uom_code': False,
                'product_uom_id': line.product_uom_id.id,
            }

    def _get_stage_material_commands_from_source(self, source_bom):
        commands = [(5, 0, 0)]
        for vals in self._iter_source_material_values(source_bom):
            commands.append((0, 0, vals))
        return commands

    def _get_stage_material_commands_by_field(self, source_bom):
        field_by_stage = {
            'priming': 'priming_material_line_ids',
            'painting': 'painting_material_line_ids',
            'carpentry': 'carpentry_material_line_ids',
            'bases': 'bases_material_line_ids',
            'finishing': 'finishing_material_line_ids',
            'tailoring': 'tailoring_material_line_ids',
            'upholstery': 'upholstery_material_line_ids',
            'packaging': 'packaging_material_line_ids',
        }
        commands_by_field = {
            field_name: []
            for field_name in field_by_stage.values()
        }
        commands_by_field['priming_material_line_ids'].append((5, 0, 0))
        for vals in self._iter_source_material_values(source_bom):
            field_name = field_by_stage.get(vals.get('stage'))
            if not field_name:
                continue
            commands_by_field[field_name].append((0, 0, vals))
        return commands_by_field

    def _get_source_bom_copy_vals(self, source_bom, include_lines=True, split_stage_fields=False, copy_name=False):
        source_bom.ensure_one()
        copy_name = copy_name or self._get_next_copy_code(source_bom)
        # Model-specific recipes deliberately share the same neutral finished
        # product.  The model is the recipe discriminator; copying a recipe
        # must not create another product SKU such as "كنبة صغيرة 2".
        copy_product = source_bom.furniture_product_id or source_bom.product_id
        vals = {
            'code': copy_name,
            'furniture_product_id': copy_product.id if copy_product else source_bom.furniture_product_id.id,
            'furniture_family_id': source_bom.furniture_family_id.id,
            'product_tmpl_id': copy_product.product_tmpl_id.id if copy_product else source_bom.product_tmpl_id.id,
            'product_qty': source_bom.product_qty,
            'furniture_width_cm': source_bom.furniture_width_cm,
            'furniture_depth_cm': source_bom.furniture_depth_cm,
            'furniture_height_cm': source_bom.furniture_height_cm,
            'use_priming': source_bom.use_priming,
            'use_painting': source_bom.use_painting,
            'use_carpentry': source_bom.use_carpentry,
            'use_bases': source_bom.use_bases,
            'use_finishing': source_bom.use_finishing,
            'use_tailoring': source_bom.use_tailoring or source_bom.use_sewing,
            'use_sewing': False,
            'use_upholstery': source_bom.use_upholstery,
            'use_packaging': source_bom.use_packaging,
        }
        if source_bom.type == 'normal' and not source_bom.furniture_is_model_recipe:
            vals['furniture_recipe_model_id'] = source_bom.furniture_recipe_model_id.id
        else:
            vals['furniture_model_id'] = source_bom.furniture_model_id.id
        if source_bom.product_uom_id:
            vals['product_uom_id'] = source_bom.product_uom_id.id
        if include_lines:
            if split_stage_fields:
                vals.update(self._get_stage_material_commands_by_field(source_bom))
            else:
                vals['furniture_stage_material_line_ids'] = self._get_stage_material_commands_from_source(source_bom)
        return vals

    @api.onchange('furniture_source_bom_id')
    def _onchange_furniture_source_bom_id(self):
        for rec in self:
            source_bom = rec.furniture_source_bom_id
            if not source_bom:
                continue
            if rec._origin and rec._origin.id:
                continue
            source_product = source_bom.furniture_product_id or source_bom.product_id
            rec.furniture_product_id = source_product
            rec.product_tmpl_id = source_product.product_tmpl_id if source_product else source_bom.product_tmpl_id
            rec.product_qty = source_bom.product_qty
            rec.product_uom_id = source_bom.product_uom_id
            rec.furniture_width_cm = source_bom.furniture_width_cm
            rec.furniture_depth_cm = source_bom.furniture_depth_cm
            rec.furniture_height_cm = source_bom.furniture_height_cm
            rec.use_priming = source_bom.use_priming
            rec.use_painting = source_bom.use_painting
            rec.use_carpentry = source_bom.use_carpentry
            rec.use_bases = source_bom.use_bases
            rec.use_finishing = source_bom.use_finishing
            rec.use_tailoring = source_bom.use_tailoring or source_bom.use_sewing
            rec.use_sewing = False
            rec.use_upholstery = source_bom.use_upholstery
            rec.use_packaging = source_bom.use_packaging

    def _get_active_stage_codes(self):
        self.ensure_one()
        if self.type == 'phantom':
            return []
        active_stages = []
        for stage_code, (_use_field, _order_field, _state_field) in FURNITURE_STAGE_FIELD_MAP.items():
            if self[_use_field]:
                active_stages.append(stage_code)
        return active_stages

    def action_autocopy_source_bom(self):
        for rec in self:
            if rec.furniture_source_bom_id:
                copy_vals = rec._get_source_bom_copy_vals(rec.furniture_source_bom_id)
                copy_vals['furniture_source_bom_id'] = rec.furniture_source_bom_id.id
                rec.with_context(furniture_skip_source_autocopy=True).write(copy_vals)
        return True

    @api.onchange('furniture_product_id')
    def _onchange_furniture_product_id(self):
        for rec in self:
            if rec.furniture_product_id:
                rec.product_tmpl_id = rec.furniture_product_id.product_tmpl_id

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id_furniture_product(self):
        for rec in self:
            if rec.product_tmpl_id and (
                not rec.furniture_product_id
                or rec.furniture_product_id.product_tmpl_id != rec.product_tmpl_id
            ):
                variant = rec.product_tmpl_id.product_variant_id
                if variant:
                    rec.furniture_product_id = variant

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if vals.get('use_sewing'):
                vals['use_tailoring'] = True
            vals['use_sewing'] = False
            source_bom = self.browse(vals.get('furniture_source_bom_id')) if vals.get('furniture_source_bom_id') else False
            if source_bom:
                copy_vals = self._get_source_bom_copy_vals(
                    source_bom,
                    include_lines=not vals.get('furniture_stage_material_line_ids'),
                    copy_name=vals.get('code'),
                )
                copy_vals['furniture_source_bom_id'] = source_bom.id
                for key, value in vals.items():
                    if key in ('code', 'furniture_product_id', 'product_tmpl_id') and not value:
                        continue
                    copy_vals[key] = value
                vals = copy_vals
            if vals.get('furniture_product_id') and not vals.get('product_tmpl_id'):
                product = self.env['product.product'].browse(vals['furniture_product_id'])
                vals['product_tmpl_id'] = product.product_tmpl_id.id
            prepared_vals_list.append(vals)
        return super().create(prepared_vals_list)

    def write(self, vals):
        vals = dict(vals)
        if vals.get('use_sewing'):
            vals['use_tailoring'] = True
        if 'use_sewing' in vals:
            vals['use_sewing'] = False
        if vals.get('furniture_source_bom_id') and not self.env.context.get('furniture_skip_source_autocopy'):
            if len(self) > 1:
                for rec in self:
                    rec.write(vals)
                return True
            source_bom = self.browse(vals['furniture_source_bom_id'])
            copy_vals = self._get_source_bom_copy_vals(source_bom, copy_name=vals.get('code'))
            copy_vals['furniture_source_bom_id'] = source_bom.id
            for key, value in vals.items():
                if key in ('code', 'furniture_product_id', 'product_tmpl_id') and not value:
                    continue
                copy_vals[key] = value
            vals = copy_vals
        if vals.get('furniture_product_id') and not vals.get('product_tmpl_id'):
            product = self.env['product.product'].browse(vals['furniture_product_id'])
            vals = dict(vals, product_tmpl_id=product.product_tmpl_id.id)
        return super().write(vals)


class FurnitureMrpBomStage(models.Model):
    _name = 'furniture.mrp.bom.stage'
    _description = 'مرحلة تصنيع داخل BoM'
    _order = 'sequence, id'

    bom_id = fields.Many2one('mrp.bom', string='BoM', required=True, ondelete='cascade')
    sequence = fields.Integer(string='الترتيب', default=10)
    stage = fields.Selection(FURNITURE_STAGE_SELECTION, string='مرحلة التصنيع', required=True)
    line_ids = fields.One2many(
        'furniture.mrp.bom.stage.line',
        'stage_id',
        string='خامات المرحلة',
        copy=True,
    )


class FurnitureMrpBomStageLine(models.Model):
    _name = 'furniture.mrp.bom.stage.line'
    _description = 'خامات مرحلة تصنيع داخل BoM'
    _order = 'sequence, id'

    stage_id = fields.Many2one(
        'furniture.mrp.bom.stage',
        string='مرحلة التصنيع',
        ondelete='cascade',
    )
    bom_id = fields.Many2one('mrp.bom', string='BoM', ondelete='cascade')
    sequence = fields.Integer(string='الترتيب', default=10)
    stage = fields.Selection(FURNITURE_STAGE_SELECTION, string='مرحلة التصنيع', required=True)
    product_id = fields.Many2one(
        'product.product',
        string='الخامة',
        required=True,
        domain=[('furniture_is_finished_product', '=', False)],
    )
    product_uom_code = fields.Selection(
        FURNITURE_STAGE_UOM_SELECTION,
        string='وحدة القياس',
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False),
    )
    product_uom_category_id = fields.Many2one(
        'uom.category',
        related='product_id.uom_id.category_id',
    )
    product_qty = fields.Float(string='الكمية', default=1.0, required=True, digits=(16, 6))
    currency_id = fields.Many2one(
        'res.currency',
        string='العملة',
        compute='_compute_recipe_quantity_cost',
    )
    recipe_quantity_cost = fields.Monetary(
        string='تكلفة الكمية',
        currency_field='currency_id',
        compute='_compute_recipe_quantity_cost',
    )
    quantity_mode = fields.Selection(
        FURNITURE_MATERIAL_QUANTITY_MODE_SELECTION,
        string='حساب الكمية',
        required=True,
        default='scaled',
        help='نسبي مع المقاس يضرب الكمية في معامل المقاس، وكمية ثابتة تتغير مع عدد القطع فقط.',
    )

    @api.depends(
        'bom_id.company_id',
        'stage_id.bom_id.company_id',
        'product_id',
        'product_id.standard_price',
        'product_qty',
        'product_uom_id',
    )
    @api.depends_context('company')
    def _compute_recipe_quantity_cost(self):
        for line in self:
            bom = line.bom_id or line.stage_id.bom_id
            company = bom.company_id or self.env.company
            line.currency_id = company.currency_id
            if not line.product_id or not line.product_uom_id:
                line.recipe_quantity_cost = 0.0
                continue

            product = line.product_id.with_company(company)
            quantity_in_product_uom = line.product_uom_id._compute_quantity(
                line.product_qty,
                product.uom_id,
                round=False,
            )
            line.recipe_quantity_cost = (
                (product.standard_price or 0.0) * quantity_in_product_uom
            )

    def _get_required_quantity(self, output_qty, dimension_factor=1.0):
        self.ensure_one()
        factor = dimension_factor if self.quantity_mode == 'scaled' else 1.0
        return (self.product_qty or 0.0) * (output_qty or 0.0) * (factor or 1.0)

    def _get_stage_uom(self, code, product=False):
        xmlid = FURNITURE_STAGE_UOM_XMLIDS.get(code or 'unit')
        uom = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else False
        if product and uom and uom.category_id != product.uom_id.category_id:
            return product.uom_id
        return uom

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.product_uom_id = rec._get_stage_uom(rec.product_uom_code, rec.product_id) or rec.product_id.uom_id

    @api.constrains('product_id', 'bom_id', 'stage_id')
    def _check_product_is_raw_material(self):
        for rec in self:
            bom = rec.bom_id or rec.stage_id.bom_id
            is_current_finished_product = bool(
                bom and bom.furniture_product_id == rec.product_id
            )
            if rec.product_id and (
                rec.product_id.furniture_is_finished_product
                or is_current_finished_product
            ):
                raise ValidationError(
                    _('%s منتج نهائي ولا يمكن إضافته كخامة في مراحل التصنيع.')
                    % rec.product_id.display_name
                )

    @api.onchange('product_uom_code')
    def _onchange_product_uom_code(self):
        for rec in self:
            rec.product_uom_id = rec._get_stage_uom(rec.product_uom_code, rec.product_id) or rec.product_uom_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            product = self.env['product.product'].browse(vals['product_id']) if vals.get('product_id') else False
            uom = self._get_stage_uom(vals.get('product_uom_code'), product)
            if uom:
                vals['product_uom_id'] = uom.id
            elif product and not vals.get('product_uom_id'):
                vals['product_uom_id'] = product.uom_id.id
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'product_uom_code' in vals:
            product = self.env['product.product'].browse(vals.get('product_id') or self.product_id.id)
            uom = self._get_stage_uom(vals.get('product_uom_code'), product)
            if uom:
                vals['product_uom_id'] = uom.id
        if vals.get('product_id') and not vals.get('product_uom_id'):
            product = self.env['product.product'].browse(vals['product_id'])
            vals['product_uom_id'] = product.uom_id.id
        return super().write(vals)

    @api.onchange('stage_id')
    def _onchange_stage_id(self):
        for rec in self:
            if rec.stage_id:
                rec.bom_id = rec.stage_id.bom_id
                rec.stage = rec.stage_id.stage


class FurnitureMrpProductStageProgress(models.TransientModel):
    _name = 'furniture.mrp.product.stage.progress'
    _description = 'تتبع مراحل تصنيع المنتج'
    _order = 'production_id desc, stage_sequence, id'

    product_id = fields.Many2one('product.product', string='المنتج', readonly=True)
    dimension_label = fields.Char(
        string='المقاس',
        related='product_id.furniture_dimension_label',
        readonly=True,
    )
    production_id = fields.Many2one('furniture.mrp.production', string='أمر الإنتاج الأصلي', readonly=True)
    production_line_id = fields.Many2one('furniture.mrp.production.line', string='سطر أمر الإنتاج', readonly=True)
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري (الشركة)',
        related='production_line_id.buyer_partner_id',
        readonly=True,
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        related='production_line_id.beneficiary_partner_id',
        readonly=True,
    )
    source_origin = fields.Char(string='المرجع', readonly=True)
    stage_sequence = fields.Integer(string='الترتيب', readonly=True)
    stage_code = fields.Selection(FURNITURE_STAGE_SELECTION, string='المرحلة', readonly=True)
    stage_label = fields.Char(string='اسم المرحلة', readonly=True)
    status = fields.Selection([
        ('done', 'منتهية'),
        ('in_progress', 'داخل المرحلة/في الصالة'),
        ('pending', 'لسه مدخلتش'),
    ], string='الحالة', readonly=True)
    qty = fields.Float(string='الكمية الموجودة', digits=(16, 3), readonly=True)
    location_id = fields.Many2one('stock.location', string='المكان الحالي', readonly=True)
    note = fields.Char(string='ملاحظة', readonly=True)

    @api.model
    def _stage_status_vals(self, production, product, stage_code, production_line=False):
        stage_order = production._stage_order_record(stage_code) if production else False
        storage_location = production._stage_storage_location(stage_code) if production else False
        work_location = production._stage_work_location(stage_code) if production else False
        stage_product = product
        storage_qty = production._stage_location_product_qty(storage_location, stage_product) if storage_location else 0.0
        work_qty = production._stage_location_product_qty(work_location, stage_product) if work_location else 0.0
        completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data') if stage_order else self.env['furniture.mrp.production.line']
        active_lines = stage_order._get_stage_line_ids_data('active_production_line_ids_data') if stage_order else self.env['furniture.mrp.production.line']
        quality_lines = stage_order._get_stage_line_ids_data('quality_production_line_ids_data') if stage_order else self.env['furniture.mrp.production.line']

        if production_line and production_line in completed_lines:
            return ('done', storage_qty, storage_location, _('اتقبلت جودة في أمر المرحلة.'))
        if float_compare(storage_qty, 0.0, precision_digits=3) > 0:
            return ('done', storage_qty, storage_location, _('موجودة في مخزن المرحلة.'))
        if production_line and production_line in (active_lines | quality_lines):
            return ('in_progress', work_qty, work_location, _('داخل صالة المرحلة أو تحت فحص الجودة.'))
        if float_compare(work_qty, 0.0, precision_digits=3) > 0:
            return ('in_progress', work_qty, work_location, _('موجودة في صالة المرحلة.'))
        if stage_order and stage_order.state == 'done' and not production_line:
            return ('done', 0.0, storage_location, _('أمر المرحلة منتهي.'))
        return ('pending', 0.0, False, _('لم تدخل هذه المرحلة بعد.'))

    @api.model
    def _build_progress_vals(self, product, production=False, production_line=False):
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        stage_index = {stage_code: index for index, (stage_code, _label) in enumerate(FURNITURE_STAGE_SELECTION, start=1)}
        route_codes = (
            production._stage_codes_for_product(product, production_line=production_line)
            if production else []
        )
        if not route_codes and product:
            furniture_model = (
                production_line.furniture_order_model_id
                if production_line else product.furniture_model_id
            )
            bom = self.env['mrp.bom']._find_furniture_normal_recipe(
                product,
                furniture_model,
                company=production.company_id if production else self.env.company,
            )
            route_codes = bom._get_active_stage_codes() if bom else []
        vals_list = []
        for stage_code in route_codes:
            status, qty, location, note = self._stage_status_vals(production, product, stage_code, production_line) if production else ('pending', 0.0, False, _('حسب ريسيبي المنتج.'))
            vals_list.append({
                'product_id': product.id,
                'production_id': production.id if production else False,
                'production_line_id': production_line.id if production_line else False,
                'source_origin': production.name if production else False,
                'stage_sequence': stage_index.get(stage_code, 99),
                'stage_code': stage_code,
                'stage_label': stage_labels.get(stage_code, stage_code),
                'status': status,
                'qty': qty,
                'location_id': location.id if location else False,
                'note': note,
            })
        return vals_list

    @api.model
    def _open_for_product(self, product, production=False, production_line=False):
        if not product:
            raise UserError(_('لا يوجد منتج لعرض مراحل التصنيع.'))
        Production = self.env['furniture.mrp.production'].sudo()
        productions = Production
        if production:
            productions |= production
        if not productions:
            productions |= Production.search([
                '|',
                ('product_id', '=', product.id),
                ('production_line_ids.product_id', '=', product.id),
            ], order='id desc', limit=10)
            move_origins = self.env['stock.move'].sudo().search([
                ('product_id', '=', product.id),
                ('origin', '!=', False),
                ('state', '=', 'done'),
            ], order='date desc, id desc', limit=50).mapped('origin')
            if move_origins:
                productions |= Production.search([('name', 'in', list(set(move_origins)))], limit=10)

        vals_list = []
        if production_line:
            vals_list += self._build_progress_vals(product, production=production_line.production_id, production_line=production_line)
        else:
            for prod in productions:
                matching_lines = prod.production_line_ids.filtered(lambda line: prod._production_line_matches_product(line, product))
                if matching_lines:
                    for line in matching_lines:
                        vals_list += self._build_progress_vals(product, production=prod, production_line=line)
                else:
                    vals_list += self._build_progress_vals(product, production=prod)
        if not vals_list:
            vals_list = self._build_progress_vals(product)
        lines = self.create(vals_list)
        return {
            'type': 'ir.actions.act_window',
            'name': _('مراحل تصنيع %s') % product.display_name,
            'res_model': 'furniture.mrp.product.stage.progress',
            'view_mode': 'list,form',
            'target': 'new',
            'domain': [('id', 'in', lines.ids)],
            'context': {'create': False, 'edit': False, 'delete': False},
        }


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    furniture_supplier_id = fields.Many2one(
        'res.partner',
        string='مورد خامة التصنيع',
        domain=[('supplier_rank', '>', 0)],
        help='المورد الذي سيتم إنشاء طلب شراء له عند نقص هذه الخامة في أمر التشغيل الأسبوعي.',
    )
    furniture_display_name = fields.Char(
        string='اسم المنتج',
        compute='_compute_furniture_display_name',
    )
    furniture_dimension_label = fields.Char(
        string='المقاس',
        compute='_compute_furniture_dimension_label',
    )
    furniture_has_manufacturing_progress = fields.Boolean(
        string='له مراحل تصنيع مفتوحة',
        compute='_compute_furniture_manufacturing_progress',
    )
    furniture_manufacturing_progress_html = fields.Html(
        string='Manufacturing',
        compute='_compute_furniture_manufacturing_progress',
        sanitize=False,
    )

    def action_view_furniture_stage_progress(self):
        self.ensure_one()
        product = self.product_variant_id or self.product_variant_ids[:1]
        return self.env['furniture.mrp.product.stage.progress']._open_for_product(product)

    @api.depends_context(
        'active_id',
        'active_ids',
        'active_model',
        'production_id',
        'furniture_source_production_id',
        'furniture_source_production_line_id',
    )
    def _compute_furniture_manufacturing_progress(self):
        for template in self:
            sections = []
            for product in template.product_variant_ids:
                section = product._build_furniture_manufacturing_progress_html()
                if section:
                    sections.append(section)
            template.furniture_has_manufacturing_progress = bool(sections)
            template.furniture_manufacturing_progress_html = ''.join(sections) if sections else False

    @api.depends('name')
    def _compute_furniture_display_name(self):
        for template in self:
            template.furniture_display_name = template.name or ''

    @api.depends('product_variant_ids.furniture_dimension_label')
    def _compute_furniture_dimension_label(self):
        for template in self:
            labels = template.product_variant_ids.mapped('furniture_dimension_label')
            template.furniture_dimension_label = labels[0] if labels else False


class ProductProduct(models.Model):
    _inherit = 'product.product'

    furniture_dimension_label = fields.Char(
        string='المقاس',
        copy=True,
        index=True,
    )
    furniture_dimension_source_product_id = fields.Many2one(
        'product.product',
        string='المنتج الأساسي للمقاس',
        copy=False,
        index=True,
        ondelete='set null',
    )
    furniture_display_name = fields.Char(
        string='اسم المنتج',
        compute='_compute_furniture_display_name',
    )
    furniture_has_manufacturing_progress = fields.Boolean(
        string='له مراحل تصنيع مفتوحة',
        compute='_compute_furniture_manufacturing_progress',
    )
    furniture_manufacturing_progress_html = fields.Html(
        string='Manufacturing',
        compute='_compute_furniture_manufacturing_progress',
        sanitize=False,
    )

    def action_view_furniture_stage_progress(self):
        self.ensure_one()
        return self.env['furniture.mrp.product.stage.progress']._open_for_product(self)

    @api.depends_context(
        'active_id',
        'active_ids',
        'active_model',
        'production_id',
        'furniture_source_production_id',
        'furniture_source_production_line_id',
    )
    def _compute_furniture_manufacturing_progress(self):
        for product in self:
            html = product._build_furniture_manufacturing_progress_html()
            product.furniture_has_manufacturing_progress = bool(html)
            product.furniture_manufacturing_progress_html = html or False

    def _furniture_matches_production_line(self, line):
        self.ensure_one()
        if not line or not line.production_id:
            return False
        line_dimension = line.production_id._get_production_line_dimension_label(line)
        if self.furniture_dimension_label:
            source_product = self.furniture_dimension_source_product_id or self
            line_source_product = line.product_id.furniture_dimension_source_product_id or line.product_id
            return line_dimension == self.furniture_dimension_label and line_source_product == source_product
        if line_dimension:
            return False
        return line.product_id == self

    def _furniture_context_source_lines_for_manufacturing_progress(self):
        self.ensure_one()
        ctx = self.env.context
        Production = self.env['furniture.mrp.production'].sudo()
        ProductionLine = self.env['furniture.mrp.production.line'].sudo()

        line_ids = []
        for key in ('furniture_source_production_line_id', 'production_line_id', 'default_production_line_id'):
            value = ctx.get(key)
            if isinstance(value, int):
                line_ids.append(value)
        active_model = ctx.get('active_model')
        if active_model == 'furniture.mrp.production.line':
            active_ids = ctx.get('active_ids') or [ctx.get('active_id')]
            line_ids += [line_id for line_id in active_ids if isinstance(line_id, int)]

        lines = ProductionLine.browse(line_ids).exists().filtered(
            lambda line: self._furniture_matches_production_line(line)
        )

        production_ids = []
        for key in ('furniture_source_production_id', 'production_id'):
            value = ctx.get(key)
            if isinstance(value, int):
                production_ids.append(value)
        if active_model == 'furniture.mrp.production' and isinstance(ctx.get('active_id'), int):
            production_ids.append(ctx['active_id'])
        productions = Production.browse(production_ids).exists()
        for production in productions:
            lines |= production.production_line_ids.filtered(
                lambda line: line.first_stage_started and self._furniture_matches_production_line(line)
            )
            carryovers = production.carryover_line_ids.filtered(
                lambda line: line.product_id == self and line.state != 'finished_transferred'
            )
            for carryover in carryovers:
                source_line = carryover.source_production_line_id
                if not source_line and carryover.production_id:
                    source_line = carryover.production_id._find_stage_product_source_line(
                        self,
                        carryover.current_stage,
                        source_production=carryover.source_production_id,
                        final_only=False,
                    )
                if source_line and self._furniture_matches_production_line(source_line):
                    lines |= source_line
        return lines

    def _furniture_pick_current_source_line(self, source_lines):
        self.ensure_one()
        ProductionLine = self.env['furniture.mrp.production.line'].sudo()
        for source_line in source_lines.sorted(lambda line: line.id, reverse=True):
            rows, has_open_work = self._furniture_stage_progress_rows_for_line(source_line)
            if rows and has_open_work:
                return source_line
        return ProductionLine

    def _furniture_source_lines_for_manufacturing_progress(self):
        self.ensure_one()
        context_lines = self._furniture_context_source_lines_for_manufacturing_progress()
        current_line = self._furniture_pick_current_source_line(context_lines)
        if current_line:
            return current_line

        product_candidates = self
        if self.furniture_dimension_source_product_id:
            product_candidates |= self.furniture_dimension_source_product_id

        ProductionLine = self.env['furniture.mrp.production.line'].sudo()
        lines = ProductionLine.search([
            ('product_id', 'in', product_candidates.ids),
            ('first_stage_started', '=', True),
        ], order='id desc', limit=100).filtered(lambda line: self._furniture_matches_production_line(line))

        Carryover = self.env['furniture.mrp.carryover.line'].sudo()
        carryover_lines = Carryover.search([
            ('product_id', '=', self.id),
            ('state', '!=', 'finished_transferred'),
        ], order='id desc', limit=100)
        for carryover in carryover_lines:
            source_line = carryover.source_production_line_id
            if not source_line and carryover.production_id:
                source_line = carryover.production_id._find_stage_product_source_line(
                    self,
                    carryover.current_stage,
                    source_production=carryover.source_production_id,
                    final_only=False,
            )
            if source_line and self._furniture_matches_production_line(source_line):
                lines |= source_line
        current_line = self._furniture_pick_current_source_line(lines)
        return current_line

    def _furniture_carryover_lines_for_source_line(self, source_line):
        self.ensure_one()
        Carryover = self.env['furniture.mrp.carryover.line'].sudo()
        carryover_lines = Carryover.search([
            ('product_id', '=', self.id),
            ('state', '!=', 'finished_transferred'),
        ], order='id asc')
        matched = Carryover
        for carryover in carryover_lines:
            carryover_source_line = carryover.source_production_line_id
            if not carryover_source_line and carryover.production_id:
                carryover_source_line = carryover.production_id._find_stage_product_source_line(
                    self,
                    carryover.current_stage,
                    source_production=carryover.source_production_id,
                    final_only=False,
                )
            if carryover_source_line == source_line:
                matched |= carryover
        return matched

    def _furniture_stage_progress_rows_for_line(self, source_line):
        self.ensure_one()
        if not source_line or not source_line.production_id:
            return [], False
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        route_codes = source_line._selected_stage_codes()
        carryover_lines = self._furniture_carryover_lines_for_source_line(source_line)
        related_productions = source_line.production_id | carryover_lines.mapped('production_id')
        rows = []
        has_open_work = bool(carryover_lines)

        for stage_code in route_codes:
            completed = False
            in_progress = False
            notes = []
            locations = []
            qty = 0.0
            seen_storage_locations = set()
            seen_work_locations = set()

            for production in related_productions:
                stage_order = production._stage_order_record(stage_code)
                if stage_order:
                    completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
                    active_lines = stage_order._get_stage_line_ids_data('active_production_line_ids_data')
                    quality_lines = stage_order._get_stage_line_ids_data('quality_production_line_ids_data')
                    if source_line in completed_lines:
                        completed = True
                    if source_line in (active_lines | quality_lines):
                        in_progress = True
                        notes.append(_('داخل أمر مرحلة %s') % stage_order.name)

                storage_location = production._stage_storage_location(stage_code)
                if storage_location and storage_location.id not in seen_storage_locations:
                    seen_storage_locations.add(storage_location.id)
                    storage_qty = production._stage_location_product_qty(storage_location, self, production.company_id)
                    if float_compare(storage_qty, 0.0, precision_digits=3) > 0:
                        qty += storage_qty
                        locations.append(storage_location.display_name)

                work_location = production._stage_work_location(stage_code)
                if work_location and work_location.id not in seen_work_locations:
                    seen_work_locations.add(work_location.id)
                    work_qty = production._stage_location_product_qty(work_location, self, production.company_id)
                    if float_compare(work_qty, 0.0, precision_digits=3) > 0:
                        qty += work_qty
                        in_progress = True
                        locations.append(work_location.display_name)

            stage_carryovers = carryover_lines.filtered(lambda line: line.current_stage == stage_code)
            if stage_carryovers.filtered(lambda line: line.state == 'stage_done'):
                completed = True
            if stage_carryovers.filtered(lambda line: line.state in ('selected', 'started')):
                in_progress = True

            if float_compare(qty, 0.0, precision_digits=3) > 0:
                has_open_work = True
            if in_progress:
                has_open_work = True

            status = 'pending'
            status_label = _('لسه')
            status_class = 'background:#eef2f7;color:#334155;border:1px solid #cbd5e1;'
            if completed:
                status = 'done'
                status_label = _('خلصت')
                status_class = 'background:#dcfce7;color:#166534;border:1px solid #86efac;'
            elif in_progress:
                status = 'in_progress'
                status_label = _('شغالة')
                status_class = 'background:#fef3c7;color:#92400e;border:1px solid #facc15;'

            rows.append({
                'stage_code': stage_code,
                'stage_label': stage_labels.get(stage_code, stage_code),
                'status': status,
                'status_label': status_label,
                'status_class': status_class,
                'qty': qty,
                'locations': ', '.join(dict.fromkeys(locations)),
                'note': '، '.join(dict.fromkeys(notes)),
            })

        all_done = bool(rows) and all(row['status'] == 'done' for row in rows)
        if all_done and not any(float_compare(row['qty'], 0.0, precision_digits=3) > 0 for row in rows):
            has_open_work = False
        return rows, has_open_work

    def _build_furniture_manufacturing_progress_html(self):
        self.ensure_one()
        source_lines = self._furniture_source_lines_for_manufacturing_progress()
        sections = []
        for source_line in source_lines:
            rows, has_open_work = self._furniture_stage_progress_rows_for_line(source_line)
            if not has_open_work or not rows:
                continue
            done_count = len([row for row in rows if row['status'] == 'done'])
            total_count = len(rows)
            route_text = '، '.join(escape(row['stage_label']) for row in rows)
            dimension = self.furniture_dimension_label or source_line.dimension_label or ''
            title = escape(self.display_name or self.name or '')
            if dimension:
                title = '%s <span style="display:inline-flex;padding:2px 10px;border-radius:999px;background:#e0f2fe;color:#075985;border:1px solid #7dd3fc;font-weight:700;margin-inline-start:8px;">%s</span>' % (
                    title,
                    escape(dimension),
                )
            row_html = []
            for row in rows:
                row_html.append(
                    '<tr>'
                    '<td style="padding:10px 12px;border-bottom:1px solid #e5edf5;font-weight:700;color:#0f172a;">%s</td>'
                    '<td style="padding:10px 12px;border-bottom:1px solid #e5edf5;">'
                    '<span style="display:inline-flex;min-width:70px;justify-content:center;padding:4px 10px;border-radius:999px;font-weight:800;%s">%s</span>'
                    '</td>'
                    '<td style="padding:10px 12px;border-bottom:1px solid #e5edf5;color:#334155;">%s</td>'
                    '<td style="padding:10px 12px;border-bottom:1px solid #e5edf5;color:#64748b;">%s</td>'
                    '</tr>' % (
                        escape(row['stage_label']),
                        row['status_class'],
                        escape(row['status_label']),
                        escape(row['locations'] or '-'),
                        escape(row['note'] or '-'),
                    )
                )
            sections.append(
                '<section style="margin:0 0 16px 0;padding:18px;border-radius:18px;background:linear-gradient(135deg,#f8fafc 0%%,#ecfeff 100%%);border:1px solid #dbeafe;box-shadow:0 10px 28px rgba(15,23,42,.06);">'
                '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px;">'
                '<div>'
                '<div style="font-size:18px;font-weight:900;color:#0f172a;">%s</div>'
                '<div style="margin-top:6px;color:#475569;">أمر البداية: <b>%s</b> | الكمية: <b>%s</b></div>'
                '<div style="margin-top:6px;color:#64748b;">المسار المختار: %s</div>'
                '</div>'
                '<div style="white-space:nowrap;border-radius:999px;background:#0f766e;color:white;padding:7px 13px;font-weight:900;">%s / %s مراحل</div>'
                '</div>'
                '<table style="width:100%%;border-collapse:separate;border-spacing:0;background:white;border-radius:14px;overflow:hidden;border:1px solid #e2e8f0;">'
                '<thead><tr style="background:#f1f5f9;color:#334155;">'
                '<th style="text-align:start;padding:10px 12px;">المرحلة</th>'
                '<th style="text-align:start;padding:10px 12px;">الحالة</th>'
                '<th style="text-align:start;padding:10px 12px;">المكان</th>'
                '<th style="text-align:start;padding:10px 12px;">ملاحظة</th>'
                '</tr></thead><tbody>%s</tbody></table>'
                '</section>' % (
                    title,
                    escape(source_line.production_id.name or ''),
                    escape(str(source_line.product_qty or 0.0)),
                    route_text,
                    done_count,
                    total_count,
                    ''.join(row_html),
                )
            )
        if not sections:
            return False
        return '<div class="o_furniture_product_manufacturing_progress">%s</div>' % ''.join(sections)

    @api.depends('name', 'default_code', 'product_tmpl_id')
    @api.depends_context('display_default_code', 'seller_id', 'company_id', 'partner_id', 'lang', 'location', 'location_id', 'active_id', 'production_id', 'active_model')
    def _compute_furniture_display_name(self):
        for product in self:
            product.furniture_display_name = product.display_name

    @api.depends('name', 'default_code', 'product_tmpl_id')
    @api.depends_context('display_default_code', 'seller_id', 'company_id', 'partner_id', 'lang', 'location', 'location_id', 'active_id', 'production_id')
    def _compute_display_name(self):
        super()._compute_display_name()


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    furniture_stage = fields.Selection(FURNITURE_STAGE_SELECTION, string='قسم التصنيع')
    furniture_quantity_mode = fields.Selection(
        FURNITURE_MATERIAL_QUANTITY_MODE_SELECTION,
        string='حساب الكمية',
        required=True,
        default='scaled',
        help='نسبي مع المقاس يضرب الكمية في معامل المقاس، وكمية ثابتة تتغير مع عدد القطع فقط.',
    )
    furniture_component_family_id = fields.Many2one(
        related='product_id.furniture_family_id',
        string='الفئة الرئيسية',
        readonly=True,
    )
    furniture_component_model_id = fields.Many2one(
        related='product_id.furniture_model_id',
        string='الموديل',
        readonly=True,
    )
    furniture_finished_stock_qty = fields.Float(
        string='المتاح في المنتج التام',
        compute='_compute_furniture_finished_stock_qty',
        digits='Product Unit of Measure',
    )

    @api.depends('product_id')
    def _compute_furniture_finished_stock_qty(self):
        location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        for line in self:
            line.furniture_finished_stock_qty = (
                line.product_id.with_context(location=location.id).qty_available
                if line.product_id and location
                else 0.0
            )

    @api.model
    def _furniture_resolve_kit_component_product(
        self, selected_product, bom, create_if_missing=True,
    ):
        """Resolve a recipe product to the stock SKU of the Kit model.

        The selector deliberately shows the neutral products that own an active
        ``Manufacture this product`` BoM.  Phantom BoM moves, however, must keep
        the real model-specific SKU so sales, FIFO and COGS use the right stock.
        """
        if (
            not selected_product
            or not bom
            or bom.type != 'phantom'
            or not bom.furniture_model_id
        ):
            return selected_product

        model = bom.furniture_model_id
        base_product = (
            selected_product.furniture_dimension_source_product_id
            or selected_product
        )
        if (
            selected_product.furniture_model_id == model
            and (
                selected_product == base_product
                or selected_product.furniture_dimension_source_product_id == base_product
            )
        ):
            return selected_product

        Product = self.env['product.product'].with_context(active_test=False)
        model_product = Product.search([
            ('furniture_dimension_source_product_id', '=', base_product.id),
            ('furniture_model_id', '=', model.id),
            ('furniture_dimension_label', '=', False),
        ], order='active desc, id asc', limit=1)
        if model_product:
            if not model_product.active:
                model_product.sudo().write({'active': True})
            return model_product
        if not create_if_missing:
            return selected_product

        model_product = base_product.sudo().copy({
            'name': base_product.product_tmpl_id.name or base_product.name,
            'default_code': False,
            'active': True,
        })
        model_product.sudo().write({
            'furniture_dimension_label': False,
            'furniture_dimension_source_product_id': base_product.id,
        })
        model_product.product_tmpl_id.with_context(
            furniture_skip_bom_classification_sync=True,
        ).sudo().write({
            'furniture_family_id': model.family_id.id,
            'furniture_model_id': model.id,
        })
        return model_product

    @api.model
    def _furniture_prepare_kit_component_vals(self, vals, line=False):
        vals = dict(vals)
        bom = (
            self.env['mrp.bom'].browse(vals['bom_id']).exists()
            if vals.get('bom_id')
            else line.bom_id if line else False
        )
        product = (
            self.env['product.product'].browse(vals['product_id']).exists()
            if vals.get('product_id')
            else line.product_id if line else False
        )
        if not bom or bom.type != 'phantom' or not product:
            return vals
        resolved_product = self._furniture_resolve_kit_component_product(
            product, bom, create_if_missing=True,
        )
        if resolved_product:
            vals['product_id'] = resolved_product.id
            vals['product_uom_id'] = resolved_product.uom_id.id
        return vals

    @api.onchange('product_id', 'bom_id')
    def _onchange_furniture_kit_component_product(self):
        for line in self:
            if line.bom_id.type != 'phantom' or not line.product_id:
                continue
            resolved_product = line._furniture_resolve_kit_component_product(
                line.product_id,
                line.bom_id,
                create_if_missing=False,
            )
            if resolved_product and resolved_product != line.product_id:
                line.product_id = resolved_product
                line.product_uom_id = resolved_product.uom_id

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([
            self._furniture_prepare_kit_component_vals(vals)
            for vals in vals_list
        ])

    def write(self, vals):
        if len(self) <= 1:
            return super().write(
                self._furniture_prepare_kit_component_vals(vals, self[:1])
            )
        result = True
        for line in self:
            line_vals = line._furniture_prepare_kit_component_vals(vals, line)
            result = super(MrpBomLine, line).write(line_vals) and result
        return result

    def _furniture_sync_kit_component_products(self):
        for line in self.filtered(lambda rec: rec.bom_id.type == 'phantom' and rec.product_id):
            resolved_product = line._furniture_resolve_kit_component_product(
                line.product_id,
                line.bom_id,
                create_if_missing=True,
            )
            if resolved_product != line.product_id:
                super(MrpBomLine, line).write({
                    'product_id': resolved_product.id,
                    'product_uom_id': resolved_product.uom_id.id,
                })
        return True

    def _get_furniture_required_quantity(self, output_qty, dimension_factor=1.0):
        self.ensure_one()
        factor = dimension_factor if self.furniture_quantity_mode == 'scaled' else 1.0
        return (self.product_qty or 0.0) * (output_qty or 0.0) * (factor or 1.0)

    @api.constrains('bom_id', 'product_id')
    def _check_furniture_kit_components(self):
        for line in self:
            if line.bom_id.type != 'phantom' or not line.product_id:
                continue
            if not line.bom_id.furniture_model_match_key:
                raise ValidationError(_(
                    'حدد موديل الطقم أولًا قبل اختيار منتجاته.'
                ))
            if (
                line.product_id.furniture_model_match_key
                != line.bom_id.furniture_model_match_key
            ):
                raise ValidationError(_(
                    'المنتج %s لا يتبع موديل الطقم %s.'
                ) % (
                    line.product_id.display_name,
                    line.bom_id.furniture_model_id.display_name,
                ))
            kit_product = line.bom_id.furniture_product_id
            if kit_product and line.product_id == kit_product:
                raise ValidationError(_('لا يمكن إضافة الطقم نفسه كمكوّن داخل نفس الـ Kit.'))


class StockMove(models.Model):
    _inherit = 'stock.move'

    furniture_source_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر إنتاج الأثاث',
        ondelete='set null',
        index=True,
        copy=False,
    )
    furniture_source_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furniture_stock_move_source_line_rel',
        'move_id',
        'production_line_id',
        string='توزيعات سطور إنتاج الأثاث',
        copy=False,
        readonly=True,
        help='القطع الفنية التي تغطيها حركة مخزون مجمعة واحدة.',
    )
    furniture_actual_unit_cost = fields.Float(
        string='تكلفة وحدة دفعة الإنتاج الفعلية',
        digits=(16, 4),
        readonly=True,
        copy=False,
    )
    furniture_mrp_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        compute='_compute_furniture_mrp_info',
    )
    furniture_mrp_move_type = fields.Selection([
        ('wip_receipt', 'استلام تحت التشغيل'),
        ('raw_material', 'سحب خامات'),
        ('stage_transfer', 'تحويل بين المراحل'),
        ('final_consumption', 'استهلاك نهائي للخامات'),
        ('finished_product', 'تسليم منتج تام'),
    ], string='نوع حركة إنتاج الأثاث', compute='_compute_furniture_mrp_info')

    @api.model
    def _prepare_merge_moves_distinct_fields(self):
        fields_list = super()._prepare_merge_moves_distinct_fields()
        if 'furniture_source_production_line_id' not in fields_list:
            fields_list.append('furniture_source_production_line_id')
        return fields_list

    def _prepare_phantom_move_values(self, bom_line, product_qty, quantity_done):
        """Take sold furniture Kit components from finished goods only."""
        values = super()._prepare_phantom_move_values(
            bom_line,
            product_qty,
            quantity_done,
        )
        bom = bom_line.bom_id
        if (
            self.picking_type_id.code == 'outgoing'
            and bom.type == 'phantom'
            and bom.furniture_model_match_key
        ):
            finished_location = self.env.ref(
                'furniture_mrp.location_finished_goods',
                raise_if_not_found=False,
            )
            if finished_location:
                values['location_id'] = finished_location.id
        return values

    def _prepare_common_svl_vals(self):
        vals = super()._prepare_common_svl_vals()
        if self.furniture_source_production_line_id:
            vals['furniture_wip_origin_line_id'] = self.furniture_source_production_line_id.id
        return vals

    def _normalize_move_uom_vals(self, vals):
        vals = dict(vals)
        product = self.env['product.product'].browse(vals.get('product_id')) if vals.get('product_id') else self.product_id[:1]
        uom = self.env['uom.uom'].browse(vals.get('product_uom')) if vals.get('product_uom') else self.product_uom[:1]
        if product and not uom:
            vals['product_uom'] = product.uom_id.id
            return vals
        normalized_uom = _normalize_uom_for_product(product, uom)
        if normalized_uom and (vals.get('product_uom') or normalized_uom != uom):
            vals['product_uom'] = normalized_uom.id
        return vals

    def _normalize_furniture_kit_source_vals(self, vals):
        """Keep outgoing furniture Kit components inside finished goods."""
        vals = dict(vals)
        bom_line = self.env['mrp.bom.line'].browse(vals.get('bom_line_id')).exists()
        if not bom_line or not bom_line.bom_id.furniture_model_match_key:
            return vals
        picking = self.env['stock.picking'].browse(vals.get('picking_id')).exists()
        picking_type = (
            picking.picking_type_id
            or self.env['stock.picking.type'].browse(vals.get('picking_type_id')).exists()
        )
        destination = self.env['stock.location'].browse(vals.get('location_dest_id')).exists()
        if picking_type.code != 'outgoing' and destination.usage != 'customer':
            return vals
        finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        if finished_location:
            vals['location_id'] = finished_location.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [
            self._normalize_furniture_kit_source_vals(
                self._normalize_move_uom_vals(vals)
            )
            for vals in vals_list
        ]
        return super().create(vals_list)

    def write(self, vals):
        if len(self) <= 1:
            return super().write(self._normalize_move_uom_vals(vals))
        if not {'product_id', 'product_uom'}.intersection(vals):
            return super().write(vals)
        result = True
        for rec in self:
            result = super(StockMove, rec).write(rec._normalize_move_uom_vals(vals)) and result
        return result

    def _compute_furniture_mrp_info(self):
        Production = self.env['furniture.mrp.production'].sudo()
        for move in self:
            production = Production.search([('name', '=', move.origin)], limit=1) if move.origin else False
            move.furniture_mrp_production_id = production
            move.furniture_mrp_move_type = False
            if not production:
                continue
            if (
                move.id in production.material_line_ids.mapped('move_id').ids
                or 'استلام إنتاج فعلي' in (move.name or '')
            ):
                move.furniture_mrp_move_type = 'raw_material'
            elif 'استهلاك خامات' in (move.name or ''):
                move.furniture_mrp_move_type = 'final_consumption'
            elif 'استلام المنتج التام' in (move.name or ''):
                move.furniture_mrp_move_type = 'finished_product'
            elif 'استلام تحت التشغيل' in (move.name or ''):
                move.furniture_mrp_move_type = 'wip_receipt'
            else:
                move.furniture_mrp_move_type = 'stage_transfer'


class StockValuationLayer(models.Model):
    _inherit = 'stock.valuation.layer'

    furniture_wip_origin_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أصل كمية تحت التشغيل',
        ondelete='set null',
        index=True,
        copy=False,
        readonly=True,
    )
    furniture_finished_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر دفعة الإنتاج التامة',
        ondelete='set null',
        index=True,
        copy=False,
        readonly=True,
    )
    furniture_finished_move_id = fields.Many2one(
        'stock.move',
        string='حركة دخول المخزن التام',
        ondelete='set null',
        index=True,
        copy=False,
        readonly=True,
    )


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _normalize_move_line_uom_vals(self, vals):
        vals = dict(vals)
        product = self.env['product.product'].browse(vals.get('product_id')) if vals.get('product_id') else self.product_id[:1]
        uom = self.env['uom.uom'].browse(vals.get('product_uom_id')) if vals.get('product_uom_id') else self.product_uom_id[:1]
        if product and not uom:
            vals['product_uom_id'] = product.uom_id.id
            return vals
        normalized_uom = _normalize_uom_for_product(product, uom)
        if normalized_uom and (vals.get('product_uom_id') or normalized_uom != uom):
            vals['product_uom_id'] = normalized_uom.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._normalize_move_line_uom_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if len(self) <= 1:
            return super().write(self._normalize_move_line_uom_vals(vals))
        if not {'product_id', 'product_uom_id'}.intersection(vals):
            return super().write(vals)
        result = True
        for rec in self:
            result = super(StockMoveLine, rec).write(rec._normalize_move_line_uom_vals(vals)) and result
        return result


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    partner_id = fields.Many2one(
        'res.partner',
        string='Vendor',
        required=False,
        index=True,
        change_default=True,
        tracking=True,
        check_company=True,
        help="You can find a vendor by its Name, TIN, Email or Internal Reference.",
    )


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _furniture_purchase_uom_for_product(self, product, uom=False):
        return _normalize_purchase_uom_for_product(product, uom)

    def _furniture_normalize_uom_vals(self, vals):
        vals = dict(vals)
        if self.env.context.get('furniture_skip_purchase_uom_normalize'):
            return vals
        if vals.get('display_type') or (not vals.get('product_id') and self and self.display_type):
            return vals
        product = self.env['product.product'].browse(vals.get('product_id')) if vals.get('product_id') else self.product_id[:1]
        if not product:
            return vals
        uom = self.env['uom.uom'].browse(vals.get('product_uom')) if vals.get('product_uom') else self.product_uom[:1]
        normalized_uom = self._furniture_purchase_uom_for_product(product, uom)
        if normalized_uom and (not uom or normalized_uom != uom):
            vals['product_uom'] = normalized_uom.id
        return vals

    def _furniture_normalize_product_uom(self):
        for line in self:
            if not line.product_id or line.display_type:
                continue
            normalized_uom = line._furniture_purchase_uom_for_product(line.product_id, line.product_uom)
            if normalized_uom and normalized_uom != line.product_uom:
                super(PurchaseOrderLine, line.with_context(furniture_skip_purchase_uom_normalize=True)).write({
                    'product_uom': normalized_uom.id,
                })

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._furniture_normalize_uom_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('furniture_skip_purchase_uom_normalize'):
            return super().write(vals)
        if len(self) <= 1:
            return super().write(self._furniture_normalize_uom_vals(vals))
        result = True
        for rec in self:
            result = super(PurchaseOrderLine, rec).write(rec._furniture_normalize_uom_vals(vals)) and result
        return result


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _furniture_normalize_uom_vals(self, vals):
        vals = dict(vals)
        if self.env.context.get('furniture_skip_account_uom_normalize'):
            return vals
        if vals.get('display_type') or (not vals.get('product_id') and self and self.display_type):
            return vals
        product = self.env['product.product'].browse(vals.get('product_id')) if vals.get('product_id') else self.product_id[:1]
        if not product:
            return vals
        uom = self.env['uom.uom'].browse(vals.get('product_uom_id')) if vals.get('product_uom_id') else self.product_uom_id[:1]
        normalized_uom = _normalize_uom_for_product(product, uom)
        if normalized_uom and (not uom or normalized_uom != uom):
            vals['product_uom_id'] = normalized_uom.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._furniture_normalize_uom_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('furniture_skip_account_uom_normalize'):
            return super().write(vals)
        if len(self) <= 1:
            return super().write(self._furniture_normalize_uom_vals(vals))
        result = True
        for rec in self:
            result = super(AccountMoveLine, rec).write(rec._furniture_normalize_uom_vals(vals)) and result
        return result
