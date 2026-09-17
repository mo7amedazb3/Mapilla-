# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Retire saved split-order markers; grouping is now model-wide."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env[
        'furniture.mrp.stage.replenishment.rule'
    ]._stage_replenishment_clear_legacy_separate_products()
