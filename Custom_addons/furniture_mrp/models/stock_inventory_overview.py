# -*- coding: utf-8 -*-

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError


class StockLocation(models.Model):
    _inherit = 'stock.location'

    @api.model
    def _check_furniture_inventory_overview_access(self):
        if not (
            self.env.user.has_group('stock.group_stock_user')
            or self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_storekeeper'
            )
        ):
            raise AccessError(_('شاشة مخازن المصنع متاحة لمستخدمي المخزون فقط.'))

    @api.model
    def _furniture_inventory_overview_locations(self):
        company = self.env.company
        warehouse = self.env.user.property_warehouse_id
        if not warehouse or warehouse.company_id != company:
            warehouse = self.env.ref('stock.warehouse0', raise_if_not_found=False)
        if not warehouse or warehouse.company_id != company:
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', company.id),
            ], order='id', limit=1)

        stock_location = warehouse.lot_stock_id if warehouse else self.browse()
        finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        if (
            finished_location
            and finished_location.company_id
            and finished_location.company_id != company
        ):
            finished_location = self.browse()
        return stock_location, finished_location

    @api.model
    def _furniture_inventory_location_card(self, location, key, title, icon):
        quant_domain = [
            ('company_id', '=', self.env.company.id),
            ('location_id', 'child_of', location.id),
        ]
        product_groups = self.env['stock.quant']._read_group(
            quant_domain,
            ['product_id'],
            ['quantity:sum'],
            having=[('quantity:sum', '!=', 0)],
        )
        pending_move_count = self.env['stock.move'].search_count([
            ('company_id', '=', self.env.company.id),
            ('state', 'not in', ('done', 'cancel')),
            '|',
            ('location_id', 'child_of', location.id),
            ('location_dest_id', 'child_of', location.id),
        ])
        return {
            'key': key,
            'title': title,
            'subtitle': location.complete_name,
            'location_id': location.id,
            'product_count': len(product_groups),
            'pending_move_count': pending_move_count,
            'icon': icon,
        }

    @api.model
    def _furniture_pending_store_request_data(self):
        user = self.env.user
        is_storekeeper = user.has_group(
            'furniture_mrp.group_furniture_mrp_storekeeper'
        )
        is_stock_manager = user.has_group('stock.group_stock_manager')
        if not (is_storekeeper or is_stock_manager):
            return {
                'is_storekeeper': False,
                'total': 0,
                'regular_count': 0,
                'advance_count': 0,
            }

        common_domain = [
            ('state', '=', 'pending'),
            ('company_id', '=', self.env.company.id),
        ]
        if is_storekeeper and not is_stock_manager:
            common_domain.append(('assigned_to_id', '=', user.id))
        regular_count = self.env['furniture.mrp.store.request'].search_count(
            common_domain,
        )
        advance_count = self.env[
            'furniture.mrp.advance.material.release'
        ].search_count(common_domain)
        return {
            'is_storekeeper': is_storekeeper,
            'total': regular_count + advance_count,
            'regular_count': regular_count,
            'advance_count': advance_count,
        }

    @api.model
    def furniture_inventory_overview_data(self):
        """Return live Stock/Finished cards and requests awaiting the keeper."""
        self._check_furniture_inventory_overview_access()
        stock_location, finished_location = (
            self._furniture_inventory_overview_locations()
        )
        if not stock_location or not finished_location:
            raise UserError(_(
                'تعذر العثور على مخزن Stock أو مخزن الإنتاج التام لهذه الشركة.'
            ))
        return {
            'locations': [
                self._furniture_inventory_location_card(
                    stock_location,
                    'stock',
                    _('مخزن الخامات (Stock)'),
                    'fa-cubes',
                ),
                self._furniture_inventory_location_card(
                    finished_location,
                    'finished',
                    _('مخزن الإنتاج التام'),
                    'fa-check-circle',
                ),
            ],
            'pending': self._furniture_pending_store_request_data(),
        }

    @api.model
    def furniture_open_inventory_location(self, location_id):
        self._check_furniture_inventory_overview_access()
        stock_location, finished_location = (
            self._furniture_inventory_overview_locations()
        )
        allowed_locations = (stock_location | finished_location).exists()
        location = self.browse(location_id).exists()
        if not location or location not in allowed_locations:
            raise AccessError(_('هذا الموقع غير متاح من كروت مخازن المصنع.'))

        action = self.env['stock.quant'].with_context(
            inventory_mode=False,
            always_show_loc=True,
            create=False,
        )._get_quants_action([
            ('location_id', 'child_of', location.id),
            ('quantity', '!=', 0),
        ], extend=True)
        action.update({
            'name': _('رصيد %s') % location.display_name,
            'context': dict(action.get('context') or {}, create=False),
        })
        return action

    @api.model
    def furniture_open_pending_store_requests(self, request_kind):
        self._check_furniture_inventory_overview_access()
        user = self.env.user
        is_storekeeper = user.has_group(
            'furniture_mrp.group_furniture_mrp_storekeeper'
        )
        is_stock_manager = user.has_group('stock.group_stock_manager')
        if not (is_storekeeper or is_stock_manager):
            raise AccessError(_('الطلبات المعلقة متاحة لأمين المخزن فقط.'))

        action_xmlids = {
            'regular': 'furniture_mrp.action_furniture_mrp_store_request',
            'advance': (
                'furniture_mrp.action_furniture_mrp_advance_material_release'
            ),
        }
        action_xmlid = action_xmlids.get(request_kind)
        if not action_xmlid:
            raise UserError(_('نوع طلب المخزن غير صحيح.'))
        action = self.env['ir.actions.actions']._for_xml_id(action_xmlid)
        domain = [
            ('state', '=', 'pending'),
            ('company_id', '=', self.env.company.id),
        ]
        if is_storekeeper and not is_stock_manager:
            domain.append(('assigned_to_id', '=', user.id))
        action.update({
            'domain': domain,
            'context': {},
        })
        return action
