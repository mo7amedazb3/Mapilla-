# -*- coding: utf-8 -*-

from datetime import datetime

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestMrpShortagePurchaseRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.buyer = new_test_user(
            cls.env,
            login='furniture_shortage_request_buyer',
            groups='purchase.group_purchase_user',
            company_id=cls.env.company.id,
        )
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Production Shortage Vendor',
            'supplier_rank': 1,
        })
        cls.material = cls.env['product.product'].create({
            'name': 'Production Shortage Raw Material',
            'is_storable': True,
            'purchase_ok': True,
        })

    def _create_production(self, required_qty):
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'product_qty': 1.0,
            'state': 'confirmed',
            'date_planned_start': datetime(2026, 8, 10, 8, 0),
            'use_priming': True,
            'use_painting': False,
            'use_carpentry': False,
            'use_bases': False,
            'use_finishing': False,
            'use_tailoring': False,
            'use_upholstery': False,
            'use_packaging': False,
            'location_src_id': self.stock_location.id,
        })
        self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'product_id': self.material.id,
            'product_uom_id': self.material.uom_id.id,
            'qty_needed': required_qty,
            'stage': 'priming',
        })
        return production

    def _managed_request(self, production, states=('requested',)):
        return self.env['furniture.purchase.request'].sudo().search([
            ('production_id', '=', production.id),
            ('production_shortage_managed', '=', True),
            ('state', 'in', states),
        ])

    def test_material_check_creates_one_requested_request_not_direct_po(self):
        production = self._create_production(3.0)

        production.action_check_materials()
        request = self._managed_request(production).ensure_one()
        line = request.line_ids.ensure_one()
        self.assertEqual(request.state, 'requested')
        self.assertEqual(line.product_id, self.material)
        self.assertAlmostEqual(line.quantity, 3.0)
        self.assertTrue(line.material_shortage_managed)
        self.assertEqual(line.material_reservation_id.production_id, production)
        self.assertFalse(self.env['purchase.order'].sudo().search([
            ('furniture_material_check_production_id', '=', production.id),
        ]))

        request_id = request.id
        line_id = line.id
        production.action_check_materials()
        request = self._managed_request(production).ensure_one()
        self.assertEqual(request.id, request_id)
        self.assertEqual(request.line_ids.ids, [line_id])
        self.assertAlmostEqual(request.line_ids.quantity, 3.0)

        production.material_line_ids.ensure_one().qty_needed = 5.0
        production.action_check_materials()
        request.invalidate_recordset(['line_ids'])
        self.assertEqual(request.line_ids.ids, [line_id])
        self.assertAlmostEqual(request.line_ids.quantity, 5.0)

    def test_purchase_converts_managed_request_to_linked_rfq(self):
        production = self._create_production(4.0)
        production.action_check_materials()
        request = self._managed_request(production).with_user(self.buyer)
        request.line_ids.supplier_id = self.vendor

        request.action_create_rfqs()
        request.invalidate_recordset()
        order = request.purchase_order_ids.ensure_one()
        order_line = order.order_line.ensure_one()
        self.assertEqual(request.state, 'rfq_created')
        self.assertEqual(order.furniture_material_check_production_id, production)
        self.assertTrue(order.furniture_material_check_managed)
        self.assertEqual(
            order_line.furniture_material_reservation_id,
            request.line_ids.material_reservation_id,
        )
        self.assertTrue(order_line.furniture_shortage_managed)
        self.assertIn(order, production.purchase_order_ids)

        production.action_check_materials()
        self.assertFalse(self._managed_request(production))

    def test_request_is_cancelled_when_shortage_disappears(self):
        production = self._create_production(2.0)
        production.action_check_materials()
        request = self._managed_request(production).ensure_one()

        production.action_release_material_reservations()
        request.invalidate_recordset()
        self.assertEqual(request.state, 'cancelled')
        self.assertFalse(request.line_ids)
