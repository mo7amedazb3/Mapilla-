# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    operation_model = env['furniture.mrp.mps.operation']
    profiles = operation_model._seed_big_moon_profile()
    if cr.dbname == 'yasser3' and not profiles:
        raise RuntimeError(
            'تعذر إنشاء بروفايل MPS لبيج مون: راجع الموديل والوصفات '
            'العادية للكنبة الكبيرة والشازلونج والفوتيه، وتأكد من عدم وجود '
            'أكثر من وصفة مرشحة لنفس الصنف أو أكثر من شركة مرشحة.'
        )
    if profiles:
        operation_model._validate_big_moon_seed_contract(profiles)
