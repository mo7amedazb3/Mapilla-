# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    StageRule = env['furniture.mrp.stage.replenishment.rule'].sudo()
    FinalRule = env['furniture.mrp.final.replenishment.rule'].sudo()
    StageTime = env['furniture.mrp.stage.time.standard'].sudo()
    StageRule._stage_replenishment_mark_default_separate_products()
    for company in env['res.company'].sudo().search([]).sorted('id'):
        StageRule.with_company(company)._stage_replenishment_sync_company(company)
        FinalRule.with_company(company)._final_replenishment_sync_company(company)
        StageTime.with_company(company)._stage_time_sync_company(company)

    # Existing running work must immediately gain a deterministic countdown.
    stage_models = {
        model_name
        for model_name in env.registry.models
        if model_name.startswith('furniture.mrp.')
        and not env[model_name]._abstract
        and env[model_name]._auto
        and 'stage_timer_started_at' in env[model_name]._fields
        and 'state' in env[model_name]._fields
    }
    for model_name in sorted(stage_models):
        records = env[model_name].sudo().search([
            ('state', 'in', ('in_progress', 'quality_check')),
            ('stage_timer_started_at', '=', False),
        ])
        if not records:
            continue
        if model_name == 'furniture.mrp.stage.product.batch':
            records._stage_timer_initialize_if_needed()
        elif 'production_order_id' in records._fields:
            for record in records:
                record._stage_timer_initialize_if_needed(
                    started_at=record.date_start,
                )
