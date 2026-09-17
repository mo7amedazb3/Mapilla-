# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Split the historical combined tailoring/sewing work center."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    workcenter_values = {
        'furniture_mrp.workcenter_sewing': {
            'name': 'قسم تفصيل',
            'code': 'TFS',
        },
        'furniture_mrp.workcenter_stitching': {
            'name': 'قسم الخياطة',
            'code': 'SEW',
        },
    }
    for xmlid, values in workcenter_values.items():
        workcenter = env.ref(xmlid, raise_if_not_found=False)
        if workcenter:
            workcenter.write(values)
