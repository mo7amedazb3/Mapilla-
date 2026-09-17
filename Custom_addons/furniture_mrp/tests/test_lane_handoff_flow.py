# -*- coding: utf-8 -*-

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user


class TestFurnitureLaneHandoffFlow(TransactionCase):

    BOM_DIMENSIONS = (210.0, 90.0, 82.0)
    CUSTOM_DIMENSIONS = (237.5, 103.25, 88.75)

    STAGE_CODES = (
        'priming',
        'painting',
        'carpentry',
        'bases',
        'finishing',
        'tailoring',
        'upholstery',
        'packaging',
    )
    READY_STAGE_BY_LANE = {
        'frame': 'carpentry',
        'finish': 'finishing',
        'tailoring': 'tailoring',
        'painting': 'painting',
        'upholstery': 'upholstery',
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({
            'name': 'Lane Handoff Finished Product',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        cls.furniture_model = cls.env['furniture.product.model'].create({
            'name': 'Lane Handoff Test Model',
        })
        visible_bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': cls.unit_uom.id,
            'type': 'normal',
            'company_id': cls.company.id,
            'furniture_product_id': cls.product.id,
            'furniture_model_id': cls.furniture_model.id,
            'furniture_width_cm': cls.BOM_DIMENSIONS[0],
            'furniture_depth_cm': cls.BOM_DIMENSIONS[1],
            'furniture_height_cm': cls.BOM_DIMENSIONS[2],
            'use_priming': True,
            'use_painting': True,
            'use_carpentry': True,
            'use_bases': True,
            'use_finishing': True,
            'use_tailoring': True,
            'use_upholstery': True,
            'use_packaging': True,
        })
        cls.bom = cls.env['mrp.bom']._find_furniture_normal_recipe(
            cls.product,
            cls.furniture_model,
            cls.company,
        )
        if not cls.bom or cls.bom.furniture_parent_bom_id != visible_bom:
            raise AssertionError('The exact handoff test recipe was not created.')
        cls.bases_supervisor_user = new_test_user(
            cls.env,
            login='lane.handoff.bases.supervisor@example.test',
            groups='base.group_user',
        )
        cls.upholstery_supervisor_user = new_test_user(
            cls.env,
            login='lane.handoff.upholstery.supervisor@example.test',
            groups='base.group_user',
        )
        employee_stages = cls.env['furniture.mrp.employee.stage'].search([
            ('code', 'in', ('bases', 'upholstery')),
        ])
        bases_stage = employee_stages.filtered(
            lambda stage: stage.code == 'bases'
        ).ensure_one()
        upholstery_stage = employee_stages.filtered(
            lambda stage: stage.code == 'upholstery'
        ).ensure_one()
        cls.env['hr.employee'].create({
            'name': 'Lane Handoff Bases Supervisor',
            'company_id': cls.company.id,
            'user_id': cls.bases_supervisor_user.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, bases_stage.ids)],
        })
        cls.env['hr.employee'].create({
            'name': 'Lane Handoff Upholstery Supervisor',
            'company_id': cls.company.id,
            'user_id': cls.upholstery_supervisor_user.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [
                (6, 0, upholstery_stage.ids),
            ],
        })

    def setUp(self):
        super().setUp()
        self._production_sequence = 0

    def _create_lane_production(
        self,
        lane,
        quantity=1.0,
        state='draft',
        dimensions=None,
        inherited_material_cost=0.0,
        inherited_labor_cost=0.0,
    ):
        self._production_sequence += 1
        width_cm, depth_cm, height_cm = dimensions or self.BOM_DIMENSIONS
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).create({
            'name': 'HANDOFF/%s/%s' % (
                self._testMethodName,
                self._production_sequence,
            ),
            'company_id': self.company.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.furniture_model.id,
            'product_qty': quantity,
            'bom_id': self.bom.id,
            'width_cm': width_cm,
            'depth_cm': depth_cm,
            'height_cm': height_cm,
            'production_lane': lane,
            'stage_plan_mode': 'custom',
            'state': state,
        })
        line = self.env['furniture.mrp.production.line'].with_context(
            furniture_preserve_explicit_bom=True,
            furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_running_line_initialization=True,
        ).create({
            'production_id': production.id,
            'sequence': 10,
            'product_id': self.product.id,
            'furniture_order_model_id': self.furniture_model.id,
            'product_qty': quantity,
            'bom_id': self.bom.id,
            'width_cm': width_cm,
            'depth_cm': depth_cm,
            'height_cm': height_cm,
            'inherited_material_cost_per_unit': inherited_material_cost,
            'inherited_labor_cost_per_unit': inherited_labor_cost,
            'stage_selection_initialized': True,
        })
        return production, line

    def _create_ready_output_for_production(
        self,
        production,
        line,
        lane=None,
        quantity=None,
    ):
        lane = lane or production.production_lane
        quantity = line.product_qty if quantity is None else quantity
        production.ensure_one()
        line.ensure_one()
        self.assertEqual(line.production_id, production)
        self.assertEqual(production.production_lane, lane)
        final_product = production._furniture_line_final_product(line)
        wip_product = self.env[
            'product.product'
        ]._furniture_get_or_create_lane_wip_product(
            self.company,
            lane,
            final_product,
            self.furniture_model,
        )
        ready_location = production._stage_storage_location(
            self.READY_STAGE_BY_LANE[lane]
        )
        ready_move = production._create_internal_move(
            production._get_production_location(),
            ready_location,
            'Lane handoff test output',
            move_type='finished_product',
            product=wip_product,
            quantity=quantity,
            uom=wip_product.uom_id,
            source_production_line=line,
            price_unit=10.0,
        )
        output = production._ensure_lane_outputs(line).ensure_one()
        self.assertEqual(output.ready_move_id, ready_move)
        output._furniture_refresh_matching_groups()
        return output, production, line

    def _create_ready_output(
        self,
        lane,
        quantity=1.0,
        dimensions=None,
        inherited_material_cost=0.0,
        inherited_labor_cost=0.0,
    ):
        production, line = self._create_lane_production(
            lane,
            quantity=quantity,
            state='confirmed',
            dimensions=dimensions,
            inherited_material_cost=inherited_material_cost,
            inherited_labor_cost=inherited_labor_cost,
        )
        return self._create_ready_output_for_production(
            production,
            line,
            lane=lane,
            quantity=quantity,
        )

    def test_stage_dashboard_hides_stage_less_physical_stock_source(self):
        _output, production, _line = self._create_ready_output(
            'finish',
            quantity=3.0,
        )

        payload = self.env[
            'furniture.mrp.production'
        ].get_stage_dashboard_data('bases')

        self.assertNotIn(
            production.id,
            {order['id'] for order in payload['orders']},
        )

    def _packaging_order_from_ready_outputs(self, quantity=1.0):
        upholstery, _production, _line = self._create_ready_output(
            'upholstery',
            quantity,
        )
        self.assertFalse(upholstery.handoff_ids.filtered(
            lambda handoff: (
                handoff.downstream_production_id.production_lane == 'packaging'
            )
        ))
        painting, _production, _line = self._create_ready_output(
            'painting',
            quantity,
        )
        packaging = (upholstery | painting).handoff_ids.mapped(
            'downstream_production_id'
        ).filtered(
            lambda production: production.production_lane == 'packaging'
        )
        return packaging.ensure_one(), upholstery

    def test_packaging_requires_manual_upholstery_quality_without_moving_stock(self):
        packaging, _upholstery = self._packaging_order_from_ready_outputs(quantity=2.0)
        user = new_test_user(self.env, login='manual.quality.packaging@example.test', groups='base.group_user')
        employee_stage = self.env['furniture.mrp.employee.stage'].search([('code', '=', 'packaging')])
        supervisor = self.env['hr.employee'].create({
            'name': 'Manual Quality Packaging Supervisor', 'user_id': user.id,
            'company_id': self.company.id, 'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, employee_stage.ids)],
        })
        if packaging.state == 'draft':
            packaging.action_confirm()
        stage = packaging._create_stage_order(
            'furniture.mrp.packaging', 'PKG', {'foreman_id': supervisor.id},
        )
        packaging.write({'packaging_order_id': stage.id})
        current = packaging.with_user(user)
        rows = packaging._incoming_upholstery_quality_rows()
        self.assertEqual(len(rows), 1)
        key = rows[0]['key']
        moves = packaging.upstream_handoff_ids.transfer_move_id
        with self.assertRaises(AccessError):
            packaging.with_user(self.upholstery_supervisor_user).action_review_handoff_quality(key, 'pass')
        with self.assertRaises(UserError):
            current.action_accept_handoff_transfer()
        with self.assertRaisesRegex(UserError, 'جودة الكسوة'):
            stage.with_user(user).action_start()
        self.assertFalse(packaging._packaging_upholstery_quality_ready())
        current.action_review_handoff_quality(key, 'reject')
        with self.assertRaises(UserError):
            current.action_accept_handoff_transfer()
        with self.assertRaisesRegex(UserError, 'جودة الكسوة'):
            stage.with_user(user).action_start()
        current.action_review_handoff_quality(key, 'pass')
        self.assertTrue(packaging._packaging_upholstery_quality_ready())
        self.assertTrue(all(move.state == 'assigned' for move in moves))
        self.assertEqual(set(packaging.upstream_handoff_ids.mapped('state')), {'reserved'})
        self.assertEqual(packaging.handoff_quality_reviews[key]['user_id'], user.id)
        current.action_accept_handoff_transfer()
        self.assertTrue(all(move.state == 'done' for move in moves))
        self.assertEqual(set(packaging.upstream_handoff_ids.mapped('state')), {'consumed'})
        current.action_accept_handoff_transfer()
        self.assertEqual(packaging.upstream_handoff_ids.transfer_move_id, moves)
        self.assertTrue(packaging._packaging_upholstery_quality_ready())

        # Accepted stock alone cannot bypass missing, rejected or stale QC.
        valid_reviews = dict(packaging.handoff_quality_reviews)
        for reviews in (
            {},
            {key: {**valid_reviews[key], 'state': 'reject'}},
            {key: {**valid_reviews[key], 'signature': [0, 2.0, 0]}},
        ):
            packaging.write({'handoff_quality_reviews': reviews})
            with self.assertRaisesRegex(UserError, 'جودة الكسوة'):
                stage.with_user(user).with_context(
                    furniture_storekeeper_approval_bypass=True,
                    furniture_skip_stage_start_prompt=True,
                ).action_start()
            with self.assertRaisesRegex(UserError, 'جودة الكسوة'):
                packaging._move_stage_materials_for_lines(
                    'packaging', packaging.production_line_ids,
                )
            self.assertEqual(stage.state, 'pending')
            self.assertFalse(stage.date_start)
            self.assertTrue(all(move.state == 'done' for move in moves))

        packaging.write({'handoff_quality_reviews': valid_reviews})
        stage._add_stage_active_lines(packaging.production_line_ids)
        stage.with_user(user).sudo().with_context(
            furniture_storekeeper_approval_bypass=True,
            furniture_skip_stage_start_prompt=True,
            furniture_skip_material_move=True,
        ).action_start()
        self.assertEqual(stage.state, 'in_progress')
        self.assertTrue(stage.date_start)

    def test_packaging_without_upholstery_review_cannot_start(self):
        packaging, line = self._create_lane_production('packaging', state='confirmed')
        stage = packaging._create_stage_order('furniture.mrp.packaging', 'PKG')
        self.assertFalse(packaging._packaging_upholstery_quality_ready(line))
        with self.assertRaisesRegex(UserError, 'جودة الكسوة'):
            stage.with_context(
                furniture_storekeeper_approval_bypass=True,
                furniture_skip_stage_start_prompt=True,
            ).action_start()
        self.assertEqual(stage.state, 'pending')

    def test_painting_and_packaging_routes_are_exact(self):
        painting, painting_line = self._create_lane_production('painting')
        packaging, packaging_line = self._create_lane_production('packaging')

        self.assertEqual(painting._required_stage_codes(), ['painting'])
        self.assertEqual(
            painting_line._selected_stage_codes(),
            ['painting'],
        )
        self.assertFalse(painting._is_material_only_stage('painting'))
        self.assertEqual(packaging._required_stage_codes(), ['packaging'])
        self.assertEqual(
            packaging_line._selected_stage_codes(),
            ['packaging'],
        )

    def test_painting_completion_creates_physical_lane_output(self):
        output, production, line = self._create_ready_output(
            'painting',
            quantity=2.0,
        )

        self.assertEqual(output.lane, 'painting')
        self.assertEqual(output.production_id, production)
        self.assertEqual(output.production_line_id, line)
        self.assertEqual(output.wip_product_id.furniture_wip_lane, 'painting')
        self.assertEqual(output.origin_receipt_move_id.state, 'done')
        self.assertEqual(output.source_location_id, production.location_painting_id)
        self.assertEqual(output.qty_ready, 2.0)
        self.assertEqual(output.physical_available_qty, 2.0)

    def test_replayed_painting_completion_preserves_consumed_output_move(self):
        packaging, _upholstery = (
            self._packaging_order_from_ready_outputs(quantity=2.0)
        )
        self.assertIs(packaging._furniture_consume_required_handoffs(), True)
        painting_handoff = packaging.upstream_handoff_ids.filtered(
            lambda handoff: handoff.role == 'painting'
        ).ensure_one()
        output = painting_handoff.output_id
        production = output.production_id
        line = output.production_line_id
        original_ready_move = output.ready_move_id
        original_unit_cost = output.unit_cost
        line.with_context(
            furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({'inherited_material_cost_per_unit': 37.0})
        move_domain = [
            ('state', '=', 'done'),
            ('origin', '=', production.name),
            ('product_id', '=', output.wip_product_id.id),
            ('location_dest_id', '=', output.source_location_id.id),
            '|',
            ('furniture_source_production_line_id', '=', line.id),
            ('furniture_source_production_line_ids', 'in', [line.id]),
        ]
        moves_before = self.env['stock.move'].search(move_domain)

        repeated_moves = production._move_stage_work_to_stock(
            'furniture.mrp.painting', production_lines=line,
        )
        refreshed_output = production._ensure_lane_outputs(line).ensure_one()

        self.assertFalse(repeated_moves)
        self.assertEqual(
            self.env['stock.move'].search(move_domain),
            moves_before,
        )
        self.assertEqual(refreshed_output, output)
        self.assertEqual(refreshed_output.ready_move_id, original_ready_move)
        self.assertNotEqual(refreshed_output.unit_cost, original_unit_cost)
        self.assertAlmostEqual(refreshed_output.unit_cost, 37.0)
        self.assertAlmostEqual(
            painting_handoff.downstream_line_id.
            inherited_material_cost_per_unit,
            37.0,
        )
        self.assertEqual(painting_handoff.state, 'consumed')

    def test_finish_cannot_start_without_ready_frame_output(self):
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )

        with self.assertRaisesRegex(UserError, 'رصيد جاهز'):
            finish.action_start_bases()
        self.assertFalse(finish.upstream_handoff_ids)

    def test_creating_finish_stage_reserves_without_consuming_handoff(self):
        self._create_ready_output('frame', quantity=1.0)
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )

        finish.action_start_bases()
        handoff = finish.upstream_handoff_ids.ensure_one()
        self.assertTrue(finish.bases_order_id)
        self.assertEqual(handoff.state, 'reserved')
        self.assertEqual(handoff.transfer_move_id.state, 'assigned')

    def test_product_batch_first_start_persists_handoff_for_acceptance(self):
        self._create_ready_output('frame', quantity=1.0)
        finish, line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        Batch = self.env['furniture.mrp.stage.product.batch']
        identity = Batch._identity_values_from_line(line)
        batch = Batch.create({
            'token': 'handoff-start-%s' % self._testMethodName,
            'company_id': self.company.id,
            'stage_code': 'bases',
            'identity_key': identity['identity_key'],
            'product_id': identity['product'].id,
            'model_id': identity['model'].id,
            'bom_id': identity['bom'].id,
            'dimension_label': identity['dimension_label'],
            'uom_id': identity['uom'].id,
            'production_line_ids': [(6, 0, line.ids)],
            'planned_qty': 1.0,
            'state': 'ready',
            'member_ids': [(0, 0, {
                'production_line_id': line.id,
                'qty_snapshot': line.product_qty,
                'identity_key': identity['identity_key'],
            })],
        })

        # Material-release validation is outside this regression's scope.  The
        # important boundary is that the first start attempt returns normally,
        # so the handoff and notification are not rolled back by the start
        # guard in the same RPC transaction.
        with patch.object(
            type(batch), '_preflight_start', return_value=True,
        ):
            result = batch._start_batch()

        handoff = finish.upstream_handoff_ids.ensure_one()
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], 'ready')
        self.assertFalse(finish.bases_order_id)
        self.assertEqual(handoff.state, 'reserved')
        self.assertEqual(handoff.transfer_move_id.state, 'assigned')
        self.assertTrue(finish.handoff_notification_sent_at)

    def test_reservation_sends_audible_sticky_alert_to_destination_supervisor(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )

        with patch.object(
            self.env.registry['bus.bus'],
            '_sendone',
        ) as mocked_send:
            handoff = finish._furniture_reserve_required_handoffs(
                source_outputs_by_lane={'frame': frame},
            ).ensure_one()

        payloads = [
            call.args[2]
            for call in mocked_send.call_args_list
            if len(call.args) >= 3
            and call.args[0] == self.bases_supervisor_user.partner_id
            and call.args[1] == 'furniture_store_notification'
        ]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['handoff_event'], 'pending')
        self.assertEqual(payloads[0]['handoff_production_id'], finish.id)
        self.assertEqual(payloads[0]['handoff_stage_code'], 'bases')
        self.assertTrue(payloads[0]['sticky'])
        self.assertTrue(payloads[0]['play_sound'])
        self.assertFalse(payloads[0]['action_model'])
        self.assertFalse(payloads[0]['action_res_id'])
        self.assertEqual(handoff.state, 'reserved')
        self.assertEqual(handoff.transfer_move_id.state, 'assigned')

    def test_closing_alert_does_not_move_stock_and_is_durable_per_user(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        handoff = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        ).ensure_one()
        pending_model = self.env['furniture.mrp.production'].with_user(
            self.bases_supervisor_user
        )
        self.assertIn(
            finish.id,
            [
                payload['handoff_production_id']
                for payload in pending_model.furniture_pending_handoff_notifications()
            ],
        )

        finish.with_user(
            self.bases_supervisor_user
        ).action_dismiss_handoff_notification()
        handoff.invalidate_recordset(['state', 'transfer_move_id'])
        supervisor_finish = finish.with_user(self.bases_supervisor_user)

        self.assertEqual(handoff.state, 'reserved')
        self.assertEqual(handoff.transfer_move_id.state, 'assigned')
        self.assertTrue(supervisor_finish.handoff_transfer_pending)
        self.assertTrue(supervisor_finish.handoff_can_current_user_accept)
        self.assertNotIn(
            finish.id,
            [
                payload['handoff_production_id']
                for payload in pending_model.furniture_pending_handoff_notifications()
            ],
        )

    def test_only_destination_supervisor_can_accept_and_start_waits(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, finish_line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        handoff = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        ).ensure_one()

        with self.assertRaisesRegex(UserError, 'بانتظار قبول مشرف'):
            finish._furniture_require_accepted_handoffs(finish_line)
        with self.assertRaises(AccessError):
            finish.with_user(
                self.upholstery_supervisor_user
            ).action_accept_handoff_transfer()

        result = finish.with_user(
            self.bases_supervisor_user
        ).action_accept_handoff_transfer()
        handoff.invalidate_recordset(['state', 'transfer_move_id'])
        finish.invalidate_recordset([
            'handoff_accepted_by_id',
            'handoff_accepted_at',
        ])

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(handoff.state, 'consumed')
        self.assertEqual(handoff.transfer_move_id.state, 'done')
        self.assertEqual(
            finish.handoff_accepted_by_id,
            self.bases_supervisor_user,
        )
        self.assertTrue(finish.handoff_accepted_at)
        self.assertTrue(
            finish._furniture_require_accepted_handoffs(finish_line)
        )

        # An acceptance retry is idempotent and cannot move a second unit.
        finish.with_user(
            self.bases_supervisor_user
        ).action_accept_handoff_transfer()
        self.assertEqual(len(finish.upstream_handoff_ids), 1)

    def test_manager_adds_stage_warning_and_non_manager_cannot_open_wizard(self):
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )

        with self.assertRaises(AccessError):
            finish.with_user(
                self.bases_supervisor_user
            ).action_open_stage_warning_wizard()

        admin = self.env.ref('base.user_admin')
        action = finish.with_user(admin).action_open_stage_warning_wizard()
        wizard = self.env[action['res_model']].with_user(admin).create({
            'production_id': finish.id,
            'stage': 'bases',
            'message': 'راجع المقاس قبل بدء التشغيل.',
        })
        notification = wizard.with_user(admin).action_add_warning()

        warning = finish.stage_warning_ids.ensure_one()
        self.assertEqual(warning.stage, 'bases')
        self.assertEqual(warning.message, 'راجع المقاس قبل بدء التشغيل.')
        self.assertEqual(notification['tag'], 'display_notification')

    def test_lane_acceptance_shows_only_matching_stage_warning_once(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        self.env['furniture.mrp.production.stage.warning'].sudo().create([
            {
                'production_id': finish.id,
                'stage': 'bases',
                'message': 'تحذير القواعد المطلوب.',
            },
            {
                'production_id': finish.id,
                'stage': 'finishing',
                'message': 'لا يظهر عند قبول القواعد.',
            },
        ])
        finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        )

        with patch.object(
            self.env.registry['bus.bus'],
            '_sendone',
        ) as mocked_send:
            finish.with_user(
                self.bases_supervisor_user
            ).action_accept_handoff_transfer()

        warning_payloads = [
            call.args[2]
            for call in mocked_send.call_args_list
            if len(call.args) >= 3
            and call.args[0] == self.bases_supervisor_user.partner_id
            and call.args[1] == 'furniture_store_notification'
            and call.args[2].get('stage_acceptance_warning')
        ]
        self.assertEqual(len(warning_payloads), 1)
        self.assertEqual(
            warning_payloads[0]['stage_acceptance_warning_stage'],
            'bases',
        )
        self.assertIn('تحذير القواعد المطلوب', warning_payloads[0]['message'])
        self.assertNotIn('لا يظهر عند قبول القواعد', warning_payloads[0]['message'])

        with patch.object(
            self.env.registry['bus.bus'],
            '_sendone',
        ) as mocked_retry_send:
            finish.with_user(
                self.bases_supervisor_user
            ).action_accept_handoff_transfer()
        self.assertFalse(any(
            len(call.args) >= 3
            and call.args[2].get('stage_acceptance_warning')
            for call in mocked_retry_send.call_args_list
        ))

    def test_internal_stage_acceptance_shows_target_stage_warning(self):
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        self.env['furniture.mrp.production.stage.warning'].sudo().create({
            'production_id': finish.id,
            'stage': 'bases',
            'message': 'تنبيه التحويل الداخلي.',
        })
        handoff = self.env[
            'furniture.mrp.stage.transfer.handoff'
        ].sudo().create({
            'production_id': finish.id,
            'source_stage': 'priming',
            'target_stage': 'bases',
        })

        with patch.object(
            self.env.registry['bus.bus'],
            '_sendone',
        ) as mocked_send:
            result = finish.with_user(
                self.bases_supervisor_user
            ).action_accept_handoff_transfer(stage_handoff_id=handoff.id)

        handoff.invalidate_recordset(['state'])
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(handoff.state, 'accepted')
        warning_payloads = [
            call.args[2]
            for call in mocked_send.call_args_list
            if len(call.args) >= 3
            and call.args[0] == self.bases_supervisor_user.partner_id
            and call.args[1] == 'furniture_store_notification'
            and call.args[2].get('stage_acceptance_warning')
        ]
        self.assertEqual(len(warning_payloads), 1)
        self.assertIn('تنبيه التحويل الداخلي', warning_payloads[0]['message'])

    def test_production_form_keeps_fallback_accept_button(self):
        arch = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_form'
        )._get_combined_arch()
        buttons = arch.xpath(
            ".//button[@name='action_accept_handoff_transfer']"
        )

        self.assertEqual(len(buttons), 1)
        self.assertEqual(
            buttons[0].get('invisible'),
            'not handoff_transfer_pending or not handoff_can_current_user_accept',
        )
        warning_buttons = arch.xpath(
            ".//button[@name='action_open_stage_warning_wizard']"
        )
        self.assertEqual(len(warning_buttons), 1)
        self.assertEqual(
            warning_buttons[0].get('groups'),
            'furniture_mrp.group_furniture_mrp_manager',
        )

    def test_reserved_handoff_blocks_manual_downstream_done(self):
        self._create_ready_output('frame', quantity=1.0)
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        finish._furniture_reserve_required_handoffs()

        with self.assertRaisesRegex(UserError, 'ما زالت تحويلات'):
            finish.action_mark_done()

        self.assertEqual(finish.state, 'confirmed')
        self.assertEqual(finish.upstream_handoff_ids.state, 'reserved')
        self.assertEqual(
            finish.upstream_handoff_ids.transfer_move_id.state,
            'assigned',
        )

    def test_active_outgoing_handoff_blocks_source_cancellation(self):
        frame, frame_production, _line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        handoff = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        ).ensure_one()

        with self.assertRaisesRegex(UserError, 'تحويلات مراحل صادرة'):
            frame_production.action_cancel()

        self.assertEqual(frame_production.state, 'confirmed')
        self.assertEqual(handoff.state, 'reserved')
        self.assertEqual(handoff.transfer_move_id.state, 'assigned')

        # Cancelling the downstream owner releases the reservation and restores
        # the normal source-cancellation path without deleting the audit row.
        finish.action_cancel()
        handoff.invalidate_recordset(['state', 'transfer_move_id'])
        frame_production.action_cancel()
        self.assertEqual(handoff.state, 'cancelled')
        self.assertEqual(handoff.transfer_move_id.state, 'cancel')
        self.assertEqual(frame_production.state, 'cancelled')

    def test_selected_line_consumes_only_its_own_handoff(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=2.0,
        )
        finish, first_line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        second_line = first_line.with_context(
            furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_mps_replan=True,
            furniture_preserve_explicit_bom=True,
        ).copy({'sequence': 20})
        handoffs = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        )
        self.assertEqual(len(handoffs), 2)

        finish._furniture_consume_required_handoffs(first_line)
        handoffs.invalidate_recordset(['state'])
        self.assertEqual(
            handoffs.filtered(
                lambda handoff: handoff.downstream_line_id == first_line
            ).state,
            'consumed',
        )
        self.assertEqual(
            handoffs.filtered(
                lambda handoff: handoff.downstream_line_id == second_line
            ).state,
            'reserved',
        )

    def test_frame_is_reserved_then_consumed_by_finish_once(self):
        frame, frame_production, _line = self._create_ready_output(
            'frame',
            quantity=2.0,
        )
        finish, _line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )

        handoff = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        ).ensure_one()
        frame.invalidate_recordset()
        self.assertEqual(handoff.role, 'frame')
        self.assertEqual(handoff.state, 'reserved')
        self.assertEqual(handoff.quantity, 1.0)
        self.assertEqual(handoff.transfer_move_id.state, 'assigned')
        self.assertEqual(frame.reserved_qty, 1.0)
        self.assertEqual(frame.available_qty, 1.0)

        # A retry while the reservation is still open must return the same
        # ledger row and must not create another stock move.
        retry_handoff = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        ).ensure_one()
        self.assertEqual(retry_handoff, handoff)
        self.assertEqual(len(finish.upstream_handoff_ids), 1)
        self.assertEqual(
            self.env['furniture.mrp.lane.handoff'].search_count([
                ('downstream_production_id', '=', finish.id),
                ('role', '=', 'frame'),
            ]),
            1,
        )
        self.assertEqual(
            self.env['stock.move'].search_count([
                ('furniture_lane_handoff_id', '=', handoff.id),
            ]),
            1,
        )

        self.assertIs(finish._furniture_consume_required_handoffs(), True)
        handoff.invalidate_recordset(['state'])
        frame.invalidate_recordset()
        self.assertEqual(handoff.state, 'consumed')
        self.assertEqual(handoff.transfer_move_id.state, 'done')
        self.assertEqual(frame.assembled_qty, 1.0)
        self.assertEqual(frame.available_qty, 1.0)
        self.assertEqual(frame_production.state, 'confirmed')

        # Retrying the handoff is idempotent: it neither reserves nor moves a
        # second unit for the same downstream line.
        self.assertIs(finish._furniture_consume_required_handoffs(), True)
        self.assertEqual(len(finish.upstream_handoff_ids), 1)
        self.assertEqual(finish.upstream_handoff_ids.transfer_move_id, handoff.transfer_move_id)

    def test_inherited_costs_flow_and_aggregate_across_handoffs(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=2.0,
            inherited_material_cost=30.0,
            inherited_labor_cost=12.0,
        )

        finish = self.env[
            'furniture.mrp.production'
        ]._furniture_create_handoff_lane_order(
            'finish',
            {'frame': frame},
            2.0,
        ).ensure_one()
        finish_line = finish.production_line_ids.ensure_one()
        self.assertAlmostEqual(
            finish_line.inherited_material_cost_per_unit,
            30.0,
        )
        self.assertAlmostEqual(
            finish_line.inherited_labor_cost_per_unit,
            12.0,
        )

        finish.write({'state': 'confirmed'})
        self.assertIs(finish._furniture_consume_required_handoffs(), True)
        finish_output, _production, _line = (
            self._create_ready_output_for_production(
                finish,
                finish_line,
                lane='finish',
            )
        )
        tailoring, _tailoring_production, _tailoring_line = (
            self._create_ready_output(
                'tailoring',
                quantity=2.0,
                inherited_material_cost=8.0,
                inherited_labor_cost=4.0,
            )
        )
        painting, _painting_production, _painting_line = (
            self._create_ready_output(
                'painting',
                quantity=2.0,
                inherited_material_cost=5.0,
                inherited_labor_cost=3.0,
            )
        )

        upholstery = (finish_output | tailoring)._furniture_auto_create_handoff_orders(
            'upholstery',
            ('finish', 'tailoring'),
            company=self.company,
            final_product=finish_output.final_product_id,
            furniture_model=self.furniture_model,
            bom=self.bom,
        ).ensure_one()
        upholstery_line = upholstery.production_line_ids.ensure_one()
        self.assertAlmostEqual(
            upholstery_line.inherited_material_cost_per_unit,
            38.0,
        )
        self.assertAlmostEqual(
            upholstery_line.inherited_labor_cost_per_unit,
            16.0,
        )

        upholstery.write({'state': 'confirmed'})
        self.assertIs(
            upholstery._furniture_consume_required_handoffs(),
            True,
        )
        upholstery_output, _production, _line = (
            self._create_ready_output_for_production(
                upholstery,
                upholstery_line,
                lane='upholstery',
            )
        )
        packaging = upholstery_output.handoff_ids.mapped(
            'downstream_production_id'
        ).filtered(
            lambda production: production.production_lane == 'packaging'
        ).ensure_one()
        packaging_line = packaging.production_line_ids.ensure_one()
        self.assertAlmostEqual(
            packaging_line.inherited_material_cost_per_unit,
            43.0,
        )
        self.assertAlmostEqual(
            packaging_line.inherited_labor_cost_per_unit,
            19.0,
        )

    def test_custom_dimensions_survive_automatic_handoffs(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
            dimensions=self.CUSTOM_DIMENSIONS,
        )
        finish = self.env[
            'furniture.mrp.production'
        ]._furniture_create_handoff_lane_order(
            'finish',
            {'frame': frame},
            1.0,
        ).ensure_one()
        finish_line = finish.production_line_ids.ensure_one()

        self.assertEqual(
            (finish.width_cm, finish.depth_cm, finish.height_cm),
            self.CUSTOM_DIMENSIONS,
        )
        self.assertEqual(
            (
                finish_line.width_cm,
                finish_line.depth_cm,
                finish_line.height_cm,
            ),
            self.CUSTOM_DIMENSIONS,
        )

        finish.write({'state': 'confirmed'})
        self.assertIs(finish._furniture_consume_required_handoffs(), True)
        finish_output, _production, _line = (
            self._create_ready_output_for_production(
                finish,
                finish_line,
                lane='finish',
            )
        )
        tailoring, _tailoring_production, _tailoring_line = (
            self._create_ready_output(
                'tailoring',
                quantity=1.0,
                dimensions=self.CUSTOM_DIMENSIONS,
            )
        )
        painting, _painting_production, _painting_line = (
            self._create_ready_output(
                'painting',
                quantity=1.0,
                dimensions=self.CUSTOM_DIMENSIONS,
            )
        )
        self.assertEqual(frame.final_product_id, finish_output.final_product_id)
        self.assertEqual(frame.final_product_id, tailoring.final_product_id)
        self.assertEqual(frame.final_product_id, painting.final_product_id)

        upholstery = (finish_output | tailoring)._furniture_auto_create_handoff_orders(
            'upholstery',
            ('finish', 'tailoring'),
            company=self.company,
            final_product=finish_output.final_product_id,
            furniture_model=self.furniture_model,
            bom=self.bom,
        ).ensure_one()
        upholstery_line = upholstery.production_line_ids.ensure_one()
        self.assertEqual(
            (
                upholstery.width_cm,
                upholstery.depth_cm,
                upholstery.height_cm,
            ),
            self.CUSTOM_DIMENSIONS,
        )
        self.assertEqual(
            (
                upholstery_line.width_cm,
                upholstery_line.depth_cm,
                upholstery_line.height_cm,
            ),
            self.CUSTOM_DIMENSIONS,
        )
        self.assertEqual(
            upholstery._furniture_line_final_product(upholstery_line),
            frame.final_product_id,
        )

    def test_switching_draft_order_to_upholstery_forces_hall(self):
        production, line = self._create_lane_production('frame')
        historical_store = self.env.ref(
            'furniture_mrp.location_stage_upholstery'
        )
        upholstery_hall = self.env.ref(
            'furniture_mrp.location_stage_upholstery_wip'
        )

        production.write({
            'production_lane': 'upholstery',
            'location_upholstery_id': historical_store.id,
            'location_upholstery_wip_id': historical_store.id,
        })
        production.invalidate_recordset()
        line.invalidate_recordset()

        self.assertEqual(production.production_lane, 'upholstery')
        self.assertEqual(production.location_upholstery_id, upholstery_hall)
        self.assertEqual(production.location_upholstery_wip_id, upholstery_hall)
        self.assertEqual(
            production._stage_storage_location('upholstery'),
            upholstery_hall,
        )
        self.assertEqual(production._required_stage_codes(), ['upholstery'])
        self.assertEqual(line._selected_stage_codes(), ['upholstery'])

    def test_handoff_move_and_output_identities_are_immutable(self):
        frame, frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, _finish_line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        handoff = finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        ).ensure_one()
        transfer_move = handoff.transfer_move_id

        with self.assertRaises(AccessError):
            transfer_move.sudo().write({
                'furniture_lane_handoff_role': 'tailoring',
            })
        with self.assertRaises(AccessError):
            transfer_move.sudo().write({
                'furniture_lane_handoff_id': False,
            })
        with self.assertRaises(AccessError):
            transfer_move.sudo().write({'origin': 'FORGED/ORIGIN'})
        with self.assertRaises(UserError):
            finish.sudo().write({'name': 'FORGED/ORDER'})
        with self.assertRaises(AccessError):
            frame.sudo().write({
                'source_location_id': frame_production._get_production_location().id,
            })
        with self.assertRaises(AccessError):
            handoff.sudo().write({'quantity': 0.5})
        with self.assertRaises(AccessError):
            transfer_move.sudo()._action_cancel()

        frame.invalidate_recordset()
        transfer_move.invalidate_recordset()
        self.assertEqual(frame.source_location_id, frame.ready_move_id.location_dest_id)
        self.assertEqual(transfer_move.furniture_lane_handoff_id, handoff)
        self.assertEqual(transfer_move.furniture_lane_handoff_role, 'frame')
        self.assertEqual(transfer_move.origin, finish.name)

    def test_handoff_protects_line_active_and_parent_identity(self):
        frame, _frame_production, _frame_line = self._create_ready_output(
            'frame',
            quantity=1.0,
        )
        finish, finish_line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        other_finish, _other_line = self._create_lane_production(
            'finish',
            quantity=1.0,
            state='confirmed',
        )
        finish._furniture_reserve_required_handoffs(
            source_outputs_by_lane={'frame': frame},
        )

        with self.assertRaises(UserError):
            finish_line.sudo().write({'active': False})
        with self.assertRaises(UserError):
            finish_line.sudo().write({'production_id': other_finish.id})

        self.assertTrue(finish_line.active)
        self.assertEqual(finish_line.production_id, finish)

    def test_finish_and_tailoring_create_one_upholstery_order(self):
        finish, _production, _line = self._create_ready_output(
            'finish',
            quantity=2.0,
        )
        tailoring, _production, _line = self._create_ready_output(
            'tailoring',
            quantity=2.0,
        )
        upholstery = (finish | tailoring)._furniture_auto_create_handoff_orders(
            'upholstery',
            ('finish', 'tailoring'),
            company=self.company,
            final_product=finish.final_product_id,
            furniture_model=self.furniture_model,
            bom=self.bom,
        ).ensure_one()

        self.assertTrue(upholstery.handoff_generated)
        self.assertEqual(upholstery.production_lane, 'upholstery')
        self.assertEqual(upholstery.product_qty, 2.0)
        self.assertEqual(upholstery._required_stage_codes(), ['upholstery'])
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('role')),
            {'finish', 'tailoring'},
        )
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )
        self.assertTrue(all(
            move.state == 'assigned'
            for move in upholstery.upstream_handoff_ids.mapped(
                'transfer_move_id'
            )
        ))
        self.assertFalse(
            (finish | tailoring)._furniture_auto_create_handoff_orders(
                'upholstery',
                ('finish', 'tailoring'),
                company=self.company,
                final_product=finish.final_product_id,
                furniture_model=self.furniture_model,
                bom=self.bom,
            )
        )

    def test_ready_frame_is_the_only_source_of_an_automatic_finish_order(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_stage_replenishment.auto_confirm_enabled',
            'False',
        )
        frame, _production, _line = self._create_ready_output(
            'frame',
            quantity=3.0,
        )

        finish = frame._furniture_auto_create_finish_orders().ensure_one()

        self.assertTrue(finish.handoff_generated)
        self.assertEqual(finish.production_lane, 'finish')
        self.assertEqual(finish.product_qty, 3.0)
        self.assertEqual(
            finish.production_line_ids._selected_stage_codes(),
            ['bases', 'finishing'],
        )
        self.assertEqual(
            set(finish.upstream_handoff_ids.mapped('role')),
            {'frame'},
        )
        self.assertEqual(
            set(finish.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )
        self.assertFalse(frame._furniture_auto_create_finish_orders())

    def test_upholstery_and_painting_auto_create_one_packaging_order(self):
        packaging, upholstery = (
            self._packaging_order_from_ready_outputs(quantity=2.0)
        )

        self.assertTrue(packaging.handoff_generated)
        self.assertEqual(packaging.production_lane, 'packaging')
        self.assertEqual(packaging.product_qty, 2.0)
        self.assertEqual(packaging._required_stage_codes(), ['packaging'])
        self.assertEqual(
            set(packaging.upstream_handoff_ids.mapped('role')),
            {'upholstery', 'painting'},
        )
        self.assertEqual(
            set(packaging.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )
        self.assertFalse(
            upholstery._furniture_auto_create_packaging_orders()
        )

    def test_handoff_generated_order_auto_confirms_in_active_window(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param(
            'furniture_stage_replenishment.auto_confirm_enabled',
            'True',
        )
        parameters.set_param(
            'furniture_stage_replenishment.auto_confirm_until',
            fields.Datetime.to_string(
                fields.Datetime.now() + timedelta(days=2)
            ),
        )
        finish, _production, _line = self._create_ready_output(
            'finish', quantity=2.0,
        )
        tailoring, _production, _line = self._create_ready_output(
            'tailoring', quantity=2.0,
        )

        upholstery = (
            finish | tailoring
        )._furniture_auto_create_handoff_orders(
            'upholstery',
            ('finish', 'tailoring'),
            company=self.company,
            final_product=finish.final_product_id,
            furniture_model=self.furniture_model,
            bom=self.bom,
        ).ensure_one()

        self.assertTrue(upholstery.handoff_generated)
        self.assertEqual(upholstery.state, 'confirmed')
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )

    def test_packaging_finished_transfer_is_idempotent(self):
        packaging, _upholstery = (
            self._packaging_order_from_ready_outputs(quantity=1.0)
        )
        self.assertIs(packaging._furniture_consume_required_handoffs(), True)
        line = packaging.production_line_ids.ensure_one()
        final_product = packaging._furniture_line_final_product(line)
        packaging._create_internal_move(
            packaging._get_production_location(),
            packaging.location_packaging_id,
            'Completed packaged product',
            move_type='finished_product',
            product=final_product,
            quantity=1.0,
            uom=final_product.uom_id,
            source_production_line=line,
            price_unit=20.0,
        )

        first_specs = packaging._furniture_prepare_packaging_finished_specs(
            line
        )
        self.assertEqual(len(first_specs), 1)
        first_moves = packaging._move_finished_product(specs=first_specs)
        self.assertEqual(len(first_moves), 1)
        self.assertEqual(first_moves.state, 'done')
        self.assertEqual(
            first_moves.location_dest_id,
            self.env.ref('furniture_mrp.location_finished_goods'),
        )

        self.assertFalse(
            packaging._furniture_prepare_packaging_finished_specs(line)
        )
        final_moves = self.env['stock.move'].search([
            ('state', '=', 'done'),
            ('origin', '=', packaging.name),
            ('product_id', '=', final_product.id),
            ('location_dest_id', '=', self.env.ref(
                'furniture_mrp.location_finished_goods'
            ).id),
            '|',
            ('furniture_source_production_line_id', '=', line.id),
            ('furniture_source_production_line_ids', 'in', [line.id]),
        ])
        self.assertEqual(len(final_moves), 1)

    def test_packaging_partial_finished_move_creates_only_delta(self):
        packaging, _upholstery = (
            self._packaging_order_from_ready_outputs(quantity=2.0)
        )
        self.assertIs(packaging._furniture_consume_required_handoffs(), True)
        line = packaging.production_line_ids.ensure_one()
        final_product = packaging._furniture_line_final_product(line)
        packaging._create_internal_move(
            packaging._get_production_location(),
            packaging.location_packaging_id,
            'Two completed packaged products',
            move_type='finished_product',
            product=final_product,
            quantity=2.0,
            uom=final_product.uom_id,
            source_production_line=line,
            price_unit=20.0,
        )
        first_spec = packaging._furniture_prepare_packaging_finished_specs(
            line
        )[0]
        first_spec['qty'] = 1.0
        packaging._move_finished_product(specs=[first_spec])

        self.assertFalse(
            packaging._furniture_packaging_lines_fully_finished(line)
        )
        delta_specs = packaging._furniture_prepare_packaging_finished_specs(
            line
        )
        self.assertEqual(len(delta_specs), 1)
        self.assertAlmostEqual(delta_specs[0]['qty'], 1.0)
        delta_moves = packaging._move_finished_product(specs=delta_specs)
        self.assertEqual(len(delta_moves), 1)
        self.assertAlmostEqual(delta_moves.quantity, 1.0)
        self.assertTrue(
            packaging._furniture_packaging_lines_fully_finished(line)
        )
