# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Install the shared per-product step matrix and refresh safe schedules."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    Operation = env['furniture.mrp.mps.operation']
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

    productions = env['furniture.mrp.production'].search([
        ('state', 'in', ('confirmed', 'in_production')),
        ('mps_schedule_plan_ids.state', 'in', ('planned', 'approved', 'in_progress')),
    ])
    if productions:
        productions._furniture_replan_proposed_mps()

