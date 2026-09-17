"""Ready components release upholstery in the completion transaction, not cron."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .test_production_plan import TestNeedToProduce


@tagged('post_install', '-at_install')
class TestReadyHandoff(TestNeedToProduce):

    def _legacy_source(self, lane, quantity=3, authorized=True):
        production, _line = self._new_production(lane, quantity)
        if authorized and lane == 'finish':
            production._final_replenishment_internal_write({
                'final_replenishment_rule_ids': [(6, 0, self.rule.ids)],
            })
        return production

    def _upholstery(self):
        return self.Production.search([
            ('furniture_order_model_id', '=', self.model.id), ('production_lane', '=', 'upholstery'),
            ('state', '!=', 'cancelled'),
        ])

    def _enable_legacy(self, maximum=3):
        self.rule.write({'min_qty': 0, 'max_qty': maximum})
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param('furniture_stage_replenishment.auto_confirm_enabled', 'True')
        parameters.set_param('furniture_stage_replenishment.auto_confirm_until', fields.Datetime.now() + timedelta(days=1))

    def test_last_component_creates_legacy_upholstery_immediately_in_either_order(self):
        for lanes in [('finish', 'tailoring'), ('tailoring', 'finish')]:
            with self.subTest(lanes=lanes), self.env.cr.savepoint() as checkpoint:
                self._enable_legacy()
                self._emit_output(self._legacy_source(lanes[0]))
                self.assertFalse(self._upholstery(), 'One completed component is insufficient')
                source = self._legacy_source(lanes[1])
                self._emit_output(source)
                upholstery = self._upholstery().ensure_one()
                self.assertEqual(upholstery.state, 'confirmed')
                self.assertEqual(upholstery.product_qty, 3)
                self.assertEqual(set(upholstery.upstream_handoff_ids.mapped('role')), {'finish', 'tailoring'})
                self.assertEqual(set(upholstery.upstream_handoff_ids.transfer_move_id.mapped('state')), {'assigned'})
                self.assertTrue(upholstery.handoff_transfer_pending)
                self.assertFalse(upholstery.handoff_accepted_at)
                source._ensure_lane_outputs()
                self.assertEqual(self._upholstery(), upholstery, 'Completion retry must not duplicate an order')
                checkpoint.rollback()

    def test_ready_callback_obeys_final_maximum_and_disabled_policy(self):
        self._enable_legacy(maximum=2)
        self._emit_output(self._legacy_source('tailoring'))
        finish = self._legacy_source('finish')
        self._emit_output(finish)
        upholstery = self._upholstery().ensure_one()
        self.assertEqual(upholstery.product_qty, 2)
        finish._ensure_lane_outputs()
        self.assertEqual(self._upholstery(), upholstery)
        self.rule.write({'min_qty': 0, 'max_qty': 0})
        self._emit_output(self._legacy_source('tailoring', 1))
        self.assertEqual(self._upholstery(), upholstery)

    def test_stock_or_draft_preview_does_not_authorize_hidden_production(self):
        self._enable_legacy()
        self._plans(3)
        self._emit_output(self._legacy_source('tailoring'))
        self._emit_output(self._legacy_source('finish', authorized=False))
        self.assertFalse(self._upholstery())
        self.assertFalse(self.Piece.search([('final_rule_id', '=', self.rule.id)]).stage_ids.production_id)

    def test_existing_approved_piece_released_without_cron_or_second_order(self):
        piece = self._plans(1)
        piece.action_approve()
        orders = piece.stage_ids.production_id
        upholstery = self._stage(piece, 'upholstery').production_id
        self.assertFalse(upholstery._furniture_handoffs_cover_lines())
        self._emit_output(self._stage(piece, 'preparation').production_id)
        self.assertFalse(upholstery._furniture_handoffs_cover_lines())
        self._emit_output(self._stage(piece, 'tailoring').production_id)
        self.assertTrue(upholstery._furniture_handoffs_cover_lines())
        self.assertTrue(upholstery.handoff_transfer_pending)
        self.assertFalse(upholstery.handoff_accepted_at)
        self.assertEqual(piece.stage_ids.production_id, orders)
        self.assertEqual(self._upholstery(), upholstery)

    def test_existing_output_completion_retries_previously_unavailable_claim(self):
        piece = self._plans(1)
        piece.action_approve()
        finish = self._stage(piece, 'preparation').production_id
        Input = self.env.registry['furniture.need.to.produce.input']
        with patch.object(Input, '_release_available_inputs', return_value=None):
            self._emit_output(finish)
        upholstery = self._stage(piece, 'upholstery').production_id
        self.assertFalse(upholstery.upstream_handoff_ids)
        # No new output row is inserted on this completion retry.
        finish._ensure_lane_outputs()
        self.assertEqual(upholstery.upstream_handoff_ids.mapped('role'), ['finish'])

    def test_approved_exact_claim_is_never_taken_by_legacy_continuation(self):
        self._enable_legacy(maximum=3)
        finish = self._legacy_source('finish', 3)
        self._emit_output(self._legacy_source('tailoring', 3))
        piece = self._plans(1)
        piece.action_approve()
        self.rule.write({'max_qty': 3})
        upholstery = self._stage(piece, 'upholstery').production_id
        self._emit_output(finish)
        self.assertEqual(self._upholstery(), upholstery)
        self.assertTrue(upholstery._furniture_handoffs_cover_lines())

    def test_busy_coordinator_queues_retry_without_undoing_receipt(self):
        self._enable_legacy()
        self._emit_output(self._legacy_source('tailoring'))
        Final = self.env.registry['furniture.mrp.final.replenishment.rule']
        with patch.object(Final, '_final_replenishment_try_lock_company', return_value=False), \
                patch.object(Final, '_final_replenishment_wake_coordinator') as wake:
            finish = self._emit_output(self._legacy_source('finish'))
        self.assertTrue(finish.exists())
        self.assertEqual(finish.origin_receipt_move_id.state, 'done')
        self.assertFalse(self._upholstery())
        wake.assert_called_once()
        self.FinalRule._final_replenishment_on_ready_outputs(finish)
        self.assertEqual(self._upholstery().product_qty, 3)

    def test_downstream_failure_keeps_completed_source_and_recovers_once(self):
        self._enable_legacy()
        self._emit_output(self._legacy_source('tailoring'))
        Final = self.env.registry['furniture.mrp.final.replenishment.rule']
        with patch.object(Final, '_final_replenishment_create_upholstery_available', side_effect=ValueError('fixture failure')), \
                patch.object(Final, '_final_replenishment_wake_coordinator') as wake:
            finish = self._emit_output(self._legacy_source('finish'))
        self.assertTrue(finish.exists())
        self.assertEqual(finish.origin_receipt_move_id.state, 'done')
        self.assertFalse(self._upholstery())
        wake.assert_called_once()
        self.FinalRule._final_replenishment_on_ready_outputs(finish)
        upholstery = self._upholstery().ensure_one()
        self.FinalRule._final_replenishment_on_ready_outputs(finish)
        self.assertEqual(self._upholstery(), upholstery)
