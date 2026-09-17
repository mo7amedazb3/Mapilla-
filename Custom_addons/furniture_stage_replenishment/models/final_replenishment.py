# -*- coding: utf-8 -*-

"""Finished-goods demand coordinator for the physical factory route.

Every component buffer keeps its independent Min/Max policy.  A triggered
finished-goods rule is an additional real-demand override: it fills any exact
shortage that the stage buffers and their open orders do not cover, even when
that demand is above a stage Max.  Matching finish + tailoring buffers create
upholstery.  Packaging then waits for both upholstery + painting and is the
operation that moves the saleable product into finished goods.
"""

import logging
import math
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_round

from .stage_replenishment import (
    RULE_STATUS_SELECTION,
    STAGE_REPLENISHMENT_MINMAX_LANE_CONTROLLERS as STAGE_REPLENISHMENT_LANE_CONTROLLERS,
    STAGE_REPLENISHMENT_LANE_LABELS,
)


FINAL_RULE_STATUS_LABELS = dict(RULE_STATUS_SELECTION)
_logger = logging.getLogger(__name__)

# A final Min/Max rule is meaningful only when its recipe can traverse the
# complete physical factory route.  The handoff engine can create upholstery
# and packaging orders, but it must not silently repair an incomplete recipe.
FINAL_REPLENISHMENT_REQUIRED_STAGES = frozenset({
    'priming',
    'carpentry',
    'bases',
    'finishing',
    'tailoring',
    'painting',
    'upholstery',
    'packaging',
})


class FurnitureMrpProductionFinalReplenishment(models.Model):
    _inherit = 'furniture.mrp.production'

    def _ensure_lane_outputs(self, production_lines=False):
        outputs = super()._ensure_lane_outputs(production_lines)
        # The receipt and FIFO output now exist. Do not wait for the ten-minute
        # Min/Max scan to pair the final finishing/tailoring component.
        self.env['furniture.mrp.final.replenishment.rule']._final_replenishment_on_ready_outputs(outputs)
        return outputs

    final_replenishment_rule_ids = fields.Many2many(
        'furniture.mrp.final.replenishment.rule',
        'furn_final_replenishment_production_rel',
        'production_id',
        'rule_id',
        string='Finished Goods Min/Max Rules',
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any('final_replenishment_rule_ids' in vals for vals in vals_list):
            raise AccessError(_(
                'Finished-goods replenishment markers are managed by the '
                'generator only.'
            ))
        return super().create(vals_list)

    def write(self, vals):
        if 'final_replenishment_rule_ids' in vals:
            raise AccessError(_(
                'Finished-goods replenishment markers are managed by the '
                'generator only.'
            ))
        return super().write(vals)

    def _final_replenishment_internal_write(self, vals):
        return self.sudo()._stage_replenishment_internal_write(vals)


class FurnitureMrpFinalReplenishmentRule(models.Model):
    _name = 'furniture.mrp.final.replenishment.rule'
    _description = 'Furniture Finished Goods Min/Max Replenishment Rule'
    _order = 'product_id, furniture_model_id, id'
    _check_company_auto = True

    active = fields.Boolean(default=True, index=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
        index=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', required=True, readonly=True, index=True,
        ondelete='restrict', string='المنتج',
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model', required=True, readonly=True, index=True,
        ondelete='restrict', string='الموديل',
    )
    bom_id = fields.Many2one(
        'mrp.bom', required=True, readonly=True, index=True,
        ondelete='restrict', check_company=True, string='الريسيبي',
    )
    uom_id = fields.Many2one(
        'uom.uom', related='product_id.uom_id', readonly=True,
        string='الوحدة',
    )
    min_qty = fields.Float(
        string='Min المنتج التام', default=0.0, required=True,
        digits='Product Unit of Measure',
    )
    max_qty = fields.Float(
        string='Max المنتج التام', default=0.0, required=True,
        digits='Product Unit of Measure',
    )
    last_generated_at = fields.Datetime(readonly=True, copy=False)
    last_body_production_id = fields.Many2one(
        'furniture.mrp.production', readonly=True, copy=False,
        ondelete='set null', string='آخر أمر نجارة قديم',
    )
    last_cover_production_id = fields.Many2one(
        'furniture.mrp.production', readonly=True, copy=False,
        ondelete='set null', string='آخر أمر كسوة',
    )
    last_frame_production_id = fields.Many2one(
        'furniture.mrp.production', readonly=True, copy=False,
        ondelete='set null', string='آخر أمر نجارة',
    )
    replenishment_cycle_active = fields.Boolean(
        readonly=True,
        copy=False,
        index=True,
        string='دورة استكمال الـMax نشطة',
        help=(
            'تظل الدورة نشطة بعد وصول المنتج إلى Min حتى يكتمل رصيد '
            'المنتج التام إلى Max، ولو اكتملت المكونات على دفعات.'
        ),
    )

    finished_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='المتاح تام',
    )
    body_wip_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='نجارة جاهزة',
    )
    body_incoming_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='نجارة جارية',
    )
    body_draft_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='مسودة نجارة',
    )
    body_coverage_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='تغطية النجارة',
    )
    body_qty_to_produce = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='مطلوب نجارة',
    )
    cover_wip_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='تفصيل جاهز',
    )
    cover_incoming_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='تفصيل جارٍ',
    )
    cover_draft_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='مسودة تفصيل',
    )
    cover_coverage_qty = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='تغطية التفصيل',
    )
    cover_qty_to_produce = fields.Float(
        compute='_compute_final_replenishment_metrics',
        digits='Product Unit of Measure', string='مطلوب تفصيل',
    )
    replenishment_status = fields.Selection(
        RULE_STATUS_SELECTION, compute='_compute_final_replenishment_metrics',
        string='الحالة',
    )

    _sql_constraints = [
        (
            'final_replenishment_identity_unique',
            'unique(company_id, product_id, furniture_model_id)',
            'A finished-goods rule already exists for this product and model.',
        ),
        (
            'final_replenishment_nonnegative_limits',
            'check(min_qty >= 0 AND max_qty >= 0)',
            'Minimum and maximum quantities cannot be negative.',
        ),
        (
            'final_replenishment_limit_order',
            'check(min_qty <= max_qty)',
            'Minimum quantity cannot exceed maximum quantity.',
        ),
    ]

    @api.model
    def _final_replenishment_check_manager(self):
        user = self.env.user
        if not (
            user._is_admin()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
        ):
            raise AccessError(_(
                'Finished-goods replenishment is available to factory '
                'managers only.'
            ))

    @api.model_create_multi
    def create(self, vals_list):
        self._final_replenishment_check_manager()
        raise AccessError(_(
            'Finished-goods identities are generated from live recipes. '
            'Use Refresh Recipes instead of creating a row.'
        ))

    @api.model
    def _final_replenishment_internal_create(self, vals_list):
        return super(
            FurnitureMrpFinalReplenishmentRule,
            self.sudo(),
        ).create(vals_list)

    def write(self, vals):
        internal_fields = {
            'company_id', 'product_id', 'furniture_model_id', 'bom_id',
            'active', 'last_generated_at', 'last_body_production_id',
            'last_cover_production_id', 'last_frame_production_id',
            'replenishment_cycle_active',
        }
        if internal_fields & set(vals):
            raise AccessError(_(
                'Product, model, recipe and generation audit fields are '
                'managed automatically.'
            ))
        if {'min_qty', 'max_qty'} & set(vals):
            self._final_replenishment_check_manager()
        result = super().write(vals)
        if {'min_qty', 'max_qty'} & set(vals):
            self._final_replenishment_internal_write({
                'replenishment_cycle_active': False,
            })
        return result

    def _final_replenishment_internal_write(self, vals):
        return super(
            FurnitureMrpFinalReplenishmentRule,
            self.sudo(),
        ).write(vals)

    @api.constrains('min_qty', 'max_qty')
    def _check_final_replenishment_limits(self):
        for rule in self:
            if not math.isfinite(rule.min_qty) or not math.isfinite(rule.max_qty):
                raise ValidationError(_('Minimum and maximum must be finite numbers.'))
            if float_compare(rule.min_qty, 0.0, precision_digits=3) < 0:
                raise ValidationError(_('Minimum quantity cannot be negative.'))
            if float_compare(rule.max_qty, 0.0, precision_digits=3) < 0:
                raise ValidationError(_('Maximum quantity cannot be negative.'))
            if float_compare(rule.min_qty, rule.max_qty, precision_digits=3) > 0:
                raise ValidationError(_('Minimum quantity cannot exceed maximum quantity.'))

    @api.model
    def _final_replenishment_sync_company(self, company=None, lock=True):
        company = company or self.env.company
        StageRule = self.env['furniture.mrp.stage.replenishment.rule'].sudo()
        if lock:
            StageRule._stage_replenishment_lock_company(company)
        stage_rules = StageRule.with_company(
            company
        )._stage_replenishment_sync_company(company, lock=False)
        stage_by_key = {
            (rule.stage_code, rule.product_id.id, rule.furniture_model_id.id): rule
            for rule in stage_rules
        }

        Rule = self.sudo().with_company(company).with_context(active_test=False)
        existing = Rule.search([('company_id', '=', company.id)])
        existing_by_key = {
            (rule.product_id.id, rule.furniture_model_id.id): rule
            for rule in existing
        }
        desired_keys = set()
        for bom in StageRule._stage_replenishment_canonical_recipes(company):
            active_stages = set(bom._get_active_stage_codes())
            if not FINAL_REPLENISHMENT_REQUIRED_STAGES.issubset(active_stages):
                continue
            product = StageRule._stage_replenishment_canonical_product(
                bom.furniture_product_id
            )
            model = bom.furniture_model_id
            key = (product.id, model.id)
            desired_keys.add(key)
            values = {
                'company_id': company.id,
                'product_id': product.id,
                'furniture_model_id': model.id,
                'bom_id': bom.id,
                'active': True,
            }
            rule = existing_by_key.get(key)
            if rule:
                changes = {
                    field_name: value
                    for field_name, value in values.items()
                    if (
                        rule[field_name].id
                        if rule._fields[field_name].type == 'many2one'
                        else rule[field_name]
                    ) != value
                }
                if changes:
                    rule._final_replenishment_internal_write(changes)
                continue

            # Preserve the live policy during the upgrade: the old body/output
            # controller was the only configured master before this model.
            body_rule = stage_by_key.get(('finishing', product.id, model.id))
            if body_rule and body_rule.max_qty > 0:
                values.update({
                    'min_qty': body_rule.min_qty,
                    'max_qty': body_rule.max_qty,
                })
            existing_by_key[key] = Rule._final_replenishment_internal_create(
                [values]
            )

        obsolete = existing.filtered(lambda rule: (
            rule.active
            and (rule.product_id.id, rule.furniture_model_id.id)
            not in desired_keys
        ))
        if obsolete:
            obsolete._final_replenishment_internal_write({'active': False})
        return Rule.with_context(active_test=True).search([
            ('company_id', '=', company.id),
        ])

    @api.model
    def _final_replenishment_finished_product_candidates(self, rule):
        Product = self.env['product.product'].sudo().with_context(
            active_test=False,
        )
        return Product.search([
            '|',
            ('id', '=', rule.product_id.id),
            '&',
            ('furniture_model_id', '=', rule.furniture_model_id.id),
            ('furniture_dimension_source_product_id', '=', rule.product_id.id),
        ])

    @api.model
    def _final_replenishment_finished_stock_map(self, rules):
        result = defaultdict(float)
        Quant = self.env['stock.quant'].sudo()
        finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        if not finished_location:
            return result
        for rule in rules:
            quantity = 0.0
            for product in self._final_replenishment_finished_product_candidates(rule):
                quantity += max(Quant.with_company(rule.company_id)._get_available_quantity(
                    product,
                    finished_location,
                    lot_id=self.env['stock.lot'],
                    package_id=self.env['stock.quant.package'],
                    owner_id=self.env['res.partner'],
                    strict=True,
                ), 0.0)
            result[(
                rule.company_id.id,
                rule.product_id.id,
                rule.furniture_model_id.id,
            )] = quantity
        return result

    @api.model
    def _final_replenishment_controller_rules(self, final_rules):
        StageRule = self.env['furniture.mrp.stage.replenishment.rule'].sudo()
        if not final_rules:
            return StageRule
        return StageRule.search([
            ('active', '=', True),
            ('company_id', 'in', final_rules.mapped('company_id').ids),
            ('product_id', 'in', final_rules.mapped('product_id').ids),
            ('furniture_model_id', 'in', final_rules.mapped('furniture_model_id').ids),
            ('stage_code', 'in', tuple(
                STAGE_REPLENISHMENT_LANE_CONTROLLERS.values()
            )),
        ])

    @api.model
    def _final_replenishment_metric_map(self, rules):
        rules = rules.exists()
        if not rules:
            return {}
        StageRule = self.env['furniture.mrp.stage.replenishment.rule'].sudo()
        controller_rules = self._final_replenishment_controller_rules(rules)
        controller_metrics = StageRule._stage_replenishment_metric_map(
            controller_rules
        )
        controller_by_key = {
            (
                rule.company_id.id,
                rule.stage_code,
                rule.product_id.id,
                rule.furniture_model_id.id,
            ): rule
            for rule in controller_rules
        }
        finished_map = self._final_replenishment_finished_stock_map(rules)
        result = {}
        for rule in rules:
            identity = (
                rule.company_id.id,
                rule.product_id.id,
                rule.furniture_model_id.id,
            )
            finished = max(finished_map.get(identity, 0.0), 0.0)
            # Min=0 is a valid empty-stock trigger, not a disabled rule.
            configured = bool(rule.max_qty > 0)
            triggered = bool(configured and (
                rule.replenishment_cycle_active
                or float_compare(
                    finished,
                    rule.min_qty,
                    precision_rounding=rule.uom_id.rounding or 0.001,
                ) <= 0
            ))
            flow_quantities = [
                self._final_replenishment_open_flow_quantities(rule, product)
                for product in self._final_replenishment_finished_product_candidates(
                    rule
                )
            ]
            open_flow_qty = sum(
                quantities['flow_qty'] for quantities in flow_quantities
            )
            open_packaging_qty = sum(
                quantities['packaging_qty'] for quantities in flow_quantities
            )
            component_demand = (
                max(rule.max_qty - finished - open_flow_qty, 0.0)
                if triggered else 0.0
            )
            painting_demand = (
                max(rule.max_qty - finished - open_packaging_qty, 0.0)
                if triggered else 0.0
            )
            lanes = {}
            for lane, controller in STAGE_REPLENISHMENT_LANE_CONTROLLERS.items():
                controller_rule = controller_by_key.get((
                    rule.company_id.id,
                    controller,
                    rule.product_id.id,
                    rule.furniture_model_id.id,
                ))
                values = controller_metrics.get(
                    controller_rule.id if controller_rule else 0,
                    {},
                )
                free_wip = max(values.get('current_qty', 0.0), 0.0)
                reserved_wip = max(values.get('reserved_qty', 0.0), 0.0)
                incoming = max(values.get('incoming_qty', 0.0), 0.0)
                draft = max(values.get('draft_qty', 0.0), 0.0)
                # Reserved output has already become an input of the next open
                # flow order.  Count that resulting order above, rather than
                # counting the same physical piece again as component stock.
                component_coverage = free_wip + incoming + draft
                lane_open_flow_qty = (
                    open_packaging_qty if lane == 'painting' else open_flow_qty
                )
                lane_demand = (
                    painting_demand if lane == 'painting' else component_demand
                )
                coverage = finished + lane_open_flow_qty + component_coverage
                shortage = 0.0
                if (
                    triggered
                    and lane in ('finish', 'tailoring', 'painting')
                    and controller_rule
                    and float_compare(
                        component_coverage,
                        lane_demand,
                        precision_rounding=rule.uom_id.rounding or 0.001,
                    ) < 0
                ):
                    shortage = float_round(
                        max(lane_demand - component_coverage, 0.0),
                        precision_rounding=rule.uom_id.rounding or 0.001,
                        rounding_method='UP',
                    )
                lanes[lane] = {
                    'rule': controller_rule,
                    'wip_qty': free_wip + reserved_wip,
                    'free_wip_qty': free_wip,
                    'reserved_wip_qty': reserved_wip,
                    'incoming_qty': incoming,
                    'draft_qty': draft,
                    'coverage_qty': coverage,
                    'qty_to_produce': shortage,
                }

            # A physical piece must be counted exactly once while it travels
            # through the serial body route.  Once frame WIP is consumed by a
            # finish order it must not disappear from final coverage and cause
            # another woodworking draft on the next cron pass.
            finish_in_process_qty = 0.0
            for product in self._final_replenishment_finished_product_candidates(
                rule
            ):
                finish_in_process_qty += (
                    self._final_replenishment_finish_in_process_qty(
                        rule,
                        product,
                    )
                )
            frame_values = lanes.get('frame', {})
            finish_values = lanes.get('finish', {})
            serial_coverage = (
                finished
                + frame_values.get('free_wip_qty', 0.0)
                + frame_values.get('reserved_wip_qty', 0.0)
                + frame_values.get('incoming_qty', 0.0)
                + frame_values.get('draft_qty', 0.0)
                + finish_values.get('free_wip_qty', 0.0)
                + finish_in_process_qty
                + open_flow_qty
            )
            if frame_values:
                frame_values['coverage_qty'] = serial_coverage
                frame_values['qty_to_produce'] = (
                    float_round(
                        max(rule.max_qty - serial_coverage, 0.0),
                        precision_rounding=rule.uom_id.rounding or 0.001,
                        rounding_method='UP',
                    )
                    if (
                        triggered
                        and frame_values.get('rule')
                        and float_compare(
                            serial_coverage,
                            rule.max_qty,
                            precision_rounding=rule.uom_id.rounding or 0.001,
                        ) < 0
                    ) else 0.0
                )

            if not configured:
                status = 'disabled'
            elif any(lane['qty_to_produce'] > 0 for lane in lanes.values()):
                status = 'to_produce'
            elif any(lane['draft_qty'] > 0 for lane in lanes.values()):
                status = 'draft'
            elif any(lane['incoming_qty'] > 0 for lane in lanes.values()):
                status = 'incoming'
            elif any(lane['wip_qty'] > 0 for lane in lanes.values()):
                status = 'working'
            else:
                status = 'ok'
            result[rule.id] = {
                'finished_qty': finished,
                'open_flow_qty': open_flow_qty,
                'open_packaging_qty': open_packaging_qty,
                'component_demand_qty': component_demand,
                'painting_demand_qty': painting_demand,
                'triggered': triggered,
                'configured': configured,
                'status': status,
                **lanes,
                # Compatibility aliases keep older views and API clients
                # readable.  ``cover`` now represents the independent cloth
                # buffer; upholstery itself has no Min/Max card.
                'body': lanes['frame'],
                'cover': lanes['tailoring'],
            }
        return result

    def _compute_final_replenishment_metrics(self):
        metrics = self._final_replenishment_metric_map(self)
        for rule in self:
            values = metrics.get(rule.id, {})
            body = values.get('body', {})
            cover = values.get('cover', {})
            rule.finished_qty = values.get('finished_qty', 0.0)
            rule.body_wip_qty = body.get('wip_qty', 0.0)
            rule.body_incoming_qty = body.get('incoming_qty', 0.0)
            rule.body_draft_qty = body.get('draft_qty', 0.0)
            rule.body_coverage_qty = body.get('coverage_qty', 0.0)
            rule.body_qty_to_produce = body.get('qty_to_produce', 0.0)
            rule.cover_wip_qty = cover.get('wip_qty', 0.0)
            rule.cover_incoming_qty = cover.get('incoming_qty', 0.0)
            rule.cover_draft_qty = cover.get('draft_qty', 0.0)
            rule.cover_coverage_qty = cover.get('coverage_qty', 0.0)
            rule.cover_qty_to_produce = cover.get('qty_to_produce', 0.0)
            rule.replenishment_status = values.get('status', 'disabled')

    @api.model
    def _final_replenishment_lock_finished_stock(self, company, rules):
        finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        if not finished_location:
            raise UserError(_('Finished-goods location is not configured.'))
        product_ids = set()
        for rule in rules:
            product_ids.update(
                self._final_replenishment_finished_product_candidates(rule).ids
            )
        if not product_ids:
            return
        self.env.cr.execute(
            'SELECT id FROM stock_quant '
            'WHERE company_id = %s AND location_id = %s AND product_id IN %s '
            'ORDER BY id FOR UPDATE NOWAIT',
            [company.id, finished_location.id, tuple(sorted(product_ids))],
        )

    @api.model
    def _final_replenishment_open_flow_quantities(self, rule, product):
        """Return de-duplicated open flow and its packaging portion."""
        Production = self.env['furniture.mrp.production'].sudo()
        open_productions = Production.search([
            ('company_id', '=', rule.company_id.id),
            ('production_lane', 'in', ('upholstery', 'packaging')),
            ('state', 'not in', ('done', 'cancelled')),
        ])
        quantity_by_lane = defaultdict(float)
        for production in open_productions:
            for line in production.production_line_ids.filtered(lambda row: (
                row.active
                and row.furniture_order_model_id == rule.furniture_model_id
                and row.bom_id == rule.bom_id
            )):
                line_product = (
                    production._get_or_create_dimensioned_finished_product_for_line(
                        line
                    )
                    or line.product_id
                )
                if line_product != product:
                    continue
                quantity_by_lane[production.production_lane] += (
                    production._quantity_in_product_uom(
                        product,
                        line.product_qty,
                        line.product_uom_id or product.uom_id,
                    )
                )

        # A packaging draft is sourced from a completed upholstery output.
        # Subtract that source once so the same physical piece is not counted
        # both as its old upholstery order and its new packaging order.
        handoffs = self.env['furniture.mrp.lane.handoff'].sudo().search([
            ('company_id', '=', rule.company_id.id),
            ('role', '=', 'upholstery'),
            ('state', 'in', ('reserved', 'consumed')),
            ('output_id.final_product_id', '=', product.id),
            ('output_id.furniture_model_id', '=', rule.furniture_model_id.id),
            ('output_id.bom_id', '=', rule.bom_id.id),
            ('output_id.production_id.state', 'not in', ('done', 'cancelled')),
            ('downstream_production_id.state', 'not in', ('done', 'cancelled')),
        ])
        packaging_qty = quantity_by_lane['packaging']
        flow_qty = max(
            quantity_by_lane['upholstery']
            + packaging_qty
            - sum(handoffs.mapped('quantity')),
            0.0,
        )
        return {
            'flow_qty': flow_qty,
            'packaging_qty': packaging_qty,
        }

    @api.model
    def _final_replenishment_open_flow_qty(self, rule, product):
        """Compatibility wrapper returning the de-duplicated open flow."""
        return self._final_replenishment_open_flow_quantities(
            rule,
            product,
        )['flow_qty']

    @api.model
    def _final_replenishment_finish_in_process_qty(self, rule, product):
        """Return physical frames already consumed by an unfinished finish MO.

        A draft/confirmed finish quantity is planning demand, not another
        physical piece.  It becomes a serial-flow piece only after its FIFO
        frame handoff is consumed.  Once a finish output is received, that
        output is counted by the finish buffer instead, so subtract it here.
        """
        Handoff = self.env['furniture.mrp.lane.handoff'].sudo()
        handoffs = Handoff.search([
            ('company_id', '=', rule.company_id.id),
            ('role', '=', 'frame'),
            ('state', '=', 'consumed'),
            ('output_id.final_product_id', '=', product.id),
            ('output_id.furniture_model_id', '=', rule.furniture_model_id.id),
            ('output_id.bom_id', '=', rule.bom_id.id),
            ('downstream_production_id.production_lane', '=', 'finish'),
            ('downstream_production_id.state', 'not in', ('done', 'cancelled')),
        ])
        if not handoffs:
            return 0.0

        consumed_by_line = defaultdict(float)
        for handoff in handoffs:
            consumed_by_line[handoff.downstream_line_id.id] += handoff.quantity

        received_by_line = defaultdict(float)
        outputs = self.env['furniture.mrp.lane.output'].sudo().search([
            ('company_id', '=', rule.company_id.id),
            ('lane', '=', 'finish'),
            ('production_line_id', 'in', list(consumed_by_line)),
            ('final_product_id', '=', product.id),
            ('furniture_model_id', '=', rule.furniture_model_id.id),
            ('bom_id', '=', rule.bom_id.id),
            ('origin_receipt_move_id.state', '=', 'done'),
        ])
        for output in outputs:
            received_by_line[output.production_line_id.id] += max(
                output.qty_ready or 0.0,
                0.0,
            )
        return sum(
            max(quantity - received_by_line.get(line_id, 0.0), 0.0)
            for line_id, quantity in consumed_by_line.items()
        )

    @api.model
    def _final_replenishment_create_upholstery_available(self, rule, values, source_outputs=None):
        """Create upholstery only from real finish and tailoring buffers."""
        if not values.get('triggered'):
            return self.env['furniture.mrp.production']
        productions = self.env['furniture.mrp.production']
        Output = (source_outputs if source_outputs is not None else self.env['furniture.mrp.lane.output']).sudo()
        products = self._final_replenishment_finished_product_candidates(
            rule
        ).sorted('id')
        for product in products:
            # The final rule is shared by the canonical product and every
            # dimensioned derivative.  Therefore its Max is one global cap,
            # not a fresh allowance for each candidate product.
            open_flow_qty = sum(
                self._final_replenishment_open_flow_qty(rule, candidate)
                for candidate in products
            )
            remaining = max(
                rule.max_qty
                - values.get('finished_qty', 0.0)
                - open_flow_qty,
                0.0,
            )
            if float_compare(
                remaining,
                0.0,
                precision_rounding=rule.uom_id.rounding or 0.001,
            ) <= 0:
                break
            created = Output._furniture_auto_create_handoff_orders(
                'upholstery',
                ('finish', 'tailoring'),
                company=rule.company_id,
                final_product=product,
                furniture_model=rule.furniture_model_id,
                bom=rule.bom_id,
                maximum_quantity=remaining,
            )
            if created and 'final_replenishment_rule_ids' in created._fields:
                created._final_replenishment_internal_write({
                    'final_replenishment_rule_ids': [(6, 0, [rule.id])],
                })
            if created:
                rule._final_replenishment_internal_write({
                    'last_generated_at': fields.Datetime.now(),
                    'last_cover_production_id': created.sorted('id')[-1].id,
                })
            productions |= created
        return productions

    @api.model
    def _final_replenishment_on_ready_outputs(self, outputs):
        """Targeted, idempotent completion callback; never generate upstream work."""
        outputs = outputs.sudo().exists().filtered(lambda output: output.lane in ('finish', 'tailoring'))
        created = self.env['furniture.mrp.production']
        for company in outputs.company_id.sorted('id'):
            try:
                company_created = self.env['furniture.mrp.production']
                with self.env.cr.savepoint():
                    # Completion already owns stock/line locks. Never block on
                    # a planner that acquired the company lock in reverse order.
                    if not self._final_replenishment_try_lock_company(company):
                        self._final_replenishment_wake_coordinator()
                        continue
                    ready = outputs.filtered(lambda output: output.company_id == company)
                    products = ready.final_product_id
                    canonical = products.mapped('furniture_dimension_source_product_id') | products
                    rules = self.sudo().with_company(company).search([
                        ('company_id', '=', company.id), ('active', '=', True), ('max_qty', '>', 0),
                        ('product_id', 'in', canonical.ids),
                        ('furniture_model_id', 'in', ready.furniture_model_id.ids),
                        ('bom_id', 'in', ready.bom_id.ids),
                    ])
                    for rule in rules.sorted('id'):
                        matching = ready.filtered(lambda output:
                            (output.final_product_id.furniture_dimension_source_product_id or output.final_product_id) == rule.product_id
                            and output.furniture_model_id == rule.furniture_model_id and output.bom_id == rule.bom_id)
                        if not matching:
                            continue
                        self.env.cr.execute('SELECT id FROM furniture_mrp_final_replenishment_rule WHERE id=%s FOR UPDATE NOWAIT', [rule.id])
                        rule.invalidate_recordset()
                        self._final_replenishment_lock_finished_stock(company, rule)
                        values = self._final_replenishment_metric_map(rule).get(rule.id, {})
                        company_created |= self._final_replenishment_create_upholstery_available(rule, values, source_outputs=matching)
                created |= company_created
            except Exception:
                # A downstream configuration/lock problem must not undo a
                # successful upstream receipt. Retry promptly after commit.
                _logger.exception('Unable to release ready upholstery for company %s', company.id)
                self._final_replenishment_wake_coordinator()
        return created

    @api.model
    def _final_replenishment_try_lock_company(self, company):
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', [4608850, company.id])
        return self.env.cr.fetchone()[0]

    @api.model
    def _final_replenishment_wake_coordinator(self):
        cron = self.env.ref('furniture_stage_replenishment.ir_cron_furniture_mrp_stage_replenishment', raise_if_not_found=False)
        if cron and cron.sudo().active:
            cron.sudo()._trigger()

    @api.model
    def _final_replenishment_run_company(
        self,
        company,
        restricted_rule_ids=None,
        lock=True,
        raise_on_error=False,
    ):
        StageRule = self.env['furniture.mrp.stage.replenishment.rule'].sudo()
        if lock:
            StageRule._stage_replenishment_lock_company(company)
        all_rules = self.with_company(company)._final_replenishment_sync_company(
            company,
            lock=False,
        )
        rules = all_rules.filtered(lambda rule: rule.max_qty > 0)
        if restricted_rule_ids is not None:
            requested = {int(rule_id) for rule_id in restricted_rule_ids}
            if not requested:
                return self.env['furniture.mrp.production']
            rules = rules.filtered(lambda rule: rule.id in requested)
        if not rules:
            return self.env['furniture.mrp.production']

        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_final_replenishment_rule '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(rules.ids)],
        )
        rules.invalidate_recordset(['min_qty', 'max_qty', 'bom_id'])
        controller_rules = self._final_replenishment_controller_rules(rules)
        StageRule._stage_replenishment_lock_editable_workload(
            company,
            controller_rules,
        )
        self._final_replenishment_lock_finished_stock(company, rules)
        finished_before = self._final_replenishment_finished_stock_map(rules)
        for rule in rules:
            identity = (
                rule.company_id.id,
                rule.product_id.id,
                rule.furniture_model_id.id,
            )
            finished = finished_before.get(identity, 0.0)
            if (
                not rule.replenishment_cycle_active
                and float_compare(
                    finished,
                    rule.min_qty,
                    precision_rounding=rule.uom_id.rounding or 0.001,
                ) <= 0
            ):
                rule._final_replenishment_internal_write({
                    'replenishment_cycle_active': True,
                })
            elif (
                rule.replenishment_cycle_active
                and float_compare(
                    finished,
                    rule.max_qty,
                    precision_rounding=rule.uom_id.rounding or 0.001,
                ) >= 0
            ):
                rule._final_replenishment_internal_write({
                    'replenishment_cycle_active': False,
                })
        metrics = self._final_replenishment_metric_map(rules)

        # Upholstery opens from finish + tailoring.  Painting remains an
        # independent buffer and joins the completed upholstery only when the
        # packaging order is created.
        flow_productions = self.env['furniture.mrp.production']
        for rule in rules.sorted('id'):
            flow_productions |= self._final_replenishment_create_upholstery_available(
                rule,
                metrics.get(rule.id, {}),
            )
        finished_after = self._final_replenishment_finished_stock_map(rules)
        for rule in rules.filtered('replenishment_cycle_active'):
            identity = (
                rule.company_id.id,
                rule.product_id.id,
                rule.furniture_model_id.id,
            )
            if float_compare(
                finished_after.get(identity, 0.0),
                rule.max_qty,
                precision_rounding=rule.uom_id.rounding or 0.001,
            ) >= 0:
                rule._final_replenishment_internal_write({
                    'replenishment_cycle_active': False,
                })

        pending_groups = defaultdict(list)
        for rule in rules:
            values = metrics.get(rule.id, {})
            # Finish is deliberately absent here.  It is a serial downstream
            # lane and is created by the handoff engine only when the frame
            # order completes carpentry and produces physical FIFO stock.
            for lane in ('frame', 'tailoring', 'painting'):
                lane_values = values.get(lane, {})
                quantity = lane_values.get('qty_to_produce', 0.0)
                controller_rule = lane_values.get('rule')
                if not controller_rule or quantity <= 0:
                    continue
                group_key = StageRule._stage_replenishment_order_group_key(
                    rule.product_id,
                    rule.furniture_model_id,
                    lane=lane,
                )
                pending_groups[group_key].append((
                    rule,
                    lane,
                    controller_rule,
                    quantity,
                ))

        affected = flow_productions
        for group_key in sorted(pending_groups):
            rows = pending_groups[group_key]
            try:
                with self.env.cr.savepoint():
                    specs = [
                        StageRule._stage_replenishment_prepare_product_spec(
                            controller_rule,
                            quantity,
                            covered_rules=controller_rule,
                        )
                        for _rule, _lane, controller_rule, quantity in rows
                    ]
                    production = StageRule._stage_replenishment_apply_production_group(
                        specs
                    )
                    if not production:
                        continue
                    final_rules = self.browse([
                        rule.id for rule, _lane, _controller, _quantity in rows
                    ])
                    production._final_replenishment_internal_write({
                        'final_replenishment_rule_ids': [(
                            6,
                            0,
                            (production.final_replenishment_rule_ids | final_rules).ids,
                        )],
                    })
                    generated_at = fields.Datetime.now()
                    for rule, lane, _controller, _quantity in rows:
                        audit_values = {'last_generated_at': generated_at}
                        if lane == 'frame':
                            audit_values.update({
                                'last_frame_production_id': production.id,
                                'last_body_production_id': production.id,
                            })
                        rule._final_replenishment_internal_write(audit_values)
                    affected |= production
            except Exception:
                if raise_on_error:
                    raise
                _logger.exception(
                    'Unable to generate finished-goods replenishment for %s',
                    [
                        rule.product_id.display_name
                        for rule, _lane, _controller, _quantity in rows
                    ],
                )
        return StageRule._stage_replenishment_auto_confirm_orders(affected)

    @api.model
    def _final_replenishment_dashboard_payload(self):
        rules = self.search([
            ('active', '=', True),
            ('company_id', '=', self.env.company.id),
        ])
        metrics = self._final_replenishment_metric_map(rules)
        rows = []
        for rule in rules:
            values = metrics.get(rule.id, {})
            body = values.get('body', {})
            cover = values.get('cover', {})
            components = []
            for lane in ('frame', 'finish', 'tailoring', 'painting'):
                component = values.get(lane, {})
                components.append({
                    'lane': lane,
                    'label': STAGE_REPLENISHMENT_LANE_LABELS.get(lane, lane),
                    'wip_qty': component.get('wip_qty', 0.0),
                    'incoming_qty': component.get('incoming_qty', 0.0),
                    'draft_qty': component.get('draft_qty', 0.0),
                    'reserved_qty': component.get('reserved_wip_qty', 0.0),
                    'coverage_qty': component.get('coverage_qty', 0.0),
                    'qty_to_produce': component.get('qty_to_produce', 0.0),
                    'owned_by_final_minmax': True,
                })
            status = values.get('status', 'disabled')
            rows.append({
                'id': rule.id,
                'product': rule.product_id.display_name,
                'model': rule.furniture_model_id.display_name,
                'recipe': rule.bom_id.sudo().display_name,
                'unit': rule.uom_id.display_name,
                'minimum': rule.min_qty,
                'maximum': rule.max_qty,
                'finished_qty': values.get('finished_qty', 0.0),
                'body_wip_qty': body.get('wip_qty', 0.0),
                'body_incoming_qty': body.get('incoming_qty', 0.0),
                'body_draft_qty': body.get('draft_qty', 0.0),
                'body_coverage_qty': body.get('coverage_qty', 0.0),
                'body_qty_to_produce': body.get('qty_to_produce', 0.0),
                'cover_wip_qty': cover.get('wip_qty', 0.0),
                'cover_incoming_qty': cover.get('incoming_qty', 0.0),
                'cover_draft_qty': cover.get('draft_qty', 0.0),
                'cover_coverage_qty': cover.get('coverage_qty', 0.0),
                'cover_qty_to_produce': cover.get('qty_to_produce', 0.0),
                'components': components,
                'status': status,
                'status_label': FINAL_RULE_STATUS_LABELS.get(status, status),
                'can_run': bool(values.get('triggered')),
                'last_body_production_id': rule.last_body_production_id.id or False,
                'last_body_production': rule.last_body_production_id.display_name or False,
                'last_cover_production_id': rule.last_cover_production_id.id or False,
                'last_cover_production': rule.last_cover_production_id.display_name or False,
            })
        rows.sort(key=lambda row: (
            row['product'] or '', row['model'] or '', row['id'],
        ))
        return {
            'rows': rows,
            'summary': {
                'total_rules': len(rows),
                'configured': sum(
                    1 for row in rows
                    if row['minimum'] > 0 and row['maximum'] > 0
                ),
                'need_production': sum(
                    1 for row in rows if row['status'] == 'to_produce'
                ),
                'finished_qty': sum(row['finished_qty'] for row in rows),
                'draft_orders': self.env['furniture.mrp.production'].search_count([
                    ('company_id', '=', self.env.company.id),
                    ('state', '=', 'draft'),
                    ('final_replenishment_rule_ids', '!=', False),
                ]),
            },
        }

    @api.model
    def final_replenishment_dashboard_data(self):
        self._final_replenishment_check_manager()
        company = self.env.company
        self.with_company(company)._final_replenishment_sync_company(company)
        return self._final_replenishment_dashboard_payload()

    @api.model
    def final_replenishment_sync_rules(self):
        return self.final_replenishment_dashboard_data()

    @api.model
    def final_replenishment_update_limits(self, rule_id, minimum, maximum):
        self._final_replenishment_check_manager()
        try:
            rule_id = int(rule_id)
            minimum = float(minimum)
            maximum = float(maximum)
        except (TypeError, ValueError) as error:
            raise ValidationError(_(
                'The rule, minimum and maximum must contain valid numbers.'
            )) from error
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            raise ValidationError(_('Minimum and maximum must be finite numbers.'))
        rule = self.search([
            ('id', '=', rule_id),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not rule:
            raise UserError(_('The finished-goods rule was not found.'))
        rule.write({'min_qty': minimum, 'max_qty': maximum})
        return self._final_replenishment_dashboard_payload()

    @api.model
    def final_replenishment_run_now(self, rule_ids=None):
        self._final_replenishment_check_manager()
        requested_ids = None
        if rule_ids is not None:
            try:
                requested_ids = list(dict.fromkeys(
                    int(rule_id) for rule_id in rule_ids
                ))
            except (TypeError, ValueError) as error:
                raise ValidationError(_('Invalid finished-goods selection.')) from error
            selected = self.search([
                ('id', 'in', requested_ids),
                ('company_id', '=', self.env.company.id),
            ])
            if set(selected.ids) != set(requested_ids):
                raise AccessError(_('One or more selected rules are unavailable.'))
        productions = self._final_replenishment_run_company(
            self.env.company,
            restricted_rule_ids=requested_ids,
            raise_on_error=True,
        )
        action = self.env[
            'furniture.mrp.stage.replenishment.rule'
        ]._stage_replenishment_production_action(productions)
        return {
            'created_ids': productions.ids,
            'created_count': len(productions),
            'action': action,
            'data': self._final_replenishment_dashboard_payload(),
        }

    @api.model
    def final_replenishment_open_orders(self, rule_id=False):
        self._final_replenishment_check_manager()
        domain = [
            ('company_id', '=', self.env.company.id),
            ('final_replenishment_rule_ids', '!=', False),
        ]
        if rule_id:
            try:
                rule_id = int(rule_id)
            except (TypeError, ValueError) as error:
                raise ValidationError(_('Invalid finished-goods rule.')) from error
            rule = self.search([
                ('id', '=', rule_id),
                ('company_id', '=', self.env.company.id),
            ], limit=1)
            if not rule:
                raise AccessError(_('The finished-goods rule is unavailable.'))
            domain.append(('final_replenishment_rule_ids', 'in', [rule.id]))
        return {
            'type': 'ir.actions.act_window',
            'name': _('أوامر تعويض المنتج النهائي'),
            'res_model': 'furniture.mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
            'context': {'search_default_group_by_state': 1},
            'target': 'current',
        }
