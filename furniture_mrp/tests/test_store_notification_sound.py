from unittest.mock import patch

from pathlib import Path

from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_resource
from odoo.tests.common import TransactionCase, new_test_user


class TestStoreNotificationSound(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.storekeeper = new_test_user(
            cls.env,
            login='furniture_notification_sound_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
        )
        cls.other_storekeeper = new_test_user(
            cls.env,
            login='furniture_notification_sound_other_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
        )
        cls.manager_user = new_test_user(
            cls.env,
            login='furniture_notification_sound_manager',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_manager'
            ),
        )
        cls.regular_user = new_test_user(
            cls.env,
            login='furniture_notification_sound_regular',
            groups='base.group_user',
        )
        worker_stage = cls.env['furniture.mrp.employee.stage'].search([
            ('code', '=', 'finishing'),
        ], limit=1)
        if not worker_stage:
            worker_stage = cls.env['furniture.mrp.employee.stage'].create({
                'name': 'Store Receipt Test Finishing',
                'code': 'finishing',
            })
        cls.worker = cls.env['hr.employee'].create({
            'name': 'Store Receipt Test Worker',
            'user_id': cls.regular_user.id,
            'furniture_mrp_role': 'worker',
            'furniture_mrp_worker_stage_ids': [(6, 0, worker_stage.ids)],
        })

    def _create_stage(self):
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        stage = self.env['furniture.mrp.finishing'].create({
            'name': 'FIN/NOTIFICATION/SOUND',
            'production_order_id': production.id,
        })
        return production, stage

    def _custom_bus_payloads(self, mocked_send):
        return [
            call.args[2]
            for call in mocked_send.call_args_list
            if len(call.args) >= 3
            and call.args[1] == 'furniture_store_notification'
        ]

    def test_bus_payload_has_explicit_sound_flag(self):
        request_model = self.env['furniture.mrp.store.request']
        bus_model = self.env.registry['bus.bus']
        active_user = self.env.ref('base.user_admin')

        with patch.object(bus_model, '_sendone') as mocked_send:
            request_model._send_bus_notification(
                active_user,
                'Audible notification',
                'Request or approval',
                play_sound=True,
            )
            request_model._send_bus_notification(
                active_user,
                'Silent notification',
                'Started or rejected',
            )

        payloads = self._custom_bus_payloads(mocked_send)
        self.assertEqual(len(payloads), 2)
        self.assertTrue(payloads[0]['play_sound'])
        self.assertFalse(payloads[1]['play_sound'])

    def test_handoff_toast_has_durable_accept_and_close_contract(self):
        source = Path(get_module_resource(
            'furniture_mrp',
            'static',
            'src',
            'js',
            'store_request_notification_service.js',
        )).read_text(encoding='utf-8')

        self.assertIn('payload.handoff_event === "pending"', source)
        self.assertIn('sticky: true', source)
        self.assertIn('name: _t("قبول التحويل")', source)
        self.assertIn('"action_accept_handoff_transfer"', source)
        self.assertIn('"action_dismiss_handoff_notification"', source)
        self.assertIn('"furniture_pending_handoff_notifications"', source)
        self.assertNotIn('const openTransfer =', source)
        self.assertNotIn('name: _t("فتح الأمر")', source)

        dashboard_source = Path(get_module_resource(
            'furniture_mrp',
            'static',
            'src',
            'js',
            'mrp_stage_dashboard.js',
        )).read_text(encoding='utf-8')
        dashboard_template = Path(get_module_resource(
            'furniture_mrp',
            'static',
            'src',
            'xml',
            'mrp_stage_dashboard.xml',
        )).read_text(encoding='utf-8')
        self.assertIn('source.pending_handoffs', dashboard_source)
        self.assertIn('async acceptPendingHandoff(handoff)', dashboard_source)
        self.assertIn('"action_accept_handoff_transfer"', dashboard_source)
        self.assertIn('pendingHandoffs.length', dashboard_template)
        self.assertIn('قبول التحويل', dashboard_template)
        self.assertIn(
            'this.stageDashboard.pendingHandoffs = this.stageDashboard.pendingHandoffs.filter(',
            dashboard_source,
        )
        self.assertIn('(item) => item.key !== handoffKey', dashboard_source)

    def test_new_store_request_notification_is_audible(self):
        production, stage = self._create_stage()
        request_model = self.env['furniture.mrp.store.request']
        assigned_storekeeper = request_model._get_storekeeper_user(
            production.company_id,
        )
        assigned_storekeeper.notification_type = 'inbox'

        with patch.object(self.env.registry['bus.bus'], '_sendone') as mocked_send:
            request_model._create_pending_request({
                'production_id': production,
                'stage_code': 'finishing',
                'stage_order_model': stage._name,
                'stage_order_res_id': stage.id,
                'stage_order_name': stage.name,
                'request_kind': 'direct',
                'start_mode': 'direct',
                'requested_product_summary': 'Notification sound product',
                'payload_json': {},
            }, {})

        payloads = self._custom_bus_payloads(mocked_send)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(payloads[0]['play_sound'])
        self.assertEqual(payloads[0]['action_model'], request_model._name)
        request = request_model.browse(payloads[0]['action_res_id'])
        inbox_notification = self.env['mail.notification'].search([
            ('res_partner_id', '=', assigned_storekeeper.partner_id.id),
            ('notification_type', '=', 'inbox'),
            ('mail_message_id.model', '=', request._name),
            ('mail_message_id.res_id', '=', request.id),
        ])
        self.assertEqual(len(inbox_notification), 1)

    def test_store_approval_notification_is_audible(self):
        production, stage = self._create_stage()
        active_user = self.env.ref('base.user_admin')
        request = self.env['furniture.mrp.store.request'].create({
            'production_id': production.id,
            'stage_code': 'finishing',
            'stage_order_model': stage._name,
            'stage_order_res_id': stage.id,
            'stage_order_name': stage.name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'requested_by_id': active_user.id,
            'assigned_to_id': self.storekeeper.id,
            'requested_product_summary': 'Notification sound product',
            'payload_json': {},
        })

        with patch.object(self.env.registry['bus.bus'], '_sendone') as mocked_send:
            request.with_user(self.storekeeper).action_approve()

        payloads = self._custom_bus_payloads(mocked_send)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(payloads[0]['play_sound'])
        self.assertEqual(payloads[0]['action_model'], stage._name)
        self.assertEqual(payloads[0]['action_res_id'], stage.id)

    def test_only_assigned_storekeeper_can_decide_legacy_request(self):
        production, stage = self._create_stage()
        request = self.env['furniture.mrp.store.request'].create({
            'production_id': production.id,
            'stage_code': 'finishing',
            'stage_order_model': stage._name,
            'stage_order_res_id': stage.id,
            'stage_order_name': stage.name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'assigned_to_id': self.storekeeper.id,
            'payload_json': {},
        })

        self.assertTrue(
            request.with_user(self.storekeeper).can_warehouse_decide,
        )
        for user in (
            self.other_storekeeper,
            self.manager_user,
            self.regular_user,
        ):
            with self.subTest(login=user.login):
                self.assertFalse(
                    request.with_user(user).can_warehouse_decide,
                )
                with self.assertRaises(AccessError):
                    request.with_user(user).action_approve()
                with self.assertRaises(AccessError):
                    request.with_user(user).action_reject()
                with self.assertRaises(AccessError):
                    request.with_user(user).write({'state': 'approved'})
                with self.assertRaises(AccessError):
                    self.env['furniture.mrp.store.request'].with_user(user).create({
                        'production_id': production.id,
                        'stage_code': 'finishing',
                        'stage_order_model': stage._name,
                        'stage_order_res_id': stage.id,
                        'request_kind': 'direct',
                        'start_mode': 'direct',
                        'assigned_to_id': user.id,
                    })

        request.with_user(self.storekeeper).action_approve()
        request.invalidate_recordset(['state', 'approved_by_id'])
        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.approved_by_id, self.storekeeper)

    def test_store_approval_waits_for_partial_production_receipt(self):
        production, stage = self._create_stage()
        stage.worker_ids = [(6, 0, self.worker.ids)]
        raw_material = self.env['product.product'].create({
            'name': 'Store receipt partial raw material',
            'type': 'consu',
            'is_storable': True,
        })
        source_location = self.env.ref('stock.stock_location_stock')
        # Keep the request source and the reservation-protection bucket
        # explicit and identical; restored databases may carry another
        # warehouse as the production model's default source.
        production.location_src_id = source_location
        handover_location = self.env.ref(
            'furniture_mrp.location_material_handover'
        )
        destination_location = production.location_finishing_wip_id
        self.env['stock.quant']._update_available_quantity(
            raw_material, source_location, 10.0,
        )
        technical_line = self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'product_id': raw_material.id,
            'product_uom_id': raw_material.uom_id.id,
            'qty_needed': 10.0,
            'stage': 'finishing',
        })
        request = self.env['furniture.mrp.store.request'].create({
            'production_id': production.id,
            'stage_code': 'finishing',
            'stage_order_model': stage._name,
            'stage_order_res_id': stage.id,
            'stage_order_name': stage.name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'requested_by_id': self.manager_user.id,
            'assigned_to_id': self.storekeeper.id,
            'source_location_id': source_location.id,
            'handover_location_id': handover_location.id,
            'destination_location_id': destination_location.id,
            'payload_json': {},
            'material_line_ids': [(0, 0, {
                'product_id': raw_material.id,
                'product_uom_id': raw_material.uom_id.id,
                'requested_qty': 10.0,
                'available_qty': 10.0,
            })],
        })

        # This case exercises receipt custody and stage-start staging.  The
        # independent soft-reservation suite covers issue protection; isolate
        # it here so restored-database reservation rows cannot mask the
        # receipt contract being tested.
        with patch.object(
            self.env.registry['furniture.mrp.production'],
            '_check_material_issue_against_other_reservations',
            return_value=True,
        ):
            request.with_user(self.storekeeper).action_approve()
        request.invalidate_recordset([
            'state', 'receipt_state', 'receipt_confirmed',
        ])
        detail = request.material_line_ids
        detail.invalidate_recordset([
            'issued_qty', 'issue_move_ids', 'received_qty',
        ])
        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.receipt_state, 'waiting')
        self.assertFalse(request.receipt_confirmed)
        self.assertEqual(detail.issued_qty, 10.0)
        self.assertEqual(len(detail.issue_move_ids), 1)
        self.assertEqual(detail.issue_move_ids.location_id, source_location)
        self.assertEqual(
            detail.issue_move_ids.location_dest_id, handover_location,
        )
        stock_qty_after_issue = production._stage_location_product_qty(
            source_location, raw_material,
        )
        with self.assertRaises(UserError):
            request.with_user(self.manager_user).action_start_approved()
        with self.assertRaises(AccessError):
            request.with_user(self.storekeeper).action_open_receipt_wizard()

        action = request.with_user(
            self.manager_user,
        ).action_open_receipt_wizard()
        wizard = self.env[action['res_model']].with_user(
            self.manager_user,
        ).browse(action['res_id'])
        wizard.line_ids.received_qty = 7.0
        wizard.receipt_note = 'Warehouse delivered three units short'
        wizard.action_confirm_receipt()

        request.invalidate_recordset([
            'receipt_state', 'receipt_confirmed', 'received_by_id',
        ])
        detail.invalidate_recordset([
            'received_qty', 'receipt_difference_qty', 'receipt_move_ids',
        ])
        self.assertTrue(request.receipt_confirmed)
        self.assertEqual(request.receipt_state, 'partial')
        self.assertEqual(request.received_by_id, self.manager_user)
        self.assertEqual(detail.received_qty, 7.0)
        self.assertEqual(detail.receipt_difference_qty, 3.0)
        # Production receipt is an accountability acknowledgement only.  The
        # physical handover -> hall move must wait for the actual stage start.
        self.assertFalse(detail.receipt_move_ids)
        self.assertEqual(
            production._stage_location_product_qty(
                handover_location, raw_material,
            ),
            10.0,
        )
        self.assertEqual(
            production._stage_location_product_qty(
                destination_location, raw_material,
            ),
            0.0,
        )
        technical_line.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
            'move_id',
        ])
        self.assertFalse(technical_line.warehouse_receipt_confirmed)
        self.assertEqual(technical_line.warehouse_received_qty, 0.0)

        request.action_start_approved()
        request.invalidate_recordset(['state'])
        technical_line.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
            'move_id',
        ])
        self.assertEqual(request.state, 'started')
        self.assertTrue(technical_line.warehouse_receipt_confirmed)
        self.assertEqual(technical_line.warehouse_received_qty, 7.0)
        self.assertEqual(technical_line.move_id, detail.receipt_move_ids)
        self.assertEqual(len(detail.receipt_move_ids), 1)
        self.assertEqual(
            detail.receipt_move_ids.location_id, handover_location,
        )
        self.assertEqual(
            detail.receipt_move_ids.location_dest_id, destination_location,
        )
        self.assertEqual(
            production._stage_location_product_qty(
                source_location, raw_material,
            ),
            stock_qty_after_issue,
        )
        self.assertEqual(
            production._stage_location_product_qty(
                handover_location, raw_material,
            ),
            3.0,
        )
        self.assertEqual(
            production._stage_location_product_qty(
                destination_location, raw_material,
            ),
            7.0,
        )

        # A retry of the staging hook reuses the exact same receipt move and
        # cannot top the missing three units up from Stock.
        receipt_moves = detail.receipt_move_ids
        request._apply_receipt_to_material_lines(technical_line)
        detail.invalidate_recordset(['receipt_move_ids'])
        self.assertEqual(detail.receipt_move_ids, receipt_moves)
        self.assertEqual(
            production._stage_location_product_qty(
                source_location, raw_material,
            ),
            stock_qty_after_issue,
        )
        self.assertEqual(
            production._stage_location_product_qty(
                handover_location, raw_material,
            ),
            3.0,
        )
        self.assertEqual(
            production._stage_location_product_qty(
                destination_location, raw_material,
            ),
            7.0,
        )
