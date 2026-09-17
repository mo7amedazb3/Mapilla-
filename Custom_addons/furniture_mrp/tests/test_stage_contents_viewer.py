from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import file_open


TEST_IMAGE = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
    b'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)
TEST_IMAGE_2 = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0k'
    b'AAAAFElEQVR4nGNkYPj/n4GBgYGJAQoAHRkCAjRcHicAAAAASUVORK5CYII='
)


class TestStageContentsViewer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_supervisor = new_test_user(
            cls.env,
            login='furniture_compact_stage_viewer',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_supervisor,'
                'furniture_mrp.group_furniture_mrp_supervisor_tailoring,'
                'furniture_mrp.group_furniture_mrp_supervisor_upholstery'
            ),
        )
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.fabric = cls.env['product.product'].create({
            'name': 'Viewer Fabric',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        cls.fabric.product_tmpl_id.write({
            'furniture_tailoring_material_kind': 'fabric',
        })
        cls.takawe = cls.env['product.product'].create({
            'name': 'Viewer Takawe',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        cls.takawe.product_tmpl_id.write({
            'furniture_tailoring_material_kind': 'takawe',
        })
        cls.other_fabric = cls.env['product.product'].create({
            'name': 'Viewer Other Fabric',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        cls.other_fabric.product_tmpl_id.write({
            'furniture_tailoring_material_kind': 'fabric',
        })

        cls.production = cls.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'product_qty': 3.0,
            'state': 'confirmed',
            'use_tailoring': True,
            'use_upholstery': True,
            'tailoring_set_image_1920': TEST_IMAGE,
            'tailoring_set_image_token': 'stage-viewer-order-image',
        })
        cls.tailoring_only_line = cls._create_production_line(
            'Viewer Tailoring Only',
            sequence=10,
            use_tailoring=True,
            use_upholstery=False,
        )
        cls.upholstery_only_line = cls._create_production_line(
            'Viewer Upholstery Only',
            sequence=20,
            use_tailoring=False,
            use_upholstery=True,
        )
        cls.shared_line = cls._create_production_line(
            'Viewer Shared Piece',
            sequence=30,
            use_tailoring=True,
            use_upholstery=True,
        )
        cls.viewer_kit_model = cls.env['furniture.product.model'].create({
            'name': 'Viewer Saved Kit Model',
        })
        cls.viewer_kit_product = cls.env['product.product'].create({
            'name': 'Viewer Saved Kit',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
            'furniture_model_id': cls.viewer_kit_model.id,
        })
        cls.viewer_kit_bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.viewer_kit_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': cls.viewer_kit_product.id,
            'furniture_model_id': cls.viewer_kit_model.id,
        })
        cls.tailoring = cls.env['furniture.mrp.tailoring'].create({
            'name': 'TAL/COMPACT/VIEW/TEST',
            'production_order_id': cls.production.id,
        })
        cls.upholstery = cls.env['furniture.mrp.upholstery'].create({
            'name': 'UPH/COMPACT/VIEW/TEST',
            'production_order_id': cls.production.id,
        })
        cls.production.write({
            'tailoring_order_id': cls.tailoring.id,
            'upholstery_order_id': cls.upholstery.id,
        })

        Allocation = cls.env[
            'furniture.mrp.tailoring.material.allocation'
        ].sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        )
        Allocation.create({
            'production_id': cls.production.id,
            'production_line_id': cls.shared_line.id,
            'material_kind': 'fabric',
            'product_id': cls.fabric.id,
            'qty': 2.5,
            'product_uom_id': cls.unit_uom.id,
        })
        Allocation.create({
            'production_id': cls.production.id,
            'production_line_id': cls.shared_line.id,
            'material_kind': 'takawe',
            'product_id': cls.takawe.id,
            'qty': 4.0,
            'product_uom_id': cls.unit_uom.id,
            'piece_size': '45',
        })
        cls.shared_line.sudo().with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).write({
            'kit_bom_id': cls.viewer_kit_bom.id,
            'kit_instance_number': 2,
            'batch_image_1920': TEST_IMAGE,
            'batch_image_token': 'stage-viewer-order-image',
        })

    @classmethod
    def _create_production_line(
        cls,
        name,
        sequence,
        use_tailoring,
        use_upholstery,
    ):
        product = cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        return cls.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'production_id': cls.production.id,
            'sequence': sequence,
            'product_id': product.id,
            'product_qty': 1.0,
            'stage_selection_initialized': True,
            'use_tailoring': use_tailoring,
            'use_upholstery': use_upholstery,
        })

    def _open_stage_viewer(self, stage):
        action = stage.with_user(
            self.stage_supervisor,
        ).action_view_current_stage_products()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.tailoring.setup.wizard',
        )
        self.assertEqual(action['target'], 'new')
        compact_form = self.env.ref(
            'furniture_mrp.view_furniture_mrp_tailoring_setup_wizard_form'
        )
        self.assertEqual(action['view_id'], compact_form.id)
        self.assertEqual(action['views'], [(compact_form.id, 'form')])
        self.assertEqual(
            action['context']['form_view_initial_mode'],
            'readonly',
        )
        wizard = self.env[action['res_model']].with_user(
            self.stage_supervisor,
        ).browse(action['res_id']).exists()
        self.assertTrue(wizard)
        self.assertEqual(wizard.production_id, self.production)
        self.assertTrue(wizard.view_only)
        return action, wizard

    def test_tailoring_action_reuses_compact_readonly_stage_rows(self):
        old_viewer_count = self.env[
            'furniture.mrp.stage.transfer.wizard'
        ].search_count([])

        _action, wizard = self._open_stage_viewer(self.tailoring)

        self.assertEqual(wizard.viewer_stage, 'tailoring')
        self.assertTrue(wizard.has_order_image)
        self.assertEqual(wizard.order_image_128, TEST_IMAGE)
        web_values = wizard.with_context(bin_size=True).read([
            'has_order_image',
            'order_image_128',
        ])[0]
        self.assertTrue(web_values['has_order_image'])
        self.assertTrue(web_values['order_image_128'])
        self.assertEqual(
            wizard.line_ids.mapped('production_line_id'),
            self.tailoring_only_line | self.shared_line,
        )
        self.assertNotIn(
            self.upholstery_only_line,
            wizard.line_ids.mapped('production_line_id'),
        )
        self.assertEqual(
            self.env['furniture.mrp.stage.transfer.wizard'].search_count([]),
            old_viewer_count,
        )

        shared_row = wizard.line_ids.filtered(
            lambda row: row.production_line_id == self.shared_line
        ).ensure_one()
        self.assertIn(self.fabric.display_name, shared_row.fabric_summary)
        self.assertIn('2.5', shared_row.fabric_summary)
        self.assertIn(self.takawe.display_name, shared_row.takawe_summary)
        self.assertIn('4', shared_row.takawe_summary)
        self.assertIn(
            self.viewer_kit_product.display_name,
            shared_row.kit_display_label,
        )
        self.assertIn('2', shared_row.kit_display_label)
        self.assertTrue(shared_row.has_image)
        self.assertTrue(shared_row.image_128)
        self.assertEqual(
            shared_row.image_cache_token,
            self.shared_line.batch_image_token,
        )

    def test_upholstery_action_reuses_compact_readonly_stage_rows(self):
        _action, wizard = self._open_stage_viewer(self.upholstery)

        self.assertEqual(wizard.viewer_stage, 'upholstery')
        self.assertEqual(
            wizard.line_ids.mapped('production_line_id'),
            self.upholstery_only_line | self.shared_line,
        )
        self.assertNotIn(
            self.tailoring_only_line,
            wizard.line_ids.mapped('production_line_id'),
        )

    def test_readonly_viewer_rejects_all_editor_and_image_mutations(self):
        _action, wizard = self._open_stage_viewer(self.tailoring)
        row = wizard.line_ids.filtered(
            lambda item: item.production_line_id == self.shared_line
        ).ensure_one()
        baseline_allocations = [
            (
                allocation.material_kind,
                allocation.product_id.id,
                allocation.qty,
                allocation.product_uom_id.id,
            )
            for allocation in self.shared_line.tailoring_material_allocation_ids
        ]
        baseline_image = self.shared_line.batch_image_1920
        baseline_image_token = self.shared_line.batch_image_token

        for method_name in (
            'action_select_fabric_editor',
            'action_select_takawe_editor',
        ):
            with self.assertRaises(AccessError):
                getattr(row, method_name)()

        wizard.write({
            'active_editor_mode': 'fabric',
            'active_material_kind': 'fabric',
            'active_production_line_id': self.shared_line.id,
            'active_source_revision':
                self.production.tailoring_material_revision,
            'active_source_product_id': self.shared_line.product_id.id,
            'active_source_bom_id': self.shared_line.bom_id.id,
            'active_source_product_qty': self.shared_line.product_qty,
            'active_source_stage_signature': wizard._stage_signature(
                self.shared_line
            ),
            'editor_line_ids': [(0, 0, {
                'product_id': self.other_fabric.id,
                'qty': 9.0,
                'product_uom_id': self.unit_uom.id,
            })],
        })
        with self.assertRaises(AccessError):
            wizard.action_apply_active_editor()
        with self.assertRaises(AccessError):
            row.upload_piece_image(TEST_IMAGE_2)
        with self.assertRaises(AccessError):
            wizard.upload_order_image(TEST_IMAGE_2)
        with self.assertRaises(AccessError):
            row.save_piece_note('غير مسموح')

        self.shared_line.invalidate_recordset([
            'tailoring_material_allocation_ids',
            'batch_image_1920',
            'batch_image_token',
        ])
        self.assertEqual([
            (
                allocation.material_kind,
                allocation.product_id.id,
                allocation.qty,
                allocation.product_uom_id.id,
            )
            for allocation in self.shared_line.tailoring_material_allocation_ids
        ], baseline_allocations)
        self.assertEqual(self.shared_line.batch_image_1920, baseline_image)
        self.assertEqual(
            self.shared_line.batch_image_token,
            baseline_image_token,
        )

    def test_legacy_stage_image_save_rpc_is_manager_only(self):
        legacy_viewer = self.env[
            'furniture.mrp.stage.transfer.wizard'
        ].with_user(self.stage_supervisor).create({
            'production_id': self.production.id,
            'source_stage': 'tailoring',
            'target_stage': 'tailoring',
            'prepared_source_stage': 'tailoring',
            'view_only': True,
        })

        with self.assertRaises(AccessError):
            legacy_viewer.action_save_first_stage_images()

    def test_manual_transfer_action_still_uses_standard_transfer_form(self):
        action = self.production.action_open_stage_transfer_wizard()
        transfer_form = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_transfer_wizard_form'
        )
        planner_form = self.env.ref(
            'furniture_mrp.view_furniture_mrp_kit_planner_form'
        )

        self.assertEqual(action['view_id'], transfer_form.id)
        self.assertEqual(action['views'], [(transfer_form.id, 'form')])
        self.assertNotEqual(action['view_id'], planner_form.id)

    def test_stage_forms_expose_viewer_button(self):
        for xmlid in (
            'furniture_mrp.view_furniture_mrp_tailoring_form',
            'furniture_mrp.view_furniture_mrp_upholstery_form',
        ):
            view = self.env.ref(xmlid)
            arch = self.env[view.model].with_user(
                self.stage_supervisor
            ).get_view(
                view_id=view.id,
                view_type='form',
            )['arch']
            document = etree.fromstring(arch.encode())
            buttons = document.xpath(
                "//header/button[@name='action_view_current_stage_products']"
            )
            self.assertEqual(len(buttons), 1, xmlid)

    def test_compact_form_has_explicit_readonly_viewer_branches(self):
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_tailoring_setup_wizard_form'
        )
        view._check_xml()
        document = etree.fromstring(view.arch_db.encode())

        self.assertTrue(document.xpath("//field[@name='view_only']"))
        self.assertTrue(document.xpath("//field[@name='viewer_stage']"))

        card = document.xpath(
            "//field[@name='line_ids']/kanban/templates/t[@t-name='card']"
        )
        self.assertEqual(len(card), 1)
        card = card[0]
        readonly_branches = card.xpath(
            ".//*[@t-if='record.view_only.raw_value']"
        )
        self.assertTrue(readonly_branches)
        self.assertTrue(all(
            not branch.xpath(".//button[@type='object']")
            and not branch.xpath(
                ".//field[@widget='furniture_piece_image_upload']"
            )
            for branch in readonly_branches
        ))

        editor_buttons = card.xpath(
            ".//button[@name='action_open_fabric_editor' or "
            "@name='action_open_takawe_editor']"
        )
        self.assertEqual(len(editor_buttons), 2)
        self.assertTrue(all(
            button.get('t-if') == '!record.view_only.raw_value'
            for button in editor_buttons
        ))
        uploaders = card.xpath(
            ".//field[@name='image_128' and "
            "@widget='furniture_piece_image_upload']"
        )
        self.assertFalse(uploaders)
        self.assertFalse(card.xpath(".//field[@name='image_128']"))
        order_uploaders = document.xpath(
            "//field[@name='order_image_128' and "
            "@widget='furniture_order_image_upload']"
        )
        order_previews = document.xpath(
            "//field[@name='order_image_128' and "
            "@widget='furniture_order_image_preview']"
        )
        self.assertEqual(len(order_uploaders), 1)
        self.assertEqual(len(order_previews), 1)
        self.assertIn(
            'view_only',
            order_uploaders[0].getparent().get('invisible') or '',
        )
        self.assertIn(
            'not view_only',
            order_previews[0].getparent().get('invisible') or '',
        )
        note_inputs = card.xpath(
            ".//field[@name='piece_note' and "
            "@widget='furniture_piece_note_input']"
        )
        self.assertEqual(len(note_inputs), 1)

        inline_editors = document.xpath(
            "//section[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_tailoring_inline_editor ')]"
        )
        self.assertFalse(inline_editors)

    def test_readonly_image_preview_uses_full_image_without_mutation(self):
        with file_open(
            'furniture_mrp/static/src/js/tailoring_setup_inline_form.js',
            mode='r',
        ) as javascript:
            source = javascript.read()

        start = source.index('export class FurniturePieceImagePreview')
        end = source.index('export class FurnitureOrderImageGallery', start)
        preview_source = source[start:end]

        for fragment in (
            'useFileViewer()',
            't-on-click.stop.prevent="openPreview"',
            '"furniture.mrp.production"',
            '"tailoring_set_image_1920"',
            '"furniture.mrp.production.line"',
            '"batch_image_1920"',
            'this.fileViewer.open({',
        ):
            self.assertIn(fragment, preview_source)
        for forbidden in (
            'useService("orm")',
            'FileUploader',
            'upload_piece_image',
        ):
            self.assertNotIn(forbidden, preview_source)
        self.assertIn(
            '.add("furniture_piece_image_preview", '
            'furniturePieceImagePreviewField)',
            source,
        )
        self.assertIn(
            '.add("furniture_order_image_preview", '
            'furnitureOrderImagePreviewField)',
            source,
        )
        self.assertIn('get_order_image_gallery', source)
        self.assertIn(
            '.add("furniture_piece_note_input", '
            'furniturePieceNoteInputField)',
            source,
        )
