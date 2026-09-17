from datetime import date, timedelta
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import file_open
from .test_delivery_requests import TestDeliveryRequests
from ..models.delivery_queue import next_delivery_week


@tagged('post_install', '-at_install')
class TestDeliveryQueue(TestDeliveryRequests):
    def test_buyer_badge_does_not_tint_the_whole_column(self):
        with file_open(
            'furniture_delivery_requests/static/src/scss/delivery_list.scss',
            mode='r',
        ) as stylesheet_file:
            stylesheet = stylesheet_file.read()
        buyer_cell = stylesheet.split(
            'tbody tr.o_data_row td[name="buyer_partner_id"] {', 1
        )[1].split('}', 1)[0]
        self.assertIn('background: inherit !important;', buyer_cell)
        self.assertNotIn('box-shadow: none', buyer_cell)
        self.assertIn('> .o_field_widget {', stylesheet)
        self.assertIn('background: #eaf0f6 !important;', stylesheet)

    def test_next_week_boundaries(self):
        for today in [date(2026, 9, 14), date(2026, 9, 18)]:
            self.assertEqual(next_delivery_week(today), (date(2026, 9, 19), date(2026, 9, 25)))
        self.assertEqual(next_delivery_week(date(2026, 9, 19)), (date(2026, 9, 26), date(2026, 10, 2)))
        start, end = next_delivery_week(fields.Date.context_today(self.env['furniture.mrp.future.order']))
        order = self._create_order()
        for day, highlighted in [(start - timedelta(days=1), False), (start, True), (end, True), (end + timedelta(days=1), False)]:
            order.delivery_date = day
            self.assertEqual(bool(order.next_week_label), highlighted)
        order.delivery_date = start
        order.action_cancel()
        self.assertFalse(order.next_week_label)

    def test_batch_reserve_atomic_and_delete_cancelled(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.finished, 3)
        orders = self._create_order(quantity=2) | self._create_order(quantity=2)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            orders.action_batch_reserve()
        orders.invalidate_recordset()
        self.assertEqual(orders.mapped('state'), ['pending', 'pending'])
        self.assertEqual(self._free_qty(self.product), 3)
        self.env['stock.quant']._update_available_quantity(self.product, self.finished, 1)
        orders.action_batch_reserve()
        self.assertEqual(orders.mapped('state'), ['reserved', 'reserved'])
        with self.assertRaises(UserError):
            orders.action_delete_cancelled()
        orders.action_cancel()
        self.assertEqual(self._free_qty(self.product), 4)
        orders.action_delete_cancelled()
        self.assertFalse(orders.exists())

    def test_batch_production_and_duplicate_guard(self):
        orders = self._create_order(quantity=2) | self._create_order(quantity=3)
        orders.action_batch_produce()
        self.assertEqual(orders.mapped('state'), ['production', 'production'])
        primary = orders.line_ids.production_order_id
        self.assertEqual(len(primary), 2)
        self.assertEqual(sum(primary.production_line_ids.mapped('product_qty')), 5)
        all_productions = orders.production_ids
        with self.assertRaises(UserError):
            orders.action_batch_produce()
        self.assertEqual(orders.production_ids, all_productions)
