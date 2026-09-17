import json
from unittest.mock import patch
from odoo.exceptions import UserError
from .test_production_plan import TestNeedToProduce


class TestParallelFinish(TestNeedToProduce):
    """Opt-in regression cases; inherited legacy tests are run separately."""

    def _parallel_plans(self, quantity=1):
        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_need_to_produce.parallel_finish_v1', 'True')
        return self._plans(quantity)

    def _pair(self, quantity=1):
        self._enable_grouped_approval()
        pieces = self._parallel_plans(quantity)
        pieces.with_user(self.manager).action_approve()
        return pieces, pieces.stage_ids.filtered(lambda stage: stage.lane == 'finish').production_id

    def _stage_order(self, production, code):
        existing = production._stage_order_record(code)
        if existing:
            return existing
        order = self.env['furniture.mrp.' + code].create({
            'name': 'PARALLEL/%s/%s' % (production.id, code),
            'production_order_id': production.id, 'state': 'pending',
        })
        production.write({code + '_order_id': order.id})
        return order

    def _receive_pair(self, pieces, production):
        frame = pieces.stage_ids.filtered(lambda stage: stage.lane == 'frame').production_id
        self._emit_output(frame)
        self._accept_inputs(production)

    def test_parallel_one_order_one_receipt_both_stage_candidates(self):
        pieces, production = self._pair(3)
        self.assertEqual(len(pieces.stage_ids.production_id), 6)
        self.assertEqual(production.product_qty, 3)
        self.assertTrue(production._need_parallel_finish())
        self.assertEqual(production._furniture_handoff_target_stage()[0], 'finishing')
        self.assertEqual(production._physical_stage_codes(), ['finishing'])
        line = production.production_line_ids
        for code in ('bases', 'finishing'):
            self.assertFalse(production._get_stage_pending_start_line_candidates(False, code))
        self._receive_pair(pieces, production)
        for code in ('bases', 'finishing'):
            self.assertEqual(production._get_stage_pending_start_line_candidates(False, code), line)
        receipts = production._need_parallel_receipt_moves(line)
        self.assertEqual(sum(receipts.mapped('quantity')), 3)
        self._accept_inputs(production)
        self.assertEqual(production._need_parallel_receipt_moves(line), receipts)
        self.assertFalse(production.lane_output_ids)
        self.assertFalse(self.env['stock.move'].search([
            ('origin', '=', production.name), ('state', '=', 'done'),
            ('location_dest_id', '=', production.location_bases_wip_id.id),
            ('product_id.furniture_wip_lane', '!=', False)]))

    def test_parallel_finish_cannot_release_without_matching_bases(self):
        pieces, production = self._pair()
        self._receive_pair(pieces, production)
        line = production.production_line_ids
        finishing = self._stage_order(production, 'finishing')
        bases = self._stage_order(production, 'bases')
        with self.assertRaisesRegex(UserError, 'بانتظار القواعد'):
            production._move_stage_work_to_stock(finishing._name, line)
        with self.assertRaisesRegex(UserError, 'بانتظار القواعد'):
            production._ensure_lane_outputs(line)
        self.assertFalse(production.lane_output_ids)
        bases._set_stage_line_ids_data('completed_production_line_ids_data', line)
        # Fixture isolates body stock from actual material/labor approval.
        production.material_line_ids.unlink()
        production._move_stage_work_to_stock(finishing._name, line)
        production._move_stage_work_to_stock(finishing._name, line)
        output = production._ensure_lane_outputs(line)
        self.assertEqual(output.lane, 'finish')
        self.assertEqual(output.qty_ready, 1)
        self.assertEqual(production._stage_location_product_qty(
            production.location_finishing_wip_id, output.wip_product_id), 0)
        self.assertFalse(production.lane_output_ids.filtered(lambda row: row.lane == 'bases'))

    def test_parallel_graph_forks_and_joins_without_serial_start_dependency(self):
        piece = self._parallel_plans()
        graph = piece.route_graph
        by_code = {node['stage_code']: node['id'] for node in graph['nodes'] if node['kind'] == 'stage'}
        edges = {(edge['from'], edge['to']) for edge in graph['edges']}
        for code in ('bases', 'finishing'):
            self.assertIn((by_code['carpentry'], by_code[code]), edges)
            self.assertIn((by_code[code], by_code['upholstery']), edges)
        self.assertNotIn((by_code['bases'], by_code['finishing']), edges)

    def test_parallel_migration_preserves_custom_materials_and_empty_snapshots(self):
        piece = self._plans(1)
        self._stage(piece, 'bases').material_ids.with_user(self.manager).write({'quantity': 9})
        self._stage(piece, 'preparation').material_ids.with_user(self.manager).unlink()
        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_need_to_produce.parallel_finish_v1', 'True')
        piece._replace_preview(piece._build_preview())
        pair = self._stage(piece, 'finish')
        self.assertEqual(pair.material_ids.mapped('stage_code'), ['bases'])
        self.assertEqual(pair.material_ids.quantity, 9)
        self.assertTrue(piece.is_custom)
        piece._replace_preview(piece._build_preview())
        self.assertEqual(self._stage(piece, 'finish').material_ids.quantity, 9)

    def test_parallel_shared_buffer_policies_do_not_double_the_units(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_need_to_produce.parallel_finish_v1', 'True')
        rules = self.StageRule.search([('product_id', '=', self.product.id),
                                     ('furniture_model_id', '=', self.model.id),
                                     ('stage_code', 'in', ['bases', 'finishing'])])
        rules.write({'min_qty': 0, 'max_qty': 3})
        self.Piece._refresh_company(self.company)
        pieces = self.Piece.search([('buffer_rule_id', 'in', rules.ids), ('state', '=', 'draft')])
        self.assertEqual(len(pieces), 3)
        self.assertEqual(set(pieces.stage_ids.mapped('lane')), {'frame', 'finish'})
        self.assertEqual(set(rules.mapped('max_qty')), {3})

    def test_parallel_both_real_stage_timers_and_quality_gate(self):
        pieces, production = self._pair()
        line = production.production_line_ids
        stages = self.env['furniture.mrp.employee.stage'].search([
            ('code', 'in', ['bases', 'finishing'])])
        employee = self.env['hr.employee'].create({
            'name': 'Parallel test supervisor', 'user_id': self.manager.id,
            'company_id': self.company.id, 'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, stages.ids)],
        })
        bases = self._stage_order(production, 'bases')
        finishing = self._stage_order(production, 'finishing')
        for stage in (bases, finishing):
            stage.foreman_id = employee
            with self.assertRaisesRegex(UserError, 'استلم تحويل النجارة'):
                stage.with_user(self.manager).sudo().with_context(
                    furniture_storekeeper_approval_bypass=True).action_start()
        self._emit_output(pieces.stage_ids.filtered(lambda s: s.lane == 'frame').production_id)
        production.with_user(self.manager).action_accept_handoff_transfer()
        self.assertTrue(production._allows_received_parallel_entry(line, 'bases'))
        # Stock/material issues remain native. This test supplies raw stock and
        # invokes the same post-store-approval stage lifecycle as batch start.
        self.env['stock.quant']._update_available_quantity(
            self.raw, production.location_src_id, 10)
        for stage in (bases, finishing):
            stage._add_stage_active_lines(line)
            result = stage.with_user(self.manager).sudo().with_context(
                furniture_storekeeper_approval_bypass=True,
                furniture_skip_stage_start_prompt=True,
                furniture_stage_start_mode='first_stage_selected',
            ).action_start()
            self.assertEqual(stage.state, 'in_progress', result)
        self.assertEqual(bases.state, finishing.state)
        finishing._send_selected_lines_to_quality(line)
        with self.assertRaisesRegex(UserError, 'بانتظار القواعد'):
            finishing.action_approve_quality()
        self.assertFalse(production.lane_output_ids)
        self.assertEqual(finishing.state, 'quality_check')
        for code in ('preparation', 'foam'):
            bases.with_context(bases_internal_substage=code).action_complete_internal_substage()
        bases._send_selected_lines_to_quality(line)
        bases.action_approve_quality()
        self.assertFalse(production.lane_output_ids)
        finishing.action_approve_quality()
        self.assertEqual(production.lane_output_ids.qty_ready, 1)
        self.assertEqual(production.lane_output_ids.lane, 'finish')

    def test_parallel_native_batch_starts_bases_before_finishing(self):
        pieces, production = self._pair()
        self._receive_pair(pieces, production)
        line = production.production_line_ids
        employee = self.env['hr.employee'].create({
            'name': 'Parallel batch supervisor', 'user_id': self.manager.id,
            'company_id': self.company.id, 'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, self.env[
                'furniture.mrp.employee.stage'].search([('code', 'in', ['bases', 'finishing'])]).ids)],
        })
        Batch = self.env['furniture.mrp.stage.product.batch'].with_user(self.manager).sudo()
        identity = Batch._identity_values_from_line(line)
        # Raw release issuance is tested separately; exercise the real batch
        # candidate/entry checks and native stage-start timers here.
        for code in ('bases', 'finishing'):
            stage = self._stage_order(production, code)
            stage.foreman_id = employee
            batch = Batch.create({
                'token': 'parallel-native-%s-%s' % (production.id, code),
                'company_id': self.company.id, 'stage_code': code,
                'identity_key': identity['identity_key'], 'product_id': identity['product'].id,
                'model_id': identity['model'].id, 'bom_id': identity['bom'].id,
                'dimension_label': identity['dimension_label'], 'uom_id': identity['uom'].id,
                'production_line_ids': [(6, 0, line.ids)], 'planned_qty': line.product_qty,
                'state': 'ready', 'member_ids': [(0, 0, {
                    'production_line_id': line.id, 'qty_snapshot': line.product_qty,
                    'identity_key': identity['identity_key'],
                })],
            })
            with patch.object(type(batch), '_check_release_scope', return_value=True), \
                    patch.object(type(batch), '_preflight_execution_materials', return_value=True), \
                    patch.object(type(batch), '_prepare_execution_materials', return_value=True):
                result = batch._start_batch()
            self.assertEqual(result['state'], 'in_progress')
            self.assertEqual(stage.state, 'in_progress')
        self.assertEqual(line.first_stage_started_stage, 'bases')
        self.assertEqual(production.bases_order_id.state, production.finishing_order_id.state)
        self.assertFalse(production.lane_output_ids)
