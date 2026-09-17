# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api, fields


FACTORY_CALENDAR_NAME = (
    'MRP أثاث - 6 أيام × 8 ساعات (10:00–18:00، الجمعة إجازة)'
)
FACTORY_WORK_DAYS = ('0', '1', '2', '3', '5', '6')


def _factory_calendar(env, company, employees):
    calendars = env['resource.calendar'].with_context(
        active_test=False,
    ).search([
        ('company_id', '=', company.id),
        ('name', 'ilike', 'MRP أثاث - 6 أيام'),
    ])
    if calendars:
        calendar = max(
            calendars,
            key=lambda item: len(employees.filtered(
                lambda employee: employee.resource_calendar_id == item
            )),
        )
        calendar.active = True
    else:
        calendar = env['resource.calendar'].create({
            'name': FACTORY_CALENDAR_NAME,
            'company_id': company.id,
            'tz': company.resource_calendar_id.tz or 'UTC',
            'hours_per_day': 8.0,
        })

    calendar.write({
        'name': FACTORY_CALENDAR_NAME,
        'hours_per_day': 8.0,
    })
    calendar.attendance_ids.unlink()
    env['resource.calendar.attendance'].create([
        {
            'name': 'دوام المصنع 10:00–18:00',
            'calendar_id': calendar.id,
            'dayofweek': dayofweek,
            'hour_from': 10.0,
            'hour_to': 18.0,
            'day_period': 'morning',
        }
        for dayofweek in FACTORY_WORK_DAYS
    ])
    company.resource_calendar_id = calendar
    return calendar


def migrate(cr, version):
    """Apply the factory payroll calendar and cover every active employee.

    Existing salaries are preserved.  Only employees without any contract get
    a new open zero-wage contract, deliberately waiting for the real wage to be
    entered by payroll management.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    employees = env['hr.employee'].with_context(active_test=False).search([
        ('active', '=', True),
    ])
    today = fields.Date.context_today(env.user)

    for company in employees.mapped('company_id'):
        company_employees = employees.filtered(
            lambda employee: employee.company_id == company
        )
        calendar = _factory_calendar(env, company, company_employees)
        company_employees.write({'resource_calendar_id': calendar.id})

        contracts = env['hr.contract'].with_context(
            active_test=False,
        ).search([
            ('employee_id', 'in', company_employees.ids),
            ('company_id', '=', company.id),
        ])
        if contracts:
            contracts.write({'resource_calendar_id': calendar.id})

        contracted_employees = contracts.mapped('employee_id')
        missing_employees = company_employees - contracted_employees
        if missing_employees:
            env['hr.contract'].create([
                {
                    'name': 'عقد - %s' % employee.display_name,
                    'employee_id': employee.id,
                    'company_id': company.id,
                    'resource_calendar_id': calendar.id,
                    'date_start': today,
                    'wage': 0.0,
                    'state': 'open',
                }
                for employee in missing_employees
            ])

    slips = env['simple.payroll.slip'].search([])
    if slips:
        slips._compute_contract_id()
        slips._compute_work_schedule()
        slips._compute_amounts()
