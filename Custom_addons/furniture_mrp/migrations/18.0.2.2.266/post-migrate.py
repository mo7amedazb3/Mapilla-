# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Install whole-piece MPS standards and rebuild unstarted schedules."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    operation_model = env['furniture.mrp.mps.operation']
    profiles = operation_model._seed_big_moon_profile()
    if cr.dbname == 'yasser3' and not profiles:
        raise RuntimeError(
            'تعذر تحديث بروفايل MPS لبيج مون: راجع الموديل والوصفات '
            'العادية للكنبة الكبيرة والشازلونج والفوتيه.'
        )
    if profiles:
        operation_model._validate_big_moon_seed_contract(profiles)

    productions = env['furniture.mrp.production'].search([
        ('state', 'in', ('confirmed', 'in_production')),
        ('mps_schedule_plan_ids.state', 'in', (
            'planned',
            'approved',
            'in_progress',
        )),
    ])
    if productions:
        productions._furniture_replan_proposed_mps()
