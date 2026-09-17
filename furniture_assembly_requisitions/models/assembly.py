from collections import defaultdict

from odoo import _, fields, models
from odoo.tools.float_utils import float_compare
from odoo.exceptions import UserError

from .requisition import SHARED_HALL_STAGES, STAGE_HALL_XMLIDS, STAGE_LABELS


class SharedHallProductBatch(models.Model):
    _inherit = 'furniture.mrp.stage.product.batch'

    def _preflight_execution_materials(self, production, lines):
        result = super()._preflight_execution_materials(production, lines)
        if self.stage_code in SHARED_HALL_STAGES:
            production._shared_hall_materials(
                self.stage_code, lines, consume=False,
            )
        return result

    def _prepare_execution_materials(self, production, lines):
        result = super()._prepare_execution_materials(production, lines)
        if self.stage_code in SHARED_HALL_STAGES:
            production._shared_hall_materials(
                self.stage_code, lines, consume=True,
            )
        return result

    def _start_batch(self):
        # Keep the hall balance check, consumption, and stage start atomic even
        # when a caller catches an inventory exception.
        with self.env.cr.savepoint():
            return super()._start_batch()


class SharedHallProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    def _shared_hall_location(self, stage_code):
        self.ensure_one()
        field_by_stage = {
            'priming': 'location_priming_wip_id',
            'painting': 'location_painting_wip_id',
            'carpentry': 'location_carpentry_wip_id',
            'bases': 'location_bases_wip_id',
            'finishing': 'location_finishing_wip_id',
            'tailoring': 'location_tailoring_wip_id',
            'upholstery': 'location_upholstery_wip_id',
            'packaging': 'location_packaging_id',
        }
        field_name = field_by_stage.get(stage_code)
        return self[field_name] if field_name else self.env['stock.location']

    def _shared_hall_materials(
        self, stage_code, lines, consume=False, material_lines=None,
    ):
        """Charge an exact stage recipe once from its department balance."""
        self.ensure_one()
        if stage_code not in SHARED_HALL_STAGES:
            raise UserError(_('المرحلة لا تستخدم رصيد صالة مشترك.'))
        self._stage_dashboard_check_stage_access(stage_code)
        if lines - self.production_line_ids:
            raise UserError(_('سطور التشغيل لا تخص هذا الأمر.'))
        self._ensure_stage_locations()
        hall = self._shared_hall_location(stage_code)
        if not hall:
            raise UserError(_(
                'صالة مرحلة %s غير معدّة على أمر الإنتاج.'
            ) % STAGE_LABELS.get(stage_code, stage_code))
        if material_lines is None:
            materials = self.material_line_ids.filtered(lambda row: (
                row.production_line_id in lines and row.stage == stage_code
            ))
        else:
            materials = material_lines
        materials = materials.filtered(lambda row: (
            row.product_id
            and row.qty_needed > 0
            and row.stage == stage_code
            and not self._material_line_already_consumed(row)
        ))
        covered_product_ids = set()
        release_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', self.id),
            ('stage_code', '=', stage_code),
            ('state', 'in', ('issued', 'started', 'completed')),
        ])
        for release_stage in release_stages:
            if (
                release_stage.source_production_line_ids
                and not (release_stage.source_production_line_ids & lines)
            ):
                continue
            covered_product_ids.update(
                int(product_id)
                for product_id, quantity in (
                    release_stage.external_covered_json or {}
                ).items()
                if quantity > 0
            )
        quantities = defaultdict(float)
        for row in materials:
            if row.product_id.id in covered_product_ids:
                quantity = self._quantity_in_product_uom(
                    row.product_id, row.qty_needed,
                    row.product_uom_id or row.product_id.uom_id,
                )
            else:
                quantity = self._material_line_required_stock_qty(row)
            quantities[row.product_id] += quantity
        Config = self.env['furniture.assembly.supply.config']
        Config._lock_and_check_stock(self.company_id, hall, quantities)
        if not consume or not materials:
            return self.env['stock.move']
        specs = []
        for product, qty in quantities.items():
            rows = materials.filtered(lambda row: row.product_id == product)
            specs.append({
                'source_location': hall,
                'dest_location': self._get_production_location(),
                'label': _('استهلاك خامات %s من رصيد الصالة') % STAGE_LABELS.get(
                    stage_code, stage_code,
                ),
                'product': product,
                'quantity': qty,
                'uom': product.uom_id,
                'material_lines': rows,
                'source_production_lines': rows.mapped('production_line_id'),
            })
        moves = self.env['stock.move']
        for spec, move in self._create_internal_moves_batch(specs):
            spec['material_lines'].sudo().write({'move_id': move.id})
            moves |= move
        return moves

    def _move_materials_to_stage_wip(
        self, stage_model, material_lines=False,
        allow_material_line_link_sudo=False,
    ):
        stage_code = self._stage_model_to_code(stage_model)
        if stage_code not in SHARED_HALL_STAGES:
            return super()._move_materials_to_stage_wip(
                stage_model,
                material_lines=material_lines,
                allow_material_line_link_sudo=allow_material_line_link_sudo,
            )
        materials = (
            material_lines
            if material_lines is not False
            else self._stage_material_lines(stage_model)
        )
        return self._shared_hall_materials(
            stage_code,
            materials.mapped('production_line_id'),
            consume=True,
            material_lines=materials,
        )


class SharedHallAdvanceRelease(models.Model):
    _inherit = 'furniture.mrp.advance.material.release'

    def _external_available_qty(
        self, company, stage_code, product, allocation_pool,
    ):
        key = ('assembly_external', company.id, stage_code, product.id)
        if key not in allocation_pool:
            hall_xmlid = STAGE_HALL_XMLIDS.get(stage_code)
            hall = self.env.ref(hall_xmlid, raise_if_not_found=False) if hall_xmlid else False
            if hall:
                self.env['furniture.mrp.material.reservation'].sudo()._lock_bucket(
                    company, hall, product,
                )
            moves = self.env['stock.move'].sudo().search([
                ('assembly_requisition_id.company_id', '=', company.id),
                ('assembly_requisition_id.stage_code', '=', stage_code),
                ('assembly_requisition_id.state', '=', 'done'),
                ('product_id', '=', product.id),
                ('state', '=', 'done'),
            ])
            supplied = sum(
                move.product_uom._compute_quantity(
                    move.quantity, product.uom_id, round=False,
                ) if move.product_uom.category_id == product.uom_id.category_id
                else move.quantity
                for move in moves
            )
            if hall:
                hall_qty = self.env['stock.quant'].sudo().with_company(
                    company
                )._get_available_quantity(product, hall, strict=True)
                # The physical hall balance is the hard ceiling.  This also
                # prevents pre-upgrade, already-consumed external receipts
                # from becoming available again merely because their historic
                # inbound moves still exist.
                supplied = min(supplied, max(hall_qty, 0.0))
            stages = self.env[
                'furniture.mrp.advance.material.release.stage'
            ].sudo().search([
                ('production_id.company_id', '=', company.id),
                ('stage_code', '=', stage_code),
                ('state', 'not in', ('rejected', 'returned', 'cancelled')),
            ])
            allocated = sum(
                float((stage.external_covered_json or {}).get(str(product.id), 0.0))
                for stage in stages
            )
            allocation_pool[key] = max(supplied - allocated, 0.0)
        return key, allocation_pool[key]

    def _apply_external_coverage(
        self, payload, production, availability_pool,
    ):
        stage_code = payload.get('stage_code')
        if stage_code not in SHARED_HALL_STAGES:
            return payload
        allocation_pool = availability_pool if availability_pool is not None else {}
        source_location = (
            production.location_src_id
            or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        )
        kept = []
        covered = {}
        for command in payload.get('material_line_ids', []):
            detail = dict(command[2])
            product = self.env['product.product'].browse(detail['product_id'])
            external_key, external_available = self._external_available_qty(
                production.company_id, stage_code, product, allocation_pool,
            )
            full_qty = detail['requested_qty']
            cover_qty = min(full_qty, external_available)
            remaining_qty = max(full_qty - cover_qty, 0.0)
            allocation_pool[external_key] = max(external_available - cover_qty, 0.0)
            if float_compare(cover_qty, 0.0, precision_digits=3) > 0:
                covered[str(product.id)] = cover_qty
            availability_key = (
                production.company_id.id,
                source_location.id if source_location else False,
                product.id,
            )
            if availability_key in allocation_pool:
                source_before = (
                    allocation_pool[availability_key]
                    + min(max(detail.get('available_qty', 0.0), 0.0), full_qty)
                )
                detail['available_qty'] = min(source_before, remaining_qty)
                allocation_pool[availability_key] = max(
                    source_before - remaining_qty, 0.0,
                )
            if float_compare(remaining_qty, 0.0, precision_digits=3) > 0:
                detail['requested_qty'] = remaining_qty
                kept.append((command[0], command[1], detail))
        payload['material_line_ids'] = kept
        payload['external_covered_json'] = covered
        return payload

    def _snapshot_payloads(
        self, production, stage_codes, availability_pool=None,
    ):
        pool = availability_pool if availability_pool is not None else {}
        payloads = []
        for sequence, stage_code in enumerate(stage_codes, start=1):
            stage_payloads = super()._snapshot_payloads(
                production, [stage_code], availability_pool=pool,
            )
            payload = self._apply_external_coverage(
                stage_payloads[0], production, pool,
            )
            payload['sequence'] = sequence * 10
            payloads.append(payload)
        return payloads

    def _scoped_stage_snapshot_payload(
        self, production, stage_code, production_lines,
        availability_pool=None, sequence=10,
    ):
        pool = availability_pool if availability_pool is not None else {}
        payload = super()._scoped_stage_snapshot_payload(
            production, stage_code, production_lines,
            availability_pool=pool, sequence=sequence,
        )
        return self._apply_external_coverage(payload, production, pool)


class SharedHallAdvanceReleaseStage(models.Model):
    _inherit = 'furniture.mrp.advance.material.release.stage'

    external_covered_json = fields.Json(
        string='كميات مغطاة من الطلب الخارجي', readonly=True,
        copy=False, default=dict,
    )

    def _current_buckets(self):
        self.ensure_one()
        buckets = super()._current_buckets()
        if self.stage_code not in SHARED_HALL_STAGES:
            return buckets
        covered = self.external_covered_json or {}
        result = {}
        for key, bucket in buckets.items():
            adjusted = dict(bucket)
            adjusted['requested_qty'] = max(
                bucket['requested_qty']
                - float(covered.get(str(bucket['product'].id), 0.0)),
                0.0,
            )
            if float_compare(
                adjusted['requested_qty'], 0.0, precision_digits=3,
            ) > 0:
                result[key] = adjusted
        return result


class SharedHallStoreRequest(models.Model):
    _inherit = 'furniture.mrp.store.request'

    def _create_pending_request(self, vals, material_buckets):
        stage_code = vals.get('stage_code')
        return super()._create_pending_request(vals, material_buckets)
