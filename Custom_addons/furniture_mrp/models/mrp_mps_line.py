# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.tools.float_utils import float_compare

from .mrp_production_order import FURNITURE_STAGE_SELECTION


class FurnitureMrpMPSLine(models.Model):
    _name = 'furniture.mrp.mps.line'
    _description = 'سطر جدول الإنتاج الرئيسي (MPS)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, id'
    _rec_name = 'product_id'

    mps_id = fields.Many2one(
        'furniture.mrp.mps',
        string='الخطة الأسبوعية',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(string='الترتيب', default=10)
    product_id = fields.Many2one(
        'product.product',
        string='المنتج',
        required=True,
        domain="[('furniture_has_active_normal_recipe', '=', True), ('furniture_dimension_source_product_id', '=', False)]",
        tracking=True,
    )
    furniture_order_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        tracking=True,
        copy=True,
        help='الموديل المطلوب إدخاله في خطة الإنتاج.',
    )
    product_template_id = fields.Many2one(
        'product.template',
        string='نموذج المنتج',
        related='product_id.product_tmpl_id',
        store=True,
    )
    product_qty = fields.Float(
        string='الكمية',
        default=1.0,
        required=True,
        tracking=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        related='product_id.uom_id',
        store=True,
        readonly=True,
    )
    bom_id = fields.Many2one(
        'mrp.bom',
        string='الريسيبي',
        domain="[('furniture_product_id', '=', product_id), ('furniture_model_id', '=', furniture_order_model_id), ('type', '=', 'normal')]",
        tracking=True,
    )
    bom_width_cm = fields.Float(
        string='عرض الريسيبي (سم)',
        related='bom_id.furniture_width_cm',
        readonly=True,
    )
    bom_depth_cm = fields.Float(
        string='عمق الريسيبي (سم)',
        related='bom_id.furniture_depth_cm',
        readonly=True,
    )
    bom_height_cm = fields.Float(
        string='ارتفاع الريسيبي (سم)',
        related='bom_id.furniture_height_cm',
        readonly=True,
    )
    width_cm = fields.Float(string='العرض (سم)', tracking=True)
    depth_cm = fields.Float(string='العمق (سم)', tracking=True)
    height_cm = fields.Float(string='الارتفاع (سم)', tracking=True)
    dimension_factor = fields.Float(
        string='معامل المقاس',
        compute='_compute_dimension_factor',
        digits=(16, 3),
    )
    dimension_label = fields.Char(
        string='المقاس',
        compute='_compute_dimension_label',
    )
    stage_count = fields.Integer(
        string='عدد المراحل',
        compute='_compute_stage_summary',
    )
    stage_summary = fields.Char(
        string='المراحل',
        compute='_compute_stage_summary',
    )
    production_order_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الناتج',
        copy=False,
        readonly=True,
        ondelete='set null',
    )
    production_state = fields.Selection(
        related='production_order_id.state',
        string='حالة أمر التشغيل',
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='الشركة',
        related='mps_id.company_id',
        store=True,
        readonly=True,
    )

    @api.depends('bom_width_cm', 'bom_depth_cm', 'bom_height_cm', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_factor(self):
        for rec in self:
            rec.dimension_factor = rec._get_dimension_factor()

    @api.depends('bom_id', 'bom_width_cm', 'bom_depth_cm', 'bom_height_cm', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_label(self):
        for rec in self:
            rec.dimension_label = rec._get_dimension_label()

    @api.depends('bom_id', 'bom_id.use_priming', 'bom_id.use_painting', 'bom_id.use_carpentry',
                 'bom_id.use_bases', 'bom_id.use_finishing', 'bom_id.use_tailoring',
                 'bom_id.use_upholstery', 'bom_id.use_packaging')
    def _compute_stage_summary(self):
        stage_label_map = dict(FURNITURE_STAGE_SELECTION)
        for rec in self:
            if not rec.bom_id:
                rec.stage_count = 0
                rec.stage_summary = False
                continue
            active_codes = [
                code
                for code in rec.bom_id._get_active_stage_codes()
                if code != 'sewing'
            ]
            rec.stage_count = len(active_codes)
            stage_labels = [stage_label_map.get(code, code) for code in active_codes]
            rec.stage_summary = '، '.join(stage_labels) if stage_labels else False

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

    def _get_dimension_label(self):
        self.ensure_one()
        if not self.bom_id:
            return False
        actual_dims = (self.width_cm or 0.0, self.depth_cm or 0.0, self.height_cm or 0.0)
        bom_dims = (self.bom_width_cm or 0.0, self.bom_depth_cm or 0.0, self.bom_height_cm or 0.0)
        if not any(
            float_compare(actual, base, precision_digits=3) != 0
            for actual, base in zip(actual_dims, bom_dims)
        ):
            return False
        return '×'.join(
            ('%.3f' % value).rstrip('0').rstrip('.') or '0'
            for value in actual_dims
        )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            rec.bom_id = False
            rec.furniture_order_model_id = False
            rec.width_cm = 0.0
            rec.depth_cm = 0.0
            rec.height_cm = 0.0
            if not rec.product_id:
                continue

    def _find_bom_for_product_model(self, product, model):
        return self.env['mrp.bom']._find_furniture_normal_recipe(
            product,
            model,
            company=self.env.company,
        )

    @api.onchange('furniture_order_model_id')
    def _onchange_furniture_order_model_id(self):
        for rec in self:
            rec.bom_id = False
            rec.width_cm = 0.0
            rec.depth_cm = 0.0
            rec.height_cm = 0.0
            if not rec.product_id or not rec.furniture_order_model_id:
                continue
            bom = rec._find_bom_for_product_model(
                rec.product_id,
                rec.furniture_order_model_id,
            )
            if not bom:
                rec.bom_id = False
                return {
                    'warning': {
                        'title': _('لا يوجد ريسيبي للمنتج'),
                        'message': _(
                            'أنشئ ريسيبي Manufacture this product للمنتج %(product)s والموديل %(model)s أولًا.'
                        ) % {
                            'product': rec.product_id.display_name,
                            'model': rec.furniture_order_model_id.display_name,
                        },
                    }
                }
            rec.bom_id = bom
            rec.width_cm = bom.furniture_width_cm
            rec.depth_cm = bom.furniture_depth_cm
            rec.height_cm = bom.furniture_height_cm

    @api.onchange('bom_id')
    def _onchange_bom_id(self):
        for rec in self:
            if rec.bom_id:
                rec.furniture_order_model_id = rec.bom_id.furniture_model_id
                rec.width_cm = rec.bom_id.furniture_width_cm
                rec.depth_cm = rec.bom_id.furniture_depth_cm
                rec.height_cm = rec.bom_id.furniture_height_cm
