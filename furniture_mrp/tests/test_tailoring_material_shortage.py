from pathlib import Path

from lxml import etree

from odoo.exceptions import UserError
from odoo.modules.module import get_module_resource
from odoo.tests.common import TransactionCase, new_test_user


class TestTailoringMaterialShortage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.worker_user = new_test_user(
            cls.env,
            login='furniture_tailoring_shortage_worker',
            groups='base.group_user',
        )
        worker_stage = cls.env['furniture.mrp.employee.stage'].search([
            ('code', '=', 'tailoring'),
        ], limit=1)
        if not worker_stage:
            worker_stage = cls.env['furniture.mrp.employee.stage'].create({
                'name': 'Tailoring shortage test',
                'code': 'tailoring',
            })
        cls.worker = cls.env['hr.employee'].create({
            'name': 'Tailoring shortage worker',
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

    def _create_tailoring_case(self, stock_qty=3.0, required_qty=5.0):
        finished = self._new_storable_product(
            'Tailoring shortage finished product',
        )
        raw = self._new_storable_product(
            'Tailoring shortage raw material',
        )
        stock = self.env.ref('stock.stock_location_stock')
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'product_qty': 1.0,
            'state': 'confirmed',
            'location_src_id': stock.id,
            'use_priming': False,
            'use_painting': False,
            'use_carpentry': False,
            'use_bases': False,
            'use_finishing': False,
            'use_tailoring': True,
            'use_upholstery': False,
            'use_packaging': False,
        })
        production_line = self.env[
            'furniture.mrp.production.line'
        ].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'production_id': production.id,
            'sequence': 10,
            'product_id': finished.id,
            'product_qty': 1.0,
            'use_tailoring': True,
            'stage_selection_initialized': True,
        })
        material_line = self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': production_line.id,
            'product_id': raw.id,
            'product_uom_id': raw.uom_id.id,
            'qty_needed': required_qty,
            'stage': 'tailoring',
        })
        production._ensure_stage_locations()
        production.action_start_tailoring()
        stage_order = production.tailoring_order_id
        stage_order.worker_ids = [(6, 0, self.worker.ids)]
        if stock_qty:
            self.env['stock.quant']._update_available_quantity(
                raw, stock, stock_qty,
            )
            self.env['stock.quant'].flush_model([
                'quantity', 'reserved_quantity',
            ])
        return production, production_line, material_line, stage_order, raw

    def _create_direct_request(
        self, production, production_line, stage_order, raw, required_qty=5.0,
    ):
        return self.env['furniture.mrp.store.request'].create({
            'production_id': production.id,
            'stage_code': 'tailoring',
            'stage_order_model': stage_order._name,
            'stage_order_res_id': stage_order.id,
            'stage_order_name': stage_order.name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'requested_by_id': self.env.user.id,
            'assigned_to_id': self.env.user.id,
            'source_location_id': production.location_src_id.id,
            'destination_location_id': production.location_tailoring_wip_id.id,
            'requested_product_summary': production_line.product_id.display_name,
            'payload_json': {
                'production_line_ids': production_line.ids,
            },
            'material_line_ids': [(0, 0, {
                'product_id': raw.id,
                'product_uom_id': raw.uom_id.id,
                'requested_qty': required_qty,
                'available_qty': 3.0,
            })],
        })

    def test_direct_tailoring_request_starts_with_shortage_and_warns(self):
        (
            production, production_line, material_line, stage_order, raw,
        ) = self._create_tailoring_case()
        request = self._create_direct_request(
            production, production_line, stage_order, raw,
        )
        available_for_issue = request._material_issue_available_qty(
            production.location_src_id, raw,
        )
        self.assertAlmostEqual(available_for_issue, 3.0)

        approval_action = request.action_approve()
        detail = request.material_line_ids.ensure_one()
        request.invalidate_recordset([
            'state', 'receipt_state', 'receipt_confirmed',
        ])
        detail.invalidate_recordset(['issued_qty', 'issue_move_ids'])

        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.receipt_state, 'waiting')
        self.assertFalse(request.receipt_confirmed)
        self.assertAlmostEqual(detail.issued_qty, 3.0)
        self.assertAlmostEqual(detail.requested_qty - detail.issued_qty, 2.0)
        self.assertEqual(approval_action['params']['type'], 'warning')
        self.assertTrue(approval_action['params']['sticky'])

        request._confirm_production_receipt({detail.id: detail.issued_qty})
        request.invalidate_recordset(['receipt_state', 'receipt_confirmed'])
        self.assertEqual(request.receipt_state, 'partial')
        self.assertTrue(request.receipt_confirmed)

        start_action = request.action_start_approved()
        stage_order.invalidate_recordset([
            'state', 'has_material_shortage', 'material_shortage_details',
            'tailoring_display_state', 'current_substage',
        ])
        material_line.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
        ])

        self.assertEqual(stage_order.state, 'in_progress')
        self.assertTrue(material_line.warehouse_receipt_confirmed)
        self.assertAlmostEqual(material_line.warehouse_received_qty, 3.0)
        self.assertTrue(stage_order.has_material_shortage)
        self.assertEqual(stage_order.tailoring_display_state, 'material_shortage')
        self.assertEqual(stage_order.current_substage, 'مواد ناقصة')
        self.assertIn(production_line.product_id.display_name, stage_order.material_shortage_details)
        self.assertIn(raw.display_name, stage_order.material_shortage_details)
        self.assertIn('2', stage_order.material_shortage_details)
        self.assertEqual(start_action['tag'], 'display_notification')
        self.assertEqual(start_action['params']['type'], 'warning')
        self.assertTrue(start_action['params']['sticky'])
        self.assertEqual(
            start_action['params']['next']['res_model'],
            stage_order._name,
        )

        dashboard = production.get_stage_dashboard_data(
            stage_code='tailoring',
        )
        payload = next(
            order
            for order in dashboard['orders']
            if order['id'] == production.id
        )
        self.assertTrue(payload['has_material_shortage'])
        self.assertEqual(payload['state_label'], 'مواد ناقصة')
        self.assertIn(raw.display_name, payload['material_shortage_details'])
        self.assertTrue(payload['product_lines'][0]['has_material_shortage'])

        # Completing the technical workflow must not make the user-facing
        # tailoring state look finished while its audited receipt is short.
        stage_order.sudo().write({'state': 'done'})
        stage_order.invalidate_recordset([
            'tailoring_display_state', 'current_substage',
        ])
        production.invalidate_recordset([
            'tailoring_state', 'tailoring_display_state',
        ])
        self.assertEqual(stage_order.tailoring_display_state, 'material_shortage')
        self.assertEqual(stage_order.current_substage, 'مواد ناقصة')
        self.assertEqual(production.tailoring_state, 'done')
        self.assertEqual(
            production.tailoring_display_state,
            'material_shortage',
        )
        completed_dashboard = production.get_stage_dashboard_data(
            stage_code='tailoring',
        )
        completed_payload = next(
            order
            for order in completed_dashboard['orders']
            if order['id'] == production.id
        )
        self.assertEqual(completed_payload['state_label'], 'مواد ناقصة')

        material_line.sudo().write({
            'warehouse_received_qty': material_line.qty_needed,
        })
        stage_order.invalidate_recordset([
            'has_material_shortage', 'tailoring_display_state',
        ])
        production.invalidate_recordset(['tailoring_display_state'])
        self.assertFalse(stage_order.has_material_shortage)
        self.assertEqual(stage_order.tailoring_display_state, 'done')
        self.assertEqual(production.tailoring_display_state, 'done')

    def test_zero_available_tailoring_can_start_without_receipt_wizard(self):
        (
            production, production_line, material_line, stage_order, raw,
        ) = self._create_tailoring_case(stock_qty=0.0)
        request = self._create_direct_request(
            production, production_line, stage_order, raw,
        )
        request.material_line_ids.sudo().write({'available_qty': 0.0})

        request.action_approve()
        detail = request.material_line_ids.ensure_one()
        request.invalidate_recordset([
            'receipt_state', 'receipt_confirmed',
        ])
        detail.invalidate_recordset(['issued_qty', 'issue_move_ids'])

        self.assertAlmostEqual(detail.issued_qty, 0.0)
        self.assertFalse(detail.issue_move_ids)
        self.assertEqual(request.receipt_state, 'partial')
        self.assertTrue(request.receipt_confirmed)

        request.action_start_approved()
        stage_order.invalidate_recordset([
            'state', 'has_material_shortage', 'material_shortage_details',
        ])
        material_line.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
        ])
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertTrue(stage_order.has_material_shortage)
        self.assertTrue(material_line.warehouse_receipt_confirmed)
        self.assertAlmostEqual(material_line.warehouse_received_qty, 0.0)
        self.assertIn('5', stage_order.material_shortage_details)

    def test_non_tailoring_request_still_requires_full_stock(self):
        production, _line, _material, stage_order, raw = (
            self._create_tailoring_case()
        )
        finishing_stage = self.env['furniture.mrp.finishing'].create({
            'name': 'FIN/FULL/STOCK/REQUIRED',
            'production_order_id': production.id,
        })
        request = self.env['furniture.mrp.store.request'].create({
            'production_id': production.id,
            'stage_code': 'finishing',
            'stage_order_model': finishing_stage._name,
            'stage_order_res_id': finishing_stage.id,
            'stage_order_name': finishing_stage.name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'requested_by_id': self.env.user.id,
            'assigned_to_id': self.env.user.id,
            'source_location_id': production.location_src_id.id,
            'destination_location_id': production.location_finishing_wip_id.id,
            'payload_json': {},
            'material_line_ids': [(0, 0, {
                'product_id': raw.id,
                'product_uom_id': raw.uom_id.id,
                'requested_qty': 5.0,
                'available_qty': 3.0,
            })],
        })

        with self.assertRaises(UserError):
            request.action_approve()
        self.assertEqual(request.state, 'pending')
        self.assertFalse(request.material_line_ids.mapped('issue_move_ids'))

    def test_advance_release_also_issues_available_tailoring_materials(self):
        (
            production, _production_line, material_line, stage_order, raw,
        ) = self._create_tailoring_case()
        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_stage_codes(
            production,
            ('tailoring',),
            notify_storekeeper=False,
        )

        issue_action = release.sudo().action_issue()
        release_stage = release.stage_line_ids.ensure_one()
        detail = release_stage.material_line_ids.ensure_one()
        release_stage.invalidate_recordset([
            'state', 'receipt_state', 'receipt_confirmed',
        ])
        detail.invalidate_recordset(['issued_qty', 'move_ids'])

        self.assertEqual(issue_action['params']['type'], 'warning')
        self.assertEqual(release_stage.state, 'issued')
        self.assertEqual(release_stage.receipt_state, 'waiting')
        self.assertFalse(release_stage.receipt_confirmed)
        self.assertAlmostEqual(detail.requested_qty, 5.0)
        self.assertAlmostEqual(detail.issued_qty, 3.0)

        release_stage.sudo()._confirm_production_receipt({
            detail.id: detail.issued_qty,
        })
        release_stage.invalidate_recordset([
            'receipt_state', 'receipt_confirmed',
        ])
        self.assertEqual(release_stage.receipt_state, 'partial')
        self.assertTrue(release_stage.receipt_confirmed)

        stage_order.sudo().action_start()
        stage_order.invalidate_recordset([
            'state', 'has_material_shortage', 'material_shortage_details',
        ])
        material_line.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
        ])
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertTrue(stage_order.has_material_shortage)
        self.assertAlmostEqual(material_line.warehouse_received_qty, 3.0)
        self.assertIn(raw.display_name, stage_order.material_shortage_details)

    def test_tailoring_view_and_dashboard_assets_expose_shortage_state(self):
        view = self.env.ref('furniture_mrp.view_furniture_mrp_tailoring_form')
        arch = self.env[view.model].get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        document = etree.fromstring(arch.encode())

        self.assertEqual(
            len(document.xpath("//field[@name='tailoring_display_state']")),
            1,
        )
        self.assertEqual(
            len(document.xpath("//field[@name='material_shortage_details']")),
            1,
        )
        alerts = document.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' alert-warning ')]"
        )
        self.assertEqual(len(alerts), 1)
        self.assertIn('not has_material_shortage', alerts[0].get('invisible'))

        production_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_form'
        )
        production_arch = self.env[production_view.model].get_view(
            view_id=production_view.id,
            view_type='form',
        )['arch']
        production_document = etree.fromstring(production_arch.encode())
        tailoring_chips = production_document.xpath(
            "//button[@name='action_view_tailoring']"
        )
        self.assertEqual(len(tailoring_chips), 1)
        self.assertEqual(
            tailoring_chips[0].xpath(
                ".//span[contains(concat(' ', normalize-space(@class), ' '), "
                "' o_furniture_stage_chip_state ')]/field/@name"
            ),
            ['tailoring_display_state'],
        )

        js_source = Path(get_module_resource(
            'furniture_mrp', 'static', 'src', 'js',
            'mrp_stage_dashboard.js',
        )).read_text(encoding='utf-8')
        css_source = Path(get_module_resource(
            'furniture_mrp', 'static', 'src', 'css',
            'mrp_stage_dashboard.css',
        )).read_text(encoding='utf-8')
        self.assertIn('values.has_material_shortage', js_source)
        self.assertIn('label: _t("مواد ناقصة")', js_source)
        self.assertIn('.is-shortage', css_source)
