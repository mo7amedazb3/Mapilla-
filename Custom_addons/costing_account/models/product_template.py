# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    costing_eligible = fields.Boolean(
        string="Costing Eligible",
        help="Include this service in monthly profitability and costing calculations.",
    )
    costing_department_id = fields.Many2one(
        'spa.service.department',
        string="Service Department",
        ondelete='restrict',
    )
    costing_bom_id = fields.Many2one(
        'mrp.bom',
        string="Material BoM",
        compute='_compute_costing_material_cost',
    )
    costing_material_cost = fields.Monetary(
        string="Material Cost / Service",
        currency_field='currency_id',
        compute='_compute_costing_material_cost',
    )

    @api.depends_context('company')
    def _compute_costing_material_cost(self):
        bom_model = self.env['mrp.bom'].sudo()
        for product in self:
            bom = bom_model.search([
                ('product_tmpl_id', '=', product.id),
                ('active', '=', True),
                '|',
                ('company_id', '=', False),
                ('company_id', '=', self.env.company.id),
            ], order='sequence, id', limit=1)
            product.costing_bom_id = bom
            if not bom:
                product.costing_material_cost = 0.0
                continue

            company = bom.company_id or self.env.company
            product_variant = bom.product_id or product.product_variant_id
            material_cost = 0.0
            for bom_line in bom.bom_line_ids:
                if bom_line._skip_bom_line(product_variant):
                    continue
                component = bom_line.product_id.with_company(company)
                unit_cost = component.uom_id._compute_price(
                    component.standard_price,
                    bom_line.product_uom_id,
                )
                material_cost += unit_cost * bom_line.product_qty

            output_quantity = bom.product_uom_id._compute_quantity(
                bom.product_qty,
                product.uom_id,
            )
            product.costing_material_cost = (
                material_cost / output_quantity if output_quantity else 0.0
            )
