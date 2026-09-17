"""Registry-local extension: legacy orders retain their original routes."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


def _keep_existing_lane_records(records):
    if records:
        raise UserError('لا يمكن إزالة مسارات الإنتاج ما دامت لها سجلات مرتبطة.')


class Production(models.Model):
    _inherit = 'furniture.mrp.production'

    production_lane = fields.Selection(selection_add=[('bases', 'القواعد'), ('preparation', 'التجهيز')],
                                       ondelete={'bases': _keep_existing_lane_records, 'preparation': _keep_existing_lane_records})
    need_to_produce_stage_id = fields.Many2one('furniture.need.to.produce.stage', readonly=True,
                                              copy=False, index=True, ondelete='restrict')

    def _furniture_lane_routes(self):
        return {**super()._furniture_lane_routes(), 'bases': ('bases',), 'preparation': ('finishing',)}

    def _furniture_lane_final_stages(self):
        return {**super()._furniture_lane_final_stages(), 'bases': 'bases', 'preparation': 'finishing'}

    def _furniture_lane_ready_stages(self):
        return {**super()._furniture_lane_ready_stages(), 'bases': 'bases', 'preparation': 'finishing'}

    def _furniture_lane_output_lanes(self):
        return {**super()._furniture_lane_output_lanes(), 'preparation': 'finish'}

    def _furniture_lane_labels(self):
        return {**super()._furniture_lane_labels(), 'bases': 'القواعد', 'preparation': 'التجهيز'}

    def _furniture_lane_handoff_required_lanes(self):
        return {**super()._furniture_lane_handoff_required_lanes(), 'bases': ('frame',), 'preparation': ('bases',)}

    def _furniture_lane_handoff_target_stages(self):
        return {**super()._furniture_lane_handoff_target_stages(),
                'bases': ('bases', 'القواعد'), 'preparation': ('finishing', 'التجهيز')}

    def _furniture_lane_handoff_stage_models(self):
        return {**super()._furniture_lane_handoff_stage_models(),
                'furniture.mrp.bases': ('finish', 'bases'), 'furniture.mrp.finishing': ('preparation',)}

    def _ensure_lane_outputs(self, production_lines=False):
        outputs = super()._ensure_lane_outputs(production_lines)
        # Also retry when an existing output was completed/refreshed, not only
        # when a brand-new output row was inserted.
        self.env['furniture.need.to.produce.input']._release_available_inputs(outputs=outputs)
        return outputs

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get('need_to_produce_stage_id') for v in vals_list):
            raise AccessError('Production plans are linked only by approval.')
        return super().create(vals_list)

    def write(self, vals):
        if 'need_to_produce_stage_id' in vals and not self.env.su:
            raise AccessError('Production plans are linked only by approval.')
        return super().write(vals)

    def _prepare_material_lines_for_product_stage(self, product, qty, stage_code, production_line=False):
        plan_stage = self.sudo().need_to_produce_stage_id
        if plan_stage and plan_stage.materials_initialized:
            scale = qty / plan_stage.quantity
            overrides = [{'product_id': row.product_id.id, 'product_uom_id': row.uom_id.id,
                          'qty_needed': row.quantity * scale}
                         for row in plan_stage.material_ids if row.stage_code == stage_code]
            return self._prepare_material_override_commands(stage_code, overrides, production_line)
        return super()._prepare_material_lines_for_product_stage(product, qty, stage_code, production_line)

    def _prepare_material_lines_from_bom_with_factor(
        self, bom, output_qty, dimension_factor=1.0,
        production_line=False, active_stage_codes=None,
    ):
        plan_stage = self.sudo().need_to_produce_stage_id
        if plan_stage and plan_stage.materials_initialized:
            # Confirmation/recalculation use this BoM-wide entry point, while
            # requests use the per-stage entry point. Both must consume the
            # approved piece snapshot, including deleted or added materials.
            stages = active_stage_codes if active_stage_codes is not None else self._required_stage_codes()
            product = production_line.product_id if production_line else self.product_id
            commands = []
            for stage_code in stages:
                commands.extend(self._prepare_material_lines_for_product_stage(
                    product, output_qty, stage_code, production_line,
                ))
            return commands
        return super()._prepare_material_lines_from_bom_with_factor(
            bom, output_qty, dimension_factor=dimension_factor,
            production_line=production_line, active_stage_codes=active_stage_codes,
        )

    def _furniture_reserve_required_handoffs(self, source_outputs_by_lane=None, production_lines=False):
        self.ensure_one()
        stage = self.sudo().need_to_produce_stage_id
        if not stage:
            return super()._furniture_reserve_required_handoffs(source_outputs_by_lane, production_lines)
        stages = self.env['furniture.need.to.produce.stage'].sudo().search([('production_id', '=', self.id)])
        if (stage not in stages or any(s.company_id != self.company_id or s.piece_id.state != 'approved' for s in stages)
                or self.state not in ('confirmed', 'in_production')):
            raise UserError('خطة القطعة غير معتمدة أو أمرها غير جاهز لاستقبال التحويل.')
        self.env['furniture.need.to.produce']._lock_company(self.company_id)
        lines = self._furniture_handoff_lines(production_lines)
        approved_lines = self.production_line_ids.filtered('active')
        if not approved_lines or lines - approved_lines:
            raise UserError('أمر القطعة يجب أن يحتفظ بالصنف المعتمد دون تغيير.')
        if any(set(s.input_ids.mapped('role')) != set(self._furniture_handoff_required_lanes()) for s in stages):
            raise UserError('مدخلات المرحلة لا تطابق مسار القطعة المعتمد.')
        line_quantity = sum(line.product_uom_id._compute_quantity(
            line.product_qty, stage.piece_id.product_id.uom_id) for line in approved_lines)
        if (any(not stage.piece_id._same_product(line.product_id) or line.bom_id != stage.piece_id.bom_id
                or line.furniture_order_model_id != stage.piece_id.furniture_model_id
                or tuple(round(value or 0, 3) for value in (line.width_cm, line.depth_cm, line.height_cm)) != stage.piece_id._dimensions()
                for line in approved_lines)
                or abs(line_quantity - sum(stages.mapped('quantity'))) > 0.000001):
            raise UserError('كمية أو مواصفات أمر الإنتاج لا تطابق القطع المعتمدة.')
        if self.sudo().upstream_handoff_ids.filtered(lambda h: h.state != 'cancelled') - stages.input_ids.handoff_ids:
            raise UserError('يوجد تحويل خارج تخصيص هذه القطعة؛ راجع الخطة قبل الاستلام.')
        # Never replace an unfinished allocated predecessor with unrelated FIFO
        # stock. Receipt and quality checks still run in the existing workflow.
        stages.input_ids._reserve_ready_inputs()
        if not self._furniture_handoffs_cover_lines(lines):
            raise UserError('المرحلة تنتظر اكتمال المدخلات المحددة في خط هذه القطعة واستلامها.')
        return stages.input_ids.handoff_ids.filtered(lambda h: h.state in ('reserved', 'consumed'))


class Product(models.Model):
    _inherit = 'product.product'
    furniture_wip_lane = fields.Selection(selection_add=[('bases', 'القواعد')], ondelete={'bases': _keep_existing_lane_records})


class Output(models.Model):
    _inherit = 'furniture.mrp.lane.output'
    lane = fields.Selection(selection_add=[('bases', 'القواعد')], ondelete={'bases': _keep_existing_lane_records})

    def _furniture_auto_create_handoff_orders(self, downstream_lane, required_lanes, **kwargs):
        # The installed approval workflow owns creation. Completing any source
        # releases already-approved work; it never creates a second hidden MO.
        self.env['furniture.need.to.produce.input']._release_available_inputs(outputs=self if self else None)
        # Only continue the already-authorized pre-planner finishing chain.
        # New/draft piece previews and standalone stock are NOT approval.
        if self and downstream_lane == 'upholstery':
            candidates = self.sudo().search([
                ('company_id', 'in', self.company_id.ids),
                ('final_product_id', 'in', self.final_product_id.ids),
                ('furniture_model_id', 'in', self.furniture_model_id.ids),
                ('bom_id', 'in', self.bom_id.ids),
                ('lane', 'in', ('finish', 'tailoring')),
                ('production_id.need_to_produce_stage_id', '=', False),
                ('production_id.state', 'in', ('confirmed', 'in_production', 'done')),
            ])
            legacy_finish = candidates.filtered(lambda output: output.lane == 'finish' and (
                output.production_id.final_replenishment_rule_ids
                or output.production_id.upstream_handoff_ids.output_id.production_id.final_replenishment_rule_ids
            ))
            if legacy_finish:
                # Approved planning claims remain exclusive even before their
                # physical stock move can be reserved.
                claims = self.env['furniture.need.to.produce.input'].sudo().search([
                    ('company_id', 'in', self.company_id.ids), ('piece_id.state', '=', 'approved'),
                ])
                claimed_lines = claims.source_line_id | claims.source_stage_id.production_id.production_line_ids
                candidates -= candidates.filtered(lambda output:
                    output in claims.output_id or output.production_line_id in claimed_lines)
                candidates = candidates.filtered(lambda output: output.lane != 'finish' or output in legacy_finish)
                return super()._furniture_auto_create_handoff_orders(
                    downstream_lane, required_lanes, candidate_output_ids=candidates.ids, **kwargs)
        return self.env['furniture.mrp.production']

    @api.model_create_multi
    def create(self, vals_list):
        outputs = super().create(vals_list)
        self.env['furniture.need.to.produce.input']._release_available_inputs(outputs=outputs)
        return outputs


class Handoff(models.Model):
    _inherit = 'furniture.mrp.lane.handoff'
    role = fields.Selection(selection_add=[('bases', 'القواعد')], ondelete={'bases': _keep_existing_lane_records})


class StockMove(models.Model):
    _inherit = 'stock.move'
    furniture_lane_handoff_role = fields.Selection(selection_add=[('bases', 'القواعد')], ondelete={'bases': _keep_existing_lane_records})


class StageRules(models.Model):
    _inherit = 'furniture.mrp.stage.replenishment.rule'

    def _stage_replenishment_recipe_rule_stages(self, bom):
        return tuple(dict.fromkeys((*super()._stage_replenishment_recipe_rule_stages(bom),
                                    *(('bases',) if 'bases' in bom._get_active_stage_codes() else ()))))

    def _stage_replenishment_preserve_retired_limits(self, existing_by_key):
        # Bases and finishing are independent policies in this registry.
        return None

    def _stage_replenishment_run_company(self, company, *args, **kwargs):
        self.env['furniture.need.to.produce']._refresh_company(company)
        return self.env['furniture.mrp.production']


class FinalRules(models.Model):
    _inherit = 'furniture.mrp.final.replenishment.rule'

    def _final_replenishment_run_company(self, company, *args, **kwargs):
        self.env['furniture.need.to.produce']._refresh_company(company)
        # Recovery for a busy completion callback; only old authorized chains
        # can create an MO through the guarded Output bridge above.
        outputs = self.env['furniture.mrp.lane.output'].sudo().search([
            ('company_id', '=', company.id), ('lane', '=', 'finish'),
            ('production_id.need_to_produce_stage_id', '=', False),
        ])
        self._final_replenishment_on_ready_outputs(outputs)
        return self.env['furniture.mrp.production']
