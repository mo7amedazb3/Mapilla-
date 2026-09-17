"""Opt-in defaults for NEW purchases; never rewrite stock or historical lines."""
from odoo import api, models


class ProductBomPurchaseUnit(models.Model):
    _inherit = 'product.product'

    def _furniture_bom_purchase_unit(self, company=False):
        self.ensure_one()
        empty = self.env['uom.uom']
        if self.env['ir.config_parameter'].sudo().get_param(
                'furniture_mrp.bom_purchase_unit', 'False') != 'True':
            return empty
        company = company or self.env.company
        # Read only the chosen company's/shared active master recipes. Piece
        # overrides must not redefine a material's default across purchases.
        rows = self.env['furniture.mrp.bom.stage.line'].sudo().search([
            ('product_id', '=', self.id), ('bom_id.active', '=', True),
            ('bom_id.company_id', 'in', [False, company.id]),
        ])
        units = rows.product_uom_id
        if len(units) != 1 or not units.active:
            return empty
        # Never use the legacy cross-category 1:1 fallback for new defaults.
        return units if units.category_id == self.uom_id.category_id else empty


class PurchaseLineBomUnit(models.Model):
    _inherit = 'purchase.order.line'

    def _suggest_quantity(self):
        result = super()._suggest_quantity()
        if self.product_id:
            unit = self.product_id._furniture_bom_purchase_unit(self.company_id)
            if unit and self.product_uom and unit != self.product_uom:
                self.product_qty = self.product_uom._compute_quantity(
                    self.product_qty, unit, round=False)
                self.product_uom = unit
        return result

    def _product_id_change(self):
        result = super()._product_id_change()
        if self.product_id:
            unit = self.product_id._furniture_bom_purchase_unit(self.company_id)
            if unit:
                self.product_uom = unit
        return result

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            vals = dict(incoming)
            if vals.get('product_id') and not vals.get('product_uom') and not vals.get('display_type'):
                order = self.env['purchase.order'].browse(vals.get('order_id')).exists()
                product = self.env['product.product'].browse(vals['product_id'])
                if order and order.state == 'draft':
                    unit = product._furniture_bom_purchase_unit(order.company_id)
                    if unit:
                        vals['product_uom'] = unit.id
            prepared.append(vals)
        return super().create(prepared)
