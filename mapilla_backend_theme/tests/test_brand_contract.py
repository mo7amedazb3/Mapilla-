"""Standalone presentation contract: run with Odoo's Python, no database writes."""
import ast
from pathlib import Path
import unittest
from lxml import etree
import sass

ROOT = Path(__file__).resolve().parents[1]

class BrandContract(unittest.TestCase):
    def test_textile_cards_keep_full_width_and_details(self):
        source = (ROOT / 'static/src/scss/brand_order_cards.scss').read_text()
        css = sass.compile(string=source)
        self.assertIn('grid-template-columns: minmax(0, 1fr)', css)
        self.assertNotIn('line-clamp', css)
        self.assertNotIn('max-height', css)
        self.assertIn('var(--mapilla-heading)', css)
        template = etree.parse(str(ROOT.parent / 'furniture_mrp/static/src/xml/mrp_stage_dashboard.xml'))
        card = template.xpath('//t[@t-name="furniture_mrp.StageDashboardOrderCard"]')[0]
        self.assertEqual(len(card.xpath('.//span[@class="o_mapilla_textile_parties"]')), 1)
        self.assertTrue(card.xpath('.//*[@t-on-click="() => this.openOrderImage(order)"]'))
        for label in ('الموديل', 'الصنف', 'المشتري', 'المستهلك', 'القماش', 'التكاوي', 'الملاحظات'):
            self.assertIn(label, ''.join(card.itertext()))

    def test_sidebar_panel_stays_hidden_without_a_floating_toggle(self):
        css = sass.compile(filename=str(ROOT / 'static/src/scss/brand_drawer.scss'))
        self.assertIn('position: fixed', css)
        self.assertIn('--mk-sidebar-width: 0', css)
        self.assertIn('prefers-reduced-motion: reduce', css)
        template = (ROOT / 'static/src/xml/brand_navigation.xml').read_text()
        self.assertIn('<attribute name="inert">inert</attribute>', template)
        self.assertNotIn('o_mapilla_drawer_toggle', template)
        self.assertNotIn('aria-expanded', template)
        script = (ROOT / 'static/src/js/brand_navigation.js').read_text()
        self.assertNotIn('mapillaToggleDrawer', script)
        self.assertNotIn('o_mapilla_drawer_toggle', script)

    def test_brand_assets_follow_all_optional_modules(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text())
        self.assertIn('data/brand_assets.xml', manifest['data'])
        self.assertEqual(manifest['depends'], ['web', 'muk_web_theme'])
        records = etree.parse(str(ROOT / 'data/brand_assets.xml')).xpath('//record')
        self.assertEqual(len(records), 5)
        for record in records:
            fields = {f.get('name'): f.text for f in record}
            self.assertEqual(record.get('model'), 'ir.asset')
            self.assertGreaterEqual(int(fields['sequence']), 1000)
            self.assertEqual(fields['directive'], 'append')
            self.assertNotIn('report', fields['bundle'])
            self.assertTrue((ROOT.parent / fields['path']).is_file())

    def test_scss_compiles_and_keeps_semantic_statuses(self):
        for name in ('brand_identity', 'brand_workspaces', 'brand_dark'):
            css = sass.compile(filename=str(ROOT / 'static/src/scss' / (name + '.scss')))
            self.assertIn('@media screen', css)
            self.assertNotIn('filter: grayscale', css)
            self.assertNotIn('overflow: hidden', css)
        source = (ROOT / 'static/src/scss/brand_workspaces.scss').read_text()
        self.assertNotIn('--ntp-graph-stage:', source)
        self.assertNotIn('--ntp-graph-incoming:', source)
        self.assertNotIn('--ntp-graph-stock:', source)
        self.assertIn('--ntp-model-hue', source)
        self.assertNotIn('.o_fsr_model_group td', source)

    def test_supplied_logo_is_reused_and_fonts_are_local(self):
        navbar = (ROOT / 'static/src/xml/navbar.xml').read_text()
        self.assertIn('mapilla_wordmark_light.png', navbar)
        self.assertIn('muk_web_appsbar.AppsBar', navbar)
        self.assertIn('o_mapilla_sidebar_brand', navbar)
        source = (ROOT / 'static/src/scss/brand_identity.scss').read_text()
        self.assertNotIn('https://', source)
        self.assertIn('Tajawal-Regular.ttf', source)
        self.assertNotIn('* {\n            font-family', source)
        self.assertIn('prefers-reduced-motion', source)

    def test_no_business_files_or_permissions_in_theme(self):
        self.assertFalse((ROOT / 'models').exists())
        self.assertFalse((ROOT / 'security').exists())
        self.assertFalse((ROOT / 'controllers').exists())

    def test_workspace_navigation_is_presentation_only(self):
        script = (ROOT / 'static/src/js/brand_navigation.js').read_text()
        template = (ROOT / 'static/src/xml/brand_navigation.xml').read_text()
        etree.fromstring(template.encode())
        self.assertIn('getAppsMenuItems()', template)
        self.assertIn('onNavBarDropdownItemSelection',
                      (ROOT.parent / 'muk_web_theme/static/src/webclient/navbar/navbar.xml').read_text())
        self.assertNotIn('fetch(', script)
        self.assertNotIn('orm.', script)
        self.assertNotIn('localStorage', script)
        self.assertIn('useDropdownState', script)
        self.assertIn('aria-label', template)
        self.assertIn('app.webIconData', template)
        self.assertIn('MapillaAppIcon', template)
        source = (ROOT / 'static/src/scss/brand_navigation.scss').read_text()
        css = sass.compile(string=source)
        self.assertIn('@media screen', css)
        self.assertIn('prefers-reduced-motion: no-preference', css)
        self.assertIn('prefers-reduced-motion: reduce', css)
        self.assertNotIn('transition: all', css)
        self.assertNotIn('.o_ntp_route_node', css)

if __name__ == '__main__':
    unittest.main()
