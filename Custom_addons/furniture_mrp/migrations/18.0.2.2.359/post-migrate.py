# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import Command, SUPERUSER_ID, api, fields


def _company_account(env, company, code):
    return env['account.account'].with_company(company).search([
        ('code', '=', code),
        ('company_ids', 'in', company.id),
    ], limit=1)


def _configure_payroll_accounts(env, company):
    journal = env['account.journal'].with_company(company).search([
        ('company_id', '=', company.id),
        ('type', '=', 'general'),
        ('code', '=', 'MISC'),
        ('active', '=', True),
    ], limit=1)
    if not journal:
        journal = env['account.journal'].with_company(company).search([
            ('company_id', '=', company.id),
            ('type', '=', 'general'),
            ('active', '=', True),
        ], order='sequence, id', limit=1)

    basic = _company_account(env, company, '400003')
    allowance = _company_account(env, company, '400012')
    advance = _company_account(env, company, '105002')
    accrued = _company_account(env, company, '201004')
    rewards = _company_account(env, company, '400080')
    if not rewards:
        rewards = env['account.account'].with_company(company).create({
            'name': 'Rewards',
            'code': '400080',
            'account_type': 'expense',
            'company_ids': [Command.set(company.ids)],
        })

    if all((journal, basic, allowance, rewards, advance, accrued)):
        company.write({
            'furniture_payroll_journal_id': journal.id,
            'furniture_basic_salary_account_id': basic.id,
            'furniture_allowance_account_id': allowance.id,
            'furniture_rewards_account_id': rewards.id,
            'furniture_salary_advance_account_id': advance.id,
            'furniture_accrued_salary_account_id': accrued.id,
        })


def _retarget_current_draft_week(env, company):
    """Move the one legacy rolling draft cohort onto the new Sat-Fri week.

    This is deliberately conservative: it only touches the most recent
    seven-day draft cohort overlapping the current week, and only when no
    target-week or conflicting slips exist.
    """
    today = fields.Date.context_today(company)
    current_from = today - timedelta(days=(today.weekday() + 2) % 7)
    current_to = current_from + timedelta(days=6)
    Slip = env['simple.payroll.slip'].with_company(company)
    if Slip.search_count([
        ('company_id', '=', company.id),
        ('date_from', '=', current_from),
        ('date_to', '=', current_to),
        ('state', '!=', 'cancel'),
    ]):
        return

    recent = Slip.search([
        ('company_id', '=', company.id),
        ('state', '=', 'draft'),
        ('date_from', '<=', current_to),
        ('date_to', '>=', current_from),
        ('date_to', '<=', today),
    ], order='date_to desc, date_from desc, id')
    if not recent:
        return
    source_from, source_to = recent[0].date_from, recent[0].date_to
    if (source_to - source_from).days != 6:
        return
    cohort = recent.filtered(
        lambda slip: slip.date_from == source_from and slip.date_to == source_to
    )
    if not cohort:
        return
    conflicts = Slip.search_count([
        ('company_id', '=', company.id),
        ('employee_id', 'in', cohort.employee_id.ids),
        ('id', 'not in', cohort.ids),
        ('state', '!=', 'cancel'),
        ('date_from', '<=', current_to),
        ('date_to', '>=', current_from),
    ])
    if conflicts:
        return
    cohort.write({'date_from': current_from, 'date_to': current_to})
    cohort._compute_contract_id()
    cohort._compute_work_schedule()
    cohort._compute_amounts()


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env['res.company'].search([]):
        _configure_payroll_accounts(env, company)
        _retarget_current_draft_week(env, company)
