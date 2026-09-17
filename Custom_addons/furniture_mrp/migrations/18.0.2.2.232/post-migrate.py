# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Normalize safe, still-editable saved fabric/takawe rows to meters."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    meter_uom = env.ref('furniture_mrp.furniture_uom_meter')
    allocations = env[
        'furniture.mrp.tailoring.material.allocation'
    ].with_context(active_test=False).search([
        ('product_uom_id', '!=', meter_uom.id),
        ('production_id.state', 'not in', ('done', 'cancelled')),
    ])
    for allocation in allocations:
        if allocation.product_id.uom_id.category_id != meter_uom.category_id:
            continue
        material_lines = env['furniture.mrp.material.line'].search([
            ('tailoring_allocation_id', '=', allocation.id),
        ])
        if material_lines.filtered(lambda line: (
            line.move_id
            or line.warehouse_receipt_confirmed
            or line.warehouse_receipt_stage_id
        )):
            continue
        allocation.sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        ).write({'product_uom_id': meter_uom.id})
        material_lines.sudo().with_context(
            furniture_skip_reservation_refresh=True,
        ).write({
            'product_uom_id': meter_uom.id,
            # Re-send the unchanged link so the material-line normalizer can
            # verify that this is the allocation's deliberate meter UoM.
            'tailoring_allocation_id': allocation.id,
        })
