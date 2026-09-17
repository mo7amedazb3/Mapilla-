# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class FurnitureMrpCosting(models.Model):
    """تقرير توزيع تكلفة الإنتاج - 3 مصادر"""
    _name = 'furniture.mrp.costing'
    _description = 'توزيع تكلفة الإنتاج'
    _inherit = ['mail.thread']
    _order = 'production_id desc'

    name = fields.Char(
        string='مرجع التكلفة', readonly=True, default='جديد', copy=False,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر التشغيل الأسبوعي',
        required=True, ondelete='cascade', tracking=True,
    )
    product_id    = fields.Many2one(related='production_id.product_id', store=True)
    product_qty   = fields.Float(related='production_id.product_qty', store=True)
    state = fields.Selection([
        ('draft',    'مسودة'),
        ('computed', 'محسوبة'),
        ('approved', 'معتمدة'),
    ], default='draft', tracking=True)

    # ─── المصدر 1: تكلفة المواد الخام ────────────────────────────────────────
    material_line_ids = fields.One2many(
        'furniture.mrp.costing.material', 'costing_id', string='تفاصيل المواد',
    )
    total_material_cost = fields.Float(
        string='إجمالي تكلفة المواد', compute='_compute_totals', store=True, digits=(16, 2),
    )

    # ─── المصدر 2: تكلفة العمالة (من MPS) ───────────────────────────────────
    labor_line_ids = fields.One2many(
        'furniture.mrp.costing.labor', 'costing_id', string='تفاصيل العمالة',
    )
    total_labor_cost = fields.Float(
        string='إجمالي تكلفة العمالة', compute='_compute_totals', store=True, digits=(16, 2),
    )

    # ─── المصدر 3: Overhead من الخزينة ───────────────────────────────────────
    analytic_account_id = fields.Many2one(
        related='production_id.analytic_account_id', store=True,
    )
    overhead_line_ids = fields.One2many(
        'furniture.mrp.costing.overhead', 'costing_id', string='تفاصيل Overhead',
    )
    total_overhead_cost = fields.Float(
        string='إجمالي Overhead', compute='_compute_totals', store=True, digits=(16, 2),
    )

    # ─── الإجماليات ───────────────────────────────────────────────────────────
    grand_total    = fields.Float(string='إجمالي التكلفة',     compute='_compute_totals', store=True, digits=(16, 2))
    cost_per_unit  = fields.Float(string='تكلفة الوحدة الأسبوعية', compute='_compute_totals', store=True, digits=(16, 2))
    material_pct   = fields.Float(string='نسبة المواد %',      compute='_compute_totals', store=True, digits=(5, 1))
    labor_pct      = fields.Float(string='نسبة العمالة %',     compute='_compute_totals', store=True, digits=(5, 1))
    overhead_pct   = fields.Float(string='نسبة Overhead %',    compute='_compute_totals', store=True, digits=(5, 1))

    @api.depends(
        'material_line_ids.total_cost',
        'labor_line_ids.total_cost',
        'overhead_line_ids.amount',
    )
    def _compute_totals(self):
        for rec in self:
            mat = sum(rec.material_line_ids.mapped('total_cost'))
            lab = sum(rec.labor_line_ids.mapped('total_cost'))
            ovh = sum(rec.overhead_line_ids.mapped('amount'))
            total = mat + lab + ovh
            rec.total_material_cost = mat
            rec.total_labor_cost    = lab
            rec.total_overhead_cost = ovh
            rec.grand_total         = total
            rec.cost_per_unit       = total / rec.product_qty if rec.product_qty else 0.0
            rec.material_pct  = (mat / total * 100) if total else 0.0
            rec.labor_pct     = (lab / total * 100) if total else 0.0
            rec.overhead_pct  = (ovh / total * 100) if total else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = self.env['ir.sequence'].next_by_code('furniture.mrp.costing') or 'جديد'
        return super().create(vals_list)

    def action_compute(self):
        """جلب البيانات تلقائياً من أمر التشغيل الأسبوعي"""
        self.ensure_one()
        prod = self.production_id
        if prod.state != 'done':
            raise UserError(_('يجب إنهاء أمر التشغيل الأسبوعي أولاً لحساب التكاليف الفعلية.'))

        # 1) مسح السطور القديمة
        self.material_line_ids.unlink()
        self.labor_line_ids.unlink()
        self.overhead_line_ids.unlink()

        # 2) المواد: من حركات المخزون المكتملة
        mat_lines = []
        material_moves = prod.stock_move_ids.filtered(
            lambda m: m.state == 'done' and m.furniture_mrp_move_type == 'raw_material'
        )
        for move in material_moves:
            unit_cost = prod._get_purchase_material_unit_cost(move.product_id, move.product_uom)
            mat_lines.append((0, 0, {
                'product_id': move.product_id.id,
                'quantity':   move.product_qty,
                'unit_cost':  unit_cost,
            }))
        self.material_line_ids = mat_lines

        # 3) العمالة: من ساعات أوامر الأقسام
        lab_lines = []
        stage_map = [
            (prod.priming_order_id,    '1 - التقديم'),
            (prod.painting_order_id,   '2 - تصنيع دهانات'),
            (prod.carpentry_order_id,  '3 - تجميع'),
            (prod.bases_order_id,      '4 - القواعد'),
            (prod.finishing_order_id,  '5 - تجهيز'),
            (prod.tailoring_order_id,  '6 - تفصيل'),
            (prod.sewing_order_id,     'سجل قديم - الخياطة المستقلة'),
            (prod.upholstery_order_id, '7 - كسوه'),
            (prod.packaging_order_id,  '8 - التغليف'),
        ]
        for stage_order, stage_name in stage_map:
            if stage_order and stage_order.labor_hours:
                active_cost = stage_order.labor_cost or 0.0
                lab_lines.append((0, 0, {
                    'stage_name': _('%s - تشغيل') % stage_name,
                    'hours':      stage_order.labor_hours,
                    'rate':       active_cost / stage_order.labor_hours,
                    'foreman_id': stage_order.foreman_id.id if stage_order.foreman_id else False,
                }))
            if stage_order and stage_order.waiting_labor_hours:
                lab_lines.append((0, 0, {
                    'stage_name': _('%s - انتظار') % stage_name,
                    'hours':      stage_order.waiting_labor_hours,
                    'rate':       stage_order.waiting_labor_cost / stage_order.waiting_labor_hours,
                    'foreman_id': stage_order.foreman_id.id if stage_order.foreman_id else False,
                }))
        self.labor_line_ids = lab_lines

        # 4) Overhead: من الحساب التحليلي
        ovh_lines = []
        if prod.analytic_account_id:
            analytic_lines = self.env['account.analytic.line'].search([
                ('account_id', '=', prod.analytic_account_id.id),
                ('amount', '<', 0),
            ])
            for al in analytic_lines:
                ovh_lines.append((0, 0, {
                    'description':   al.name or _('مصروف'),
                    'date':          al.date,
                    'amount':        abs(al.amount),
                    'analytic_line_id': al.id,
                }))
        self.overhead_line_ids = ovh_lines

        self.state = 'computed'
        self.message_post(body=_('✅ تم حساب التكاليف من مصادرها الثلاثة'))

    def action_approve(self):
        self.ensure_one()
        if self.state != 'computed':
            raise UserError(_('يجب حساب التكاليف أولاً.'))
        self.state = 'approved'
        self.message_post(body=_('✅ تم اعتماد تقرير التكاليف'))

    def action_reset_draft(self):
        self.state = 'draft'


class FurnitureMrpCostingMaterial(models.Model):
    """تفاصيل تكلفة المواد"""
    _name = 'furniture.mrp.costing.material'
    _description = 'تفاصيل تكلفة المواد'

    costing_id  = fields.Many2one('furniture.mrp.costing', required=True, ondelete='cascade')
    product_id  = fields.Many2one('product.product', string='المادة', required=True)
    quantity    = fields.Float(string='الكمية', digits=(16, 3))
    unit_cost   = fields.Float(string='سعر الوحدة', digits=(16, 2))
    total_cost  = fields.Float(string='الإجمالي', compute='_compute_total', store=True, digits=(16, 2))

    @api.depends('quantity', 'unit_cost')
    def _compute_total(self):
        for r in self:
            r.total_cost = r.quantity * r.unit_cost


class FurnitureMrpCostingLabor(models.Model):
    """تفاصيل تكلفة العمالة"""
    _name = 'furniture.mrp.costing.labor'
    _description = 'تفاصيل تكلفة العمالة'

    costing_id  = fields.Many2one('furniture.mrp.costing', required=True, ondelete='cascade')
    stage_name  = fields.Char(string='المرحلة')
    foreman_id  = fields.Many2one('hr.employee', string='رئيس القسم')
    hours       = fields.Float(string='الساعات', digits=(16, 2))
    rate        = fields.Float(string='تكلفة الساعة', digits=(16, 2))
    total_cost  = fields.Float(string='الإجمالي', compute='_compute_total', store=True, digits=(16, 2))

    @api.depends('hours', 'rate')
    def _compute_total(self):
        for r in self:
            r.total_cost = r.hours * r.rate


class FurnitureMrpCostingOverhead(models.Model):
    """تفاصيل تكلفة Overhead من الخزينة"""
    _name = 'furniture.mrp.costing.overhead'
    _description = 'تفاصيل Overhead'

    costing_id       = fields.Many2one('furniture.mrp.costing', required=True, ondelete='cascade')
    description      = fields.Char(string='البيان')
    date             = fields.Date(string='التاريخ')
    amount           = fields.Float(string='المبلغ', digits=(16, 2))
    analytic_line_id = fields.Many2one('account.analytic.line', string='سطر التحليلي')
