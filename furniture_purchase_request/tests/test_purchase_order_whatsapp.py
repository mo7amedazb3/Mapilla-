# -*- coding: utf-8 -*-

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPurchaseOrderWhatsApp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({
            'name': 'WhatsApp Purchase Vendor',
            'supplier_rank': 1,
            'mobile': '01012345678',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'WhatsApp Requested Wood',
            'purchase_ok': True,
        })
        cls.order = cls.env['purchase.order'].create({
            'partner_id': cls.vendor.id,
            'order_line': [(0, 0, {
                'product_id': cls.product.id,
                'product_qty': 2.5,
                'product_uom': cls.product.uom_po_id.id,
            })],
        })

    def test_button_builds_supplier_message_and_opens_internal_module(self):
        action = self.order.action_send_via_whatsapp()

        message = self.env['whatsapp.msg'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', self.order.id),
        ], order='id desc', limit=1)
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'whatsapp.msg')
        self.assertEqual(action['res_id'], message.id)
        self.assertEqual(action['target'], 'new')
        self.assertEqual(message.partner_id, self.vendor)
        self.assertEqual(message.mobile, self.vendor.mobile)
        self.assertIn(self.order.name, message.message)
        self.assertIn(self.product.display_name, message.message)
        self.assertIn('2.5', message.message)

    def test_button_requires_supplier_phone(self):
        self.vendor.mobile = False
        self.vendor.phone = False
        with self.assertRaises(UserError):
            self.order.action_send_via_whatsapp()

    def test_button_uses_connected_api_when_instance_is_ready(self):
        self.env['whatsapp.instance'].create({
            'name': 'Purchase Test WhatsApp',
            'instance_id': 'purchase-test-ready',
            'status': 'ready',
        })
        whatsapp_model = self.env.registry['whatsapp.msg']
        with patch.object(
            whatsapp_model,
            'action_send_via_api',
            autospec=True,
            return_value=True,
        ) as send_mock:
            action = self.order.action_send_via_whatsapp()

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(send_mock.call_count, 1)
