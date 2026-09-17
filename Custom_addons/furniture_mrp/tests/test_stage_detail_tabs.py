from lxml import etree

from odoo.tests.common import TransactionCase


class TestStageDetailTabs(TransactionCase):

    stage_form_xmlids = (
        'furniture_mrp.view_furniture_mrp_priming_form',
        'furniture_mrp.view_furniture_mrp_painting_form',
        'furniture_mrp.view_furniture_mrp_carpentry_form',
        'furniture_mrp.view_furniture_mrp_bases_form',
        'furniture_mrp.view_furniture_mrp_finishing_form',
        'furniture_mrp.view_furniture_mrp_tailoring_form',
        'furniture_mrp.view_furniture_mrp_upholstery_form',
        'furniture_mrp.view_furniture_mrp_packaging_form',
    )

    production_material_fields = {
        'priming': 'priming_material_line_ids',
        'painting': 'painting_material_line_ids',
        'carpentry': 'carpentry_material_line_ids',
        'bases': 'bases_material_line_ids',
        'finishing': 'finishing_material_line_ids',
        'tailoring': 'tailoring_material_line_ids',
        'upholstery': 'upholstery_material_line_ids',
        'packaging': 'packaging_material_line_ids',
    }

    hidden_pages = {
        'furniture_mrp.view_furniture_mrp_priming_form': 'تفاصيل التقديم',
        'furniture_mrp.view_furniture_mrp_painting_form': 'تفاصيل تصنيع دهانات',
        'furniture_mrp.view_furniture_mrp_carpentry_form': 'تفاصيل التجميع',
        'furniture_mrp.view_furniture_mrp_finishing_form': 'قائمة الفحص',
        'furniture_mrp.view_furniture_mrp_tailoring_form': 'مواد التفصيل',
        'furniture_mrp.view_furniture_mrp_upholstery_form': 'تفاصيل كسوه',
    }

    def _effective_view(self, view, view_type):
        arch = self.env[view.model].get_view(
            view_id=view.id,
            view_type=view_type,
        )['arch']
        return etree.fromstring(arch.encode())

    def _effective_form(self, view):
        return self._effective_view(view, 'form')

    def test_legacy_detail_tabs_are_hidden(self):
        for xmlid, label_fragment in self.hidden_pages.items():
            view = self.env.ref(xmlid)
            view._check_xml()
            pages = self._effective_form(view).xpath('//notebook/page')
            targets = [
                page for page in pages
                if label_fragment in page.get('string', '')
            ]
            self.assertEqual(len(targets), 1, (xmlid, label_fragment))
            self.assertEqual(targets[0].get('invisible'), '1')
            self.assertTrue(any(
                'العمالة' in page.get('string', '')
                and page.get('invisible') != '1'
                for page in pages
            ), xmlid)
            self.assertTrue(any(
                'الجودة' in page.get('string', '')
                and page.get('invisible') != '1'
                for page in pages
            ), xmlid)

    def test_painting_operational_tab_is_preserved(self):
        view = self.env.ref('furniture_mrp.view_furniture_mrp_painting_form')
        document = self._effective_form(view)
        self.assertTrue(document.xpath(
            "//page[contains(@string, 'مراحل تصنيع الدهانات') "
            "and not(@invisible='1')]"
        ))
        self.assertFalse(document.xpath("//field[@name='substage_plan']"))
        for field_name in (
            'substage_priming_required',
            'substage_assembly_required',
            'substage_cells_required',
            'substage_veneer_required',
            'substage_impregnation_required',
            'substage_paint_required',
            'current_substage',
            'substage_progress',
            'substage_cells_external_state',
            'substage_veneer_external_state',
            'substage_paint_external_state',
        ):
            self.assertTrue(
                document.xpath("//field[@name='%s']" % field_name),
                field_name,
            )
        self.assertFalse(document.xpath(
            "//button[@name='action_complete_current_substage']"
        ))
        self.assertEqual(
            len(document.xpath("//button[@name='action_complete_internal_substage']")),
            3,
        )
        substage_rows = document.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_painting_substage_row ')]"
        )
        self.assertEqual(len(substage_rows), 6)
        for row in substage_rows:
            class_tokens = set((row.get('class') or '').split())
            self.assertIn('flex-nowrap', class_tokens)
            self.assertNotIn('flex-wrap', class_tokens)
        outside_group = document.xpath(
            "//group[contains(@string, 'خارج المصنع')]"
        )
        self.assertEqual(len(outside_group), 1)
        self.assertEqual(outside_group[0].get('col'), '1')
        for method_name in (
            'action_external_substage_exit',
            'action_external_substage_deliver',
            'action_external_substage_receive',
        ):
            self.assertEqual(
                len(document.xpath("//button[@name='%s']" % method_name)),
                3,
                method_name,
            )

    def test_all_stage_forms_use_a_dedicated_quality_tab(self):
        for xmlid in self.stage_form_xmlids:
            view = self.env.ref(xmlid)
            view._check_xml()
            document = self._effective_form(view)
            quality_pages = document.xpath(
                "//notebook/page[contains(@string, 'الجودة') "
                "and not(@invisible='1')]"
            )
            self.assertEqual(len(quality_pages), 1, xmlid)
            for field_name in (
                'quality_inspector_id',
                'quality_check',
                'quality_notes',
            ):
                self.assertEqual(
                    len(quality_pages[0].xpath(
                        ".//field[@name='%s']" % field_name
                    )),
                    1,
                    (xmlid, field_name),
                )

    def test_stage_forms_hide_nonessential_operational_fields(self):
        hidden_by_view = {
            'furniture_mrp.view_furniture_mrp_carpentry_form': (
                'pieces_count',
            ),
            'furniture_mrp.view_furniture_mrp_finishing_form': (
                'finishing_type',
                'serial_number',
                'packaging_type',
                'packaging_notes',
            ),
            'furniture_mrp.view_furniture_mrp_packaging_form': (
                'packaging_type',
                'package_count',
                'packaging_notes',
            ),
            'furniture_mrp.view_furniture_mrp_tailoring_form': (
                'cutting_pattern',
                'cutting_method',
            ),
        }
        for xmlid in self.stage_form_xmlids:
            view = self.env.ref(xmlid)
            document = self._effective_form(view)
            self.assertFalse(
                document.xpath("//field[@name='priority']"),
                xmlid,
            )
            self.assertFalse(
                document.xpath("//field[@name='stage_batch_qty']"),
                xmlid,
            )
            for field_name in hidden_by_view.get(xmlid, ()):
                self.assertFalse(
                    document.xpath("//field[@name='%s']" % field_name),
                    (xmlid, field_name),
                )

        for xmlid, hidden_fields in (
            (
                'furniture_mrp.view_furniture_mrp_carpentry_list',
                ('pieces_count',),
            ),
            (
                'furniture_mrp.view_furniture_mrp_finishing_list',
                ('finishing_type', 'serial_number'),
            ),
            (
                'furniture_mrp.view_furniture_mrp_tailoring_list',
                ('cutting_method',),
            ),
        ):
            view = self.env.ref(xmlid)
            document = self._effective_view(view, 'list')
            for field_name in hidden_fields:
                self.assertFalse(
                    document.xpath("//field[@name='%s']" % field_name),
                    (xmlid, field_name),
                )

        # Keep the database fields for backwards compatibility and existing
        # records; this change intentionally simplifies only the UI.
        for model_name, field_names in (
            ('furniture.mrp.carpentry', ('pieces_count',)),
            (
                'furniture.mrp.finishing',
                (
                    'finishing_type',
                    'serial_number',
                    'packaging_type',
                    'packaging_notes',
                ),
            ),
            (
                'furniture.mrp.packaging',
                ('packaging_type', 'package_count', 'packaging_notes'),
            ),
            (
                'furniture.mrp.tailoring',
                ('cutting_pattern', 'cutting_method', 'stage_batch_qty'),
            ),
        ):
            for field_name in field_names:
                self.assertIn(
                    field_name,
                    self.env[model_name]._fields,
                    (model_name, field_name),
                )

    def test_packaging_form_keeps_only_basic_header_data(self):
        view = self.env.ref('furniture_mrp.view_furniture_mrp_packaging_form')
        view._check_xml()
        document = self._effective_form(view)
        page_labels = [
            page.get('string', '')
            for page in document.xpath('//notebook/page')
        ]
        self.assertFalse(any('تفاصيل' in label for label in page_labels))
        self.assertTrue(any('العمالة' in label for label in page_labels))
        self.assertTrue(any('الجودة' in label for label in page_labels))
        self.assertFalse(document.xpath(
            "//group[contains(@string, 'الجودة والتواريخ')]"
        ))
        self.assertEqual(len(document.xpath(
            "//group[@string='التواريخ']/field[@name='date_start']"
        )), 1)
        self.assertEqual(len(document.xpath(
            "//group[@string='التواريخ']/field[@name='date_finish']"
        )), 1)

    def test_product_line_bom_popup_uses_distinct_stage_fields(self):
        production_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        production_document = self._effective_form(production_view)
        self.assertFalse(production_document.xpath(
            "//page[@name='production_material_components']"
        ))

        line_model = self.env['furniture.mrp.production.line']
        popup_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_line_bom_popup'
        )
        popup_document = self._effective_form(popup_view)
        for stage in self.production_material_fields:
            field_name = 'bom_%s_material_line_ids' % stage
            field = line_model._fields[field_name]
            self.assertEqual(field.inverse_name, 'production_line_id')
            self.assertFalse(field.compute)
            self.assertEqual(field.domain, [('stage', '=', stage)])

            nodes = popup_document.xpath(".//field[@name='%s']" % field_name)
            self.assertEqual(len(nodes), 1, field_name)
            self.assertEqual(nodes[0].get('readonly'), '0')
            lists = nodes[0].xpath('./list')
            self.assertEqual(len(lists), 1)
            self.assertEqual(lists[0].get('create'), '0')
            self.assertEqual(lists[0].get('edit'), '1')
            self.assertEqual(lists[0].get('delete'), '0')
            self.assertEqual(lists[0].get('editable'), 'bottom')

    def test_stage_specific_material_fields_filter_their_rows(self):
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        product = self.env['product.product'].create({
            'name': 'Production Stage Field Material',
            'type': 'consu',
            'is_storable': True,
        })
        production_line_a = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).create({
            'production_id': production.id,
            'product_id': product.id,
            'product_qty': 1.0,
        })
        production_line_b = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).create({
            'production_id': production.id,
            'product_id': product.id,
            'product_qty': 2.0,
        })
        priming_a = self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': production_line_a.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'qty_needed': 1.0,
            'stage': 'priming',
        })
        packaging_a = self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': production_line_a.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'qty_needed': 2.0,
            'stage': 'packaging',
        })
        priming_b = self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': production_line_b.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'qty_needed': 3.0,
            'stage': 'priming',
        })
        packaging_b = self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': production_line_b.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'qty_needed': 4.0,
            'stage': 'packaging',
        })

        production.material_display_line_id = production_line_a
        self.assertEqual(production.priming_material_line_ids, priming_a)
        self.assertEqual(production.packaging_material_line_ids, packaging_a)

        production.material_display_line_id = production_line_b
        self.assertEqual(production.priming_material_line_ids, priming_b)
        self.assertEqual(production.packaging_material_line_ids, packaging_b)
