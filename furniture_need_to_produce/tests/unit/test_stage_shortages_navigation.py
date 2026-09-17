import ast
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


class TestStageShortagesNavigation(unittest.TestCase):
    def test_actions_share_only_the_paired_buffer_and_reuse_authorized_planner(self):
        tree = ET.parse(ROOT / 'views/stage_shortages_views.xml')
        actions = tree.findall("./record[@model='ir.actions.act_window']")
        self.assertEqual(len(actions), 5)
        lanes = set()
        for action in actions:
            values = {field.attrib['name']: field for field in action.findall('field')}
            context = ast.literal_eval(values['context'].text)
            lane = context['ntp_stage_shortages']
            lanes.add(lane)
            target = (('target_lane', 'in', ['bases', 'preparation']) if lane in ('bases', 'preparation')
                      else ('target_lane', '=', lane))
            self.assertEqual(ast.literal_eval(values['domain'].text), [
                ('buffer_rule_id', '!=', False), ('final_rule_id', '=', False), target])
            self.assertEqual(values['res_model'].text, 'furniture.need.to.produce')
            self.assertEqual(values['view_id'].attrib['ref'], 'view_need_to_produce_pipeline')
            self.assertIn('group_furniture_mrp_manager', values['groups_id'].attrib['eval'])
        self.assertEqual(lanes, {'frame', 'bases', 'preparation', 'tailoring', 'painting'})
        final = ET.parse(ROOT / 'views/production_plan_views.xml').find("./record[@id='action_need_to_produce']")
        self.assertEqual(ast.literal_eval(final.find("field[@name='domain']").text), [
            ('final_rule_id', '!=', False), ('buffer_rule_id', '=', False)])
    def test_both_workspaces_are_leaf_menus_inside_the_existing_app(self):
        tree = ET.parse(ROOT / 'views/stage_shortages_views.xml')
        menus = tree.findall("./record[@model='ir.ui.menu']")
        self.assertEqual(len(menus), 2)
        expected = [
            ('menu_need_to_produce_final', 'نواقص المنتج التام', 'action_need_to_produce', '10'),
            ('menu_stage_shortages', 'نواقص المراحل', 'action_stage_shortages_frame', '20'),
        ]
        for xmlid, name, action, sequence in expected:
            menu = tree.find(f"./record[@id='{xmlid}']")
            self.assertEqual(menu.find("field[@name='parent_id']").attrib['ref'], 'menu_need_to_produce')
            self.assertEqual(menu.find("field[@name='name']").text, name)
            self.assertEqual(menu.find("field[@name='action']").attrib['ref'], action)
            self.assertEqual(menu.find("field[@name='sequence']").text, sequence)
            self.assertIn('group_furniture_mrp_manager', menu.find("field[@name='groups_id']").attrib['eval'])
        stage = tree.find("./record[@id='menu_stage_shortages']")
        self.assertEqual(stage.find("field[@name='web_icon']").attrib['eval'], 'False')

    def test_navigation_is_packaged_without_new_business_models_or_policies(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text())
        self.assertIn('views/stage_shortages_views.xml', manifest['data'])
        for filename in ['stage_shortages_views.xml', 'production_plan_views.xml']:
            for record in ET.parse(ROOT / 'views' / filename).findall('./record'):
                self.assertIn(record.attrib['model'], {'ir.ui.menu', 'ir.actions.act_window',
                    'ir.actions.act_window.view', 'ir.ui.view'})
        ET.parse(ROOT / 'static/description/stage_shortages.svg')


if __name__ == '__main__':
    unittest.main()
