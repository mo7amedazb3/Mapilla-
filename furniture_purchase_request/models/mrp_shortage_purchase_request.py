# -*- coding: utf-8 -*-

from odoo import _, fields, models
from odoo.tools.float_utils import float_compare


class FurnitureMrpProductionShortagePurchaseRequest(models.Model):
    _inherit = 'furniture.mrp.production'

    def _material_shortage_procured_qty(self, reservation):
        """Quantity already covered by an RFQ/PO, including legacy draft RFQs."""
        lines = self.env['purchase.order.line'].sudo().search([
            ('furniture_material_reservation_id', '=', reservation.id),
            ('furniture_shortage_managed', '=', True),
            ('order_id.state', '!=', 'cancel'),
        ])
        outstanding = 0.0
        for line in lines:
            remaining = line.product_qty or 0.0
            if line.order_id.state in ('purchase', 'done'):
                remaining = max(remaining - (line.qty_received or 0.0), 0.0)
            outstanding += self._quantity_in_product_uom(
                reservation.product_id,
                remaining,
                line.product_uom,
            )
        return outstanding

    def _sync_material_shortage_purchase_orders(self):
        """Route shortages to the warehouse request workflow, never directly to a PO."""
        self.ensure_one()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [self.id],
        )
        Request = self.env['furniture.purchase.request'].sudo()
        RequestLine = self.env['furniture.purchase.request.line'].sudo()
        open_requests = Request.search([
            ('production_id', '=', self.id),
            ('production_shortage_managed', '=', True),
            ('state', 'in', ('draft', 'requested')),
        ], order='id desc')
        request = open_requests[:1]
        if len(open_requests) > 1:
            (open_requests - request)._system_cancel_material_shortage_request()

        payloads = {}
        active_reservations = self.material_reservation_ids.filtered(
            lambda item: item.state == 'active'
        )
        for reservation in active_reservations:
            procured_qty = self._material_shortage_procured_qty(reservation)
            quantity = max(reservation.shortage_qty - procured_qty, 0.0)
            rounding = reservation.product_uom_id.rounding or 0.001
            if float_compare(
                quantity, 0.0, precision_rounding=rounding,
            ) > 0:
                payloads[reservation.id] = {
                    'reservation': reservation,
                    'quantity': quantity,
                }

        if not payloads:
            if request:
                request.line_ids.filtered(
                    'material_shortage_managed'
                )._system_unlink_material_shortage_lines()
                request._system_cancel_material_shortage_request()
            self.with_context(
                furniture_skip_reservation_refresh=True,
            ).sudo().write({'missing_vendor_purchase_order_id': False})
            return self.env['purchase.order']

        if not request:
            request = Request._system_create_material_shortage_request(self)

        existing_lines = request.line_ids.filtered('material_shortage_managed')
        existing_by_reservation = {
            line.material_reservation_id.id: line
            for line in existing_lines
            if line.material_reservation_id
        }
        for reservation_id, payload in payloads.items():
            reservation = payload['reservation']
            values = {
                'product_id': reservation.product_id.id,
                'quantity': payload['quantity'],
                'product_uom_id': reservation.product_uom_id.id,
                'material_reservation_id': reservation.id,
                'material_shortage_managed': True,
            }
            line = existing_by_reservation.get(reservation_id)
            if line:
                line._system_write_material_shortage_line(values)
            else:
                values['request_id'] = request.id
                RequestLine._system_create_material_shortage_lines([values])

        stale_lines = existing_lines.filtered(
            lambda line: line.material_reservation_id.id not in payloads
        )
        if stale_lines:
            stale_lines._system_unlink_material_shortage_lines()

        self.with_context(
            furniture_skip_reservation_refresh=True,
        ).sudo().write({'missing_vendor_purchase_order_id': False})
        if not open_requests:
            self.message_post(body=_(
                '🛒 تم إنشاء طلب شراء خامات داخلي %s بدلًا من إنشاء PO مباشر.',
                request.name,
            ))
        return self.env['purchase.order']
