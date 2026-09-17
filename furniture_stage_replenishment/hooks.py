# -*- coding: utf-8 -*-


def post_init_hook(env):
    """Create safe rules and two-hour stage standards for every company."""
    Rule = env['furniture.mrp.stage.replenishment.rule'].sudo()
    FinalRule = env['furniture.mrp.final.replenishment.rule'].sudo()
    StageTime = env['furniture.mrp.stage.time.standard'].sudo()
    Rule._stage_replenishment_clear_legacy_separate_products()
    for company in env['res.company'].sudo().search([]).sorted('id'):
        Rule.with_company(company)._stage_replenishment_sync_company(company)
        FinalRule.with_company(company)._final_replenishment_sync_company(company)
        StageTime.with_company(company)._stage_time_sync_company(company)
