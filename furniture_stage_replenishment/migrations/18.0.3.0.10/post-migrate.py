# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Activate canonical controllers and archive retired rows safely."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    StageRule = env['furniture.mrp.stage.replenishment.rule'].sudo()
    for company in env['res.company'].sudo().search([]).sorted('id'):
        # Sync also copies a configured bases-only policy to finishing before
        # archiving bases.  Upholstery and bases rows remain in the database
        # with their limits and audit history intact.
        StageRule.with_company(company)._stage_replenishment_sync_company(
            company
        )
