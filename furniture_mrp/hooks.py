# -*- coding: utf-8 -*-


def post_init_hook(env):
    """Seed detailed standards, then cover every saved model recipe in MPS."""
    env['hr.employee']._sync_all_furniture_mrp_supervisor_groups()
    operation_model = env['furniture.mrp.mps.operation']
    profiles = operation_model._seed_big_moon_profile()
    if profiles:
        operation_model._validate_big_moon_seed_contract(profiles)
    timings = env['furniture.mrp.mps.product.time']._sync_from_boms()
    for model_id, company_id in sorted({
        (timing.furniture_model_id.id, timing.company_id.id)
        for timing in timings
        if timing.furniture_model_id and timing.company_id
    }):
        env['furniture.mrp.mps.product.time']._ensure_auto_profile(
            env['furniture.product.model'].browse(model_id),
            env['res.company'].browse(company_id),
        )
