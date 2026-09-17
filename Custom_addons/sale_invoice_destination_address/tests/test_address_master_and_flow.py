# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAddressMasterAndFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Address Test Customer'})

    def test_address_is_trimmed_and_duplicate_is_rejected(self):
        address = self.env['sale.destination.address'].create({
            'name': '  Port Said  ',
            'company_id': self.env.company.id,
        })
        self.assertEqual(address.name, 'Port Said')

        with self.assertRaises(ValidationError):
            self.env['sale.destination.address'].create({
                'name': 'port said',
                'company_id': self.env.company.id,
            })

    def test_quotation_keeps_snapshot_and_prepares_invoice_address(self):
        address = self.env['sale.destination.address'].create({
            'name': 'Alexandria',
            'company_id': self.env.company.id,
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'destination_address_id': address.id,
        })

        self.assertEqual(order.destination_address, 'Alexandria')
        address.name = 'Alexandria City'
        self.assertEqual(order.destination_address, 'Alexandria')

        invoice_vals = order._prepare_invoice()
        self.assertEqual(invoice_vals['destination_address_id'], address.id)
        self.assertEqual(invoice_vals['destination_address'], 'Alexandria')

    def test_clearing_address_clears_snapshot(self):
        address = self.env['sale.destination.address'].create({
            'name': 'Mansoura',
            'company_id': self.env.company.id,
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'destination_address_id': address.id,
        })
        order.destination_address_id = False
        self.assertFalse(order.destination_address)
