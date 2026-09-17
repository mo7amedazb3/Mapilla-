# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Cover safe bases/sewing gaps in full-route releases made before v124."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env[
        'furniture.mrp.advance.material.release'
    ]._repair_legacy_added_stage_coverage()
