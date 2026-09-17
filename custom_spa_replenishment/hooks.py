# -*- coding: utf-8 -*-


def post_init_hook(env):
    """Seed safe manual Buy rules for the raw materials used by live recipes."""
    administrator = env.ref('base.user_admin')
    env['stock.warehouse.orderpoint'].with_user(administrator).with_company(
        administrator.company_id
    )._spa_sync_single_warehouse_materials()
