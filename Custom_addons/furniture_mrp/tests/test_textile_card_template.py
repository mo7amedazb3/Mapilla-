"""Static guards for the shared textile card; no database is required.

Run with: python furniture_mrp/tests/test_textile_card_template.py
"""
from pathlib import Path
import unittest

from lxml import etree


class TestTextileCardTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[1] / 'static/src/xml/mrp_stage_dashboard.xml'
        cls.card = etree.parse(str(path)).xpath(
            '//t[@t-name="furniture_mrp.StageDashboardOrderCard"]'
        )[0]

    def test_textile_identity_is_model_first_with_order_reference(self):
        title = self.card.xpath('.//strong[@class="o_furniture_textile_order_model"]')[0]
        self.assertEqual(title.get('t-esc'), "order.model_name || 'بدون موديل'")
        self.assertEqual(title.getparent().get('t-if'), 'showOrderTextileDetails')
        reference = self.card.xpath('.//span[@class="o_furniture_textile_order_reference"]/b')[0]
        self.assertEqual(reference.get('t-esc'), 'order.name')

    def test_party_fields_preserve_the_existing_data_bindings(self):
        parties = self.card.xpath('.//span[@class="o_furniture_textile_order_parties"]')[0]
        self.assertEqual(parties.get('t-if'), 'showOrderTextileDetails')
        self.assertEqual(parties.xpath('./span/strong/@t-esc'), [
            "order.buyer_summary || '—'", "order.beneficiary_summary || '—'",
        ])

    def test_other_stages_keep_order_identity_and_model_badge(self):
        identity = self.card.xpath('.//span[@class="o_furniture_order_supervisor_order__identity"]')[0]
        self.assertEqual(identity.xpath('./t[@t-else]/strong/@t-esc'), ['order.name'])
        badge = self.card.xpath('.//span[@class="o_furniture_order_supervisor_order__model"]')[0]
        self.assertEqual(badge.get('t-if'), 'order.model_name && !showOrderTextileDetails')

    def test_popup_style_reference_is_in_the_three_metadata_cells(self):
        parties = self.card.xpath('.//span[@class="o_furniture_textile_order_parties"]')[0]
        self.assertEqual(len(parties.xpath('./span')), 3)
        self.assertEqual(parties.xpath('.//b/@t-esc'), ['order.name'])
        identity = self.card.xpath('.//span[@class="o_furniture_order_supervisor_order__identity"]')[0]
        self.assertFalse(identity.xpath('.//span[@class="o_furniture_textile_order_reference"]'))

    def test_quantity_uses_real_planned_quantity_and_original_uom_formatter(self):
        quantity = self.card.xpath('.//em[@class="o_furniture_textile_product_quantity"]')[0]
        self.assertEqual(quantity.get('t-if'), 'showOrderTextileDetails && isOrderWorkflowSupervisorMode')
        self.assertEqual(quantity.get('t-esc'), 'formatProductQuantity(product.planned_qty, product)')

    def test_materials_keep_all_items_sizes_and_notes(self):
        self.assertTrue(self.card.xpath('.//span[@t-foreach="product.fabric_items"]'))
        self.assertTrue(self.card.xpath('.//span[@t-foreach="product.takawe_items"]'))
        self.assertTrue(self.card.xpath('.//em[@t-if="item.pieceSize"]'))
        self.assertTrue(self.card.xpath('.//strong[@t-esc="product.notes || \'—\'"]'))

    def test_image_preview_is_outside_the_supervisor_stacking_context(self):
        document = self.card.getroottree()
        calls = document.xpath('//t[@t-call="furniture_mrp.StageDashboardOrderImage"]')
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].get('t-if'))
        self.assertFalse(calls[0].xpath('ancestor::t[@t-name="furniture_mrp.StageDashboardOrderSupervisor"]'))

    def test_operational_buttons_remain_supervisor_only(self):
        for button in self.card.xpath('.//button'):
            handler = button.get('t-on-click', '')
            if 'openOrderImage' in handler:
                continue
            condition = button.get('t-if', '')
            if 'openProductionOrder' in handler:
                self.assertEqual(condition, '!isOrderWorkflowSupervisorMode')
            elif 'reviewOrderProductQuality' in handler:
                self.assertIn('isOrderWorkflowSupervisorMode', button.getparent().get('t-if'))
            else:
                self.assertIn('isOrderWorkflowSupervisorMode', condition)


if __name__ == '__main__':
    unittest.main()
