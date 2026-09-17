# -*- coding: utf-8 -*-
"""Independent stage buffers and auditable inter-order production flow.

Legacy body/cover orders and their historical four-buffer assemblies remain
readable.  New work uses explicit FIFO hand-offs between separate production
orders; the hand-off implementation lives in ``mrp_lane_handoff.py``.
"""

import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .mrp_production_order import FURNITURE_STAGE_FIELD_MAP


PRODUCTION_LANE_SELECTION = [
    ('legacy', 'المسار القديم الكامل'),
    ('frame', 'نجارة'),
    ('finish', 'القواعد والتجهيز'),
    ('tailoring', 'التفصيل والخياطة'),
    ('painting', 'تصنيع الدهانات'),
    ('upholstery', 'الكسوة'),
    ('packaging', 'التغليف'),
    # Historical two-lane values remain readable for orders and audit rows
    # created before the component-buffer flow was introduced.
    ('body', 'نجارة (قديم)'),
    ('cover', 'الكسوة والتفصيل'),
]

PRODUCTION_LANE_ROUTES = {
    'frame': ('priming', 'carpentry'),
    'finish': ('bases', 'finishing'),
    'tailoring': ('tailoring',),
    'painting': ('painting',),
    'upholstery': ('upholstery',),
    'packaging': ('packaging',),
    'body': ('priming', 'carpentry', 'bases', 'finishing'),
    # The business route is upholstery first, then tailoring.  Do not reorder
    # the global legacy stage map: old records depend on its historical order.
    'cover': ('upholstery', 'tailoring'),
}

PRODUCTION_LANE_FINAL_STAGE = {
    'frame': 'carpentry',
    'finish': 'finishing',
    'tailoring': 'tailoring',
    'painting': 'painting',
    'upholstery': 'upholstery',
    'packaging': 'packaging',
    'body': 'finishing',
    'cover': 'tailoring',
}

PRODUCTION_LANE_READY_STAGE = {
    'frame': 'carpentry',
    'finish': 'finishing',
    'tailoring': 'tailoring',
    'painting': 'painting',
    'upholstery': 'upholstery',
    'packaging': 'packaging',
    'body': 'finishing',
    # Tailoring is a material-only stage, so the physical cover remains in the
    # upholstery store while tailoring completes operationally.
    'cover': 'upholstery',
}

PRODUCTION_COMPONENT_LANES = ('frame', 'finish', 'tailoring', 'upholstery')
PRODUCTION_LEGACY_LANES = ('body', 'cover')
PRODUCTION_OUTPUT_LANE_SELECTION = [
    item for item in PRODUCTION_LANE_SELECTION if item[0] != 'legacy'
]
PRODUCTION_OUTPUT_LANE_LABELS = dict(PRODUCTION_OUTPUT_LANE_SELECTION)
FINAL_ASSEMBLY_ROLE_SELECTION = [
    *PRODUCTION_OUTPUT_LANE_SELECTION,
    ('final', 'المنتج النهائي'),
]

LANE_OUTPUT_STATE_SELECTION = [
    ('ready', 'جاهز للتجميع'),
    ('partially_reserved', 'محجوز جزئيًا'),
    ('exhausted', 'تم استهلاكه'),
]

FINAL_ASSEMBLY_STATE_SELECTION = [
    ('draft', 'مسودة'),
    ('reserved', 'محجوز'),
    ('done', 'تم التجميع'),
    ('cancelled', 'ملغي'),
]

WIP_IDENTITY_FIELDS = frozenset({
    'furniture_wip_lane',
    'furniture_wip_final_product_id',
    'furniture_wip_model_id',
    'furniture_wip_company_id',
})

FINAL_ASSEMBLY_MOVE_FIELDS = frozenset({
    'furniture_final_assembly_id',
    'furniture_final_assembly_role',
})


def _lane_route_values(model, lane):
    """Return exact stage booleans without changing the historical map."""
    route = model.env['furniture.mrp.production']._furniture_lane_routes().get(lane)
    return model._stage_selection_vals(route) if route else {}


class ProductProductLaneWip(models.Model):
    _inherit = 'product.product'

    furniture_wip_lane = fields.Selection(
        PRODUCTION_OUTPUT_LANE_SELECTION,
        string='مسار منتج تحت التشغيل',
        copy=False,
        readonly=True,
        index=True,
    )
    furniture_wip_final_product_id = fields.Many2one(
        'product.product',
        string='المنتج النهائي المقابل',
        copy=False,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    furniture_wip_model_id = fields.Many2one(
        'furniture.product.model',
        string='موديل منتج تحت التشغيل',
        copy=False,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    furniture_wip_company_id = fields.Many2one(
        'res.company',
        string='شركة منتج تحت التشغيل',
        copy=False,
        readonly=True,
        index=True,
        ondelete='restrict',
    )

    _sql_constraints = [
        (
            'furniture_wip_lane_identity_uniq',
            'unique(furniture_wip_company_id, furniture_wip_lane, '
            'furniture_wip_final_product_id, furniture_wip_model_id)',
            'يوجد بالفعل منتج تحت التشغيل لنفس الشركة والمسار والمنتج والموديل.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if any(WIP_IDENTITY_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(_(
                'هوية منتج WIP فنية ولا يمكن تعيينها يدويًا.'
            ))
        return super().create(vals_list)

    def write(self, vals):
        if WIP_IDENTITY_FIELDS & set(vals):
            raise AccessError(_(
                'هوية منتج WIP فنية ولا يمكن تعديلها يدويًا.'
            ))
        return super().write(vals)

    def _furniture_internal_set_wip_identity(self, vals):
        """Resolver-only identity write; leading-underscore methods are not RPC."""
        allowed_fields = WIP_IDENTITY_FIELDS | {'default_code'}
        if set(vals) - allowed_fields or not WIP_IDENTITY_FIELDS.issubset(vals):
            raise ValidationError(_('قيم هوية منتج WIP غير مكتملة.'))
        return super(
            ProductProductLaneWip,
            self.sudo(),
        ).write(vals)

    @api.constrains(
        'furniture_wip_lane',
        'furniture_wip_final_product_id',
        'furniture_wip_model_id',
        'furniture_wip_company_id',
    )
    def _check_furniture_wip_identity(self):
        for product in self:
            identity = (
                product.furniture_wip_lane,
                product.furniture_wip_final_product_id,
                product.furniture_wip_model_id,
                product.furniture_wip_company_id,
            )
            if any(identity) and not all(identity):
                raise ValidationError(_(
                    'منتج تحت التشغيل لازم يكون مرتبطًا بالمسار والمنتج النهائي '
                    'والموديل والشركة معًا.'
                ))
            if product.furniture_wip_final_product_id == product:
                raise ValidationError(_('منتج تحت التشغيل لا يمكن أن يكون هو المنتج النهائي نفسه.'))

    @api.model
    def _furniture_get_or_create_lane_wip_product(
        self,
        company,
        lane,
        final_product,
        furniture_model,
    ):
        """Resolve one company/lane/product/model WIP identity atomically."""
        company = company.exists()
        final_product = final_product.exists()
        furniture_model = furniture_model.exists()
        lane_routes = self.env['furniture.mrp.production']._furniture_lane_routes()
        output_lanes = self.env['furniture.mrp.production']._furniture_lane_output_lanes()
        if lane not in set(output_lanes.get(key, key) for key in lane_routes):
            raise ValidationError(_('مسار منتج تحت التشغيل غير صحيح.'))
        if not company or not final_product or not furniture_model:
            raise ValidationError(_(
                'إنشاء منتج تحت التشغيل يحتاج الشركة والمنتج النهائي والموديل.'
            ))
        if final_product.furniture_wip_lane:
            if (
                final_product.furniture_wip_lane == lane
                and final_product.furniture_wip_company_id == company
                and final_product.furniture_wip_model_id == furniture_model
            ):
                return final_product
            raise ValidationError(_('لا يمكن استخدام منتج WIP لمسار أو موديل مختلف.'))

        lock_key = 'furniture-lane-wip:%s:%s:%s:%s' % (
            company.id,
            lane,
            final_product.id,
            furniture_model.id,
        )
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(hashtext(%s))',
            [lock_key],
        )
        Product = self.sudo().with_context(active_test=False)
        domain = [
            ('furniture_wip_company_id', '=', company.id),
            ('furniture_wip_lane', '=', lane),
            ('furniture_wip_final_product_id', '=', final_product.id),
            ('furniture_wip_model_id', '=', furniture_model.id),
        ]
        product = Product.search(domain, limit=1)
        if product:
            restore_vals = {}
            if not product.active:
                restore_vals['active'] = True
            if not product.product_tmpl_id.active:
                product.product_tmpl_id.sudo().write({'active': True})
            if restore_vals:
                product.write(restore_vals)
            return product

        lane_label = self.env['furniture.mrp.production']._furniture_lane_labels()[lane]
        template_name = _('%(product)s — %(model)s — WIP %(lane)s') % {
            'product': final_product.display_name,
            'model': furniture_model.display_name,
            'lane': lane_label,
        }
        template_vals = {
            'name': template_name,
            'type': 'consu',
            'is_storable': True,
            'sale_ok': False,
            'purchase_ok': False,
            'categ_id': final_product.categ_id.id,
            'uom_id': final_product.uom_id.id,
            'uom_po_id': (
                final_product.uom_po_id.id
                if final_product.uom_po_id.category_id == final_product.uom_id.category_id
                else final_product.uom_id.id
            ),
        }
        ProductTemplate = self.env['product.template'].sudo()
        if 'company_id' in ProductTemplate._fields:
            template_vals['company_id'] = company.id
        if 'tracking' in ProductTemplate._fields:
            template_vals['tracking'] = 'none'
        template = ProductTemplate.create(template_vals)
        product = template.product_variant_id or template.product_variant_ids[:1]
        if not product:
            raise UserError(_('تعذر إنشاء نسخة منتج تحت التشغيل.'))
        product._furniture_internal_set_wip_identity({
            'default_code': 'FWIP-%s-%s-%s-%s' % (
                lane.upper(), company.id, final_product.id, furniture_model.id,
            ),
            'furniture_wip_lane': lane,
            'furniture_wip_final_product_id': final_product.id,
            'furniture_wip_model_id': furniture_model.id,
            'furniture_wip_company_id': company.id,
        })
        product.with_company(company).sudo().write({'standard_price': 0.0})
        return product


class FurnitureMrpProductionLane(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _furniture_lane_routes(self):
        """Registry-local extension point; never mutate shared module constants."""
        return dict(PRODUCTION_LANE_ROUTES)

    @api.model
    def _furniture_lane_final_stages(self):
        return dict(PRODUCTION_LANE_FINAL_STAGE)

    @api.model
    def _furniture_lane_ready_stages(self):
        return dict(PRODUCTION_LANE_READY_STAGE)

    @api.model
    def _furniture_lane_output_lanes(self):
        """Production routes may share a compatible physical output identity."""
        return {lane: lane for lane in self._furniture_lane_routes()}

    @api.model
    def _furniture_lane_labels(self):
        return dict(PRODUCTION_LANE_SELECTION)

    production_lane = fields.Selection(
        PRODUCTION_LANE_SELECTION,
        string='مسار أمر الإنتاج',
        default='legacy',
        required=True,
        tracking=True,
        copy=True,
        index=True,
    )
    lane_output_ids = fields.One2many(
        'furniture.mrp.lane.output',
        'production_id',
        string='مخرجات المسار الجاهزة',
        readonly=True,
    )
    can_open_final_assembly = fields.Boolean(
        string='يمكن تأكيد إتمام المنتج',
        compute='_compute_can_open_final_assembly',
        compute_sudo=True,
    )

    @api.depends(
        'production_lane',
        'lane_output_ids.state',
        'lane_output_ids.completable_qty',
    )
    def _compute_can_open_final_assembly(self):
        for production in self:
            production.can_open_final_assembly = (
                production.production_lane in PRODUCTION_LEGACY_LANES
                and any(
                    output.state != 'exhausted'
                    and float_compare(
                        output.completable_qty,
                        0.0,
                        precision_rounding=output.uom_id.rounding or 0.001,
                    ) > 0
                    for output in production.lane_output_ids
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals = []
        for source_vals in vals_list:
            vals = dict(source_vals)
            lane = vals.get('production_lane') or 'legacy'
            if lane in self._furniture_lane_routes():
                vals.update(_lane_route_values(self, lane))
            prepared_vals.append(vals)
        records = super().create(prepared_vals)
        records._furniture_ensure_lane_route()
        return records

    def write(self, vals):
        requested_lane = vals.get('production_lane') or 'legacy'
        if 'production_lane' in vals:
            changing = self.filtered(lambda rec: rec.production_lane != requested_lane)
            protected = changing.filtered(lambda rec: (
                rec.state not in ('draft', 'confirmed')
                or any(rec[field_names[1]] for field_names in FURNITURE_STAGE_FIELD_MAP.values())
                or any(rec.production_line_ids.mapped('first_stage_started'))
            ))
            if protected:
                raise UserError(_(
                    'لا يمكن تغيير مسار أمر الإنتاج بعد بدء أي مرحلة. '
                    'أنشئ أمرًا جديدًا بالمسار الصحيح.'
                ))
        # Inject the exact route *before* inherited write hooks inspect stage
        # flags or rebuild materials.  A post-write repair would leave those
        # hooks exposed to a caller-supplied transient route.
        route_sensitive_fields = (
            set(self._stage_use_field_names())
            | {
                'production_lane', 'product_id', 'furniture_order_model_id',
                'bom_id', 'stage_selection_initialized',
            }
        )
        routed_records = self.env['furniture.mrp.production']
        result = True
        for lane in self._furniture_lane_routes():
            records = self.filtered(lambda rec: (
                requested_lane if 'production_lane' in vals else rec.production_lane
            ) == lane)
            if not records:
                continue
            routed_records |= records
            lane_vals = dict(vals)
            if set(vals) & route_sensitive_fields:
                lane_vals.update(_lane_route_values(records, lane))
            result = super(
                FurnitureMrpProductionLane,
                records,
            ).write(lane_vals) and result
        other_records = self - routed_records
        if other_records:
            result = super(
                FurnitureMrpProductionLane,
                other_records,
            ).write(dict(vals)) and result
        # Keep the route exact after every write.  This also makes stage flags
        # safe against callers forging arbitrary context keys over JSON-RPC.
        self._furniture_ensure_lane_route()
        return result

    def _furniture_ensure_lane_route(self):
        for production in self.filtered(lambda rec: rec.production_lane in self._furniture_lane_routes()):
            route_vals = _lane_route_values(production, production.production_lane)
            header_vals = {
                key: value for key, value in route_vals.items()
                if key in production._fields and production[key] != value
            }
            if header_vals:
                super(
                    FurnitureMrpProductionLane,
                    production.with_context(
                        furniture_skip_material_refresh=True,
                        furniture_skip_stage_plan_sync=True,
                    ),
                ).write(header_vals)
            production.production_line_ids._furniture_ensure_lane_route()
        return True

    def _required_stage_codes(self):
        self.ensure_one()
        route = self._furniture_lane_routes().get(self.production_lane)
        return list(route) if route else super()._required_stage_codes()

    def _is_material_only_stage(self, stage_code):
        self.ensure_one()
        if self.production_lane == 'tailoring' and stage_code == 'tailoring':
            # In the independent buffer flow tailoring produces a real,
            # countable cut/sewn component in the tailoring store.  Legacy
            # and historic combined-cover orders keep the old material-only
            # behaviour so their stock audit is not rewritten.
            return False
        return super()._is_material_only_stage(stage_code)

    def _lane_transform_output_specs(self, specs, ensure_storable=True):
        self.ensure_one()
        if self.production_lane not in self._furniture_lane_routes():
            return specs
        transformed = []
        Product = self.env['product.product']
        for raw_spec in specs:
            spec = dict(raw_spec)
            source_product = spec.get('product')
            if source_product.furniture_wip_lane:
                transformed.append(spec)
                continue
            production_lines = spec.get('production_lines')
            models_in_spec = production_lines.mapped('furniture_order_model_id') if production_lines else self.env['furniture.product.model']
            furniture_model = models_in_spec[:1] or self.furniture_order_model_id
            if len(models_in_spec) > 1:
                raise ValidationError(_(
                    'لا يمكن دمج أكثر من موديل داخل نفس مخرج المسار للصنف %s.'
                ) % source_product.display_name)
            wip_product = Product._furniture_get_or_create_lane_wip_product(
                self.company_id,
                self._furniture_lane_output_lanes().get(self.production_lane, self.production_lane),
                source_product,
                furniture_model,
            )
            if ensure_storable:
                self._ensure_stock_product_is_storable(wip_product, wip_product.uom_id)
            spec.update({
                'product': wip_product,
                'final_product': source_product,
                'uom': wip_product.uom_id,
                'label': _('%s — تحت التشغيل (%s)') % (
                    spec.get('label') or source_product.display_name,
                    self._furniture_lane_labels()[self.production_lane],
                ),
            })
            transformed.append(spec)
        return transformed

    def _get_finished_output_specs_from_lines(self, production_lines, ensure_storable=True):
        specs = super()._get_finished_output_specs_from_lines(
            production_lines,
            ensure_storable=ensure_storable,
        )
        return self._lane_transform_output_specs(specs, ensure_storable=ensure_storable)

    def _get_finished_output_specs(self, ensure_storable=True):
        specs = super()._get_finished_output_specs(ensure_storable=ensure_storable)
        return self._lane_transform_output_specs(specs, ensure_storable=ensure_storable)

    def _production_line_matches_product(self, line, product):
        final_product = product.furniture_wip_final_product_id if product else False
        return super()._production_line_matches_product(line, final_product or product)

    def _get_material_only_stage_start_line_candidates(self, stage_code, stage_order=False):
        candidates = super()._get_material_only_stage_start_line_candidates(
            stage_code,
            stage_order=stage_order,
        )
        if self.production_lane == 'cover' and stage_code == 'tailoring':
            # A tailoring supervisor must be able to evaluate the technical
            # prerequisite without being granted read access to upholstery
            # orders themselves.  Keep the result scoped to the already
            # visible production lines and elevate only the internal yes/no
            # completion lookup across the other department's stage record.
            completion_reader = self.sudo()
            candidates = candidates.filtered(
                lambda line: completion_reader._production_line_stage_done(
                    line.sudo(), 'upholstery'
                )
            )
        return candidates

    def _record_stage_costs(
        self,
        stage_order,
        stage_code,
        production_lines=False,
        carryover_payloads=False,
    ):
        entries = super()._record_stage_costs(
            stage_order,
            stage_code,
            production_lines=production_lines,
            carryover_payloads=carryover_payloads,
        )
        if (
            self.production_lane in self._furniture_lane_final_stages()
            and stage_code == self._furniture_lane_final_stages()[self.production_lane]
        ):
            self._ensure_lane_outputs(production_lines)
        return entries

    def _find_lane_ready_move(self, line, wip_product, source_location):
        self.ensure_one()
        if not line or not wip_product or not source_location:
            return self.env['stock.move']
        return self.env['stock.move'].sudo().search([
            ('state', '=', 'done'),
            ('product_id', '=', wip_product.id),
            ('location_dest_id', '=', source_location.id),
            '|',
            ('furniture_source_production_line_id', '=', line.id),
            ('furniture_source_production_line_ids', 'in', [line.id]),
        ], order='date desc, id desc', limit=1)

    def _ensure_lane_outputs(self, production_lines=False):
        self.ensure_one()
        production_lane = self.production_lane
        if production_lane not in self._furniture_lane_routes():
            return self.env['furniture.mrp.lane.output']
        lane = self._furniture_lane_output_lanes().get(production_lane, production_lane)
        lines = (production_lines or self.production_line_ids).exists().filtered(
            lambda line: line.product_id and float_compare(
                line.product_qty or 0.0, 0.0, precision_digits=3,
            ) > 0
        )
        if not lines:
            return self.env['furniture.mrp.lane.output']

        # Serialise quality callbacks for the same technical batches.  The SQL
        # uniqueness below is a second line of defence against retries.
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production_line '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(lines.ids)],
        )
        Output = self.env['furniture.mrp.lane.output'].sudo()
        outputs = Output
        ready_stage = self._furniture_lane_ready_stages()[production_lane]
        source_location = self._stage_storage_location(ready_stage)
        if not source_location:
            raise UserError(_('لا يوجد مخزن جاهزية محدد لمسار %s.') % lane)

        for line in lines:
            final_product = (
                self._get_or_create_dimensioned_finished_product_for_line(line)
                or line.product_id
            )
            furniture_model = line.furniture_order_model_id or self.furniture_order_model_id
            wip_product = self.env['product.product']._furniture_get_or_create_lane_wip_product(
                self.company_id,
                lane,
                final_product,
                furniture_model,
            )
            qty_ready = self._quantity_in_product_uom(
                wip_product,
                line.product_qty,
                line.product_uom_id or wip_product.uom_id,
            )
            ready_move = self._find_lane_ready_move(line, wip_product, source_location)
            if not ready_move:
                raise UserError(_(
                    'اكتملت المرحلة لكن لم تُوجد حركة مخزون WIP للصنف %s في %s.'
                ) % (wip_product.display_name, source_location.display_name))
            receipt_move = self._finished_wip_receipt_move(wip_product, line)
            if not receipt_move:
                raise UserError(_(
                    'تعذر تحديد حركة إنشاء رصيد WIP للصنف %s.'
                ) % wip_product.display_name)
            snapshot = self._get_production_line_cost_snapshot(line)
            unit_cost = max(
                (snapshot.get('material_unit') or 0.0)
                + (snapshot.get('labor_unit') or 0.0),
                0.0,
            )
            output = Output.search([
                ('production_line_id', '=', line.id),
                ('lane', '=', lane),
            ], limit=1)
            values = {
                'company_id': self.company_id.id,
                'lane': lane,
                'production_id': self.id,
                'production_line_id': line.id,
                'final_product_id': final_product.id,
                'wip_product_id': wip_product.id,
                'furniture_model_id': furniture_model.id,
                'uom_id': wip_product.uom_id.id,
                'source_location_id': source_location.id,
                'origin_receipt_move_id': receipt_move.id,
                'ready_move_id': ready_move.id,
                'qty_ready': qty_ready,
                'unit_cost': unit_cost,
            }
            if output:
                if output.allocation_ids.filtered(
                    lambda allocation: allocation.assembly_id.state in ('reserved', 'done')
                ):
                    immutable_relational_fields = {
                        'final_product_id', 'wip_product_id', 'furniture_model_id',
                        'uom_id', 'source_location_id',
                    }
                    changed = [
                        field_name for field_name in immutable_relational_fields
                        if output[field_name].id != values[field_name]
                    ]
                    if float_compare(output.qty_ready, qty_ready, precision_digits=3) != 0:
                        changed.append('qty_ready')
                    if changed:
                        raise UserError(_(
                            'لا يمكن تغيير هوية أو كمية مخرج مسار تم حجزه للتجميع.'
                        ))
                output._furniture_write_completion_values(values)
            else:
                output = Output.create({
                    **values,
                    # ``ready_at`` is the immutable FIFO timestamp of the first
                    # quality acceptance.  A retry may refresh costs or move
                    # links, but it must never make an old batch look new.
                    'ready_at': fields.Datetime.now(),
                })

            self._assign_finished_fifo_cost(
                ready_move,
                line,
                qty_ready,
                unit_cost,
            )
            outputs |= output
        return outputs

    def _get_finished_transfer_current_payloads(self, runtime_cache=None):
        if self.production_lane in self._furniture_lane_routes():
            return []
        return super()._get_finished_transfer_current_payloads(runtime_cache=runtime_cache)

    def _get_finished_transfer_payloads(self, include_legacy=True, runtime_cache=None):
        if self.production_lane in self._furniture_lane_routes():
            return []
        return super()._get_finished_transfer_payloads(
            include_legacy=include_legacy,
            runtime_cache=runtime_cache,
        )

    def _compute_can_transfer_finished_product(self):
        super()._compute_can_transfer_finished_product()
        for production in self.filtered(
            lambda rec: rec.production_lane in self._furniture_lane_routes()
        ):
            production.can_transfer_finished_product = False

    def action_transfer_finished_product(self):
        lane_orders = self.filtered(
            lambda rec: rec.production_lane in self._furniture_lane_routes()
        )
        if lane_orders:
            raise UserError(_(
                'أوامر مسارات النجارة والكسوة لا تدخل المخزن التام مباشرة. '
                'استخدم زر تأكيد إتمام المنتج لحجز النجارة والكسوة وتجميعهما.'
            ))
        return super().action_transfer_finished_product()

    def action_start_tailoring(self):
        legacy_orders = self.filtered(lambda rec: rec.production_lane != 'cover')
        if legacy_orders:
            super(FurnitureMrpProductionLane, legacy_orders).action_start_tailoring()
        for rec in self.filtered(lambda item: item.production_lane == 'cover'):
            rec._ensure_stage_required('tailoring')
            rec._ensure_stage_locations()
            if rec.state not in ('confirmed', 'in_production'):
                raise UserError(_('يجب تأكيد الأمر أولاً.'))
            if rec.tailoring_order_id:
                raise UserError(_('مرحلة تفصيل بدأت بالفعل.'))
            rec._ensure_stage_start_available('tailoring')
            order = rec._create_stage_order('furniture.mrp.tailoring', 'TAL', {
                'upholstery_order_id': rec.upholstery_order_id.id,
            })
            values = {'state': 'in_production', 'tailoring_order_id': order.id}
            if not rec.date_start:
                values['date_start'] = fields.Datetime.now()
            rec.write(values)
            rec.message_post(body=_('✂️ بدأت مرحلة تفصيل بعد الكسوة: %s') % order.name)
        return True

    def action_open_final_assembly_wizard(self):
        self.ensure_one()
        self.env['furniture.mrp.final.assembly']._furniture_check_completion_access()
        output = self.env['furniture.mrp.lane.output'].search([
            ('production_id', '=', self.id),
            ('state', '!=', 'exhausted'),
            ('completable_qty', '>', 0),
        ], order='fifo_date, ready_at, id', limit=1)
        if not output:
            raise UserError(_(
                'لا يوجد حاليًا جسم وكسوة مطابقان ومتاحان فعليًا لهذا الأمر.'
            ))
        return output.action_open_completion_wizard()

    def _furniture_auto_close_fully_assembled_orders(self):
        """Close a lane order only after every output is physically assembled.

        A reservation is deliberately insufficient: ``assembled_qty`` only
        includes completed assemblies whose final stock move reached finished
        goods.  Checking every positive active production line also prevents a
        multi-product weekly order from closing after its first product.
        """
        productions = self.sudo().exists().filtered(
            lambda rec: rec.production_lane in self._furniture_lane_routes()
            and rec.state == 'in_production'
        )
        if not productions:
            return self

        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(productions.ids)],
        )
        productions.invalidate_recordset([
            'state', 'date_finish', 'production_line_ids', 'lane_output_ids',
        ])
        acting_partner = self.env.user.partner_id
        for production in productions:
            if production.state != 'in_production':
                continue
            lines = production.production_line_ids.filtered(
                lambda line: line.active and line.product_id and float_compare(
                    line.product_qty or 0.0,
                    0.0,
                    precision_rounding=(
                        line.product_uom_id.rounding
                        or line.product_id.uom_id.rounding
                        or 0.001
                    ),
                ) > 0
            )
            outputs = production.lane_output_ids.filtered(
                lambda output: output.production_line_id in lines
            )
            if not lines or set(outputs.mapped('production_line_id').ids) != set(lines.ids):
                continue

            outputs._compute_allocation_quantities()
            if any(
                float_compare(
                    output.assembled_qty,
                    output.qty_ready,
                    precision_rounding=output.uom_id.rounding or 0.001,
                ) < 0
                for output in outputs
            ):
                continue
            if production._has_running_stage_orders():
                continue

            production.write({
                'state': 'done',
                'date_finish': fields.Datetime.now(),
            })
            production.message_post(
                body=_(
                    '✅ أُغلق أمر الإنتاج تلقائيًا بعد تجميع كل أصنافه '
                    'ودخولها مخزن المنتج التام.'
                ),
                author_id=acting_partner.id,
            )
        return self


class FurnitureMrpProductionLineLane(models.Model):
    _inherit = 'furniture.mrp.production.line'

    production_lane = fields.Selection(
        related='production_id.production_lane',
        string='مسار أمر الإنتاج',
        store=True,
        readonly=True,
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals = []
        for source_vals in vals_list:
            vals = dict(source_vals)
            production = self.env['furniture.mrp.production'].browse(
                vals.get('production_id')
            ).exists()
            if production.production_lane in production._furniture_lane_routes():
                vals.update(_lane_route_values(self, production.production_lane))
            prepared_vals.append(vals)
        records = super().create(prepared_vals)
        records._furniture_ensure_lane_route()
        return records

    def write(self, vals):
        lane_lines = self.filtered(
            lambda line: line.production_lane in self.env['furniture.mrp.production']._furniture_lane_routes()
        )
        other_lines = self - lane_lines
        result = True
        if other_lines:
            result = super(FurnitureMrpProductionLineLane, other_lines).write(
                dict(vals)
            ) and result
        for lane in self.env['furniture.mrp.production']._furniture_lane_routes():
            records = lane_lines.filtered(lambda line: line.production_lane == lane)
            if not records:
                continue
            lane_vals = dict(vals)
            if set(vals) & (
                set(records._stage_use_field_names())
                | {'product_id', 'furniture_order_model_id', 'bom_id', 'stage_selection_initialized'}
            ):
                lane_vals.update(_lane_route_values(records, lane))
            result = super(FurnitureMrpProductionLineLane, records).write(
                lane_vals
            ) and result
        lane_lines._furniture_ensure_lane_route()
        return result

    def _furniture_ensure_lane_route(self):
        for lane, route in self.env['furniture.mrp.production']._furniture_lane_routes().items():
            records = self.filtered(lambda line: line.production_lane == lane)
            if not records:
                continue
            desired = _lane_route_values(records, lane)
            dirty = records.filtered(lambda line: any(
                field_name in line._fields and line[field_name] != value
                for field_name, value in desired.items()
            ))
            if dirty:
                super(
                    FurnitureMrpProductionLineLane,
                    dirty.with_context(
                        furniture_skip_stage_plan_sync=True,
                        furniture_skip_material_refresh=True,
                    ),
                ).write(desired)
        return True

    def _selected_stage_codes(self):
        self.ensure_one()
        route = self.env['furniture.mrp.production']._furniture_lane_routes().get(self.production_lane)
        return list(route) if route else super()._selected_stage_codes()

    def _sync_stage_plan_from_bom(self):
        legacy_lines = self.filtered(
            lambda line: line.production_lane not in self.env['furniture.mrp.production']._furniture_lane_routes()
        )
        if legacy_lines:
            super(FurnitureMrpProductionLineLane, legacy_lines)._sync_stage_plan_from_bom()
        self.filtered(
            lambda line: line.production_lane in self.env['furniture.mrp.production']._furniture_lane_routes()
        )._furniture_ensure_lane_route()
        return True


class FurnitureMrpTailoringLane(models.Model):
    _inherit = 'furniture.mrp.tailoring'

    upholstery_order_id = fields.Many2one(
        'furniture.mrp.upholstery',
        string='أمر الكسوة المرجعي',
        readonly=True,
        copy=False,
        ondelete='restrict',
    )


class FurnitureMrpLaneOutput(models.Model):
    _name = 'furniture.mrp.lane.output'
    _description = 'رصيد جاهزية مسارات تصنيع الأثاث'
    _order = 'fifo_date, ready_at, id'
    _rec_name = 'name'

    name = fields.Char(string='المرجع', required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True, ondelete='restrict',
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )
    lane = fields.Selection(
        PRODUCTION_OUTPUT_LANE_SELECTION,
        required=True,
        readonly=True,
        index=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', required=True, readonly=True,
        index=True, ondelete='restrict',
    )
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line', required=True, readonly=True,
        index=True, ondelete='restrict',
    )
    bom_id = fields.Many2one(
        'mrp.bom', related='production_line_id.bom_id', store=True,
        readonly=True, index=True, string='الريسيبي',
    )
    final_product_id = fields.Many2one(
        'product.product', required=True, readonly=True,
        index=True, ondelete='restrict', string='المنتج النهائي',
    )
    wip_product_id = fields.Many2one(
        'product.product', required=True, readonly=True,
        index=True, ondelete='restrict', string='منتج WIP',
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model', required=True, readonly=True,
        index=True, ondelete='restrict', string='الموديل',
    )
    uom_id = fields.Many2one(
        'uom.uom', required=True, readonly=True, ondelete='restrict',
        string='وحدة القياس',
    )
    source_location_id = fields.Many2one(
        'stock.location', required=True, readonly=True,
        index=True, ondelete='restrict', string='مخزن الجاهزية',
    )
    origin_receipt_move_id = fields.Many2one(
        'stock.move', required=True, readonly=True,
        index=True, ondelete='restrict', string='حركة إنشاء WIP',
    )
    ready_move_id = fields.Many2one(
        'stock.move', required=True, readonly=True,
        index=True, ondelete='restrict', string='حركة الجاهزية',
    )
    fifo_date = fields.Datetime(
        related='origin_receipt_move_id.date', store=True, readonly=True, index=True,
        string='تاريخ FIFO',
    )
    qty_ready = fields.Float(
        string='الكمية الجاهزة', required=True, readonly=True,
        digits='Product Unit of Measure',
    )
    unit_cost = fields.Monetary(
        string='تكلفة الوحدة', required=True, readonly=True,
        currency_field='currency_id',
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    total_cost = fields.Monetary(
        string='إجمالي التكلفة', compute='_compute_total_cost',
        currency_field='currency_id',
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    ready_at = fields.Datetime(
        string='وقت الجاهزية', required=True, readonly=True,
        default=fields.Datetime.now, index=True,
    )
    allocation_ids = fields.One2many(
        'furniture.mrp.final.assembly.allocation',
        'output_id',
        string='حجوزات التجميع',
        readonly=True,
    )
    reserved_qty = fields.Float(
        string='الكمية المحجوزة', compute='_compute_allocation_quantities',
        store=True, digits='Product Unit of Measure',
    )
    assembled_qty = fields.Float(
        string='الكمية المجمعة', compute='_compute_allocation_quantities',
        store=True, digits='Product Unit of Measure',
    )
    available_qty = fields.Float(
        string='الكمية المتاحة', compute='_compute_allocation_quantities',
        store=True, digits='Product Unit of Measure',
    )
    physical_available_qty = fields.Float(
        string='المتاح فعليًا بالمخزن', compute='_compute_physical_available_qty',
        digits='Product Unit of Measure',
        help='الأقل بين الرصيد الدفتري الحر والرصيد الحر الفعلي في مخزن الجاهزية.',
    )
    matching_available_qty = fields.Float(
        string='المتاح في المسار المقابل', compute='_compute_matching_quantities',
        store=True, digits='Product Unit of Measure',
    )
    completable_qty = fields.Float(
        string='الكمية القابلة للإكمال', compute='_compute_matching_quantities',
        store=True, digits='Product Unit of Measure',
    )
    state = fields.Selection(
        LANE_OUTPUT_STATE_SELECTION,
        string='الحالة',
        compute='_compute_allocation_quantities',
        store=True,
        index=True,
    )

    _sql_constraints = [
        (
            'furniture_lane_output_line_lane_uniq',
            'unique(production_line_id, lane)',
            'تم تسجيل مخرج هذا السطر والمسار بالفعل.',
        ),
        (
            'furniture_lane_output_qty_positive',
            'check(qty_ready > 0)',
            'كمية مخرج المسار يجب أن تكون أكبر من صفر.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for source_vals in vals_list:
            vals = dict(source_vals)
            if not vals.get('name'):
                lane = vals.get('lane', '').upper()
                vals['name'] = 'LANE/%s/%s/%s' % (
                    lane,
                    vals.get('production_id') or 'NEW',
                    vals.get('production_line_id') or uuid.uuid4().hex[:8],
                )
            prepared.append(vals)
        outputs = super().create(prepared)
        outputs._furniture_refresh_matching_groups()
        return outputs

    def _furniture_write_completion_values(self, values):
        """Trusted lane-completion update hook for downstream extensions."""
        self.ensure_one()
        return self.write(values)

    @api.depends('qty_ready', 'unit_cost')
    def _compute_total_cost(self):
        for output in self:
            output.total_cost = output.qty_ready * output.unit_cost

    @api.depends(
        'qty_ready',
        'allocation_ids.qty',
        'allocation_ids.assembly_id.state',
    )
    def _compute_allocation_quantities(self):
        for output in self:
            reserved = 0.0
            assembled = 0.0
            for allocation in output.allocation_ids:
                if allocation.assembly_id.state == 'reserved':
                    reserved += allocation.qty
                elif allocation.assembly_id.state == 'done':
                    assembled += allocation.qty
            available = max(output.qty_ready - reserved - assembled, 0.0)
            output.reserved_qty = reserved
            output.assembled_qty = assembled
            output.available_qty = available
            if float_compare(available, 0.0, precision_digits=3) <= 0:
                output.state = 'exhausted'
            elif float_compare(reserved + assembled, 0.0, precision_digits=3) > 0:
                output.state = 'partially_reserved'
            else:
                output.state = 'ready'

    @api.depends(
        'available_qty', 'wip_product_id', 'source_location_id', 'company_id',
    )
    def _compute_physical_available_qty(self):
        self = self.sudo()
        Quant = self.env['stock.quant'].sudo()
        seen_keys = set()
        for seed in self:
            if not seed.wip_product_id or not seed.source_location_id:
                seed.physical_available_qty = 0.0
                continue
            key = (
                seed.company_id.id,
                seed.wip_product_id.id,
                seed.source_location_id.id,
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            group_outputs = self.search([
                ('company_id', '=', key[0]),
                ('wip_product_id', '=', key[1]),
                ('source_location_id', '=', key[2]),
            ], order='fifo_date, ready_at, id')
            free_stock = Quant._get_available_quantity(
                seed.wip_product_id,
                seed.source_location_id,
                lot_id=self.env['stock.lot'],
                package_id=self.env['stock.quant.package'],
                owner_id=self.env['res.partner'],
                strict=True,
            )
            remaining_free = max(free_stock, 0.0)
            for output in group_outputs:
                allocated = max(
                    min(output.available_qty, remaining_free),
                    0.0,
                )
                output.physical_available_qty = allocated
                remaining_free -= allocated

    @api.depends(
        'available_qty', 'company_id', 'lane',
        'final_product_id', 'furniture_model_id', 'bom_id', 'uom_id',
    )
    def _compute_matching_quantities(self):
        Output = self.env['furniture.mrp.lane.output']
        for output in self:
            required_lanes = (
                PRODUCTION_COMPONENT_LANES
                if output.lane in PRODUCTION_COMPONENT_LANES
                else PRODUCTION_LEGACY_LANES
            )
            available_by_lane = {}
            for lane in required_lanes:
                matches = Output.search([
                    ('company_id', '=', output.company_id.id),
                    ('lane', '=', lane),
                    ('final_product_id', '=', output.final_product_id.id),
                    ('furniture_model_id', '=', output.furniture_model_id.id),
                    ('bom_id', '=', output.bom_id.id),
                    ('uom_id', '=', output.uom_id.id),
                ])
                available_by_lane[lane] = sum(
                    matches.mapped('physical_available_qty')
                )
            other_quantities = [
                quantity for lane, quantity in available_by_lane.items()
                if lane != output.lane
            ]
            output.matching_available_qty = (
                min(other_quantities) if other_quantities else 0.0
            )
            output.completable_qty = min(available_by_lane.values())

    def _furniture_refresh_matching_groups(self):
        """Refresh both cards because one lane's balance drives the other card."""
        self = self.sudo()
        grouped_outputs = self.env['furniture.mrp.lane.output']
        seen_keys = set()
        for output in self.exists():
            key = (
                output.company_id.id,
                output.final_product_id.id,
                output.furniture_model_id.id,
                output.bom_id.id,
                output.uom_id.id,
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            grouped_outputs |= self.search([
                ('company_id', '=', key[0]),
                ('final_product_id', '=', key[1]),
                ('furniture_model_id', '=', key[2]),
                ('bom_id', '=', key[3]),
                ('uom_id', '=', key[4]),
            ])
        if grouped_outputs:
            grouped_outputs._compute_allocation_quantities()
            grouped_outputs._compute_physical_available_qty()
            grouped_outputs._compute_matching_quantities()
        return grouped_outputs

    @api.model
    def _furniture_auto_assemble_ready_components(
        self,
        company=False,
        final_product=False,
        furniture_model=False,
        bom=False,
        maximum_quantity=False,
    ):
        """FIFO-consume one ready row from each independent component pool.

        The four component buffers are separate Min/Max domains.  This is the
        only bridge between them: no component order is transferred into the
        next component order and no combined manufacturing order is created.
        """
        Output = self.sudo()
        domain = [
            ('lane', 'in', PRODUCTION_COMPONENT_LANES),
            ('state', '!=', 'exhausted'),
        ]
        if company:
            domain.append(('company_id', '=', company.id))
        if final_product:
            domain.append(('final_product_id', '=', final_product.id))
        if furniture_model:
            domain.append(('furniture_model_id', '=', furniture_model.id))
        if bom:
            domain.append(('bom_id', '=', bom.id))
        candidates = Output.search(domain, order='fifo_date, ready_at, id')
        identities = sorted({
            (
                output.company_id.id,
                output.final_product_id.id,
                output.furniture_model_id.id,
                output.bom_id.id,
                output.uom_id.id,
            )
            for output in candidates
        })
        assemblies = self.env['furniture.mrp.final.assembly']
        remaining_limit = (
            max(float(maximum_quantity), 0.0)
            if maximum_quantity is not False else False
        )
        for identity in identities:
            if remaining_limit is not False and float_compare(
                remaining_limit, 0.0, precision_digits=3,
            ) <= 0:
                break
            self.env.cr.execute(
                'SELECT pg_advisory_xact_lock(hashtext(%s))',
                ['furniture-component-assembly:%s' % ':'.join(
                    str(value) for value in identity
                )],
            )
            group_domain = [
                ('company_id', '=', identity[0]),
                ('final_product_id', '=', identity[1]),
                ('furniture_model_id', '=', identity[2]),
                ('bom_id', '=', identity[3]),
                ('uom_id', '=', identity[4]),
                ('lane', 'in', PRODUCTION_COMPONENT_LANES),
                ('state', '!=', 'exhausted'),
            ]
            while True:
                group_outputs = Output.search(
                    group_domain, order='fifo_date, ready_at, id'
                )
                if not group_outputs:
                    break
                group_outputs._furniture_refresh_matching_groups()
                chosen = {}
                for lane in PRODUCTION_COMPONENT_LANES:
                    chosen[lane] = group_outputs.filtered(
                        lambda row, lane=lane: (
                            row.lane == lane
                            and float_compare(
                                row.physical_available_qty,
                                0.0,
                                precision_rounding=row.uom_id.rounding or 0.001,
                            ) > 0
                        )
                    ).sorted(lambda row: (
                        row.fifo_date or row.ready_at or fields.Datetime.now(),
                        row.ready_at or row.fifo_date or fields.Datetime.now(),
                        row.id,
                    ))[:1]
                if any(not output for output in chosen.values()):
                    break
                quantity = min(
                    output.physical_available_qty
                    for output in chosen.values()
                )
                if remaining_limit is not False:
                    quantity = min(quantity, remaining_limit)
                if float_compare(quantity, 0.0, precision_digits=3) <= 0:
                    break
                assembly = self.env[
                    'furniture.mrp.final.assembly'
                ].sudo().create({
                    'frame_output_id': chosen['frame'].id,
                    'finish_output_id': chosen['finish'].id,
                    'tailoring_output_id': chosen['tailoring'].id,
                    'upholstery_output_id': chosen['upholstery'].id,
                    'quantity': quantity,
                })
                assembly.sudo().action_complete()
                assemblies |= assembly
                if remaining_limit is not False:
                    remaining_limit -= quantity
        return assemblies

    @api.constrains(
        'company_id', 'lane', 'production_id', 'production_line_id',
        'final_product_id', 'wip_product_id', 'furniture_model_id', 'uom_id',
        'source_location_id', 'origin_receipt_move_id', 'ready_move_id',
    )
    def _check_lane_output_identity(self):
        for output in self:
            if output.production_id.company_id != output.company_id:
                raise ValidationError(_('شركة مخرج المسار لا تطابق شركة أمر الإنتاج.'))
            production = output.production_id
            output_lane = production._furniture_lane_output_lanes().get(
                production.production_lane, production.production_lane,
            )
            if output_lane != output.lane:
                raise ValidationError(_('نوع مخرج المسار لا يطابق أمر الإنتاج.'))
            if output.production_line_id.production_id != output.production_id:
                raise ValidationError(_('سطر الإنتاج لا يتبع أمر الإنتاج المسجل.'))
            if (
                output.wip_product_id.furniture_wip_lane != output.lane
                or output.wip_product_id.furniture_wip_final_product_id != output.final_product_id
                or output.wip_product_id.furniture_wip_model_id != output.furniture_model_id
                or output.wip_product_id.furniture_wip_company_id != output.company_id
            ):
                raise ValidationError(_('هوية منتج WIP لا تطابق هوية مخرج المسار.'))
            if output.uom_id.category_id != output.wip_product_id.uom_id.category_id:
                raise ValidationError(_('وحدة مخرج المسار لا تطابق وحدة منتج WIP.'))
            if output.ready_move_id.product_id != output.wip_product_id:
                raise ValidationError(_('حركة الجاهزية لا تخص منتج WIP المسجل.'))
            if output.ready_move_id.location_dest_id != output.source_location_id:
                raise ValidationError(_('حركة الجاهزية لا تنتهي في مخزن الجاهزية المسجل.'))

    def _furniture_lock_rows(self):
        ids = self.exists().ids
        if ids:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_lane_output '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(ids)],
            )
            fields_to_invalidate = [
                'allocation_ids', 'reserved_qty', 'assembled_qty',
                'available_qty', 'physical_available_qty', 'state',
            ]
            # ``handoff_ids`` is contributed by mrp_lane_handoff.  Keep this
            # base lock helper usable without importing that extension, while
            # invalidating its reservation cache whenever it is installed.
            if 'handoff_ids' in self._fields:
                fields_to_invalidate.append('handoff_ids')
            self.invalidate_recordset(fields_to_invalidate)
        return self

    def _furniture_fifo_group_outputs(self):
        """Return every pool row sharing the same physical WIP stock bucket."""
        grouped_outputs = self.env['furniture.mrp.lane.output']
        seen_keys = set()
        for output in self.sudo().exists():
            key = (
                output.company_id.id,
                output.wip_product_id.id,
                output.source_location_id.id,
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            grouped_outputs |= self.sudo().search([
                ('company_id', '=', key[0]),
                ('wip_product_id', '=', key[1]),
                ('source_location_id', '=', key[2]),
            ])
        return grouped_outputs

    def _furniture_assert_fifo_available(self):
        """Keep stock valuation and the selected pool row on the same FIFO batch."""
        for output in self:
            older_outputs = self.search([
                ('id', '!=', output.id),
                ('company_id', '=', output.company_id.id),
                ('lane', '=', output.lane),
                ('wip_product_id', '=', output.wip_product_id.id),
                ('source_location_id', '=', output.source_location_id.id),
                '|',
                ('fifo_date', '<', output.fifo_date),
                '&',
                ('fifo_date', '=', output.fifo_date),
                ('id', '<', output.id),
            ], order='fifo_date, id')
            older_available = sum(older_outputs.mapped('physical_available_qty'))
            if float_compare(older_available, 0.0, precision_digits=3) > 0:
                raise UserError(_(
                    'لازم تستخدم أقدم دفعة FIFO من %(lane)s أولًا. '
                    'يوجد %(qty)s من دفعات أقدم للصنف %(product)s.'
                ) % {
                    'lane': self.env['furniture.mrp.production']._furniture_lane_labels()[output.lane],
                    'qty': older_available,
                    'product': output.final_product_id.display_name,
                })
        return True

    def action_open_completion_wizard(self):
        self.ensure_one()
        if self.lane in PRODUCTION_COMPONENT_LANES:
            raise UserError(_(
                'تجميع مخازن المكونات الأربعة يتم تلقائيًا بنظام FIFO فور '
                'اكتمال رصيد مطابق، ولا يحتاج اختيارًا يدويًا.'
            ))
        self.env['furniture.mrp.final.assembly']._furniture_check_completion_access()
        context = {
            'default_company_id': self.company_id.id,
            'default_final_product_id': self.final_product_id.id,
            'default_furniture_model_id': self.furniture_model_id.id,
            'default_bom_id': self.bom_id.id,
            'default_uom_id': self.uom_id.id,
            'default_quantity': self.completable_qty or 1.0,
        }
        context[
            'default_body_output_id' if self.lane == 'body' else 'default_cover_output_id'
        ] = self.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('إكمال منتج من النجارة والكسوة'),
            'res_model': 'furniture.mrp.final.assembly.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': context,
        }

    def unlink(self):
        if self.mapped('allocation_ids'):
            raise UserError(_('لا يمكن حذف مخرج مسار له سجل حجز أو تجميع.'))
        return super().unlink()


class FurnitureMrpFinalAssembly(models.Model):
    _name = 'furniture.mrp.final.assembly'
    _description = 'تجميع مكونات مستقلة إلى منتج أثاث نهائي'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='مرجع التجميع', required=True, readonly=True, copy=False,
        default=lambda self: 'ASM/%s' % uuid.uuid4().hex[:10].upper(),
        index=True,
    )
    body_output_id = fields.Many2one(
        'furniture.mrp.lane.output', index=True,
        ondelete='restrict', string='دفعة النجارة', tracking=True,
    )
    cover_output_id = fields.Many2one(
        'furniture.mrp.lane.output', index=True,
        ondelete='restrict', string='دفعة الكسوة والتفصيل', tracking=True,
    )
    frame_output_id = fields.Many2one(
        'furniture.mrp.lane.output', index=True, ondelete='restrict',
        string='دفعة النجارة', tracking=True,
    )
    finish_output_id = fields.Many2one(
        'furniture.mrp.lane.output', index=True, ondelete='restrict',
        string='دفعة القواعد والتجهيز', tracking=True,
    )
    tailoring_output_id = fields.Many2one(
        'furniture.mrp.lane.output', index=True, ondelete='restrict',
        string='دفعة التفصيل والخياطة', tracking=True,
    )
    upholstery_output_id = fields.Many2one(
        'furniture.mrp.lane.output', index=True, ondelete='restrict',
        string='دفعة الكسوة', tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', compute='_compute_assembly_identity', store=True,
        readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )
    final_product_id = fields.Many2one(
        'product.product', compute='_compute_assembly_identity', store=True,
        readonly=True, index=True, string='المنتج النهائي',
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model', compute='_compute_assembly_identity',
        store=True, readonly=True, index=True, string='الموديل',
    )
    bom_id = fields.Many2one(
        'mrp.bom', compute='_compute_assembly_identity', store=True,
        readonly=True, index=True, string='الريسيبي',
    )
    uom_id = fields.Many2one(
        'uom.uom', compute='_compute_assembly_identity', store=True,
        readonly=True, string='وحدة القياس',
    )
    quantity = fields.Float(
        string='كمية التجميع', required=True, default=1.0,
        digits='Product Unit of Measure', tracking=True,
    )
    allocation_ids = fields.One2many(
        'furniture.mrp.final.assembly.allocation',
        'assembly_id',
        string='توزيعات المسارات',
        readonly=True,
    )
    component_move_ids = fields.One2many(
        'stock.move',
        'furniture_final_assembly_id',
        string='حركات استهلاك المسارات',
        domain=[('furniture_final_assembly_role', 'in', tuple(
            lane for lane, _label in PRODUCTION_OUTPUT_LANE_SELECTION
        ))],
        readonly=True,
    )
    final_move_id = fields.Many2one(
        'stock.move', string='حركة المنتج النهائي',
        readonly=True, copy=False, ondelete='restrict', tracking=True,
    )
    body_unit_cost = fields.Monetary(
        string='تكلفة النجارة للوحدة', readonly=True,
        currency_field='currency_id', tracking=True,
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    cover_unit_cost = fields.Monetary(
        string='تكلفة الكسوة والتفصيل للوحدة', readonly=True,
        currency_field='currency_id', tracking=True,
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    frame_unit_cost = fields.Monetary(
        string='تكلفة النجارة للوحدة', readonly=True,
        currency_field='currency_id', tracking=True,
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    finish_unit_cost = fields.Monetary(
        string='تكلفة القواعد والتجهيز للوحدة', readonly=True,
        currency_field='currency_id', tracking=True,
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    tailoring_unit_cost = fields.Monetary(
        string='تكلفة التفصيل والخياطة للوحدة', readonly=True,
        currency_field='currency_id', tracking=True,
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    upholstery_unit_cost = fields.Monetary(
        string='تكلفة الكسوة للوحدة', readonly=True,
        currency_field='currency_id', tracking=True,
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    unit_cost = fields.Monetary(
        string='تكلفة المنتج النهائي للوحدة', compute='_compute_costs',
        store=True, currency_field='currency_id',
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    total_cost = fields.Monetary(
        string='إجمالي التكلفة', compute='_compute_costs',
        store=True, currency_field='currency_id',
        groups='furniture_mrp.group_furniture_mrp_manager',
    )
    state = fields.Selection(
        FINAL_ASSEMBLY_STATE_SELECTION,
        string='الحالة', default='draft', required=True,
        readonly=True, copy=False, tracking=True, index=True,
    )
    reserved_by_id = fields.Many2one(
        'res.users', string='حجز بواسطة', readonly=True, copy=False,
    )
    reserved_at = fields.Datetime(string='وقت الحجز', readonly=True, copy=False)
    completed_by_id = fields.Many2one(
        'res.users', string='أكمل بواسطة', readonly=True, copy=False,
    )
    completed_at = fields.Datetime(string='وقت الإكمال', readonly=True, copy=False)

    _sql_constraints = [
        (
            'furniture_final_assembly_qty_positive',
            'check(quantity > 0)',
            'كمية التجميع يجب أن تكون أكبر من صفر.',
        ),
    ]

    @api.depends(
        'body_output_id', 'cover_output_id', 'frame_output_id',
        'finish_output_id', 'tailoring_output_id', 'upholstery_output_id',
    )
    def _compute_assembly_identity(self):
        for assembly in self:
            anchor = (
                assembly.frame_output_id
                or assembly.body_output_id
                or assembly.finish_output_id
                or assembly.tailoring_output_id
                or assembly.upholstery_output_id
                or assembly.cover_output_id
            )
            assembly.company_id = anchor.company_id if anchor else False
            assembly.final_product_id = (
                anchor.final_product_id if anchor else False
            )
            assembly.furniture_model_id = (
                anchor.furniture_model_id if anchor else False
            )
            assembly.bom_id = anchor.bom_id if anchor else False
            assembly.uom_id = anchor.uom_id if anchor else False

    def _furniture_output_field_map(self):
        self.ensure_one()
        if self.frame_output_id:
            return {
                'frame': 'frame_output_id',
                'finish': 'finish_output_id',
                'tailoring': 'tailoring_output_id',
                'upholstery': 'upholstery_output_id',
            }
        return {'body': 'body_output_id', 'cover': 'cover_output_id'}

    def _furniture_component_outputs(self):
        self.ensure_one()
        outputs = self.env['furniture.mrp.lane.output']
        for field_name in self._furniture_output_field_map().values():
            outputs |= self[field_name]
        return outputs

    @api.model
    def _furniture_check_completion_access(self):
        if self.env.su:
            return True
        user = self.env.user
        if (
            user.has_group('furniture_mrp.group_furniture_mrp_completion_operator')
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
        ):
            return True
        raise AccessError(_(
            'إتمام المنتج وحجز مساري النجارة والكسوة متاح فقط لمسؤول الإتمام أو مدير الإنتاج.'
        ))

    @api.model_create_multi
    def create(self, vals_list):
        self._furniture_check_completion_access()
        protected_fields = {
            'state', 'body_unit_cost', 'cover_unit_cost', 'final_move_id',
            'frame_unit_cost', 'finish_unit_cost', 'tailoring_unit_cost',
            'upholstery_unit_cost',
            'reserved_by_id', 'reserved_at', 'completed_by_id', 'completed_at',
        }
        for vals in vals_list:
            if set(vals) & protected_fields:
                raise AccessError(_('حقول تدقيق التجميع لا تُكتب يدويًا.'))
        return super().create(vals_list)

    @api.model
    def _furniture_internal_create(self, vals_list):
        """Wizard-only creation after an explicit completion-group check."""
        self._furniture_check_completion_access()
        return super(
            FurnitureMrpFinalAssembly,
            self.sudo(),
        ).create(vals_list)

    @api.depends(
        'body_unit_cost', 'cover_unit_cost', 'frame_unit_cost',
        'finish_unit_cost', 'tailoring_unit_cost', 'upholstery_unit_cost',
        'quantity',
    )
    def _compute_costs(self):
        for assembly in self:
            if assembly.frame_output_id:
                assembly.unit_cost = sum((
                    assembly.frame_unit_cost,
                    assembly.finish_unit_cost,
                    assembly.tailoring_unit_cost,
                    assembly.upholstery_unit_cost,
                ))
            else:
                assembly.unit_cost = (
                    assembly.body_unit_cost + assembly.cover_unit_cost
                )
            assembly.total_cost = assembly.unit_cost * assembly.quantity

    def write(self, vals):
        self._furniture_check_completion_access()
        protected_fields = {
            'state', 'body_unit_cost', 'cover_unit_cost', 'final_move_id',
            'frame_unit_cost', 'finish_unit_cost', 'tailoring_unit_cost',
            'upholstery_unit_cost',
            'reserved_by_id', 'reserved_at', 'completed_by_id', 'completed_at',
        }
        if set(vals) & protected_fields:
            raise AccessError(_('حقول حالة وتكلفة وتدقيق التجميع تتغير من الأزرار المعتمدة فقط.'))
        sensitive_fields = {
            'body_output_id', 'cover_output_id', 'frame_output_id',
            'finish_output_id', 'tailoring_output_id',
            'upholstery_output_id', 'quantity',
        }
        if set(vals) & sensitive_fields and any(
            assembly.state != 'draft' for assembly in self
        ):
            raise AccessError(_(
                'لا يمكن تغيير النجارة أو الكسوة أو الكمية بعد حجز عملية التجميع.'
            ))
        outputs_to_refresh = self.mapped('allocation_ids.output_id')
        outputs_to_refresh |= (
            self.mapped('body_output_id') | self.mapped('cover_output_id')
            | self.mapped('frame_output_id') | self.mapped('finish_output_id')
            | self.mapped('tailoring_output_id')
            | self.mapped('upholstery_output_id')
        )
        result = super().write(vals)
        if set(vals) & (sensitive_fields | {'state'}):
            for assembly in self:
                outputs_to_refresh |= assembly._furniture_component_outputs()
            outputs_to_refresh._furniture_refresh_matching_groups()
        return result

    def _furniture_internal_write(self, vals):
        """Workflow-only write path; private ORM methods are not JSON-RPC callable."""
        outputs_to_refresh = self.mapped('allocation_ids.output_id').sudo()
        for assembly in self:
            outputs_to_refresh |= assembly._furniture_component_outputs().sudo()
        result = super(FurnitureMrpFinalAssembly, self.sudo()).write(vals)
        if set(vals) & {
            'state', 'body_output_id', 'cover_output_id', 'frame_output_id',
            'finish_output_id', 'tailoring_output_id',
            'upholstery_output_id', 'quantity',
        }:
            for assembly in self:
                outputs_to_refresh |= assembly._furniture_component_outputs()
            outputs_to_refresh._furniture_refresh_matching_groups()
        return result

    @api.constrains(
        'body_output_id', 'cover_output_id', 'frame_output_id',
        'finish_output_id', 'tailoring_output_id', 'upholstery_output_id',
        'quantity',
    )
    def _check_output_pair(self):
        for assembly in self:
            field_map = assembly._furniture_output_field_map()
            outputs = [assembly[field_name] for field_name in field_map.values()]
            if not all(outputs):
                raise ValidationError(_(
                    'لازم تختار كل دفعات المكونات المطلوبة قبل التجميع النهائي.'
                ))
            if any(
                output.lane != lane
                for lane, output in zip(field_map, outputs)
            ):
                raise ValidationError(_(
                    'نوع دفعة المكون لا يطابق مخزن الجاهزية المحدد.'
                ))
            if assembly.frame_output_id and (
                assembly.body_output_id or assembly.cover_output_id
            ):
                raise ValidationError(_(
                    'لا يمكن خلط رصيد المسارين القديمين مع مخازن المكونات الجديدة.'
                ))
            body = outputs[0]
            identity_fields = (
                'company_id', 'final_product_id', 'furniture_model_id', 'uom_id',
            )
            if any(
                body[field_name] != output[field_name]
                for output in outputs[1:]
                for field_name in identity_fields
            ):
                raise ValidationError(_(
                    'كل دفعات المكونات لازم تكون لنفس الشركة والمنتج والموديل ووحدة القياس.'
                ))
            if any(body.bom_id != output.bom_id for output in outputs[1:]):
                raise ValidationError(_('كل دفعات المكونات لازم تتبع نفس الريسيبي.'))
            if len({output.wip_product_id.id for output in outputs}) != len(outputs):
                raise ValidationError(_('منتجات WIP للمكونات لازم تكون منفصلة.'))
            if assembly.quantity <= 0:
                raise ValidationError(_('كمية التجميع يجب أن تكون أكبر من صفر.'))

    def _furniture_lock_assembly(self):
        ids = self.exists().ids
        if ids:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_final_assembly '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(ids)],
            )
            self.invalidate_recordset()
        return self

    def _furniture_component_move_values(self, output, role, production_location):
        self.ensure_one()
        return {
            'name': _('%s - حجز %s') % (
                self.name,
                self.env['furniture.mrp.production']._furniture_lane_labels()[output.lane],
            ),
            'product_id': output.wip_product_id.id,
            'product_uom': output.uom_id.id,
            'product_uom_qty': self.quantity,
            'location_id': output.source_location_id.id,
            'location_dest_id': production_location.id,
            'origin': self.name,
            'company_id': self.company_id.id,
            'furniture_source_production_line_id': output.production_line_id.id,
            'furniture_final_assembly_id': self.id,
            'furniture_final_assembly_role': role,
        }

    def action_reserve(self):
        self._furniture_check_completion_access()
        for assembly in self:
            assembly._furniture_lock_assembly()
            if assembly.state in ('reserved', 'done'):
                continue
            if assembly.state != 'draft':
                raise UserError(_('يمكن حجز عملية تجميع وهي في حالة مسودة فقط.'))
            field_map = assembly._furniture_output_field_map()
            outputs = assembly._furniture_component_outputs().sudo()
            fifo_group_outputs = outputs._furniture_fifo_group_outputs()
            fifo_group_outputs._furniture_lock_rows()
            fifo_group_outputs._compute_allocation_quantities()
            fifo_group_outputs._compute_physical_available_qty()
            assembly._check_output_pair()
            outputs._furniture_assert_fifo_available()
            for output in outputs:
                if float_compare(
                    output.physical_available_qty,
                    assembly.quantity,
                    precision_digits=3,
                ) < 0:
                    raise UserError(_(
                        'الرصيد المتاح من %(lane)s هو %(available)s، والمطلوب %(required)s.'
                    ) % {
                        'lane': self.env['furniture.mrp.production']._furniture_lane_labels()[output.lane],
                        'available': output.physical_available_qty,
                        'required': assembly.quantity,
                    })

            anchor_output = outputs.sorted('id')[:1]
            production_location = anchor_output.production_id._get_production_location()
            if not production_location:
                raise UserError(_('لا يوجد موقع إنتاج لتجميع مكونات المنتج.'))
            Move = self.env['stock.move']
            moves = Move._furniture_internal_create_for_assembly([
                assembly._furniture_component_move_values(
                    assembly[field_name], lane, production_location,
                )
                for lane, field_name in field_map.items()
            ])
            moves._action_confirm(merge=False)
            moves._action_assign()
            unreserved = moves.filtered(lambda move: move.state != 'assigned')
            if unreserved:
                raise UserError(_(
                    'تعذر حجز رصيد مخزني فعلي لعملية التجميع: %s'
                ) % '، '.join(unreserved.mapped('product_id.display_name')))

            move_by_role = {
                move.furniture_final_assembly_role: move for move in moves
            }
            self.env[
                'furniture.mrp.final.assembly.allocation'
            ]._furniture_internal_create([{
                'assembly_id': assembly.id,
                'lane': lane,
                'output_id': assembly[field_name].id,
                'qty': assembly.quantity,
                'consumption_move_id': move_by_role[lane].id,
            } for lane, field_name in field_map.items()])
            cost_values = {
                'state': 'reserved',
                'reserved_by_id': self.env.user.id,
                'reserved_at': fields.Datetime.now(),
            }
            for lane, field_name in field_map.items():
                cost_values['%s_unit_cost' % lane] = (
                    assembly[field_name].sudo().unit_cost
                )
            assembly._furniture_internal_write(cost_values)
            assembly.sudo().message_post(
                body=_('🔒 تم حجز كل مكونات المنتج فعليًا في المخزون.'),
                author_id=self.env.user.partner_id.id,
            )
        return True

    def _furniture_move_actual_unit_cost(self, move, quantity):
        self.ensure_one()
        layers = self.env['stock.valuation.layer'].sudo().search([
            ('stock_move_id', '=', move.id),
        ])
        if not layers or float_compare(quantity, 0.0, precision_digits=3) <= 0:
            return 0.0
        # Component consumption leaves internal stock for a production location,
        # therefore its valuation is negative.
        return max(-sum(layers.mapped('value')) / quantity, 0.0)

    def _furniture_complete_component_moves(self):
        self.ensure_one()
        for allocation in self.allocation_ids.sorted(lambda row: row.lane):
            move = allocation.consumption_move_id.sudo()
            if move.state == 'done':
                continue
            if move.state == 'cancel':
                raise UserError(_('إحدى حركات الحجز ملغاة؛ ألغِ عملية التجميع وأنشئ حجزًا جديدًا.'))
            move._action_assign()
            if move.state != 'assigned':
                raise UserError(_('رصيد WIP المحجوز لم يعد متاحًا لإكمال التجميع.'))
            move.quantity = allocation.qty
            move.picked = True
            move._action_done()

    def _furniture_create_final_move(self):
        self.ensure_one()
        anchor_output = self._furniture_component_outputs().sorted('id')[:1]
        anchor_production = anchor_output.production_id
        anchor_production._ensure_stock_product_is_storable(
            self.final_product_id,
            self.uom_id,
        )
        source_location = anchor_production._get_production_location()
        destination = (
            anchor_production.location_dest_id
            or self.env.ref('furniture_mrp.location_finished_goods', raise_if_not_found=False)
        )
        if not source_location or not destination:
            raise UserError(_('لا يوجد موقع إنتاج أو مخزن منتج تام لإكمال التجميع.'))
        assembly_sudo = self.sudo()
        move = self.env['stock.move']._furniture_internal_create_for_assembly([{
            'name': _('%s - استلام المنتج النهائي بعد تجميع النجارة والكسوة') % self.name,
            'product_id': self.final_product_id.id,
            'product_uom': self.uom_id.id,
            'product_uom_qty': self.quantity,
            'location_id': source_location.id,
            'location_dest_id': destination.id,
            'origin': self.name,
            'company_id': self.company_id.id,
            'price_unit': assembly_sudo.unit_cost,
            'furniture_actual_unit_cost': assembly_sudo.unit_cost,
            'furniture_source_production_line_id': anchor_output.production_line_id.id,
            'furniture_final_assembly_id': self.id,
            'furniture_final_assembly_role': 'final',
        }])
        anchor_production._finalize_stock_move(move, self.quantity)
        layers = self.env['stock.valuation.layer'].sudo().search([
            ('stock_move_id', '=', move.id),
        ])
        if layers:
            layers.write({
                'furniture_final_assembly_id': self.id,
                'furniture_finished_production_line_id': (
                    anchor_output.production_line_id.id
                ),
                'furniture_finished_move_id': move.id,
            })
        return move

    def action_complete(self):
        self._furniture_check_completion_access()
        productions_to_check = self.env['furniture.mrp.production']
        for assembly in self:
            assembly._furniture_lock_assembly()
            productions_to_check |= assembly._furniture_component_outputs().mapped(
                'production_id'
            )
            if assembly.state == 'done':
                continue
            if assembly.state == 'draft':
                assembly.action_reserve()
                assembly._furniture_lock_assembly()
            if assembly.state != 'reserved':
                raise UserError(_('لا يمكن إكمال عملية تجميع غير محجوزة.'))

            assembly._furniture_complete_component_moves()
            cost_values = {}
            for allocation in assembly.allocation_ids:
                actual = assembly._furniture_move_actual_unit_cost(
                    allocation.consumption_move_id,
                    assembly.quantity,
                )
                cost_field = '%s_unit_cost' % allocation.lane
                cost_values[cost_field] = (
                    actual or assembly.sudo()[cost_field]
                )
            assembly._furniture_internal_write(cost_values)
            if assembly.final_move_id:
                final_move = assembly.final_move_id
                if final_move.state != 'done':
                    assembly._furniture_component_outputs().sorted('id')[:1].production_id._finalize_stock_move(
                        final_move,
                        assembly.quantity,
                    )
            else:
                final_move = assembly._furniture_create_final_move()
                assembly._furniture_internal_write({'final_move_id': final_move.id})
            assembly._furniture_internal_write({
                'state': 'done',
                'completed_by_id': self.env.user.id,
                'completed_at': fields.Datetime.now(),
            })
            assembly.sudo().message_post(
                body=_('✅ تم إنشاء المنتج النهائي من مخازن مكوناته الجاهزة.'),
                author_id=self.env.user.partner_id.id,
            )
        productions_to_check._furniture_auto_close_fully_assembled_orders()
        # Public object buttons must return JSON-serializable values.  The
        # final move stays available through ``final_move_id`` for audit.
        return True

    def action_cancel(self):
        self._furniture_check_completion_access()
        for assembly in self:
            assembly._furniture_lock_assembly()
            if assembly.state == 'done':
                raise UserError(_('لا يمكن إلغاء تجميع تم إدخاله للمخزن التام.'))
            if assembly.state == 'cancelled':
                continue
            moves = assembly.sudo().component_move_ids.filtered(
                lambda move: move.state not in ('done', 'cancel')
            )
            if moves:
                moves._action_cancel()
            assembly._furniture_internal_write({'state': 'cancelled'})
            assembly.sudo().message_post(
                body=_('❌ تم إلغاء حجز عملية التجميع.'),
                author_id=self.env.user.partner_id.id,
            )
        return True

    def unlink(self):
        self._furniture_check_completion_access()
        protected = self.filtered(lambda assembly: assembly.state not in ('draft', 'cancelled'))
        if protected:
            raise UserError(_('لا يمكن حذف عملية تجميع محجوزة أو مكتملة.'))
        return super().unlink()


class FurnitureMrpFinalAssemblyAllocation(models.Model):
    _name = 'furniture.mrp.final.assembly.allocation'
    _description = 'توزيع رصيد مسار على عملية تجميع نهائي'
    _order = 'assembly_id, lane, id'

    assembly_id = fields.Many2one(
        'furniture.mrp.final.assembly', required=True, index=True,
        ondelete='cascade', string='عملية التجميع',
    )
    lane = fields.Selection(
        PRODUCTION_OUTPUT_LANE_SELECTION,
        required=True, readonly=True, index=True,
    )
    output_id = fields.Many2one(
        'furniture.mrp.lane.output', required=True, index=True,
        ondelete='restrict', string='مخرج المسار',
    )
    qty = fields.Float(
        string='الكمية', required=True, readonly=True,
        digits='Product Unit of Measure',
    )
    consumption_move_id = fields.Many2one(
        'stock.move', required=True, readonly=True,
        ondelete='restrict', string='حركة الاستهلاك',
    )

    _sql_constraints = [
        (
            'furniture_assembly_allocation_lane_uniq',
            'unique(assembly_id, lane)',
            'لا يمكن تكرار نفس المسار داخل عملية التجميع.',
        ),
        (
            'furniture_assembly_allocation_qty_positive',
            'check(qty > 0)',
            'كمية توزيع المسار يجب أن تكون أكبر من صفر.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_('توزيعات المسارات تُنشأ داخليًا عند حجز التجميع فقط.'))

    @api.model
    def _furniture_internal_create(self, vals_list):
        """Workflow-only creation; the leading underscore blocks JSON-RPC."""
        return super(
            FurnitureMrpFinalAssemblyAllocation,
            self.sudo(),
        ).create(vals_list)

    def write(self, vals):
        raise AccessError(_('توزيعات المسارات سجلات تدقيق غير قابلة للتعديل اليدوي.'))

    def unlink(self):
        raise AccessError(_('توزيعات المسارات سجلات تدقيق غير قابلة للحذف اليدوي.'))

    @api.constrains(
        'assembly_id', 'lane', 'output_id', 'qty', 'consumption_move_id',
    )
    def _check_allocation_identity(self):
        for allocation in self:
            if allocation.output_id.lane != allocation.lane:
                raise ValidationError(_('نوع مخرج المسار لا يطابق سطر التوزيع.'))
            field_name = allocation.assembly_id._furniture_output_field_map().get(
                allocation.lane
            )
            expected_output = (
                allocation.assembly_id[field_name] if field_name else False
            )
            if allocation.output_id != expected_output:
                raise ValidationError(_('مخرج المسار لا يطابق عملية التجميع.'))
            if float_compare(
                allocation.qty,
                allocation.assembly_id.quantity,
                precision_digits=3,
            ) != 0:
                raise ValidationError(_('كمية توزيع المسار لازم تساوي كمية التجميع.'))
            if (
                allocation.consumption_move_id.furniture_final_assembly_id
                != allocation.assembly_id
                or allocation.consumption_move_id.furniture_final_assembly_role
                != allocation.lane
            ):
                raise ValidationError(_('حركة الاستهلاك لا تطابق عملية التجميع أو المسار.'))


class StockMoveFinalAssembly(models.Model):
    _inherit = 'stock.move'

    furniture_final_assembly_id = fields.Many2one(
        'furniture.mrp.final.assembly',
        string='عملية تجميع النجارة والكسوة',
        copy=False,
        readonly=True,
        index=True,
        ondelete='set null',
    )
    furniture_final_assembly_role = fields.Selection(
        FINAL_ASSEMBLY_ROLE_SELECTION,
        string='دور الحركة في التجميع',
        copy=False,
        readonly=True,
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any(FINAL_ASSEMBLY_MOVE_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(_(
                'ربط حركة المخزون بتجميع نهائي يتم من مسار الإتمام فقط.'
            ))
        return super().create(vals_list)

    def write(self, vals):
        if FINAL_ASSEMBLY_MOVE_FIELDS & set(vals):
            raise AccessError(_(
                'لا يمكن تعديل هوية حركة التجميع النهائي يدويًا.'
            ))
        return super().write(vals)

    @api.model_create_multi
    def _furniture_internal_create_for_assembly(self, vals_list):
        """Workflow-only move creation after completion access was checked."""
        allowed_roles = {
            lane for lane, _label in FINAL_ASSEMBLY_ROLE_SELECTION
        }
        for vals in vals_list:
            if (
                not vals.get('furniture_final_assembly_id')
                or vals.get('furniture_final_assembly_role')
                not in allowed_roles
            ):
                raise ValidationError(_('هوية حركة التجميع غير مكتملة.'))
        return super(
            StockMoveFinalAssembly,
            self.sudo(),
        ).create(vals_list)

    @api.constrains('furniture_final_assembly_id', 'furniture_final_assembly_role')
    def _check_final_assembly_move_identity(self):
        for move in self:
            if bool(move.furniture_final_assembly_id) != bool(move.furniture_final_assembly_role):
                raise ValidationError(_('مرجع التجميع ودور حركة المخزون لازم يتحددوا معًا.'))


class StockValuationLayerFinalAssembly(models.Model):
    _inherit = 'stock.valuation.layer'

    furniture_final_assembly_id = fields.Many2one(
        'furniture.mrp.final.assembly',
        string='عملية تجميع النجارة والكسوة',
        copy=False,
        readonly=True,
        index=True,
        ondelete='set null',
    )


class StockQuantLaneOutputRefresh(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _update_available_quantity(
        self,
        product_id,
        location_id,
        quantity=False,
        reserved_quantity=False,
        lot_id=None,
        package_id=None,
        owner_id=None,
        in_date=None,
    ):
        result = super()._update_available_quantity(
            product_id,
            location_id,
            quantity=quantity,
            reserved_quantity=reserved_quantity,
            lot_id=lot_id,
            package_id=package_id,
            owner_id=owner_id,
            in_date=in_date,
        )
        if product_id.furniture_wip_lane:
            outputs = self.env['furniture.mrp.lane.output'].sudo().search([
                ('wip_product_id', '=', product_id.id),
                ('source_location_id', '=', location_id.id),
            ])
            if outputs:
                outputs._furniture_refresh_matching_groups()
        return result


class FurnitureMrpFinalAssemblyWizard(models.TransientModel):
    _name = 'furniture.mrp.final.assembly.wizard'
    _description = 'إكمال منتج نهائي من النجارة والكسوة'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )
    final_product_id = fields.Many2one(
        'product.product', string='المنتج النهائي', required=True,
        domain=[('furniture_wip_lane', '=', False)],
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model', string='الموديل', required=True,
    )
    bom_id = fields.Many2one(
        'mrp.bom', string='الريسيبي',
        domain="[('type', '=', 'normal')]",
    )
    uom_id = fields.Many2one(
        'uom.uom', string='وحدة القياس', required=True, readonly=True,
    )
    quantity = fields.Float(
        string='الكمية', required=True, default=1.0,
        digits='Product Unit of Measure',
    )
    body_output_id = fields.Many2one(
        'furniture.mrp.lane.output', string='دفعة النجارة', required=True,
        domain="[('company_id', '=', company_id), ('lane', '=', 'body'), ('final_product_id', '=', final_product_id), ('furniture_model_id', '=', furniture_model_id), ('bom_id', '=', bom_id), ('uom_id', '=', uom_id), ('state', '!=', 'exhausted')]",
    )
    cover_output_id = fields.Many2one(
        'furniture.mrp.lane.output', string='دفعة الكسوة والتفصيل', required=True,
        domain="[('company_id', '=', company_id), ('lane', '=', 'cover'), ('final_product_id', '=', final_product_id), ('furniture_model_id', '=', furniture_model_id), ('bom_id', '=', bom_id), ('uom_id', '=', uom_id), ('state', '!=', 'exhausted')]",
    )
    available_body_qty = fields.Float(
        related='body_output_id.physical_available_qty', readonly=True,
        digits='Product Unit of Measure', string='النجارة المتاحة',
    )
    available_cover_qty = fields.Float(
        related='cover_output_id.physical_available_qty', readonly=True,
        digits='Product Unit of Measure', string='الكسوة المتاحة',
    )

    @api.onchange('final_product_id')
    def _onchange_final_product_id(self):
        for wizard in self:
            wizard.uom_id = wizard.final_product_id.uom_id
            wizard.body_output_id = False
            wizard.cover_output_id = False

    @api.onchange(
        'company_id', 'final_product_id', 'furniture_model_id', 'bom_id', 'uom_id',
    )
    def _onchange_matching_outputs(self):
        for wizard in self:
            if not all((
                wizard.company_id,
                wizard.final_product_id,
                wizard.furniture_model_id,
                wizard.uom_id,
            )):
                continue
            Output = self.env['furniture.mrp.lane.output']
            common_domain = [
                ('company_id', '=', wizard.company_id.id),
                ('final_product_id', '=', wizard.final_product_id.id),
                ('furniture_model_id', '=', wizard.furniture_model_id.id),
                ('bom_id', '=', wizard.bom_id.id),
                ('uom_id', '=', wizard.uom_id.id),
                ('state', '!=', 'exhausted'),
            ]
            body = Output.search(
                common_domain + [('lane', '=', 'body')],
                order='fifo_date, ready_at, id', limit=1,
            )
            cover = Output.search(
                common_domain + [('lane', '=', 'cover')],
                order='fifo_date, ready_at, id', limit=1,
            )
            wizard.body_output_id = body
            wizard.cover_output_id = cover
            if body and cover:
                wizard.quantity = min(
                    wizard.quantity or 1.0,
                    body.available_qty,
                    cover.available_qty,
                )

    @api.constrains(
        'company_id', 'final_product_id', 'furniture_model_id', 'bom_id', 'uom_id',
        'body_output_id', 'cover_output_id', 'quantity',
    )
    def _check_wizard_pair(self):
        for wizard in self:
            if wizard.quantity <= 0:
                raise ValidationError(_('كمية الإكمال يجب أن تكون أكبر من صفر.'))
            for output, lane in (
                (wizard.body_output_id, 'body'),
                (wizard.cover_output_id, 'cover'),
            ):
                if not output:
                    continue
                if (
                    output.lane != lane
                    or output.company_id != wizard.company_id
                    or output.final_product_id != wizard.final_product_id
                    or output.furniture_model_id != wizard.furniture_model_id
                    or output.bom_id != wizard.bom_id
                    or output.uom_id != wizard.uom_id
                ):
                    raise ValidationError(_('دفعة المسار المختارة لا تطابق بيانات المنتج.'))

    def _create_assembly(self):
        self.ensure_one()
        Assembly = self.env['furniture.mrp.final.assembly']
        Assembly._furniture_check_completion_access()
        return Assembly._furniture_internal_create([{
            'body_output_id': self.body_output_id.id,
            'cover_output_id': self.cover_output_id.id,
            'quantity': self.quantity,
        }])

    def _assembly_action(self, assembly):
        return {
            'type': 'ir.actions.act_window',
            'name': _('عملية إكمال المنتج'),
            'res_model': 'furniture.mrp.final.assembly',
            'res_id': assembly.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_reserve(self):
        self.ensure_one()
        assembly = self._create_assembly()
        assembly.action_reserve()
        return self._assembly_action(assembly)

    def action_complete(self):
        self.ensure_one()
        assembly = self._create_assembly()
        assembly.action_complete()
        return self._assembly_action(assembly)
