"""One physical finishing receipt; bases are a gated parallel operation.

Only newly approved combined plan stages opt in. Historic finish/bases/preparation
orders, their receipts and their serial routing are intentionally unchanged.
"""
from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class ParallelFinishProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    def _need_parallel_finish(self):
        self.ensure_one()
        stage = self.sudo().need_to_produce_stage_id
        return bool(self.production_lane == 'finish' and stage and stage.lane == 'finish')

    def _furniture_handoff_target_stage(self):
        if self._need_parallel_finish():
            return ('finishing', 'التجهيز والقواعد')
        return super()._furniture_handoff_target_stage()

    @api.model
    def _furniture_lane_handoff_stage_models(self):
        result = super()._furniture_lane_handoff_stage_models()
        result['furniture.mrp.finishing'] = tuple(dict.fromkeys(
            (*result.get('furniture.mrp.finishing', ()), 'finish')))
        return result

    def _is_material_only_stage(self, stage_code):
        if stage_code == 'bases' and self._need_parallel_finish():
            # This describes stock movement only, NOT permission to start.
            # The bases department has materials/labor but no duplicate body.
            return True
        return super()._is_material_only_stage(stage_code)

    def _need_parallel_receipt_moves(self, line):
        return self.env['stock.move'].sudo().search([
            ('company_id', '=', self.company_id.id), ('state', '=', 'done'),
            ('origin', '=', self.name),
            ('location_id', '=', self._get_production_location().id),
            ('location_dest_id', '=', self.location_finishing_wip_id.id),
            ('product_id.furniture_wip_lane', '=', 'finish'),
            ('furniture_source_production_line_id', '=', line.id),
        ])

    def _need_parallel_received(self, line):
        if not self._furniture_accepted_handoffs_cover_lines(line):
            return False
        moves = self._need_parallel_receipt_moves(line)
        quantity = sum(move.product_uom._compute_quantity(move.quantity, line.product_uom_id)
                       for move in moves)
        return float_compare(quantity, line.product_qty,
                             precision_rounding=line.product_uom_id.rounding or 0.001) >= 0

    def _furniture_consume_required_handoffs(self, production_lines=False):
        result = super()._furniture_consume_required_handoffs(production_lines)
        if not self._need_parallel_finish():
            return result
        # The existing handoff consumes frame WIP into the production location.
        # Materialize its single successor body in finishing, never in bases.
        # The same transaction and exact source-line receipt make retries safe.
        self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE', [self.id])
        self._ensure_stage_locations()
        for line in self._furniture_handoff_lines(production_lines):
            self._furniture_require_accepted_handoffs(line)
            if self._need_parallel_received(line):
                continue
            if self._need_parallel_receipt_moves(line):
                raise UserError(_('استلام التجهيز غير مكتمل؛ راجع التحويل المسجل قبل إعادة الاستلام.'))
            specs = self._get_finished_output_specs_from_lines(line)
            self._create_internal_moves_batch([{
                'source_location': self._get_production_location(),
                'dest_location': self.location_finishing_wip_id,
                'label': _('استلام النجارة في التجهيز — تشغيل القواعد والتجهيز بالتوازي'),
                'product': spec['product'], 'quantity': spec['qty'], 'uom': spec['uom'],
                'source_production_line': line,
            } for spec in specs])
            if not self._need_parallel_received(line):
                raise UserError(_('تعذر إثبات استلام القطعة في صالة التجهيز.'))
        return result

    def _need_parallel_candidates(self, stage_code, stage_order=False):
        lines = self._get_sorted_production_lines().filtered(lambda line: (
            line.active and line.product_qty > 0 and stage_code in line._selected_stage_codes()))
        stage_order = stage_order or self._stage_order_record(stage_code)
        if stage_order:
            for field in ('active_production_line_ids_data', 'quality_production_line_ids_data',
                          'completed_production_line_ids_data'):
                lines -= stage_order._get_stage_line_ids_data(field)
        return lines.filtered(self._need_parallel_received)

    def _allows_received_parallel_entry(self, line, stage_code):
        if self._need_parallel_finish() and stage_code in ('bases', 'finishing'):
            return line.production_id == self and self._need_parallel_received(line)
        return super()._allows_received_parallel_entry(line, stage_code)

    def _get_material_only_stage_start_line_candidates(self, stage_code, stage_order=False):
        if stage_code == 'bases' and self._need_parallel_finish():
            return self._need_parallel_candidates(stage_code, stage_order)
        return super()._get_material_only_stage_start_line_candidates(stage_code, stage_order)

    def _get_stage_pending_start_line_candidates(self, stage_order, stage_code):
        if stage_code in ('bases', 'finishing') and self._need_parallel_finish():
            return self._need_parallel_candidates(stage_code, stage_order)
        return super()._get_stage_pending_start_line_candidates(stage_order, stage_code)

    def _get_start_stage_selector_line_candidates(self, stage_code):
        if stage_code in ('bases', 'finishing') and self._need_parallel_finish():
            return self._need_parallel_candidates(stage_code)
        return super()._get_start_stage_selector_line_candidates(stage_code)

    def _get_first_stage_start_line_candidates(self, stage_code):
        if stage_code in ('bases', 'finishing') and self._need_parallel_finish():
            return self._need_parallel_candidates(stage_code)
        return super()._get_first_stage_start_line_candidates(stage_code)

    def _need_require_bases_complete(self, production_lines=False):
        lines = self._furniture_handoff_lines(production_lines)
        bases = self._stage_order_record('bases')
        completed = (bases._get_stage_line_ids_data('completed_production_line_ids_data')
                     if bases else lines.browse())
        missing = lines - completed
        if missing:
            raise UserError(_('بانتظار القواعد: لا يمكن إتمام التجهيز أو تسليمه للكسوة '
                              'قبل قبول جودة القواعد لنفس القطعة أو الدفعة.'))
        self._furniture_require_accepted_handoffs(lines)
        return lines

    def _move_stage_work_to_stock(self, stage_model, production_lines=False):
        if stage_model == 'furniture.mrp.finishing' and self._need_parallel_finish():
            self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE', [self.id])
            lines = self._need_require_bases_complete(production_lines)
            for line in lines:
                for spec in self._get_finished_output_specs_from_lines(line):
                    if self._stage_output_move_already_done(spec, self.location_finishing_id, source_line=line):
                        continue
                    if not self._need_parallel_received(line):
                        raise UserError(_('القطعة لم تُستلم في التجهيز.'))
                    # Native first-stage quality would create a body from the
                    # production location. Ours already exists: move it once.
                    self._create_internal_moves_batch([{
                        'source_location': self.location_finishing_wip_id,
                        'dest_location': self.location_finishing_id,
                        'label': _('اكتمال التجهيز والقواعد — جاهز للكسوة'),
                        'product': spec['product'], 'quantity': spec['qty'], 'uom': spec['uom'],
                        'source_production_line': line,
                    }])
        # Existing quality still consumes actual materials and records costs;
        # bases is material-only, finishing's existing receipt is idempotent.
        return super()._move_stage_work_to_stock(stage_model, production_lines)

    def _ensure_lane_outputs(self, production_lines=False):
        if self._need_parallel_finish():
            self._need_require_bases_complete(production_lines)
        return super()._ensure_lane_outputs(production_lines)


class ParallelFinishStage(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    def action_start(self):
        for stage in self:
            production = stage.production_order_id
            if production and production._need_parallel_finish():
                # No context key, raw-material approval or manually-created
                # stage order can make bases independent from actual receipt.
                lines = production._furniture_handoff_lines()
                if not lines or any(not production._need_parallel_received(line) for line in lines):
                    raise UserError(_('استلم تحويل النجارة في التجهيز أولًا، ثم ابدأ القواعد أو التجهيز.'))
        return super().action_start()
