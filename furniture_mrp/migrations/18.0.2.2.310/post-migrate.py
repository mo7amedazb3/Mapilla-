# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import SUPERUSER_ID, api
from odoo.tools.float_utils import float_compare


def migrate(cr, version):
    """Remove harmless per-line rounding excess from staged raw materials.

    Older receipts could move a two-decimal stock quantity, then distribute it
    across several three-decimal recipe rows.  Independent rounding could make
    the saved row total one thousandth larger than the physical move and block
    quality approval.  Only unconsumed rows linked to the same completed
    receipt move are normalized, and never above the move's real quantity.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    material_model = env['furniture.mrp.material.line'].sudo()
    lines = material_model.search([
        ('warehouse_receipt_confirmed', '=', True),
        ('move_id', '!=', False),
        ('move_id.state', '=', 'done'),
    ])
    grouped = defaultdict(lambda: material_model)
    for line in lines:
        production = line.production_id
        move = line.move_id
        if (
            not production
            or not line.product_id
            or move.location_dest_id == production._get_production_location()
        ):
            continue
        grouped[(production.id, line.stage, line.product_id.id, move.id)] |= line

    for group_lines in grouped.values():
        production = group_lines[:1].production_id
        product = group_lines[:1].product_id
        move = group_lines[:1].move_id
        moved_product_qty = production._quantity_in_product_uom(
            product,
            move.quantity or move.product_uom_qty,
            move.product_uom,
        )
        allocated_product_qty = sum(
            production._material_line_required_stock_qty(line)
            for line in group_lines
        )
        if float_compare(
            allocated_product_qty, moved_product_qty, precision_digits=3,
        ) <= 0:
            continue
        rounding_tolerance = max(move.product_uom.rounding or 0.001, 0.001)
        if allocated_product_qty - moved_product_qty > rounding_tolerance + 1e-9:
            continue
        allocations = production._allocate_received_product_qty(
            group_lines, moved_product_qty,
        )
        for line in group_lines:
            line.write({'warehouse_received_qty': allocations[line.id]})
