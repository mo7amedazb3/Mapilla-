# -*- coding: utf-8 -*-

import math
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


SPA_PURCHASE_STATE_SELECTION = [
    ('ok', 'Stock OK'),
    ('to_order', 'To Order'),
    ('partial_order', 'Partially Ordered'),
    ('ordered', 'Ordered'),
    ('no_supplier', 'Supplier Missing'),
]
SPA_COMPUTED_FIELDS = [
    'spa_supplier_id',
    'spa_required_qty',
    'spa_ordered_qty',
    'spa_qty_to_order',
    'spa_purchase_state',
]


class FurniturePurchaseRequest(models.Model):
    _inherit = 'furniture.purchase.request'

    spa_central_replenishment = fields.Boolean(
        string='Central Purchase Replenishment',
        readonly=True,
        copy=False,
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals = []
        for incoming_vals in vals_list:
            vals = dict(incoming_vals)
            if (
                vals.get('spa_central_replenishment')
                and (not vals.get('name') or vals['name'] == _('New'))
            ):
                company = self.env['res.company'].browse(
                    vals.get('company_id') or self.env.company.id
                )
                vals['name'] = self.env['ir.sequence'].with_company(
                    company
                ).next_by_code('spa.central.purchase.request') or _('New')
            prepared_vals.append(vals)
        return super().create(prepared_vals)

    @api.model
    def _user_can_submit(self):
        return (
            super()._user_can_submit()
            or self.env.user.has_group(
                'custom_spa_replenishment.group_spa_central_inventory'
            )
        )

    def write(self, vals):
        vals = dict(vals)
        if 'spa_central_replenishment' in vals:
            requested_value = bool(vals.pop('spa_central_replenishment'))
            if any(
                request.spa_central_replenishment != requested_value
                for request in self
            ):
                raise AccessError(_(
                    'The Central Purchase Replenishment marker is managed by '
                    'the system only.'
                ))
        return super().write(vals)


class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    spa_raw_material = fields.Boolean(
        string='Raw Material Purchase Configuration',
        default=False,
        copy=False,
        index=True,
    )
    spa_replenishment_scope = fields.Selection(
        [('central_purchase', 'Central Purchase')],
        string='Replenishment Scope',
        copy=False,
        index=True,
    )
    spa_supplier_id = fields.Many2one(
        'res.partner',
        string='Default Supplier',
        compute='_compute_spa_purchase_metrics',
        search='_search_spa_supplier_id',
    )
    spa_required_qty = fields.Float(
        string='Required To Maximum',
        compute='_compute_spa_purchase_metrics',
        digits='Product Unit of Measure',
    )
    spa_ordered_qty = fields.Float(
        string='Already Ordered',
        compute='_compute_spa_purchase_metrics',
        digits='Product Unit of Measure',
    )
    spa_qty_to_order = fields.Float(
        string='Remaining To Order',
        compute='_compute_spa_purchase_metrics',
        digits='Product Unit of Measure',
    )
    spa_purchase_state = fields.Selection(
        SPA_PURCHASE_STATE_SELECTION,
        string='Status',
        compute='_compute_spa_purchase_metrics',
        search='_search_spa_purchase_state',
    )

    @api.model
    def _search_spa_supplier_id(self, operator, value):
        return [
            ('product_id.product_tmpl_id.seller_ids.partner_id', operator, value),
        ]

    @api.model
    def _search_spa_purchase_state(self, operator, value):
        if operator in ('=', '=='):
            expected = {value}
            negate = False
        elif operator == 'in':
            expected = set(value or [])
            negate = False
        elif operator in ('!=', '<>'):
            expected = {value}
            negate = True
        elif operator == 'not in':
            expected = set(value or [])
            negate = True
        else:
            raise UserError(_('Unsupported status search operator: %s', operator))
        rules = self.search([
            ('active', '=', True),
            ('spa_raw_material', '=', True),
            ('spa_replenishment_scope', '=', 'central_purchase'),
        ])
        rules.invalidate_recordset(SPA_COMPUTED_FIELDS)
        matching_ids = rules.filtered(
            lambda rule: (rule.spa_purchase_state in expected) != negate
        ).ids
        return [('id', 'in', matching_ids)]

    @api.model
    def _spa_single_warehouse(self, company=None):
        """Resolve this installation's sole operational warehouse without DB ids."""
        company = company or self.env.company
        main_warehouse = self.env.ref('stock.warehouse0', raise_if_not_found=False)
        if (
            main_warehouse
            and main_warehouse.active
            and main_warehouse.company_id == company
        ):
            return main_warehouse
        return self.env['stock.warehouse'].search([
            ('company_id', '=', company.id),
            ('active', '=', True),
        ], order='id', limit=1)

    @api.model
    def _spa_ensure_inventory_manager(self):
        if not (
            self.env.user.has_group('stock.group_stock_manager')
            or self.env.user.has_group(
                'custom_spa_replenishment.group_spa_central_inventory'
            )
        ):
            raise AccessError(_(
                'Central Purchase Replenishment is available to Central '
                'Inventory Managers only.'
            ))

    @api.model
    def _spa_managed_rules(self, company=None):
        company = company or self.env.company
        warehouse = self._spa_single_warehouse(company)
        if not warehouse:
            return self.browse()
        buy_route = warehouse.buy_pull_id.route_id
        if not buy_route:
            return self.browse()
        return self.search([
            ('active', '=', True),
            ('company_id', '=', company.id),
            ('warehouse_id', '=', warehouse.id),
            ('location_id', '=', warehouse.lot_stock_id.id),
            ('trigger', '=', 'manual'),
            ('route_id', '=', buy_route.id),
            ('product_id.active', '=', True),
            ('product_id.purchase_ok', '=', True),
            ('product_id.is_storable', '=', True),
            ('product_id.furniture_is_finished_product', '=', False),
            ('spa_raw_material', '=', True),
            ('spa_replenishment_scope', '=', 'central_purchase'),
        ])

    @api.model
    def _spa_is_purchase_material(self, product):
        return bool(
            product
            and product.active
            and product.purchase_ok
            and product.is_storable
            and not product.furniture_is_finished_product
        )

    @api.model
    def _spa_recipe_raw_material_products(self, company=None):
        """Use live furniture recipes as the single-warehouse material source.

        The source system mirrors an already configured branch rule into the
        central warehouse.  This database intentionally has no branch, so a
        live recipe line is the equivalent authoritative raw-material signal.
        Existing Buy rules remain candidates as well.
        """
        company = company or self.env.company
        StageLine = self.env['furniture.mrp.bom.stage.line']
        lines = StageLine.search([
            ('product_id', '!=', False),
            ('product_id.active', '=', True),
            ('product_id.purchase_ok', '=', True),
        ])
        lines = lines.filtered(lambda line: (
            (line.bom_id or line.stage_id.bom_id).active
            and (
                not (line.bom_id or line.stage_id.bom_id).company_id
                or (line.bom_id or line.stage_id.bom_id).company_id == company
            )
        ))
        return lines.mapped('product_id').filtered(lambda product: (
            product.active
            and product.purchase_ok
            and product.is_storable
            and not product.furniture_is_finished_product
        ))

    @api.model
    def _spa_sync_single_warehouse_materials(self, company=None):
        company = company or self.env.company
        warehouse = self._spa_single_warehouse(company)
        if not warehouse or not warehouse.lot_stock_id:
            raise UserError(_('No Stock warehouse is configured for this company.'))

        buy_route = warehouse.buy_pull_id.route_id
        if not buy_route:
            raise UserError(_('The Buy route is not configured for the Stock warehouse.'))
        Orderpoint = self.with_company(company).with_context(active_test=False)
        existing = Orderpoint.search([
            ('company_id', '=', company.id),
            ('warehouse_id', '=', warehouse.id),
            ('location_id', '=', warehouse.lot_stock_id.id),
        ])
        previously_managed = existing.filtered(lambda rule: (
            rule.spa_replenishment_scope == 'central_purchase'
            or rule.spa_raw_material
        ))
        valid_previously_managed = previously_managed.filtered(lambda rule: (
            rule.active
            and self._spa_is_purchase_material(rule.product_id)
            and (not rule.route_id or rule.route_id == buy_route)
        ))
        invalid_previously_managed = previously_managed - valid_previously_managed
        if invalid_previously_managed:
            invalid_previously_managed.write({
                'spa_raw_material': False,
                'spa_replenishment_scope': False,
            })

        # Existing automatic or non-Buy rules belong to another replenishment
        # workflow.  Never repurpose or disable those rules during installation.
        adoptable = existing.filtered(lambda rule: (
            rule.active
            and self._spa_is_purchase_material(rule.product_id)
            and (not rule.route_id or rule.route_id == buy_route)
            and (
                rule in valid_previously_managed
                or rule.trigger == 'manual'
            )
        ))
        products = adoptable.mapped('product_id') | self._spa_recipe_raw_material_products(
            company
        )
        rules_by_product = {rule.product_id.id: rule for rule in existing}

        for rule in adoptable:
            values = {
                'spa_raw_material': True,
                'spa_replenishment_scope': 'central_purchase',
                'trigger': 'manual',
            }
            if not rule.route_id and buy_route:
                values['route_id'] = buy_route.id
            rule.write(values)

        to_create = []
        for product in products.sorted(lambda item: (item.display_name or '', item.id)):
            rule = rules_by_product.get(product.id)
            if rule:
                # A unique rule already exists but is automatic, archived, or
                # uses another route.  Leave it untouched instead of changing
                # an existing replenishment policy behind the user's back.
                continue
            values = {
                'product_id': product.id,
                'company_id': company.id,
                'warehouse_id': warehouse.id,
                'location_id': warehouse.lot_stock_id.id,
                'trigger': 'manual',
                'product_min_qty': 0.0,
                'product_max_qty': 0.0,
                'spa_raw_material': True,
                'spa_replenishment_scope': 'central_purchase',
            }
            if buy_route:
                values['route_id'] = buy_route.id
            to_create.append(values)
        if to_create:
            Orderpoint.create(to_create)
        return True

    @api.autovacuum
    def _unlink_processed_orderpoints(self):
        """Keep the dashboard's persistent manual rules out of Core cleanup.

        Odoo also creates temporary manual 0/0 orderpoints for its standard
        replenishment report and removes them after processing.  These central
        purchase rules are user configuration, even while their initial limits
        are 0/0, so they must survive that cleanup.
        """
        if self.ids:
            candidates = self.filtered(lambda rule: not (
                rule.spa_raw_material
                and rule.spa_replenishment_scope == 'central_purchase'
            ))
        else:
            candidates = self.with_context(active_test=False).search([
                '|',
                ('spa_raw_material', '=', False),
                ('spa_replenishment_scope', '!=', 'central_purchase'),
            ])
        if not candidates:
            return candidates
        return super(StockWarehouseOrderpoint, candidates)._unlink_processed_orderpoints()

    @api.depends(
        'product_id',
        'product_min_qty',
        'product_max_qty',
        'qty_on_hand',
        'qty_forecast',
        'warehouse_id',
        'company_id',
    )
    def _compute_spa_purchase_metrics(self):
        ordered_by_key = defaultdict(float)
        rules = self.filtered(lambda rule: (
            rule.product_id and rule.warehouse_id and rule.company_id
        ))
        products = rules.mapped('product_id')
        warehouses = rules.mapped('warehouse_id')
        companies = rules.mapped('company_id')

        if products and warehouses and companies:
            purchase_lines = self.env['purchase.order.line'].search([
                ('display_type', '=', False),
                ('product_id', 'in', products.ids),
                ('order_id.company_id', 'in', companies.ids),
                ('order_id.picking_type_id.warehouse_id', 'in', warehouses.ids),
                ('order_id.state', 'in', ('draft', 'sent', 'to approve', 'purchase')),
            ])
            for line in purchase_lines:
                remaining = max((line.product_qty or 0.0) - (line.qty_received or 0.0), 0.0)
                if float_is_zero(remaining, precision_rounding=line.product_uom.rounding):
                    continue
                quantity = line.product_uom._compute_quantity(
                    remaining,
                    line.product_id.uom_id,
                    round=False,
                )
                warehouse = line.order_id.picking_type_id.warehouse_id
                ordered_by_key[(warehouse.id, line.product_id.id)] += quantity

            request_lines = self.env['furniture.purchase.request.line'].search([
                ('product_id', 'in', products.ids),
                ('request_id.company_id', 'in', companies.ids),
                ('request_id.warehouse_id', 'in', warehouses.ids),
                ('request_id.spa_central_replenishment', '=', True),
                ('request_id.state', 'in', ('draft', 'requested')),
                ('purchase_order_id', '=', False),
            ])
            for line in request_lines:
                quantity = line.product_uom_id._compute_quantity(
                    line.quantity,
                    line.product_id.uom_id,
                    round=False,
                )
                ordered_by_key[(line.request_id.warehouse_id.id, line.product_id.id)] += quantity

        today = fields.Date.context_today(self)
        for rule in self:
            if not rule.product_id:
                rule.spa_supplier_id = False
                rule.spa_required_qty = 0.0
                rule.spa_ordered_qty = 0.0
                rule.spa_qty_to_order = 0.0
                rule.spa_purchase_state = 'ok'
                continue

            product = rule.product_id
            sellers = product.with_company(rule.company_id)._prepare_sellers(False).filtered(
                lambda seller: (
                    (not seller.company_id or seller.company_id == rule.company_id)
                    and (not seller.date_start or seller.date_start <= today)
                    and (not seller.date_end or seller.date_end >= today)
                )
            ).sorted(lambda seller: (seller.sequence, seller.id))
            supplier = sellers[:1].partner_id
            rounding = product.uom_id.rounding
            on_hand = rule.qty_on_hand or 0.0
            minimum = rule.product_min_qty or 0.0
            maximum = rule.product_max_qty or 0.0
            threshold_reached = float_compare(
                on_hand, minimum, precision_rounding=rounding
            ) <= 0
            required = max(maximum - on_hand, 0.0) if threshold_reached else 0.0
            ordered = ordered_by_key[(rule.warehouse_id.id, product.id)]
            remaining = max(required - ordered, 0.0)

            required_zero = float_is_zero(required, precision_rounding=rounding)
            ordered_zero = float_is_zero(ordered, precision_rounding=rounding)
            remaining_zero = float_is_zero(remaining, precision_rounding=rounding)
            if not threshold_reached:
                state = 'ok'
            elif required_zero:
                state = 'ordered' if not ordered_zero else 'ok'
            elif remaining_zero:
                state = 'ordered'
            elif not ordered_zero:
                state = 'partial_order'
            elif supplier:
                state = 'to_order'
            else:
                state = 'no_supplier'

            rule.spa_supplier_id = supplier
            rule.spa_required_qty = required
            rule.spa_ordered_qty = ordered
            rule.spa_qty_to_order = remaining
            rule.spa_purchase_state = state

    @api.model
    def spa_central_purchase_dashboard_data(self):
        self._spa_ensure_inventory_manager()
        rules = self._spa_managed_rules()
        rules.invalidate_recordset(SPA_COMPUTED_FIELDS)
        state_labels = dict(SPA_PURCHASE_STATE_SELECTION)
        rows = []
        supplier_ids = set()
        for rule in rules.sorted(lambda item: (
            (item.product_id.display_name or '').casefold(), item.id
        )):
            if rule.spa_supplier_id:
                supplier_ids.add(rule.spa_supplier_id.id)
            rows.append({
                'id': rule.id,
                'product': rule.product_id.display_name,
                'supplier': rule.spa_supplier_id.display_name or _('No Supplier'),
                'on_hand': rule.qty_on_hand,
                'forecast': rule.qty_forecast,
                'minimum': rule.product_min_qty,
                'maximum': rule.product_max_qty,
                'required_qty': rule.spa_required_qty,
                'ordered_qty': rule.spa_ordered_qty,
                'remaining_qty': rule.spa_qty_to_order,
                'unit': rule.product_uom_name,
                'status': rule.spa_purchase_state,
                'status_label': state_labels.get(rule.spa_purchase_state, ''),
                'can_prepare': rule.spa_purchase_state in (
                    'to_order', 'partial_order', 'no_supplier'
                ) and not float_is_zero(
                    rule.spa_qty_to_order,
                    precision_rounding=rule.product_id.uom_id.rounding,
                ),
            })
        return {
            'rows': rows,
            'summary': {
                'total_items': len(rows),
                'need_reorder': sum(
                    row['status'] in ('to_order', 'partial_order', 'no_supplier')
                    for row in rows
                ),
                'stock_ok': sum(row['status'] == 'ok' for row in rows),
                'suppliers': len(supplier_ids),
            },
        }

    @api.model
    def spa_sync_purchase_materials(self):
        self._spa_ensure_inventory_manager()
        self.check_access('create')
        self.check_access('write')
        self._spa_sync_single_warehouse_materials()
        return self.spa_central_purchase_dashboard_data()

    @api.model
    def spa_update_central_purchase_limits(self, rule_id, minimum, maximum):
        self._spa_ensure_inventory_manager()
        try:
            rule_id = int(rule_id)
            minimum = float(minimum)
            maximum = float(maximum)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError(_('Minimum and maximum must be valid numbers.'))
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            raise ValidationError(_('Minimum and maximum must be finite numbers.'))
        if minimum < 0 or maximum < 0:
            raise ValidationError(_('Minimum and maximum cannot be negative.'))
        if minimum > maximum:
            raise ValidationError(_(
                'The minimum quantity must be less than or equal to the maximum quantity.'
            ))

        rule = self.browse(rule_id).exists()
        if not rule:
            raise UserError(_('The selected replenishment rule is no longer available.'))
        managed = self._spa_managed_rules(rule.company_id)
        if rule not in managed:
            raise AccessError(_('This replenishment rule is outside the Stock warehouse.'))
        rule.check_access('write')
        rule.write({
            'product_min_qty': minimum,
            'product_max_qty': maximum,
        })
        return self.spa_central_purchase_dashboard_data()

    @api.model
    def spa_prepare_central_purchase_rfq(self, rule_ids):
        self._spa_ensure_inventory_manager()
        try:
            ids = sorted({int(rule_id) for rule_id in (rule_ids or []) if rule_id})
        except (TypeError, ValueError, OverflowError):
            raise UserError(_('The selected replenishment rules are invalid.'))
        if not ids:
            raise UserError(_('Select at least one material first.'))

        self.env.cr.execute(
            'SELECT id FROM stock_warehouse_orderpoint '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(ids)],
        )
        rules = self.browse(ids).exists()
        if len(rules) != len(ids):
            raise UserError(_('One or more replenishment rules are no longer available.'))
        managed = self._spa_managed_rules()
        if rules - managed:
            raise AccessError(_('One or more rules are outside the Stock warehouse.'))

        rules.invalidate_recordset(SPA_COMPUTED_FIELDS)
        lines = self.browse()
        for rule in rules:
            if rule.spa_purchase_state not in ('to_order', 'partial_order', 'no_supplier'):
                continue
            if float_is_zero(
                rule.spa_qty_to_order,
                precision_rounding=rule.product_id.uom_id.rounding,
            ):
                continue
            lines |= rule
        if not lines:
            raise UserError(_('The selected materials no longer need a purchase request.'))

        warehouse = self._spa_single_warehouse(self.env.company)
        request = self.env['furniture.purchase.request'].create({
            'company_id': self.env.company.id,
            'warehouse_id': warehouse.id,
            'requested_by_id': self.env.user.id,
            'spa_central_replenishment': True,
            'note': _('Created from Central Purchase Replenishment.'),
        })
        RequestLine = self.env['furniture.purchase.request.line']
        for rule in lines:
            RequestLine.create({
                'request_id': request.id,
                'product_id': rule.product_id.id,
                'quantity': rule.spa_qty_to_order,
                'product_uom_id': rule.product_id.uom_id.id,
            })

        action = self.env['ir.actions.actions']._for_xml_id(
            'custom_spa_replenishment.action_spa_central_purchase_request'
        )
        form_view = self.env.ref(
            'custom_spa_replenishment.view_spa_central_purchase_request_form'
        )
        action.update({
            'views': [(form_view.id, 'form')],
            'res_id': request.id,
            'target': 'current',
        })
        return action

    def action_spa_prepare_rfq(self):
        return self.spa_prepare_central_purchase_rfq(self.ids)
