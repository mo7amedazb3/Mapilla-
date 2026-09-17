from datetime import date, datetime
from pathlib import Path
import re

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_resource
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user


class TestAdvanceMaterialRelease(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.storekeeper = new_test_user(
            cls.env,
            login='furniture_advance_material_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
        )
        cls.other_storekeeper = new_test_user(
            cls.env,
            login='furniture_advance_material_other_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
        )
        cls.manager_user = new_test_user(
            cls.env,
            login='furniture_advance_material_manager',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_manager'
            ),
        )
        cls.worker_user = new_test_user(
            cls.env,
            login='furniture_advance_material_worker',
            groups='base.group_user',
        )
        worker_stage = cls.env['furniture.mrp.employee.stage'].search([
            ('code', '=', 'priming'),
        ], limit=1)
        if not worker_stage:
            worker_stage = cls.env['furniture.mrp.employee.stage'].create({
                'name': 'Advance Material Priming',
                'code': 'priming',
            })
        cls.worker = cls.env['hr.employee'].create({
            'name': 'Advance Material Priming Worker',
            'user_id': cls.worker_user.id,
            'furniture_mrp_role': 'worker',
            'furniture_mrp_worker_stage_ids': [(6, 0, worker_stage.ids)],
        })

    def _new_storable_product(self, name):
        return self.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
        })

    def _stock(self, product, quantity):
        self.env['stock.quant']._update_available_quantity(
            product,
            self.env.ref('stock.stock_location_stock'),
            quantity,
        )

    def _location_qty(self, production, location, product):
        return production._stage_location_product_qty(
            location,
            product,
            production.company_id,
        )

    def _handover_location(self):
        return self.env.ref('furniture_mrp.location_material_handover')

    def _receive_stage(self, release, stage_code, quantities=None, note=False):
        stage = release.stage_line_ids.filtered(
            lambda line: line.stage_code == stage_code
        ).ensure_one()
        action = stage.with_user(self.manager_user).action_open_receipt_wizard()
        wizard = self.env[action['res_model']].with_user(
            self.manager_user
        ).browse(action['res_id'])
        quantities = quantities or {}
        for line in wizard.line_ids:
            if line.product_id.id in quantities:
                line.received_qty = quantities[line.product_id.id]
        if note:
            wizard.receipt_note = note
        result = wizard.action_confirm_receipt()
        stage.invalidate_recordset([
            'receipt_state', 'receipt_confirmed', 'received_by_id',
        ])
        return stage, result

    def _create_manual_production(self, shared_stock=10.0, unique_stock=2.0):
        """Two selected stages sharing one raw material.

        Material rows are deliberately separate and linked to one production
        product row.  The release may aggregate its warehouse presentation,
        while the original MRP traceability must remain intact.
        """
        suffix = self.env['ir.sequence'].next_by_code('furniture.mrp.production') or 'test'
        finished = self._new_storable_product(
            'Advance Release Finished %s' % suffix,
        )
        shared = self._new_storable_product(
            'Advance Release Shared Raw %s' % suffix,
        )
        unique = self._new_storable_product(
            'Advance Release Upholstery Raw %s' % suffix,
        )
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'product_qty': 2.0,
            'state': 'confirmed',
            'date_planned_start': datetime(2026, 7, 1, 8, 0),
            'date_planned_finish': datetime(2026, 7, 1, 17, 0),
            'use_priming': True,
            'use_painting': False,
            'use_carpentry': False,
            'use_bases': False,
            'use_finishing': False,
            'use_tailoring': False,
            'use_upholstery': True,
            'use_packaging': False,
        })
        production_line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'production_id': production.id,
            'sequence': 10,
            'product_id': finished.id,
            'product_qty': 2.0,
            'use_priming': True,
            'use_bases': False,
            'use_upholstery': True,
            'stage_selection_initialized': True,
        })
        material_lines = self.env['furniture.mrp.material.line'].create([
            {
                'production_id': production.id,
                'production_line_id': production_line.id,
                'product_id': shared.id,
                'product_uom_id': shared.uom_id.id,
                'qty_needed': 4.0,
                'stage': 'priming',
            },
            {
                'production_id': production.id,
                'production_line_id': production_line.id,
                'product_id': shared.id,
                'product_uom_id': shared.uom_id.id,
                'qty_needed': 6.0,
                'stage': 'upholstery',
            },
            {
                'production_id': production.id,
                'production_line_id': production_line.id,
                'product_id': unique.id,
                'product_uom_id': unique.uom_id.id,
                'qty_needed': 2.0,
                'stage': 'upholstery',
            },
        ])
        production._ensure_stage_locations()
        if shared_stock:
            self._stock(shared, shared_stock)
        if unique_stock:
            self._stock(unique, unique_stock)
        return production, production_line, material_lines, shared, unique

    def _create_release(self, production, stage_codes=('priming', 'upholstery')):
        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_stage_codes(production, stage_codes)
        release.sudo().write({'assigned_to_id': self.storekeeper.id})
        release.activity_ids.sudo().write({'user_id': self.storekeeper.id})
        return release

    def _create_stage_supervisor(self, stage_code):
        stage_codes = (
            (stage_code,)
            if isinstance(stage_code, str)
            else tuple(stage_code)
        )
        supervisor = new_test_user(
            self.env,
            login='advance_receipt_%s_supervisor_%s' % (
                '_'.join(stage_codes), self._testMethodName,
            ),
            groups='base.group_user',
        )
        stages = self.env['furniture.mrp.employee.stage'].search([
            ('code', 'in', list(stage_codes)),
        ])
        self.assertEqual(set(stages.mapped('code')), set(stage_codes))
        self.env['hr.employee'].create({
            'name': 'Advance Receipt %s Supervisor' % ', '.join(stage_codes),
            'user_id': supervisor.id,
            'company_id': self.env.company.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, stages.ids)],
        })
        return supervisor

    def _create_single_store_request_for_report(self, production, product):
        request = self.env['furniture.mrp.store.request'].sudo().create({
            'name': 'STR/PRINT/00001',
            'production_id': production.id,
            'stage_code': 'priming',
            'stage_order_model': 'furniture.mrp.priming',
            'stage_order_res_id': 1,
            'stage_order_name': 'PRM/PRINT/00001',
            'request_kind': 'direct',
            'start_mode': 'direct',
            'requested_by_id': self.manager_user.id,
            'assigned_to_id': self.storekeeper.id,
            'requested_at': datetime(2026, 7, 1, 9, 0),
            'requested_product_summary': 'Printed finished product',
            'source_location_id': self.env.ref(
                'stock.stock_location_stock'
            ).id,
            'handover_location_id': self._handover_location().id,
            'destination_location_id': production.location_priming_wip_id.id,
        })
        self.env['furniture.mrp.store.request.line'].sudo().create({
            'request_id': request.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'requested_qty': 4.0,
            'available_qty': 3.0,
        })
        return request

    def test_storekeeper_print_buttons_and_a4_reports_cover_both_vouchers(self):
        production, _line, _materials, shared, _unique = (
            self._create_manual_production()
        )
        aggregate_release = self._create_release(production)
        single_request = self._create_single_store_request_for_report(
            production, shared,
        )

        report_cases = [
            (
                single_request,
                'furniture_mrp.action_report_furniture_mrp_store_request',
                'إذن استلام خامات مرحلة',
            ),
            (
                aggregate_release,
                'furniture_mrp.action_report_furniture_mrp_advance_material_release',
                'إذن استلام خامات مخزن',
            ),
        ]
        for document, report_xmlid, expected_title in report_cases:
            action = document.with_user(
                self.storekeeper
            ).action_print_warehouse_receipt()
            report = self.env.ref(report_xmlid)
            self.assertEqual(action['type'], 'ir.actions.report')
            self.assertEqual(action['report_name'], report.report_name)
            self.assertEqual(report.report_type, 'qweb-pdf')
            self.assertEqual(report.paperformat_id.format, 'A4')
            self.assertEqual(report.paperformat_id.orientation, 'Portrait')
            if document._name == 'furniture.mrp.advance.material.release':
                self.assertEqual(report.paperformat_id.margin_left, 5)
                self.assertEqual(report.paperformat_id.margin_right, 5)
            else:
                self.assertEqual(report.paperformat_id.margin_left, 9)
                self.assertEqual(report.paperformat_id.margin_right, 9)
            self.assertEqual(
                self.env['ir.actions.report']._get_report_from_name(
                    report.report_name
                ),
                report,
            )

            html = self.env['ir.actions.report']._render_qweb_html(
                report.report_name, document.ids,
            )[0].decode('utf-8')
            self.assertIn(expected_title, html)
            self.assertIn(document.name, html)
            self.assertIn(shared.display_name, html)
            html_document = etree.HTML(html)
            if document._name == 'furniture.mrp.advance.material.release':
                material_tables = html_document.xpath(
                    "//table[contains(concat(' ', normalize-space(@class), ' '), ' wr-compact-lines ')]"
                )
                self.assertEqual(len(material_tables), 1)
                self.assertFalse(html_document.xpath(
                    "//table[contains(concat(' ', normalize-space(@class), ' '), ' wr-lines ')]"
                ))
                self.assertFalse(html_document.xpath(
                    "//div[contains(concat(' ', normalize-space(@class), ' '), ' wr-stage ')]"
                ))
                self.assertNotIn('المتاح', html)
                self.assertNotIn('العجز', html)
                material_rows = material_tables[0].xpath('./tbody/tr')
                self.assertEqual(
                    len(material_rows),
                    len(document.stage_line_ids.mapped('material_line_ids')),
                )
                material_names = material_tables[0].xpath(
                    ".//span[contains(concat(' ', normalize-space(@class), ' '), ' material-name ')]"
                )
                self.assertEqual(len(material_names), len(material_rows))
                self.assertTrue(all(
                    material_name.get('dir') == 'auto'
                    for material_name in material_names
                ))
            else:
                material_tables = html_document.xpath(
                    "//table[contains(concat(' ', normalize-space(@class), ' '), ' wr-lines ')]"
                )
                self.assertEqual(len(material_tables), 1)

            pdf, output_type = self.env[
                'ir.actions.report'
            ].with_context(force_report_rendering=True)._render_qweb_pdf(
                report.report_name, res_ids=document.ids,
            )
            self.assertEqual(output_type, 'pdf')
            self.assertTrue(pdf.startswith(b'%PDF'))
            self.assertGreater(len(pdf), 5000)

        for view_xmlid in [
            'furniture_mrp.view_furniture_mrp_store_request_form',
            'furniture_mrp.view_furniture_mrp_advance_material_release_form',
        ]:
            view = self.env.ref(view_xmlid)
            arch = self.env[view.model].with_user(self.storekeeper).get_view(
                view_id=view.id,
                view_type='form',
            )['arch']
            document = etree.fromstring(arch.encode())
            buttons = document.xpath(
                "//header/button[@name='action_print_warehouse_receipt']"
            )
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0].get('string'), 'Print')
            self.assertEqual(buttons[0].get('icon'), 'fa-print')

    def test_storekeeper_incoming_picking_has_header_print_and_a4_pdf(self):
        product = self._new_storable_product('Incoming receipt print material')
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        self.assertTrue(picking_type)

        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        self.env['stock.move'].sudo().create({
            'name': product.display_name,
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': 7.0,
            'product_uom': product.uom_id.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
        })

        action = picking.with_user(
            self.storekeeper
        ).action_print_warehouse_receipt()
        report = self.env.ref(
            'furniture_mrp.action_report_furniture_stock_receipt'
        )
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(
            action['report_name'],
            'furniture_mrp.report_furniture_stock_receipt',
        )
        self.assertEqual(report.paperformat_id.format, 'A4')
        self.assertEqual(report.paperformat_id.orientation, 'Portrait')

        pdf, output_type = self.env[
            'ir.actions.report'
        ].with_context(force_report_rendering=True)._render_qweb_pdf(
            report.id, res_ids=picking.ids,
        )
        self.assertEqual(output_type, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 5000)

        view = self.env.ref('stock.view_picking_form')
        arch = self.env['stock.picking'].with_user(
            self.storekeeper
        ).get_view(view_id=view.id, view_type='form')['arch']
        document = etree.fromstring(arch.encode())
        buttons = document.xpath(
            "//header/button[@name='action_print_warehouse_receipt']"
        )
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get('string'), 'Print')
        self.assertIn(
            "picking_type_code != 'incoming'",
            buttons[0].get('invisible', ''),
        )

    def _create_batch_wizard(self, productions):
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        return Wizard.create({
            'period_start': date(2000, 1, 1),
            'period_end': date(2100, 12, 31),
            'production_ids': [(6, 0, productions.ids)],
            'order_line_ids': Wizard._order_commands(productions),
        })

    def _create_issued_batch_release(self, productions, stage_code='priming'):
        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_production_stage_map(
            productions,
            {
                production.id: {stage_code}
                for production in productions
            },
            notify_storekeeper=False,
            is_batch_request=True,
        )
        release.sudo().write({'assigned_to_id': self.storekeeper.id})
        release.sudo().action_issue()
        return release

    def test_batch_receipt_opens_exact_issued_orders_stages_and_materials(self):
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.receipt.wizard'
        ]
        pending_before = Wizard._pending_batch_receipt_stages()
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_issued_batch_release(first | second)

        action = self.env[
            'furniture.mrp.production'
        ].with_user(
            self.manager_user
        ).action_open_batch_advance_material_receipt_wizard()
        wizard = self.env[action['res_model']].with_user(
            self.manager_user
        ).browse(action['res_id'])

        expected_stages = release.stage_line_ids.sorted(
            lambda stage: (stage.production_id.id, stage.sequence, stage.id)
        )
        expected_all_stages = pending_before | expected_stages
        self.assertFalse(release.production_id)
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.advance.material.batch.receipt.wizard',
        )
        self.assertEqual(action['target'], 'new')
        self.assertEqual(
            set(wizard.release_stage_ids.ids),
            set(expected_all_stages.ids),
        )
        self.assertGreaterEqual(wizard.production_count, 2)
        self.assertEqual(wizard.stage_count, len(expected_all_stages))
        self.assertEqual(
            set(wizard.line_ids.mapped('release_line_id').ids),
            set(expected_all_stages.mapped('material_line_ids').ids),
        )
        self.assertTrue(all(
            abs(line.received_qty - line.issued_qty) < 0.001
            for line in wizard.line_ids
        ))

    def test_single_order_from_batch_launcher_stays_in_batch_receipt(self):
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.receipt.wizard'
        ]
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_issued_batch_release(production)

        self.assertFalse(release.production_id)
        self.assertEqual(release.production_ids, production)
        self.assertEqual(
            Wizard._pending_batch_receipt_stages() & release.stage_line_ids,
            release.stage_line_ids,
        )

    def test_batch_receipt_covers_all_eight_stages_without_new_store_requests(self):
        production, production_line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        stage_codes = (
            'priming', 'painting', 'carpentry', 'bases', 'finishing',
            'tailoring', 'upholstery', 'packaging',
        )
        route_values = {
            'use_%s' % stage_code: True
            for stage_code in stage_codes
        }
        safe_context = {
            'furniture_skip_material_refresh': True,
            'furniture_skip_stage_plan_sync': True,
        }
        production.with_context(**safe_context).write(route_values)
        production_line.with_context(**safe_context).write(route_values)

        release = self._create_release(production, stage_codes)
        release.sudo().action_issue()
        stages = release.stage_line_ids.sorted('sequence')
        self.assertEqual(stages.mapped('stage_code'), list(stage_codes))

        Wizard = self.env[
            'furniture.mrp.advance.material.batch.receipt.wizard'
        ].with_user(self.manager_user)
        wizard = Wizard.create({
            'company_id': production.company_id.id,
            'release_stage_ids': [(6, 0, stages.ids)],
            'line_ids': Wizard._line_commands(stages),
        })
        wizard.action_confirm_receipts()
        stages.invalidate_recordset([
            'receipt_confirmed', 'receipt_state',
        ])
        self.assertTrue(all(stages.mapped('receipt_confirmed')))
        self.assertEqual(set(stages.mapped('receipt_state')), {'full'})

        stage_models = {
            'priming': 'furniture.mrp.priming',
            'painting': 'furniture.mrp.painting',
            'carpentry': 'furniture.mrp.carpentry',
            'bases': 'furniture.mrp.bases',
            'finishing': 'furniture.mrp.finishing',
            'tailoring': 'furniture.mrp.tailoring',
            'upholstery': 'furniture.mrp.upholstery',
            'packaging': 'furniture.mrp.packaging',
        }
        for sequence, stage_code in enumerate(stage_codes, start=1):
            stage_order = self.env[stage_models[stage_code]].create({
                'name': 'ALL-STAGES/%02d' % sequence,
                'production_order_id': production.id,
                'state': 'pending',
            })
            stage_order.invalidate_recordset([
                'store_request_id', 'store_request_state',
                'advance_material_release_stage_id',
                'advance_material_receipt_confirmed',
            ])
            self.assertFalse(stage_order.store_request_id, stage_code)
            self.assertEqual(
                stage_order.store_request_state, 'approved', stage_code,
            )
            self.assertTrue(
                stage_order.advance_material_receipt_confirmed, stage_code,
            )
            action = stage_order.action_request_store_approval()
            self.assertEqual(action['res_model'], release._name, stage_code)
            self.assertEqual(action['res_id'], release.id, stage_code)

        self.assertFalse(self.env['furniture.mrp.store.request'].search([
            ('production_id', '=', production.id),
        ]))

    def test_legacy_full_route_release_safely_backfills_bases(self):
        production, production_line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(
            production, ('priming', 'upholstery'),
        )
        release.sudo().action_issue()
        self._receive_stage(release, 'priming')
        self._receive_stage(release, 'upholstery')

        safe_context = {
            'furniture_skip_material_refresh': True,
            'furniture_skip_stage_plan_sync': True,
        }
        late_route_values = {'use_bases': True}
        production.with_context(**safe_context).write(late_route_values)
        production_line.with_context(**safe_context).write(late_route_values)

        repaired = self.env[
            'furniture.mrp.advance.material.release'
        ]._repair_legacy_added_stage_coverage(production)
        self.assertEqual(
            set(repaired.mapped('stage_code')), {'bases'},
        )
        self.assertEqual(repaired.mapped('release_id'), release)
        self.assertEqual(set(repaired.mapped('state')), {'issued'})
        self.assertTrue(all(repaired.mapped('receipt_confirmed')))
        self.assertEqual(set(repaired.mapped('receipt_state')), {'full'})
        self.assertFalse(repaired.mapped('material_line_ids'))

        for stage_code, model_name in (
            ('bases', 'furniture.mrp.bases'),
        ):
            stage_order = self.env[model_name].create({
                'name': 'LEGACY-%s' % stage_code.upper(),
                'production_order_id': production.id,
                'state': 'pending',
            })
            stage_order.invalidate_recordset([
                'store_request_id', 'store_request_state',
                'advance_material_release_stage_id',
                'advance_material_receipt_confirmed',
            ])
            self.assertEqual(stage_order.store_request_state, 'approved')
            self.assertTrue(stage_order.advance_material_receipt_confirmed)
            action = stage_order.action_request_store_approval()
            self.assertEqual(action['res_model'], release._name)
            self.assertEqual(action['res_id'], release.id)

        self.assertFalse(self.env['furniture.mrp.store.request'].search([
            ('production_id', '=', production.id),
        ]))

    def test_batch_receipt_confirms_full_and_partial_atomically_without_hall_move(self):
        first, _line, _materials, first_raw, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, second_raw, _unique = (
            self._create_manual_production()
        )
        release = self._create_issued_batch_release(first | second)
        action = self.env[
            'furniture.mrp.production'
        ].with_user(
            self.manager_user
        ).action_open_batch_advance_material_receipt_wizard()
        wizard = self.env[action['res_model']].with_user(
            self.manager_user
        ).browse(action['res_id'])

        first_wizard_line = wizard.line_ids.filtered(
            lambda line: line.production_id == first
        ).ensure_one()
        first_wizard_line.received_qty = first_wizard_line.issued_qty - 1.0
        wizard.receipt_note = 'فرق فعلي في تسليم أول أمر'
        first_hall_before = self._location_qty(
            first, first.location_priming_wip_id, first_raw,
        )
        second_hall_before = self._location_qty(
            second, second.location_priming_wip_id, second_raw,
        )

        result = wizard.action_confirm_receipts()
        stages = release.stage_line_ids.sorted(
            lambda stage: stage.production_id.id
        )
        stages.invalidate_recordset([
            'receipt_confirmed', 'receipt_state', 'received_by_id',
            'receipt_note',
        ])

        self.assertEqual(result['type'], 'ir.actions.client')
        self.assertEqual(stages.mapped('receipt_state'), ['partial', 'full'])
        self.assertTrue(all(stages.mapped('receipt_confirmed')))
        self.assertEqual(stages.mapped('received_by_id'), self.manager_user)
        self.assertTrue(all(
            stage.receipt_note == wizard.receipt_note
            for stage in stages
        ))
        self.assertAlmostEqual(
            self._location_qty(
                first, first.location_priming_wip_id, first_raw,
            ),
            first_hall_before,
        )
        self.assertAlmostEqual(
            self._location_qty(
                second, second.location_priming_wip_id, second_raw,
            ),
            second_hall_before,
        )
        self.assertFalse(stages.mapped('material_line_ids.receipt_move_ids'))

    def test_batch_receipt_includes_single_order_releases_and_storekeeper(self):
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.receipt.wizard'
        ]
        pending_before = Wizard._pending_batch_receipt_stages()
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        single_release = self._create_release(first, ('priming',))
        single_release.sudo().action_issue()

        pending_after = Wizard._pending_batch_receipt_stages()
        self.assertEqual(
            set(pending_after.ids),
            set((pending_before | single_release.stage_line_ids).ids),
        )
        self.assertEqual(
            pending_after & single_release.stage_line_ids,
            single_release.stage_line_ids,
        )
        self.assertEqual(single_release.production_id, first)
        self.assertEqual(single_release.stage_line_ids.receipt_state, 'waiting')

        action = self.env[
            'furniture.mrp.production'
        ].with_user(
            self.manager_user
        ).action_open_batch_advance_material_receipt_wizard()
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(
            wizard.release_stage_ids & single_release.stage_line_ids,
            single_release.stage_line_ids,
        )
        with self.assertRaises(AccessError):
            self.env[
                'furniture.mrp.production'
            ].with_user(
                self.storekeeper
            ).action_open_batch_advance_material_receipt_wizard()

    def _issue_moves(self, production):
        return self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('state', '=', 'done'),
            ('location_id', '=', self.env.ref('stock.stock_location_stock').id),
        ])

    def _create_recipe_production(
        self, quantity=4.0, include_confirm_result=False,
    ):
        model = self.env['furniture.product.model'].create({
            'name': 'Advance Release Recipe Model',
        })
        finished = self._new_storable_product('Advance Release Recipe Finished')
        raw = self._new_storable_product('Advance Release Recipe Raw')
        self.env['mrp.bom'].create({
            'product_tmpl_id': finished.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': finished.id,
            'furniture_recipe_model_id': model.id,
            'use_priming': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': raw.id,
                'product_qty': 1.0,
                'product_uom_id': raw.uom_id.id,
                'quantity_mode': 'scaled',
            })],
        })
        production = self.env['furniture.mrp.production'].create({
            'product_qty': quantity,
            'date_planned_start': '2026-08-22 08:00:00',
            'date_planned_finish': '2026-08-22 17:00:00',
            # This fixture isolates raw-material release/receipt mechanics.
            # Use the model's explicit test-only duration so an unrelated MPS
            # approval state cannot mask the stock-flow assertions below.
            'temporary_stage_fixed_hours': 2.0,
        })
        production_line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': finished.id,
            'furniture_order_model_id': model.id,
            'product_qty': quantity,
        })
        confirm_result = production.action_confirm()
        self._stock(raw, quantity)
        if include_confirm_result:
            return production, production_line, raw, confirm_result
        return production, production_line, raw

    def test_confirm_does_not_open_advance_material_wizard(self):
        production, _line, _raw, confirm_result = (
            self._create_recipe_production(
                2.0,
                include_confirm_result=True,
            )
        )

        self.assertEqual(production.state, 'confirmed')
        self.assertEqual(
            confirm_result,
            {'type': 'ir.actions.client', 'tag': 'reload'},
        )

    def test_storekeeper_cannot_open_production_or_dashboard(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production).with_user(self.storekeeper)

        with self.assertRaises(AccessError):
            release.action_open_production_order()
        with self.assertRaises(AccessError):
            release.action_open_mrp_dashboard()

        manager_release = release.with_user(self.manager_user)
        production_action = manager_release.action_open_production_order()
        self.assertEqual(production_action['type'], 'ir.actions.act_window')
        self.assertEqual(production_action['res_model'], production._name)
        self.assertEqual(production_action['res_id'], production.id)
        self.assertEqual(production_action['view_mode'], 'form')
        self.assertEqual(production_action['views'], [(False, 'form')])
        self.assertEqual(production_action['target'], 'main')

        dashboard_action = manager_release.action_open_mrp_dashboard()
        expected_dashboard = self.env.ref(
            'furniture_mrp.action_furniture_mrp_dashboard'
        )
        self.assertEqual(dashboard_action['type'], 'ir.actions.act_window')
        self.assertEqual(dashboard_action['id'], expected_dashboard.id)
        self.assertEqual(dashboard_action['res_model'], production._name)
        self.assertEqual(dashboard_action['view_mode'], 'kanban,list,form')
        self.assertEqual(dashboard_action['target'], 'main')

    def test_navigation_buttons_follow_storekeeper_scope(self):
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_release_form'
        )
        arch = self.env[view.model].with_user(self.storekeeper).get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        document = etree.fromstring(arch.encode())

        production_buttons = document.xpath(
            "//header/button[@name='action_open_production_order']"
        )
        dashboard_buttons = document.xpath(
            "//header/button[@name='action_open_mrp_dashboard']"
        )
        self.assertFalse(production_buttons)
        self.assertFalse(dashboard_buttons)

        manager_arch = self.env[view.model].with_user(
            self.manager_user
        ).get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        manager_document = etree.fromstring(manager_arch.encode())
        manager_production_buttons = manager_document.xpath(
            "//header/button[@name='action_open_production_order']"
        )
        manager_dashboard_buttons = manager_document.xpath(
            "//header/button[@name='action_open_mrp_dashboard']"
        )
        self.assertEqual(len(manager_production_buttons), 1)
        self.assertEqual(manager_production_buttons[0].get('type'), 'object')
        self.assertEqual(len(manager_dashboard_buttons), 1)
        self.assertEqual(manager_dashboard_buttons[0].get('type'), 'object')

    def test_wizard_selected_stages_create_one_master_release(self):
        production, _line, material_lines, _shared, _unique = (
            self._create_manual_production()
        )
        Wizard = self.env[
            'furniture.mrp.advance.material.release.wizard'
        ].with_context(default_production_id=production.id)
        defaults = Wizard.default_get(['production_id', 'line_ids'])
        wizard = Wizard.create(defaults)

        self.assertEqual(wizard.production_id, production)
        self.assertEqual(
            set(wizard.line_ids.mapped('stage_code')),
            {'priming', 'upholstery'},
        )
        wizard.line_ids.write({'selected': True})
        action = wizard.action_send_request()
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertFalse(action['params']['sticky'])
        self.assertIn('تم إرسال إذن المراحل', action['params']['message'])
        self.assertIn('لن تبدأ أي مرحلة', action['params']['message'])
        next_action = action['params']['next']
        self.assertEqual(next_action['res_model'], 'furniture.mrp.advance.material.release')
        self.assertEqual(next_action['view_mode'], 'form')
        self.assertEqual(
            next_action['views'],
            [(
                self.env.ref(
                    'furniture_mrp.view_furniture_mrp_advance_material_release_form'
                ).id,
                'form',
            )],
        )
        release = self.env[next_action['res_model']].browse(next_action['res_id'])
        self.assertIn(release.name, action['params']['message'])

        self.assertEqual(release.production_id, production)
        self.assertEqual(release.production_ids, production)
        self.assertEqual(release.requested_by_id, self.env.user)
        self.assertEqual(release.state, 'pending')
        self.assertEqual(len(release.stage_line_ids), 2)
        self.assertEqual(
            set(release.stage_line_ids.mapped('stage_code')),
            {'priming', 'upholstery'},
        )
        self.assertEqual(
            release.stage_line_ids.mapped('production_id'),
            production,
        )
        self.assertEqual(
            release.stage_line_ids.mapped('source_material_line_ids'),
            material_lines,
        )
        production.invalidate_recordset([
            'has_uncovered_advance_material_stages',
        ])
        self.assertFalse(production.has_uncovered_advance_material_stages)
        self.assertFalse(self._issue_moves(production))
        with self.assertRaises(UserError):
            self._create_release(production, ('priming',))

    def test_wizard_bulk_selection_selects_only_available_stages(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        self._create_release(production, ('priming',))
        release_count = self.env[
            'furniture.mrp.advance.material.release'
        ].search_count([])
        Wizard = self.env[
            'furniture.mrp.advance.material.release.wizard'
        ].with_context(default_production_id=production.id)
        wizard = Wizard.create(
            Wizard.default_get(['production_id', 'line_ids'])
        )
        blocked_line = wizard.line_ids.filtered(
            lambda line: not line.selectable
        ).ensure_one()
        available_line = wizard.line_ids.filtered('selectable').ensure_one()
        blocked_line.write({'selected': True})

        action = wizard.action_select_all_stages()
        wizard.invalidate_recordset([
            'line_ids', 'selected_count', 'selectable_count',
        ])
        self.assertTrue(available_line.selected)
        self.assertFalse(blocked_line.selected)
        self.assertEqual(wizard.selected_count, 1)
        self.assertEqual(wizard.selectable_count, 1)
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], wizard._name)
        self.assertEqual(action['res_id'], wizard.id)
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['view_mode'], 'form')
        self.assertEqual(
            action['view_id'],
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_advance_material_wizard_form'
            ).id,
        )
        self.assertEqual(
            action['context']['default_production_id'], production.id,
        )
        self.assertEqual(
            self.env['furniture.mrp.advance.material.release'].search_count([]),
            release_count,
        )

        repeated_action = wizard.action_select_all_stages()
        self.assertEqual(repeated_action['res_id'], wizard.id)
        self.assertEqual(wizard.selected_count, 1)

        clear_action = wizard.action_clear_stage_selection()
        wizard.invalidate_recordset([
            'line_ids', 'selected_count', 'selectable_count',
        ])
        self.assertFalse(wizard.line_ids.filtered('selected'))
        self.assertEqual(wizard.selected_count, 0)
        self.assertEqual(clear_action['res_id'], wizard.id)

    def test_wizard_material_items_are_structured_for_stage_cards(self):
        production, _line, material_lines, shared, unique = (
            self._create_manual_production(
                shared_stock=0.0,
                unique_stock=0.0,
            )
        )
        Wizard = self.env[
            'furniture.mrp.advance.material.release.wizard'
        ].with_context(default_production_id=production.id)
        wizard = Wizard.create(
            Wizard.default_get(['production_id', 'line_ids'])
        )
        upholstery = wizard.line_ids.filtered(
            lambda line: line.stage_code == 'upholstery'
        )
        self.assertEqual(len(upholstery), 1)

        required_by_product = {
            item['product_id']: item
            for item in upholstery.material_items
        }
        available_by_product = {
            item['product_id']: item
            for item in upholstery.available_items
        }
        shortage_by_product = {
            item['product_id']: item
            for item in upholstery.shortage_items
        }
        self.assertEqual(
            set(required_by_product),
            set(material_lines.filtered(
                lambda line: line.stage == 'upholstery'
            ).mapped('product_id').ids),
        )
        self.assertEqual(set(required_by_product), {shared.id, unique.id})
        self.assertEqual(set(available_by_product), {shared.id, unique.id})
        self.assertEqual(set(shortage_by_product), {shared.id, unique.id})
        self.assertEqual(
            required_by_product[shared.id]['quantity'],
            production._format_dimension_value(6.0),
        )
        self.assertEqual(
            required_by_product[unique.id]['quantity'],
            production._format_dimension_value(2.0),
        )
        self.assertEqual(
            shortage_by_product[shared.id]['quantity'],
            required_by_product[shared.id]['quantity'],
        )
        self.assertEqual(
            shortage_by_product[unique.id]['quantity'],
            required_by_product[unique.id]['quantity'],
        )
        for item in upholstery.material_items:
            self.assertEqual(
                set(item),
                {'key', 'product_id', 'name', 'quantity', 'uom'},
            )
            self.assertTrue(item['key'])
            self.assertTrue(item['name'])
            self.assertTrue(item['uom'])

    def test_single_production_material_wizard_uses_scoped_stage_cards(self):
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_wizard_form'
        )
        view._check_xml()
        document = etree.fromstring(view.arch_db.encode())

        self.assertIn(
            'o_furniture_advance_material_wizard',
            document.get('class', ''),
        )
        line_fields = document.xpath(
            "./sheet//field[@name='line_ids']"
        )
        self.assertEqual(len(line_fields), 1)
        line_field = line_fields[0]
        self.assertIn(
            'o_furniture_advance_stage_cards',
            line_field.get('class', ''),
        )
        self.assertEqual(line_field.get('mode'), 'kanban')
        self.assertFalse(line_field.xpath('./list'))

        kanbans = line_field.xpath('./kanban')
        self.assertEqual(len(kanbans), 1)
        kanban = kanbans[0]
        self.assertEqual(kanban.get('create'), '0')
        self.assertEqual(kanban.get('delete'), '0')
        self.assertEqual(kanban.get('can_open'), '0')
        self.assertEqual(kanban.get('records_draggable'), '0')
        for field_name in (
            'selected', 'selectable', 'stage_code', 'material_count',
            'material_summary', 'available_summary', 'shortage_summary',
            'material_items', 'available_items', 'shortage_items',
            'has_shortage', 'active_release_name', 'note',
        ):
            fields = kanban.xpath("./field[@name='%s']" % field_name)
            self.assertEqual(len(fields), 1, field_name)
            self.assertEqual(fields[0].get('force_save'), '1', field_name)

        cards = kanban.xpath(
            ".//t[@t-name='card']"
            "//article[contains(@class, 'o_furniture_advance_stage_card')]"
        )
        toggles = kanban.xpath(
            ".//t[@t-name='card']"
            "//field[@name='selected' and @widget='boolean_toggle']"
        )
        self.assertEqual(len(cards), 1)
        self.assertEqual(
            cards[0].get('t-att-data-stage-code'),
            'record.stage_code.raw_value',
        )
        card_state_classes = cards[0].get('t-att-class', '')
        for state_expression in (
            "'is-selected': record.selected.raw_value",
            "'is-empty': !record.material_count.raw_value",
            "'has-shortage': record.has_shortage.raw_value",
            "'is-blocked': !record.selectable.raw_value",
        ):
            self.assertIn(state_expression, card_state_classes)
        self.assertIn(
            "'is-empty': !record.material_count.raw_value",
            card_state_classes,
        )
        self.assertEqual(len(toggles), 1)
        self.assertEqual(toggles[0].get('force_save'), '1')
        self.assertIn("'autosave': False", toggles[0].get('options', ''))

        # ``header`` is a reserved structural node in Odoo's KanbanArchParser.
        # Using it inside the card makes the parser skip all nested fields, so
        # the Owl compiler later receives field ids missing from fieldNodes.
        card_headers = kanban.xpath(
            ".//t[@t-name='card']"
            "//div[contains(@class, 'o_furniture_advance_stage_header')]"
        )
        self.assertEqual(len(card_headers), 1)
        self.assertFalse(kanban.xpath(".//t[@t-name='card']//header"))

        item_sources = {
            node.get('t-foreach')
            for node in kanban.xpath(
                ".//t[@t-name='card']//t[@t-foreach]"
            )
        }
        self.assertEqual(item_sources, {
            'record.material_items.raw_value',
            'record.available_items.raw_value',
            'record.shortage_items.raw_value',
        })
        self.assertEqual(len(kanban.xpath(
            ".//t[@t-name='card']"
            "//*[contains(@class, 'o_furniture_advance_material_chip')]"
        )), 3)

        production_fields = document.xpath(
            "./sheet//field[@name='production_id']"
        )
        selected_counts = document.xpath(
            "./sheet//field[@name='selected_count']"
        )
        selectable_counts = document.xpath(
            "./sheet//field[@name='selectable_count']"
        )
        self.assertEqual(len(production_fields), 1)
        self.assertEqual(production_fields[0].get('readonly'), '1')
        self.assertEqual(len(selected_counts), 1)
        self.assertEqual(len(selectable_counts), 1)
        self.assertEqual(selectable_counts[0].get('invisible'), '1')

        select_all_buttons = document.xpath(
            "./sheet//button[@name='action_select_all_stages']"
        )
        clear_all_buttons = document.xpath(
            "./sheet//button[@name='action_clear_stage_selection']"
        )
        self.assertEqual(len(select_all_buttons), 1)
        self.assertEqual(select_all_buttons[0].get('type'), 'object')
        self.assertEqual(
            select_all_buttons[0].get('string'), 'تحديد كل المراحل',
        )
        self.assertEqual(
            select_all_buttons[0].get('invisible'),
            'selectable_count == 0',
        )
        self.assertIsNone(select_all_buttons[0].get('special'))
        self.assertEqual(len(clear_all_buttons), 1)
        self.assertEqual(clear_all_buttons[0].get('type'), 'object')
        self.assertEqual(clear_all_buttons[0].get('string'), 'إلغاء التحديد')
        self.assertEqual(
            clear_all_buttons[0].get('invisible'), 'selected_count == 0',
        )
        self.assertFalse(kanban.xpath(
            ".//button[@name='action_select_all_stages' or "
            "@name='action_clear_stage_selection']"
        ))

        send_buttons = document.xpath(
            "./footer/button[@name='action_send_request']"
        )
        skip_buttons = document.xpath(
            "./footer/button[@name='action_skip']"
        )
        cancel_buttons = document.xpath(
            "./footer/button[@special='cancel']"
        )
        self.assertEqual(len(send_buttons), 1)
        self.assertEqual(send_buttons[0].get('type'), 'object')
        self.assertEqual(len(skip_buttons), 1)
        self.assertEqual(skip_buttons[0].get('type'), 'object')
        self.assertTrue(skip_buttons[0].get('confirm'))
        self.assertEqual(len(cancel_buttons), 1)

    def test_single_production_material_cards_use_clean_stage_rows(self):
        css_path = get_module_resource(
            'furniture_mrp',
            'static',
            'src',
            'css',
            'furniture_mrp.css',
        )
        css = Path(css_path).read_text(encoding='utf-8')

        modal_renderer_rule = re.search(
            r'\.modal-content:has\('
            r'\.o_furniture_advance_material_wizard\)\s+'
            r'\.o_form_renderer\.o_furniture_advance_material_wizard\s*'
            r'\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(modal_renderer_rule)
        modal_renderer_declarations = modal_renderer_rule.group(1)
        self.assertIn(
            'height: auto !important;',
            modal_renderer_declarations,
        )
        self.assertNotIn('height: 100%', modal_renderer_declarations)
        self.assertNotIn('min-height:', modal_renderer_declarations)
        self.assertNotIn('overflow:', modal_renderer_declarations)

        modal_body_rule = re.search(
            r'\.modal-content:has\('
            r'\.o_furniture_advance_material_wizard\)\s+'
            r'\.modal-body\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(modal_body_rule)
        modal_body_declarations = modal_body_rule.group(1)
        self.assertIn('min-height: 0;', modal_body_declarations)
        self.assertIn('flex: 0 1 auto;', modal_body_declarations)
        self.assertIn('overflow-y: auto;', modal_body_declarations)
        self.assertNotIn('height: 100%', modal_body_declarations)
        self.assertNotIn('overflow: visible;', modal_body_declarations)

        modal_container_rule = re.search(
            r'\.modal-content:has\('
            r'\.o_furniture_advance_material_wizard\)\s+'
            r'\.o_form_view_container\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(modal_container_rule)
        modal_container_declarations = modal_container_rule.group(1)
        self.assertIn(
            'height: auto !important;',
            modal_container_declarations,
        )
        self.assertIn(
            'min-height: 0 !important;',
            modal_container_declarations,
        )
        self.assertIn('flex: 0 1 auto;', modal_container_declarations)
        self.assertNotIn('height: 100%', modal_container_declarations)
        self.assertNotIn('flex: 1 1 auto;', modal_container_declarations)

        renderer_rule = re.search(
            r'\.o_furniture_advance_stage_cards\s+'
            r'\.o_kanban_renderer\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(renderer_rule)
        renderer_declarations = renderer_rule.group(1)
        self.assertIn('display: grid !important;', renderer_declarations)
        self.assertIn(
            'grid-template-columns: minmax(0, 1fr);',
            renderer_declarations,
        )
        self.assertIn('grid-auto-rows: 1fr;', renderer_declarations)
        self.assertIn('min-height: 0 !important;', renderer_declarations)
        self.assertIn('align-items: stretch;', renderer_declarations)
        self.assertIn('align-content: start;', renderer_declarations)
        self.assertIn('max-height: none;', renderer_declarations)
        self.assertIn('overflow: visible;', renderer_declarations)
        self.assertNotIn('display: flex !important;', renderer_declarations)
        self.assertNotIn('flex-direction:', renderer_declarations)
        self.assertNotIn('overflow-y: auto;', renderer_declarations)
        self.assertNotIn('repeat(2', renderer_declarations)

        record_rule = re.search(
            r'\.o_furniture_advance_stage_cards\s+'
            r'\.o_kanban_record\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(record_rule)
        record_declarations = record_rule.group(1)
        self.assertIn('display: flex !important;', record_declarations)
        self.assertIn('height: 100% !important;', record_declarations)
        self.assertIn('align-self: stretch !important;', record_declarations)

        card_rule = re.search(
            r'\.o_furniture_advance_stage_card\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(card_rule)
        card_declarations = card_rule.group(1)
        self.assertIn('display: grid;', card_declarations)
        self.assertIn('height: 100%;', card_declarations)
        self.assertNotIn('max-height:', card_declarations)
        self.assertIn(
            'grid-template-columns: minmax(0, 1fr) 220px;',
            card_declarations,
        )
        self.assertIn(
            'grid-template-rows: 1fr auto auto;',
            card_declarations,
        )
        self.assertIn('"required rail"', card_declarations)
        self.assertIn('"shortage shortage"', card_declarations)
        self.assertIn('"blocked blocked"', card_declarations)
        self.assertIn('--stage-accent: #718f8b;', card_declarations)
        self.assertIn('--stage-rail: #f7faf9;', card_declarations)
        self.assertNotIn('--stage-surface:', card_declarations)
        self.assertIn('--stage-badge: #eaf3f1;', card_declarations)
        self.assertIn('--stage-ink: #315f59;', card_declarations)
        self.assertIn(
            'border-inline-start: 3px solid var(--stage-accent);',
            card_declarations,
        )
        self.assertNotIn('border-block-start:', card_declarations)
        self.assertIn('background: #fff;', card_declarations)

        palette_rules = re.findall(
            r'\.o_furniture_advance_material_wizard\s+'
            r'\.o_furniture_advance_stage_card'
            r'\[data-stage-code="([a-z0-9_-]+)"\]\s*\{([^}]*)\}',
            css,
        )
        expected_stage_codes = {
            'priming', 'painting', 'carpentry', 'bases', 'finishing',
            'tailoring', 'upholstery', 'packaging',
        }
        self.assertEqual(
            {stage_code for stage_code, _declarations in palette_rules},
            expected_stage_codes,
        )
        self.assertEqual(len(palette_rules), len(expected_stage_codes))
        accents = set()
        for stage_code, declarations in palette_rules:
            for variable_name in (
                '--stage-accent', '--stage-rail',
                '--stage-badge', '--stage-ink',
            ):
                self.assertIn(variable_name + ':', declarations, stage_code)
            accent = re.search(
                r'--stage-accent:\s*(#[0-9a-f]{6});', declarations,
            )
            self.assertIsNotNone(accent, stage_code)
            accents.add(accent.group(1))
        self.assertEqual(len(accents), len(expected_stage_codes))
        self.assertNotIn('data-stage-code="sewing"', css)
        self.assertNotRegex(
            css,
            r'\.o_furniture_batch_release_wizard[^\{]*'
            r'data-stage-code=',
        )

        heading_rule = re.search(
            r'\.o_furniture_advance_section_heading\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(heading_rule)
        heading_declarations = heading_rule.group(1)
        self.assertIn('position: sticky;', heading_declarations)
        self.assertIn('top: 0;', heading_declarations)
        self.assertIn('z-index: 3;', heading_declarations)

        stage_header_rule = re.search(
            r'\.o_furniture_advance_stage_header\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(stage_header_rule)
        stage_header_declarations = stage_header_rule.group(1)
        self.assertIn('flex-direction: column;', stage_header_declarations)
        self.assertIn('min-height: 72px;', stage_header_declarations)
        self.assertIn(
            'border-inline-end: 1px solid #e2e8e7;',
            stage_header_declarations,
        )
        self.assertIn(
            'background: var(--stage-rail);',
            stage_header_declarations,
        )

        stage_badge_rule = re.search(
            r'\.o_furniture_advance_stage_identity\s+'
            r'\.badge\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(stage_badge_rule)
        stage_badge_declarations = stage_badge_rule.group(1)
        self.assertIn(
            'font-size: 0.9rem !important;',
            stage_badge_declarations,
        )
        self.assertIn(
            'font-weight: 900 !important;',
            stage_badge_declarations,
        )
        self.assertNotIn('font-size: 0.84rem', stage_badge_declarations)

        material_rule = re.search(
            r'\.o_furniture_advance_material_wizard\s+'
            r'\.o_furniture_advance_material_items\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(material_rule)
        material_declarations = material_rule.group(1)
        self.assertIn(
            'grid-template-columns: repeat(4, minmax(0, 1fr));',
            material_declarations,
        )
        self.assertIn('max-height: none;', material_declarations)
        self.assertIn('overflow: visible;', material_declarations)
        self.assertNotIn('overflow-y: auto;', material_declarations)

        material_chip_rule = re.search(
            r'\.o_furniture_advance_material_wizard\s+'
            r'\.o_furniture_advance_material_chip\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(material_chip_rule)
        material_chip_declarations = material_chip_rule.group(1)
        self.assertIn(
            'border: 1px solid #e1e8e7;',
            material_chip_declarations,
        )
        self.assertIn('border-radius: 7px;', material_chip_declarations)
        self.assertIn('background: #fff;', material_chip_declarations)

        required_rule = re.search(
            r'\.o_furniture_advance_required\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(required_rule)
        self.assertIn('background: #fff;', required_rule.group(1))
        self.assertNotIn(
            'background: var(--stage-rail);', required_rule.group(1),
        )

        quantity_rule = re.search(
            r'\.o_furniture_advance_material_wizard\s+'
            r'\.o_furniture_advance_material_qty\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(quantity_rule)
        self.assertIn('background: #edf3f1;', quantity_rule.group(1))
        self.assertIn('color: #315f59;', quantity_rule.group(1))

        selected_rule = re.search(
            r'\.o_furniture_advance_stage_card\.is-selected\s*'
            r'\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(selected_rule)
        self.assertIn('background: #fff;', selected_rule.group(1))
        self.assertIn(
            'border-inline-start-color: var(--stage-accent);',
            selected_rule.group(1),
        )
        self.assertIn(
            'outline: 1px solid #0f766e;', selected_rule.group(1),
        )

        shortage_rule = re.search(
            r'\.o_furniture_advance_stage_card\.has-shortage\s*'
            r'\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(shortage_rule)
        self.assertIn(
            'border-block-end: 2px solid #d09b49;',
            shortage_rule.group(1),
        )
        self.assertNotIn(
            'border-inline-start-color:', shortage_rule.group(1),
        )

        shortage_grid_rule = re.search(
            r'\.o_furniture_advance_shortage_grid\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(shortage_grid_rule)
        self.assertIn('grid-area: shortage;', shortage_grid_rule.group(1))

        blocked_note_rule = re.search(
            r'\.o_furniture_advance_blocked_note\s*\{([^}]*)\}',
            css,
        )
        self.assertIsNotNone(blocked_note_rule)
        self.assertIn('grid-area: blocked;', blocked_note_rule.group(1))

        scoped_rules_start = css.index(
            '.o_furniture_advance_material_wizard '
            '.o_furniture_advance_material_items'
        )
        wide_start = css.index(
            '@media (max-width: 1100px)',
            scoped_rules_start,
        )
        tablet_start = css.index(
            '@media (max-width: 760px)',
            wide_start,
        )
        mobile_start = css.index('@media (max-width: 520px)', tablet_start)
        wide_css = css[wide_start:tablet_start]
        tablet_css = css[tablet_start:mobile_start]
        mobile_css = css[mobile_start:]
        self.assertIn(
            'grid-template-columns: repeat(3, minmax(0, 1fr));',
            wide_css,
        )
        self.assertIn(
            'grid-template-columns: repeat(2, minmax(0, 1fr));',
            tablet_css,
        )
        tablet_card_rule = re.search(
            r'\.o_furniture_advance_stage_card\s*\{([^}]*)\}',
            tablet_css,
        )
        self.assertIsNotNone(tablet_card_rule)
        tablet_card_declarations = tablet_card_rule.group(1)
        self.assertIn('height: auto;', tablet_card_declarations)
        self.assertIn(
            'grid-template-columns: 1fr;',
            tablet_card_declarations,
        )
        self.assertIn(
            'grid-template-rows: auto auto auto auto;',
            tablet_card_declarations,
        )
        for area_name in ('rail', 'required', 'shortage', 'blocked'):
            self.assertIn(
                f'"{area_name}"',
                tablet_card_declarations,
            )
        self.assertIn('grid-auto-rows: auto;', tablet_css)
        self.assertIn(
            '.o_furniture_advance_stage_cards .o_kanban_record',
            tablet_css,
        )
        self.assertIn('height: auto !important;', tablet_css)
        self.assertIn('grid-template-columns: 1fr;', mobile_css)
        for responsive_css in (wide_css, tablet_css, mobile_css):
            self.assertIn(
                '.o_furniture_advance_material_wizard '
                '.o_furniture_advance_shortage_grid',
                responsive_css,
            )

    def test_batch_wizard_creates_one_release_for_multiple_productions(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = first | second
        wizard = self._create_batch_wizard(productions)

        self.assertEqual(wizard.production_ids, productions)
        self.assertEqual(
            wizard.order_line_ids.mapped('production_id'),
            productions,
        )
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == first
        ).write({
            'use_priming': True,
            'use_upholstery': False,
        })
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == second
        ).write({
            'use_priming': False,
            'use_upholstery': True,
        })

        Release = self.env['furniture.mrp.advance.material.release']
        releases_before = Release.search([])
        action = wizard.action_send_requests()
        release = Release.search([]) - releases_before

        self.assertEqual(len(release), 1)
        self.assertFalse(release.production_id)
        self.assertEqual(
            release.production_ids.sorted('id'), productions.sorted('id'),
        )
        self.assertEqual(len(release.stage_line_ids), 2)
        self.assertEqual(
            {
                (stage.production_id.id, stage.stage_code)
                for stage in release.stage_line_ids
            },
            {
                (first.id, 'priming'),
                (second.id, 'upholstery'),
            },
        )
        self.assertFalse(self._issue_moves(first))
        self.assertFalse(self._issue_moves(second))
        self.assertFalse(self.env['stock.move'].sudo().search([
            ('origin', 'in', productions.mapped('name')),
        ]))

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertIn('إذن واحد', action['params']['message'])
        self.assertIn('2', action['params']['message'])
        next_action = action['params']['next']
        self.assertEqual(
            next_action,
            {'type': 'ir.actions.act_window_close'},
        )

    def test_batch_wizard_onchange_adds_multiple_productions(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        third, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        wizard = Wizard.new({'company_id': self.env.company.id})
        for production in (first, second, third):
            wizard.production_ids = wizard.production_ids | production
            wizard._onchange_production_ids()
        productions = (first | second | third).sorted('id')
        self.assertEqual(
            wizard.production_ids._origin.ids,
            productions.ids,
        )
        self.assertEqual(
            wizard.order_line_ids.mapped(
                'production_id'
            )._origin.sorted('id'),
            productions,
        )
        self.assertTrue(all(
            wizard.order_line_ids.mapped('has_selectable_stages')
        ))
        self.assertTrue(all(
            wizard.order_line_ids.mapped('can_use_priming')
        ))
        self.assertTrue(all(
            wizard.order_line_ids.mapped('can_use_upholstery')
        ))

    def test_batch_menu_defaults_empty_until_period_filter(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        first.write({
            'date_planned_start': datetime(2026, 7, 1, 8, 0),
            'date_planned_finish': datetime(2026, 7, 2, 17, 0),
        })
        second.write({
            'date_planned_start': datetime(2026, 7, 3, 8, 0),
            'date_planned_finish': datetime(2026, 7, 4, 17, 0),
        })

        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ].with_context(
            form_view_initial_mode='edit',
            auto_load_eligible_productions=True,
        )
        defaults = Wizard.default_get([
            'company_id', 'period_start', 'period_end',
            'production_ids', 'order_line_ids',
        ])
        wizard = Wizard.create(defaults)

        self.assertFalse(wizard.period_start)
        self.assertFalse(wizard.period_end)
        self.assertFalse(wizard.production_ids)
        self.assertFalse(wizard.order_line_ids)

    def test_batch_list_launch_waits_for_period_and_starts_unchecked(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        third, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = first | second | third
        for production, day in zip(productions, (1, 2, 3)):
            production.write({
                'date_planned_start': datetime(2026, 7, day, 8, 0),
                'date_planned_finish': datetime(2026, 7, day, 17, 0),
            })

        selected = first | third
        action = selected.action_open_batch_advance_material_wizard()
        wizard = self.env[action['res_model']].browse(action['res_id'])

        self.assertFalse(wizard.production_ids)
        self.assertFalse(wizard.order_line_ids)
        wizard.period_start = date(2026, 7, 1)
        wizard.period_end = date(2026, 7, 3)
        wizard._onchange_period_filter()
        shown = wizard.production_ids & productions
        shown_lines = wizard.order_line_ids.filtered(
            lambda line: line.production_id in productions
        )

        self.assertEqual(shown.sorted('id'), productions.sorted('id'))
        self.assertEqual(
            shown_lines.mapped('production_id').sorted('id'),
            productions.sorted('id'),
        )
        self.assertFalse(any(shown_lines.mapped('include_in_request')))

    def test_batch_period_filter_requires_both_dates_and_clears_cards(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        production.write({
            'date_planned_start': datetime(2026, 7, 5, 8, 0),
            'date_planned_finish': datetime(2026, 7, 5, 17, 0),
        })
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        wizard = Wizard.new({'company_id': self.env.company.id})

        wizard.period_start = date(2026, 7, 5)
        wizard._onchange_period_filter()
        self.assertFalse(wizard.production_ids)
        self.assertFalse(wizard.order_line_ids)

        wizard.period_end = date(2026, 7, 5)
        wizard._onchange_period_filter()
        self.assertIn(production, wizard.production_ids._origin)
        self.assertTrue(wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == production
        ))

        wizard.period_start = False
        wizard._onchange_period_filter()
        self.assertFalse(wizard.production_ids)
        self.assertFalse(wizard.order_line_ids)

    def test_batch_period_filter_uses_inclusive_interval_overlap(self):
        left_boundary, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        right_boundary, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        inside, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        start_without_finish, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        before, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        after, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        finish_without_start, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        draft_overlap, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        left_boundary.write({
            'date_planned_start': datetime(2026, 7, 1, 8, 0),
            'date_planned_finish': datetime(2026, 7, 5, 0, 0),
        })
        right_boundary.write({
            'date_planned_start': datetime(2026, 7, 10, 23, 59),
            'date_planned_finish': datetime(2026, 7, 12, 17, 0),
        })
        inside.write({
            'date_planned_start': datetime(2026, 7, 6, 8, 0),
            'date_planned_finish': datetime(2026, 7, 9, 17, 0),
        })
        start_without_finish.write({
            'date_planned_start': datetime(2026, 7, 7, 12, 0),
            'date_planned_finish': False,
        })
        before.write({
            'date_planned_start': datetime(2026, 7, 1, 8, 0),
            'date_planned_finish': datetime(2026, 7, 4, 23, 59),
        })
        after.write({
            'date_planned_start': datetime(2026, 7, 11, 0, 0),
            'date_planned_finish': datetime(2026, 7, 12, 17, 0),
        })
        finish_without_start.write({
            'date_planned_start': False,
            'date_planned_finish': datetime(2026, 7, 7, 17, 0),
        })
        draft_overlap.write({
            'state': 'draft',
            'date_planned_start': datetime(2026, 7, 7, 8, 0),
            'date_planned_finish': datetime(2026, 7, 7, 17, 0),
        })
        created = (
            left_boundary | right_boundary | inside | start_without_finish
            | before | after | finish_without_start | draft_overlap
        )
        expected = (
            left_boundary | right_boundary | inside | start_without_finish
        )
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ].with_context(tz='UTC')
        wizard = Wizard.new({
            'company_id': self.env.company.id,
            'period_start': date(2026, 7, 5),
            'period_end': date(2026, 7, 10),
        })

        wizard._onchange_period_filter()
        shown = wizard.production_ids._origin & created
        shown_lines = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin in created
        )

        self.assertEqual(shown.sorted('id'), expected.sorted('id'))
        self.assertEqual(
            shown_lines.mapped('production_id')._origin.sorted('id'),
            expected.sorted('id'),
        )
        self.assertFalse(any(shown_lines.mapped('include_in_request')))

    def test_batch_period_filter_respects_user_timezone_dates(self):
        local_fifth, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        local_sixth, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        # UTC timestamps cross midnight in Asia/Riyadh (UTC+3).
        local_fifth.write({
            'date_planned_start': datetime(2026, 7, 4, 22, 30),
            'date_planned_finish': datetime(2026, 7, 4, 23, 0),
        })
        local_sixth.write({
            'date_planned_start': datetime(2026, 7, 5, 22, 30),
            'date_planned_finish': datetime(2026, 7, 5, 23, 0),
        })
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ].with_context(tz='Asia/Riyadh')
        wizard = Wizard.new({
            'company_id': self.env.company.id,
            'period_start': date(2026, 7, 5),
            'period_end': date(2026, 7, 5),
        })

        wizard._onchange_period_filter()
        shown = wizard.production_ids._origin & (local_fifth | local_sixth)

        self.assertEqual(shown, local_fifth)

    def test_batch_period_filter_rejects_reversed_range(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        production.write({
            'date_planned_start': datetime(2026, 7, 5, 8, 0),
            'date_planned_finish': datetime(2026, 7, 5, 17, 0),
        })
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        wizard = Wizard.new({
            'company_id': self.env.company.id,
            'period_start': date(2026, 7, 1),
            'period_end': date(2026, 7, 10),
        })
        wizard._onchange_period_filter()
        self.assertIn(production, wizard.production_ids._origin)

        wizard.period_start = date(2026, 7, 10)
        wizard.period_end = date(2026, 7, 1)
        result = wizard._onchange_period_filter()

        self.assertFalse(wizard.production_ids)
        self.assertFalse(wizard.order_line_ids)
        self.assertTrue(result and result.get('warning'))

    def test_batch_period_filter_preserves_remaining_order_choices(self):
        staying, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        departing, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        arriving, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        staying.write({
            'date_planned_start': datetime(2026, 7, 5, 8, 0),
            'date_planned_finish': datetime(2026, 7, 10, 17, 0),
        })
        departing.write({
            'date_planned_start': datetime(2026, 7, 1, 8, 0),
            'date_planned_finish': datetime(2026, 7, 4, 17, 0),
        })
        arriving.write({
            'date_planned_start': datetime(2026, 7, 11, 8, 0),
            'date_planned_finish': datetime(2026, 7, 12, 17, 0),
        })
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        wizard = Wizard.new({
            'company_id': self.env.company.id,
            'period_start': date(2026, 7, 1),
            'period_end': date(2026, 7, 10),
        })
        wizard._onchange_period_filter()
        staying_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == staying
        )
        departing_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == departing
        )
        staying_line.include_in_request = True
        staying_line.use_priming = True
        departing_line.include_in_request = True
        departing_line.use_upholstery = True

        wizard.period_start = date(2026, 7, 5)
        wizard.period_end = date(2026, 7, 12)
        wizard._onchange_period_filter()
        staying_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == staying
        )
        arriving_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == arriving
        )

        self.assertTrue(staying_line.include_in_request)
        self.assertTrue(staying_line.use_priming)
        self.assertFalse(staying_line.use_upholstery)
        self.assertNotIn(departing, wizard.production_ids._origin)
        self.assertFalse(wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == departing
        ))
        self.assertTrue(arriving_line)
        self.assertFalse(arriving_line.include_in_request)
        self.assertFalse(arriving_line.use_priming)
        self.assertFalse(arriving_line.use_upholstery)

    def test_batch_period_fields_are_visible_before_order_cards(self):
        view = self.env.ref(
            'furniture_mrp.'
            'view_furniture_mrp_advance_material_batch_wizard_form'
        )
        document = etree.fromstring(view.arch_db.encode())
        period_start = document.xpath("//field[@name='period_start']")
        period_end = document.xpath("//field[@name='period_end']")
        order_lines = document.xpath("//field[@name='order_line_ids']")

        self.assertEqual(len(period_start), 1)
        self.assertEqual(len(period_end), 1)
        self.assertEqual(len(order_lines), 1)
        self.assertIsNone(period_start[0].get('invisible'))
        self.assertIsNone(period_end[0].get('invisible'))
        self.assertLess(
            list(document.iter()).index(period_start[0]),
            list(document.iter()).index(order_lines[0]),
        )
        self.assertLess(
            list(document.iter()).index(period_end[0]),
            list(document.iter()).index(order_lines[0]),
        )

    def test_batch_onchange_preserves_include_and_stages_per_order(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        third, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        commands = Wizard._order_commands(first | second)
        for _operation, _virtual_id, values in commands:
            if values['production_id'] == first.id:
                values.update({
                    'include_in_request': True,
                    'use_priming': True,
                    'use_upholstery': False,
                })
            else:
                values.update({
                    'include_in_request': False,
                    'use_priming': False,
                    'use_upholstery': True,
                })
        wizard = Wizard.new({
            'company_id': self.env.company.id,
            'production_ids': [(6, 0, (first | second).ids)],
            'order_line_ids': commands,
        })
        wizard.production_ids = first | second | third
        wizard._onchange_production_ids()

        first_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == first
        )
        second_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == second
        )
        third_line = wizard.order_line_ids.filtered(
            lambda line: line.production_id._origin == third
        )
        self.assertTrue(first_line.include_in_request)
        self.assertTrue(first_line.use_priming)
        self.assertFalse(first_line.use_upholstery)
        self.assertFalse(second_line.include_in_request)
        self.assertFalse(second_line.use_priming)
        self.assertTrue(second_line.use_upholstery)
        self.assertFalse(third_line.include_in_request)
        self.assertFalse(third_line.use_priming)
        self.assertFalse(third_line.use_upholstery)

    def test_batch_send_ignores_unchecked_orders(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        third, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = first | second | third
        wizard = self._create_batch_wizard(productions)
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == first
        ).write({
            'include_in_request': True,
            'use_priming': True,
            'use_upholstery': False,
        })
        # A stale stage tick on an unchecked card must never send that order.
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == second
        ).write({
            'include_in_request': False,
            'use_priming': False,
            'use_upholstery': True,
        })
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == third
        ).write({
            'include_in_request': True,
            'use_priming': False,
            'use_upholstery': True,
        })

        Release = self.env['furniture.mrp.advance.material.release']
        releases_before = Release.search([])
        wizard.action_send_requests()
        release = Release.search([]) - releases_before

        self.assertEqual(len(release), 1)
        self.assertFalse(release.production_id)
        self.assertEqual(
            release.production_ids.sorted('id'),
            (first | third).sorted('id'),
        )
        self.assertEqual(
            {
                (stage.production_id.id, stage.stage_code)
                for stage in release.stage_line_ids
            },
            {
                (first.id, 'priming'),
                (third.id, 'upholstery'),
            },
        )
        self.assertNotIn(second, release.production_ids)

    def test_batch_checked_order_requires_at_least_one_stage(self):
        included, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        excluded, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = included | excluded
        wizard = self._create_batch_wizard(productions)
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == included
        ).write({
            'include_in_request': True,
            'use_priming': False,
            'use_upholstery': False,
        })
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == excluded
        ).write({
            'include_in_request': False,
            'use_priming': True,
            'use_upholstery': False,
        })

        with self.assertRaises(UserError):
            wizard.action_send_requests()

        self.assertFalse(self.env[
            'furniture.mrp.advance.material.release.stage'
        ].search([
            ('production_id', 'in', productions.ids),
        ]))

    def test_manager_executes_batch_end_to_end(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = (first | second).with_user(self.manager_user)
        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ].with_user(self.manager_user)
        wizard = Wizard.create({
            'period_start': date(2000, 1, 1),
            'period_end': date(2100, 12, 31),
            'production_ids': [(6, 0, productions.ids)],
            'order_line_ids': Wizard._order_commands(productions),
        })
        wizard.order_line_ids.write({
            'use_priming': True,
            'use_upholstery': False,
        })

        Release = self.env['furniture.mrp.advance.material.release']
        releases_before = Release.search([])
        action = wizard.action_send_requests()
        release = Release.search([]) - releases_before

        self.assertEqual(len(release), 1)
        self.assertEqual(
            release.production_ids.sorted('id'), productions.sorted('id'),
        )
        self.assertEqual(release.requested_by_id, self.manager_user)
        self.assertEqual(action['tag'], 'display_notification')

    def test_batch_availability_pool_is_shared_between_orders(self):
        first, _line, _materials, shared, _unique = (
            self._create_manual_production(shared_stock=5.0)
        )
        second, _line, _materials, _other_shared, _unique = (
            self._create_manual_production(shared_stock=0.0)
        )
        second.material_line_ids.filtered(
            lambda line: line.stage == 'priming'
        ).write({
            'product_id': shared.id,
            'product_uom_id': shared.uom_id.id,
        })
        productions = first | second
        wizard = self._create_batch_wizard(productions)
        wizard.order_line_ids.write({
            'use_priming': True,
            'use_upholstery': False,
        })

        Release = self.env['furniture.mrp.advance.material.release']
        releases_before = Release.search([])
        wizard.action_send_requests()
        release = Release.search([]) - releases_before
        available_by_production = {
            production.id: sum(
                release.stage_line_ids.filtered(
                    lambda stage, current=production: (
                        stage.production_id == current
                    )
                ).material_line_ids.filtered(
                    lambda detail: detail.product_id == shared
                ).mapped('available_qty')
            )
            for production in productions
        }

        self.assertEqual(available_by_production[first.id], 4.0)
        self.assertEqual(available_by_production[second.id], 1.0)

    def test_combined_issue_keeps_each_order_stage_and_moves_independent(self):
        first, _line, first_materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, second_materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = first | second
        wizard = self._create_batch_wizard(productions)
        wizard.order_line_ids.write({
            'use_priming': True,
            'use_upholstery': False,
        })

        Release = self.env['furniture.mrp.advance.material.release']
        releases_before = Release.search([])
        wizard.action_send_requests()
        release = Release.search([]) - releases_before

        # The same stage code is valid twice because each row belongs to a
        # different production order inside the one warehouse permission.
        self.assertEqual(len(release), 1)
        self.assertFalse(release.production_id)
        self.assertFalse(Release._fields['production_id'].required)
        self.assertEqual(len(release.stage_line_ids), 2)
        self.assertEqual(
            set(release.stage_line_ids.mapped('stage_code')),
            {'priming'},
        )
        self.assertEqual(
            release.stage_line_ids.mapped('production_id').sorted('id'),
            productions.sorted('id'),
        )

        release.with_user(release.assigned_to_id).action_issue()
        release.invalidate_recordset(['state'])
        first.invalidate_recordset([
            'state', 'priming_order_id', 'advance_material_release_ids',
            'advance_material_release_count',
        ])
        second.invalidate_recordset([
            'state', 'priming_order_id', 'advance_material_release_ids',
            'advance_material_release_count',
        ])
        first_materials.invalidate_recordset(['move_id'])
        second_materials.invalidate_recordset(['move_id'])

        self.assertEqual(release.state, 'issued')
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'issued'})
        self.assertEqual(first.state, 'confirmed')
        self.assertEqual(second.state, 'confirmed')
        self.assertFalse(first.priming_order_id)
        self.assertFalse(second.priming_order_id)
        self.assertEqual(first.advance_material_release_ids, release)
        self.assertEqual(second.advance_material_release_ids, release)
        self.assertEqual(first.advance_material_release_count, 1)
        self.assertEqual(second.advance_material_release_count, 1)

        first_moves = self._issue_moves(first)
        second_moves = self._issue_moves(second)
        self.assertEqual(len(first_moves), 1)
        self.assertEqual(len(second_moves), 1)
        self.assertFalse(first_moves & second_moves)
        self.assertEqual(set(first_moves.mapped('origin')), {first.name})
        self.assertEqual(set(second_moves.mapped('origin')), {second.name})
        self.assertIn(
            first_materials.filtered(lambda line: line.stage == 'priming').move_id,
            first_moves,
        )
        self.assertIn(
            second_materials.filtered(lambda line: line.stage == 'priming').move_id,
            second_moves,
        )

    def test_combined_issue_shortage_rolls_back_every_order_before_moves(self):
        first, _line, _materials, shared, _unique = (
            self._create_manual_production(
                shared_stock=4.0, unique_stock=0.0,
            )
        )
        second, _line, _materials, _other_shared, _unique = (
            self._create_manual_production(
                shared_stock=0.0, unique_stock=0.0,
            )
        )
        second.material_line_ids.filtered(
            lambda line: line.stage == 'priming'
        ).write({
            'product_id': shared.id,
            'product_uom_id': shared.uom_id.id,
        })
        productions = first | second
        wizard = self._create_batch_wizard(productions)
        wizard.order_line_ids.write({
            'use_priming': True,
            'use_upholstery': False,
        })

        Release = self.env['furniture.mrp.advance.material.release']
        releases_before = Release.search([])
        wizard.action_send_requests()
        release = Release.search([]) - releases_before
        source = self.env.ref('stock.stock_location_stock')
        stock_before = self._location_qty(first, source, shared)

        with self.assertRaises(UserError):
            release.sudo().action_issue()

        release.invalidate_recordset(['state'])
        release.stage_line_ids.invalidate_recordset(['state'])
        self.assertEqual(release.state, 'pending')
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'pending'})
        self.assertFalse(self._issue_moves(first))
        self.assertFalse(self._issue_moves(second))
        self.assertAlmostEqual(
            self._location_qty(first, source, shared),
            stock_before,
        )

    def test_manager_cannot_request_for_an_unavailable_company(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        other_company = self.env['res.company'].create({
            'name': 'Advance Material Other Company',
        })
        production.sudo().write({'company_id': other_company.id})

        with self.assertRaises(AccessError):
            self.env[
                'furniture.mrp.advance.material.release'
            ].with_user(self.manager_user).create_from_stage_codes(
                production.with_user(self.manager_user),
                ('priming',),
            )

    def test_stage_supervisor_can_request_own_stage_materials(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        supervisor = self._create_stage_supervisor('priming')
        Release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(supervisor)

        release = Release.create_from_stage_codes(
            production.id,
            ('priming',),
            notify_storekeeper=False,
        )

        self.assertEqual(
            release.sudo().stage_line_ids.mapped('stage_code'),
            ['priming'],
        )
        self.assertEqual(release.sudo().requested_by_id, supervisor)

    def test_stage_supervisor_cannot_request_foreign_stage_materials(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        supervisor = self._create_stage_supervisor('priming')
        Release = self.env[
            'furniture.mrp.advance.material.release'
        ]
        release_count_before = Release.sudo().search_count([
            ('stage_line_ids.production_id', '=', production.id),
        ])

        with self.assertRaises(AccessError):
            Release.with_user(
                supervisor
            ).create_from_production_stage_map(
                production.id,
                {production.id: ('upholstery',)},
                notify_storekeeper=False,
            )

        self.assertEqual(
            Release.sudo().search_count([
                ('stage_line_ids.production_id', '=', production.id),
            ]),
            release_count_before,
        )

    def test_multi_stage_supervisor_can_request_assigned_stage_union(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        supervisor = self._create_stage_supervisor(
            ('priming', 'upholstery')
        )
        Release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(supervisor)

        release = Release.create_from_production_stage_map(
            [first.id, second.id],
            {
                first.id: ('priming',),
                second.id: ('upholstery',),
            },
            notify_storekeeper=False,
            is_batch_request=True,
        )

        stage_pairs = {
            (stage.production_id.id, stage.stage_code)
            for stage in release.sudo().stage_line_ids
        }
        self.assertEqual(stage_pairs, {
            (first.id, 'priming'),
            (second.id, 'upholstery'),
        })

    def test_manager_can_request_any_valid_stage_union(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        Release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(self.manager_user)

        release = Release.create_from_stage_codes(
            production.id,
            ('priming', 'upholstery'),
            notify_storekeeper=False,
        )

        self.assertEqual(
            set(release.sudo().stage_line_ids.mapped('stage_code')),
            {'priming', 'upholstery'},
        )

    def test_supervisor_assignment_keeps_its_company_stage_pair(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        supervisor = self._create_stage_supervisor('priming')
        other_company = self.env['res.company'].create({
            'name': 'Advance Material Supervisor Scope Company',
        })
        supervisor.sudo().write({
            'company_ids': [(4, other_company.id)],
        })
        production.sudo().write({'company_id': other_company.id})
        Release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(supervisor).with_context(
            allowed_company_ids=[self.env.company.id, other_company.id],
        )

        with self.assertRaises(AccessError):
            Release.create_from_stage_codes(
                production.id,
                ('priming',),
                notify_storekeeper=False,
            )

    def test_batch_wizard_rolls_back_all_when_later_order_is_duplicate(self):
        first, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        second, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        productions = first | second
        existing_release = self._create_release(second, ('upholstery',))
        Release = self.env['furniture.mrp.advance.material.release']
        production_domain = [
            ('stage_line_ids.production_id', 'in', productions.ids),
        ]
        releases_before = Release.search(production_domain)
        wizard = self._create_batch_wizard(productions)
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == first
        ).write({
            'use_priming': True,
            'use_upholstery': False,
        })
        wizard.order_line_ids.filtered(
            lambda line: line.production_id == second
        ).write({
            'use_priming': False,
            'use_upholstery': True,
        })

        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                wizard.action_send_requests()

        releases_after = Release.search(production_domain)
        self.assertEqual(releases_after, releases_before)
        self.assertEqual(releases_after, existing_release)
        self.assertFalse(self._issue_moves(first))
        self.assertFalse(self._issue_moves(second))

    def test_worker_cannot_create_single_or_batch_advance_release(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        Release = self.env[
            'furniture.mrp.advance.material.release'
        ]

        with self.assertRaises(AccessError):
            Release.with_user(self.worker_user).create_from_stage_codes(
                production,
                ('priming',),
            )

        Wizard = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ].sudo()
        wizard = Wizard.create({
            'period_start': date(2000, 1, 1),
            'period_end': date(2100, 12, 31),
            'production_ids': [(6, 0, production.ids)],
            'order_line_ids': Wizard._order_commands(production),
        })
        wizard.order_line_ids.write({
            'use_priming': True,
            'use_upholstery': False,
        })
        with self.assertRaises(AccessError):
            wizard.with_user(self.worker_user).action_send_requests()

        self.assertFalse(Release.search([
            ('production_ids', 'in', production.ids),
        ]))

    def test_production_list_and_kanban_expose_grouped_batch_button(self):
        expected_groups = {
            'furniture_mrp.group_furniture_mrp_manager',
            'furniture_mrp.group_furniture_mrp_supervisor',
        }
        action = self.env.ref(
            'furniture_mrp.'
            'action_furniture_mrp_advance_material_batch_wizard'
        )
        action_name = str(action.id)
        view_specs = (
            (
                'furniture_mrp.view_furniture_mrp_production_list',
                'list',
            ),
            (
                'furniture_mrp.view_furniture_mrp_production_kanban',
                'kanban',
            ),
        )
        for xml_id, view_type in view_specs:
            with self.subTest(view_type=view_type):
                view = self.env.ref(xml_id)
                source = etree.fromstring(view.arch_db.encode())
                buttons = source.xpath(
                    (
                        '//%s/header/button['
                        "@name='%s'"
                        ']'
                    ) % (view_type, action_name)
                )
                self.assertEqual(len(buttons), 1)
                self.assertEqual(buttons[0].get('type'), 'action')
                self.assertEqual(
                    set(buttons[0].get('groups', '').split(',')),
                    expected_groups,
                )

                manager_arch = self.env[view.model].with_user(
                    self.manager_user
                ).get_view(
                    view_id=view.id,
                    view_type=view_type,
                )['arch']
                manager_document = etree.fromstring(manager_arch.encode())
                self.assertEqual(len(manager_document.xpath(
                    "//header/button[@name='%s']" % action_name
                )), 1)

                worker_arch = self.env[view.model].with_user(
                    self.worker_user
                ).get_view(
                    view_id=view.id,
                    view_type=view_type,
                )['arch']
                worker_document = etree.fromstring(worker_arch.encode())
                self.assertFalse(worker_document.xpath(
                    "//header/button[@name='%s']" % action_name
                ))

    def test_batch_order_card_uses_owl_boolean_negation(self):
        view = self.env.ref(
            'furniture_mrp.'
            'view_furniture_mrp_advance_material_batch_wizard_form'
        )
        document = etree.fromstring(view.arch_db.encode())
        empty_stage_alerts = document.xpath(
            "//div[@t-if='!record.has_selectable_stages.raw_value']"
        )
        card_headers = document.xpath(
            "//t[@t-name='card']//header"
        )
        card_heading_blocks = document.xpath(
            "//t[@t-name='card']"
            "//div[contains(@class, 'o_furniture_batch_release_order_header')]"
        )

        self.assertEqual(len(empty_stage_alerts), 1)
        self.assertFalse(card_headers)
        self.assertEqual(len(card_heading_blocks), 1)
        self.assertNotIn(
            't-if="not record.has_selectable_stages.raw_value"',
            view.arch_db,
        )

    def test_batch_view_auto_loads_orders_without_manual_add_line(self):
        view = self.env.ref(
            'furniture_mrp.'
            'view_furniture_mrp_advance_material_batch_wizard_form'
        )
        document = etree.fromstring(view.arch_db.encode())
        production_fields = document.xpath(
            "//field[@name='production_ids']"
        )
        order_kanbans = document.xpath(
            "//field[@name='order_line_ids']/kanban"
        )
        include_fields = document.xpath(
            "//field[@name='order_line_ids']"
            "//*[self::form or self::kanban]/field["
            "@name='include_in_request' and @force_save='1']"
        )
        action = self.env.ref(
            'furniture_mrp.'
            'action_furniture_mrp_advance_material_batch_wizard'
        )

        self.assertEqual(len(production_fields), 1)
        self.assertEqual(production_fields[0].get('invisible'), '1')
        self.assertFalse(production_fields[0].xpath('./list'))
        self.assertEqual(len(order_kanbans), 1)
        self.assertEqual(order_kanbans[0].get('create'), '0')
        self.assertEqual(order_kanbans[0].get('delete'), '0')
        self.assertEqual(len(include_fields), 2)
        self.assertIn(
            "'auto_load_eligible_productions': True", action.context
        )

    def test_batch_receipt_view_force_saves_server_generated_links(self):
        view = self.env.ref(
            'furniture_mrp.'
            'view_furniture_mrp_advance_material_batch_receipt_wizard_form'
        )
        document = etree.fromstring(view.arch_db.encode())

        for field_name in ('company_id', 'release_stage_ids'):
            fields = document.xpath(
                "//form/sheet/field[@name='%s']" % field_name
            )
            self.assertEqual(len(fields), 1)
            self.assertEqual(fields[0].get('force_save'), '1')

        line_lists = document.xpath("//field[@name='line_ids']/list")
        self.assertEqual(len(line_lists), 1)
        line_list = line_lists[0]
        self.assertEqual(line_list.get('create'), '0')
        self.assertEqual(line_list.get('delete'), '0')
        for field_name in (
            'release_stage_id', 'release_line_id', 'issued_qty',
        ):
            fields = line_list.xpath(
                "./field[@name='%s']" % field_name
            )
            self.assertEqual(len(fields), 1)
            self.assertEqual(fields[0].get('force_save'), '1')

        for field_name in ('release_stage_id', 'release_line_id'):
            fields = line_list.xpath(
                "./field[@name='%s']" % field_name
            )
            self.assertEqual(fields[0].get('column_invisible'), '1')

    def test_multi_stage_request_button_hides_after_all_stages_are_covered(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        production.invalidate_recordset([
            'has_uncovered_advance_material_stages',
        ])
        self.assertTrue(production.has_uncovered_advance_material_stages)

        self._create_release(production, ('priming',))
        production.invalidate_recordset([
            'has_uncovered_advance_material_stages',
        ])
        self.assertTrue(production.has_uncovered_advance_material_stages)

        upholstery_release = self._create_release(
            production, ('upholstery',),
        )
        production.invalidate_recordset([
            'has_uncovered_advance_material_stages',
        ])
        self.assertFalse(production.has_uncovered_advance_material_stages)
        with self.assertRaises(UserError):
            production.action_open_advance_material_wizard()

        upholstery_release.sudo().action_reject()
        production.invalidate_recordset([
            'has_uncovered_advance_material_stages',
        ])
        self.assertTrue(production.has_uncovered_advance_material_stages)

    def test_production_view_hides_multi_stage_request_without_uncovered_stages(self):
        view = self.env.ref('furniture_mrp.view_furniture_mrp_production_form')
        arch = self.env[view.model].with_user(self.manager_user).get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        document = etree.fromstring(arch.encode())
        buttons = document.xpath(
            "//header/button[@name='action_open_advance_material_wizard']"
        )
        helper_fields = document.xpath(
            "//header/field[@name='has_uncovered_advance_material_stages']"
        )

        self.assertEqual(len(buttons), 1)
        self.assertIn(
            'o_furniture_advance_material_button',
            buttons[0].get('class', ''),
        )
        self.assertIn(
            'not has_uncovered_advance_material_stages',
            buttons[0].get('invisible', ''),
        )
        self.assertEqual(len(helper_fields), 1)
        self.assertEqual(helper_fields[0].get('invisible'), '1')

    def test_issue_button_executes_without_confirmation_dialog(self):
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_release_form'
        )
        arch = self.env[view.model].with_user(self.storekeeper).get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        document = etree.fromstring(arch.encode())
        buttons = document.xpath("//header/button[@name='action_issue']")
        production_fields = document.xpath(
            "//sheet/group/group/field[@name='production_ids']"
        )

        self.assertEqual(len(buttons), 1)
        self.assertIsNone(buttons[0].get('confirm'))
        self.assertEqual(document.get('edit'), '0')
        self.assertEqual(len(production_fields), 1)
        self.assertEqual(production_fields[0].get('readonly'), '1')
        self.assertIsNone(production_fields[0].get('required'))
        self.assertFalse(document.xpath(
            "//sheet/group/group/field[@name='production_id']"
        ))
        stage_lists = document.xpath(
            "//field[@name='stage_line_ids']/list"
        )
        self.assertEqual(len(stage_lists), 1)
        stage_columns = stage_lists[0].xpath('./field/@name')
        self.assertIn('requested_qty', stage_columns)
        self.assertLess(
            stage_columns.index('requested_qty'),
            stage_columns.index('received_qty'),
        )
        requested_columns = stage_lists[0].xpath(
            "./field[@name='requested_qty']"
        )
        self.assertEqual(len(requested_columns), 1)
        self.assertEqual(
            requested_columns[0].get('string'),
            'الكمية المطلوبة',
        )

    def test_aggregate_shortage_is_rejected_before_first_move(self):
        production, _line, _materials, shared, unique = (
            self._create_manual_production(shared_stock=9.0, unique_stock=2.0)
        )
        release = self._create_release(production)
        source = self.env.ref('stock.stock_location_stock')
        shared_before = self._location_qty(production, source, shared)
        unique_before = self._location_qty(production, source, unique)

        with self.assertRaises(UserError):
            release.sudo().action_issue()

        release.invalidate_recordset(['state'])
        self.assertEqual(release.state, 'pending')
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'pending'})
        self.assertFalse(self._issue_moves(production))
        self.assertAlmostEqual(
            self._location_qty(production, source, shared), shared_before,
        )
        self.assertAlmostEqual(
            self._location_qty(production, source, unique), unique_before,
        )

    def test_legacy_request_blocks_consolidated_request_for_same_stage(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        self.env['furniture.mrp.store.request'].sudo().create({
            'production_id': production.id,
            'stage_code': 'priming',
            'stage_order_model': 'furniture.mrp.priming',
            'stage_order_res_id': production.id,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'assigned_to_id': self.storekeeper.id,
        })

        with self.assertRaises(UserError):
            self._create_release(production, ('priming',))

    def test_issue_moves_to_handover_without_starting_or_entering_halls(self):
        production, _line, material_lines, shared, unique = (
            self._create_manual_production()
        )
        release = self._create_release(production)

        release.sudo().action_issue()
        release.invalidate_recordset(['state', 'issued_by_id', 'issued_at'])
        production.invalidate_recordset([
            'state', 'priming_order_id', 'upholstery_order_id',
        ])

        self.assertEqual(release.state, 'issued')
        self.assertTrue(release.issued_by_id)
        self.assertTrue(release.issued_at)
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'issued'})
        self.assertEqual(production.state, 'confirmed')
        self.assertFalse(production.priming_order_id)
        self.assertFalse(production.upholstery_order_id)
        moves = self._issue_moves(production)
        self.assertEqual(len(moves), 3)
        self.assertEqual(
            set(moves.mapped('location_dest_id')),
            {self._handover_location()},
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, shared,
            ),
            0.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_upholstery_wip_id, shared,
            ),
            0.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_upholstery_wip_id, unique,
            ),
            0.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), shared,
            ),
            10.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), unique,
            ),
            2.0,
        )
        self.assertEqual(
            set(release.stage_line_ids.mapped('receipt_state')),
            {'waiting'},
        )
        self.assertTrue(all(line.move_id for line in material_lines))

    def test_issue_preserves_recipe_precision_finer_than_product_uom(self):
        production, _line, material_lines, shared, _unique = (
            self._create_manual_production()
        )
        precise_uom = self.env['uom.uom'].create({
            'name': 'Advance Release Precise Recipe Unit',
            'category_id': shared.uom_id.category_id.id,
            'uom_type': 'bigger',
            'factor': 1.0,
            'rounding': 0.001,
        })
        priming_material = material_lines.filtered(
            lambda line: line.stage == 'priming'
        ).ensure_one()
        priming_material.write({
            'product_uom_id': precise_uom.id,
            'qty_needed': 0.054,
        })
        release = self._create_release(production, ('priming',))

        release.sudo().action_issue()

        detail = release.stage_line_ids.material_line_ids.ensure_one()
        move = detail.move_ids.ensure_one()
        self.assertEqual(release.state, 'issued')
        self.assertEqual(move.state, 'done')
        self.assertLessEqual(move.product_uom.rounding, precise_uom.rounding)
        self.assertAlmostEqual(move.quantity, 0.054, places=3)
        self.assertAlmostEqual(detail.requested_qty, 0.054, places=3)
        self.assertAlmostEqual(detail.issued_qty, 0.054, places=3)
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), shared,
            ),
            0.054,
            places=3,
        )

    def test_assigned_storekeeper_can_issue_and_link_production_materials(self):
        production, _line, material_lines, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production)

        release.with_user(self.storekeeper).action_issue()
        release.invalidate_recordset(['state', 'issued_by_id'])
        material_lines.invalidate_recordset(['move_id'])

        self.assertEqual(release.state, 'issued')
        self.assertEqual(release.issued_by_id, self.storekeeper)
        self.assertTrue(all(line.move_id for line in material_lines))

    def test_only_assigned_storekeeper_can_decide_advance_release(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production)

        self.assertTrue(
            release.with_user(self.storekeeper).can_warehouse_decide,
        )
        for user in (
            self.other_storekeeper,
            self.manager_user,
            self.worker_user,
        ):
            with self.subTest(login=user.login):
                self.assertFalse(
                    release.with_user(user).can_warehouse_decide,
                )
                with self.assertRaises(AccessError):
                    release.with_user(user).action_issue()
                with self.assertRaises(AccessError):
                    release.with_user(user).action_reject()
                with self.assertRaises(AccessError):
                    release.with_user(user).write({'state': 'issued'})
                with self.assertRaises(AccessError):
                    self.env[
                        'furniture.mrp.advance.material.release'
                    ].with_user(user).create({
                        'production_id': production.id,
                        'assigned_to_id': user.id,
                    })

        release.invalidate_recordset(['state'])
        self.assertEqual(release.state, 'pending')

    def test_double_issue_does_not_duplicate_stock_moves(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production)
        release.sudo().action_issue()
        issued_move_ids = self._issue_moves(production).ids

        with self.assertRaises(UserError):
            release.sudo().action_issue()

        self.assertEqual(self._issue_moves(production).ids, issued_move_ids)

    def test_reject_closes_stage_slots_without_stock_movement(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production)

        release.sudo().action_reject()
        release.invalidate_recordset(['state'])

        self.assertEqual(release.state, 'rejected')
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'rejected'})
        self.assertFalse(self._issue_moves(production))
        replacement = self._create_release(production, ('priming',))
        self.assertEqual(replacement.state, 'pending')

    def test_return_restores_stock_before_any_stage_starts(self):
        production, _line, material_lines, shared, unique = (
            self._create_manual_production()
        )
        release = self._create_release(production)
        release.sudo().action_issue()

        release.sudo().action_return()
        release.invalidate_recordset(['state', 'returned_by_id', 'returned_at'])
        material_lines.invalidate_recordset(['move_id'])
        source = self.env.ref('stock.stock_location_stock')

        self.assertEqual(release.state, 'returned')
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'returned'})
        self.assertTrue(release.returned_by_id)
        self.assertTrue(release.returned_at)
        self.assertTrue(all(
            move.origin_returned_move_id
            for move in release.stage_line_ids.mapped(
                'material_line_ids.return_move_ids'
            )
        ))
        self.assertAlmostEqual(self._location_qty(production, source, shared), 10.0)
        self.assertAlmostEqual(self._location_qty(production, source, unique), 2.0)
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, shared,
            ),
            0.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), shared,
            ),
            0.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_upholstery_wip_id, shared,
            ),
            0.0,
        )
        self.assertFalse(material_lines.mapped('move_id'))
        self.assertTrue(release.stage_line_ids.mapped('material_line_ids.return_move_ids'))

    def test_start_after_advance_issue_does_not_pull_raw_material_twice(self):
        production, production_line, raw = self._create_recipe_production(2.0)
        release = self._create_release(production, ('priming',))
        production.action_start_priming()
        stage_order = production.priming_order_id
        stage_order.worker_ids = [(6, 0, self.worker.ids)]
        stage_order.invalidate_recordset([
            'store_request_state', 'advance_material_release_state',
        ])
        self.assertEqual(stage_order.store_request_state, 'pending')
        self.assertEqual(stage_order.advance_material_release_state, 'pending')

        release.sudo().action_issue()
        issued_moves = self._issue_moves(production)
        self.assertEqual(len(issued_moves), 1)
        stage_order.invalidate_recordset([
            'store_request_state', 'advance_material_release_state',
        ])
        self.assertEqual(stage_order.store_request_state, 'awaiting_receipt')
        self.assertEqual(stage_order.advance_material_release_state, 'issued')
        with self.assertRaises(UserError):
            stage_order.action_start()
        advance_stage, _result = self._receive_stage(release, 'priming')
        detail = advance_stage.material_line_ids.ensure_one()
        technical_material = production.material_line_ids.filtered(
            lambda line: line.stage == 'priming'
        ).ensure_one()
        technical_material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
        ])
        self.assertFalse(detail.receipt_move_ids)
        self.assertFalse(technical_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), raw,
            ),
            2.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            0.0,
        )
        stage_order.invalidate_recordset([
            'store_request_state', 'advance_material_release_state',
        ])
        self.assertEqual(stage_order.store_request_state, 'approved')
        start_action = stage_order.action_start()
        self.assertEqual(
            start_action['res_model'],
            'furniture.mrp.first.stage.start.wizard',
        )
        wizard = self.env[start_action['res_model']].with_context(
            start_action.get('context', {}),
        ).browse(start_action['res_id'])
        wizard.action_start_selected_from_stock()

        stage_order.invalidate_recordset(['state'])
        production_line.invalidate_recordset([
            'first_stage_started', 'first_stage_started_stage',
        ])
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertTrue(production_line.first_stage_started)
        self.assertEqual(production_line.first_stage_started_stage, 'priming')
        release.stage_line_ids.invalidate_recordset(['state'])
        self.assertEqual(set(release.stage_line_ids.mapped('state')), {'started'})
        self.assertEqual(self._issue_moves(production), issued_moves)
        detail.invalidate_recordset(['receipt_move_ids'])
        technical_material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
            'move_id',
        ])
        self.assertEqual(len(detail.receipt_move_ids), 1)
        self.assertTrue(technical_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(technical_material.warehouse_received_qty, 2.0)
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            2.0,
        )

        receipt_moves = detail.receipt_move_ids
        advance_stage._stage_received_materials_for_execution()
        detail.invalidate_recordset(['receipt_move_ids'])
        self.assertEqual(detail.receipt_move_ids, receipt_moves)
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            2.0,
        )

        # Raw receipt quants must never replace the actual production item in
        # the stage-content display after the manager starts the stage.
        payload_products = self.env['product.product'].browse([
            payload['product'].id
            for payload in production._get_stage_work_location_payloads(
                'priming'
            )
        ])
        self.assertNotIn(raw, payload_products)
        stage_order.invalidate_recordset([
            'production_product_summary', 'stage_batch_qty',
        ])
        self.assertIn(
            production_line.product_id.display_name,
            stage_order.production_product_summary,
        )
        self.assertNotIn(
            raw.display_name,
            stage_order.production_product_summary,
        )
        self.assertAlmostEqual(
            stage_order.stage_batch_qty,
            production_line.product_qty,
        )
        with self.assertRaises(UserError):
            release.sudo().action_return()
        release.invalidate_recordset(['state'])
        self.assertEqual(release.state, 'issued')

    def test_full_receipt_records_custody_without_entering_stage_hall(self):
        production, _line, material_lines, shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production, ('priming',))
        release.sudo().action_issue()

        stage, _result = self._receive_stage(release, 'priming')
        material_lines.invalidate_recordset([
            'move_id', 'warehouse_receipt_confirmed',
            'warehouse_received_qty', 'warehouse_receipt_stage_id',
        ])

        self.assertTrue(stage.receipt_confirmed)
        self.assertEqual(stage.receipt_state, 'full')
        self.assertEqual(stage.received_by_id, self.manager_user)
        self.assertAlmostEqual(stage.receipt_difference_qty, 0.0)
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), shared,
            ),
            4.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, shared,
            ),
            0.0,
        )
        priming_material = material_lines.filtered(
            lambda line: line.stage == 'priming'
        ).ensure_one()
        detail = stage.material_line_ids.ensure_one()
        self.assertFalse(detail.receipt_move_ids)
        self.assertFalse(priming_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(priming_material.warehouse_received_qty, 0.0)
        self.assertFalse(priming_material.warehouse_receipt_stage_id)
        self.assertEqual(
            priming_material.move_id.location_dest_id,
            self._handover_location(),
        )
        with self.assertRaises(UserError):
            stage.with_user(self.manager_user).action_open_receipt_wizard()
        with self.assertRaises(UserError):
            release.sudo().action_return()

    def test_fractional_material_receipt_keeps_move_and_allocation_precision(self):
        production, production_line, material_lines, raw, _unique = (
            self._create_manual_production()
        )
        cubic_uom = self.env.ref(
            'furniture_mrp.furniture_bom_uom_cubic_meter'
        )
        priming_material = material_lines.filtered(
            lambda line: line.stage == 'priming'
        ).ensure_one()
        priming_material.write({
            'qty_needed': 0.108,
            'product_uom_id': cubic_uom.id,
        })
        fractional_lines = priming_material | self.env[
            'furniture.mrp.material.line'
        ].create([
            {
                'production_id': production.id,
                'production_line_id': production_line.id,
                'product_id': raw.id,
                'product_uom_id': cubic_uom.id,
                'qty_needed': 0.060,
                'stage': 'priming',
            },
            {
                'production_id': production.id,
                'production_line_id': production_line.id,
                'product_id': raw.id,
                'product_uom_id': cubic_uom.id,
                'qty_needed': 0.108,
                'stage': 'priming',
            },
        ])
        request = self._create_single_store_request_for_report(
            production, raw,
        )
        request_line = request.material_line_ids.ensure_one()
        request_line.write({
            'product_uom_id': cubic_uom.id,
            'requested_qty': 0.276,
            'available_qty': 0.276,
        })

        request._issue_materials_to_handover()
        request_line.invalidate_recordset(['issued_qty', 'issue_move_ids'])
        issue_move = request_line.issue_move_ids.ensure_one()
        self.assertAlmostEqual(issue_move.quantity, 0.280, places=3)
        self.assertAlmostEqual(request_line.issued_qty, 0.280, places=3)

        # Reproduce the historical 0.280 movement.  Its proportional shares
        # used to persist as 0.110 + 0.061 + 0.110 = 0.281.
        allocations = production._allocate_received_product_qty(
            fractional_lines, 0.280,
        )
        self.assertEqual(
            [allocations[line.id] for line in fractional_lines.sorted('id')],
            [0.110, 0.061, 0.109],
        )
        self.assertAlmostEqual(sum(allocations.values()), 0.280, places=3)

    def test_partial_receipt_records_difference_and_never_tops_up_from_stock(self):
        production, production_line, raw = self._create_recipe_production(4.0)
        raw.standard_price = 10.0
        release = self._create_release(production, ('priming',))
        production.action_start_priming()
        stage_order = production.priming_order_id
        stage_order.worker_ids = [(6, 0, self.worker.ids)]
        release.sudo().action_issue()

        stage, _result = self._receive_stage(
            release,
            'priming',
            quantities={raw.id: 3.0},
            note='المخزن سلّم وحدة أقل من الإذن',
        )
        detail = stage.material_line_ids.ensure_one()
        material = production.material_line_ids.filtered(
            lambda line: line.stage == 'priming'
        ).ensure_one()
        material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty', 'move_id',
        ])

        self.assertEqual(stage.receipt_state, 'partial')
        self.assertAlmostEqual(detail.issued_qty, 4.0)
        self.assertAlmostEqual(detail.received_qty, 3.0)
        self.assertAlmostEqual(detail.receipt_difference_qty, 1.0)
        self.assertFalse(detail.receipt_move_ids)
        self.assertFalse(material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(material.warehouse_received_qty, 0.0)
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), raw,
            ),
            4.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            0.0,
        )

        start_action = stage_order.action_start()
        wizard = self.env[start_action['res_model']].with_context(
            start_action.get('context', {}),
        ).browse(start_action['res_id'])
        wizard.action_start_selected_from_stock()
        production_line.invalidate_recordset(['first_stage_started'])
        detail.invalidate_recordset(['receipt_move_ids'])
        material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
            'move_id',
        ])

        self.assertTrue(production_line.first_stage_started)
        self.assertEqual(len(self._issue_moves(production)), 1)
        self.assertEqual(len(detail.receipt_move_ids), 1)
        self.assertTrue(material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(material.warehouse_received_qty, 3.0)
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), raw,
            ),
            1.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            3.0,
        )

        receipt_moves = detail.receipt_move_ids
        stage._stage_received_materials_for_execution()
        detail.invalidate_recordset(['receipt_move_ids'])
        self.assertEqual(detail.receipt_move_ids, receipt_moves)
        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), raw,
            ),
            1.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            3.0,
        )

        stage_order.action_send_to_quality()
        stage_order.action_approve_quality()
        cost_entry = self.env['furniture.mrp.stage.cost.entry'].search([
            ('production_line_id', '=', production_line.id),
            ('stage', '=', 'priming'),
        ]).ensure_one()
        material.invalidate_recordset(['material_cost'])
        self.assertAlmostEqual(material.material_cost, 30.0)
        self.assertAlmostEqual(cost_entry.stage_material_cost, 30.0)

    def test_stock_repair_cannot_bypass_pending_production_receipt(self):
        production, _line, raw = self._create_recipe_production(4.0)
        release = self._create_release(production, ('priming',))
        release.sudo().action_issue()

        with self.assertRaises(UserError):
            production.action_repair_stock_moves()

        self.assertAlmostEqual(
            self._location_qty(
                production, self._handover_location(), raw,
            ),
            4.0,
        )
        self.assertAlmostEqual(
            self._location_qty(
                production, production.location_priming_wip_id, raw,
            ),
            0.0,
        )

    def test_receipt_rejects_storekeeper_and_quantity_above_issue(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production, ('priming',))
        release.sudo().action_issue()
        stage = release.stage_line_ids.ensure_one()

        with self.assertRaises(AccessError):
            stage.with_user(self.storekeeper).action_open_receipt_wizard()

        action = stage.with_user(self.manager_user).action_open_receipt_wizard()
        wizard = self.env[action['res_model']].with_user(
            self.manager_user
        ).browse(action['res_id'])
        wizard.line_ids.ensure_one().received_qty += 1.0
        wizard.receipt_note = 'اختبار كمية أكبر من المصروف'
        with self.assertRaises(UserError):
            wizard.action_confirm_receipt()
        stage.invalidate_recordset(['receipt_confirmed', 'receipt_state'])
        self.assertFalse(stage.receipt_confirmed)
        self.assertEqual(stage.receipt_state, 'waiting')

    def test_receipt_is_scoped_to_the_supervisors_own_stage(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production, ('priming',))
        release.sudo().action_issue()
        release_stage = release.stage_line_ids.ensure_one()
        priming_supervisor = self._create_stage_supervisor('priming')
        upholstery_supervisor = self._create_stage_supervisor('upholstery')
        received_by_line = {
            line.id: line.issued_qty
            for line in release_stage.material_line_ids
        }

        with self.assertRaises(AccessError):
            release_stage.with_user(
                upholstery_supervisor
            ).action_open_receipt_wizard()
        with self.assertRaises(AccessError):
            release_stage.with_user(
                upholstery_supervisor
            )._confirm_production_receipt(received_by_line)

        action = release_stage.with_user(
            priming_supervisor
        ).action_open_receipt_wizard()
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.advance.material.receipt.wizard',
        )
        release_stage.invalidate_recordset([
            'receipt_confirmed', 'receipt_state',
        ])
        self.assertFalse(release_stage.receipt_confirmed)
        self.assertEqual(release_stage.receipt_state, 'waiting')

    def test_aggregate_release_navigation_is_hidden_from_stage_supervisor(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(
            production, ('priming', 'upholstery')
        )
        release_stage = release.stage_line_ids.filtered(
            lambda stage: stage.stage_code == 'priming'
        ).ensure_one()
        supervisor = self._create_stage_supervisor('priming')

        with self.assertRaises(AccessError):
            release.with_user(supervisor).action_open()

        supervisor_status = self.env[
            'furniture.mrp.store.request'
        ].with_user(supervisor)._advance_release_status_action(
            release_stage.with_user(supervisor)
        )
        self.assertEqual(supervisor_status['type'], 'ir.actions.client')
        self.assertEqual(supervisor_status['tag'], 'display_notification')
        self.assertNotIn('next', supervisor_status['params'])

        manager_action = release.with_user(self.manager_user).action_open()
        self.assertEqual(manager_action['type'], 'ir.actions.act_window')
        self.assertEqual(manager_action['res_model'], release._name)
        self.assertEqual(manager_action['res_id'], release.id)

        manager_status = self.env[
            'furniture.mrp.store.request'
        ].with_user(self.manager_user)._advance_release_status_action(
            release_stage.with_user(self.manager_user)
        )
        self.assertEqual(
            manager_status['params']['next']['res_id'], release.id,
        )

    def test_release_orm_reads_are_limited_to_the_supervisors_stage(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(
            production, ('priming', 'upholstery')
        )
        priming_supervisor = self._create_stage_supervisor('priming')

        Release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(priming_supervisor)
        ReleaseStage = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].with_user(priming_supervisor)
        ReleaseLine = self.env[
            'furniture.mrp.advance.material.release.line'
        ].with_user(priming_supervisor)

        self.assertFalse(Release.search([('id', '=', release.id)]))
        with self.assertRaises(AccessError):
            Release.browse(release.id).read(['name', 'stage_line_ids'])

        visible_stages = ReleaseStage.search([
            ('release_id', '=', release.id),
        ])
        self.assertEqual(visible_stages.mapped('stage_code'), ['priming'])
        visible_lines = ReleaseLine.search([
            ('release_id', '=', release.id),
        ])
        self.assertTrue(visible_lines)
        self.assertEqual(set(visible_lines.mapped('stage_code')), {'priming'})

        worker_release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(self.worker_user)
        worker_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].with_user(self.worker_user)
        worker_lines = self.env[
            'furniture.mrp.advance.material.release.line'
        ].with_user(self.worker_user)
        self.assertFalse(worker_release.search([('id', '=', release.id)]))
        self.assertFalse(worker_stages.search([('release_id', '=', release.id)]))
        self.assertFalse(worker_lines.search([('release_id', '=', release.id)]))

        manager_release = self.env[
            'furniture.mrp.advance.material.release'
        ].with_user(self.manager_user).search([('id', '=', release.id)])
        manager_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].with_user(self.manager_user).search([
            ('release_id', '=', release.id),
        ])
        self.assertEqual(manager_release, release)
        self.assertEqual(
            set(manager_stages.mapped('stage_code')),
            {'priming', 'upholstery'},
        )

    def test_stage_supervisor_can_still_load_scoped_receipt_wizard(self):
        production, _line, _materials, _shared, _unique = (
            self._create_manual_production()
        )
        release = self._create_release(production, ('priming',))
        release.sudo().action_issue()
        priming_supervisor = self._create_stage_supervisor('priming')
        release_stage = release.stage_line_ids.ensure_one().with_user(
            priming_supervisor
        )

        action = release_stage.action_open_receipt_wizard()
        wizard = self.env[action['res_model']].with_user(
            priming_supervisor
        ).browse(action['res_id'])
        payload = wizard.read([
            'production_id', 'release_id', 'stage_code', 'line_ids',
        ])[0]

        self.assertEqual(payload['production_id'][0], production.id)
        self.assertEqual(payload['release_id'][0], release.id)
        self.assertEqual(payload['stage_code'], 'priming')
        self.assertTrue(payload['line_ids'])

    def test_partial_split_relinks_the_already_issued_aggregate_move(self):
        production, production_line, _raw = self._create_recipe_production(4.0)
        release = self._create_release(production, ('priming',))
        release.sudo().action_issue()
        issue_move = self._issue_moves(production).ensure_one()

        remaining_line = production_line._split_for_partial_first_stage(1.5)
        production._refresh_material_lines_for_stage_plan()
        refreshed_lines = production.material_line_ids.filtered(
            lambda line: line.stage == 'priming'
        )

        self.assertTrue(remaining_line)
        self.assertEqual(len(refreshed_lines), 2)
        self.assertAlmostEqual(sum(refreshed_lines.mapped('qty_needed')), 4.0)
        self.assertEqual(set(refreshed_lines.mapped('move_id')), {issue_move})
        self.assertEqual(len(self._issue_moves(production)), 1)
