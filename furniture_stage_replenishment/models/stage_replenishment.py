# -*- coding: utf-8 -*-

import logging
import math
from collections import defaultdict
from datetime import timedelta

from psycopg2.errors import LockNotAvailable

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_round

from odoo.addons.furniture_mrp.models.mrp_production_order import (
    FURNITURE_STAGE_FIELD_MAP,
    FURNITURE_STAGE_SELECTION,
)


_logger = logging.getLogger(__name__)

STAGE_CODES = tuple(code for code, _label in FURNITURE_STAGE_SELECTION)
STAGE_LABELS = dict(FURNITURE_STAGE_SELECTION)
# Bases and finishing remain two real manufacturing departments, but they are
# one replenishment buffer controlled by ``finishing``.  Upholstery is driven
# by the production order, not by its own Min/Max rule.
STAGE_REPLENISHMENT_RETIRED_RULE_CODES = frozenset({
    'bases',
    'upholstery',
})
STAGE_REPLENISHMENT_DISPLAY_CODES = (
    'carpentry',
    'painting',
    'finishing',
    'tailoring',
)
STAGE_REPLENISHMENT_STAGE_LABELS = {
    **STAGE_LABELS,
    'carpentry': 'النجارة',
    'finishing': 'القواعد والتجهيز',
}
STAGE_REPLENISHMENT_LANE_SELECTION = [
    ('legacy', 'المسار القديم'),
    ('frame', 'نجارة'),
    ('painting', 'تصنيع الدهانات'),
    ('finish', 'القواعد والتجهيز'),
    ('tailoring', 'التفصيل والخياطة'),
    ('upholstery', 'الكسوة'),
    ('body', 'نجارة (قديم)'),
    ('cover', 'الكسوة والتفصيل (قديم)'),
]
STAGE_REPLENISHMENT_LANE_LABELS = dict(
    STAGE_REPLENISHMENT_LANE_SELECTION
)
# Min/Max keeps a separate buffer for every managed department, while one
# replenishment order may still run through more than one department.  Keep
# these routes local to the bridge module so historic ``furniture_mrp`` orders
# remain readable even while its legacy eight-stage route is preserved.
STAGE_REPLENISHMENT_LANE_ROUTES = {
    'frame': ('priming', 'carpentry'),
    'painting': ('painting',),
    'finish': ('bases', 'finishing'),
    'tailoring': ('tailoring',),
    # Kept readable for historical outputs/orders.  No new upholstery Min/Max
    # rule is synchronized or generated.
    'upholstery': ('upholstery',),
}
STAGE_REPLENISHMENT_CONTROLLER_LANES = {
    'carpentry': 'frame',
    'painting': 'painting',
    'finishing': 'finish',
    'tailoring': 'tailoring',
}
# Compatibility mapping retained for historical final-assembly records.
STAGE_REPLENISHMENT_LANE_CONTROLLERS = {
    'frame': 'carpentry',
    'finish': 'finishing',
    'tailoring': 'tailoring',
    'upholstery': 'upholstery',
}
STAGE_REPLENISHMENT_MINMAX_LANE_CONTROLLERS = {
    'frame': 'carpentry',
    'painting': 'painting',
    'finish': 'finishing',
    'tailoring': 'tailoring',
}
ACTIVE_PRODUCTION_STATES = (
    'confirmed', 'in_production', 'priming', 'painting', 'carpentry',
    'bases', 'finishing', 'tailoring', 'upholstery', 'packaging',
)
RULE_STATUS_SELECTION = [
    ('disabled', 'غير مفعّل'),
    ('to_produce', 'يحتاج أمر تصنيع'),
    ('draft', 'مغطى بمسودة'),
    ('incoming', 'قادم للمرحلة'),
    ('working', 'داخل المرحلة'),
    ('ok', 'المستوى آمن'),
]

AUTO_CONFIRM_ENABLED_PARAM = (
    'furniture_stage_replenishment.auto_confirm_enabled'
)
AUTO_CONFIRM_UNTIL_PARAM = (
    'furniture_stage_replenishment.auto_confirm_until'
)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    furniture_stage_replenishment_separate_order = fields.Boolean(
        string='أمر Min/Max منفصل',
        default=False,
        copy=True,
        help=(
            'حقل تاريخي للتوافق مع البيانات القديمة. أوامر Min/Max الحالية '
            'تجمع كل أصناف الموديل داخل أمر إنتاج واحد لكل مسار.'
        ),
    )


class FurnitureMrpProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    _STAGE_REPLENISHMENT_INTERNAL_FIELDS = {
        'stage_replenishment_generated',
        'stage_replenishment_rule_ids',
        'stage_replenishment_created_at',
    }

    stage_replenishment_generated = fields.Boolean(
        string='Automatic Stage Replenishment',
        readonly=True,
        copy=False,
        index=True,
    )
    stage_replenishment_rule_ids = fields.Many2many(
        'furniture.mrp.stage.replenishment.rule',
        'furn_stage_replenishment_production_rel',
        'production_id',
        'rule_id',
        string='Stage Replenishment Rules',
        readonly=True,
        copy=False,
    )
    stage_replenishment_created_at = fields.Datetime(
        string='Stage Replenishment Generated At',
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any(
            self._STAGE_REPLENISHMENT_INTERNAL_FIELDS & set(vals)
            for vals in vals_list
        ):
            raise AccessError(_(
                'Automatic stage replenishment markers are managed by the '
                'generator only.'
            ))
        return super().create(vals_list)

    @api.model
    def _stage_replenishment_internal_create(self, vals_list):
        """Create a generated draft through a private, non-RPC entry point."""
        return super(
            FurnitureMrpProduction,
            self.sudo(),
        ).create(vals_list)

    def write(self, vals):
        if self._STAGE_REPLENISHMENT_INTERNAL_FIELDS & set(vals):
            raise AccessError(_(
                'Automatic stage replenishment markers are managed by the '
                'generator only.'
            ))
        return super().write(vals)

    def _stage_replenishment_internal_write(self, vals):
        """Update generated-draft metadata without a forgeable context key."""
        return super(
            FurnitureMrpProduction,
            self.sudo(),
        ).write(vals)


class FurnitureMrpStageReplenishmentRule(models.Model):
    _name = 'furniture.mrp.stage.replenishment.rule'
    _description = 'Furniture MRP Stage Min/Max Replenishment Rule'
    _order = 'stage_code, product_id, furniture_model_id, id'
    _check_company_auto = True

    @api.model
    def _stage_replenishment_auto_confirm_is_active(self):
        """Return whether this database is inside its temporary window."""
        parameters = self.env['ir.config_parameter'].sudo()
        if parameters.get_param(AUTO_CONFIRM_ENABLED_PARAM) != 'True':
            return False
        auto_confirm_until = fields.Datetime.to_datetime(
            parameters.get_param(AUTO_CONFIRM_UNTIL_PARAM)
        )
        return bool(
            auto_confirm_until
            and fields.Datetime.now() < auto_confirm_until
        )

    @api.model
    def _stage_replenishment_auto_confirm_orders(self, productions):
        """Confirm generated Min/Max drafts without touching manual orders."""
        if not self._stage_replenishment_auto_confirm_is_active():
            return productions
        drafts = productions.filtered(lambda production: (
            production.stage_replenishment_generated
            and production.state == 'draft'
        ))
        for production in drafts.sorted('id'):
            try:
                with self.env.cr.savepoint():
                    production.sudo().action_confirm()
            except Exception:
                _logger.exception(
                    'Unable to auto-confirm Min/Max production %s',
                    production.display_name,
                )
        return productions

    active = fields.Boolean(default=True, index=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete='cascade',
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='Stage',
        required=True,
        readonly=True,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='Model',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    bom_id = fields.Many2one(
        'mrp.bom',
        string='Recipe',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
        check_company=True,
    )
    uom_id = fields.Many2one(
        'uom.uom',
        related='product_id.uom_id',
        string='Unit',
        readonly=True,
    )
    min_qty = fields.Float(
        string='Minimum',
        default=0.0,
        required=True,
        digits='Product Unit of Measure',
    )
    max_qty = fields.Float(
        string='Maximum',
        default=0.0,
        required=True,
        digits='Product Unit of Measure',
    )
    last_generated_at = fields.Datetime(readonly=True, copy=False)
    last_production_id = fields.Many2one(
        'furniture.mrp.production',
        string='Last Generated Production',
        readonly=True,
        copy=False,
        ondelete='set null',
    )

    current_qty = fields.Float(
        string='In Stage',
        compute='_compute_stage_replenishment_metrics',
        digits='Product Unit of Measure',
    )
    incoming_qty = fields.Float(
        string='Incoming',
        compute='_compute_stage_replenishment_metrics',
        digits='Product Unit of Measure',
    )
    draft_qty = fields.Float(
        string='Draft',
        compute='_compute_stage_replenishment_metrics',
        digits='Product Unit of Measure',
    )
    reserved_qty = fields.Float(
        string='Reserved For Final Assembly',
        compute='_compute_stage_replenishment_metrics',
        digits='Product Unit of Measure',
    )
    forecast_qty = fields.Float(
        string='Covered Quantity',
        compute='_compute_stage_replenishment_metrics',
        digits='Product Unit of Measure',
    )
    qty_to_produce = fields.Float(
        string='Quantity To Produce',
        compute='_compute_stage_replenishment_metrics',
        digits='Product Unit of Measure',
    )
    replenishment_status = fields.Selection(
        RULE_STATUS_SELECTION,
        string='Status',
        compute='_compute_stage_replenishment_metrics',
    )

    _sql_constraints = [
        (
            'stage_replenishment_identity_unique',
            'unique(company_id, stage_code, product_id, furniture_model_id)',
            'A stage replenishment rule already exists for this product and model.',
        ),
        (
            'stage_replenishment_nonnegative_limits',
            'check(min_qty >= 0 AND max_qty >= 0)',
            'Minimum and maximum quantities cannot be negative.',
        ),
        (
            'stage_replenishment_limit_order',
            'check(min_qty <= max_qty)',
            'Minimum quantity cannot exceed maximum quantity.',
        ),
    ]

    @api.model
    def _stage_replenishment_check_manager(self):
        user = self.env.user
        if not (
            user._is_admin()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
        ):
            raise AccessError(_(
                'Stage replenishment configuration is available to factory '
                'managers only.'
            ))

    @api.model_create_multi
    def create(self, vals_list):
        self._stage_replenishment_check_manager()
        raise AccessError(_(
            'Stage replenishment identities are generated from live '
            'recipes. Use Refresh Recipes instead of creating a row.'
        ))

    @api.model
    def _stage_replenishment_internal_create(self, vals_list):
        """Create synchronized identities through a private ORM path."""
        return super(
            FurnitureMrpStageReplenishmentRule,
            self.sudo(),
        ).create(vals_list)

    def write(self, vals):
        internal_fields = {
            'company_id', 'stage_code', 'product_id',
            'furniture_model_id', 'bom_id', 'active',
            'last_generated_at', 'last_production_id',
        }
        if internal_fields & set(vals):
            raise AccessError(_(
                'Product, model, recipe, stage and generation audit fields '
                'are managed automatically from live recipes.'
            ))
        if {'min_qty', 'max_qty'} & set(vals):
            self._stage_replenishment_check_manager()
        return super().write(vals)

    def _stage_replenishment_internal_write(self, vals):
        """Update synchronized identities/audit through a private ORM path."""
        return super(
            FurnitureMrpStageReplenishmentRule,
            self.sudo(),
        ).write(vals)

    @api.constrains('min_qty', 'max_qty')
    def _check_stage_replenishment_limits(self):
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
    def _stage_replenishment_lock_company(self, company):
        """Serialize sync and generation for one company in every entry point."""
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)',
            [4608850, company.id],
        )

    @api.model
    def _stage_replenishment_canonical_recipes(self, company):
        Bom = self.env['mrp.bom'].sudo().with_company(company)
        candidates = Bom.search([
            ('active', '=', True),
            ('type', '=', 'normal'),
            ('furniture_is_model_recipe', '=', True),
            ('furniture_recipe_ready', '=', True),
            ('furniture_product_id', '!=', False),
            ('furniture_product_id.active', '=', True),
            ('furniture_model_id', '!=', False),
            ('furniture_model_id.active', '=', True),
            ('company_id', 'in', [False, company.id]),
        ])
        identities = {
            (bom.furniture_product_id.id, bom.furniture_model_id.id)
            for bom in candidates
        }
        recipes = Bom
        for product_id, model_id in sorted(identities):
            product = Bom.env['product.product'].browse(product_id)
            model = Bom.env['furniture.product.model'].browse(model_id)
            recipe = Bom._find_furniture_production_recipe(
                product,
                model=model,
                company=company,
            )
            if (
                recipe
                and recipe.active
                and recipe.type == 'normal'
                and recipe.furniture_recipe_ready
            ):
                recipes |= recipe
        return recipes

    @api.model
    def _stage_replenishment_lane_for_stage(self, stage_code):
        return STAGE_REPLENISHMENT_CONTROLLER_LANES.get(
            stage_code,
            'legacy',
        )

    @api.model
    def _stage_replenishment_rule_lane(self, rule):
        return self._stage_replenishment_lane_for_stage(rule.stage_code)

    @api.model
    def _stage_replenishment_lane_rule_stages(self, lane):
        """Return the single canonical Min/Max controller for ``lane``."""
        controller = STAGE_REPLENISHMENT_MINMAX_LANE_CONTROLLERS.get(lane)
        return (controller,) if controller else ()

    @api.model
    def _stage_replenishment_recipe_rule_stages(self, bom):
        """Return controller stages, with a safe fallback for old recipes.

        A live recipe that has component-lane controllers is managed only by
        those department buffers.  Incomplete historic/test recipes which
        have no controller keep their old per-stage rules; this prevents an
        upgrade from deleting their configuration while ensuring real modern
        recipes never create one order per intermediate department.
        """
        active_stages = tuple(
            code for code in bom._get_active_stage_codes()
            if code in STAGE_CODES
        )
        controller_stages = tuple(
            stage_code
            for stage_code in STAGE_REPLENISHMENT_CONTROLLER_LANES
            if stage_code in active_stages
        )
        legacy_stages = tuple(
            stage_code
            for stage_code in active_stages
            if stage_code not in STAGE_REPLENISHMENT_RETIRED_RULE_CODES
        )
        return controller_stages or legacy_stages

    @api.model
    def _stage_replenishment_preserve_retired_limits(self, existing_by_key):
        """Move a bases-only policy to the shared finishing controller.

        If both old rows were configured, finishing was already the live
        output controller and therefore remains authoritative.  The archived
        bases row keeps its own limits and audit trail in either case.
        """
        for (stage_code, product_id, model_id), bases_rule in tuple(
            existing_by_key.items()
        ):
            if stage_code != 'bases':
                continue
            finishing_rule = existing_by_key.get((
                'finishing',
                product_id,
                model_id,
            ))
            if not finishing_rule:
                continue
            bases_has_limits = bool(
                bases_rule.active
                and (bases_rule.min_qty > 0 or bases_rule.max_qty > 0)
            )
            finishing_has_limits = bool(
                finishing_rule.min_qty > 0 or finishing_rule.max_qty > 0
            )
            if bases_has_limits and not finishing_has_limits:
                finishing_rule._stage_replenishment_internal_write({
                    'min_qty': bases_rule.min_qty,
                    'max_qty': bases_rule.max_qty,
                })

    @api.model
    def _stage_replenishment_lane_route(self, lane, bom):
        active_stages = set(bom._get_active_stage_codes())
        if lane in STAGE_REPLENISHMENT_LANE_ROUTES:
            return tuple(
                stage_code
                for stage_code in STAGE_REPLENISHMENT_LANE_ROUTES[lane]
                if stage_code in active_stages
            )
        return tuple(
            stage_code for stage_code in STAGE_CODES
            if stage_code in active_stages
        )

    @api.model
    def _stage_replenishment_sync_company(self, company=None, lock=True):
        company = company or self.env.company
        if lock:
            self._stage_replenishment_lock_company(company)
        Rule = self.sudo().with_company(company).with_context(
            active_test=False,
        )
        existing = Rule.search([('company_id', '=', company.id)])
        existing_by_key = {
            (rule.stage_code, rule.product_id.id, rule.furniture_model_id.id): rule
            for rule in existing
        }
        desired_keys = set()
        for bom in self._stage_replenishment_canonical_recipes(company):
            product = bom.furniture_product_id
            model = bom.furniture_model_id
            active_stages = self._stage_replenishment_recipe_rule_stages(bom)
            for stage_code in active_stages:
                key = (stage_code, product.id, model.id)
                desired_keys.add(key)
                vals = {
                    'company_id': company.id,
                    'stage_code': stage_code,
                    'product_id': product.id,
                    'furniture_model_id': model.id,
                    'bom_id': bom.id,
                    'active': True,
                }
                rule = existing_by_key.get(key)
                if rule:
                    changes = {
                        field_name: value
                        for field_name, value in vals.items()
                        if (
                            rule[field_name].id if rule._fields[field_name].type == 'many2one'
                            else rule[field_name]
                        ) != value
                    }
                    if changes:
                        rule._stage_replenishment_internal_write(changes)
                else:
                    existing_by_key[key] = (
                        Rule._stage_replenishment_internal_create([vals])
                    )
        Rule._stage_replenishment_preserve_retired_limits(existing_by_key)
        obsolete = existing.filtered(lambda rule: (
            rule.active
            and (rule.stage_code, rule.product_id.id, rule.furniture_model_id.id)
            not in desired_keys
        ))
        if obsolete:
            obsolete._stage_replenishment_internal_write({'active': False})
        return Rule.with_context(active_test=True).search([
            ('company_id', '=', company.id),
        ])

    @api.model
    def _stage_replenishment_line_key(self, line, stage_code):
        product = (
            line.product_id.furniture_dimension_source_product_id
            or line.product_id
        )
        model = line.furniture_order_model_id or line.bom_id.furniture_model_id
        company = line.production_id.company_id
        if not product or not model or not company:
            return False
        return (company.id, stage_code, product.id, model.id)

    @api.model
    def _stage_replenishment_add_lines(
        self,
        bucket,
        lines,
        stage_code,
        quantity_by_line=None,
    ):
        for line in lines:
            key = self._stage_replenishment_line_key(line, stage_code)
            if key:
                quantity = (
                    quantity_by_line.get(line.id, 0.0)
                    if quantity_by_line is not None
                    else line.product_qty
                )
                bucket[key] += max(quantity or 0.0, 0.0)

    @api.model
    def _stage_replenishment_header_key(self, production, stage_code):
        """Identity fallback for legacy/MPS orders that have no product lines."""
        product = (
            production.product_id.furniture_dimension_source_product_id
            or production.product_id
        )
        model = (
            production.furniture_order_model_id
            or production.bom_id.furniture_model_id
        )
        if not product or not model or not production.company_id:
            return False
        return (
            production.company_id.id,
            stage_code,
            product.id,
            model.id,
        )

    @api.model
    def _stage_replenishment_add_header(self, bucket, production, stage_code):
        key = self._stage_replenishment_header_key(production, stage_code)
        if key:
            bucket[key] += max(production.product_qty or 0.0, 0.0)

    @api.model
    def _stage_replenishment_lane_output_maps(self, companies):
        """Return free buffers and already received quantities per source.

        The output ledger carries business reservations while ``stock.quant``
        carries physical stock reservations.  Capping the ledger's logical
        free quantity by the physical free quantity makes both mechanisms
        safe without subtracting the same reservation twice.
        """
        current = defaultdict(float)
        reserved = defaultdict(float)
        received_by_line = defaultdict(float)
        received_by_production = defaultdict(float)
        logical_groups = defaultdict(float)
        Output = self.env['furniture.mrp.lane.output'].sudo()
        Quant = self.env['stock.quant'].sudo()

        for company in companies:
            outputs = Output.with_company(company).search([
                ('company_id', '=', company.id),
                ('lane', 'in', tuple(STAGE_REPLENISHMENT_LANE_ROUTES)),
                ('origin_receipt_move_id.state', '=', 'done'),
            ])
            for output in outputs:
                product = self._stage_replenishment_canonical_product(
                    output.final_product_id
                )
                model = output.furniture_model_id
                controller = STAGE_REPLENISHMENT_MINMAX_LANE_CONTROLLERS.get(
                    output.lane
                )
                if not product or not model or not controller:
                    continue
                key = (company.id, controller, product.id, model.id)
                total_qty = max(output.qty_ready or 0.0, 0.0)
                assembled_qty = min(
                    max(output.assembled_qty or 0.0, 0.0),
                    total_qty,
                )
                reserved_qty = min(
                    max(output.reserved_qty or 0.0, 0.0),
                    max(total_qty - assembled_qty, 0.0),
                )
                logical_free = max(output.available_qty or 0.0, 0.0)
                reserved[key] += reserved_qty
                if output.production_line_id:
                    received_by_line[output.production_line_id.id] += total_qty
                if output.production_id:
                    received_by_production[output.production_id.id] += total_qty

                wip_product = output.wip_product_id
                location = (
                    output.source_location_id
                    or output.origin_receipt_move_id.location_dest_id
                )
                if not wip_product or not location:
                    continue
                logical_groups[(
                    key,
                    wip_product.id,
                    location.id,
                )] += logical_free

            for (key, wip_product_id, location_id), logical_free in (
                logical_groups.items()
            ):
                if key[0] != company.id:
                    continue
                physical_free = Quant.with_company(
                    company
                )._get_available_quantity(
                    self.env['product.product'].browse(wip_product_id),
                    self.env['stock.location'].browse(location_id),
                    lot_id=self.env['stock.lot'],
                    package_id=self.env['stock.quant.package'],
                    owner_id=self.env['res.partner'],
                    strict=True,
                )
                current[key] += min(
                    max(logical_free, 0.0),
                    max(physical_free, 0.0),
                )

        return {
            'current': current,
            'reserved': reserved,
            'received_by_line': received_by_line,
            'received_by_production': received_by_production,
        }

    @api.model
    def _stage_replenishment_add_lane_production(
        self,
        bucket,
        production,
        stage_code,
        received_by_line=None,
        received_by_production=None,
    ):
        active_lines = production.production_line_ids.filtered(lambda line: (
            line.active
            and line.product_id
            and (line.product_qty or 0.0) > 0.0
        ))
        if active_lines:
            quantity_by_line = {
                line.id: max(
                    (line.product_qty or 0.0)
                    - (received_by_line or {}).get(line.id, 0.0),
                    0.0,
                )
                for line in active_lines
            }
            self._stage_replenishment_add_lines(
                bucket,
                active_lines,
                stage_code,
                quantity_by_line=quantity_by_line,
            )
            return
        key = self._stage_replenishment_header_key(production, stage_code)
        if key:
            bucket[key] += max(
                (production.product_qty or 0.0)
                - (received_by_production or {}).get(production.id, 0.0),
                0.0,
            )

    @api.model
    def _stage_replenishment_workload_maps(self, companies):
        current = defaultdict(float)
        incoming = defaultdict(float)
        draft = defaultdict(float)
        reserved = defaultdict(float)
        open_lane_supply = defaultdict(float)
        Production = self.env['furniture.mrp.production'].sudo()

        lane_outputs = self._stage_replenishment_lane_output_maps(companies)
        for key, quantity in lane_outputs['current'].items():
            current[key] += quantity
        for key, quantity in lane_outputs['reserved'].items():
            reserved[key] += quantity

        for company in companies:
            active_productions = Production.with_company(company).search([
                ('company_id', '=', company.id),
                ('state', 'in', ACTIVE_PRODUCTION_STATES),
            ])
            lane_productions = active_productions.filtered(
                lambda production: (
                    production.production_lane
                    in STAGE_REPLENISHMENT_LANE_ROUTES
                )
            )
            for production in lane_productions:
                controller = STAGE_REPLENISHMENT_MINMAX_LANE_CONTROLLERS.get(
                    production.production_lane
                )
                if controller:
                    self._stage_replenishment_add_lane_production(
                        open_lane_supply,
                        production,
                        controller,
                        received_by_line=lane_outputs['received_by_line'],
                        received_by_production=(
                            lane_outputs['received_by_production']
                        ),
                    )
                for stage_code in self._stage_replenishment_lane_rule_stages(
                    production.production_lane
                ):
                    _use_field, order_field, _state_field = (
                        FURNITURE_STAGE_FIELD_MAP[stage_code]
                    )
                    stage_order = production[order_field]
                    active_lines = production.production_line_ids.filtered(
                        lambda line: (
                            line.active
                            and line.product_id
                            and (line.product_qty or 0.0) > 0.0
                            and stage_code in line._selected_stage_codes()
                        )
                    )
                    if not active_lines:
                        if stage_code not in production._required_stage_codes():
                            continue
                        if stage_order and stage_order.state == 'done':
                            continue
                        target = (
                            current
                            if stage_order
                            and stage_order.state in (
                                'in_progress', 'quality_check'
                            )
                            else incoming
                        )
                        self._stage_replenishment_add_header(
                            target,
                            production,
                            stage_code,
                        )
                        continue
                    if not stage_order:
                        self._stage_replenishment_add_lane_production(
                            incoming,
                            production,
                            stage_code,
                            received_by_line=(
                                lane_outputs['received_by_line']
                            ),
                            received_by_production=(
                                lane_outputs['received_by_production']
                            ),
                        )
                        continue
                    tracking_map = Production._stage_dashboard_tracking_map(
                        stage_order
                    )
                    tracking_lines = tracking_map.get(
                        (stage_order._name, stage_order.id),
                        {},
                    )
                    buckets = Production._stage_dashboard_line_buckets(
                        production,
                        stage_code,
                        stage_order,
                        tracking_lines=tracking_lines,
                    )
                    self._stage_replenishment_add_lines(
                        current,
                        buckets['working_lines'],
                        stage_code,
                    )
                    self._stage_replenishment_add_lines(
                        incoming,
                        buckets['not_started_lines'],
                        stage_code,
                    )

            legacy_productions = active_productions - lane_productions
            for stage_code in STAGE_CODES:
                _use_field, order_field, _state_field = (
                    FURNITURE_STAGE_FIELD_MAP[stage_code]
                )
                stage_orders = legacy_productions.mapped(order_field)
                tracking_map = Production._stage_dashboard_tracking_map(
                    stage_orders
                )
                for production in legacy_productions:
                    stage_order = production[order_field]
                    active_lines = production.production_line_ids.filtered(
                        lambda line: (
                            line.active
                            and line.product_id
                            and (line.product_qty or 0.0) > 0.0
                        )
                    )
                    if not active_lines:
                        if stage_code not in production._required_stage_codes():
                            continue
                        if stage_order and stage_order.state == 'done':
                            continue
                        target = (
                            current
                            if stage_order
                            and stage_order.state in ('in_progress', 'quality_check')
                            else incoming
                        )
                        self._stage_replenishment_add_header(
                            target,
                            production,
                            stage_code,
                        )
                        continue
                    tracking_lines = (
                        tracking_map.get((stage_order._name, stage_order.id), {})
                        if stage_order else {}
                    )
                    buckets = Production._stage_dashboard_line_buckets(
                        production,
                        stage_code,
                        stage_order,
                        tracking_lines=tracking_lines,
                    )
                    self._stage_replenishment_add_lines(
                        current,
                        buckets['working_lines'],
                        stage_code,
                    )
                    self._stage_replenishment_add_lines(
                        incoming,
                        buckets['not_started_lines'],
                        stage_code,
                    )

            draft_productions = Production.with_company(company).search([
                ('company_id', '=', company.id),
                ('state', '=', 'draft'),
            ])
            for production in draft_productions:
                if production.production_lane in STAGE_REPLENISHMENT_LANE_ROUTES:
                    for stage_code in (
                        self._stage_replenishment_lane_rule_stages(
                            production.production_lane
                        )
                    ):
                        self._stage_replenishment_add_lane_production(
                            draft,
                            production,
                            stage_code,
                        )
                    continue
                active_lines = production.production_line_ids.filtered(
                    lambda line: (
                        line.active
                        and line.product_id
                        and (line.product_qty or 0.0) > 0.0
                    )
                )
                if active_lines:
                    for line in active_lines:
                        for stage_code in line._selected_stage_codes():
                            if stage_code in STAGE_CODES:
                                self._stage_replenishment_add_lines(
                                    draft,
                                    line,
                                    stage_code,
                                )
                    continue
                for stage_code in production._required_stage_codes():
                    if stage_code in STAGE_CODES:
                        self._stage_replenishment_add_header(
                            draft,
                            production,
                            stage_code,
                        )
        return {
            'current': current,
            'incoming': incoming,
            'draft': draft,
            'reserved': reserved,
            'open_lane_supply': open_lane_supply,
        }

    @api.model
    def _stage_replenishment_metric_map(self, rules):
        rules = rules.exists()
        if not rules:
            return {}
        workload = self._stage_replenishment_workload_maps(
            rules.mapped('company_id')
        )
        result = {}
        for rule in rules:
            key = (
                rule.company_id.id,
                rule.stage_code,
                rule.product_id.id,
                rule.furniture_model_id.id,
            )
            current_qty = round(workload['current'].get(key, 0.0), 3)
            incoming_qty = round(workload['incoming'].get(key, 0.0), 3)
            draft_qty = round(workload['draft'].get(key, 0.0), 3)
            reserved_qty = round(workload['reserved'].get(key, 0.0), 3)
            lane = self._stage_replenishment_rule_lane(rule)
            upstream_qty = 0.0
            if lane == 'finish':
                frame_key = (
                    rule.company_id.id,
                    STAGE_REPLENISHMENT_MINMAX_LANE_CONTROLLERS['frame'],
                    rule.product_id.id,
                    rule.furniture_model_id.id,
                )
                upstream_qty = round(
                    # A frame stays part of the finish pipeline when it changes
                    # from an open production into physically ready FIFO stock.
                    # Reserved ready stock remains a physical frame until its
                    # FIFO handoff is consumed.  Keeping it in upstream supply
                    # also prevents the same finish draft from growing merely
                    # because its inputs changed from free to reserved.
                    workload['open_lane_supply'].get(frame_key, 0.0)
                    + workload['current'].get(frame_key, 0.0)
                    + workload['reserved'].get(frame_key, 0.0),
                    3,
                )
            forecast_qty = round(
                current_qty + incoming_qty + draft_qty + upstream_qty,
                3,
            )
            configured = (
                float_compare(rule.min_qty, 0.0, precision_digits=3) >= 0
                and float_compare(rule.max_qty, 0.0, precision_digits=3) > 0
            )
            if lane in STAGE_REPLENISHMENT_LANE_ROUTES:
                # The free output buffer decides *when* to replenish.  Open
                # supply only decides *how much* is still needed to reach Max.
                needs_production = (
                    configured
                    and float_compare(
                        current_qty,
                        rule.min_qty,
                        precision_rounding=rule.uom_id.rounding or 0.001,
                    ) <= 0
                    and float_compare(
                        forecast_qty,
                        rule.max_qty,
                        precision_rounding=rule.uom_id.rounding or 0.001,
                    ) < 0
                )
            else:
                # Historic recipes without a lane controller retain their
                # established workload semantics.
                needs_production = (
                    configured
                    and float_compare(
                        forecast_qty,
                        rule.min_qty,
                        precision_rounding=rule.uom_id.rounding or 0.001,
                    ) < 0
                )
            qty_to_produce = (
                max(rule.max_qty - forecast_qty, 0.0)
                if needs_production else 0.0
            )
            if not configured:
                status = 'disabled'
            elif needs_production:
                status = 'to_produce'
            elif draft_qty > 0:
                status = 'draft'
            elif incoming_qty > 0:
                status = 'incoming'
            elif upstream_qty > 0:
                status = 'incoming'
            elif current_qty > 0:
                status = 'working'
            else:
                status = 'ok'
            result[rule.id] = {
                'current_qty': current_qty,
                'incoming_qty': incoming_qty,
                'draft_qty': draft_qty,
                'reserved_qty': reserved_qty,
                'upstream_qty': upstream_qty,
                'forecast_qty': forecast_qty,
                'qty_to_produce': round(qty_to_produce, 3),
                'status': status,
            }
        return result

    @api.model
    def _stage_replenishment_lock_editable_workload(self, company, rules):
        """Freeze editable workload without waiting on an in-flight user edit."""
        rules = rules.exists()
        if not rules:
            return self.env['furniture.mrp.production']
        product_ids = rules.mapped('product_id').ids
        model_ids = rules.mapped('furniture_model_id').ids
        lanes = sorted({
            self._stage_replenishment_rule_lane(rule)
            for rule in rules
        })
        Production = self.env['furniture.mrp.production'].sudo().with_company(
            company
        )
        Line = self.env[
            'furniture.mrp.production.line'
        ].sudo().with_company(company).with_context(active_test=False)
        reusable_productions = Production.search([
            ('company_id', '=', company.id),
            ('state', '=', 'draft'),
            ('stage_replenishment_generated', '=', True),
            ('production_lane', 'in', lanes),
            ('furniture_order_model_id', 'in', model_ids),
        ], order='id')
        lines = Line.search([
            ('production_id.company_id', '=', company.id),
            ('production_id.state', 'in', ('draft', 'confirmed')),
            ('production_id.production_lane', 'in', lanes),
            '|',
            ('product_id', 'in', product_ids),
            ('product_id.furniture_dimension_source_product_id', 'in', product_ids),
            '|',
            ('furniture_order_model_id', 'in', model_ids),
            ('bom_id.furniture_model_id', 'in', model_ids),
        ], order='id')
        if reusable_productions:
            lines |= Line.search([
                ('production_id', 'in', reusable_productions.ids),
            ], order='id')
        header_productions = Production.search([
            ('company_id', '=', company.id),
            ('state', 'in', ('draft', 'confirmed')),
            ('production_lane', 'in', lanes),
            '|',
            ('product_id', 'in', product_ids),
            ('product_id.furniture_dimension_source_product_id', 'in', product_ids),
            '|',
            ('furniture_order_model_id', 'in', model_ids),
            ('bom_id.furniture_model_id', 'in', model_ids),
        ], order='id')
        productions = (
            lines.mapped('production_id')
            | header_productions
            | reusable_productions
        )
        if productions:
            lines |= Line.search([
                ('production_id', 'in', productions.ids),
            ], order='id')

        lane_names = [
            lane for lane in lanes
            if lane in STAGE_REPLENISHMENT_LANE_ROUTES
        ]
        Output = self.env['furniture.mrp.lane.output'].sudo().with_company(
            company
        )
        outputs = Output.search([
            ('company_id', '=', company.id),
            ('lane', 'in', lane_names),
            '|',
            ('final_product_id', 'in', product_ids),
            (
                'final_product_id.furniture_dimension_source_product_id',
                'in',
                product_ids,
            ),
            ('furniture_model_id', 'in', model_ids),
        ], order='id') if lane_names else Output
        quant_domain = [('id', '=', 0)]
        wip_products = outputs.mapped('wip_product_id')
        output_locations = (
            outputs.mapped('source_location_id')
            | outputs.mapped('origin_receipt_move_id.location_dest_id')
        )
        if wip_products and output_locations:
            quant_domain = [
                ('company_id', '=', company.id),
                ('product_id', 'in', wip_products.ids),
                ('location_id', 'in', output_locations.ids),
            ]
        quants = self.env['stock.quant'].sudo().with_company(company).search(
            quant_domain,
            order='id',
        )
        try:
            with self.env.cr.savepoint():
                # Production-line edits synchronize their parent after writing
                # the line. NOWAIT makes either lock order fail fast instead of
                # forming a line/parent deadlock with an iPad user edit.
                if lines:
                    self.env.cr.execute(
                        'SELECT id FROM furniture_mrp_production_line '
                        'WHERE id IN %s ORDER BY id FOR UPDATE NOWAIT',
                        [tuple(lines.ids)],
                    )
                if productions:
                    self.env.cr.execute(
                        'SELECT id FROM furniture_mrp_production '
                        'WHERE id IN %s ORDER BY id FOR UPDATE NOWAIT',
                        [tuple(productions.ids)],
                    )
                # The output row is the shared serialization point used by
                # final assembly reservations.  Do not additionally lock the
                # assembly row here: its workflow deliberately locks assembly
                # then output, and taking the inverse order would deadlock.
                for records in (outputs, quants):
                    if not records:
                        continue
                    self.env.cr.execute(
                        'SELECT id FROM %s WHERE id IN %%s '
                        'ORDER BY id FOR UPDATE NOWAIT' % records._table,
                        [tuple(records.ids)],
                    )
        except LockNotAvailable as exc:
            raise UserError(_(
                'A draft production order is being edited right now. No '
                'replenishment was generated; retry after the edit is saved.'
            )) from exc
        lines.invalidate_recordset()
        productions.invalidate_recordset()
        outputs.invalidate_recordset()
        quants.invalidate_recordset()
        return productions

    @api.depends('min_qty', 'max_qty', 'active')
    def _compute_stage_replenishment_metrics(self):
        metrics = self._stage_replenishment_metric_map(self)
        for rule in self:
            values = metrics.get(rule.id, {})
            rule.current_qty = values.get('current_qty', 0.0)
            rule.incoming_qty = values.get('incoming_qty', 0.0)
            rule.draft_qty = values.get('draft_qty', 0.0)
            rule.reserved_qty = values.get('reserved_qty', 0.0)
            rule.forecast_qty = values.get('forecast_qty', 0.0)
            rule.qty_to_produce = values.get('qty_to_produce', 0.0)
            rule.replenishment_status = values.get('status', 'disabled')

    @api.model
    def _stage_replenishment_manager_users(self, company):
        group = self.env.ref(
            'furniture_mrp.group_furniture_mrp_manager',
            raise_if_not_found=False,
        )
        users = group.users if group else self.env['res.users']
        users = users.filtered(lambda user: (
            user.active
            and user.partner_id
            and company in user.company_ids
        ))
        if not users:
            administrator = self.env.ref('base.user_admin', raise_if_not_found=False)
            users = (
                administrator
                if (
                    administrator
                    and administrator.active
                    and company in administrator.company_ids
                )
                else users
            )
        return users

    @api.model
    def _stage_replenishment_clear_legacy_separate_products(self):
        """Retire the old per-product split and normalize saved markers."""
        templates = self.env['product.template'].sudo().with_context(
            active_test=False,
        ).search([
            ('furniture_stage_replenishment_separate_order', '=', True),
        ])
        if templates:
            templates.write({
                'furniture_stage_replenishment_separate_order': False,
            })
        return templates

    @api.model
    def _stage_replenishment_mark_default_separate_products(self):
        """Compatibility alias for migrations created before grouping changed."""
        return self._stage_replenishment_clear_legacy_separate_products()

    @api.model
    def _stage_replenishment_canonical_product(self, product):
        return product.furniture_dimension_source_product_id or product

    @api.model
    def _stage_replenishment_order_group_key(
        self,
        product,
        model,
        lane='legacy',
    ):
        # Product identity deliberately does not participate in the key: every
        # product of one furniture model shares one draft per production lane.
        return (model.id, lane or 'legacy', 'model', 0)

    @api.model
    def _stage_replenishment_production_group_key(self, production):
        model = (
            production.furniture_order_model_id
            or production.bom_id.furniture_model_id
        )
        if not model:
            return False
        lane = (
            production.production_lane
            if production.production_lane in STAGE_REPLENISHMENT_LANE_ROUTES
            else 'legacy'
        )
        products = production.production_line_ids.filtered(lambda line: (
            line.active and line.product_id and (line.product_qty or 0.0) > 0.0
        )).mapped('product_id')
        if not products and production.product_id:
            products = production.product_id
        canonical_products = self.env['product.product']
        for product in products:
            canonical_products |= self._stage_replenishment_canonical_product(
                product
            )
        if not canonical_products:
            return False
        return (model.id, lane, 'model', 0)

    @api.model
    def _stage_replenishment_prepare_product_spec(
        self,
        trigger_rules,
        quantity,
        covered_rules=None,
    ):
        trigger_rules = trigger_rules.exists()
        covered_rules = (covered_rules or trigger_rules).exists()
        if not trigger_rules or not covered_rules or quantity <= 0:
            return False
        anchor = trigger_rules.sorted('id')[0]
        company = anchor.company_id
        product = anchor.product_id
        model = anchor.furniture_model_id
        lane = self._stage_replenishment_rule_lane(anchor)
        identity_mismatch = (trigger_rules | covered_rules).filtered(lambda rule: (
            rule.company_id != company
            or rule.product_id != product
            or rule.furniture_model_id != model
            or self._stage_replenishment_rule_lane(rule) != lane
        ))
        if identity_mismatch:
            raise UserError(_(
                'Stage replenishment rules must belong to one company, product '
                'and model before creating a production order.'
            ))
        Bom = self.env['mrp.bom'].sudo().with_company(company)
        bom = Bom._find_furniture_production_recipe(
            product,
            model=model,
            company=company,
        )
        if not bom:
            raise UserError(_(
                'No live production recipe exists for %(product)s / %(model)s.'
            ) % {
                'product': product.display_name,
                'model': model.display_name,
            })
        source_product = self._stage_replenishment_canonical_product(product)
        if (
            bom.furniture_product_id != source_product
            or bom.furniture_model_id != model
            or (bom.company_id and bom.company_id != company)
            or not bom.active
            or not bom.furniture_recipe_ready
        ):
            raise UserError(_(
                'The live production recipe no longer matches %(product)s / '
                '%(model)s for this company. Refresh recipes and try again.'
            ) % {
                'product': product.display_name,
                'model': model.display_name,
            })
        quantity = float_round(
            quantity,
            precision_rounding=product.uom_id.rounding or 0.001,
            rounding_method='UP',
        )
        stage_codes = self._stage_replenishment_lane_route(lane, bom)
        if not stage_codes:
            raise UserError(_(
                'The production recipe has no stages for the %(lane)s lane.'
            ) % {
                'lane': STAGE_REPLENISHMENT_LANE_LABELS.get(lane, lane),
            })
        expected_route = STAGE_REPLENISHMENT_LANE_ROUTES.get(lane)
        if expected_route and tuple(stage_codes) != tuple(expected_route):
            raise UserError(_(
                'The production recipe is missing part of the %(lane)s route. '
                'Required stages: %(stages)s.'
            ) % {
                'lane': STAGE_REPLENISHMENT_LANE_LABELS.get(lane, lane),
                'stages': '، '.join(
                    STAGE_REPLENISHMENT_STAGE_LABELS.get(code, code)
                    for code in expected_route
                ),
            })
        return {
            'company': company,
            'product': product,
            'model': model,
            'bom': bom,
            'lane': lane,
            'stage_codes': stage_codes,
            'quantity': quantity,
            'trigger_rules': trigger_rules,
            'covered_rules': covered_rules,
            'trigger_stage_names': '، '.join(
                STAGE_REPLENISHMENT_STAGE_LABELS.get(
                    rule.stage_code,
                    rule.stage_code,
                )
                for rule in trigger_rules.sorted('stage_code')
            ),
            'covered_stage_names': '، '.join(
                STAGE_REPLENISHMENT_STAGE_LABELS.get(
                    rule.stage_code,
                    rule.stage_code,
                )
                for rule in covered_rules.sorted('stage_code')
            ),
        }

    @api.model
    def _stage_replenishment_find_reusable_draft(
        self,
        company,
        group_key,
    ):
        Production = self.env['furniture.mrp.production'].sudo().with_company(
            company
        )
        candidates = Production.search([
            ('company_id', '=', company.id),
            ('state', '=', 'draft'),
            ('stage_replenishment_generated', '=', True),
            ('furniture_order_model_id', '=', group_key[0]),
            ('production_lane', '=', group_key[1]),
        ], order='id')
        if not candidates:
            return Production
        candidates.invalidate_recordset([
            'state', 'product_id', 'bom_id', 'furniture_order_model_id',
            'production_lane', 'production_line_ids',
        ])
        for production in candidates:
            if (
                production.state != 'draft'
                or self._stage_replenishment_production_group_key(production)
                != group_key
            ):
                continue
            lines = production.production_line_ids.filtered('active')
            if lines:
                lines.invalidate_recordset([
                    'active', 'product_id', 'product_qty', 'bom_id',
                    'furniture_order_model_id',
                ])
            production.invalidate_recordset(['state', 'production_line_ids'])
            if (
                production.state == 'draft'
                and self._stage_replenishment_production_group_key(production)
                == group_key
            ):
                return production
        return Production

    @api.model
    def _stage_replenishment_sync_header_from_first_line(self, production):
        first_line = production._get_sorted_production_lines()[:1]
        if not first_line:
            return
        header_vals = {
            'product_id': first_line.product_id.id,
            'bom_id': first_line.bom_id.id,
            'furniture_order_model_id': first_line.furniture_order_model_id.id,
            'product_qty': first_line.product_qty,
            'width_cm': first_line.width_cm,
            'depth_cm': first_line.depth_cm,
            'height_cm': first_line.height_cm,
        }
        changes = {
            field_name: value
            for field_name, value in header_vals.items()
            if (
                production[field_name].id
                if production._fields[field_name].type == 'many2one'
                else production[field_name]
            ) != value
        }
        if changes:
            production.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
                furniture_skip_order_common_sync=True,
            ).write(changes)

    @api.model
    def _stage_replenishment_stage_flag_values(self, stage_codes):
        selected = set(stage_codes or ())
        return {
            use_field: stage_code in selected
            for stage_code, (use_field, _order_field, _state_field)
            in FURNITURE_STAGE_FIELD_MAP.items()
        }

    @api.model
    def _stage_replenishment_upsert_group_lines(self, production, specs):
        Line = self.env['furniture.mrp.production.line'].sudo().with_company(
            production.company_id
        )
        active_lines = production.production_line_ids.filtered('active')
        lines_by_product = {}
        for line in active_lines.sorted(lambda row: (row.sequence, row.id)):
            key = (
                self._stage_replenishment_canonical_product(line.product_id).id,
                line.furniture_order_model_id.id,
            )
            lines_by_product.setdefault(key, line)

        next_sequence = max(active_lines.mapped('sequence') or [0]) + 10
        create_vals = []
        for spec in sorted(specs, key=lambda item: item['product'].id):
            key = (
                self._stage_replenishment_canonical_product(spec['product']).id,
                spec['model'].id,
            )
            line = lines_by_product.get(key)
            if line:
                if set(line._selected_stage_codes()) != set(spec['stage_codes']):
                    raise UserError(_(
                        'The reusable replenishment draft has a different '
                        'production lane route. Create a separate draft.'
                    ))
                line.with_context(
                    furniture_skip_line_consolidation=True,
                ).write({
                    'product_qty': float_round(
                        line.product_qty + spec['quantity'],
                        precision_rounding=line.product_uom_id.rounding or 0.001,
                        rounding_method='UP',
                    ),
                })
                continue
            bom = spec['bom']
            line_vals = {
                'production_id': production.id,
                'sequence': next_sequence,
                'product_id': spec['product'].id,
                'furniture_order_model_id': spec['model'].id,
                'product_qty': spec['quantity'],
                'bom_id': bom.id,
                'width_cm': bom.furniture_width_cm,
                'depth_cm': bom.furniture_depth_cm,
                'height_cm': bom.furniture_height_cm,
                'stage_selection_initialized': True,
            }
            line_vals.update(self._stage_replenishment_stage_flag_values(
                spec['stage_codes']
            ))
            create_vals.append(line_vals)
            next_sequence += 10
        if create_vals:
            Line.with_context(
                furniture_preserve_explicit_bom=True,
                furniture_skip_line_consolidation=True,
            ).create(create_vals)
        self._stage_replenishment_sync_header_from_first_line(production)

    @api.model
    def _stage_replenishment_notify_group(
        self,
        production,
        specs,
        managers,
        created_new,
    ):
        detail = '، '.join(
            _('%(product)s: %(qty)s %(uom)s') % {
                'product': spec['product'].display_name,
                'qty': spec['quantity'],
                'uom': spec['product'].uom_id.display_name,
            }
            for spec in specs
        )
        stage_names = '، '.join(sorted({
            stage_name
            for spec in specs
            for stage_name in spec['trigger_stage_names'].split('، ')
            if stage_name
        }))
        production.message_post(
            body=(
                _(
                    '🔔 تم إنشاء مسودة تعويض Min/Max للموديل %(model)s: '
                    '%(details)s. المراحل: %(stages)s. الأمر بانتظار التأكيد.'
                )
                if created_new else
                _(
                    '➕ تم تحديث مسودة تعويض Min/Max الحالية للموديل '
                    '%(model)s وإضافة: %(details)s. المراحل: %(stages)s.'
                )
            ) % {
                'model': production.furniture_order_model_id.display_name,
                'details': detail,
                'stages': stage_names,
            },
            partner_ids=managers.mapped('partner_id').ids,
        )
        todo_type = self.env.ref(
            'mail.mail_activity_data_todo',
            raise_if_not_found=False,
        )
        for manager in managers:
            has_open_activity = bool(todo_type and production.activity_ids.filtered(
                lambda activity: (
                    activity.user_id == manager
                    and activity.activity_type_id == todo_type
                )
            ))
            if has_open_activity:
                continue
            production.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=manager.id,
                summary=_('تأكيد أمر تعويض Min/Max للمراحل'),
                note=_(
                    'راجع أمر التصنيع %(order)s للموديل %(model)s: '
                    '%(details)s، ثم أكده عند الاعتماد.'
                ) % {
                    'order': production.name,
                    'model': production.furniture_order_model_id.display_name,
                    'details': detail,
                },
            )
        StoreRequest = self.env['furniture.mrp.store.request']
        if hasattr(StoreRequest, '_send_bus_notification'):
            StoreRequest._send_bus_notification(
                managers,
                (
                    _('أمر تصنيع تعويض جديد')
                    if created_new else _('تم تحديث أمر تعويض موجود')
                ),
                _('%(order)s / %(model)s — %(details)s') % {
                    'order': production.name,
                    'model': production.furniture_order_model_id.display_name,
                    'details': detail,
                },
                notification_type='warning',
                action_model=production._name,
                action_res_id=production.id,
                action_name=_('فتح أمر التصنيع'),
                play_sound=True,
            )

    @api.model
    def _stage_replenishment_apply_production_group(self, specs):
        specs = [spec for spec in specs if spec]
        if not specs:
            return self.env['furniture.mrp.production']
        anchor = specs[0]
        company = anchor['company']
        model = anchor['model']
        lane = anchor['lane']
        if lane == 'finish':
            raise UserError(_(
                'لا يتم إنشاء أمر القواعد والتجهيز مباشرة من Min/Max. '
                'يُنشأ تلقائيًا فقط بعد اكتمال النجارة ووصول رصيدها الفعلي.'
            ))
        group_key = self._stage_replenishment_order_group_key(
            anchor['product'],
            model,
            lane=lane,
        )
        if any(
            spec['company'] != company
            or spec['model'] != model
            or spec['lane'] != lane
            or self._stage_replenishment_order_group_key(
                spec['product'], spec['model'], lane=spec['lane']
            ) != group_key
            for spec in specs
        ):
            raise UserError(_(
                'A grouped replenishment order must contain one company, one '
                'model and one compatible grouping policy.'
            ))
        managers = self._stage_replenishment_manager_users(company)
        if not managers:
            raise UserError(_(
                'No active factory manager is assigned to %(company)s. '
                'Assign a manager before automatic stage replenishment can '
                'create and notify about a production order.'
            ) % {'company': company.display_name})

        Production = self.env['furniture.mrp.production'].sudo().with_company(
            company
        )
        generated_at = fields.Datetime.now()
        all_covered_rules = self.env[
            'furniture.mrp.stage.replenishment.rule'
        ]
        for spec in specs:
            all_covered_rules |= spec['covered_rules']
        self._stage_replenishment_lock_editable_workload(
            company,
            all_covered_rules,
        )
        production = self._stage_replenishment_find_reusable_draft(
            company,
            group_key,
        )
        created_new = not bool(production)
        if created_new:
            bom = anchor['bom']
            production_vals = {
                'company_id': company.id,
                'product_id': anchor['product'].id,
                'furniture_order_model_id': model.id,
                'product_qty': anchor['quantity'],
                'bom_id': bom.id,
                'width_cm': bom.furniture_width_cm,
                'depth_cm': bom.furniture_depth_cm,
                'height_cm': bom.furniture_height_cm,
                'date_planned_start': generated_at,
                'date_planned_finish': generated_at + timedelta(days=7),
                'responsible_id': managers[:1].id or False,
                'production_lane': lane,
                'stage_plan_mode': 'custom',
                'stage_replenishment_generated': True,
                'stage_replenishment_rule_ids': [
                    (6, 0, all_covered_rules.ids),
                ],
                'stage_replenishment_created_at': generated_at,
                'notes': _(
                    'مسودة تعويض آلي مجمعة لمستويات Min/Max. '
                    'الموديل: %(model)s. الأصناف: %(products)s.'
                ) % {
                    'model': model.display_name,
                    'products': '، '.join(
                        spec['product'].display_name for spec in specs
                    ),
                },
            }
            production_vals.update(
                self._stage_replenishment_stage_flag_values(
                    anchor['stage_codes']
                )
            )
            production = Production.with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
                furniture_skip_line_consolidation=True,
            )._stage_replenishment_internal_create([production_vals])
        self._stage_replenishment_upsert_group_lines(production, specs)
        combined_rules = (
            production.stage_replenishment_rule_ids | all_covered_rules
        )
        production._stage_replenishment_internal_write({
            'stage_replenishment_rule_ids': [(6, 0, combined_rules.ids)],
        })
        all_covered_rules._stage_replenishment_internal_write({
            'last_generated_at': generated_at,
            'last_production_id': production.id,
        })
        self._stage_replenishment_notify_group(
            production,
            specs,
            managers,
            created_new,
        )
        return production

    @api.model
    def _stage_replenishment_create_production(
        self,
        trigger_rules,
        quantity,
        covered_rules=None,
    ):
        """Compatibility wrapper for callers that generate one product."""
        spec = self._stage_replenishment_prepare_product_spec(
            trigger_rules,
            quantity,
            covered_rules=covered_rules,
        )
        return self._stage_replenishment_apply_production_group([spec])

    @api.model
    def _stage_replenishment_run_company(
        self,
        company,
        restricted_rule_ids=None,
        lock=True,
        raise_on_error=False,
    ):
        if lock:
            self._stage_replenishment_lock_company(company)
        all_rules = self._stage_replenishment_sync_company(company, lock=False)
        rules = all_rules.filtered(lambda rule: rule.max_qty > 0)
        # Normal finish, tailoring and painting buffers retain their own
        # Min/Max generation.  The final coordinator only adds an uncovered
        # real-demand delta.  Frame is suppressed here for final-controlled
        # identities because that serial body delta is owned by the final
        # coordinator and would otherwise be generated twice.
        FinalRule = self.env['furniture.mrp.final.replenishment.rule'].sudo()
        final_identities = {
            (rule.product_id.id, rule.furniture_model_id.id)
            for rule in FinalRule.with_company(company).search([
                ('active', '=', True),
                ('company_id', '=', company.id),
                ('min_qty', '>=', 0),
                ('max_qty', '>', 0),
            ])
        }
        if final_identities:
            rules = rules.filtered(lambda rule: not (
                (
                    rule.product_id.id,
                    rule.furniture_model_id.id,
                ) in final_identities
                and self._stage_replenishment_rule_lane(rule) == 'frame'
            ))
        if restricted_rule_ids is not None:
            if not restricted_rule_ids:
                return self.env['furniture.mrp.production']
            selected = rules.filtered(lambda rule: rule.id in restricted_rule_ids)
            identities = {
                (
                    rule.product_id.id,
                    rule.furniture_model_id.id,
                    self._stage_replenishment_rule_lane(rule),
                )
                for rule in selected
            }
            rules = rules.filtered(lambda rule: (
                rule.product_id.id,
                rule.furniture_model_id.id,
                self._stage_replenishment_rule_lane(rule),
            ) in identities)
        if not rules:
            return self.env['furniture.mrp.production']
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_stage_replenishment_rule '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(rules.ids)],
        )
        rules.invalidate_recordset(['min_qty', 'max_qty', 'bom_id'])
        self._stage_replenishment_lock_editable_workload(company, rules)
        metrics = self._stage_replenishment_metric_map(rules)
        grouped = defaultdict(lambda: self.env[
            'furniture.mrp.stage.replenishment.rule'
        ])
        for rule in rules:
            grouped[(
                rule.product_id.id,
                rule.furniture_model_id.id,
                self._stage_replenishment_rule_lane(rule),
            )] |= rule

        pending_groups = defaultdict(list)
        for identity_rules in grouped.values():
            shortage_rules = identity_rules.filtered(lambda rule: (
                metrics.get(rule.id, {}).get('qty_to_produce', 0.0) > 0
            ))
            if not shortage_rules:
                continue
            quantity = max(
                metrics[rule.id]['qty_to_produce']
                for rule in shortage_rules
            )
            covered_rules = all_rules.filtered(lambda rule: (
                rule.product_id == shortage_rules[0].product_id
                and rule.furniture_model_id
                == shortage_rules[0].furniture_model_id
                and self._stage_replenishment_rule_lane(rule)
                == self._stage_replenishment_rule_lane(shortage_rules[0])
            ))
            lane = self._stage_replenishment_rule_lane(shortage_rules[0])
            # Bases/finishing is a serial downstream lane.  It must not get a
            # speculative Min/Max order: completing the frame/carpentry lane
            # creates it from the physical FIFO output and reserves that exact
            # batch through the handoff engine.
            if lane == 'finish':
                # Keep recipe validation eager even though physical generation
                # is deferred.  A missing bases/finishing route must still be
                # reported to the manager instead of being silently ignored.
                self._stage_replenishment_prepare_product_spec(
                    shortage_rules,
                    quantity,
                    covered_rules=covered_rules,
                )
                continue
            group_key = self._stage_replenishment_order_group_key(
                shortage_rules[0].product_id,
                shortage_rules[0].furniture_model_id,
                lane=lane,
            )
            pending_groups[group_key].append((
                shortage_rules,
                quantity,
                covered_rules,
            ))

        affected = self.env['furniture.mrp.production']
        for group_key in sorted(pending_groups):
            pending_specs = pending_groups[group_key]
            try:
                with self.env.cr.savepoint():
                    specs = [
                        self._stage_replenishment_prepare_product_spec(
                            shortage_rules,
                            quantity,
                            covered_rules=covered_rules,
                        )
                        for shortage_rules, quantity, covered_rules
                        in pending_specs
                    ]
                    affected |= self._stage_replenishment_apply_production_group(
                        specs
                    )
            except Exception:
                if raise_on_error:
                    raise
                _logger.exception(
                    'Unable to generate furniture stage replenishment for %s',
                    [
                        rules[0].product_id.display_name
                        for rules, _quantity, _covered in pending_specs
                    ],
                )
        return self._stage_replenishment_auto_confirm_orders(affected)

    @api.model
    def _cron_generate_stage_replenishment(self):
        Rule = self.sudo()
        # Advisory locks live until transaction end, so every multi-company
        # entry point must acquire them in the same deterministic order.
        for company in self.env['res.company'].sudo().search([]).sorted('id'):
            try:
                with self.env.cr.savepoint():
                    Rule._stage_replenishment_lock_company(company)
                    Rule.with_company(company)._stage_replenishment_run_company(
                        company,
                        lock=False,
                        raise_on_error=False,
                    )
                    # Stage buffers are refreshed first.  The finished-goods
                    # coordinator can then create upholstery from finish +
                    # tailoring in the same cron pass instead of waiting for
                    # the next ten-minute cycle.
                    self.env[
                        'furniture.mrp.final.replenishment.rule'
                    ].sudo().with_company(
                        company
                    )._final_replenishment_run_company(
                        company,
                        lock=False,
                        raise_on_error=False,
                    )
            except Exception:
                _logger.exception(
                    'Unable to run stage replenishment for company %s',
                    company.display_name,
                )
        return True

    @api.model
    def _stage_replenishment_status_label(self, status):
        return dict(RULE_STATUS_SELECTION).get(status, status)

    @api.model
    def _stage_replenishment_dashboard_payload(self):
        rules = self.search([
            ('active', '=', True),
            ('company_id', '=', self.env.company.id),
            ('stage_code', 'in', STAGE_REPLENISHMENT_DISPLAY_CODES),
        ])
        metrics = self._stage_replenishment_metric_map(rules)
        rows = []
        stage_counts = {
            code: {'total': 0, 'need': 0}
            for code in STAGE_CODES
        }
        for rule in rules:
            values = metrics.get(rule.id, {})
            status = values.get('status', 'disabled')
            stage_counts[rule.stage_code]['total'] += 1
            if status == 'to_produce':
                stage_counts[rule.stage_code]['need'] += 1
            rows.append({
                'id': rule.id,
                'stage_code': rule.stage_code,
                'stage': STAGE_REPLENISHMENT_STAGE_LABELS.get(
                    rule.stage_code,
                    rule.stage_code,
                ),
                'lane': self._stage_replenishment_rule_lane(rule),
                'lane_label': STAGE_REPLENISHMENT_LANE_LABELS.get(
                    self._stage_replenishment_rule_lane(rule),
                    self._stage_replenishment_rule_lane(rule),
                ),
                'product': rule.product_id.display_name,
                'model': rule.furniture_model_id.display_name,
                'recipe': rule.bom_id.sudo().display_name,
                'unit': rule.uom_id.display_name,
                'minimum': rule.min_qty,
                'maximum': rule.max_qty,
                'current_qty': values.get('current_qty', 0.0),
                'incoming_qty': values.get('incoming_qty', 0.0),
                'draft_qty': values.get('draft_qty', 0.0),
                'reserved_qty': values.get('reserved_qty', 0.0),
                'upstream_qty': values.get('upstream_qty', 0.0),
                'pipeline_qty': (
                    values.get('incoming_qty', 0.0)
                    + values.get('upstream_qty', 0.0)
                ),
                'forecast_qty': values.get('forecast_qty', 0.0),
                'qty_to_produce': values.get('qty_to_produce', 0.0),
                'status': status,
                'status_label': self._stage_replenishment_status_label(status),
                'can_run': status == 'to_produce',
                'last_production_id': rule.last_production_id.id or False,
                'last_production': rule.last_production_id.display_name or False,
            })
        display_rank = {
            stage_code: index
            for index, stage_code in enumerate(
                STAGE_REPLENISHMENT_DISPLAY_CODES
            )
        }
        rows.sort(key=lambda row: (
            display_rank.get(
                row['stage_code'],
                len(STAGE_REPLENISHMENT_DISPLAY_CODES),
            ),
            row['product'] or '',
            row['model'] or '',
            row['id'],
        ))
        return {
            'stages': [
                {
                    'code': code,
                    'label': STAGE_REPLENISHMENT_STAGE_LABELS[code],
                    **stage_counts[code],
                }
                for code in STAGE_REPLENISHMENT_DISPLAY_CODES
            ],
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
                'draft_orders': self.env['furniture.mrp.production'].search_count([
                    ('company_id', '=', self.env.company.id),
                    ('state', '=', 'draft'),
                    ('stage_replenishment_generated', '=', True),
                ]),
            },
        }

    @api.model
    def stage_replenishment_dashboard_data(self):
        self._stage_replenishment_check_manager()
        company = self.env.company
        self.with_company(company)._stage_replenishment_sync_company(company)
        return self._stage_replenishment_dashboard_payload()

    @api.model
    def stage_replenishment_sync_rules(self):
        self._stage_replenishment_check_manager()
        company = self.env.company
        self.with_company(company)._stage_replenishment_sync_company(company)
        return self._stage_replenishment_dashboard_payload()

    @api.model
    def stage_replenishment_update_limits(self, rule_id, minimum, maximum):
        self._stage_replenishment_check_manager()
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
            raise UserError(_('The stage replenishment rule was not found.'))
        rule.write({'min_qty': minimum, 'max_qty': maximum})
        return self._stage_replenishment_dashboard_payload()

    @api.model
    def stage_replenishment_run_now(self, rule_ids=None):
        self._stage_replenishment_check_manager()
        requested_ids = None
        if rule_ids is not None:
            try:
                requested_ids = list(dict.fromkeys(int(rule_id) for rule_id in rule_ids))
            except (TypeError, ValueError) as error:
                raise ValidationError(_('Invalid stage replenishment selection.')) from error
            if not requested_ids:
                return {
                    'created_ids': [],
                    'created_count': 0,
                    'action': False,
                    'data': self._stage_replenishment_dashboard_payload(),
                }
        selected = self.search([
            ('id', 'in', requested_ids or []),
            ('company_id', '=', self.env.company.id),
        ]) if requested_ids is not None else self
        if requested_ids is not None and set(selected.ids) != set(requested_ids):
            raise AccessError(_(
                'One or more selected rules are missing or belong to another '
                'company. Refresh the page and try again.'
            ))
        created = self.env['furniture.mrp.production']
        company = self.env.company
        created |= self.with_company(company)._stage_replenishment_run_company(
            company,
            restricted_rule_ids=(
                selected.ids if requested_ids is not None else None
            ),
            raise_on_error=True,
        )
        return {
            'created_ids': created.ids,
            'created_count': len(created),
            'action': self._stage_replenishment_production_action(created),
            'data': self._stage_replenishment_dashboard_payload(),
        }

    @api.model
    def _stage_replenishment_production_action(self, productions):
        productions = productions.exists()
        if not productions:
            return False
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Stage Replenishment Production Orders'),
            'res_model': 'furniture.mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', productions.ids)],
            'target': 'current',
        }
        if len(productions) == 1:
            action.update({
                'res_id': productions.id,
                'view_mode': 'form',
                'views': [(False, 'form')],
            })
        return action

    @api.model
    def stage_replenishment_open_orders(self, rule_id=False):
        self._stage_replenishment_check_manager()
        domain = [
            ('company_id', '=', self.env.company.id),
            ('stage_replenishment_generated', '=', True),
        ]
        if rule_id:
            try:
                rule_id = int(rule_id)
            except (TypeError, ValueError) as error:
                raise ValidationError(_(
                    'Invalid stage replenishment rule.'
                )) from error
            rule = self.browse(rule_id).exists()
            if not rule or rule.company_id != self.env.company:
                raise AccessError(_('The selected rule is not available.'))
            domain.append(('stage_replenishment_rule_ids', 'in', rule.ids))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Stage Replenishment Production Orders'),
            'res_model': 'furniture.mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
            'target': 'current',
        }

    def action_stage_replenishment_sync_rules(self):
        """Object-button adapter; RPC payloads are not valid Odoo actions."""
        self._stage_replenishment_check_manager()
        companies = self.mapped('company_id') or self.env.company
        for company in companies.sorted('id'):
            self.with_company(company)._stage_replenishment_sync_company(company)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_stage_replenishment_run_now(self):
        """Object-button adapter for the fallback list/form view."""
        result = self.stage_replenishment_run_now(self.ids or None)
        return result.get('action') or {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
