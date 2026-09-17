# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    drafts_without_plan = env["employee.advance"].search(
        [
            ("state", "=", "draft"),
            ("installment_ids", "=", False),
        ]
    )
    drafts_without_plan._generate_installments()
