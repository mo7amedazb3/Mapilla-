# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Build product-stage timing rows and backfill every active MPS order."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    Operation = env['furniture.mrp.mps.operation']
    Timing = env['furniture.mrp.mps.product.time']

    # Boolean columns introduced on an existing table may contain NULL on old
    # rows until explicitly normalized.  Never let SQL CHECK's NULL semantics
    # bypass the detailed-operation contract.
    cr.execute(
        'UPDATE furniture_mrp_mps_profile '
        'SET is_auto_product_profile = FALSE '
        'WHERE is_auto_product_profile IS NULL'
    )
    cr.execute(
        'UPDATE furniture_mrp_mps_operation '
        'SET is_auto_product_standard = FALSE '
        'WHERE is_auto_product_standard IS NULL'
    )
    cr.execute(
        'UPDATE furniture_mrp_mps_operation '
        'SET time_is_configured = TRUE '
        'WHERE time_is_configured IS NULL'
    )

    detailed_profiles = Operation._seed_big_moon_profile()
    if detailed_profiles:
        Operation._validate_big_moon_seed_contract(detailed_profiles)

    timings = Timing._sync_from_boms()
    pairs = sorted({
        (timing.furniture_model_id.id, timing.company_id.id)
        for timing in timings
        if timing.furniture_model_id and timing.company_id
    })
    profiles = env['furniture.mrp.mps.profile']
    for model_id, company_id in pairs:
        profiles |= Timing._ensure_auto_profile(
            env['furniture.product.model'].browse(model_id),
            env['res.company'].browse(company_id),
        )

    if cr.dbname == 'yasser3' and (not timings or not profiles):
        raise RuntimeError(
            'تعذر تكوين مصفوفة MPS لكل ريسيبيات الموديلات المحفوظة.'
        )

    productions = env['furniture.mrp.production'].search([
        ('state', 'in', ('confirmed', 'in_production')),
    ])
    schedulable = env['furniture.mrp.production']
    for production in productions:
        model = production.furniture_order_model_id
        lines = production.production_line_ids.filtered(
            lambda line: line.active and line.product_qty > 0 and line.product_id
        )
        if not model or not lines:
            continue
        required_product_ids = {
            (
                line.product_id.furniture_dimension_source_product_id
                or line.product_id
            ).id
            for line in lines
        }
        recipe_product_ids = {
            (
                bom.furniture_product_id.furniture_dimension_source_product_id
                or bom.furniture_product_id
            ).id
            for bom in Timing._eligible_boms(model, production.company_id)
        }
        if required_product_ids.issubset(recipe_product_ids):
            schedulable |= production
    incompatible = productions - schedulable
    for production in incompatible:
        production.message_post(body=(
            '⚠️ لم يُنشأ MPS أثناء الترقية لأن الأمر القديم لا يحمل '
            'موديلًا وريسيبي موديل محفوظًا وأصنافًا موجبة قابلة للجدولة.'
        ))
    if schedulable:
        schedulable._generate_mps_schedule()
