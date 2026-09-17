# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .mrp_production_order import FURNITURE_LEGACY_STAGE_SELECTION


class FurnitureMrpStageCostEntry(models.Model):
    _name = 'furniture.mrp.stage.cost.entry'
    _description = 'تكلفة دفعة منتج حسب مرحلة الإنتاج'
    _order = 'accepted_at desc, id desc'
    _rec_name = 'product_id'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الذي اعتمد التكلفة',
        required=True,
        ondelete='cascade',
        index=True,
    )
    source_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج الأصلي',
        related='production_line_id.production_id',
        store=True,
        readonly=True,
        index=True,
    )
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='دفعة المنتج',
        required=True,
        ondelete='cascade',
        index=True,
    )
    stage = fields.Selection(
        FURNITURE_LEGACY_STAGE_SELECTION,
        string='المرحلة',
        required=True,
        index=True,
    )
    stage_order_model = fields.Char(string='موديل أمر المرحلة', required=True, index=True)
    stage_order_res_id = fields.Integer(string='رقم أمر المرحلة', required=True, index=True)
    stage_order_name = fields.Char(string='أمر المرحلة', readonly=True)
    product_id = fields.Many2one('product.product', string='المنتج', required=True, index=True)
    dimension_label = fields.Char(
        string='المقاس',
        related='product_id.furniture_dimension_label',
        store=True,
        readonly=True,
    )
    quantity = fields.Float(string='الكمية المقبولة', required=True, digits=(16, 3))
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True)
    previous_material_cost = fields.Float(string='خامات المراحل السابقة', digits=(16, 2))
    previous_labor_cost = fields.Float(string='عمالة المراحل السابقة', digits=(16, 2))
    stage_material_cost = fields.Float(string='خامات المرحلة', digits=(16, 2))
    stage_labor_cost = fields.Float(string='عمالة المرحلة', digits=(16, 2))
    cumulative_material_cost = fields.Float(string='إجمالي الخامات التراكمي', digits=(16, 2))
    cumulative_labor_cost = fields.Float(string='إجمالي العمالة التراكمي', digits=(16, 2))
    stage_total_cost = fields.Float(
        string='تكلفة المرحلة',
        compute='_compute_totals',
        store=True,
        digits=(16, 2),
    )
    cumulative_total_cost = fields.Float(
        string='التكلفة التراكمية',
        compute='_compute_totals',
        store=True,
        digits=(16, 2),
    )
    material_cost_per_unit = fields.Float(
        string='خامات الوحدة حتى الآن',
        compute='_compute_totals',
        store=True,
        digits=(16, 2),
    )
    labor_cost_per_unit = fields.Float(
        string='عمالة الوحدة حتى الآن',
        compute='_compute_totals',
        store=True,
        digits=(16, 2),
    )
    current_unit_cost = fields.Float(
        string='تكلفة الوحدة الحالية',
        compute='_compute_totals',
        store=True,
        digits=(16, 2),
    )
    accepted_at = fields.Datetime(string='تاريخ قبول الجودة', required=True, default=fields.Datetime.now, index=True)
    accepted_by_id = fields.Many2one('res.users', string='اعتمد بواسطة', required=True, default=lambda self: self.env.user)
    company_id = fields.Many2one(
        'res.company',
        related='production_id.company_id',
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='company_id.currency_id',
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            'production_line_stage_unique',
            'unique(production_line_id, stage)',
            'تم اعتماد تكلفة هذه الدفعة في هذه المرحلة من قبل.',
        ),
    ]

    @api.depends(
        'quantity',
        'stage_material_cost',
        'stage_labor_cost',
        'cumulative_material_cost',
        'cumulative_labor_cost',
    )
    def _compute_totals(self):
        for rec in self:
            rec.stage_total_cost = (rec.stage_material_cost or 0.0) + (rec.stage_labor_cost or 0.0)
            rec.cumulative_total_cost = (
                (rec.cumulative_material_cost or 0.0)
                + (rec.cumulative_labor_cost or 0.0)
            )
            quantity = rec.quantity or 0.0
            rec.material_cost_per_unit = rec.cumulative_material_cost / quantity if quantity else 0.0
            rec.labor_cost_per_unit = rec.cumulative_labor_cost / quantity if quantity else 0.0
            rec.current_unit_cost = rec.cumulative_total_cost / quantity if quantity else 0.0
