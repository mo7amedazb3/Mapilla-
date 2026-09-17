from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestFutureDeliveryOrder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.finished = cls.env.ref('furniture_mrp.location_finished_goods')
        cls.manager = new_test_user(
            cls.env,
            login='future_delivery_order_manager',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_manager'
            ),
            company_id=cls.company.id,
        )
        cls.model = cls.env['furniture.product.model'].create({
            'name': 'Future Delivery Model',
        })
        cls.other_model = cls.env['furniture.product.model'].create({
            'name': 'Future Delivery Other Model',
        })
        cls.buyer = cls.env['res.partner'].create({
            'name': 'Future Delivery Buyer',
            'is_company': True,
            'customer_rank': 1,
        })
        cls.consumer = cls.env['res.partner'].create({
            'name': 'Future Delivery Consumer',
            'customer_rank': 1,
        })
        cls.product, cls.product_bom = cls._create_component(
            'Future Delivery Sofa', cls.model,
        )

    @classmethod
    def _create_component(cls, name, model):
        product = cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': model.id,
        })
        bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': product.id,
            'furniture_model_id': model.id,
            'use_priming': True,
        })
        # Creating a model-routed root BoM also creates/updates its operational
        # hidden model recipe.  Future delivery production deliberately uses
        # that exact resolver, so assert against the resolved recipe rather
        # than the editable root record.
        operational_bom = cls.env['mrp.bom']._find_furniture_production_recipe(
            product,
            model,
            company=cls.company,
        )
        return product, operational_bom or bom

    @classmethod
    def _create_order(cls, product=None, model=None, quantity=1.0, days=10):
        product = product or cls.product
        model = model or cls.model
        return cls.env['furniture.mrp.future.order'].with_user(cls.manager).create({
            'delivery_date': fields.Date.context_today(
                cls.env['furniture.mrp.future.order']
            ) + timedelta(days=days),
            'buyer_partner_id': cls.buyer.id,
            'beneficiary_partner_id': cls.consumer.id,
            'line_ids': [(0, 0, {
                'product_id': product.id,
                'furniture_model_id': model.id,
                'quantity': quantity,
            })],
        })

    def _free_qty(self, product):
        return self.env['stock.quant']._get_available_quantity(
            product,
            self.finished,
            lot_id=self.env['stock.lot'],
            package_id=self.env['stock.quant.package'],
            owner_id=self.env['res.partner'],
            strict=True,
        )

    def test_full_reservation_reduces_free_stock_and_cancel_releases_it(self):
        self.env['stock.quant']._update_available_quantity(
            self.product, self.finished, 3.0,
        )
        order = self._create_order(quantity=2.0)
        valuation_count = self.env['stock.valuation.layer'].search_count([])

        order.action_reserve_from_finished()

        self.assertEqual(order.state, 'reserved')
        self.assertEqual(order.reservation_picking_id.state, 'assigned')
        self.assertAlmostEqual(order.reservation_picking_id.move_ids.quantity, 2.0)
        self.assertAlmostEqual(self._free_qty(self.product), 1.0)
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', self.finished.id),
        ], limit=1)
        self.assertAlmostEqual(quant.quantity, 3.0)
        self.assertEqual(
            self.env['stock.valuation.layer'].search_count([]),
            valuation_count,
        )
        with self.assertRaises(UserError):
            order.action_reserve_from_finished()

        order.action_cancel()
        self.assertEqual(order.state, 'cancelled')
        self.assertEqual(order.reservation_picking_id.state, 'cancel')
        self.assertAlmostEqual(self._free_qty(self.product), 3.0)

    def test_full_reservation_is_atomic_when_stock_is_short(self):
        self.env['stock.quant']._update_available_quantity(
            self.product, self.finished, 1.0,
        )
        order = self._create_order(quantity=2.0)
        picking_count = self.env['stock.picking'].search_count([
            ('origin', '=', order.name),
        ])

        with self.assertRaises(UserError):
            order.action_reserve_from_finished()

        self.assertEqual(order.state, 'pending')
        self.assertFalse(order.reservation_picking_id)
        self.assertEqual(
            self.env['stock.picking'].search_count([('origin', '=', order.name)]),
            picking_count,
        )
        self.assertAlmostEqual(self._free_qty(self.product), 1.0)

    def test_shortage_decision_reserves_available_and_produces_only_shortage(self):
        self.env['stock.quant']._update_available_quantity(
            self.product, self.finished, 1.0,
        )
        order = self._create_order(quantity=3.0)

        order.action_produce_shortage()

        self.assertEqual(order.state, 'production')
        self.assertEqual(order.reservation_picking_id.state, 'assigned')
        self.assertAlmostEqual(self._free_qty(self.product), 0.0)
        production = order.production_ids.ensure_one()
        self.assertEqual(production.future_order_id, order)
        self.assertEqual(production.future_order_line_id, order.line_ids)
        self.assertEqual(production.buyer_partner_id, self.buyer)
        self.assertEqual(production.beneficiary_partner_id, self.consumer)
        self.assertEqual(production.date_planned_finish.date(), order.delivery_date)
        line = production.production_line_ids.ensure_one()
        self.assertEqual(line.product_id, self.product)
        self.assertAlmostEqual(line.product_qty, 2.0)
        self.assertEqual(line.bom_id, self.product_bom)

    def test_kit_reserves_components_and_never_creates_kit_parent_line(self):
        chair, _chair_bom = self._create_component(
            'Future Delivery Kit Chair', self.model,
        )
        table, _table_bom = self._create_component(
            'Future Delivery Kit Table', self.model,
        )
        kit = self.env['product.product'].create({
            'name': 'Future Delivery Complete Kit',
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': self.model.id,
        })
        self.env['mrp.bom'].create({
            'product_tmpl_id': kit.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': kit.id,
            'furniture_model_id': self.model.id,
            'bom_line_ids': [
                (0, 0, {
                    'product_id': chair.id,
                    'product_qty': 1.0,
                    'product_uom_id': chair.uom_id.id,
                }),
                (0, 0, {
                    'product_id': table.id,
                    'product_qty': 2.0,
                    'product_uom_id': table.uom_id.id,
                }),
            ],
        })
        self.env['stock.quant']._update_available_quantity(
            chair, self.finished, 1.0,
        )
        order = self._create_order(product=kit, quantity=2.0)

        order.action_produce_shortage()

        move_products = order.reservation_picking_id.move_ids.mapped('product_id')
        self.assertEqual(set(move_products.ids), {chair.id, table.id})
        self.assertNotIn(kit, move_products)
        production_lines = order.production_ids.production_line_ids
        self.assertEqual(set(production_lines.mapped('product_id').ids), {
            chair.id, table.id,
        })
        quantities = {
            line.product_id: line.product_qty for line in production_lines
        }
        self.assertAlmostEqual(quantities[chair], 1.0)
        self.assertAlmostEqual(quantities[table], 4.0)
        self.assertFalse(production_lines.mapped('kit_bom_id'))
        self.assertFalse(any(production_lines.mapped('kit_instance_number')))

    def test_kit_model_mismatch_is_rejected_server_side(self):
        kit = self.env['product.product'].create({
            'name': 'Future Delivery Mismatched Kit',
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': self.model.id,
        })
        self.env['mrp.bom'].create({
            'product_tmpl_id': kit.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': kit.id,
            'furniture_model_id': self.model.id,
            'bom_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 1.0,
                'product_uom_id': self.product.uom_id.id,
            })],
        })
        with self.assertRaises(ValidationError):
            self._create_order(product=kit, model=self.other_model)

    def test_alert_badge_only_counts_undecided_orders_due_within_15_days(self):
        self.env['stock.quant']._update_available_quantity(
            self.product, self.finished, 2.0,
        )
        due = self._create_order(days=15)
        self._create_order(days=16)

        before = self.env['furniture.mrp.future.order'].with_user(
            self.manager
        ).get_alert_data()
        self.assertTrue(before['can_manage'])
        self.assertEqual(before['pending_total'], 2)
        self.assertEqual(before['due_soon_count'], 1)
        self.assertEqual([item['id'] for item in before['orders']], [due.id])

        due.action_reserve_from_finished()
        after = self.env['furniture.mrp.future.order'].with_user(
            self.manager
        ).get_alert_data()
        self.assertEqual(after['pending_total'], 1)
        self.assertEqual(after['due_soon_count'], 0)

    def test_non_manager_cannot_decide_or_see_alert_data(self):
        outsider = new_test_user(
            self.env,
            login='future_delivery_order_outsider',
            groups='base.group_user',
            company_id=self.company.id,
        )
        order = self._create_order()

        alert_data = self.env['furniture.mrp.future.order'].with_user(
            outsider
        ).get_alert_data()
        self.assertFalse(alert_data['can_manage'])
        with self.assertRaises(AccessError):
            order.with_user(outsider).action_produce_shortage()

    def test_availability_preview_does_not_create_model_sku(self):
        source = self.env['product.product'].create({
            'name': 'Future Delivery Preview Source',
            'type': 'consu',
            'is_storable': True,
        })
        for model in (self.model, self.other_model):
            self.env['mrp.bom'].create({
                'product_tmpl_id': source.product_tmpl_id.id,
                'product_qty': 1.0,
                'type': 'normal',
                'furniture_product_id': source.id,
                'furniture_model_id': model.id,
                'use_priming': True,
            })
        source.product_tmpl_id.with_context(
            furniture_skip_bom_classification_sync=True,
        ).write({
            'furniture_model_id': False,
            'furniture_family_id': False,
        })
        source.invalidate_recordset(['furniture_model_id'])
        product_count = self.env['product.product'].with_context(
            active_test=False
        ).search_count([])

        order = self._create_order(product=source, model=self.model)
        order.line_ids.invalidate_recordset(['available_qty', 'shortage_qty'])
        self.assertAlmostEqual(order.line_ids.available_qty, 0.0)
        self.assertAlmostEqual(order.line_ids.shortage_qty, 1.0)
        self.assertEqual(
            self.env['product.product'].with_context(active_test=False).search_count([]),
            product_count,
        )
