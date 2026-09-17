# -*- coding: utf-8 -*-

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestSingleWarehousePurchaseReplenishment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env.ref('stock.warehouse0')
        cls.manager = new_test_user(
            cls.env,
            login='spa_replenishment_inventory_manager',
            groups='stock.group_stock_manager',
            company_id=cls.company.id,
        )
        cls.central_manager = new_test_user(
            cls.env,
            login='spa_replenishment_central_manager',
            groups='custom_spa_replenishment.group_spa_central_inventory',
            company_id=cls.company.id,
        )
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Replenishment Test Vendor',
            'supplier_rank': 1,
        })

    def _create_product(self, name, with_vendor=False):
        product = self.env['product.product'].create({
            'name': name,
            'is_storable': True,
            'purchase_ok': True,
            'sale_ok': False,
        })
        if with_vendor:
            self.env['product.supplierinfo'].create({
                'partner_id': self.vendor.id,
                'product_tmpl_id': product.product_tmpl_id.id,
                'sequence': 1,
                'min_qty': 0.0,
                'price': 10.0,
            })
        return product

    def _create_rule(self, product, minimum=5.0, maximum=10.0, warehouse=None):
        warehouse = warehouse or self.warehouse
        values = {
            'product_id': product.id,
            'company_id': self.company.id,
            'warehouse_id': warehouse.id,
            'location_id': warehouse.lot_stock_id.id,
            'trigger': 'manual',
            'product_min_qty': minimum,
            'product_max_qty': maximum,
            'spa_raw_material': True,
            'spa_replenishment_scope': 'central_purchase',
        }
        buy_route = warehouse.buy_pull_id.route_id
        if buy_route:
            values['route_id'] = buy_route.id
        return self.env['stock.warehouse.orderpoint'].create(values)

    def _set_stock(self, product, quantity, warehouse=None):
        warehouse = warehouse or self.warehouse
        location = warehouse.lot_stock_id
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', location.id),
        ])
        current = sum(quants.mapped('quantity'))
        self.env['stock.quant']._update_available_quantity(
            product,
            location,
            quantity - current,
        )
        product.invalidate_recordset([
            'qty_available',
            'virtual_available',
            'free_qty',
            'incoming_qty',
            'outgoing_qty',
        ])

    @staticmethod
    def _invalidate_metrics(rule):
        rule.invalidate_recordset([
            'qty_on_hand',
            'qty_forecast',
            'spa_supplier_id',
            'spa_required_qty',
            'spa_ordered_qty',
            'spa_qty_to_order',
            'spa_purchase_state',
        ])

    def _draft_request_line(self, product, quantity, central=True):
        request_values = {
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
        }
        if central:
            request_values['spa_central_replenishment'] = True
        request = self.env['furniture.purchase.request'].with_user(
            self.manager
        ).create(request_values)
        self.env['furniture.purchase.request.line'].with_user(
            self.manager
        ).create({
            'request_id': request.id,
            'product_id': product.id,
            'quantity': quantity,
            'product_uom_id': product.uom_id.id,
        })
        return request

    def test_sync_leaves_existing_automatic_rule_untouched(self):
        product = self._create_product('Existing Automatic Replenishment Material')
        automatic_rule = self.env['stock.warehouse.orderpoint'].create({
            'product_id': product.id,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'route_id': self.warehouse.buy_pull_id.route_id.id,
            'trigger': 'auto',
            'product_min_qty': 2.0,
            'product_max_qty': 7.0,
        })

        self.env['stock.warehouse.orderpoint'].with_user(
            self.manager
        ).spa_sync_purchase_materials()
        automatic_rule.invalidate_recordset()

        self.assertEqual(automatic_rule.trigger, 'auto')
        self.assertFalse(automatic_rule.spa_raw_material)
        self.assertFalse(automatic_rule.spa_replenishment_scope)
        self.assertAlmostEqual(automatic_rule.product_min_qty, 2.0)
        self.assertAlmostEqual(automatic_rule.product_max_qty, 7.0)

    def test_central_inventory_group_can_open_dashboard(self):
        self.assertTrue(self.central_manager.has_group(
            'custom_spa_replenishment.group_spa_central_inventory'
        ))
        self.assertFalse(self.central_manager.has_group('stock.group_stock_manager'))

        data = self.env['stock.warehouse.orderpoint'].with_user(
            self.central_manager
        ).spa_central_purchase_dashboard_data()
        self.assertIn('rows', data)
        self.assertIn('summary', data)

    def test_zero_limit_central_rule_survives_core_manual_cleanup(self):
        product = self._create_product('Persistent Zero Limit Material')
        rule = self._create_rule(product, minimum=0.0, maximum=0.0)

        removed = self.env['stock.warehouse.orderpoint']._unlink_processed_orderpoints()

        self.assertNotIn(rule.id, removed.ids)
        self.assertTrue(rule.exists())

    def test_unrelated_purchase_request_is_not_double_counted(self):
        product = self._create_product(
            'Unrelated Request Replenishment Material',
            with_vendor=True,
        )
        rule = self._create_rule(product)
        self._set_stock(product, 2.0)
        self._draft_request_line(product, 3.0, central=False)
        self._invalidate_metrics(rule)

        self.assertAlmostEqual(rule.spa_required_qty, 8.0)
        self.assertAlmostEqual(rule.spa_ordered_qty, 0.0)
        self.assertAlmostEqual(rule.spa_qty_to_order, 8.0)
        self.assertEqual(rule.spa_purchase_state, 'to_order')

    def test_need_calculation_and_all_purchase_states(self):
        product = self._create_product('Replenishment State Material')
        rule = self._create_rule(product)
        self._set_stock(product, 2.0)
        self._invalidate_metrics(rule)

        self.assertAlmostEqual(rule.qty_on_hand, 2.0)
        self.assertAlmostEqual(rule.spa_required_qty, 8.0)
        self.assertAlmostEqual(rule.spa_ordered_qty, 0.0)
        self.assertAlmostEqual(rule.spa_qty_to_order, 8.0)
        self.assertEqual(rule.spa_purchase_state, 'no_supplier')

        self.env['product.supplierinfo'].create({
            'partner_id': self.vendor.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'sequence': 1,
            'price': 10.0,
        })
        self._invalidate_metrics(rule)
        self.assertEqual(rule.spa_supplier_id, self.vendor)
        self.assertEqual(rule.spa_purchase_state, 'to_order')

        request = self._draft_request_line(product, 3.0)
        self._invalidate_metrics(rule)
        self.assertAlmostEqual(rule.spa_ordered_qty, 3.0)
        self.assertAlmostEqual(rule.spa_qty_to_order, 5.0)
        self.assertEqual(rule.spa_purchase_state, 'partial_order')

        self.env['furniture.purchase.request.line'].with_user(
            self.manager
        ).create({
            'request_id': request.id,
            'product_id': product.id,
            'quantity': 5.0,
            'product_uom_id': product.uom_id.id,
        })
        self._invalidate_metrics(rule)
        self.assertAlmostEqual(rule.spa_ordered_qty, 8.0)
        self.assertAlmostEqual(rule.spa_qty_to_order, 0.0)
        self.assertEqual(rule.spa_purchase_state, 'ordered')

        self._set_stock(product, 6.0)
        self._invalidate_metrics(rule)
        self.assertAlmostEqual(rule.spa_required_qty, 0.0)
        self.assertEqual(rule.spa_purchase_state, 'ok')

    def test_only_main_stock_warehouse_is_managed(self):
        second_warehouse = self.env['stock.warehouse'].create({
            'name': 'Replenishment Test Secondary Warehouse',
            'code': 'RTS2',
            'company_id': self.company.id,
        })
        main_product = self._create_product('Main Warehouse Material')
        outside_product = self._create_product('Secondary Warehouse Material')
        main_rule = self._create_rule(main_product)
        outside_rule = self._create_rule(
            outside_product,
            warehouse=second_warehouse,
        )

        model = self.env['stock.warehouse.orderpoint'].with_user(self.manager)
        self.assertEqual(model._spa_single_warehouse(), self.warehouse)
        managed_rules = model._spa_managed_rules()
        self.assertIn(main_rule.id, managed_rules.ids)
        self.assertNotIn(outside_rule.id, managed_rules.ids)

        dashboard = model.spa_central_purchase_dashboard_data()
        dashboard_ids = {row['id'] for row in dashboard['rows']}
        self.assertIn(main_rule.id, dashboard_ids)
        self.assertNotIn(outside_rule.id, dashboard_ids)
        with self.assertRaises(AccessError):
            model.spa_update_central_purchase_limits(
                outside_rule.id,
                2.0,
                4.0,
            )

    def test_update_limits_returns_refreshed_dashboard_and_validates_values(self):
        product = self._create_product(
            'Replenishment Editable Limits Material',
            with_vendor=True,
        )
        rule = self._create_rule(product, minimum=1.0, maximum=2.0)
        model = self.env['stock.warehouse.orderpoint'].with_user(self.manager)

        result = model.spa_update_central_purchase_limits(
            rule.id,
            4.0,
            12.0,
        )
        rule.invalidate_recordset(['product_min_qty', 'product_max_qty'])
        self.assertAlmostEqual(rule.product_min_qty, 4.0)
        self.assertAlmostEqual(rule.product_max_qty, 12.0)
        row = next(row for row in result['rows'] if row['id'] == rule.id)
        self.assertAlmostEqual(row['minimum'], 4.0)
        self.assertAlmostEqual(row['maximum'], 12.0)

        invalid_values = (
            ('not-a-number', 12.0),
            (float('nan'), 12.0),
            (-1.0, 12.0),
            (8.0, 7.0),
        )
        for minimum, maximum in invalid_values:
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(ValidationError):
                    model.spa_update_central_purchase_limits(
                        rule.id,
                        minimum,
                        maximum,
                    )

    def test_prepare_creates_central_furniture_purchase_request(self):
        product = self._create_product(
            'Replenishment Prepare Material',
            with_vendor=True,
        )
        rule = self._create_rule(product)
        self._set_stock(product, 2.0)
        self._invalidate_metrics(rule)
        self.assertEqual(rule.spa_purchase_state, 'to_order')
        self.assertAlmostEqual(rule.spa_qty_to_order, 8.0)

        model = self.env['stock.warehouse.orderpoint'].with_user(self.manager)
        action = model.spa_prepare_central_purchase_rfq([rule.id])
        request = self.env['furniture.purchase.request'].browse(
            action['res_id']
        ).exists().ensure_one()
        line = request.line_ids.ensure_one()

        self.assertTrue(request.spa_central_replenishment)
        self.assertEqual(request.company_id, self.company)
        self.assertEqual(request.warehouse_id, self.warehouse)
        self.assertEqual(request.requested_by_id, self.manager)
        self.assertEqual(request.state, 'draft')
        self.assertTrue(request.name.startswith('CPR/'))
        self.assertEqual(line.product_id, product)
        self.assertEqual(line.product_uom_id, product.uom_id)
        self.assertAlmostEqual(line.quantity, 8.0)
        self.assertEqual(line.suggested_supplier_id, self.vendor)
        self.assertFalse(request.purchase_order_ids)
        with self.assertRaises(AccessError):
            request.with_user(self.manager).write({
                'spa_central_replenishment': False,
            })

        form_view = self.env.ref(
            'custom_spa_replenishment.view_spa_central_purchase_request_form'
        )
        self.assertEqual(action['res_model'], 'furniture.purchase.request')
        self.assertEqual(action['res_id'], request.id)
        self.assertEqual(action['views'], [(form_view.id, 'form')])
        self.assertEqual(action['target'], 'current')

        self._invalidate_metrics(rule)
        self.assertAlmostEqual(rule.spa_ordered_qty, 8.0)
        self.assertAlmostEqual(rule.spa_qty_to_order, 0.0)
        self.assertEqual(rule.spa_purchase_state, 'ordered')
        with self.assertRaises(UserError):
            model.spa_prepare_central_purchase_rfq([rule.id])
