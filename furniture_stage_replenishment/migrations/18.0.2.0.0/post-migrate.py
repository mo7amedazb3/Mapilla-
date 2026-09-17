# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Activate the two lane controllers during upgrade, not first UI load.

    Synchronization is intentionally non-destructive: controller identities
    reuse their saved limits, while obsolete per-stage identities are merely
    archived with their limits and traceability left intact.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    Rule = env['furniture.mrp.stage.replenishment.rule'].sudo()
    Rule._stage_replenishment_mark_default_separate_products()
    for company in env['res.company'].sudo().search([]).sorted('id'):
        with cr.savepoint():
            Rule.with_company(company)._stage_replenishment_sync_company(
                company
            )
