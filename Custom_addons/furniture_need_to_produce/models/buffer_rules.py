"""Department policies for the shared bases/preparation production buffer."""

from odoo import api, models
from odoo.tools.float_utils import float_compare


BUFFER_LANES = {'bases': 'bases', 'finishing': 'preparation'}
BUFFER_ROLES = {'bases': 'bases', 'finishing': 'finish'}
BUFFER_LABELS = {'bases': 'القواعد', 'finishing': 'التجهيز'}


class NeedToProduceBufferRules(models.Model):
    _inherit = 'furniture.mrp.stage.replenishment.rule'

    @api.model
    def _stage_replenishment_lane_for_stage(self, stage_code):
        return BUFFER_LANES.get(stage_code) or super()._stage_replenishment_lane_for_stage(stage_code)

    @api.model
    def _stage_replenishment_lane_rule_stages(self, lane):
        if lane == 'bases':
            return ('bases',)
        if lane == 'preparation':
            return ('finishing',)
        # Old combined finish orders keep their historical interpretation.
        return super()._stage_replenishment_lane_rule_stages(lane)

    @api.model
    def _stage_replenishment_lane_route(self, lane, bom):
        controller = {'bases': 'bases', 'preparation': 'finishing'}.get(lane)
        if controller:
            return (controller,) if controller in bom._get_active_stage_codes() else ()
        return super()._stage_replenishment_lane_route(lane, bom)

    @api.model
    def _need_buffer_outputs(self, temporary, role):
        outputs = self.env['furniture.mrp.lane.output'].sudo().search([
            ('company_id', '=', temporary.company_id.id),
            ('furniture_model_id', '=', temporary.furniture_model_id.id),
            ('bom_id', '=', temporary.bom_id.id),
            ('lane', '=', role),
            ('origin_receipt_move_id.state', '=', 'done'),
        ])
        return outputs.filtered(lambda row: (
            temporary._same_product(row.final_product_id)
            and row._furniture_handoff_dimension_key() == temporary._dimensions()
        ))

    @api.model
    def _need_buffer_approved_quantity(self, rule, role):
        """Only this buffer's unproduced target, never a final-piece consumer."""
        pieces = self.env['furniture.need.to.produce'].sudo().search([
            ('company_id', '=', rule.company_id.id),
            ('buffer_rule_id', '=', rule.id),
            ('state', '=', 'approved'),
            ('target_lane', '=', BUFFER_LANES[rule.stage_code]),
        ])
        result = 0.0
        for piece in pieces:
            for stage in piece.stage_ids.filtered(lambda row: row.lane == piece._target_stage_lane()):
                production = stage.production_id
                if production and production.state in ('cancelled', 'done'):
                    continue
                produced = sum(production.sudo().lane_output_ids.filtered(
                    lambda output: output.lane == role
                ).mapped('qty_ready')) if production else 0.0
                result += max(stage.quantity - produced, 0.0)
        return result

    @api.model
    def _need_buffer_metric(self, rule):
        Piece = self.env['furniture.need.to.produce'].sudo().with_company(rule.company_id)
        temporary = Piece.new({
            'company_id': rule.company_id.id,
            'product_id': rule.product_id.id,
            'furniture_model_id': rule.furniture_model_id.id,
            'bom_id': rule.bom_id.id,
            'target_lane': BUFFER_LANES[rule.stage_code],
        })
        role = BUFFER_ROLES[rule.stage_code]
        if Piece._parallel_finish_enabled():
            role = 'finish'
        # The same allocator used by approval subtracts both actual handoffs
        # and draft/approved planning claims from exact FIFO supply.
        supply = temporary._supply_pools(ignore_pieces=Piece.browse()).get(role, [])
        current = sum(row['quantity'] for row in supply if row['kind'] == 'stock')
        incoming_rows = [row for row in supply if row['kind'] == 'incoming']
        source_lines = self.env['furniture.mrp.production.line'].sudo().browse(
            [row['source_id'] for row in incoming_rows]
        ).exists()
        draft_ids = set(source_lines.filtered(
            lambda line: line.production_id.state == 'draft'
        ).ids)
        source_ids = set(source_lines.ids)
        draft = sum(row['quantity'] for row in incoming_rows if row['source_id'] in draft_ids)
        incoming = sum(row['quantity'] for row in incoming_rows
                       if row['source_id'] in source_ids and row['source_id'] not in draft_ids)
        # Approved buffer targets already enter the shared supply pool, net of
        # exact planning claims. Adding their gross quantity again would count
        # one future item twice and hide a real department shortage.
        outputs = self._need_buffer_outputs(temporary, role)
        # physical_available already excludes actual stock/handoff reservations;
        # its difference from free planning stock is unexecuted preview claims.
        planning_reserved = max(sum(outputs.mapped('physical_available_qty')) - current, 0.0)
        reserved = sum(outputs.mapped('reserved_qty')) + planning_reserved
        forecast = current + incoming + draft
        rounding = rule.uom_id.rounding or 0.001
        configured = float_compare(rule.max_qty, 0.0, precision_rounding=rounding) > 0
        needs = (
            configured
            and float_compare(current, rule.min_qty, precision_rounding=rounding) <= 0
            and float_compare(forecast, rule.max_qty, precision_rounding=rounding) < 0
        )
        status = (
            'disabled' if not configured else 'to_produce' if needs
            else 'draft' if draft > 0 else 'incoming' if incoming > 0
            else 'working' if current > 0 else 'ok'
        )
        return {
            'current_qty': round(current, 3),
            'incoming_qty': round(incoming, 3),
            'draft_qty': round(draft, 3),
            'reserved_qty': round(reserved, 3),
            'upstream_qty': 0.0,
            'forecast_qty': round(forecast, 3),
            'qty_to_produce': round(max(rule.max_qty - forecast, 0.0), 3) if needs else 0.0,
            'status': status,
        }

    @api.model
    def _stage_replenishment_metric_map(self, rules):
        rules = rules.exists()
        independent = rules.filtered(lambda rule: rule.stage_code in BUFFER_LANES)
        result = super()._stage_replenishment_metric_map(rules - independent)
        for rule in independent:
            result[rule.id] = self._need_buffer_metric(rule)
        return result

    @api.model
    def _stage_replenishment_dashboard_payload(self):
        result = super()._stage_replenishment_dashboard_payload()
        for row in result['rows']:
            if row['stage_code'] == 'finishing':
                row.update(stage=BUFFER_LABELS['finishing'], lane='preparation', lane_label=BUFFER_LABELS['finishing'])
        for stage in result['stages']:
            if stage['code'] == 'finishing':
                stage['label'] = BUFFER_LABELS['finishing']
        bases_rules = self.search([
            ('active', '=', True),
            ('company_id', '=', self.env.company.id),
            ('stage_code', '=', 'bases'),
        ])
        metrics = self._stage_replenishment_metric_map(bases_rules)
        need_count = 0
        for rule in bases_rules:
            values = metrics[rule.id]
            status = values['status']
            need_count += int(status == 'to_produce')
            result['rows'].append({
                'id': rule.id,
                'stage_code': 'bases',
                'stage': BUFFER_LABELS['bases'],
                'lane': 'bases',
                'lane_label': BUFFER_LABELS['bases'],
                'product': rule.product_id.display_name,
                'model': rule.furniture_model_id.display_name,
                'recipe': rule.bom_id.sudo().display_name,
                'unit': rule.uom_id.display_name,
                'minimum': rule.min_qty,
                'maximum': rule.max_qty,
                **values,
                'pipeline_qty': values['incoming_qty'],
                'status_label': self._stage_replenishment_status_label(status),
                'can_run': status == 'to_produce',
                'last_production_id': rule.last_production_id.id or False,
                'last_production': rule.last_production_id.display_name or False,
            })
        result['stages'].append({
            'code': 'bases', 'label': BUFFER_LABELS['bases'],
            'total': len(bases_rules), 'need': need_count,
        })
        result['summary'].update({
            'total_rules': len(result['rows']),
            # Min=0/Max>0 is an enabled policy, not an unconfigured rule.
            'configured': sum(row['maximum'] > 0 for row in result['rows']),
            'need_production': sum(row['status'] == 'to_produce' for row in result['rows']),
        })
        return result
