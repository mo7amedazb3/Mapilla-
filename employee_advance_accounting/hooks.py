# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def post_init_hook(env):
    if not isinstance(env, api.Environment):
        env = api.Environment(env, SUPERUSER_ID, {})

    Account = env["account.account"].sudo()
    for company in env["res.company"].sudo().search([]):
        account = Account.with_company(company).search(
            [
                ("code", "=", "105002"),
                ("company_ids", "in", company.id),
            ],
            limit=1,
        )
        if not account:
            Account.with_company(company).create(
                {
                    "code": "105002",
                    "name": "Employee Advances",
                    "account_type": "asset_current",
                    "reconcile": True,
                    "company_ids": [(6, 0, [company.id])],
                }
            )

