from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestFurnitureInventoryOverviewLocations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.finished_location = cls.env.ref(
            'furniture_mrp.location_finished_goods'
        )
        cls.storekeeper = new_test_user(
            cls.env,
            login='furniture_inventory_overview_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
            company_id=cls.company.id,
        )
        cls.outsider = new_test_user(
            cls.env,
            login='furniture_inventory_overview_outsider',
            groups='base.group_user',
            company_id=cls.company.id,
        )
        cls.raw_product = cls.env['product.product'].create({
            'name': 'Overview Raw Material',
            'is_storable': True,
        })
        cls.finished_product = cls.env['product.product'].create({
            'name': 'Overview Finished Product',
            'is_storable': True,
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.raw_product, cls.stock_location, 7.0,
        )
        cls.env['stock.quant']._update_available_quantity(
            cls.finished_product, cls.finished_location, 3.0,
        )

        production = cls.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        cls.regular_request = cls.env['furniture.mrp.store.request'].create({
            'production_id': production.id,
            'stage_code': 'priming',
            'stage_order_model': 'furniture.mrp.priming',
            'stage_order_res_id': 1,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'assigned_to_id': cls.storekeeper.id,
            'payload_json': {},
        })
        cls.advance_request = cls.env[
            'furniture.mrp.advance.material.release'
        ].create({
            'company_id': cls.company.id,
            'assigned_to_id': cls.storekeeper.id,
        })

    def test_overview_returns_real_stock_and_finished_locations(self):
        data = self.env['stock.location'].with_user(
            self.storekeeper
        ).furniture_inventory_overview_data()
        cards = {card['key']: card for card in data['locations']}

        self.assertEqual(set(cards), {'stock', 'finished'})
        self.assertEqual(cards['stock']['location_id'], self.stock_location.id)
        self.assertEqual(
            cards['finished']['location_id'], self.finished_location.id,
        )
        self.assertGreaterEqual(cards['stock']['product_count'], 1)
        self.assertGreaterEqual(cards['finished']['product_count'], 1)
        self.assertEqual(data['pending']['regular_count'], 1)
        self.assertEqual(data['pending']['advance_count'], 1)
        self.assertEqual(data['pending']['total'], 2)

    def test_location_card_action_is_read_only_and_location_scoped(self):
        action = self.env['stock.location'].with_user(
            self.storekeeper
        ).furniture_open_inventory_location(self.stock_location.id)

        self.assertEqual(action['res_model'], 'stock.quant')
        self.assertIn(
            ('location_id', 'child_of', self.stock_location.id),
            action['domain'],
        )
        self.assertFalse(action['context']['inventory_mode'])
        self.assertFalse(action['context']['create'])

        with self.assertRaises(AccessError):
            self.env['stock.location'].with_user(
                self.storekeeper
            ).furniture_open_inventory_location(
                self.env.ref('stock.stock_location_customers').id,
            )

    def test_pending_action_is_limited_to_assigned_storekeeper(self):
        action = self.env['stock.location'].with_user(
            self.storekeeper
        ).furniture_open_pending_store_requests('advance')

        self.assertEqual(
            action['res_model'], 'furniture.mrp.advance.material.release',
        )
        self.assertIn(('state', '=', 'pending'), action['domain'])
        self.assertIn(
            ('assigned_to_id', '=', self.storekeeper.id), action['domain'],
        )

    def test_non_inventory_user_cannot_call_dashboard_rpc(self):
        with self.assertRaises(AccessError):
            self.env['stock.location'].with_user(
                self.outsider
            ).furniture_inventory_overview_data()

    def test_inventory_overview_uses_custom_renderer_only(self):
        view = self.env.ref(
            'furniture_mrp.view_stock_picking_type_kanban_furniture_locations'
        )
        self.assertIn(
            'furniture_stock_dashboard_kanban', view.arch_db,
        )
