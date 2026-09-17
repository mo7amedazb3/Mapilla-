from pathlib import Path

from lxml import etree

from odoo.modules.module import get_module_resource
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval


class TestFurnitureMrpDashboard(TransactionCase):

    button_attributes = (
        'name',
        'string',
        'type',
        'class',
        'display',
        'groups',
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Production = cls.env['furniture.mrp.production']

    def _document(self, xml_id):
        view = self.env.ref(xml_id)
        view._check_xml()
        return etree.fromstring(view.arch_db.encode())

    def _static_document(self, *relative_path):
        path = get_module_resource(
            'furniture_mrp', 'static', 'src', *relative_path
        )
        return etree.parse(path).getroot()

    def _static_source(self, *relative_path):
        path = get_module_resource(
            'furniture_mrp', 'static', 'src', *relative_path
        )
        return Path(path).read_text(encoding='utf-8')

    def _action_button_contract(self, xml_id, root_tag, action_xml_id):
        action = self.env.ref(action_xml_id)
        buttons = self._document(xml_id).xpath(
            (
                '//%s/header/button['
                "@name='%s'"
                ']'
            ) % (root_tag, action.id)
        )
        self.assertEqual(len(buttons), 1, xml_id)
        return {
            attribute: buttons[0].get(attribute)
            for attribute in self.button_attributes
        }

    def _batch_button_contract(self, xml_id, root_tag):
        return self._action_button_contract(
            xml_id,
            root_tag,
            'furniture_mrp.action_furniture_mrp_advance_material_batch_wizard',
        )

    def _batch_receipt_button_contract(self, xml_id, root_tag):
        return self._action_button_contract(
            xml_id,
            root_tag,
            'furniture_mrp.'
            'action_furniture_mrp_advance_material_batch_receipt_wizard',
        )

    def _batch_material_check_button_contract(self, xml_id, root_tag):
        return self._action_button_contract(
            xml_id,
            root_tag,
            'furniture_mrp.action_furniture_mrp_material_check_batch_wizard',
        )

    def _production_vals(self, name):
        return {
            'name': name,
            'company_id': self.env.company.id,
            'stage_plan_mode': 'custom',
            'use_priming': True,
            'use_painting': False,
            'use_carpentry': False,
            'use_finishing': False,
            'use_tailoring': False,
            'use_upholstery': False,
            'use_packaging': False,
        }

    def test_dashboard_and_weekly_menus_have_distinct_action_view_stacks(self):
        dashboard_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_dashboard'
        )
        weekly_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_production'
        )
        dashboard_menu = self.env.ref(
            'furniture_mrp.menu_furniture_mrp_dashboard'
        )
        root_menu = self.env.ref(
            'furniture_mrp.menu_furniture_mrp_root'
        )
        stage_dashboard_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_stage_dashboard_page'
        )
        weekly_menu = self.env.ref(
            'furniture_mrp.menu_furniture_mrp_production'
        )

        # The app root deliberately has no fixed action: Odoo opens the first
        # child visible to the current role, so workers land on their MPS queue
        # instead of the manager-only factory dashboard.
        self.assertFalse(root_menu.action)
        self.assertEqual(dashboard_menu.action, stage_dashboard_action)
        self.assertEqual(weekly_menu.action, weekly_action)
        self.assertNotEqual(dashboard_action, weekly_action)

        dashboard_views = dashboard_action.view_ids.sorted('sequence')
        expected_views = (
            (
                'kanban',
                self.env.ref(
                    'furniture_mrp.view_furniture_mrp_dashboard_kanban'
                ).id,
            ),
            (
                'list',
                self.env.ref(
                    'furniture_mrp.view_furniture_mrp_dashboard_list'
                ).id,
            ),
            (
                'form',
                self.env.ref(
                    'furniture_mrp.view_furniture_mrp_production_form'
                ).id,
            ),
        )
        self.assertEqual(
            tuple(
                (action_view.view_mode, action_view.view_id.id)
                for action_view in dashboard_views
            ),
            expected_views,
        )
        self.assertEqual(
            dashboard_action.search_view_id,
            self.env.ref('furniture_mrp.view_furniture_mrp_dashboard'),
        )
        self.assertNotIn(
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_production_kanban'
            ),
            dashboard_views.mapped('view_id'),
        )
        self.assertNotIn(
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_production_list'
            ),
            dashboard_views.mapped('view_id'),
        )

    def test_weekly_action_and_views_stay_unchanged(self):
        weekly_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_production'
        )
        weekly_search_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_search'
        )
        weekly_kanban_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_kanban'
        )
        weekly_list_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_list'
        )
        weekly_kanban = self._document(
            'furniture_mrp.view_furniture_mrp_production_kanban'
        )
        weekly_list = self._document(
            'furniture_mrp.view_furniture_mrp_production_list'
        )

        self.assertEqual(weekly_action.view_mode, 'kanban,list,form')
        self.assertEqual(weekly_kanban.tag, 'kanban')
        self.assertEqual(weekly_kanban.get('default_group_by'), 'state')
        self.assertEqual(weekly_list.tag, 'list')
        self.assertEqual(weekly_list.get('string'), 'أوامر التشغيل الأسبوعية')
        self.assertFalse(weekly_kanban.xpath(
            ".//field[@name='dashboard_sequence']"
        ))
        self.assertFalse(weekly_list.xpath(
            ".//field[@name='dashboard_sequence']"
        ))
        self.assertEqual(self.Production._order, 'name desc')

        # The weekly action deliberately has no explicit view bindings.  Its
        # original views must therefore remain Odoo's defaults even though the
        # dashboard has separate views for the same model.
        self.assertEqual(
            self.Production.get_view(view_type='kanban')['id'],
            weekly_kanban_view.id,
        )
        self.assertEqual(
            self.Production.get_view(view_type='list')['id'],
            weekly_list_view.id,
        )
        self.assertEqual(
            self.Production.get_view(view_type='search')['id'],
            weekly_search_view.id,
        )

        dashboard_views = (
            self.env.ref('furniture_mrp.view_furniture_mrp_dashboard'),
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_dashboard_kanban'
            ),
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_dashboard_list'
            ),
        )
        weekly_views = (
            weekly_search_view,
            weekly_kanban_view,
            weekly_list_view,
        )
        for dashboard_view, weekly_view in zip(
            dashboard_views, weekly_views
        ):
            self.assertGreater(
                dashboard_view.priority,
                weekly_view.priority,
            )

    def test_dashboard_manual_sequence_is_stored_and_resequenceable(self):
        sequence_field = self.Production._fields['dashboard_sequence']
        self.assertTrue(sequence_field.store)
        self.assertFalse(sequence_field.copy)

        dashboard_kanban = self._document(
            'furniture_mrp.view_furniture_mrp_dashboard_kanban'
        )
        dashboard_list = self._document(
            'furniture_mrp.view_furniture_mrp_dashboard_list'
        )
        for document in (dashboard_kanban, dashboard_list):
            self.assertTrue(
                document.get('default_order', '').startswith(
                    'dashboard_sequence'
                )
            )
            handles = document.xpath(
                ".//field[@name='dashboard_sequence' and @widget='handle']"
            )
            self.assertEqual(len(handles), 1)

        current_last = self.Production.search(
            [('company_id', '=', self.env.company.id)],
            order='dashboard_sequence desc, id desc',
            limit=1,
        )
        expected_first_sequence = (
            (current_last.dashboard_sequence or 0) + 10
        )
        orders = self.Production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create([
            self._production_vals('DASHBOARD-SEQUENCE-A'),
            self._production_vals('DASHBOARD-SEQUENCE-B'),
            self._production_vals('DASHBOARD-SEQUENCE-C'),
        ])
        self.assertEqual(
            orders.mapped('dashboard_sequence'),
            [
                expected_first_sequence,
                expected_first_sequence + 10,
                expected_first_sequence + 20,
            ],
        )

        protected_values = {
            'state': orders[2].state,
            'priority': orders[2].priority,
            'date_planned_start': orders[2].date_planned_start,
            'date_planned_finish': orders[2].date_planned_finish,
        }
        orders[2].write({'dashboard_sequence': expected_first_sequence - 5})
        ordered = self.Production.search(
            [('id', 'in', orders.ids)],
            order='dashboard_sequence asc, id asc',
        )
        self.assertEqual(ordered[0], orders[2])
        self.assertEqual(
            {
                'state': orders[2].state,
                'priority': orders[2].priority,
                'date_planned_start': orders[2].date_planned_start,
                'date_planned_finish': orders[2].date_planned_finish,
            },
            protected_values,
        )

    def test_dashboard_batch_button_matches_weekly_contract_exactly(self):
        weekly_contracts = (
            self._batch_button_contract(
                'furniture_mrp.view_furniture_mrp_production_kanban',
                'kanban',
            ),
            self._batch_button_contract(
                'furniture_mrp.view_furniture_mrp_production_list',
                'list',
            ),
        )
        dashboard_contracts = (
            self._batch_button_contract(
                'furniture_mrp.view_furniture_mrp_dashboard_kanban',
                'kanban',
            ),
            self._batch_button_contract(
                'furniture_mrp.view_furniture_mrp_dashboard_list',
                'list',
            ),
        )
        self.assertEqual(weekly_contracts[0], weekly_contracts[1])
        self.assertEqual(dashboard_contracts[0], weekly_contracts[0])
        self.assertEqual(dashboard_contracts[1], weekly_contracts[0])
        self.assertEqual(weekly_contracts[0]['type'], 'action')
        self.assertEqual(
            weekly_contracts[0]['name'],
            str(self.env.ref(
                'furniture_mrp.'
                'action_furniture_mrp_advance_material_batch_wizard'
            ).id),
        )

    def test_dashboard_batch_material_check_button_and_wizard_contract(self):
        weekly_contracts = (
            self._batch_material_check_button_contract(
                'furniture_mrp.view_furniture_mrp_production_kanban',
                'kanban',
            ),
            self._batch_material_check_button_contract(
                'furniture_mrp.view_furniture_mrp_production_list',
                'list',
            ),
        )
        dashboard_contracts = (
            self._batch_material_check_button_contract(
                'furniture_mrp.view_furniture_mrp_dashboard_kanban',
                'kanban',
            ),
            self._batch_material_check_button_contract(
                'furniture_mrp.view_furniture_mrp_dashboard_list',
                'list',
            ),
        )
        self.assertEqual(weekly_contracts[0], weekly_contracts[1])
        self.assertEqual(dashboard_contracts[0], weekly_contracts[0])
        self.assertEqual(dashboard_contracts[1], weekly_contracts[0])
        self.assertEqual(weekly_contracts[0]['type'], 'action')
        self.assertIn('btn-warning', weekly_contracts[0]['class'])

        action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_material_check_batch_wizard'
        )
        expected_view = self.env.ref(
            'furniture_mrp.'
            'view_furniture_mrp_material_check_batch_wizard_form'
        )
        action_data = self.env['ir.actions.actions']._for_xml_id(
            'furniture_mrp.action_furniture_mrp_material_check_batch_wizard'
        )
        context = action_data['context']
        if isinstance(context, str):
            context = safe_eval(context)
        self.assertEqual(
            action.res_model,
            'furniture.mrp.material.check.batch.wizard',
        )
        self.assertEqual(action.target, 'new')
        self.assertEqual(action.view_id, expected_view)
        self.assertTrue(context['auto_load_eligible_productions'])
        self.assertEqual(context['form_view_initial_mode'], 'edit')

        wizard_document = self._document(
            'furniture_mrp.'
            'view_furniture_mrp_material_check_batch_wizard_form'
        )
        selectors = wizard_document.xpath(
            "//field[@name='selected' and "
            "@widget='furniture_batch_material_check_selector']"
        )
        self.assertEqual(len(selectors), 1)
        select_all_widgets = wizard_document.xpath(
            "//field[@name='select_all' and "
            "@widget='furniture_batch_material_check_select_all']"
        )
        self.assertEqual(len(select_all_widgets), 1)
        self.assertEqual(
            len(wizard_document.xpath(
                "//button[@name='action_check_selected_materials']"
            )),
            1,
        )
        selector_source = self._static_source(
            'js', 'furniture_stage_selector.js'
        )
        selector_template = self._static_document(
            'xml', 'furniture_stage_selector.xml'
        )
        self.assertIn(
            'export class FurnitureBatchMaterialCheckSelector',
            selector_source,
        )
        self.assertIn(
            '"furniture_batch_material_check_selector"',
            selector_source,
        )
        self.assertIn(
            'export class FurnitureBatchMaterialCheckSelectAll',
            selector_source,
        )
        self.assertIn(
            '"furniture_batch_material_check_select_all"',
            selector_source,
        )
        self.assertIn(
            'record.data.line_ids?.records || []',
            selector_source,
        )
        self.assertIn(
            'await lineRecord.update({ selected: shouldSelect })',
            selector_source,
        )
        self.assertEqual(
            len(selector_template.xpath(
                "./t[@t-name='furniture_mrp."
                "FurnitureBatchMaterialCheckSelector']"
            )),
            1,
        )
        self.assertEqual(
            len(selector_template.xpath(
                "./t[@t-name='furniture_mrp."
                "FurnitureBatchMaterialCheckSelectAll']"
            )),
            1,
        )

    def test_dashboard_batch_receipt_button_matches_weekly_contract_exactly(self):
        weekly_contracts = (
            self._batch_receipt_button_contract(
                'furniture_mrp.view_furniture_mrp_production_kanban',
                'kanban',
            ),
            self._batch_receipt_button_contract(
                'furniture_mrp.view_furniture_mrp_production_list',
                'list',
            ),
        )
        dashboard_contracts = (
            self._batch_receipt_button_contract(
                'furniture_mrp.view_furniture_mrp_dashboard_kanban',
                'kanban',
            ),
            self._batch_receipt_button_contract(
                'furniture_mrp.view_furniture_mrp_dashboard_list',
                'list',
            ),
        )
        self.assertEqual(weekly_contracts[0], weekly_contracts[1])
        self.assertEqual(dashboard_contracts[0], weekly_contracts[0])
        self.assertEqual(dashboard_contracts[1], weekly_contracts[0])
        self.assertEqual(weekly_contracts[0]['type'], 'action')
        self.assertEqual(
            weekly_contracts[0]['name'],
            str(self.env.ref(
                'furniture_mrp.'
                'action_furniture_mrp_advance_material_batch_receipt_wizard'
            ).id),
        )

    def test_header_buttons_use_hidden_menu_actions_exactly(self):
        launchers = (
            (
                'furniture_mrp.'
                'action_furniture_mrp_advance_material_batch_wizard',
                'furniture_mrp.'
                'menu_furniture_mrp_advance_material_batch_wizard',
                'furniture.mrp.advance.material.batch.wizard',
                'furniture_mrp.'
                'view_furniture_mrp_advance_material_batch_wizard_form',
                'auto_load_eligible_productions',
            ),
            (
                'furniture_mrp.'
                'action_furniture_mrp_advance_material_batch_receipt_wizard',
                'furniture_mrp.'
                'menu_furniture_mrp_advance_material_batch_receipt_wizard',
                'furniture.mrp.advance.material.batch.receipt.wizard',
                'furniture_mrp.'
                'view_furniture_mrp_advance_material_batch_receipt_wizard_form',
                'auto_load_pending_batch_receipts',
            ),
        )
        for action_xml_id, menu_xml_id, model, view_xml_id, auto_key in launchers:
            with self.subTest(action=action_xml_id):
                action = self.env.ref(action_xml_id)
                menu = self.env.ref(menu_xml_id)
                action_data = self.env[
                    'ir.actions.actions'
                ]._for_xml_id(action_xml_id)
                expected_view = self.env.ref(view_xml_id)
                action_context = action_data['context']
                if isinstance(action_context, str):
                    action_context = safe_eval(action_context)

                self.assertFalse(menu.active)
                self.assertEqual(menu.action.id, action.id)
                self.assertEqual(action_data['type'], 'ir.actions.act_window')
                self.assertEqual(action_data['res_model'], model)
                self.assertEqual(action_data['target'], 'new')
                self.assertEqual(
                    action_data['views'], [(expected_view.id, 'form')]
                )
                self.assertTrue(action_context[auto_key])
                self.assertEqual(
                    action_context['form_view_initial_mode'], 'edit'
                )

    def test_dashboard_exposes_operational_details(self):
        expected_fields = {
            'state',
            'priority',
            'header_product_summary',
            'stage_completion_summary',
            'stage_current_stage',
            'material_availability',
            'date_planned_start',
            'date_planned_finish',
            'responsible_id',
            'buyer_partner_ids',
            'beneficiary_partner_ids',
            'material_cost',
            'labor_cost',
            'total_cost',
            'cost_per_unit',
            'dashboard_stage_label',
            'dashboard_stage_state_label',
            'dashboard_stage_planned_qty',
            'dashboard_stage_started_qty',
            'dashboard_stage_working_qty',
            'dashboard_stage_quality_qty',
            'dashboard_stage_completed_qty',
            'dashboard_stage_not_started_qty',
            'dashboard_stage_remaining_qty',
            'dashboard_stage_progress',
        }
        dashboard_kanban = self._document(
            'furniture_mrp.view_furniture_mrp_dashboard_kanban'
        )
        exposed_fields = {
            field.get('name')
            for field in dashboard_kanban.xpath('.//field[@name]')
        }
        self.assertTrue(expected_fields.issubset(exposed_fields))
        self.assertEqual(
            dashboard_kanban.get('js_class'),
            'furniture_mrp_stage_dashboard',
        )
        self.assertTrue(dashboard_kanban.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_dashboard_progress_track ')]"
        ))
        dashboard_list = self._document(
            'furniture_mrp.view_furniture_mrp_dashboard_list'
        )
        self.assertFalse(dashboard_list.xpath(
            ".//field[@name='progress']"
        ))
        self.assertFalse(dashboard_kanban.xpath(
            ".//*[@t-if and contains(@t-if, 'not record.') ]"
        ))
        self.assertEqual(len(dashboard_kanban.xpath(
            ".//*[@t-if='!record.date_planned_start.raw_value and "
            "!record.date_planned_finish.raw_value']"
        )), 1)

    def test_dashboard_uses_responsive_multi_column_order_grid(self):
        dashboard_css_path = get_module_resource(
            'furniture_mrp',
            'static',
            'src',
            'css',
            'furniture_mrp.css',
        )
        stage_css_path = get_module_resource(
            'furniture_mrp',
            'static',
            'src',
            'css',
            'mrp_stage_dashboard.css',
        )
        dashboard_css = Path(dashboard_css_path).read_text(encoding='utf-8')
        stage_css = Path(stage_css_path).read_text(encoding='utf-8')
        self.assertRegex(
            dashboard_css,
            r'\.o_furniture_factory_dashboard\s+\.o_kanban_renderer\s*\{'
            r'[^}]*grid-template-columns:\s*repeat\('
            r'auto-fit,\s*minmax\(520px,\s*1fr\)\);',
        )
        self.assertRegex(
            dashboard_css,
            r'@media\s*\(max-width:\s*767px\)\s*\{'
            r'[\s\S]*?\.o_furniture_factory_dashboard\s+'
            r'\.o_kanban_renderer\s*\{'
            r'[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);',
        )
        # ``mrp_stage_dashboard.css`` is loaded later and uses a more specific
        # controller selector.  Assert the final cascade rule too; otherwise a
        # valid base rule can exist while the actual browser still shows one
        # full-width order per row.
        self.assertRegex(
            stage_css,
            r'\.o_furniture_stage_dashboard_view\s+'
            r'\.o_kanban_renderer\s*\{'
            r'[^}]*grid-template-columns:\s*repeat\('
            r'auto-fit,\s*minmax\(520px,\s*1fr\)\)\s*!important;',
        )
        self.assertRegex(
            stage_css,
            r'@media\s*\(max-width:\s*767\.98px\)\s*\{'
            r'[\s\S]*?\.o_furniture_stage_dashboard_view\s+'
            r'\.o_kanban_renderer\s*\{'
            r'[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s*!important;',
        )
        self.assertRegex(
            stage_css,
            r'\.o_furniture_stage_dashboard__stage_copy\s+strong\s*\{'
            r'[^}]*overflow:\s*visible;'
            r'[^}]*text-overflow:\s*clip;'
            r'[^}]*white-space:\s*normal;',
        )
        self.assertRegex(
            stage_css,
            r'\.o_furniture_stage_dashboard_view\s+'
            r'\.o_furniture_stage_dashboard__stages\s*\{'
            r'[^}]*grid-template-columns:\s*repeat\('
            r'8,\s*minmax\(108px,\s*1fr\)\);',
        )
        self.assertNotRegex(
            stage_css,
            r'\.o_furniture_stage_dashboard_view\s+'
            r'\.o_furniture_stage_dashboard__stages\s*\{'
            r'[^}]*grid-template-columns:\s*repeat\('
            r'9,\s*minmax\(108px,\s*1fr\)\);',
        )

    def test_tailoring_material_cards_keep_multiple_items_readable(self):
        css = self._static_source('css', 'furniture_mrp.css')

        self.assertIn('width: min(1480px, 97vw) !important;', css)
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_setup_row\s*\{'
            r'[^}]*height:\s*72px;'
            r'[^}]*min-height:\s*72px;'
            r'[^}]*direction:\s*ltr;'
            r'[^}]*grid-template-columns:\s*'
            r'minmax\(315px,\s*1\.25fr\)\s*'
            r'minmax\(250px,\s*1fr\)\s*'
            r'minmax\(300px,\s*1\.25fr\)\s*'
            r'minmax\(220px,\s*0\.85fr\);',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_setup_product\s*\{'
            r'[^}]*align-items:\s*flex-start;'
            r'[^}]*direction:\s*rtl;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_setup_product_name,\s*'
            r'\.o_furniture_tailoring_setup_product_name\s+'
            r'\.o_field_widget,\s*'
            r'\.o_furniture_tailoring_setup_product_name\s+'
            r'\.o_field_widget\s+a\s*\{'
            r'[^}]*font-size:\s*1\.02rem;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_setup_notes\s*\{'
            r'[^}]*height:\s*52px;'
            r'[^}]*direction:\s*rtl;',
        )
        tailoring_css_start = css.index(
            '.o_furniture_tailoring_setup_row'
        )
        desktop_css = css[
            tailoring_css_start:
            css.index('@media (max-width: 900px)', tailoring_css_start)
        ]
        for selector in (
            r'\.o_furniture_tailoring_setup_product',
            r'\.o_furniture_tailoring_setup_material_cell\.is-fabric',
            r'\.o_furniture_tailoring_setup_material_cell\.is-takawe',
            r'\.o_furniture_tailoring_setup_notes',
        ):
            self.assertNotRegex(
                desktop_css,
                selector + r'\s*\{[^}]*grid-column:',
            )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_button\s*\{'
            r'[^}]*height:\s*46px;'
            r'[^}]*direction:\s*rtl;'
            r'[^}]*text-align:\s*right;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_summary\s*\{'
            r'[^}]*display:\s*block;'
            r'[^}]*overflow-x:\s*auto;'
            r'[^}]*overflow-wrap:\s*normal;'
            r'[^}]*text-overflow:\s*clip;'
            r'[^}]*white-space:\s*nowrap;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_summary,\s*'
            r'\.o_furniture_tailoring_material_empty\s*\{'
            r'[^}]*font-size:\s*clamp\(0\.76rem,\s*0\.82vw,\s*0\.82rem\);'
            r'[^}]*direction:\s*rtl;'
            r'[^}]*text-align:\s*right;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_summary\s+'
            r'\.o_field_widget\s*\{'
            r'[^}]*white-space:\s*nowrap\s*!important;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_setup_kit\s*\{'
            r'[^}]*display:\s*inline-flex;'
            r'[^}]*max-width:\s*100%;'
            r'[^}]*border-radius:\s*999px;'
            r'[^}]*text-overflow:\s*ellipsis;',
        )
        self.assertIn(
            'width: min(980px, 96vw) !important;',
            css,
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_choices\s+'
            r'\.o_furniture_quick_selector__primary\s*\{'
            r'[^}]*grid-template-columns:\s*repeat\('
            r'3,\s*minmax\(0,\s*1fr\)\);',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_editor_context\s*>'
            r'\s*\.is-product\s+strong,'
            r'[^}]*\{[^}]*font-size:\s*1\.38rem;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_editor_context\s*>'
            r'\s*\.is-model\s+strong,'
            r'[^}]*\{[^}]*font-size:\s*1\.14rem;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_editor_title\s*\{'
            r'[^}]*align-items:\s*center;'
            r'[^}]*justify-content:\s*center;'
            r'[^}]*text-align:\s*center;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_editor_title\s+strong,'
            r'[^}]*\{[^}]*font-size:\s*1\.32rem;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_size\s*\{'
            r'[^}]*border-radius:\s*999px;'
            r'[^}]*font-weight:\s*900;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_size\.is-fabric\s*\{'
            r'[^}]*background:\s*#ddf3ef;'
            r'[^}]*color:\s*#0e6b63;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_tailoring_material_size\.is-takawe\s*\{'
            r'[^}]*background:\s*#f9ecd4;'
            r'[^}]*color:\s*#8a5a16;',
        )
        self.assertRegex(
            css,
            r'td\[data-name="qty"\]\s+input\s*\{'
            r'[^}]*min-height:\s*42px;'
            r'[^}]*font-size:\s*1\.05rem;',
        )

    def test_kit_planner_uses_shared_compact_professional_layout(self):
        css = self._static_source('css', 'furniture_mrp.css')
        document = self._document(
            'furniture_mrp.view_furniture_mrp_kit_planner_form'
        )

        self.assertRegex(
            css,
            r'\.o_furniture_kit_planner_scope\s*\{'
            r'[^}]*display:\s*flex;'
            r'[^}]*align-items:\s*center;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_option_lines table\s*\{'
            r'[^}]*table-layout:\s*fixed;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_option_lines tbody tr\s*\{'
            r'[^}]*height:\s*54px;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_summaries\s*\{'
            r'[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_preview_lines \.o_list_renderer\s*\{'
            r'[^}]*min-height:\s*0\s*!important;'
            r'[^}]*width:\s*calc\(100%\s*-\s*16px\);'
            r'[^}]*margin-inline:\s*auto;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_preview_lines tbody tr:not\(\.fw-bold\)\s*\{'
            r'[^}]*height:\s*46px\s*!important;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_preview_lines td\[name="batch_image_1920"\] '
            r'\.o_field_image\s*\{'
            r'[^}]*width:\s*48px\s*!important;'
            r'[^}]*height:\s*36px\s*!important;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_kit_plan_preview_lines th\[data-name="buyer_partner_id"\],'
            r'[^}]*\.o_furniture_kit_plan_preview_lines td\[name="beneficiary_partner_id"\]\s*\{'
            r'[^}]*width:\s*140px\s*!important;'
            r'[^}]*min-width:\s*140px\s*!important;'
            r'[^}]*max-width:\s*140px\s*!important;',
        )
        preview_lists = document.xpath(
            "//field[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_kit_plan_preview_lines ')]/list"
        )
        self.assertEqual(len(preview_lists), 1)
        visible_fields = [
            field.get('name')
            for field in preview_lists[0].xpath('./field')
            if field.get('column_invisible') != '1'
        ]
        self.assertEqual(
            visible_fields,
            [
                'product_display_label',
                'buyer_partner_id',
                'beneficiary_partner_id',
                'dimension_label',
                'kit_piece_note',
                'kit_component_qty',
                'batch_image_1920',
            ],
        )

    def test_stage_control_panel_navigation_covers_all_active_stage_models(self):
        source = self._static_source('js', 'mrp_stage_control_panel.js')
        dashboard_method = source.split(
            'async openFurnitureMrpDashboard() {', 1
        )[1].split(
            'async _openFurnitureStageNavigationAction', 1
        )[0]
        self.assertIn(
            '"furniture_mrp.action_furniture_mrp_stage_dashboard_page"',
            dashboard_method,
        )
        self.assertNotIn(
            '"furniture_mrp.action_furniture_mrp_dashboard"',
            dashboard_method,
        )
        self.assertIn('clearBreadcrumbs: true', dashboard_method)

        declaration = source.split(
            'const FURNITURE_STAGE_MODELS = new Set([', 1
        )[1].split(']);', 1)[0]
        expected_models = {
            'furniture.mrp.priming',
            'furniture.mrp.painting',
            'furniture.mrp.carpentry',
            'furniture.mrp.bases',
            'furniture.mrp.finishing',
            'furniture.mrp.tailoring',
            'furniture.mrp.upholstery',
            'furniture.mrp.packaging',
        }
        declared_models = {
            line.strip().strip(',').strip('"')
            for line in declaration.splitlines()
            if 'furniture.mrp.' in line
        }
        self.assertEqual(declared_models, expected_models)

        template = self._static_document(
            'xml', 'mrp_stage_control_panel.xml'
        )
        self.assertEqual(len(template.xpath(
            "//button[contains(@class, 'o_furniture_production_nav_button')]"
        )), 1)
        dashboard_buttons = template.xpath(
            "//button[contains(@class, 'o_furniture_dashboard_nav_button')]"
        )
        self.assertEqual(len(dashboard_buttons), 1)
        self.assertEqual(
            dashboard_buttons[0].get('t-on-click.stop'),
            'openFurnitureMrpDashboard',
        )
        order_labels = template.xpath(
            "//span[contains(@class, 'o_furniture_production_cp_order')]"
        )
        self.assertEqual(len(order_labels), 1)
        self.assertIn(
            'isFurnitureProductionOrder',
            order_labels[0].get('t-if', ''),
        )
        self.assertEqual(
            order_labels[0].xpath('./strong/@t-esc'),
            ['furnitureProductionOrderName'],
        )
        self.assertIn(
            'get furnitureProductionOrderName()',
            source,
        )
        self.assertIn(
            'this.model?.root?.data?.name',
            source,
        )

    def test_stage_page_trigger_follows_native_header_buttons(self):
        """The full-page stage action belongs beside native header actions."""
        dashboard_kanban = self._document(
            'furniture_mrp.view_furniture_mrp_dashboard_kanban'
        )
        weekly_kanban = self._document(
            'furniture_mrp.view_furniture_mrp_production_kanban'
        )
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')

        self.assertEqual(
            dashboard_kanban.get('js_class'),
            'furniture_mrp_stage_dashboard',
        )
        self.assertNotEqual(
            weekly_kanban.get('js_class'),
            'furniture_mrp_stage_dashboard',
        )

        # ``position="inside"`` appends the client-only button after Odoo's
        # existing header-button loop in this exact always-visible slot.
        insertions = template.xpath(
            "./t[@t-name='furniture_mrp.StageDashboardKanbanView']/"
            "xpath[@expr=\"//t[@t-set-slot='control-panel-always-buttons']\" "
            "and @position='inside']"
        )
        self.assertEqual(len(insertions), 1)
        stage_buttons = insertions[0].xpath(
            "./button[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_analytics_button ')]"
        )
        self.assertEqual(len(stage_buttons), 1)
        stage_button = stage_buttons[0]
        self.assertEqual(stage_button.get('type'), 'button')
        self.assertIn(
            'openStageDashboard()',
            stage_button.get('t-on-click', ''),
        )
        self.assertIsNone(stage_button.get('t-att-aria-expanded'))
        self.assertIsNone(stage_button.get('t-att-disabled'))
        self.assertIn(
            'المراحل',
            ''.join(stage_button.itertext()),
        )

    def test_stage_analytics_render_on_dedicated_client_action_page(self):
        """Stage analytics use a normal action page, never a modal dialog."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')
        page_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_stage_dashboard_page'
        )
        new_order_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_new_production_product_wizard'
        )

        kanban_template = template.xpath(
            "./t[@t-name='furniture_mrp.StageDashboardKanbanView']"
        )
        self.assertEqual(len(kanban_template), 1)
        self.assertFalse(kanban_template[0].xpath(
            ".//xpath[@expr=\"//t[@t-component='props.Renderer']\" "
            "and @position='before']"
        ))
        self.assertFalse(kanban_template[0].xpath(
            ".//*[@t-call='furniture_mrp.StageDashboardPanel']"
        ))

        page_templates = template.xpath(
            "./t[@t-name='furniture_mrp.StageDashboardPage']"
        )
        self.assertEqual(len(page_templates), 1)
        self.assertFalse(page_templates[0].xpath('.//Dialog'))
        self.assertEqual(
            len(page_templates[0].xpath(
                ".//*[@t-call='furniture_mrp.StageDashboardPanel']"
            )),
            1,
        )
        self.assertFalse(page_templates[0].xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard_page__back ')]"
        ))
        page_action_buttons = page_templates[0].xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard_page__actions ')]/button"
        )
        self.assertEqual(len(page_action_buttons), 4)
        self.assertEqual(
            {button.get('t-on-click') for button in page_action_buttons},
            {
                '() => this.createProductionOrder()',
                '() => this.openFutureDeliveryOrders()',
                '() => this.openBatchMaterialWizard()',
                '() => this.openBatchMaterialCheckWizard()',
            },
        )
        buttons_by_click = {
            button.get('t-on-click'): button
            for button in page_action_buttons
        }
        self.assertEqual(
            buttons_by_click['() => this.createProductionOrder()'].get('t-if'),
            'permissions.canCreateProduction',
        )
        for click in (
            '() => this.openFutureDeliveryOrders()',
            '() => this.openBatchMaterialWizard()',
            '() => this.openBatchMaterialCheckWizard()',
        ):
            self.assertIn(
                '!isBatchSupervisorMode and !isOrderWorkflowSupervisorMode',
                buttons_by_click[click].get('t-if'),
            )

        self.assertEqual(
            page_action.tag,
            'furniture_mrp_stage_dashboard_page',
        )
        self.assertEqual(page_action.target, 'current')
        self.assertEqual(
            new_order_action.res_model,
            'furniture.mrp.production.first.line.wizard',
        )
        self.assertEqual(new_order_action.target, 'new')
        self.assertEqual(
            new_order_action.view_id,
            self.env.ref(
                'furniture_mrp.'
                'view_furniture_mrp_production_first_line_wizard_form'
            ),
        )
        new_order_context = safe_eval(new_order_action.context or '{}')
        self.assertTrue(new_order_context['default_create_new_production'])
        self.assertEqual(new_order_context['form_view_initial_mode'], 'edit')
        self.assertEqual(
            new_order_action.groups_id,
            self.env.ref('furniture_mrp.group_furniture_mrp_manager')
            | self.env.ref('furniture_mrp.group_furniture_mrp_supervisor'),
        )
        self.assertNotIn('import { Dialog }', source)
        self.assertNotIn('analyticsOpen', source)
        self.assertIn(
            'export class FurnitureMrpStageDashboardPage extends Component',
            source,
        )
        self.assertIn(
            'static template = "furniture_mrp.StageDashboardPage"',
            source,
        )
        self.assertIn(
            'static props = { ...standardActionServiceProps }',
            source,
        )
        self.assertIn(
            '"furniture_mrp.action_furniture_mrp_stage_dashboard_page"',
            source,
        )
        self.assertIn(
            'registry.category("actions").add(',
            source,
        )
        self.assertIn('import { user } from "@web/core/user"', source)
        self.assertIn(
            'user.checkAccessRight("furniture.mrp.production", "create")',
            source,
        )
        self.assertIn(
            '"furniture_mrp.action_furniture_mrp_new_production_product_wizard"',
            source,
        )
        self.assertNotIn('{ viewType: "form" }', source)
        self.assertIn(
            '"furniture_mrp.action_furniture_mrp_advance_material_batch_wizard"',
            source,
        )
        self.assertIn(
            '"furniture_mrp.action_furniture_mrp_material_check_batch_wizard"',
            source,
        )
        self.assertNotIn('openMainDashboard()', source)

        navigation = template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_navigation ')]"
        )
        analytics = template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard ')]"
        )
        self.assertEqual(len(navigation), 1)
        self.assertEqual(len(analytics), 1)
        self.assertFalse(
            navigation[0].xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_stage_dashboard__metrics ')]"
            )
        )

        stage_buttons = navigation[0].xpath(
            ".//button[@t-foreach='stageOptions' and @t-as='stage']"
        )
        self.assertEqual(len(stage_buttons), 1)
        self.assertIn(
            'selectStage(stage.code)',
            stage_buttons[0].get('t-on-click', ''),
        )
        self.assertIn(
            'selectedStageCode',
            stage_buttons[0].get('t-att-aria-pressed', ''),
        )

    def test_stage_dashboard_omits_overdue_dropdown_and_its_rpc(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')

        self.assertFalse(template.xpath(
            ".//*[contains(@class, 'o_furniture_stage_dashboard_page__overdue')]"
        ))
        self.assertNotIn('overdueOrders', source)
        self.assertNotIn('get_overdue_dashboard_orders', source)
        self.assertNotIn('toggleOverdueOrders', source)
        self.assertNotIn('openOverdueProduction', source)
        self.assertEqual(len(template.xpath(
            ".//header[@class='o_furniture_stage_dashboard_page__header']"
            "/div[@class='o_furniture_stage_dashboard__date_filter is-header']"
        )), 1)
        self.assertEqual(len(template.xpath(
            ".//button[contains(@t-on-click, 'openFutureDeliveryOrders()')]"
        )), 1)
        self.assertIn('async openFutureDeliveryOrders()', source)
        self.assertIn('async openProductionOrder(orderId)', source)
        self.assertIn('group_furniture_mrp_completion_operator', source)

    def test_stage_dashboard_material_check_button_matches_teal_palette(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        css = self._static_source('css', 'mrp_stage_dashboard.css')

        buttons = template.xpath(
            ".//button[contains(concat(' ', normalize-space(@class), ' '), "
            "' is-material-check ')]"
        )
        self.assertEqual(len(buttons), 1)
        button_classes = buttons[0].get('class', '')
        self.assertIn('btn-secondary', button_classes)
        self.assertNotIn('btn-warning', button_classes)
        self.assertEqual(
            buttons[0].get('t-on-click'),
            '() => this.openBatchMaterialCheckWizard()',
        )
        self.assertIn(
            '.o_furniture_stage_dashboard_page__action.is-material-check {',
            css,
        )
        self.assertIn(
            'background: linear-gradient(135deg, #348b83, #2b746f) !important;',
            css,
        )
        self.assertIn('-webkit-text-fill-color: #fff !important;', css)

    def test_stage_analytics_date_filter_is_server_backed_and_optional(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')

        date_filter = template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__date_filter ')]"
        )
        self.assertEqual(len(date_filter), 1)
        self.assertEqual(
            len(template.xpath(
                ".//header[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_stage_dashboard_page__header ')]"
                "//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_stage_dashboard__date_filter ')]"
            )),
            1,
        )
        self.assertFalse(template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_navigation ')]"
            "//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__date_filter ')]"
        ))
        date_inputs = date_filter[0].xpath(".//input[@type='date']")
        self.assertEqual(len(date_inputs), 2)
        self.assertIn(
            'onDateFromChange',
            date_inputs[0].get('t-on-change', ''),
        )
        self.assertIn(
            'onDateToChange',
            date_inputs[1].get('t-on-change', ''),
        )
        self.assertEqual(len(date_filter[0].xpath(
            ".//button[contains(@t-on-click, 'applyDateFilter()')]"
        )), 1)
        self.assertEqual(len(date_filter[0].xpath(
            ".//button[contains(@t-on-click, 'clearDateFilter()')]"
        )), 1)
        self.assertIn(
            'date_from: this.stageDashboard.appliedDateFrom || false',
            source,
        )
        self.assertIn(
            'date_to: this.stageDashboard.appliedDateTo || false',
            source,
        )

    def test_stage_selection_does_not_filter_main_order_cards(self):
        """Stage payloads may drive their page, never the native Kanban domain."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')

        self.assertNotIn('dashboardDomain', source)
        self.assertNotIn('_applyStageDomain', source)
        self.assertNotIn('this.model.load(', source)
        self.assertNotIn('from "@web/core/domain"', source)
        self.assertFalse(template.xpath(
            ".//xpath[contains(@expr, 'MultiRecordViewButton')]/"
            "attribute[@name='domain']"
        ))

        # Stage changes still refresh the client-action payload itself.
        self.assertIn('await this._reloadStage(stageCode)', source)
        self.assertIn(
            'await this._fetchStageDashboard(stageCode, '
            '{ finishLoading: false })',
            source,
        )

    def test_stage_kpis_open_payload_backed_order_drilldown(self):
        """Every KPI is actionable and its detail rows open real orders."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')

        metric_buttons = template.xpath(
            ".//button[@t-foreach='summaryMetrics' and @t-as='metric']"
        )
        self.assertEqual(len(metric_buttons), 1)
        metric_button = metric_buttons[0]
        self.assertEqual(metric_button.get('type'), 'button')
        self.assertIn(
            'toggleMetricDetails(metric.key)',
            metric_button.get('t-on-click', ''),
        )
        self.assertIn(
            'selectedMetricKey',
            metric_button.get('t-att-aria-expanded', ''),
        )

        detail_panels = template.xpath(".//*[@t-if='selectedMetric']")
        self.assertEqual(len(detail_panels), 1)
        detail_panel = detail_panels[0]
        detail_rows = detail_panel.xpath(
            ".//t[@t-foreach='metricDetailRows' and @t-as='row']"
            "/t[@t-call='furniture_mrp.StageDashboardOrderCard']"
        )
        self.assertEqual(len(detail_rows), 1)
        card = template.xpath(".//t[@t-name='furniture_mrp.StageDashboardOrderCard']")[0]
        self.assertEqual(len(card.xpath(
            ".//button[contains(@t-on-click, 'openProductionOrder(order.id)')]"
        )), 1)
        self.assertEqual(
            len(detail_panel.xpath(
                ".//button[contains(@t-on-click, 'closeMetricDetails()')]"
            )),
            1,
        )

        # Drilldown rows must come from the complete stage payload.  Depending
        # on the currently rendered Kanban records would silently omit orders
        # hidden by the pager or a renderer refresh.
        self.assertIn('this.stageDashboard.orders', source)
        self.assertNotIn('this.model.records.map', source)
        self.assertIn('import { FormViewDialog }', source)
        self.assertIn('this.dialog.add(FormViewDialog', source)
        self.assertIn('resModel: "furniture.mrp.production"', source)
        self.assertIn('resId: productionId', source)

    def test_stage_order_rows_have_external_visual_separation(self):
        """Adjacent production orders stay distinct without changing cards."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        css = self._static_source('css', 'mrp_stage_dashboard.css')

        containers = template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__details_rows ')]"
        )
        self.assertEqual(len(containers), 1)
        loops = containers[0].xpath(
            "./t[@t-foreach='metricDetailRows' and @t-as='row' and "
            "@t-key='row.order.id']"
        )
        self.assertEqual(len(loops), 1)
        self.assertEqual(
            [child.tag for child in loops[0]], ['t', 'span'],
        )
        self.assertEqual(loops[0][0].get('t-call'), 'furniture_mrp.StageDashboardOrderCard')
        separators = loops[0].xpath(
            "./span[@t-if='!row_last' and @aria-hidden='true' and "
            "contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__order_separator ')]"
        )
        self.assertEqual(len(separators), 1)
        self.assertRegex(
            css,
            r'\.o_furniture_stage_dashboard_view\s+'
            r'\.o_furniture_stage_dashboard__details_rows\s*\{'
            r'[^}]*display:\s*grid;'
            r'[^}]*gap:\s*0;'
            r'[^}]*padding:\s*8px;'
            r'[^}]*background:\s*transparent;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_stage_dashboard_view\s+'
            r'\.o_furniture_stage_dashboard__order_separator\s*\{'
            r'[^}]*display:\s*block;'
            r'[^}]*height:\s*4px;'
            r'[^}]*margin-block:\s*12px;'
            r'[^}]*margin-inline:\s*16px;'
            r'[^}]*border-radius:\s*999px;'
            r'[^}]*background:\s*linear-gradient\('
            r'[^)]*#000000\s+7%,'
            r'[^)]*#000000\s+50%,'
            r'[^)]*#000000\s+93%,'
            r'[^)]*\);',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_stage_dashboard_view\s+'
            r'\.o_furniture_stage_dashboard__order_separator\s*\{'
            r'[^}]*box-shadow:\s*0\s+2px\s+5px\s+'
            r'rgba\(0,\s*0,\s*0,\s*0\.22\);'
            r'[^}]*pointer-events:\s*none;',
        )
        self.assertNotIn('#548f88', css)
        self.assertNotIn('#2f746c', css)
        self.assertNotIn('rgba(47, 139, 131, 0.42)', css)
        self.assertNotIn('#5f9f98', css)
        self.assertNotIn('#4f8f88', css)

    def test_stage_kpi_rows_expose_explicit_working_breakdown(self):
        """A drilldown row explains planned, running and not-started stock."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')

        # The details use the same additive contract as the server payload.
        self.assertRegex(
            source,
            r'currentQty\s*=\s*asNumber\(\s*'
            r'order\s*&&\s*order\.planned_qty\s*\)',
        )
        self.assertRegex(
            source,
            r'notWorkingQty\s*=\s*asNumber\(\s*'
            r'order\s*&&\s*order\.remaining_qty\s*\)',
        )
        for property_name in (
            'currentLabel',
            'workingLabel',
            'notWorkingLabel',
        ):
            self.assertIn(property_name, source)

        self.assertRegex(
            source,
            r'get\s+metricWorkingBreakdown\s*\(\s*\)',
        )
        working_summaries = template.xpath(
            ".//*[@t-if='metricWorkingBreakdown']"
        )
        self.assertEqual(len(working_summaries), 1)
        working_summary_source = etree.tostring(
            working_summaries[0], encoding='unicode'
        )
        for expression in (
            'metricWorkingBreakdown.currentLabel',
            'metricWorkingBreakdown.workingLabel',
            'metricWorkingBreakdown.notWorkingLabel',
        ):
            self.assertIn(expression, working_summary_source)
        self.assertNotIn(
            'metricWorkingBreakdown.plannedLabel',
            working_summary_source,
        )

    def test_stage_drilldown_is_product_first_and_uses_piece_counts(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        card = template.xpath(".//t[@t-name='furniture_mrp.StageDashboardOrderCard']")[0]
        card_source = etree.tostring(card, encoding='unicode')
        for expression in (
            'order.model_name', 'order.buyer_summary', 'order.beneficiary_summary',
            'product.product_name', 'product.dimension_label', 'product.kit_name',
            'product.fabric_items', 'product.takawe_items', 'product.notes',
            'product.quantity_status.state', 'product.quantity_status.label',
            'product.quantity_status.quantity',
            'openOrderImage(order)', 'order.order_image_url',
        ):
            self.assertIn(expression, card_source)
        quantities = card.xpath(".//div[@class='o_furniture_admin_order_quantities']")
        self.assertEqual(len(quantities), 1)
        self.assertEqual(quantities[0].get('t-if'), '!isOrderWorkflowSupervisorMode')
        self.assertEqual(len(quantities[0].xpath('./span')), 1)
        self.assertEqual(len(quantities[0].xpath('./span/small')), 1)
        self.assertEqual(len(quantities[0].xpath('./span/strong')), 1)
        quantity_source = etree.tostring(quantities[0], encoding='unicode')
        for old_expression in ('product.working_qty', 'product.completed_qty', 'product.remaining_qty'):
            self.assertNotIn(old_expression, quantity_source)
        self.assertFalse(card.xpath('.//button//button'))
        self.assertFalse(card.xpath('.//button//input'))

    def test_order_textile_details_are_scoped_to_selected_stage(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        card = template.xpath(".//t[@t-name='furniture_mrp.StageDashboardOrderCard']")[0]
        for field in ('fabric', 'takawe', 'notes'):
            nodes = card.xpath(".//span[@class='o_furniture_order_supervisor_order__%s']" % field)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].get('t-if'), 'showOrderTextileDetails')
        product = card.xpath(".//article[@class='o_furniture_order_supervisor_order__product']")[0]
        self.assertEqual(product.get('t-att-class'), "{'is-compact-product': !showOrderTextileDetails}")
        self.assertIn('.o_furniture_order_supervisor_order__product.is-compact-product',
                      self._static_source('css', 'mrp_stage_dashboard.css'))

    def test_stage_order_drilldown_uses_trimmed_operational_layout(self):
        """Admin and supervisor use one card without enabling admin workflow controls."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')
        card = template.xpath(".//t[@t-name='furniture_mrp.StageDashboardOrderCard']")[0]
        calls = template.xpath(".//t[@t-call='furniture_mrp.StageDashboardOrderCard']")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(template.xpath(
            ".//t[@t-foreach='metricDetailRows']/t[@t-call='furniture_mrp.StageDashboardOrderCard']"
        )), 1)
        self.assertEqual(len(template.xpath(
            ".//t[@t-foreach='orderSupervisorOrders']/t[@t-call='furniture_mrp.StageDashboardOrderCard']"
        )), 1)
        for selector in (
            ".//label", ".//button[contains(@t-on-click, 'startOrder')]",
            ".//button[contains(@t-on-click, 'finishOrderStage')]",
            ".//button[contains(@t-on-click, 'requestOrderStageMaterials')]",
            ".//span[contains(@class, 'o_furniture_manual_quality')]",
        ):
            nodes = card.xpath(selector)
            self.assertTrue(nodes)
            for node in nodes:
                self.assertTrue(node.get('t-if', '').startswith('isOrderWorkflowSupervisorMode'))
        card_source = etree.tostring(card, encoding='unicode')
        for expression in (
            'order.stage_date_start', 'order.stage_date_finish',
            'order.operational_state', 'product.quantity_status.state',
        ):
            self.assertIn(expression, card_source)
        self.assertNotIn('order.date_planned_finish', card_source)
        self.assertIn('export function operationalStatus', source)
        self.assertEqual(len(template.xpath(
            ".//t[@t-call='furniture_mrp.StageDashboardOrderImage' and not(@t-if)]"
        )), 1)

    def test_non_order_metrics_group_exact_products_across_orders(self):
        """Quantity KPIs aggregate display rows without losing order audit."""
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')

        product_groups = template.xpath(
            ".//details[@t-foreach='metricProductRows' and @t-as='row']"
        )
        self.assertEqual(len(product_groups), 1)
        group_source = etree.tostring(product_groups[0], encoding='unicode')
        group_summaries = product_groups[0].xpath('./summary')
        self.assertEqual(len(group_summaries), 1)
        summary_source = etree.tostring(group_summaries[0], encoding='unicode')
        for expression in (
            'row.product.product_name',
            'row.product.model_name',
            'row.product.dimension_label',
            'row.buyerSummary',
            'row.beneficiarySummary',
            'row.toneClass',
            'row.working_qty',
            'row.completed_qty',
            'row.remaining_qty',
            'row.contributors',
            'contributor.order.name',
            'contributor.buyerNames',
            'contributor.beneficiaryNames',
            'contributor.kitNames',
            'contributor.routeSummaries',
            'openProductionOrder(contributor.order.id)',
        ):
            self.assertIn(expression, group_source)

        # The selected KPI value is already shown in the metric header. The
        # shared grouped-product card intentionally keeps only the three
        # operational quantities so planned/quality are not repeated as boxes.
        self.assertNotIn('row.planned_qty', summary_source)
        self.assertNotIn('row.quality_qty', summary_source)
        self.assertNotIn('>المخطط<', summary_source)
        self.assertNotIn('>في الجودة<', summary_source)
        self.assertNotIn('selectedMetric.label', summary_source)
        self.assertNotIn('row.contributionLabel', summary_source)
        self.assertNotIn('product_group_metric', summary_source)
        self.assertLess(
            summary_source.index('row.product.model_name'),
            summary_source.index('row.product.product_name'),
        )

        customer_groups = product_groups[0].xpath(
            ".//span[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__product_group_customers ')]"
        )
        self.assertEqual(len(customer_groups), 1)
        self.assertEqual(len(customer_groups[0].xpath('./span')), 2)

        self.assertIn('export function aggregateProductMetricRows', source)
        self.assertIn('productIdentityKey(product)', source)
        self.assertIn('pushUnique(group.buyerNames', source)
        self.assertIn('pushUnique(group.beneficiaryNames', source)
        self.assertIn('group.buyerSummary = summarizeNames', source)
        self.assertIn('group.beneficiarySummary = summarizeNames', source)
        self.assertIn('group.toneClass = productToneClass(group.key)', source)
        self.assertIn('const PRODUCT_CARD_TONES', source)
        self.assertIn('o_furniture_stage_dashboard__product_group_identity_line', group_source)
        identity_start = source.index('function productIdentityKey(product)')
        identity_end = source.index('\n}\n', identity_start)
        identity_source = source[identity_start:identity_end]
        for identity_field in (
            'product.product_id',
            'product.model_id',
            'product.bom_id',
            'product.dimension_label',
            'product.uom_id',
        ):
            self.assertIn(identity_field, identity_source)
        for audit_only_field in ('buyer_id', 'beneficiary_id', 'kit_id'):
            self.assertNotIn(audit_only_field, identity_source)

        self.assertIn('get isOrderMetric()', source)
        order_loops = template.xpath(
            ".//t[@t-foreach='metricDetailRows' and @t-as='row']"
        )
        self.assertEqual(len(order_loops), 1)
        order_container = order_loops[0].getparent()
        self.assertIn('isOrderMetric', order_container.get('t-if', ''))

    def test_stage_product_rows_keep_distinct_status_colours_and_readable_name(self):
        """Operational quantities stay visibly distinct in both dashboard modes."""
        css = self._static_source('css', 'mrp_stage_dashboard.css')

        for colour in (
            'background: #e8f4fb;',
            'background: #eaf7ee;',
            'background: #f5effa;',
            'background: #eaf6f3;',
        ):
            self.assertIn(colour, css)

        # Keep these selectors at least as specific as the generic direct-child
        # quantity box rule, otherwise that rule paints every box off-white.
        for selector in (
            '.o_furniture_stage_dashboard__product_quantities > span.is-product-name',
            '.o_furniture_stage_dashboard__product_quantities > span.is-working',
            '.o_furniture_stage_dashboard__product_quantities > span.is-completed',
            '.o_furniture_stage_dashboard__product_quantities > span.is-remaining',
        ):
            self.assertIn(selector, css)

        self.assertIn(
            '.o_furniture_stage_dashboard__product_group_product strong',
            css,
        )
        self.assertIn('font-size: 0.92rem;', css)

    def test_packaging_uses_shared_textile_order_card(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')
        css = self._static_source('css', 'mrp_stage_dashboard.css')
        shared_card = template.xpath(
            ".//t[@t-name='furniture_mrp.StageDashboardOrderCard']"
        )[0]
        card_source = etree.tostring(shared_card, encoding='unicode')

        self.assertNotIn("selectedStageCode === 'packaging'", card_source)
        self.assertNotIn('is-static-packaging-card', card_source)
        self.assertNotIn('is-static-packaging-card', css)
        self.assertIn(
            '["tailoring", "upholstery", "packaging"]',
            source,
        )
        self.assertEqual(len(shared_card.xpath('./details/summary')), 1)

    def test_order_supervisor_dashboard_is_selectable_sticky_order_workflow(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')
        css = self._static_source('css', 'mrp_stage_dashboard.css')

        workflow_branches = template.xpath(
            ".//t[@t-elif='isOrderWorkflowSupervisorMode']"
            "/t[@t-call='furniture_mrp.StageDashboardOrderSupervisor']"
        )
        self.assertEqual(len(workflow_branches), 1)
        workflow_templates = template.xpath(
            ".//t[@t-name='furniture_mrp.StageDashboardOrderSupervisor']"
        )
        self.assertEqual(len(workflow_templates), 1)
        workflow_template = workflow_templates[0]
        workflow_source = etree.tostring(
            workflow_template, encoding='unicode'
        )
        shared_card = template.xpath(".//t[@t-name='furniture_mrp.StageDashboardOrderCard']")[0]
        image_template = template.xpath(".//t[@t-name='furniture_mrp.StageDashboardOrderImage']")[0]
        workflow_source += etree.tostring(shared_card, encoding='unicode')
        workflow_source += etree.tostring(image_template, encoding='unicode')

        order_loops = workflow_template.xpath(
            ".//t[@t-foreach='orderSupervisorOrders' "
            "and @t-as='order' and @t-key='order.id']"
        )
        self.assertEqual(len(order_loops), 1)
        checkboxes = shared_card.xpath(
            ".//label[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_order_supervisor_order__select ')]"
            "/input[@type='checkbox' "
            "and @t-att-checked='isSupervisorOrderSelected(order.id)' "
            "and contains(@t-on-change, "
            "'toggleSupervisorOrderSelection(order.id)')]"
        )
        self.assertEqual(len(checkboxes), 1)
        metric_loops = workflow_template.xpath(
            ".//*[@t-foreach='orderSupervisorSummaryMetrics' "
            "and @t-as='metric' and @t-key='metric.key']"
        )
        self.assertEqual(len(metric_loops), 1)

        for expression in (
            'stageDashboard.selectedOrderIds.length',
            'hasOrderSupervisorSelection',
            'clearSupervisorOrderSelection()',
            'order.product_lines',
            'product.fabric_items',
            'product.fabric_summary',
            'product.takawe_items',
            'product.takawe_summary',
            'product.notes',
            'product.can_open_bom',
            'openOrderProductBom(order, product)',
            'عرض BoM',
            'requestOrderStageMaterials(order)',
            'order.can_receive_materials',
            'is-receive-materials',
            'استلام من المخزن',
            'order.can_start_stage',
            'startOrderAllProducts(order)',
            '<span>بدء المرحلة</span>',
            'order.can_finish_stage',
            'finishOrderStage(order)',
            '<span>إنهاء المرحلة</span>',
            'product.can_start_stage',
            'startOrderProductStage(order, product)',
            'بدء المرحلة لهذا الصنف',
            'openOrderImage(order)',
            'order.order_image_url',
            'stageDashboard.imagePreviewUrl',
            'closeOrderImage()',
        ):
            self.assertIn(expression, workflow_source)
        self.assertNotIn('متابعة طلب الخامات', workflow_source)
        self.assertNotIn('product.fabric_piece_size', workflow_source)
        for css_class in (
            'o_furniture_order_supervisor_dashboard',
            'o_furniture_order_supervisor_metrics is-sticky',
            'o_furniture_order_supervisor_order',
            'o_furniture_order_supervisor_order__header',
            'o_furniture_order_supervisor_order__stage_overview',
            'o_furniture_order_supervisor_order__model',
            'o_furniture_order_supervisor_order__actions',
            'o_furniture_order_supervisor_order__image',
            'o_furniture_order_supervisor_order__products',
            'o_furniture_order_supervisor_order__product',
            'o_furniture_order_supervisor_order__bom',
            'o_furniture_order_supervisor_order__product_start',
            'o_furniture_order_supervisor_order__fabric',
            'o_furniture_order_supervisor_order__takawe',
            'o_furniture_order_supervisor_order__notes',
            'o_furniture_order_supervisor_image_lightbox',
        ):
            self.assertIn(css_class, workflow_source)

        # This mode has only the three requested KPIs.  The ordinary admin
        # planned/running cards and the old per-product quantity trio must not
        # be rendered inside the order-supervisor template.
        for hidden_contract in (
            'الكمية المخططة',
            'قيد التشغيل',
            'product.planned_qty',
            'product.working_qty',
            'product.completed_qty',
            'product.remaining_qty',
        ):
            self.assertNotIn(hidden_contract, etree.tostring(workflow_template, encoding='unicode'))

        for javascript_contract in (
            'const ORDER_SUPERVISOR_STAGE_CODES',
            'orderSupervisorMode: Boolean(source.order_supervisor_mode)',
            'selectedOrderIds: []',
            'busyOrderActionKey: ""',
            'imagePreviewUrl: ""',
            'get isOrderWorkflowSupervisorMode()',
            'get orderSupervisorOrders()',
            'get selectedOrderSupervisorOrders()',
            'get orderSupervisorSummaryMetrics()',
            'isSupervisorOrderSelected(orderId)',
            'toggleSupervisorOrderSelection(orderId)',
            'requestOrderStageMaterials(order)',
            'order.can_receive_materials = Boolean(order.can_receive_materials)',
            'openOrderProductBom(order, product)',
            'action_open_stage_dashboard_bom',
            'production_line_id: productionLineId',
            'startOrderProductStage(order, product)',
            'startOrderAllProducts(order)',
            'new Set(',
            '.flatMap((product) =>',
            'order.can_finish_stage = Boolean(order.can_finish_stage)',
            'finishOrderStage(order)',
            'action_stage_dashboard_finish_order_stage',
            'openOrderImage(order)',
            'closeOrderImage()',
            'action_stage_dashboard_request_order_materials',
            'action_stage_dashboard_start_order_product',
            'production_line_ids: productionLineIds',
            '[[Number(order.id)]]',
            'stage_code: this.selectedStageCode',
            'action.type === "ir.actions.act_window"',
            '!Array.isArray(action.views)',
            'action.view_mode || "form"',
            'views: (viewModes.length ? viewModes : ["form"]).map(',
        ):
            self.assertIn(javascript_contract, source)

        metric_start = source.index('get orderSupervisorSummaryMetrics()')
        metric_end = source.index(
            '\n    get hasOrderSupervisorSelection()', metric_start
        )
        metric_source = source[metric_start:metric_end]
        for metric_key in ('key: "orders"', 'key: "completed"', 'key: "remaining"'):
            self.assertIn(metric_key, metric_source)
        for hidden_metric in ('key: "planned"', 'key: "working"', 'working_qty'):
            self.assertNotIn(hidden_metric, metric_source)
        self.assertIn('const orders = this.selectedOrderSupervisorOrders', metric_source)
        self.assertIn('order.completed_qty', metric_source)
        self.assertIn('order.remaining_qty', metric_source)

        selected_start = source.index('get selectedOrderSupervisorOrders()')
        selected_end = source.index(
            '\n    get orderSupervisorSummaryMetrics()', selected_start
        )
        selected_source = source[selected_start:selected_end]
        self.assertIn('this.stageDashboard.selectedOrderIds', selected_source)
        self.assertIn('selectedIds.has(Number(order.id))', selected_source)

        self.assertRegex(
            css,
            r'\.o_furniture_stage_dashboard\.is-order-supervisor-mode\s*\{'
            r'[^}]*overflow:\s*visible;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_dashboard\s*\{'
            r'[^}]*direction:\s*rtl;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_metrics\.is-sticky\s*\{'
            r'[^}]*position:\s*sticky;'
            r'[^}]*top:\s*7px;'
            r'[^}]*z-index:\s*18;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_order__select\s*\{'
            r'[^}]*min-height:\s*54px;'
            r'[^}]*touch-action:\s*manipulation;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_order__actions \.btn\s*\{'
            r'[^}]*min-height:\s*48px;'
            r'[^}]*touch-action:\s*manipulation;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_order__bom\s*\{'
            r'[^}]*min-height:\s*36px;'
            r'[^}]*touch-action:\s*manipulation;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_order__model strong\s*\{'
            r'[^}]*font-size:\s*clamp\(1\.42rem,\s*2\.1vw,\s*1\.92rem\);'
            r'[^}]*font-weight:\s*950;',
        )
        self.assertRegex(
            css,
            r'\.is-materials\.is-receive-materials\s*\{'
            r'[^}]*border-color:\s*#d6a53d;'
            r'[^}]*background:\s*#f3c764;'
            r'[^}]*color:\s*#4e3a0e;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_image_lightbox\s*\{'
            r'[^}]*position:\s*fixed;'
            r'[^}]*inset:\s*0;',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_order_supervisor_image_lightbox figure > img\s*\{'
            r'[^}]*max-height:\s*calc\(90vh - 70px\);'
            r'[^}]*object-fit:\s*contain;',
        )

    def test_painting_batches_share_the_compact_supervisor_layout(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        supervisor_sections = template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_supervisor_dashboard ')]"
        )
        self.assertEqual(len(supervisor_sections), 1)
        compact_batch_stages = supervisor_sections[0].get('t-att-class', '')
        for stage_code in (
            'priming',
            'painting',
            'carpentry',
            'bases',
            'finishing',
        ):
            self.assertIn(stage_code, compact_batch_stages)

    def test_batch_supervisor_dashboard_is_planned_only_touch_workflow(self):
        template = self._static_document('xml', 'mrp_stage_dashboard.xml')
        source = self._static_source('js', 'mrp_stage_dashboard.js')
        css = self._static_source('css', 'mrp_stage_dashboard.css')

        supervisor_sections = template.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_supervisor_dashboard ')]"
        )
        self.assertEqual(len(supervisor_sections), 1)
        section_source = etree.tostring(
            supervisor_sections[0], encoding='unicode'
        )

        # The restricted supervisor surface consumes the server-owned product
        # batches directly.  It must never reconstruct an operational batch
        # from the admin order/contributor payload: doing so both makes the
        # membership unstable and leaks production-order/customer metadata.
        batch_loops = supervisor_sections[0].xpath(
            ".//article[@t-foreach='supervisorProductBatches' "
            "and @t-as='batch' and @t-key='batch.batch_token']"
        )
        self.assertEqual(len(batch_loops), 1)
        for expression in (
            'selectedStage.planned_qty',
            'supervisorProductBatches',
            'batch.batch_token',
            'batch.product.model_name',
            'batch.product.product_name',
            'batch.product.dimension_label',
            'batch.stateClass',
            'batch.state_label',
            'batch.quantityLabel',
            'batch.toneClass',
            'formatBatchElapsed(batch)',
            'requestProductBatchMaterials(batch)',
            'receiveProductBatchMaterials(batch)',
            'startProductBatch(batch)',
            'finishProductBatch(batch)',
            'openProductBatchBom(batch)',
            'isProductBatchBusy(batch.batch_token)',
        ):
            self.assertIn(expression, section_source)
        for forbidden_reference in (
            'metricProductRows',
            'row.contributors',
            'contributor.',
            'contributor.order',
            'stageDashboard.orders',
            'order.product_lines',
            'order.id',
            'order.name',
            'production_id',
            'production_line_id',
            'buyer_',
            'beneficiary_',
            'customer',
            'openProductionOrder',
        ):
            self.assertNotIn(forbidden_reference, section_source)

        # Keep the supervisor product cards on the same visual system as the
        # admin dashboard instead of growing a second set of layout classes.
        for shared_class in (
            'o_furniture_stage_dashboard__metrics',
            'o_furniture_stage_dashboard__metric',
            'o_furniture_stage_dashboard__metric_icon',
            'o_furniture_stage_dashboard__metric_copy',
            'o_furniture_stage_dashboard__details',
            'o_furniture_stage_dashboard__details_header',
            'o_furniture_stage_dashboard__details_icon',
            'o_furniture_stage_dashboard__details_title',
            'o_furniture_stage_dashboard__details_total',
            'o_furniture_stage_dashboard__product_groups',
            'o_furniture_stage_dashboard__product_group',
            'o_furniture_stage_dashboard__product_group_summary',
            'o_furniture_stage_dashboard__product_group_identity',
            'o_furniture_stage_dashboard__product_group_identity_line',
            'o_furniture_stage_dashboard__product_group_model',
            'o_furniture_stage_dashboard__product_group_product',
            'o_furniture_stage_dashboard__product_group_dimension',
            'o_furniture_stage_dashboard__product_group_quantities',
        ):
            self.assertIn(shared_class, section_source)
        self.assertIn('is-supervisor-batch', section_source)
        self.assertIn('is-supervisor-single', section_source)
        self.assertIn("' is-state-' + (batch.stateClass || 'remaining')", section_source)
        model_nodes = supervisor_sections[0].xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__product_group_model ')]"
        )
        self.assertEqual(len(model_nodes), 1)
        self.assertIn('الموديل', ''.join(model_nodes[0].itertext()))

        for javascript_contract in (
            'source.product_batches',
            'productBatches',
            'get supervisorProductBatches()',
            'busyBatchToken',
            '_callProductBatchAction(methodName, batch, extra = {})',
            'action_stage_dashboard_review_product_batch_quality',
            'action_stage_dashboard_review_order_product_quality',
            'action_review_handoff_quality',
            'batch_token: batch.batch_token',
            'stage_code: batch.stage_code || this.selectedStageCode',
            'action_stage_dashboard_request_product_batch_materials',
            'action_stage_dashboard_receive_product_batch_materials',
            'action_stage_dashboard_start_product_batch',
            'action_stage_dashboard_finish_product_batch',
            'action_open_stage_dashboard_product_batch_bom',
        ):
            self.assertIn(javascript_contract, source)
        for obsolete_contract in (
            'action_stage_dashboard_start_batch',
            'action_stage_dashboard_finish_batch',
            'startStageBatch',
            'finishStageBatch',
            'busyOrderId',
        ):
            self.assertNotIn(obsolete_contract, source)
        self.assertIn('setInterval(() => {', source)
        self.assertIn('onWillUnmount(() => {', source)
        self.assertIn(
            '.o_furniture_stage_dashboard__product_batch_actions',
            css,
        )
        self.assertIn(
            '.o_furniture_stage_dashboard__product_batch_timer',
            css,
        )
        self.assertIn('touch-action: manipulation;', css)
        self.assertIn('["unit", "units", "unit(s)"]', source)
        self.assertIn('_t("وحدات")', source)
        self.assertRegex(
            css,
            r'\.o_furniture_stage_dashboard__product_group_summary\s*\{'
            r'[^}]*min-height:\s*58px;'
            r'[^}]*grid-template-areas:\s*"identity quantities actions";',
        )
        self.assertRegex(
            css,
            r'\.o_furniture_stage_dashboard__product_batch_actions\s*\{'
            r'[^}]*flex-wrap:\s*nowrap;'
            r'[^}]*border-top:\s*0;',
        )
        self.assertIn(
            '.o_furniture_stage_dashboard__product_group.is-supervisor-batch.is-state-completed',
            css,
        )

        restricted_groups = (
            self.env.ref(
                'furniture_mrp.group_furniture_mrp_supervisor_priming'
            )
            | self.env.ref(
                'furniture_mrp.group_furniture_mrp_supervisor_carpentry'
            )
            | self.env.ref(
                'furniture_mrp.group_furniture_mrp_supervisor_bases'
            )
            | self.env.ref(
                'furniture_mrp.group_furniture_mrp_supervisor_finishing'
            )
        )
        self.assertIn(
            'furniture_mrp.group_furniture_mrp_supervisor_finishing',
            source,
        )
        for menu_xmlid in (
            'furniture_mrp.menu_furniture_mrp_production',
            'furniture_mrp.menu_furniture_mrp_mps',
            'furniture_mrp.menu_furniture_mrp_departments',
            'furniture_mrp.menu_furniture_mrp_costing',
        ):
            menu = self.env.ref(menu_xmlid)
            self.assertFalse(menu.groups_id & restricted_groups)
            self.assertIn(
                self.env.ref('furniture_mrp.group_furniture_mrp_manager'),
                menu.groups_id,
            )
