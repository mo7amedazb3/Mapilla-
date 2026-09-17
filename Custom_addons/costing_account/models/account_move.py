# -*- coding: utf-8 -*-

from collections import defaultdict

from odoo import models, _
from odoo.exceptions import UserError
from odoo.tools import float_is_zero


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        result = super().action_post()
        customer_invoices = self.filtered(lambda move: move.move_type == 'out_invoice')
        customer_invoices._spa_consume_bom_materials()
        customer_invoices._spa_recalculate_costing_periods()
        return result

    def button_draft(self):
        posted_invoices = self.filtered(
            lambda move: move.state == 'posted' and move.move_type == 'out_invoice'
        )
        posted_invoices._spa_reverse_bom_materials()
        result = super().button_draft()
        posted_invoices._spa_recalculate_costing_periods()
        return result

    def button_cancel(self):
        posted_invoices = self.filtered(
            lambda move: move.state == 'posted' and move.move_type == 'out_invoice'
        )
        posted_invoices._spa_reverse_bom_materials()
        result = super().button_cancel()
        posted_invoices._spa_recalculate_costing_periods()
        return result

    def _spa_recalculate_costing_periods(self):
        dates_by_company = defaultdict(set)
        for move in self:
            if move.invoice_date:
                dates_by_company[move.company_id].add(move.invoice_date.replace(day=1))
        for company, period_dates in dates_by_company.items():
            periods = self.env['spa.costing.period'].search([
                ('company_id', '=', company.id),
                ('period_date', 'in', list(period_dates)),
                ('state', '!=', 'approved'),
            ])
            periods.with_context(skip_auto_calculate=True).action_calculate()

    def _spa_get_stock_locations(self):
        self.ensure_one()
        warehouse = self.invoice_line_ids.sale_line_ids.order_id.warehouse_id[:1]
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search(
                [('company_id', '=', self.company_id.id)],
                limit=1,
            )
        source = warehouse.lot_stock_id
        destination = self.env['stock.location'].search([
            ('usage', '=', 'production'),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not source or not destination:
            raise UserError(_("Stock and production locations are required to consume service BoM materials."))
        return source, destination

    def _spa_consume_bom_materials(self):
        for move in self:
            if move.move_type != 'out_invoice' or move.state != 'posted':
                continue
            source, destination = move._spa_get_stock_locations()
            for invoice_line in move.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product'
                and line.product_id.product_tmpl_id.costing_eligible
                and not line.spa_material_consumed
            ):
                product_template = invoice_line.product_id.product_tmpl_id
                bom = product_template.costing_bom_id
                if not bom:
                    continue

                required_moves = []
                material_cost = 0.0
                invoice_quantity = abs(invoice_line.quantity)
                output_quantity = bom.product_uom_id._compute_quantity(
                    bom.product_qty,
                    product_template.uom_id,
                )
                if not output_quantity:
                    raise UserError(_("BoM output quantity must be greater than zero."))

                for bom_line in bom.bom_line_ids:
                    if bom_line._skip_bom_line(invoice_line.product_id):
                        continue
                    component = bom_line.product_id.with_company(move.company_id)
                    if float_is_zero(
                        component.standard_price,
                        precision_rounding=move.company_id.currency_id.rounding,
                    ):
                        raise UserError(_(
                            "Set a cost for material '%(material)s' before delivering "
                            "service '%(service)s'.",
                            material=component.display_name,
                            service=invoice_line.product_id.display_name,
                        ))
                    required_quantity = (
                        bom_line.product_qty * invoice_quantity / output_quantity
                    )
                    available_quantity = component.uom_id._compute_quantity(
                        component.with_context(location=source.id).qty_available,
                        bom_line.product_uom_id,
                    )
                    if available_quantity < required_quantity:
                        raise UserError(_(
                            "Not enough stock for '%(material)s' to deliver '%(service)s'. "
                            "Required: %(required)s %(uom)s, available: %(available)s %(uom)s.",
                            material=component.display_name,
                            service=invoice_line.product_id.display_name,
                            required=required_quantity,
                            available=available_quantity,
                            uom=bom_line.product_uom_id.display_name,
                        ))

                    unit_cost = component.uom_id._compute_price(
                        component.standard_price,
                        bom_line.product_uom_id,
                    )
                    material_cost += unit_cost * required_quantity
                    required_moves.append({
                        'name': _("%s material consumption") % invoice_line.product_id.display_name,
                        'origin': move.name,
                        'company_id': move.company_id.id,
                        'product_id': component.id,
                        'product_uom_qty': required_quantity,
                        'product_uom': bom_line.product_uom_id.id,
                        'location_id': source.id,
                        'location_dest_id': destination.id,
                        'spa_invoice_line_id': invoice_line.id,
                    })

                stock_moves = self.env['stock.move'].create(required_moves)
                stock_moves._action_confirm()
                stock_moves._action_assign()
                for stock_move in stock_moves:
                    stock_move.quantity = stock_move.product_uom_qty
                    stock_move.picked = True
                stock_moves._action_done()
                invoice_line.write({
                    'spa_bom_id': bom.id,
                    'spa_material_cost': move.company_id.currency_id.round(material_cost),
                    'spa_material_consumed': True,
                })

    def _spa_reverse_bom_materials(self):
        for move in self:
            source, destination = move._spa_get_stock_locations()
            for invoice_line in move.invoice_line_ids.filtered('spa_material_consumed'):
                original_moves = invoice_line.spa_consumption_move_ids.filtered(
                    lambda stock_move: stock_move.state == 'done'
                    and not stock_move.spa_consumption_reversal
                    and not stock_move.spa_consumption_reversed
                )
                reversal_values = []
                for original_move in original_moves:
                    reversal_values.append({
                        'name': _("%s material consumption reversal") % invoice_line.product_id.display_name,
                        'origin': move.name,
                        'company_id': move.company_id.id,
                        'product_id': original_move.product_id.id,
                        'product_uom_qty': original_move.quantity,
                        'product_uom': original_move.product_uom.id,
                        'location_id': destination.id,
                        'location_dest_id': source.id,
                        'spa_invoice_line_id': invoice_line.id,
                        'spa_consumption_reversal': True,
                    })
                reversal_moves = self.env['stock.move'].create(reversal_values)
                reversal_moves._action_confirm()
                for reversal_move in reversal_moves:
                    reversal_move.quantity = reversal_move.product_uom_qty
                    reversal_move.picked = True
                reversal_moves._action_done()
                original_moves.spa_consumption_reversed = True
                invoice_line.write({
                    'spa_material_consumed': False,
                    'spa_material_cost': 0.0,
                    'spa_bom_id': False,
                })
