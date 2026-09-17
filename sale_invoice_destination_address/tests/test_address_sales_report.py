# -*- coding: utf-8 -*-

from datetime import date

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import ValidationError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestAddressSalesReport(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.port_said = cls.env['sale.destination.address'].create({
            'name': 'Port Said',
            'company_id': cls.env.company.id,
        })
        cls.alexandria = cls.env['sale.destination.address'].create({
            'name': 'Alexandria',
            'company_id': cls.env.company.id,
        })
        cls.mansoura = cls.env['sale.destination.address'].create({
            'name': 'Mansoura',
            'company_id': cls.env.company.id,
        })

    @classmethod
    def _create_customer_document(
        cls, move_type, invoice_date, amount, address=None, posted=True
    ):
        move = cls.env['account.move'].create({
            'move_type': move_type,
            'partner_id': cls.partner_a.id,
            'invoice_date': invoice_date,
            'destination_address_id': address.id if address else False,
            'invoice_line_ids': [Command.create({
                'name': 'Address sales report line',
                'product_id': cls.product_a.id,
                'quantity': 1.0,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
            })],
        })
        if posted:
            move.action_post()
        return move

    def test_period_validation_and_report_action(self):
        with self.assertRaises(ValidationError):
            self.env['sale.destination.address.report.wizard'].create({
                'date_from': date(2026, 7, 31),
                'date_to': date(2026, 7, 1),
            })

        wizard = self.env['sale.destination.address.report.wizard'].create({
            'date_from': date(2026, 7, 1),
            'date_to': date(2026, 7, 31),
        })
        action = wizard.action_print()
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(
            action['report_name'],
            'sale_invoice_destination_address.address_sales',
        )

    def test_invoice_sales_grouping_percentages_and_zero_addresses(self):
        self._create_customer_document('out_invoice', date(2026, 7, 1), 1000, self.port_said)
        self._create_customer_document('out_invoice', date(2026, 7, 31), 500, self.port_said)
        self._create_customer_document('out_refund', date(2026, 7, 15), 200, self.port_said)
        self._create_customer_document('out_invoice', date(2026, 7, 10), 700, self.alexandria)
        self._create_customer_document('out_refund', date(2026, 7, 20), 100, self.alexandria)
        self._create_customer_document('out_invoice', date(2026, 7, 12), 100, address=None)

        self._create_customer_document('out_invoice', date(2026, 6, 30), 999, self.port_said)
        self._create_customer_document(
            'out_invoice', date(2026, 7, 18), 888, self.port_said, posted=False
        )

        wizard = self.env['sale.destination.address.report.wizard'].create({
            'date_from': date(2026, 7, 1),
            'date_to': date(2026, 7, 31),
        })
        data = wizard._get_address_sales_data()
        rows = {row['name']: row for row in data['rows']}

        self.assertAlmostEqual(rows['Port Said']['untaxed_sales'], 1500.0)
        self.assertEqual(rows['Port Said']['invoice_count'], 2)
        self.assertNotIn('refund_count', rows['Port Said'])
        self.assertAlmostEqual(rows['Port Said']['percentage'], 1500.0 / 2300.0 * 100.0)

        self.assertAlmostEqual(rows['Alexandria']['untaxed_sales'], 700.0)
        self.assertAlmostEqual(rows['Alexandria']['percentage'], 700.0 / 2300.0 * 100.0)
        self.assertAlmostEqual(rows['Mansoura']['untaxed_sales'], 0.0)
        self.assertAlmostEqual(rows['No Address']['untaxed_sales'], 100.0)
        self.assertAlmostEqual(rows['No Address']['percentage'], 100.0 / 2300.0 * 100.0)

        self.assertAlmostEqual(data['total_untaxed_sales'], 2300.0)
        self.assertEqual(data['total_invoice_count'], 4)
        self.assertNotIn('total_refund_count', data)
        self.assertEqual(data['total_percentage'], 100.0)

    def test_refunds_are_excluded_and_zero_total_is_safe(self):
        self._create_customer_document('out_refund', date(2026, 8, 6), 100, self.port_said)
        wizard = self.env['sale.destination.address.report.wizard'].create({
            'date_from': date(2026, 8, 1),
            'date_to': date(2026, 8, 31),
        })
        data = wizard._get_address_sales_data()
        self.assertAlmostEqual(data['total_untaxed_sales'], 0.0)
        self.assertTrue(all(row['percentage'] == 0.0 for row in data['rows']))
