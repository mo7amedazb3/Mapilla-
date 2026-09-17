# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Synchronize the four independent component controllers safely."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    StageRule = env['furniture.mrp.stage.replenishment.rule'].sudo()
    FinalRule = env['furniture.mrp.final.replenishment.rule'].sudo()
    for company in env['res.company'].sudo().search([]).sorted('id'):
        StageRule.with_company(company)._stage_replenishment_sync_company(
            company
        )
        FinalRule.with_company(company)._final_replenishment_sync_company(
            company
        )
