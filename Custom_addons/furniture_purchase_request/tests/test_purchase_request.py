# -*- coding: utf-8 -*-

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestFurniturePurchaseRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env.ref('stock.warehouse0')
        cls.storekeeper = new_test_user(
            cls.env,
            login='furniture_purchase_request_storekeeper',
            groups='furniture_mrp.group_furniture_mrp_storekeeper',
            company_id=cls.company.id,
        )
        cls.buyer = new_test_user(
            cls.env,
            login='furniture_purchase_request_buyer',
            groups='purchase.group_purchase_user',
            company_id=cls.company.id,
        )
        cls.outsider = new_test_user(
            cls.env,
            login='furniture_purchase_request_outsider',
            groups='base.group_user',
            company_id=cls.company.id,
        )
        cls.vendor_a = cls.env['res.partner'].create({
            'name': 'Request Vendor A',
            'supplier_rank': 1,
        })
        cls.vendor_b = cls.env['res.partner'].create({
            'name': 'Request Vendor B',
            'supplier_rank': 1,
        })
        cls.product_a = cls.env['product.product'].create({
            'name': 'Request Material A',
            'is_storable': True,
            'purchase_ok': True,
        })
        cls.product_b = cls.env['product.product'].create({
            'name': 'Request Material B',
            'is_storable': True,
            'purchase_ok': True,
        })

    def _request(self, product_vendor_pairs=None):
        pairs = product_vendor_pairs or [(self.product_a, self.vendor_a)]
        request = self.env['furniture.purchase.request'].with_user(
            self.storekeeper
        ).create({
            'line_ids': [
                (0, 0, {
                    'product_id': product.id,
                    'quantity': 2.0,
                    'product_uom_id': product.uom_po_id.id,
                })
                for product, _vendor in pairs
            ],
        })
        return request

    def test_storekeeper_submits_stock_request_but_cannot_create_rfq(self):
        request = self._request()
        self.assertEqual(request.warehouse_id, self.warehouse)
        self.assertEqual(request.stock_location_id, self.warehouse.lot_stock_id)
        request.with_user(self.storekeeper).action_submit()
        self.assertEqual(request.state, 'requested')
        with self.assertRaises(AccessError):
            request.with_user(self.storekeeper).action_create_rfqs()

    def test_purchase_groups_lines_by_supplier_and_uses_stock_receipt(self):
        request = self._request([
            (self.product_a, self.vendor_a),
            (self.product_b, self.vendor_b),
        ])
        request.with_user(self.storekeeper).action_submit()
        request = request.with_user(self.buyer)
        request.line_ids[0].supplier_id = self.vendor_a
        request.line_ids[1].supplier_id = self.vendor_b
        action = request.action_create_rfqs()
        request.invalidate_recordset()
        self.assertEqual(request.state, 'rfq_created')
        self.assertEqual(len(request.purchase_order_ids), 2)
        self.assertEqual(set(request.purchase_order_ids.mapped('partner_id').ids), {
            self.vendor_a.id, self.vendor_b.id,
        })
        self.assertTrue(all(
            order.picking_type_id == self.warehouse.in_type_id
            for order in request.purchase_order_ids
        ))
        self.assertTrue(all(order.origin == request.name for order in request.purchase_order_ids))
        self.assertEqual(action['res_model'], 'purchase.order')
        with self.assertRaises(UserError):
            request.action_create_rfqs()

    def test_lines_for_same_supplier_share_one_rfq(self):
        request = self._request([
            (self.product_a, self.vendor_a),
            (self.product_b, self.vendor_a),
        ])
        request.with_user(self.storekeeper).action_submit()
        request = request.with_user(self.buyer)
        request.line_ids.supplier_id = self.vendor_a
        request.action_create_rfqs()
        self.assertEqual(len(request.purchase_order_ids), 1)
        self.assertEqual(len(request.purchase_order_ids.order_line), 2)

    def test_supplier_is_required_before_rfq(self):
        request = self._request()
        request.with_user(self.storekeeper).action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.buyer).action_create_rfqs()

    def test_outsider_has_no_request_access(self):
        model = self.env['furniture.purchase.request'].with_user(self.outsider)
        with self.assertRaises(AccessError):
            model.create({'note': 'not allowed'})

    def test_storekeeper_cannot_edit_after_submit(self):
        request = self._request()
        request.with_user(self.storekeeper).action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.storekeeper).write({'note': 'changed'})
        with self.assertRaises(AccessError):
            request.with_user(self.storekeeper).with_context(
                furniture_purchase_request_allow_rfq_link=True,
            ).write({'purchase_order_ids': [(6, 0, [])]})

    def test_rpc_context_cannot_bypass_workflow(self):
        request = self._request()
        with self.assertRaises(AccessError):
            request.with_user(self.storekeeper).with_context(
                furniture_purchase_request_allow_transition=True,
            ).write({'state': 'requested'})
        self.assertEqual(request.state, 'draft')

        request.with_user(self.storekeeper).action_submit()
        with self.assertRaises(AccessError):
            request.with_user(self.buyer).with_context(
                furniture_purchase_request_allow_transition=True,
                furniture_purchase_request_allow_rfq_link=True,
            ).write({
                'purchase_order_ids': [(6, 0, [])],
                'state': 'rfq_created',
            })
        self.assertEqual(request.state, 'requested')

    def test_purchase_can_choose_supplier_but_cannot_change_request(self):
        request = self._request()
        request.with_user(self.storekeeper).action_submit()
        request = request.with_user(self.buyer)
        request.line_ids.write({'supplier_id': self.vendor_a.id})
        self.assertEqual(request.line_ids.supplier_id, self.vendor_a)
        with self.assertRaises(AccessError):
            request.line_ids.write({'quantity': 99.0})
        with self.assertRaises(AccessError):
            request.line_ids.create({
                'request_id': request.id,
                'product_id': self.product_b.id,
                'quantity': 1.0,
                'product_uom_id': self.product_b.uom_id.id,
            })
        with self.assertRaises(AccessError):
            request.line_ids.unlink()

    def test_purchase_user_cannot_create_request(self):
        with self.assertRaises(AccessError):
            self.env['furniture.purchase.request'].with_user(self.buyer).create({
                'note': 'buyer draft must not be created',
            })

    def test_request_name_is_immutable(self):
        request = self._request()
        with self.assertRaises(AccessError):
            request.with_user(self.storekeeper).write({'name': 'MANUAL/1'})

    def test_linked_rfq_cannot_be_deleted(self):
        request = self._request()
        request.with_user(self.storekeeper).action_submit()
        request = request.with_user(self.buyer)
        request.line_ids.supplier_id = self.vendor_a
        request.action_create_rfqs()
        with self.assertRaises(UserError):
            request.purchase_order_ids.unlink()

    def test_rfq_quantity_is_converted_to_final_furniture_uom(self):
        unit = self.env.ref('uom.product_uom_unit')
        dozen = self.env.ref('uom.product_uom_dozen')
        furniture_unit = self.env.ref('furniture_mrp.furniture_uom_unit')
        product = self.env['product.product'].create({
            'name': 'Request Material By Dozen',
            'is_storable': True,
            'purchase_ok': True,
            'uom_id': unit.id,
            'uom_po_id': dozen.id,
        })
        request = self.env['furniture.purchase.request'].with_user(
            self.storekeeper
        ).create({
            'line_ids': [(0, 0, {
                'product_id': product.id,
                'quantity': 2.0,
                'product_uom_id': dozen.id,
            })],
        })
        request.with_user(self.storekeeper).action_submit()
        request = request.with_user(self.buyer)
        request.line_ids.supplier_id = self.vendor_a
        request.action_create_rfqs()
        order_line = request.purchase_order_ids.order_line
        self.assertEqual(order_line.product_uom, furniture_unit)
        quantity_in_request_uom = order_line.product_uom._compute_quantity(
            order_line.product_qty, dozen, round=False,
        )
        self.assertAlmostEqual(quantity_in_request_uom, 2.0)
