# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env['res.company'].search([]):
        cash = env['account.journal'].with_company(company).search([
            ('company_id', '=', company.id),
            ('type', '=', 'cash'),
            ('active', '=', True),
        ], order='sequence, id', limit=1)
        bank = env['account.journal'].with_company(company).search([
            ('company_id', '=', company.id),
            ('type', '=', 'bank'),
            ('active', '=', True),
        ], order='sequence, id', limit=1)
        values = {}
        if cash:
            values['furniture_payroll_cash_journal_id'] = cash.id
        if bank:
            values['furniture_payroll_bank_journal_id'] = bank.id
        if values:
            company.write(values)
