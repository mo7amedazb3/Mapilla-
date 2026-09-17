# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Switch every MPS standard to balanced whole-piece allocation.

    Approved/running history is deliberately left untouched.  Only plans whose
    work is still entirely proposed/blocked are regenerated with the new rule.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    Operation = env['furniture.mrp.mps.operation'].with_context(active_test=False)
    Timing = env['furniture.mrp.mps.product.time']

    detailed_profiles = Operation._seed_big_moon_profile()
    if detailed_profiles:
        Operation._validate_big_moon_seed_contract(detailed_profiles)

    timings = Timing._sync_from_boms()
    for model_id, company_id in sorted({
        (timing.furniture_model_id.id, timing.company_id.id)
        for timing in timings
        if timing.furniture_model_id and timing.company_id
    }):
        Timing._ensure_auto_profile(
            env['furniture.product.model'].browse(model_id),
            env['res.company'].browse(company_id),
        )

    # Managed standards follow the new universal factory rule.  Keep the
    # technical ``single`` mode available for genuinely custom operations.
    Operation.search([
        '|',
        ('is_seeded_big_moon', '=', True),
        ('is_auto_product_standard', '=', True),
    ]).write({
        'distribution_mode': 'stage_pool_equal',
        'same_worker_key': False,
        'different_worker_key': False,
    })

    safe_plans = env['furniture.mrp.mps.plan'].search([
        ('state', '=', 'planned'),
        ('production_id.state', '=', 'confirmed'),
    ]).filtered(lambda plan: (
        all(line.state in ('proposed', 'blocked') for line in plan.line_ids)
        and all(
            assignment.state == 'proposed'
            for assignment in plan.assignment_ids
        )
    ))
    if safe_plans:
        safe_plans.mapped('production_id')._furniture_replan_proposed_mps()
