# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class FurnitureMrpMPS(models.Model):
    _name = 'furniture.mrp.mps'
    _description = 'جدول الإنتاج الرئيسي (MPS)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, product_id'
    _rec_name = 'name'

    # ─── الحقول الأساسية ───────────────────────────────────────────────────────
    name = fields.Char(
        string='المرجع',
        readonly=True,
        default='جديد',
        copy=False,
        tracking=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='المنتج',
        domain="[('furniture_has_active_normal_recipe', '=', True), ('furniture_dimension_source_product_id', '=', False)]",
        tracking=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.mps.line',
        'mps_id',
        string='الأصناف',
    )
    line_count = fields.Integer(
        string='عدد الأصناف',
        compute='_compute_line_summary',
    )
    line_total_qty = fields.Float(
        string='إجمالي كميات الأصناف',
        compute='_compute_line_summary',
    )
    product_template_id = fields.Many2one(
        'product.template',
        string='نموذج المنتج',
        related='product_id.product_tmpl_id',
        store=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        related='product_id.uom_id',
        store=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='الشركة',
        default=lambda self: self.env.company,
        required=True,
    )
    responsible_id = fields.Many2one(
        'res.users',
        string='المسؤول',
        default=lambda self: self.env.user,
        tracking=True,
    )

    # ─── الفترة الزمنية (أسبوعية) ────────────────────────────────────────────
    date_from = fields.Date(
        string='بداية الأسبوع',
        required=True,
        tracking=True,
    )
    date_to = fields.Date(
        string='نهاية الأسبوع',
        compute='_compute_date_to',
        store=True,
        readonly=False,
        tracking=True,
    )

    # ─── الكميات ─────────────────────────────────────────────────────────────
    forecast_qty = fields.Float(
        string='الكمية المخططة للإنتاج',
        default=0.0,
        tracking=True,
    )
    demand_qty = fields.Float(
        string='الكمية المطلوبة (الطلب)',
        default=0.0,
        tracking=True,
    )
    stock_on_hand = fields.Float(
        string='المخزون الحالي',
        compute='_compute_stock',
        store=False,
        digits=(16, 2),
    )
    expected_stock = fields.Float(
        string='المخزون المتوقع',
        compute='_compute_expected_stock',
        store=False,
        digits=(16, 2),
    )

    # ─── الحالة ──────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'مسودة'),
        ('confirmed', 'مؤكد'),
        ('done', 'منتهي'),
        ('cancelled', 'ملغي'),
    ], string='الحالة', default='draft', tracking=True, index=True)

    priority = fields.Selection([
        ('0', 'عادي'),
        ('1', 'مهم'),
        ('2', 'عاجل'),
    ], string='الأولوية', default='0')

    # ─── أوامر التشغيل الأسبوعية المرتبطة ───────────────────────────────────
    production_order_ids = fields.One2many(
        'furniture.mrp.production',
        'mps_id',
        string='أوامر التشغيل الأسبوعية',
    )
    production_count = fields.Integer(
        string='عدد أوامر التشغيل الأسبوعية',
        compute='_compute_production_count',
    )
    produced_qty = fields.Float(
        string='الكمية المنتجة فعلياً',
        compute='_compute_produced_qty',
    )

    # ─── طاقة الأقسام ────────────────────────────────────────────────────────
    capacity_line_ids = fields.One2many(
        'furniture.mrp.mps.capacity',
        'mps_id',
        string='طاقة الأقسام',
    )

    notes = fields.Text(string='ملاحظات')
    color = fields.Integer(string='اللون', default=0)

    # ─── Computed ─────────────────────────────────────────────────────────────
    @api.depends('date_from')
    def _compute_date_to(self):
        for rec in self:
            if rec.date_from:
                rec.date_to = rec.date_from + timedelta(days=6)
            else:
                rec.date_to = False

    def _compute_stock(self):
        for rec in self:
            rec.stock_on_hand = rec.product_id.qty_available if rec.product_id else 0.0

    @api.depends('stock_on_hand', 'forecast_qty', 'demand_qty', 'line_ids', 'line_ids.product_qty')
    def _compute_expected_stock(self):
        for rec in self:
            rec.expected_stock = rec.stock_on_hand + rec._get_planned_qty() - rec.demand_qty

    @api.depends('line_ids', 'line_ids.product_qty')
    def _compute_line_summary(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.line_total_qty = sum(rec.line_ids.mapped('product_qty'))

    def _compute_production_count(self):
        for rec in self:
            rec.production_count = len(rec.production_order_ids)

    def _compute_produced_qty(self):
        for rec in self:
            done_orders = rec.production_order_ids.filtered(lambda o: o.state == 'done')
            rec.produced_qty = sum(done_orders.mapped('product_qty'))

    def _get_planned_qty(self):
        self.ensure_one()
        if self.line_ids:
            return sum(self.line_ids.mapped('product_qty'))
        return self.forecast_qty

    # ─── Constraints ──────────────────────────────────────────────────────────
    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_('تاريخ النهاية يجب أن يكون بعد تاريخ البداية.'))

    @api.constrains('forecast_qty', 'demand_qty')
    def _check_quantities(self):
        for rec in self:
            if rec.forecast_qty < 0:
                raise ValidationError(_('الكمية المخططة لا يمكن أن تكون سالبة.'))
            if rec.demand_qty < 0:
                raise ValidationError(_('الكمية المطلوبة لا يمكن أن تكون سالبة.'))

    # ─── ORM ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'furniture.mrp.mps'
                ) or 'جديد'
        return super().create(vals_list)

    # ─── الإجراءات ────────────────────────────────────────────────────────────
    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('يمكن تأكيد المسودات فقط.'))
            if rec.line_ids:
                planned_qty = rec._get_planned_qty()
            else:
                planned_qty = rec.forecast_qty
            if planned_qty <= 0:
                raise UserError(_('يجب تحديد الكمية المخططة قبل التأكيد.'))
            rec.state = 'confirmed'
            rec.message_post(body=_('✅ تم تأكيد جدول MPS'))

    def action_mark_done(self):
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(_('يجب تأكيد الجدول أولاً.'))
            rec.state = 'done'
            rec.message_post(body=_('🏁 تم إغلاق جدول MPS'))

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(_('لا يمكن إلغاء جدول منتهٍ.'))
            rec.state = 'cancelled'
            rec.message_post(body=_('❌ تم إلغاء جدول MPS'))

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError(_('لا يمكن إعادة هذا الجدول للمسودة.'))
            rec.state = 'draft'

    def action_generate_production(self):
        """توليد أمر تشغيل أسبوعي من جدول MPS"""
        self.ensure_one()
        if self.state not in ('confirmed',):
            raise UserError(_('يجب تأكيد الجدول أولاً قبل توليد أوامر التشغيل الأسبوعية.'))
        created_productions = self.env['furniture.mrp.production']
        reused_productions = self.env['furniture.mrp.production']

        if self.line_ids:
            for line in self.line_ids.sorted(lambda l: (l.sequence, l.id)):
                if not line.product_id:
                    raise UserError(_('يجب تحديد صنف لكل سطر في الخطة الأسبوعية.'))
                if line.product_qty <= 0:
                    continue
                if not line.furniture_order_model_id:
                    raise UserError(
                        _('يجب تحديد الموديل للصنف: %s') % line.product_id.display_name
                    )
                bom = self.env['mrp.bom']._find_furniture_normal_recipe(
                    line.product_id,
                    line.furniture_order_model_id,
                    company=self.company_id,
                )
                if not bom:
                    raise UserError(
                        _('لا توجد ريسيبي للصنف %(product)s مع الموديل %(model)s.') % {
                            'product': line.product_id.display_name,
                            'model': line.furniture_order_model_id.display_name,
                        }
                    )
                existing_production = line.production_order_id
                production_vals = {
                    'product_id': line.product_id.id,
                    'furniture_order_model_id': line.furniture_order_model_id.id,
                    'product_qty': line.product_qty,
                    'bom_id': bom.id,
                    'width_cm': line.width_cm or bom.furniture_width_cm,
                    'depth_cm': line.depth_cm or bom.furniture_depth_cm,
                    'height_cm': line.height_cm or bom.furniture_height_cm,
                    'date_planned_start': fields.Datetime.now(),
                    'mps_id': self.id,
                    'mps_line_id': line.id,
                    'notes': _('تم إنشاؤه تلقائياً من جدول MPS: %s / %s') % (
                        self.name, line.product_id.display_name,
                    ),
                }
                if existing_production and existing_production.state in ('draft', 'confirmed'):
                    existing_production.write(production_vals)
                    reused_productions |= existing_production
                    continue
                if existing_production and existing_production.state != 'cancelled':
                    reused_productions |= existing_production
                    continue
                production = self.env['furniture.mrp.production'].create(production_vals)
                line.production_order_id = production.id
                created_productions |= production
        else:
            if not self.product_id:
                raise UserError(_('يجب تحديد صنف أو إضافة أصناف داخل الخطة الأسبوعية.'))
            planned_qty = self._get_planned_qty()
            remaining = planned_qty - self.produced_qty
            if remaining <= 0:
                raise UserError(_('تم إنتاج الكمية المخططة بالكامل بالفعل.'))
            furniture_model = self.product_id.furniture_model_id
            bom = self.env['mrp.bom']._find_furniture_normal_recipe(
                self.product_id,
                furniture_model,
                company=self.company_id,
            )
            if not bom:
                raise UserError(_(
                    'أضف الصنف داخل سطور الخطة وحدد الموديل؛ لا يمكن اختيار ريسيبي من المنتج وحده.'
                ))
            production = self.env['furniture.mrp.production'].create({
                'product_id': self.product_id.id,
                'furniture_order_model_id': furniture_model.id,
                'product_qty': remaining,
                'bom_id': bom.id,
                'width_cm': bom.furniture_width_cm,
                'depth_cm': bom.furniture_depth_cm,
                'height_cm': bom.furniture_height_cm,
                'date_planned_start': fields.Datetime.now(),
                'mps_id': self.id,
                'notes': _('تم إنشاؤه تلقائياً من جدول MPS: %s') % self.name,
            })
            created_productions |= production

        all_productions = created_productions | reused_productions
        if not all_productions:
            raise UserError(_('لم يتم إنشاء أي أمر تشغيل لأن جميع السطور لديها أوامر مرتبطة بالفعل.'))

        if len(all_productions) == 1:
            production = all_productions[0]
            self.message_post(
                body=_('📋 تم إنشاء أمر التشغيل الأسبوعي: <b>%s</b> (الكمية: %s)') % (
                    production.name, production.product_qty,
                )
            )
            return {
                'type': 'ir.actions.act_window',
                'name': 'أمر التشغيل الأسبوعي',
                'res_model': 'furniture.mrp.production',
                'res_id': production.id,
                'view_mode': 'form',
                'target': 'current',
            }

        self.message_post(
            body=_('📋 تم إنشاء أو ربط %s أمر تشغيل أسبوعي من جدول MPS: <b>%s</b>') % (
                len(all_productions), self.name,
            )
        )
        return {
            'type': 'ir.actions.act_window',
            'name': 'أوامر التشغيل الأسبوعية',
            'res_model': 'furniture.mrp.production',
            'domain': [('id', 'in', all_productions.ids)],
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_view_productions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'أوامر التشغيل الأسبوعية',
            'res_model': 'furniture.mrp.production',
            'domain': [('mps_id', '=', self.id)],
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'أصناف الخطة الأسبوعية',
            'res_model': 'furniture.mrp.mps.line',
            'domain': [('mps_id', '=', self.id)],
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_initialize_capacity(self):
        """إنشاء سطور طاقة الأقسام الافتراضية"""
        self.ensure_one()
        departments = [
            ('priming',    'التقديم'),
            ('painting',   'تصنيع دهانات'),
            ('carpentry',  'تجميع'),
            ('bases',      'القواعد'),
            ('finishing',  'تجهيز'),
            ('tailoring',  'تفصيل'),
            ('upholstery', 'كسوه'),
            ('packaging',  'التغليف'),
        ]
        existing = self.capacity_line_ids.mapped('department')
        for dept_code, _dept_name in departments:
            if dept_code not in existing:
                self.env['furniture.mrp.mps.capacity'].create({
                    'mps_id': self.id,
                    'department': dept_code,
                    'planned_orders': 10,
                })
        return True


class FurnitureMrpMPSCapacity(models.Model):
    _name = 'furniture.mrp.mps.capacity'
    _description = 'طاقة أقسام MPS'
    _order = 'id'

    mps_id = fields.Many2one(
        'furniture.mrp.mps',
        string='جدول MPS',
        required=True,
        ondelete='cascade',
    )
    department = fields.Selection([
        ('priming',    '🎨 التقديم'),
        ('painting',   '🖌️ تصنيع دهانات'),
        ('carpentry',  '🪵 تجميع'),
        ('bases',      '🧱 القواعد'),
        ('finishing',  '🔧 تجهيز'),
        ('tailoring',  '✂️ تفصيل'),
        ('upholstery', '🛋️ كسوه'),
        ('packaging',  '📦 التغليف'),
    ], string='القسم', required=True)

    planned_orders = fields.Integer(
        string='الطاقة المخططة (عدد أوامر)',
        default=10,
    )
    actual_orders = fields.Integer(
        string='الأوامر الفعلية',
        compute='_compute_actual_orders',
        store=True,
    )
    capacity_pct = fields.Float(
        string='نسبة الاستخدام (%)',
        compute='_compute_capacity_pct',
        digits=(5, 1),
    )
    capacity_status = fields.Selection([
        ('ok', 'مقبول ✅'),
        ('warning', 'تحذير ⚠️'),
        ('overload', 'تجاوز ❌'),
    ], string='الحالة', compute='_compute_capacity_pct')

    # ─── Computed ─────────────────────────────────────────────────────────────
    @api.depends('mps_id', 'mps_id.production_order_ids', 'department')
    def _compute_actual_orders(self):
        dept_model_map = {
            'priming':    'priming_order_id',
            'painting':   'painting_order_id',
            'carpentry':  'carpentry_order_id',
            'bases':      'bases_order_id',
            'finishing':  'finishing_order_id',
            'tailoring':  'tailoring_order_id',
            'upholstery': 'upholstery_order_id',
            'packaging':  'packaging_order_id',
        }
        for rec in self:
            if not rec.mps_id:
                rec.actual_orders = 0
                continue
            field = dept_model_map.get(rec.department)
            if field:
                count = rec.mps_id.production_order_ids.filtered(
                    lambda o: o[field]
                )
                rec.actual_orders = len(count)
            else:
                rec.actual_orders = 0

    @api.depends('planned_orders', 'actual_orders')
    def _compute_capacity_pct(self):
        for rec in self:
            if rec.planned_orders:
                pct = (rec.actual_orders / rec.planned_orders) * 100
            else:
                pct = 0.0
            rec.capacity_pct = pct
            if pct >= 100:
                rec.capacity_status = 'overload'
            elif pct >= 80:
                rec.capacity_status = 'warning'
            else:
                rec.capacity_status = 'ok'
