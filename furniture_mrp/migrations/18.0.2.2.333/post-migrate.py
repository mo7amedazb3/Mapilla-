# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


OLD_FACTORY_CALENDAR_NAME = (
    'MRP أثاث - 6 أيام × 8 ساعات (10:00–18:00، الجمعة إجازة)'
)
FACTORY_CALENDAR_NAME = (
    'MRP أثاث - 6 أيام × 10 ساعات (10:00–20:00، الجمعة إجازة)'
)
FACTORY_WORK_DAYS = ('0', '1', '2', '3', '5', '6')


def migrate(cr, version):
    """Correct the live factory shift without changing calendar links."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    calendars = env['resource.calendar'].with_context(
        active_test=False,
    ).search([
        ('name', '=', OLD_FACTORY_CALENDAR_NAME),
    ])

    for calendar in calendars:
        calendar.write({
            'name': FACTORY_CALENDAR_NAME,
            'hours_per_day': 10.0,
            'active': True,
        })
        calendar.attendance_ids.unlink()
        env['resource.calendar.attendance'].create([
            {
                'name': 'دوام المصنع 10:00–20:00',
                'calendar_id': calendar.id,
                'dayofweek': dayofweek,
                'hour_from': 10.0,
                'hour_to': 20.0,
                'day_period': 'morning',
            }
            for dayofweek in FACTORY_WORK_DAYS
        ])

    if calendars:
        slips = env['simple.payroll.slip'].search([])
        if slips:
            slips._compute_contract_id()
            slips._compute_work_schedule()
            slips._compute_amounts()
