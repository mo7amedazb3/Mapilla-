import json

_SNAPSHOT_KEY = 'furniture_delivery_requests.previous_navigation'


def pre_init_hook(env):
    menu = env.ref('furniture_mrp.menu_furniture_mrp_future_orders').with_context(lang='en_US')
    action = env.ref('furniture_mrp.action_furniture_mrp_future_order')
    snapshot = {
        'parent_id': menu.parent_id.id, 'name': menu.name, 'sequence': menu.sequence,
        'menu_groups': menu.groups_id.ids, 'action_groups': action.groups_id.ids,
    }
    env['ir.config_parameter'].sudo().set_param(_SNAPSHOT_KEY, json.dumps(snapshot))


def uninstall_hook(env):
    # Uninstalling only the app restores MRP navigation; requests/links stay put.
    params = env['ir.config_parameter'].sudo()
    snapshot = json.loads(params.get_param(_SNAPSHOT_KEY, '{}'))
    menu = env.ref('furniture_mrp.menu_furniture_mrp_future_orders', raise_if_not_found=False)
    action = env.ref('furniture_mrp.action_furniture_mrp_future_order', raise_if_not_found=False)
    if snapshot and menu:
        menu.with_context(lang='en_US').write({
            'parent_id': snapshot['parent_id'], 'name': snapshot['name'],
            'sequence': snapshot['sequence'], 'groups_id': [(6, 0, snapshot['menu_groups'])],
        })
    if snapshot and action:
        action.write({'groups_id': [(6, 0, snapshot['action_groups'])]})
    params.search([('key', '=', _SNAPSHOT_KEY)]).unlink()
