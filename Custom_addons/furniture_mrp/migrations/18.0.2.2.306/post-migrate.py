# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api
from odoo.tools.float_utils import float_compare


def _sync_big_moon_mps(env):
    """Apply the managed worker/dependency contract on upgrades from .305."""
    operation_model = env['furniture.mrp.mps.operation'].sudo()
    profiles = operation_model._seed_big_moon_profile()
    if not profiles:
        return
    operation_model._validate_big_moon_seed_contract(profiles)

    # Seed rows are configuration while work/assignments are snapshots.  Replan
    # only active orders through the normal API: it retains lines from running
    # stages and rebuilds only work that has not started yet.
    plans = env['furniture.mrp.mps.plan'].sudo().search([
        ('profile_id', 'in', profiles.ids),
        ('state', 'in', ('planned', 'approved', 'in_progress')),
        ('production_id.state', 'in', ('confirmed', 'in_production')),
    ])
    for production in plans.mapped('production_id'):
        production._furniture_replan_proposed_mps()


def migrate(cr, version):
    """Route new upholstery work through its hall and retire its old store.

    Historical stock moves keep their location reference.  The location is
    archived only when it has no real quant balance, so an upgrade can never
    hide live stock accidentally.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    _sync_big_moon_mps(env)

    upholstery_store = env.ref(
        'furniture_mrp.location_stage_upholstery',
        raise_if_not_found=False,
    )
    upholstery_hall = env.ref(
        'furniture_mrp.location_stage_upholstery_wip',
        raise_if_not_found=False,
    )
    if upholstery_hall:
        open_orders = env['furniture.mrp.production'].with_context(
            active_test=False,
        ).search([
            ('production_lane', '=', 'upholstery'),
            ('state', 'in', ('draft', 'confirmed')),
            ('upholstery_order_id', '=', False),
        ])
        if open_orders:
            open_orders.with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
            ).write({
                'location_upholstery_wip_id': upholstery_hall.id,
                'location_upholstery_id': upholstery_hall.id,
            })

    if not upholstery_store:
        return
    quants = env['stock.quant'].sudo().search([
        ('location_id', 'child_of', upholstery_store.id),
    ])
    has_live_balance = any(
        float_compare(
            (quant.quantity or 0.0) - (quant.reserved_quantity or 0.0),
            0.0,
            precision_rounding=quant.product_id.uom_id.rounding or 0.001,
        ) != 0
        or float_compare(
            quant.reserved_quantity or 0.0,
            0.0,
            precision_rounding=quant.product_id.uom_id.rounding or 0.001,
        ) != 0
        for quant in quants
    )
    if not has_live_balance and upholstery_store.active:
        upholstery_store.write({'active': False})
