"""Static contract for the standalone Min / Max application."""
import ast
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET


ADDON = Path(__file__).resolve().parents[1]


class TestMinMaxAppNavigation(unittest.TestCase):
    def setUp(self):
        self.manifest = ast.literal_eval(
            (ADDON / '__manifest__.py').read_text(encoding='utf-8')
        )
        self.views = ET.parse(ADDON / 'views/stage_replenishment_views.xml')

    def test_addon_is_a_standalone_application(self):
        self.assertTrue(self.manifest['application'])
        self.assertEqual(self.manifest['name'], 'Min / Max')
        self.assertEqual(
            self.manifest['icon'],
            '/furniture_stage_replenishment/static/description/icon.svg',
        )

    def test_existing_root_menu_is_promoted_without_replacing_its_action(self):
        menu = self.views.find(
            "./record[@id='menu_furniture_stage_replenishment_root']"
        )
        self.assertIsNotNone(menu)
        self.assertEqual(menu.attrib['model'], 'ir.ui.menu')
        self.assertEqual(
            menu.find("field[@name='parent_id']").attrib['eval'],
            'False',
        )
        self.assertEqual(menu.find("field[@name='name']").text, 'Min / Max')
        self.assertEqual(
            menu.find("field[@name='action']").attrib['ref'],
            'action_furniture_stage_replenishment_dashboard',
        )
        self.assertEqual(
            menu.find("field[@name='web_icon']").text,
            'furniture_stage_replenishment,static/description/icon.svg',
        )

    def test_icon_is_safe_transparent_vector(self):
        svg = ET.parse(ADDON / 'static/description/icon.svg')
        self.assertEqual(svg.getroot().attrib['viewBox'], '0 0 128 128')
        for element in svg.iter():
            self.assertNotIn(
                element.tag.rsplit('}', 1)[-1],
                ('script', 'image', 'foreignObject', 'rect'),
            )


if __name__ == '__main__':
    unittest.main()
