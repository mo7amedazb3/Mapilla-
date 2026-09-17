# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def _allowance_bucket(name):
    normalized = (name or '').strip().lower()
    if any(token in normalized for token in ('أكل', 'اكل', 'طعام', 'food')):
        return 'furniture_food_allowance'
    if any(token in normalized for token in (
        'مواصل', 'انتقال', 'نقل', 'transport',
    )):
        return 'furniture_transport_allowance'
    return 'furniture_basic_allowance'


def migrate(cr, version):
    """Move legacy free-form allowances to the three fixed contract fields."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['furniture.mrp.contract.allowance'].with_context(
        active_test=False,
    ).search([('active', '=', True)])
    for contract in lines.mapped('contract_id'):
        values = {
            'furniture_food_allowance': (
                contract.furniture_food_allowance or 0.0
            ),
            'furniture_transport_allowance': (
                contract.furniture_transport_allowance or 0.0
            ),
            'furniture_basic_allowance': (
                contract.furniture_basic_allowance or 0.0
            ),
        }
        for line in lines.filtered(lambda item: item.contract_id == contract):
            bucket = _allowance_bucket(line.name)
            values[bucket] += line.amount or 0.0
        contract.write(values)

    slips = env['simple.payroll.slip'].search([])
    if slips:
        slips._compute_contract_id()
        slips._compute_work_schedule()
        slips._compute_amounts()
