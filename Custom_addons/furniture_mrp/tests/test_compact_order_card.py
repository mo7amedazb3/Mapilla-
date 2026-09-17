from pathlib import Path
import unittest
from lxml import etree


class TestCompactOrderCard(unittest.TestCase):
    def test_supervisor_has_orders_without_extra_metrics_panel(self):
        source = Path(__file__).parents[1] / 'static/src/xml/mrp_stage_dashboard.xml'
        root = etree.parse(str(source))
        section = root.xpath("//t[@t-name='furniture_mrp.StageDashboardOrderSupervisor']")[0]
        self.assertFalse(section.xpath(".//*[contains(@class, 'o_furniture_order_supervisor_metrics')]"))
        self.assertTrue(section.xpath(".//t[@t-call='furniture_mrp.StageDashboardOrderCard']"))

    def test_native_disclosure_preserves_details_and_timer(self):
        source = Path(__file__).parents[1] / 'static/src/xml/mrp_stage_dashboard.xml'
        root = etree.parse(str(source))
        card = root.xpath("//t[@t-name='furniture_mrp.StageDashboardOrderCard']/details")[0]
        self.assertNotIn('open', card.attrib)
        summary = card[0]
        self.assertEqual(summary.tag, 'summary')
        text = etree.tostring(summary, encoding='unicode')
        for field in ('order.model_name', 'order.buyer_summary', 'order.beneficiary_summary', 'stageTimerValue(order.timer)'):
            self.assertIn(field, text)
        self.assertFalse(summary.xpath('.//button | .//input'))
        self.assertTrue(card.xpath(".//button[contains(@t-on-click, 'startOrderAllProducts')]"))
        self.assertTrue(card.xpath(".//button[contains(@t-on-click, 'pauseOrderTimer')]"))
        self.assertTrue(card.xpath(".//*[@class='o_furniture_order_supervisor_order__products']"))


if __name__ == '__main__':
    unittest.main()
