"""Real approval/cancellation lifecycle, exclusively transactional fixtures."""
from unittest.mock import patch
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.addons.furniture_mrp.models.mrp_production_order import FURNITURE_STAGE_FIELD_MAP
from odoo.tests.common import new_test_user
from .test_production_plan import TestNeedToProduce


@tagged('post_install', '-at_install')
class TestApprovedTracking(TestNeedToProduce):
    def test_open_productions_action_is_ready_for_client_preprocessing(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        action = piece.with_user(self.manager).action_open_productions()
        self.assertEqual(action['views'], [(False, 'list'), (False, 'form')])
        self.assertEqual(action['target'], 'current')
        self.assertEqual(action['domain'], [('id', 'in', piece.stage_ids.production_id.ids)])

    def test_compatible_separate_approvals_share_card_and_cancel_atomically(self):
        pieces = self._plans(3)
        # A modified piece must remain isolated, even with the same product/route.
        material = pieces[2].stage_ids.material_ids[:1]
        material.with_user(self.manager).write({'quantity': material.quantity + 1})
        for piece in pieces:
            piece.with_user(self.manager).action_approve()
        ordinary, custom = pieces[:2], pieces[2:]
        orders, custom_orders = ordinary.stage_ids.production_id, custom.stage_ids.production_id
        data = self.Piece.with_user(self.manager).get_approved_tracking(search=self.model.name)
        self.assertEqual(data['total'], 2)
        card = next(c for c in data['cards'] if not c['custom'])
        self.assertEqual(set(card['piece_ids']), set(ordinary.ids))
        self.assertEqual(card['quantity'], 2)
        self.assertEqual(card['order_count'], len(orders))
        self.assertEqual(len(card['approvals']), 2)
        searched = self.Piece.with_user(self.manager).get_approved_tracking(search=orders[0].name)
        self.assertEqual(searched['cards'][0]['piece_ids'], card['piece_ids'])
        original = type(orders).action_cancel
        calls = []
        def fail_second(records):
            calls.append(records.id)
            if len(calls) == 2:
                raise UserError('injected grouped failure')
            return original(records)
        with patch.object(type(orders), 'action_cancel', fail_second):
            with self.assertRaises(UserError):
                ordinary.with_user(self.manager).action_cancel_approval(card['approval_token'])
        self.assertEqual(set(orders.mapped('state')), {'confirmed'})
        self.assertEqual(set(ordinary.mapped('state')), {'approved'})
        ordinary.with_user(self.manager).action_cancel_approval(card['approval_token'])
        self.assertFalse(orders.exists())
        self.assertEqual(set(ordinary.mapped('state')), {'draft'})
        self.assertEqual(custom.state, 'approved')
        self.assertEqual(custom_orders.exists(), custom_orders)

    def test_model_navigation_summarizes_before_pagination(self):
        # Distinct models can share a name; ID controls navigation, not the label.
        cards = [{'id': i, 'model_id': 10 if i < 27 else 20, 'model': 'Same name',
                  'quantity': 2, 'completed_steps': 2, 'total_steps': 8,
                  'status': 'running'} for i in range(30)]
        result = self.Piece._approved_tracking_page(cards)
        self.assertEqual(len(result['cards']), 24)
        self.assertEqual([m['count'] for m in result['models']], [27, 3])
        self.assertEqual([m['quantity'] for m in result['models']], [54, 6])
        self.assertEqual(result['counts']['running'], 30)
        selected = self.Piece._approved_tracking_page(cards, page=1, model_id=10)
        self.assertEqual([c['id'] for c in selected['cards']], [24, 25, 26])
        self.assertEqual(selected['total'], 27)
        self.assertEqual(selected['counts']['running'], 27)
        other = self.Piece._approved_tracking_page(cards, page=4, model_id=20)
        self.assertEqual(other['page'], 0)
        self.assertEqual([c['id'] for c in other['cards']], [27, 28, 29])
        empty = self.Piece._approved_tracking_page(cards, model_id=999)
        self.assertEqual(empty['total'], 0)
        self.assertEqual(empty['cards'], [])

    def test_cancel_deletes_exact_orders_and_allows_reapproval(self):
        pieces = self._plans(2)
        pieces.with_user(self.manager).action_approve()
        orders = pieces[:1].stage_ids.production_id
        untouched = pieces[1:].stage_ids.production_id
        pieces[:1].with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())
        self.assertEqual(pieces[0].state, 'draft')
        self.assertFalse(pieces[0].stage_ids.production_id)
        self.assertEqual(set(untouched.mapped('state')), {'confirmed'})
        self.assertEqual(len(pieces[0].approval_cancellation_history[0]['orders']), len(orders))
        pieces[:1].with_user(self.manager).action_cancel_approval()
        pieces[:1].with_user(self.manager).action_approve()
        self.assertEqual(pieces[0].state, 'approved')
        self.assertFalse(pieces[0].stage_ids.production_id & orders)

    def test_grouped_card_is_complete_and_partial_cancellation_rejected(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        pieces.with_user(self.manager).action_approve()
        orders = pieces.stage_ids.production_id
        data = self.Piece.with_user(self.manager).get_approved_tracking(search=self.model.name)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['cards'][0]['piece_ids'], pieces.ids)
        self.assertEqual(data['cards'][0]['order_count'], 7)
        self.assertEqual(data['models'][0]['id'], self.model.id)
        self.assertEqual(data['models'][0]['quantity'], 3)
        selected = self.Piece.with_user(self.manager).get_approved_tracking(
            search=self.model.name, model_id=self.model.id)
        self.assertEqual(selected['cards'][0]['piece_ids'], pieces.ids)
        with self.assertRaises(UserError):
            pieces[:1].with_user(self.manager).action_cancel_approval()
        self.assertEqual(len(orders.exists()), 7)
        pieces.with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())
        self.assertEqual(set(pieces.mapped('state')), {'draft'})

    def test_reserved_input_is_released_without_deleting_source(self):
        source = self._stock('frame', 1)
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        orders = piece.stage_ids.production_id
        handoffs = piece.stage_ids.input_ids.handoff_ids
        self.assertTrue(handoffs)
        self.assertEqual(set(handoffs.mapped('state')), {'reserved'})
        piece.with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())
        self.assertTrue(source.production_id.exists())
        self.assertFalse(handoffs.exists())
        self.assertEqual(source.physical_available_qty, 1)

    def test_consumed_input_blocks_entire_cancellation(self):
        self._stock('frame', 1)
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        self._accept_inputs(self._stage(piece, 'bases').production_id)
        orders = piece.stage_ids.production_id
        with self.assertRaises(UserError):
            piece.with_user(self.manager).action_cancel_approval()
        self.assertEqual(piece.state, 'approved')
        self.assertEqual(orders.exists(), orders)
        self.assertNotIn('cancelled', orders.mapped('state'))

    def test_running_or_finished_order_cannot_be_deleted(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        order = piece.stage_ids.production_id[:1]
        for state in ('in_production', 'done'):
            order.write({'state': state})
            with self.assertRaises(UserError):
                piece.with_user(self.manager).action_cancel_approval()
            self.assertEqual(piece.state, 'approved')
            self.assertTrue(order.exists())

    def test_atomic_rollback_when_later_order_cancel_fails(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        orders = piece.stage_ids.production_id
        original = type(orders).action_cancel
        calls = []
        def fail_second(records):
            calls.append(records.id)
            if len(calls) == 2:
                raise UserError('injected failure')
            return original(records)
        with patch.object(type(orders), 'action_cancel', fail_second):
            with self.assertRaises(UserError):
                piece.with_user(self.manager).action_cancel_approval()
        self.assertEqual(set(orders.mapped('state')), {'confirmed'})
        self.assertEqual(piece.state, 'approved')
        self.assertEqual(piece.stage_ids.production_id, orders)
        self.assertFalse(piece.approval_cancellation_history)

    def test_stage_tab_and_cancellation(self):
        rule = self.StageRule._stage_replenishment_sync_company(self.company)
        rule = self.StageRule.search([]).filtered(lambda r:
            r.company_id == self.company and r.product_id == self.product and
            r.furniture_model_id == self.model and r.stage_code == 'carpentry')[:1]
        self.assertTrue(rule)
        rule.write({'min_qty': 0, 'max_qty': 1})
        self.Piece._sync_request(rule, 'frame', 1, 'buffer_rule_id')
        piece = self.Piece.search([('buffer_rule_id', '=', rule.id)])
        piece.with_user(self.manager).action_approve()
        orders = piece.stage_ids.production_id
        self.assertEqual(self.Piece.with_user(self.manager).get_approved_tracking(scope='final', search=self.model.name)['total'], 0)
        data = self.Piece.with_user(self.manager).get_approved_tracking(scope='stage', search=self.model.name)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['cards'][0]['piece_ids'], piece.ids)
        self.assertEqual(data['cards'][0]['status'], 'attention')
        self.assertTrue(data['cards'][0]['shortage_stage_names'])
        piece.with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())

    def test_tracking_reads_actual_stage_states_without_writes(self):
        self.env['stock.quant']._update_available_quantity(
            self.raw, self.env.ref('stock.stock_location_stock'), 100,
        )
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        order = piece.stage_ids.production_id[:1]
        code, stage, _state = order._required_stage_infos()[0]
        if not stage:
            stage = order._create_stage_order('furniture.mrp.' + code, 'TRACK')
            order.write({FURNITURE_STAGE_FIELD_MAP[code][1]: stage.id})
        stage.write({'state': 'in_progress'})
        data = self.Piece.with_user(self.manager).get_approved_tracking(search=self.model.name)
        card = data['cards'][0]
        self.assertEqual(card['status'], 'running')
        self.assertFalse(card['can_cancel'])
        self.assertEqual(next(s for s in card['steps'] if s['key'] == f'{order.id}:{code}')['state'], 'in_progress')
        self.assertEqual(piece.state, 'approved')

    def test_attention_only_marks_open_stages_with_unavailable_raw_materials(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        data = self.Piece.with_user(self.manager).get_approved_tracking(
            search=self.model.name,
        )
        card = data['cards'][0]
        shortage_steps = [step for step in card['steps'] if step['material_shortage']]
        self.assertEqual(card['status'], 'attention')
        self.assertTrue(shortage_steps)
        self.assertTrue(card['shortage_stage_names'])
        self.assertTrue(all(step['material_shortages'] for step in shortage_steps))
        self.assertEqual(data['counts']['attention'], 1)

        self.env['stock.quant']._update_available_quantity(
            self.raw, self.env.ref('stock.stock_location_stock'), 100,
        )
        available = self.Piece.with_user(self.manager).get_approved_tracking(
            search=self.model.name,
        )
        available_card = available['cards'][0]
        self.assertEqual(available_card['status'], 'waiting')
        self.assertFalse(available_card['shortage_stage_names'])
        self.assertFalse(any(
            step['material_shortage'] for step in available_card['steps']
        ))

    def test_cancelled_order_alone_is_not_a_material_attention_status(self):
        self.env['stock.quant']._update_available_quantity(
            self.raw, self.env.ref('stock.stock_location_stock'), 100,
        )
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        piece.stage_ids.production_id[:1].write({'state': 'cancelled'})
        card = self.Piece.with_user(self.manager).get_approved_tracking(
            search=self.model.name,
        )['cards'][0]
        self.assertNotEqual(card['status'], 'attention')
        self.assertFalse(card['shortage_stage_names'])

    def test_non_manager_and_company_scope(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        ordinary = new_test_user(self.env, login='ntp.tracking.ordinary', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.Piece.with_user(ordinary).get_approved_tracking()
        with self.assertRaises(AccessError):
            piece.with_user(ordinary).action_cancel_approval()
        other = self.env['res.company'].create({'name': 'Tracking isolated other company'})
        self.assertEqual(self.Piece.with_context(allowed_company_ids=other.ids).get_approved_tracking(search=self.model.name)['total'], 0)
        with self.assertRaises(AccessError):
            piece.with_context(allowed_company_ids=other.ids).action_cancel_approval()

    def test_approved_downstream_blocks_source_withdrawal(self):
        self.StageRule._stage_replenishment_sync_company(self.company)
        rule = self.StageRule.search([('company_id', '=', self.company.id), ('product_id', '=', self.product.id),
                                      ('furniture_model_id', '=', self.model.id), ('stage_code', '=', 'carpentry')], limit=1)
        rule.write({'min_qty': 0, 'max_qty': 1})
        self.Piece._sync_request(rule, 'frame', 1, 'buffer_rule_id')
        source = self.Piece.search([('buffer_rule_id', '=', rule.id)])
        source.with_user(self.manager).action_approve()
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        self.assertTrue(piece.stage_ids.input_ids.filtered(lambda i: i.source_line_id.production_id in source.stage_ids.production_id))
        with self.assertRaises(UserError):
            source.with_user(self.manager).action_cancel_approval()
        self.assertEqual(source.state, 'approved')
        piece.with_user(self.manager).action_cancel_approval()
        # The returned draft may use the same incoming source; cancellation
        # rebuilds that draft first so no dangling source line survives.
        orders = source.stage_ids.production_id
        source.with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())
        self.assertFalse(piece.stage_ids.input_ids.source_line_id & orders.production_line_ids)

    def test_custom_recipe_survives_withdrawal(self):
        piece = self._plans(1)
        material = piece.stage_ids.material_ids[:1]
        code = material.stage_code
        material.with_user(self.manager).write({'quantity': 3.5})
        piece.with_user(self.manager).action_approve()
        piece.with_user(self.manager).action_cancel_approval()
        self.assertTrue(piece.is_custom)
        self.assertEqual(piece.stage_ids.material_ids.filtered(lambda m: m.stage_code == code).quantity, 3.5)

    def test_pending_warehouse_request_is_cancelled(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        order = self._stage(piece, 'frame').production_id
        release = self.env['furniture.mrp.advance.material.release'].create_from_stage_codes(
            order, ['priming'], notify_storekeeper=False)
        self.assertEqual(release.state, 'pending')
        piece.with_user(self.manager).action_cancel_approval()
        self.assertEqual(release.state, 'cancelled')
        self.assertFalse(release.stage_line_ids)

    def test_stale_card_cannot_cancel_a_new_approval(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        token = piece._approval_token()
        piece.with_user(self.manager).action_cancel_approval(approval_token=token)
        piece.with_user(self.manager).action_approve()
        orders = piece.stage_ids.production_id
        with self.assertRaises(UserError):
            piece.with_user(self.manager).action_cancel_approval(approval_token=token)
        self.assertEqual(piece.state, 'approved')
        self.assertEqual(orders.exists(), orders)

    def test_pending_shared_kit_dissolves_without_deleting_other_orders(self):
        pieces = self._plans(2)
        pieces.with_user(self.manager).action_approve()
        orders = pieces[0].stage_ids.production_id
        other_orders = pieces[1].stage_ids.production_id
        lines = pieces.stage_ids.filtered(lambda s: s.lane == 'tailoring').production_id.production_line_ids
        Kit = self.env['furniture.textile.kit']
        kit = Kit.create({'token': 'tracking-pending-test-kit', 'company_id': self.company.id,
                          'model_id': self.model.id, 'recipe_id': self.bom.id, 'stage_code': 'tailoring',
                          'member_ids': [(0, 0, {'production_line_id': line.id,
                                               'snapshot': Kit._line_snapshot(line)}) for line in lines]})
        self.assertFalse(pieces[:1]._approval_cancel_blocker())
        pieces[:1].with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())
        self.assertFalse(kit.exists())
        self.assertEqual(other_orders.exists(), other_orders)
        self.assertEqual(pieces[1].state, 'approved')
        self.assertTrue(other_orders.production_line_ids)

    def test_shared_pending_request_keeps_other_order_lines(self):
        pieces = self._plans(2)
        pieces.with_user(self.manager).action_approve()
        orders = pieces.stage_ids.filtered(lambda s: s.lane == 'frame').production_id
        release = self.env['furniture.mrp.advance.material.release'].create_from_production_stage_map(
            orders, {order.id: ['priming'] for order in orders}, notify_storekeeper=False)
        self.assertEqual(len(release.stage_line_ids), 2)
        preserved = release.stage_line_ids.filtered(lambda s: s.production_id in pieces[1].stage_ids.production_id)
        pieces[:1].with_user(self.manager).action_cancel_approval()
        release.invalidate_recordset()
        self.assertEqual(release.state, 'pending')
        self.assertEqual(release.stage_line_ids, preserved)
        self.assertTrue(preserved.material_line_ids)
        self.assertEqual(pieces[1].state, 'approved')

    def test_waiting_product_batch_members_do_not_prevent_order_deletion(self):
        piece = self._plans(1)
        piece.with_user(self.manager).action_approve()
        orders = piece.stage_ids.production_id
        line = self._stage(piece, 'frame').production_id.production_line_ids[:1]
        batch = self.env['furniture.mrp.stage.product.batch'].create({
            'token': 'tracking-pending-batch', 'identity_key': 'tracking-key',
            'company_id': self.company.id, 'stage_code': 'priming',
            'product_id': self.product.id, 'uom_id': self.unit.id, 'planned_qty': 1,
            'production_line_ids': [(6, 0, line.ids)],
            'member_ids': [(0, 0, {'production_line_id': line.id, 'qty_snapshot': 1, 'identity_key': 'tracking-key'})]})
        piece.with_user(self.manager).action_cancel_approval()
        self.assertFalse(orders.exists())
        self.assertEqual(batch.state, 'cancelled')
        self.assertFalse(batch.member_ids)
