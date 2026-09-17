# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Persist the armchair exception on its canonical finished product."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env[
        'furniture.mrp.stage.replenishment.rule'
    ]._stage_replenishment_mark_default_separate_products()
