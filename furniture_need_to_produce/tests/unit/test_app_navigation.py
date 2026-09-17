"""No-database checks for the standalone shortages app definition."""
import ast
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET


ADDON = Path(__file__).resolve().parents[2]


class TestShortagesAppNavigation(unittest.TestCase):
    def setUp(self):
        self.manifest = ast.literal_eval((ADDON / '__manifest__.py').read_text())
        self.views = ET.parse(ADDON / 'views/production_plan_views.xml')

    def test_existing_addon_is_an_application_with_icon(self):
        self.assertTrue(self.manifest['application'])
        self.assertEqual(self.manifest['name'], 'النواقص')
        self.assertEqual(self.manifest['depends'], ['furniture_stage_replenishment'])
        self.assertEqual(self.manifest['icon'], '/furniture_need_to_produce/static/description/icon.svg')

    def test_existing_menu_is_explicitly_promoted_without_new_action(self):
        menu = self.views.find("./record[@id='menu_need_to_produce']")
        self.assertIsNotNone(menu)
        self.assertEqual(menu.attrib['model'], 'ir.ui.menu')
        self.assertEqual(menu.find("field[@name='parent_id']").attrib['eval'], 'False')
        self.assertEqual(menu.find("field[@name='action']").attrib['ref'], 'action_need_to_produce')
        self.assertEqual(menu.find("field[@name='name']").text, 'النواقص')
        self.assertIn('furniture_mrp.group_furniture_mrp_manager', menu.find("field[@name='groups_id']").attrib['eval'])
        self.assertEqual(menu.find("field[@name='web_icon']").text,
                         'furniture_need_to_produce,static/description/icon.svg')

    def test_action_keeps_workflow_and_permissions(self):
        action = self.views.find("./record[@id='action_need_to_produce']")
        self.assertEqual(action.find("field[@name='name']").text, 'النواقص')
        self.assertEqual(action.find("field[@name='res_model']").text, 'furniture.need.to.produce')
        self.assertEqual(action.find("field[@name='view_mode']").text, 'kanban,list,form')
        self.assertIn('furniture_mrp.group_furniture_mrp_manager', action.find("field[@name='groups_id']").attrib['eval'])
        self.assertEqual(ast.literal_eval(action.find("field[@name='context']").text), {'search_default_draft': 1})

    def test_icon_is_vector_without_an_opaque_background_or_external_resource(self):
        svg = ET.parse(ADDON / 'static/description/icon.svg')
        self.assertEqual(svg.getroot().attrib['viewBox'], '0 0 128 128')
        for element in svg.iter():
            self.assertNotIn(element.tag.rsplit('}', 1)[-1], ('script', 'image', 'foreignObject', 'rect'))

    def test_user_facing_templates_use_the_new_name(self):
        for relative in ('production_pipeline.xml', 'need_produce_list.xml', 'minmax_workspace.xml'):
            source = (ADDON / 'static/src/xml' / relative).read_text()
            ET.fromstring(source)
            self.assertIn('النواقص', source)
            self.assertNotIn('بحاجة', source)

    def test_piece_heading_does_not_repeat_the_workspace_model(self):
        kanban = self.views.find("./record[@id='view_need_to_produce_pipeline']/field[@name='arch']/kanban")
        heading = kanban.find(".//div[@class='o_ntp_piece_identity']/div/h2")
        self.assertIsNotNone(heading.find("field[@name='product_id']"))
        self.assertIsNone(heading.find(".//field[@name='furniture_model_id']"))
        self.assertIsNone(kanban.find(".//span[@class='o_ntp_piece_model']"))
        # Keep model data for filtering and the shared workspace heading.
        self.assertIsNotNone(kanban.find("field[@name='furniture_model_id']"))
        workspace = ET.parse(ADDON / 'static/src/xml/production_pipeline.xml')
        identity = workspace.find(".//div[@class='o_ntp_workspace_summary_identity']")
        self.assertIsNotNone(identity.find(".//t[@t-esc='pipelineFilters.modelName']"))


if __name__ == '__main__':
    unittest.main()
