from lxml import etree

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import file_open


class TestStageCardSelector(TransactionCase):

    stage_fields = (
        'use_priming',
        'use_painting',
        'use_carpentry',
        'use_bases',
        'use_finishing',
        'use_tailoring',
        'use_upholstery',
        'use_packaging',
    )

    def _effective_form(self, xmlid):
        view = self.env.ref(xmlid)
        view._check_xml()
        arch = self.env[view.model].get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        return etree.fromstring(arch.encode())

    def _assert_card_selector(self, container):
        anchor = container.xpath(
            ".//field[@name='use_priming' "
            "and @widget='furniture_stage_selector']"
        )
        self.assertEqual(len(anchor), 1)
        self.assertIn(
            'o_furniture_stage_cards_field',
            anchor[0].get('class', ''),
        )
        for field_name in self.stage_fields[1:]:
            fields = container.xpath(
                ".//field[@name='%s']" % field_name
            )
            self.assertEqual(len(fields), 1, field_name)
            self.assertEqual(fields[0].get('invisible'), '1', field_name)

    def _assert_adaptive_float(self, field, label):
        options = field.get('options', '')
        self.assertIn(
            "'minDigits': 1",
            options,
            '%s list formatter' % label,
        )
        self.assertIn(
            "'min_display_digits': 1",
            options,
            '%s field component' % label,
        )

    def test_production_line_popup_hides_stage_cards(self):
        document = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        forms = document.xpath(
            "//field[@name='production_line_ids']/form"
        )
        self.assertEqual(len(forms), 1)
        line_form = forms[0]
        self.assertFalse(
            line_form.xpath(".//page[@string='مراحل التصنيع']")
        )
        self.assertFalse(
            line_form.xpath(".//field[@widget='furniture_stage_selector']")
        )
        for field_name in self.stage_fields:
            fields = line_form.xpath(
                ".//field[@name='%s']" % field_name
            )
            self.assertEqual(len(fields), 1, field_name)
            self.assertEqual(fields[0].get('invisible'), '1', field_name)
            self.assertEqual(fields[0].get('force_save'), '1', field_name)
        initialized = line_form.xpath(
            ".//field[@name='stage_selection_initialized']"
        )
        self.assertEqual(len(initialized), 1)
        self.assertEqual(initialized[0].get('invisible'), '1')
        self.assertEqual(initialized[0].get('force_save'), '1')
        self.assertEqual(
            len(line_form.xpath(".//page[@string='خامات الصنف']")),
            0,
        )
        progress_fields = line_form.xpath(
            ".//field[@name='stage_progress_html']"
        )
        self.assertEqual(len(progress_fields), 1)
        self.assertEqual(progress_fields[0].get('readonly'), '1')

    def test_production_form_uses_order_level_model_and_customer_data(self):
        document = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        self.assertFalse(
            document.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_info_card_weekly ')]"
            )
        )
        self.assertNotIn('بيانات التشغيل الأسبوعي', ''.join(document.itertext()))

        common_strips = document.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_order_common_strip ')]"
        )
        self.assertEqual(len(common_strips), 1)
        common_strip = common_strips[0]
        for field_name in (
            'buyer_partner_id',
            'beneficiary_partner_id',
        ):
            self.assertEqual(
                len(common_strip.xpath("./div/field[@name='%s']" % field_name)),
                1,
                field_name,
            )
        self.assertFalse(
            common_strip.xpath(".//field[@name='furniture_order_model_id']")
        )

        model_heroes = document.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_order_model_hero ')]"
        )
        self.assertEqual(len(model_heroes), 1)
        self.assertEqual(
            model_heroes[0].xpath(
                "./field[@name='furniture_order_model_id']/@class"
            ),
            ['o_furniture_order_model_hero_value'],
        )
        self.assertIn('الموديل', ''.join(model_heroes[0].itertext()))

        order_numbers = document.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_order_number ')]"
        )
        self.assertFalse(order_numbers)
        hidden_order_names = document.xpath(
            "//header/field[@name='name' and @invisible='1']"
        )
        self.assertEqual(len(hidden_order_names), 1)

        planning_strips = document.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_order_planning_strip ')]"
        )
        self.assertEqual(len(planning_strips), 1)
        planning_strip = planning_strips[0]
        for field_name in ('date_planned_start', 'date_planned_finish'):
            planned_fields = planning_strip.xpath(
                "./div/field[@name='%s']" % field_name
            )
            self.assertEqual(len(planned_fields), 1, field_name)
            self.assertEqual(planned_fields[0].get('required'), '1', field_name)

        products_page = common_strip.getparent()
        workbench_header = document.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_workbench_header ')]"
        )[0]
        self.assertLess(
            products_page.index(workbench_header),
            products_page.index(planning_strip),
        )
        self.assertLess(
            products_page.index(planning_strip),
            products_page.index(common_strip),
        )

        production_list = document.xpath(
            "//field[@name='production_line_ids']/list"
        )[0]
        self.assertEqual(
            production_list.xpath("./field[@name='furniture_order_model_id']")[0].get(
                'column_invisible'
            ),
            '1',
        )
        self.assertFalse(production_list.xpath("./field[@name='buyer_partner_id']"))
        self.assertFalse(
            production_list.xpath("./field[@name='beneficiary_partner_id']")
        )

    def test_kit_summary_click_uses_dedicated_product_detail_form(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        summary_fields = production_form.xpath(
            "//field[@name='kit_order_summary_line_ids']"
        )
        self.assertEqual(len(summary_fields), 1)
        self.assertIn(
            'furniture_mrp.view_furniture_mrp_production_line_summary_form',
            summary_fields[0].get('context', ''),
        )

        line_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_line_summary_form'
        )
        self.assertEqual(line_form.get('create'), '0')
        self.assertEqual(line_form.get('edit'), '0')
        self.assertEqual(line_form.get('delete'), '0')
        for field_name in (
            'production_id',
            'product_id',
            'sale_kit_product_id',
            'furniture_order_model_id',
            'buyer_partner_id',
            'beneficiary_partner_id',
            'kit_order_summary_qty',
            'width_cm',
            'depth_cm',
            'height_cm',
            'stage_summary',
            'stage_count',
            'stage_progress_html',
        ):
            self.assertEqual(
                len(line_form.xpath(".//field[@name='%s']" % field_name)),
                1,
                field_name,
            )
        for technical_field in (
            'activity_ids',
            'activity_state',
            'message_follower_ids',
            'message_partner_ids',
            'message_ids',
        ):
            self.assertFalse(
                line_form.xpath(".//field[@name='%s']" % technical_field),
                technical_field,
            )

    def test_production_form_hides_carryover_tab(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        self.assertFalse(
            production_form.xpath("//page[@string='🔁 الشغل المكمل']")
        )
        self.assertFalse(
            production_form.xpath("//field[@name='carryover_line_ids']")
        )

    def test_production_form_hides_stock_move_tab(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        self.assertFalse(
            production_form.xpath("//page[@string='📦 حركات المخزون']")
        )
        self.assertFalse(
            production_form.xpath("//field[@name='stock_move_ids']")
        )

    def test_production_form_promotes_product_table_and_removes_progress_tab(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        self.assertFalse(
            production_form.xpath(
                "//div[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_production_hero ')]"
            )
        )
        self.assertFalse(
            production_form.xpath("//page[@string='📊 تقدم المراحل']")
        )

        sheet = production_form.xpath(
            "//sheet[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_production_sheet ')]"
        )[0]
        stage_strip = sheet.xpath("./div[@name='furniture_stage_strip']")[0]
        main_notebook = sheet.xpath(
            "./notebook[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_tabs ')]"
        )[0]
        self.assertLess(sheet.index(stage_strip), sheet.index(main_notebook))
        self.assertFalse(
            sheet.xpath(
                "./group[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_overview_grid ')]"
            )
        )

        remaining_tabs = main_notebook.xpath('./page/@string')
        self.assertEqual(remaining_tabs, ['🧾 أصناف أمر التشغيل'])
        self.assertIn('🧾 أصناف أمر التشغيل', remaining_tabs)
        self.assertNotIn('📝 ملاحظات', remaining_tabs)
        self.assertFalse(main_notebook.xpath(".//field[@name='notes']"))
        self.assertNotIn('📊 تقدم المراحل', remaining_tabs)
        self.assertNotIn('🏭 مكونات الإنتاج (BoM)', remaining_tabs)

        with file_open(
            'furniture_mrp/static/src/css/furniture_mrp.css',
            mode='r',
        ) as stylesheet:
            production_form_css = stylesheet.read()
        self.assertIn(
            '.o_furniture_tabs > .o_notebook_headers',
            production_form_css,
        )
        self.assertEqual(
            production_form_css.count(
                '.o_form_view.o_furniture_production_form '
                '.o_furniture_stage_strip {'
            ),
            1,
        )
        self.assertEqual(
            production_form_css.count(
                '.o_form_view.o_furniture_production_form '
                '.o_furniture_stage_chip {'
            ),
            1,
        )

        workbench_headers = main_notebook.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_workbench_header ')]"
        )
        self.assertEqual(len(workbench_headers), 1)
        self.assertFalse(
            workbench_headers[0].xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_products_workbench_title ')]"
            )
        )
        self.assertEqual(
            len(workbench_headers[0].xpath(
                ".//button[@name='action_open_product_batch_wizard']"
            )),
            1,
        )
        self.assertFalse(
            main_notebook.xpath(
                ".//div[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_batch_add_entry ')]"
            )
        )
        table_shells = main_notebook.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_table_shell ')]"
        )
        self.assertEqual(len(table_shells), 1)
        for relation_name in ('production_line_ids', 'kit_order_summary_line_ids'):
            self.assertEqual(
                len(table_shells[0].xpath("./field[@name='%s']" % relation_name)),
                1,
                relation_name,
            )

    def test_each_product_row_has_stage_split_bom_popup(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        for relation_name in (
            'production_line_ids',
            'kit_order_summary_line_ids',
        ):
            relation = production_form.xpath(
                "//field[@name='%s']" % relation_name
            )[0]
            buttons = relation.xpath(
                ".//button[@name='action_open_bom_popup']"
            )
            self.assertEqual(len(buttons), 1, relation_name)
            self.assertEqual(buttons[0].get('string'), 'BoM')
            self.assertIn(
                'o_furniture_line_bom_button',
                buttons[0].get('class', ''),
            )

        for relation_name, quantity_name in (
            ('production_line_ids', 'product_qty'),
            ('kit_order_summary_line_ids', 'kit_order_summary_qty'),
        ):
            relation = production_form.xpath(
                "//field[@name='%s']" % relation_name
            )[0]
            material_list = relation.xpath('./list')[0]
            self._assert_adaptive_float(
                material_list.xpath(
                    "./field[@name='%s']" % quantity_name
                )[0],
                '%s quantity' % relation_name,
            )
            self._assert_adaptive_float(
                material_list.xpath("./field[@name='dimension_factor']")[0],
                '%s dimension factor' % relation_name,
            )

        popup = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_line_bom_popup'
        )
        self.assertEqual(popup.get('create'), '0')
        self.assertEqual(popup.get('edit'), '1')
        self.assertEqual(popup.get('delete'), '0')
        quantity_fields = popup.xpath(".//field[@name='product_qty']")
        self.assertEqual(len(quantity_fields), 1)
        self.assertEqual(quantity_fields[0].get('readonly'), '1')
        self._assert_adaptive_float(quantity_fields[0], 'popup product quantity')
        edit_quantity_buttons = popup.xpath(
            ".//button[@name='action_open_bom_quantity_wizard' "
            "and @type='object']"
        )
        self.assertFalse(edit_quantity_buttons)
        save_buttons = popup.xpath(".//footer/button[@special='save']")
        self.assertEqual(len(save_buttons), 1)
        self.assertIn('حفظ الكميات', save_buttons[0].get('string', ''))
        stage_fields = (
            'bom_priming_material_line_ids',
            'bom_painting_material_line_ids',
            'bom_carpentry_material_line_ids',
            'bom_bases_material_line_ids',
            'bom_finishing_material_line_ids',
            'bom_tailoring_material_line_ids',
            'bom_upholstery_material_line_ids',
            'bom_packaging_material_line_ids',
        )
        for field_name in stage_fields:
            fields_in_popup = popup.xpath(
                ".//field[@name='%s']" % field_name
            )
            self.assertEqual(len(fields_in_popup), 1, field_name)
            material_list = fields_in_popup[0].xpath('./list')[0]
            self.assertEqual(material_list.get('create'), '0')
            self.assertEqual(material_list.get('edit'), '1')
            self.assertEqual(material_list.get('delete'), '0')
            self.assertEqual(material_list.get('editable'), 'bottom')
            editable_flag = material_list.xpath(
                "./field[@name='bom_qty_editable']"
            )
            self.assertEqual(len(editable_flag), 1, field_name)
            self.assertEqual(
                editable_flag[0].get('column_invisible'),
                'True',
            )
            for column in (
                'product_id',
                'qty_needed',
                'product_uom_id',
                'purchase_unit_cost',
                'material_cost',
                'qty_available',
                'availability',
            ):
                self.assertEqual(
                    len(material_list.xpath("./field[@name='%s']" % column)),
                    1,
                    '%s:%s' % (field_name, column),
                )
            self.assertEqual(
                material_list.xpath("./field[@name='qty_needed']")[0].get(
                    'readonly'
                ),
                'not bom_qty_editable',
            )
            self._assert_adaptive_float(
                material_list.xpath("./field[@name='qty_needed']")[0],
                '%s material quantity' % field_name,
            )
            for readonly_column in (
                'product_id',
                'product_uom_id',
                'purchase_unit_cost',
                'material_cost',
                'qty_available',
                'availability',
            ):
                self.assertEqual(
                    material_list.xpath(
                        "./field[@name='%s']" % readonly_column
                    )[0].get('readonly'),
                    '1',
                    '%s:%s' % (field_name, readonly_column),
                )

    def test_production_product_table_uses_scoped_readable_card_rows(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        table_shell = production_form.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_table_shell ')]"
        )[0]
        heading = table_shell.xpath(
            "./div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_table_heading ')]"
        )[0]
        self.assertEqual(len(heading.xpath(
            "./span[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_table_heading_icon ')]/i"
        )), 1)
        self.assertEqual(len(heading.xpath(
            "./span[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_table_heading_copy ')]/strong"
        )), 1)

        for relation_name in (
            'production_line_ids',
            'kit_order_summary_line_ids',
        ):
            relation = table_shell.xpath(
                "./field[@name='%s']" % relation_name
            )[0]
            self.assertIn(
                'o_furniture_production_lines',
                relation.get('class', ''),
            )
            self.assertEqual(
                len(relation.xpath("./list/field[@name='product_id']")),
                1,
            )
            self.assertEqual(
                len(relation.xpath("./list/field[@name='stage_summary']")),
                1,
            )

        with file_open(
            'furniture_mrp/static/src/css/furniture_mrp.css',
            mode='r',
        ) as stylesheet:
            css = stylesheet.read()
        for selector_fragment in (
            '.o_furniture_production_lines .o_list_renderer',
            '.o_furniture_production_lines table.o_list_table thead th',
            '.o_furniture_production_lines table.o_list_table tbody tr.o_data_row td',
            '.o_furniture_production_lines td[name="product_id"]',
            '.o_furniture_production_lines td[name="product_qty"]',
            '.o_furniture_production_lines td[name="stage_summary"]',
            '.o_furniture_production_lines .o_furniture_line_bom_button',
        ):
            self.assertIn(selector_fragment, css)

    def test_costing_menu_uses_live_production_cost_views(self):
        action = self.env.ref('furniture_mrp.action_furniture_mrp_costing')
        self.assertEqual(action.res_model, 'furniture.mrp.production')
        self.assertEqual(action.view_mode, 'list,form')
        self.assertEqual(
            action.search_view_id,
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_production_costing_search'
            ),
        )
        self.assertIn('search_default_has_approved_cost', action.context)

        list_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_costing_list'
        )
        list_view._check_xml()
        list_document = etree.fromstring(list_view.arch_db.encode())
        self.assertEqual(list_document.get('create'), '0')
        for field_name in (
            'actual_costed_quantity',
            'material_cost',
            'labor_cost',
            'overhead_cost',
            'total_cost',
            'cost_per_unit',
        ):
            self.assertEqual(
                len(list_document.xpath(
                    ".//field[@name='%s']" % field_name
                )),
                1,
                field_name,
            )

        form_document = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_costing_form'
        )
        self.assertEqual(form_document.get('create'), '0')
        self.assertEqual(form_document.get('edit'), '0')
        self.assertEqual(form_document.get('delete'), '0')
        self.assertEqual(
            len(form_document.xpath(
                ".//field[@name='cost_distribution_html']"
            )),
            1,
        )
        self.assertEqual(
            len(form_document.xpath(
                ".//field[@name='stage_cost_entry_ids']"
            )),
            1,
        )

    def test_production_line_popup_uses_compact_quick_card_layout(self):
        document = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        line_form = document.xpath(
            "//field[@name='production_line_ids']/form"
        )[0]

        product_fields = line_form.xpath(".//field[@name='product_id']")
        self.assertEqual(len(product_fields), 1)
        self.assertEqual(
            product_fields[0].get('widget'),
            'furniture_quick_card_selector',
        )
        product_options = product_fields[0].get('options', '')
        for product_name in (
            'كنبة كبيرة',
            'كنبة صغيرة',
            'فوتيه',
            'شازلونج',
        ):
            self.assertIn(product_name, product_options)
        self.assertIn("'quick_limit': 4", product_options)

        model_fields = line_form.xpath(
            ".//field[@name='furniture_order_model_id']"
        )
        self.assertEqual(len(model_fields), 1)
        self.assertEqual(model_fields[0].get('invisible'), '1')

        customer_strips = line_form.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_line_customer_strip ')]"
        )
        compact_details = line_form.xpath(
            ".//group[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_line_compact_details ')]"
        )
        self.assertEqual(len(customer_strips), 0)
        self.assertEqual(len(compact_details), 1)
        for customer_field in ('buyer_partner_id', 'beneficiary_partner_id'):
            fields_found = line_form.xpath(
                ".//field[@name='%s']" % customer_field
            )
            self.assertEqual(len(fields_found), 1)
            self.assertEqual(fields_found[0].get('invisible'), '1')

        for actual_dimension in ('width_cm', 'depth_cm', 'height_cm'):
            self.assertEqual(
                len(compact_details[0].xpath(
                    ".//field[@name='%s']" % actual_dimension
                )),
                1,
            )

        for removed_field in (
            'bom_id',
            'bom_width_cm',
            'bom_depth_cm',
            'bom_height_cm',
            'dimension_factor',
            'dimension_label',
            'product_uom_id',
            'material_cost',
            'material_line_ids',
        ):
            self.assertFalse(
                line_form.xpath(".//field[@name='%s']" % removed_field),
                removed_field,
            )

    def test_quick_card_template_has_more_grid(self):
        with file_open(
            'furniture_mrp/static/src/xml/furniture_quick_card_selector.xml',
            mode='rb',
        ) as template_file:
            document = etree.parse(template_file)
        for template_name in (
            'furniture_mrp.FurnitureQuickCardSelector',
            'furniture_mrp.FurnitureMultiCardSelector',
        ):
            more_buttons = document.xpath(
                "//t[@t-name='%s']" % template_name
                + "//button[contains(concat(' ', normalize-space(@class), ' '), "
                + "' o_furniture_quick_card--more ')]"
            )
            self.assertEqual(len(more_buttons), 1, template_name)
            self.assertEqual(more_buttons[0].get('t-if'), 'showMoreCard')
            self.assertEqual(
                ''.join(more_buttons[0].itertext()).strip(),
                'More',
            )
            more_grids = document.xpath(
                "//t[@t-name='%s']" % template_name
                + "//div[contains(concat(' ', normalize-space(@class), ' '), "
                + "' o_furniture_quick_selector__more_grid ')]"
            )
            self.assertEqual(len(more_grids), 1, template_name)
            self.assertIn('showMoreCard', more_grids[0].get('t-if', ''))
            self.assertIn('cardState.expanded', more_grids[0].get('t-if', ''))
            self.assertEqual(
                len(more_grids[0].xpath(
                    ".//div[contains(concat(' ', normalize-space(@class), ' '), "
                    "' o_furniture_quick_selector__empty ')]"
                )),
                1,
                template_name,
            )

    def test_multi_quick_card_selector_has_instant_search_support(self):
        with file_open(
            'furniture_mrp/static/src/xml/furniture_quick_card_selector.xml',
            mode='rb',
        ) as template_file:
            document = etree.parse(template_file)
        template = document.xpath(
            "//t[@t-name='furniture_mrp.FurnitureMultiCardSelector']"
        )[0]
        search_inputs = template.xpath(
            ".//label[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_multi_selector__search ')]/input[@type='search']"
        )
        self.assertEqual(len(search_inputs), 1)
        self.assertEqual(search_inputs[0].get('t-on-input'), 'onSearchInput')
        self.assertEqual(
            search_inputs[0].get('t-att-value'),
            'cardState.searchQuery',
        )
        self.assertTrue(template.xpath(
            ".//button[@t-on-click.stop.prevent='clearSearch']"
        ))

        with file_open(
            'furniture_mrp/static/src/js/furniture_quick_card_selector.js',
            mode='r',
        ) as script_file:
            script = script_file.read()
        multi_selector = script.split(
            'export class FurnitureMultiCardSelector', 1
        )[1].split('const furnitureMultiCardSelector', 1)[0]
        self.assertIn('get filteredItems()', multi_selector)
        self.assertIn('.includes(query)', multi_selector)
        self.assertIn('onSearchInput(event)', multi_selector)
        self.assertIn('clearSearch()', multi_selector)
        self.assertIn('{ name: "searchable", type: "boolean"', script)

    def test_batch_product_wizard_is_model_first_and_creates_separate_rows(self):
        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        batch_buttons = production_form.xpath(
            ".//button[@name='action_open_product_batch_wizard']"
        )
        self.assertEqual(len(batch_buttons), 1)
        self.assertIn(
            'o_furniture_batch_add_button',
            batch_buttons[0].get('class', ''),
        )
        with file_open(
            'furniture_mrp/static/src/css/furniture_mrp.css',
            mode='r',
        ) as stylesheet:
            batch_button_css = stylesheet.read()
        self.assertIn(
            '.o_furniture_products_workbench_header .o_furniture_batch_add_button',
            batch_button_css,
        )
        self.assertIn(
            '.o_furniture_products_workbench_header .o_furniture_batch_add_button span',
            batch_button_css,
        )
        self.assertIn('color: #fff !important;', batch_button_css)
        self.assertIn('-webkit-text-fill-color: #fff !important;', batch_button_css)
        production_lists = production_form.xpath(
            "//field[@name='production_line_ids']/list"
        )
        self.assertEqual(len(production_lists), 1)
        self.assertEqual(production_lists[0].get('create'), '0')

        wizard = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_first_line_wizard_form'
        )
        choices = wizard.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_batch_product_choices ')]"
        )[0]
        model_field = choices.xpath(
            ".//field[@name='furniture_order_model_id']"
        )[0]
        product_field = choices.xpath(
            ".//field[@name='selected_product_ids']"
        )[0]
        self.assertLess(choices.index(model_field), choices.index(product_field))
        self.assertEqual(
            model_field.get('widget'),
            'furniture_quick_card_selector',
        )
        self.assertEqual(
            product_field.get('widget'),
            'furniture_multi_card_selector',
        )
        self.assertIn("'always_show_more': True", model_field.get('options', ''))
        self.assertIn("'always_show_more': True", product_field.get('options', ''))
        self.assertIn('كنبة كبيرة', product_field.get('options', ''))
        self.assertIn('شازلونج', product_field.get('options', ''))

        line_list = wizard.xpath(".//field[@name='line_ids']/list")[0]
        self.assertEqual(line_list.get('create'), '0')
        self.assertEqual(line_list.get('delete'), '0')
        for field_name in (
            'product_id',
            'product_qty',
            'dimensions_expanded',
            'width_cm',
            'depth_cm',
            'height_cm',
        ):
            self.assertEqual(
                len(line_list.xpath("./field[@name='%s']" % field_name)),
                1,
                field_name,
            )
        dimension_toggle = line_list.xpath(
            "./field[@name='dimensions_expanded']"
        )[0]
        self.assertEqual(
            dimension_toggle.get('widget'),
            'furniture_dimension_toggle',
        )
        mode_fields = wizard.xpath(".//field[@name='create_new_production']")
        self.assertEqual(len(mode_fields), 1)
        self.assertEqual(mode_fields[0].get('invisible'), '1')
        footer_buttons = wizard.xpath(
            ".//footer/button[@name='action_create_lines']"
        )
        self.assertEqual(len(footer_buttons), 2)
        self.assertEqual(
            {button.get('invisible') for button in footer_buttons},
            {'not create_new_production', 'create_new_production'},
        )
        for field_name in ('width_cm', 'depth_cm', 'height_cm'):
            self.assertIn(
                'not dimensions_expanded',
                line_list.xpath("./field[@name='%s']" % field_name)[0].get(
                    'invisible', ''
                ),
            )

    def test_quick_card_assets_include_multi_select_and_dimension_toggle(self):
        with file_open(
            'furniture_mrp/static/src/xml/furniture_quick_card_selector.xml',
            mode='rb',
        ) as template_file:
            document = etree.parse(template_file)
        self.assertEqual(
            len(document.xpath(
                "//t[@t-name='furniture_mrp.FurnitureMultiCardSelector']"
            )),
            1,
        )
        self.assertEqual(
            len(document.xpath(
                "//t[@t-name='furniture_mrp.FurnitureDimensionToggle']"
            )),
            1,
        )
        dimension_buttons = document.xpath(
            "//t[@t-name='furniture_mrp.FurnitureDimensionToggle']"
            "//button[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_dimension_toggle ')]"
        )
        self.assertEqual(len(dimension_buttons), 1)
        self.assertEqual(
            dimension_buttons[0].get('t-on-click.stop.prevent'),
            'toggle',
        )
        self.assertIsNone(dimension_buttons[0].get('t-on-click'))
        self.assertIsNone(dimension_buttons[0].get('t-att-disabled'))
        multi_buttons = document.xpath(
            "//t[@t-name='furniture_mrp.FurnitureMultiCardSelector']"
            "//button[@t-on-click='() => this.toggleItem(item)']"
        )
        self.assertEqual(len(multi_buttons), 2)
        self.assertTrue(all(button.get('t-att-aria-pressed') for button in multi_buttons))

        with file_open(
            'furniture_mrp/static/src/css/furniture_mrp.css',
            mode='r',
        ) as stylesheet_file:
            stylesheet = stylesheet_file.read()
        dimension_styles = stylesheet.split(
            '.o_form_view .o_furniture_dimension_toggle {',
            1,
        )[1].split('}', 1)[0]
        self.assertIn('min-width: 126px;', dimension_styles)
        self.assertNotIn('width: 100%;', dimension_styles)
        self.assertNotIn('min-height: 36px;', dimension_styles)

        with file_open(
            'furniture_mrp/static/src/js/furniture_quick_card_selector.js',
            mode='r',
        ) as script_file:
            script = script_file.read()
        self.assertIn(
            'async toggle() {\n'
            '        await this.props.record.update({',
            script,
        )

    def test_single_quick_card_selector_reloads_when_domain_changes(self):
        with file_open(
            'furniture_mrp/static/src/js/furniture_quick_card_selector.js',
            mode='r',
        ) as script_file:
            script = script_file.read()
        single_selector = script.split(
            'export class FurnitureQuickCardSelector', 1
        )[1].split('const furnitureQuickCardSelector', 1)[0]
        self.assertIn('onWillUpdateProps(async (nextProps)', single_selector)
        self.assertIn(
            'this.getDomainForProps(nextProps)',
            single_selector,
        )
        self.assertIn('const request = ++this.requestNumber;', single_selector)
        self.assertIn('if (request !== this.requestNumber)', single_selector)

    def test_stage_edit_wizard_uses_same_cards(self):
        document = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_line_stage_wizard_form'
        )
        self._assert_card_selector(document)
        self.assertFalse(document.xpath(".//field[@name='use_sewing']"))

    def test_independent_sewing_surfaces_are_legacy_only(self):
        sewing_menu = self.env.ref(
            'furniture_mrp.menu_furniture_mrp_sewing',
            raise_if_not_found=False,
        )
        self.assertFalse(sewing_menu and sewing_menu.active)
        for xmlid in (
            'furniture_mrp.action_furniture_mrp_sewing',
            'furniture_mrp.action_report_furniture_mrp_sewing',
            'furniture_mrp.view_furniture_mrp_sewing_form',
            'furniture_mrp.view_furniture_mrp_sewing_list',
            'furniture_mrp.report_sewing_template',
        ):
            self.assertFalse(
                self.env.ref(xmlid, raise_if_not_found=False),
                xmlid,
            )

        legacy_models = self.env['ir.model'].search([
            ('model', 'in', (
                'furniture.mrp.sewing',
                'furniture.mrp.sewing.worker.log',
            )),
        ])
        legacy_access = self.env['ir.model.access'].search([
            ('model_id', 'in', legacy_models.ids),
        ])
        self.assertTrue(legacy_access)
        # Explicit zero-permission ACL rows are kept for ordinary internal
        # users while managers and the exact legacy-stage supervisor retain
        # read-only access.  Only ACL rows that actually grant something must
        # therefore be read grants.
        granting_access = legacy_access.filtered(
            lambda access: any((
                access.perm_read,
                access.perm_write,
                access.perm_create,
                access.perm_unlink,
            ))
        )
        self.assertTrue(granting_access)
        self.assertTrue(all(granting_access.mapped('perm_read')))
        self.assertFalse(any(legacy_access.mapped('perm_write')))
        self.assertFalse(any(legacy_access.mapped('perm_create')))
        self.assertFalse(any(legacy_access.mapped('perm_unlink')))

        bom_form = self._effective_form(
            'furniture_mrp.view_mrp_bom_form_furniture_stages'
        )
        self.assertFalse(bom_form.xpath(".//field[@name='use_sewing']"))
        self.assertFalse(bom_form.xpath(
            ".//field[@name='sewing_material_line_ids']"
        ))

        production_form = self._effective_form(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        for xpath in (
            ".//button[@name='action_view_sewing']",
            ".//field[@name='location_sewing_wip_id']",
            ".//field[@name='location_sewing_id']",
            ".//field[@name='sewing_material_line_ids']",
        ):
            self.assertFalse(production_form.xpath(xpath), xpath)

        capacity_selection = dict(
            self.env['furniture.mrp.mps.capacity']
            ._fields['department']
            ._description_selection(self.env)
        )
        self.assertNotIn('sewing', capacity_selection)
        mps = self.env['furniture.mrp.mps'].create({
            'date_from': fields.Date.today(),
        })
        mps.action_initialize_capacity()
        self.assertEqual(len(mps.capacity_line_ids), 8)
        self.assertNotIn('sewing', mps.capacity_line_ids.mapped('department'))

    def test_batch_selector_has_per_order_select_all(self):
        with file_open(
            'furniture_mrp/static/src/xml/furniture_stage_selector.xml',
            mode='rb',
        ) as template_file:
            document = etree.parse(template_file)
        buttons = document.xpath(
            "//button[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_batch_select_all ') and "
            "@t-if='isBatchReleaseLine']"
        )
        self.assertEqual(len(buttons), 1)
        self.assertEqual(
            buttons[0].get('t-on-click.stop'),
            '() => this.selectAllAvailableStages()',
        )
        self.assertIn(
            'allAvailableStagesSelected',
            buttons[0].get('t-att-disabled', ''),
        )

        with file_open(
            'furniture_mrp/static/src/js/furniture_stage_selector.js',
            mode='r',
        ) as script_file:
            script = script_file.read()
        self.assertIn('selectAllAvailableStages()', script)
        self.assertIn('values[stage.field] = Boolean(', script)


class TestProductionConfirmationPlannedDates(TransactionCase):

    def _new_production(self, **values):
        return self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
            'use_priming': True,
            **values,
        })

    def _confirm(self, production):
        return production.with_context(
            furniture_skip_empty_order_warning=True,
        ).action_confirm()

    def test_confirmation_requires_both_planned_dates(self):
        planned_start = fields.Datetime.to_datetime('2026-08-22 08:00:00')
        planned_finish = fields.Datetime.to_datetime('2026-08-22 17:00:00')
        cases = (
            ({'date_planned_finish': planned_finish}, 'تاريخ البدء المخطط'),
            ({'date_planned_start': planned_start}, 'تاريخ الانتهاء المخطط'),
            ({}, 'تاريخ البدء المخطط'),
        )
        for values, missing_label in cases:
            production = self._new_production(**values)
            with self.assertRaisesRegex(ValidationError, missing_label):
                self._confirm(production)
            self.assertEqual(production.state, 'draft')

    def test_confirmation_succeeds_with_both_planned_dates(self):
        production = self._new_production(
            date_planned_start='2026-08-22 08:00:00',
            date_planned_finish='2026-08-22 17:00:00',
        )

        result = self._confirm(production)

        self.assertEqual(production.state, 'confirmed')
        self.assertEqual(result.get('tag'), 'reload')

    def test_batch_confirmation_rejects_all_before_any_side_effect(self):
        ready = self._new_production(
            date_planned_start='2026-08-22 08:00:00',
            date_planned_finish='2026-08-22 17:00:00',
        )
        missing_finish = self._new_production(
            date_planned_start='2026-08-23 08:00:00',
        )

        with self.assertRaises(ValidationError):
            self._confirm(ready | missing_finish)

        self.assertEqual(ready.state, 'draft')
        self.assertEqual(missing_finish.state, 'draft')
