# -*- coding: utf-8 -*-
from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible_ids = super()._visible_menu_ids(debug=debug)
        user = self.env.user
        storekeeper_only = (
            user.has_group('furniture_mrp.group_furniture_mrp_storekeeper')
            and not user.has_group('furniture_mrp.group_furniture_mrp_manager')
            and not user.has_group('furniture_mrp.group_furniture_mrp_supervisor')
        )
        if not storekeeper_only:
            return visible_ids

        hidden_roots = self.browse([
            menu.id for menu in (
                self.env.ref('furniture_mrp.menu_furniture_mrp_root', raise_if_not_found=False),
                self.env.ref('mrp.menu_mrp_root', raise_if_not_found=False),
            ) if menu
        ])
        if not hidden_roots:
            return visible_ids
        hidden_ids = set(self.sudo().with_context(
            active_test=False,
            **{'ir.ui.menu.full_list': True},
        ).search([
            ('id', 'child_of', hidden_roots.ids),
        ]).ids)
        return visible_ids - hidden_ids
