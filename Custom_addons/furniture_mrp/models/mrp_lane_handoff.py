# -*- coding: utf-8 -*-
"""FIFO hand-offs between the independent furniture production orders.

The historical four-buffer final assembly remains untouched for audit.  New
orders follow the physical factory route instead:

    frame -> finish
    finish + tailoring -> upholstery
    upholstery + painting -> packaging -> finished goods

Each arrow is backed by an assigned stock move and an immutable ledger row.
The move is completed only when the destination-stage supervisor accepts its
durable alert; downstream work remains blocked until that explicit receipt.
"""

from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .mrp_lane_flow import (
    PRODUCTION_OUTPUT_LANE_SELECTION,
    _lane_route_values,
)


HANDOFF_REQUIRED_LANES = {
    'finish': ('frame',),
    'upholstery': ('finish', 'tailoring'),
    'packaging': ('upholstery', 'painting'),
}

HANDOFF_TARGET_STAGE_BY_LANE = {
    'finish': ('bases', 'القواعد والتجهيز'),
    'upholstery': ('upholstery', 'الكسوة'),
    'packaging': ('packaging', 'التغليف'),
}

HANDOFF_STATE_SELECTION = [
    ('reserved', 'محجوز للمرحلة التالية'),
    ('consumed', 'تم التحويل والاستهلاك'),
    ('cancelled', 'ملغي'),
]

HANDOFF_MOVE_FIELDS = frozenset({
    'furniture_lane_handoff_id',
    'furniture_lane_handoff_role',
})

HANDOFF_MOVE_IDENTITY_FIELDS = HANDOFF_MOVE_FIELDS | frozenset({
    'origin',
    'product_id',
    'product_uom',
    'product_uom_qty',
    'location_id',
    'location_dest_id',
    'company_id',
    'furniture_source_production_line_id',
    'furniture_source_production_line_ids',
})

HANDOFF_OUTPUT_IMMUTABLE_FIELDS = frozenset({
    'company_id',
    'lane',
    'production_id',
    'production_line_id',
    'final_product_id',
    'wip_product_id',
    'furniture_model_id',
    'uom_id',
    'source_location_id',
    'origin_receipt_move_id',
    'ready_move_id',
    'qty_ready',
    'unit_cost',
    'ready_at',
})

HANDOFF_LINE_IDENTITY_FIELDS = frozenset({
    'active',
    'production_id',
    'product_id',
    'product_qty',
    'product_uom_id',
    'bom_id',
    'furniture_order_model_id',
    'width_cm',
    'depth_cm',
    'height_cm',
})

HANDOFF_PRODUCTION_IDENTITY_FIELDS = frozenset({
    'name',
    'company_id',
    'production_lane',
    'product_id',
    'product_qty',
    'product_uom_id',
    'bom_id',
    'furniture_order_model_id',
    'width_cm',
    'depth_cm',
    'height_cm',
})

HANDOFF_STAGE_MODELS = {
    'furniture.mrp.bases': 'finish',
    'furniture.mrp.upholstery': 'upholstery',
    'furniture.mrp.packaging': 'packaging',
}


def _furniture_value_changes(record, field_name, new_value):
    """Compare an ORM write value without treating idempotent retries as edits."""
    field = record._fields[field_name]
    current = record[field_name]
    if field.type == 'many2one':
        target_id = (
            new_value.id
            if getattr(new_value, '_name', False)
            else int(new_value or 0)
        )
        return current.id != target_id
    if field.type in ('many2many', 'one2many'):
        # Command lists are intentionally not normalised here: identity
        # x2manys on linked stock moves have no legitimate mutable operation.
        return True
    if field.type in ('float', 'monetary'):
        return float_compare(
            current or 0.0,
            float(new_value or 0.0),
            precision_digits=6,
        ) != 0
    if field.type == 'datetime':
        return fields.Datetime.to_datetime(current) != fields.Datetime.to_datetime(
            new_value,
        )
    if field.type == 'date':
        return fields.Date.to_date(current) != fields.Date.to_date(new_value)
    return current != new_value


class FurnitureMrpProductionLaneHandoff(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _furniture_lane_handoff_required_lanes(self):
        """Registry-local source roles for each downstream production route."""
        return dict(HANDOFF_REQUIRED_LANES)

    @api.model
    def _furniture_lane_handoff_target_stages(self):
        return dict(HANDOFF_TARGET_STAGE_BY_LANE)

    @api.model
    def _furniture_lane_handoff_stage_models(self):
        """A stage model may start more than one independent production route."""
        return {
            model_name: (lane,)
            for model_name, lane in HANDOFF_STAGE_MODELS.items()
        }

    upstream_handoff_ids = fields.One2many(
        'furniture.mrp.lane.handoff',
        'downstream_production_id',
        string='تحويلات المراحل السابقة',
        readonly=True,
    )
    handoff_generated = fields.Boolean(
        string='أمر مولد من تدفق المراحل',
        default=False,
        readonly=True,
        copy=False,
        index=True,
    )
    handoff_notification_sent_at = fields.Datetime(
        string='تاريخ تنبيه التحويل',
        readonly=True,
        copy=False,
    )
    handoff_notification_dismissed_user_ids = fields.Many2many(
        'res.users',
        'furniture_mrp_handoff_dismissed_user_rel',
        'production_id',
        'user_id',
        string='مستخدمون أغلقوا تنبيه التحويل',
        readonly=True,
        copy=False,
    )
    handoff_accepted_by_id = fields.Many2one(
        'res.users',
        string='قبل التحويل',
        readonly=True,
        copy=False,
    )
    handoff_accepted_at = fields.Datetime(
        string='تاريخ قبول التحويل',
        readonly=True,
        copy=False,
    )

    def _furniture_auto_confirm_generated_handoff_order(self):
        """Confirm a system-generated handoff order in the active test window."""
        self.ensure_one()
        if not self.handoff_generated or self.state != 'draft':
            return self
        parameters = self.env['ir.config_parameter'].sudo()
        if parameters.get_param(
            'furniture_stage_replenishment.auto_confirm_enabled'
        ) != 'True':
            return self
        auto_confirm_until = fields.Datetime.to_datetime(
            parameters.get_param(
                'furniture_stage_replenishment.auto_confirm_until'
            )
        )
        if not auto_confirm_until or fields.Datetime.now() >= auto_confirm_until:
            return self
        self.sudo().action_confirm()
        return self
    handoff_transfer_pending = fields.Boolean(
        string='تحويل مرحلة بانتظار القبول',
        compute='_compute_handoff_acceptance_status',
    )
    handoff_can_current_user_accept = fields.Boolean(
        string='يمكن للمستخدم قبول التحويل',
        compute='_compute_handoff_acceptance_status',
    )

    @api.depends(
        'upstream_handoff_ids.state',
        'stage_transfer_handoff_ids.state',
    )
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_handoff_acceptance_status(self):
        current_user = self.env.user
        for production in self:
            pending_lane = production.upstream_handoff_ids.filtered(
                lambda row: row.state == 'reserved'
            )
            pending_stage = production.sudo(
            ).stage_transfer_handoff_ids.filtered(
                lambda row: row.state == 'pending'
            )
            production.handoff_transfer_pending = bool(
                pending_lane or pending_stage
            )
            production.handoff_can_current_user_accept = bool(
                (
                    pending_lane
                    and current_user
                    in production._furniture_handoff_supervisor_users()
                )
                or any(
                    current_user in handoff._supervisor_users()
                    for handoff in pending_stage
                )
            )

    def _furniture_handoff_target_stage(self):
        self.ensure_one()
        return self._furniture_lane_handoff_target_stages().get(
            self.production_lane,
            (False, False),
        )

    def _furniture_stage_supervisor_users(self, stage_code):
        """Exact stage supervisors, with a same-company manager safety net."""
        self.ensure_one()
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        if not stage_code:
            return Users
        employees = self.env['hr.employee'].sudo().with_context(
            active_test=False,
        ).search([
            ('active', '=', True),
            ('furniture_mrp_role', '=', 'supervisor'),
            ('furniture_mrp_supervisor_stage_ids.code', '=', stage_code),
            ('user_id', '!=', False),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', self.company_id.id),
        ])
        users = employees.mapped('user_id').filtered(lambda user: (
            user.active
            and not user.share
            and self.company_id in user.company_ids
        ))
        if users:
            return users

        # Never deadlock a physical transfer merely because a supervisor
        # employee has not yet been linked to a login.  In that exceptional
        # case only, production managers of the same company can accept it.
        manager_group = self.env.ref(
            'furniture_mrp.group_furniture_mrp_manager',
            raise_if_not_found=False,
        )
        return (
            manager_group.sudo().users.filtered(lambda user: (
                user.active
                and not user.share
                and self.company_id in user.company_ids
            ))
            if manager_group else Users
        )

    def _furniture_handoff_supervisor_users(self):
        """Exact destination-stage supervisors for a lane handoff."""
        self.ensure_one()
        stage_code, _stage_label = self._furniture_handoff_target_stage()
        return self._furniture_stage_supervisor_users(stage_code)

    def _furniture_handoff_notification_payload(self):
        self.ensure_one()
        stage_code, stage_label = self._furniture_handoff_target_stage()
        handoffs = self.upstream_handoff_ids.filtered(
            lambda row: row.state == 'reserved'
        )
        source_labels = '، '.join(sorted({
            self._furniture_lane_labels().get(row.role, row.role)
            for row in handoffs
        }))
        product_rows = []
        for line in self._furniture_handoff_lines():
            product = self._furniture_line_final_product(line)
            product_rows.append('%s × %s' % (
                product.display_name,
                self._format_dimension_value(line.product_qty),
            ))
        product_summary = '، '.join(product_rows)
        return {
            'title': _('تحويل منتظر إلى %s') % stage_label,
            'message': _(
                'هيتحول لك إلى مرحلة %(stage)s من %(sources)s: %(products)s. '
                'لن تتحرك الكميات قبل الضغط على «قبول التحويل». '
                'إغلاق التنبيه يخفيه فقط ولا ينفذ التحويل.'
            ) % {
                'stage': stage_label,
                'sources': source_labels or _('المرحلة السابقة'),
                'products': product_summary or self.product_id.display_name,
            },
            'type': 'warning',
            'sticky': True,
            'refresh_model': self._name,
            'refresh_res_id': self.id,
            'play_sound': True,
            'handoff_event': 'pending',
            'handoff_kind': 'lane',
            'handoff_notification_key': 'lane:%s' % self.id,
            'handoff_production_id': self.id,
            'handoff_stage_code': stage_code,
            'handoff_stage_label': stage_label,
        }

    def _furniture_notify_pending_handoff(self):
        self.ensure_one()
        if (
            self.handoff_notification_sent_at
            or not self.upstream_handoff_ids.filtered(
                lambda row: row.state == 'reserved'
            )
            or not self._furniture_handoffs_cover_lines()
        ):
            return False
        users = self._furniture_handoff_supervisor_users()
        if not users:
            return False
        payload = self._furniture_handoff_notification_payload()
        self.env['furniture.mrp.store.request']._send_bus_notification(
            users,
            payload['title'],
            payload['message'],
            notification_type=payload['type'],
            sticky=True,
            refresh_model=self._name,
            refresh_res_id=self.id,
            play_sound=True,
            extra_payload={
                key: value
                for key, value in payload.items()
                if key.startswith('handoff_')
            },
        )
        self.sudo().write({
            'handoff_notification_sent_at': fields.Datetime.now(),
            'handoff_notification_dismissed_user_ids': [Command.clear()],
        })
        return True

    def _furniture_check_handoff_acceptance_access(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if user not in self._furniture_handoff_supervisor_users():
            _stage_code, stage_label = self._furniture_handoff_target_stage()
            raise AccessError(_(
                'قبول التحويل متاح فقط لمشرف مرحلة %s.'
            ) % stage_label)
        return True

    @api.model
    def furniture_pending_handoff_notifications(self):
        """Return durable sticky alerts for the signed-in supervisor only."""
        current_user = self.env.user
        candidates = self.sudo().search([
            ('company_id', 'in', current_user.company_ids.ids),
            ('upstream_handoff_ids.state', '=', 'reserved'),
            ('state', 'not in', ('done', 'cancelled')),
        ], order='create_date, id')
        payloads = []
        for production in candidates:
            if (
                current_user not in production._furniture_handoff_supervisor_users()
                or current_user in production.handoff_notification_dismissed_user_ids
            ):
                continue
            payloads.append(production._furniture_handoff_notification_payload())
        stage_handoffs = self.env[
            'furniture.mrp.stage.transfer.handoff'
        ].sudo().search([
            ('company_id', 'in', current_user.company_ids.ids),
            ('state', '=', 'pending'),
            ('production_id.state', 'not in', ('done', 'cancelled')),
        ], order='id')
        for handoff in stage_handoffs:
            if (
                current_user not in handoff._supervisor_users()
                or current_user in handoff.notification_dismissed_user_ids
            ):
                continue
            payloads.append(handoff._notification_payload())
        return payloads

    @api.model
    def _furniture_stage_dashboard_pending_handoffs(self, stage_code):
        """Return acceptable transfers even after their toast was dismissed."""
        if not stage_code:
            return []
        current_user = self.env.user
        allowed_company_ids = current_user.company_ids.ids
        payloads = []

        stage_handoffs = self.env[
            'furniture.mrp.stage.transfer.handoff'
        ].sudo().search([
            ('company_id', 'in', allowed_company_ids),
            ('target_stage', '=', stage_code),
            ('state', '=', 'pending'),
            ('production_id.state', 'not in', ('done', 'cancelled')),
        ], order='id')
        for handoff in stage_handoffs:
            if not handoff.production_id.with_env(self.env)._visible_for_stage(stage_code):
                continue
            if current_user not in handoff._supervisor_users():
                continue
            notification = handoff._notification_payload()
            payloads.append({
                'key': notification['handoff_notification_key'],
                'kind': 'stage',
                'handoff_id': handoff.id,
                'production_id': handoff.production_id.id,
                'title': notification['title'],
                'message': notification['message'],
            })

        lane_candidates = self.sudo().search([
            ('company_id', 'in', allowed_company_ids),
            ('upstream_handoff_ids.state', '=', 'reserved'),
            ('state', 'not in', ('done', 'cancelled')),
        ], order='id')
        for production in lane_candidates:
            if not production.with_env(self.env)._visible_for_stage(stage_code):
                continue
            target_stage, _target_label = (
                production._furniture_handoff_target_stage()
            )
            if (
                target_stage != stage_code
                or current_user
                not in production._furniture_handoff_supervisor_users()
                or not production._furniture_handoffs_cover_lines()
            ):
                continue
            notification = production._furniture_handoff_notification_payload()
            payloads.append({
                'key': notification['handoff_notification_key'],
                'kind': 'lane',
                'handoff_id': False,
                'production_id': production.id,
                'title': notification['title'],
                'message': notification['message'],
            })
        return payloads

    @api.model
    def get_stage_dashboard_data(
        self, stage_code=False, date_from=False, date_to=False,
    ):
        result = super().get_stage_dashboard_data(
            stage_code=stage_code,
            date_from=date_from,
            date_to=date_to,
        )
        selected_stage = result.get('selected_stage')
        result['pending_handoffs'] = (
            self._furniture_stage_dashboard_pending_handoffs(selected_stage)
        )
        return result

    def action_dismiss_handoff_notification(self, stage_handoff_id=False):
        current_user = self.env.user
        if stage_handoff_id:
            handoff = self.env[
                'furniture.mrp.stage.transfer.handoff'
            ].sudo().browse(stage_handoff_id).exists()
            if (
                len(self) != 1
                or not handoff
                or handoff.production_id.id != self.id
            ):
                raise AccessError(_('التحويلة المطلوبة لا تخص أمر الإنتاج.'))
            return handoff._dismiss_for_user(current_user)
        for production in self.sudo().exists():
            production._furniture_check_handoff_acceptance_access(current_user)
            if production.upstream_handoff_ids.filtered(
                lambda row: row.state == 'reserved'
            ):
                production.write({
                    'handoff_notification_dismissed_user_ids': [
                        Command.link(current_user.id),
                    ],
                })
        return True

    def action_accept_handoff_transfer(self, stage_handoff_id=False):
        current_user = self.env.user
        if stage_handoff_id:
            handoff = self.env[
                'furniture.mrp.stage.transfer.handoff'
            ].sudo().browse(stage_handoff_id).exists()
            if (
                len(self) != 1
                or not handoff
                or handoff.production_id.id != self.id
            ):
                raise AccessError(_('التحويلة المطلوبة لا تخص أمر الإنتاج.'))
            accepted_handoffs = handoff._accept(current_user)
            if accepted_handoffs:
                handoff.production_id._furniture_notify_stage_acceptance_warning(
                    handoff.target_stage,
                    current_user,
                )
            return {
                'status': 'accepted',
                'title': _('تم قبول التحويل'),
                'message': _(
                    'تم تنفيذ حركة المخزون، ويمكن بدء المرحلة الآن.'
                ),
            }
        for source_production in self:
            production = source_production.sudo()
            production._furniture_check_handoff_acceptance_access(current_user)
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production WHERE id = %s '
                'FOR UPDATE',
                [production.id],
            )
            production.invalidate_recordset([
                'upstream_handoff_ids',
                'handoff_accepted_by_id',
                'handoff_accepted_at',
            ])
            reserved = production.upstream_handoff_ids.filtered(
                lambda row: row.state == 'reserved'
            )
            if not reserved:
                if production.upstream_handoff_ids.filtered(
                    lambda row: row.state == 'consumed'
                ):
                    continue
                raise UserError(_('لا توجد تحويلة معلقة لهذا الأمر.'))
            if not production._furniture_handoffs_cover_lines():
                raise UserError(_(
                    'التحويلة لم تكتمل لكل أصناف ومدخلات أمر المرحلة بعد.'
                ))
            production._furniture_consume_required_handoffs()
            accepted_at = fields.Datetime.now()
            production.write({
                'handoff_accepted_by_id': current_user.id,
                'handoff_accepted_at': accepted_at,
            })
            _stage_code, stage_label = production._furniture_handoff_target_stage()
            message = _(
                'قبل %(user)s التحويل إلى %(stage)s للأمر %(order)s. '
                'تم تنفيذ حركة المخزون وأصبح الأمر جاهزًا لخطوات بدء المرحلة.'
            ) % {
                'user': current_user.display_name,
                'stage': stage_label,
                'order': production.name,
            }
            production.message_post(body='✅ %s' % message)
            users = (
                production._furniture_handoff_supervisor_users()
                | current_user.sudo()
            )
            self.env['furniture.mrp.store.request']._send_bus_notification(
                users,
                _('تم قبول تحويل المرحلة'),
                message,
                notification_type='success',
                refresh_model=production._name,
                refresh_res_id=production.id,
                extra_payload={
                    'handoff_event': 'resolved',
                    'handoff_kind': 'lane',
                    'handoff_notification_key': 'lane:%s' % production.id,
                    'handoff_production_id': production.id,
                },
            )
            production._furniture_notify_stage_acceptance_warning(
                _stage_code,
                current_user,
            )
        return {
            'status': 'accepted',
            'title': _('تم قبول التحويل'),
            'message': _('تم تنفيذ حركة المخزون، ويمكن بدء المرحلة الآن.'),
        }

    @api.model
    def _furniture_upholstery_hall(self):
        return self.env.ref(
            'furniture_mrp.location_stage_upholstery_wip',
            raise_if_not_found=False,
        )

    def _furniture_normalize_upholstery_hall(self):
        """Keep every live upholstery pointer on the canonical factory hall."""
        hall = self._furniture_upholstery_hall()
        upholstery_orders = self.sudo().exists().filtered(
            lambda production: production.production_lane == 'upholstery'
        )
        if not hall or not upholstery_orders:
            return hall
        dirty = upholstery_orders.filtered(lambda production: (
            production.location_upholstery_wip_id != hall
            or production.location_upholstery_id != hall
        ))
        if dirty:
            # The caller has already passed create/write or stage-operation
            # access.  Elevate only the two technical location pointers so a
            # supervisor cannot be blocked by historic location ACLs.
            super(
                FurnitureMrpProductionLaneHandoff,
                dirty.with_context(
                    furniture_skip_material_refresh=True,
                    furniture_skip_stage_plan_sync=True,
                ),
            ).write({
                'location_upholstery_wip_id': hall.id,
                'location_upholstery_id': hall.id,
            })
        return hall

    @api.model_create_multi
    def create(self, vals_list):
        upholstery_hall = self._furniture_upholstery_hall()
        prepared = []
        for source_vals in vals_list:
            vals = dict(source_vals)
            if vals.get('production_lane') == 'upholstery' and upholstery_hall:
                # The upholstery hall is the only live location for new work.
                # The historical store remains archived/readable for old moves.
                vals['location_upholstery_wip_id'] = upholstery_hall.id
                vals['location_upholstery_id'] = upholstery_hall.id
            prepared.append(vals)
        records = super().create(prepared)
        records._furniture_normalize_upholstery_hall()
        return records

    def write(self, vals):
        vals = dict(vals)
        if vals.get('state') == 'done' and self.ids:
            reserved_inputs = self.env[
                'furniture.mrp.lane.handoff'
            ].sudo().search_count([
                ('downstream_production_id', 'in', self.ids),
                ('state', '=', 'reserved'),
            ])
            if reserved_inputs:
                raise UserError(_(
                    'لا يمكن إنهاء أمر مرحلة وما زالت تحويلات المراحل السابقة '
                    'محجوزة ولم تُستهلك.'
                ))
        candidate_fields = HANDOFF_PRODUCTION_IDENTITY_FIELDS & set(vals)
        if candidate_fields:
            active_handoffs = self.env[
                'furniture.mrp.lane.handoff'
            ].sudo().search([
                ('state', 'in', ('reserved', 'consumed')),
                '|',
                ('downstream_production_id', 'in', self.ids),
                ('output_id.production_id', 'in', self.ids),
            ])
            protected_ids = set(
                active_handoffs.mapped('downstream_production_id').ids
            ) | set(active_handoffs.mapped('output_id.production_id').ids)
            protected = self.filtered(
                lambda production: production.id in protected_ids
            )
            changed = protected.filtered(lambda production: any(
                _furniture_value_changes(
                    production,
                    field_name,
                    vals[field_name],
                )
                for field_name in candidate_fields
            ))
            if changed:
                raise UserError(_(
                    'لا يمكن تغيير هوية أمر إنتاج مرتبط بتحويلات مراحل نشطة.'
                ))
        upholstery_records = self.filtered(
            lambda production: (
                vals.get('production_lane', production.production_lane)
                == 'upholstery'
            )
        )
        other_records = self - upholstery_records
        result = True
        if upholstery_records:
            upholstery_vals = dict(vals)
            upholstery_hall = self._furniture_upholstery_hall()
            if upholstery_hall:
                upholstery_vals.update({
                    'location_upholstery_wip_id': upholstery_hall.id,
                    'location_upholstery_id': upholstery_hall.id,
                })
            result = super(
                FurnitureMrpProductionLaneHandoff,
                upholstery_records,
            ).write(upholstery_vals) and result
        if other_records:
            result = super(
                FurnitureMrpProductionLaneHandoff,
                other_records,
            ).write(vals) and result
        self._furniture_normalize_upholstery_hall()
        return result

    def _stage_storage_location(self, stage_code):
        self.ensure_one()
        if self.production_lane == 'upholstery' and stage_code == 'upholstery':
            return (
                self._furniture_normalize_upholstery_hall()
                or self.location_upholstery_wip_id
            )
        return super()._stage_storage_location(stage_code)

    def _ensure_stage_locations(self):
        result = super()._ensure_stage_locations()
        self._furniture_normalize_upholstery_hall()
        return result

    def _is_material_only_stage(self, stage_code):
        self.ensure_one()
        if self.production_lane == 'painting' and stage_code == 'painting':
            # Painting is an independent, countable Min/Max buffer.  Legacy
            # orders keep the historical material-only behaviour.
            return False
        return super()._is_material_only_stage(stage_code)

    def _lane_transform_output_specs(self, specs, ensure_storable=True):
        self.ensure_one()
        if self.production_lane == 'packaging':
            # Packaging creates the saleable product in its hall.  Quality
            # acceptance then transfers that exact product to finished goods.
            return specs
        return super()._lane_transform_output_specs(
            specs,
            ensure_storable=ensure_storable,
        )

    def _stage_storage_location_for_handoff(self):
        self.ensure_one()
        if self.production_lane == 'upholstery':
            self._furniture_normalize_upholstery_hall()
        production_location = self._get_production_location()
        if not production_location:
            raise UserError(_(
                'لا يوجد موقع إنتاج لتنفيذ تحويلات المراحل.'
            ))
        return production_location

    def _furniture_handoff_required_lanes(self):
        self.ensure_one()
        return self._furniture_lane_handoff_required_lanes().get(self.production_lane, ())

    def _furniture_line_final_product(self, line):
        self.ensure_one()
        return (
            self._get_or_create_dimensioned_finished_product_for_line(line)
            or line.product_id
        )

    def _furniture_handoff_lines(self, production_lines=False):
        """Return an exact, validated downstream batch selection."""
        self.ensure_one()
        Line = self.env['furniture.mrp.production.line']
        if production_lines:
            lines = (
                production_lines
                if getattr(production_lines, '_name', False) == Line._name
                else Line.browse(production_lines)
            ).exists()
            if lines.filtered(lambda line: line.production_id != self):
                raise ValidationError(_(
                    'لا يمكن حجز تحويلة لسطر لا يتبع أمر المرحلة.'
                ))
        else:
            lines = self.production_line_ids
        return lines.filtered(lambda line: (
            line.active
            and line.product_id
            and float_compare(
                line.product_qty or 0.0,
                0.0,
                precision_digits=3,
            ) > 0
        )).sorted(lambda line: (line.sequence, line.id))

    def _furniture_line_handoff_dimensions(self, line):
        """Return effective dimensions, retaining each custom override."""
        self.ensure_one()
        actual, bom = self._get_production_line_dimension_values(line)
        return tuple(
            actual_value
            if float_compare(actual_value or 0.0, 0.0, precision_digits=3)
            else (bom_value or 0.0)
            for actual_value, bom_value in zip(actual, bom)
        )

    def _furniture_handoff_dimensions_match(self, output, line):
        self.ensure_one()
        source_dimensions = output._furniture_handoff_dimensions()
        target_dimensions = self._furniture_line_handoff_dimensions(line)
        return all(
            float_compare(source, target, precision_digits=3) == 0
            for source, target in zip(source_dimensions, target_dimensions)
        )

    def _furniture_sync_handoff_inherited_costs(self, production_lines=False):
        """Rebuild cumulative upstream cost on downstream lines from scratch.

        Recomputing instead of incrementing makes manual/retried reservation
        idempotent and correctly weights FIFO fragments from multiple outputs.
        """
        self.ensure_one()
        lines = self._furniture_handoff_lines(production_lines)
        if not lines:
            return True
        Handoff = self.env['furniture.mrp.lane.handoff'].sudo()
        handoffs = Handoff.search([
            ('downstream_production_id', '=', self.id),
            ('downstream_line_id', 'in', lines.ids),
            ('state', 'in', ('reserved', 'consumed')),
        ], order='downstream_line_id, id')
        for line in lines:
            line_handoffs = handoffs.filtered(
                lambda handoff: handoff.downstream_line_id.id == line.id
            )
            material_total = 0.0
            labor_total = 0.0
            for handoff in line_handoffs:
                output = handoff.output_id
                source_line = output.production_line_id
                snapshot = output.production_id._get_production_line_cost_snapshot(
                    source_line,
                )
                material_unit = snapshot.get('material_unit') or 0.0
                labor_unit = snapshot.get('labor_unit') or 0.0
                if not snapshot.get('has_cost') and output.unit_cost:
                    # Older output rows stored only the cumulative total.  It
                    # remains cost-safe to carry that total as material rather
                    # than silently losing it or counting it twice.
                    material_unit = output.unit_cost
                    labor_unit = 0.0
                material_total += material_unit * handoff.quantity
                labor_total += labor_unit * handoff.quantity
            final_product = self._furniture_line_final_product(line)
            required_qty = self._quantity_in_product_uom(
                final_product,
                line.product_qty,
                line.product_uom_id or final_product.uom_id,
            )
            values = {
                'inherited_material_cost_per_unit': (
                    material_total / required_qty if required_qty else 0.0
                ),
                'inherited_labor_cost_per_unit': (
                    labor_total / required_qty if required_qty else 0.0
                ),
            }
            if any(float_compare(
                line[field_name] or 0.0,
                value,
                precision_digits=4,
            ) != 0 for field_name, value in values.items()):
                line.sudo().with_context(
                    furniture_skip_line_consolidation=True,
                    furniture_skip_material_refresh=True,
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_mps_replan=True,
                ).write(values)
        return True

    def _get_equivalent_production_line_groups(self):
        """Never consolidate a technical identity already in the ledger."""
        groups = super()._get_equivalent_production_line_groups()
        safe_groups = []
        for group in groups:
            protected = self.env[
                'furniture.mrp.lane.handoff'
            ].sudo().search_count([
                ('state', 'in', ('reserved', 'consumed')),
                '|',
                ('downstream_line_id', 'in', group.ids),
                ('output_id.production_line_id', 'in', group.ids),
            ])
            if not protected:
                safe_groups.append(group)
        return safe_groups

    def _furniture_handoff_output_domain(self, line, lane):
        self.ensure_one()
        final_product = self._furniture_line_final_product(line)
        return [
            ('company_id', '=', self.company_id.id),
            ('lane', '=', lane),
            ('final_product_id', '=', final_product.id),
            ('furniture_model_id', '=', (
                line.furniture_order_model_id or self.furniture_order_model_id
            ).id),
            ('bom_id', '=', (line.bom_id or self.bom_id).id),
            ('state', '!=', 'exhausted'),
        ]

    def _furniture_reserve_required_handoffs(
        self,
        source_outputs_by_lane=None,
        production_lines=False,
    ):
        """Reserve exact upstream WIP without consuming it."""
        self.ensure_one()
        required_lanes = self._furniture_handoff_required_lanes()
        if not required_lanes:
            return self.env['furniture.mrp.lane.handoff']
        if self.state not in ('draft', 'confirmed', 'in_production'):
            raise UserError(_('حالة أمر الإنتاج لا تسمح بحجز تحويلة.'))

        source_outputs_by_lane = source_outputs_by_lane or {}
        Output = self.env['furniture.mrp.lane.output'].sudo()
        Handoff = self.env['furniture.mrp.lane.handoff'].sudo()
        all_handoffs = Handoff
        destination = self._stage_storage_location_for_handoff()
        lines = self._furniture_handoff_lines(production_lines)
        if not lines:
            return all_handoffs

        # Serialise reservations for the same downstream order/lines.  Output
        # rows are locked below as the second half of the concurrency barrier.
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production_line '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(lines.ids)],
        )
        self.invalidate_recordset(['upstream_handoff_ids'])
        lines.invalidate_recordset(['downstream_handoff_ids'])

        for line in lines:
            final_product = self._furniture_line_final_product(line)
            required_qty = self._quantity_in_product_uom(
                final_product,
                line.product_qty,
                line.product_uom_id or final_product.uom_id,
            )
            for lane in required_lanes:
                existing = self.upstream_handoff_ids.filtered(lambda handoff: (
                    handoff.downstream_line_id == line
                    and handoff.role == lane
                    and handoff.state != 'cancelled'
                )).sudo()
                existing_qty = sum(existing.mapped('quantity'))
                missing = max(required_qty - existing_qty, 0.0)
                if float_compare(
                    missing,
                    0.0,
                    precision_rounding=final_product.uom_id.rounding or 0.001,
                ) <= 0:
                    all_handoffs |= existing
                    continue

                supplied = source_outputs_by_lane.get(lane)
                candidates = (
                    supplied.sudo().exists()
                    if supplied else
                    Output.search(
                        self._furniture_handoff_output_domain(line, lane),
                        order='fifo_date, ready_at, id',
                    )
                )
                candidates = candidates.filtered(lambda output: (
                    output.lane == lane
                    and output.final_product_id == final_product
                    and output.company_id == self.company_id
                    and output.furniture_model_id
                    == (line.furniture_order_model_id or self.furniture_order_model_id)
                    and output.bom_id == (line.bom_id or self.bom_id)
                    and self._furniture_handoff_dimensions_match(output, line)
                ))
                if not candidates:
                    raise UserError(_(
                        'لا يوجد رصيد جاهز مطابق من %(lane)s '
                        'للصنف %(product)s.'
                    ) % {
                        'lane': self._furniture_lane_labels().get(lane, lane),
                        'product': final_product.display_name,
                    })

                fifo_group = candidates._furniture_fifo_group_outputs()
                fifo_group._furniture_lock_rows()
                fifo_group._furniture_refresh_matching_groups()
                candidates.invalidate_recordset([
                    'handoff_ids', 'reserved_qty', 'assembled_qty',
                    'available_qty', 'physical_available_qty', 'state',
                ])
                candidates = candidates.sorted(lambda output: (
                    output.fifo_date or output.ready_at or fields.Datetime.now(),
                    output.ready_at or output.fifo_date or fields.Datetime.now(),
                    output.id,
                ))
                available_total = sum(
                    max(output.physical_available_qty, 0.0)
                    for output in candidates
                )
                if float_compare(
                    available_total,
                    missing,
                    precision_rounding=final_product.uom_id.rounding or 0.001,
                ) < 0:
                    raise UserError(_(
                        'المتاح فعليًا من %(lane)s للصنف '
                        '%(product)s هو %(available)s، والمطلوب %(required)s.'
                    ) % {
                        'lane': self._furniture_lane_labels().get(lane, lane),
                        'product': final_product.display_name,
                        'available': available_total,
                        'required': missing,
                    })

                for output in candidates:
                    if float_compare(missing, 0.0, precision_digits=3) <= 0:
                        break
                    available = max(output.physical_available_qty, 0.0)
                    quantity = min(available, missing)
                    if float_compare(quantity, 0.0, precision_digits=3) <= 0:
                        continue
                    output._furniture_assert_fifo_available()
                    move = self.env[
                        'stock.move'
                    ]._furniture_internal_create_for_handoff([{
                        'name': _('%(order)s - تحويل %(lane)s') % {
                            'order': self.name,
                            'lane': self._furniture_lane_labels().get(
                                lane, lane,
                            ),
                        },
                        'product_id': output.wip_product_id.id,
                        'product_uom': output.uom_id.id,
                        'product_uom_qty': quantity,
                        'location_id': output.source_location_id.id,
                        'location_dest_id': destination.id,
                        'origin': self.name,
                        'company_id': self.company_id.id,
                        'furniture_source_production_line_id': (
                            output.production_line_id.id
                        ),
                        # The row is linked immediately after it is created;
                        # role still protects this internal creation path.
                        'furniture_lane_handoff_role': lane,
                    }])
                    move._action_confirm(merge=False)
                    move._action_assign()
                    if move.state != 'assigned':
                        raise UserError(_(
                            'تعذر حجز رصيد %(lane)s فعليًا للأمر %(order)s.'
                        ) % {
                            'lane': self._furniture_lane_labels().get(lane, lane),
                            'order': self.name,
                        })
                    handoff = Handoff._furniture_internal_create([{
                        'downstream_production_id': self.id,
                        'downstream_line_id': line.id,
                        'output_id': output.id,
                        'role': lane,
                        'quantity': quantity,
                        'transfer_move_id': move.id,
                        'state': 'reserved',
                    }])
                    all_handoffs |= handoff
                    missing -= quantity
                    self.invalidate_recordset(['upstream_handoff_ids'])
                    line.invalidate_recordset(['downstream_handoff_ids'])
                    output.invalidate_recordset([
                        'handoff_ids', 'reserved_qty', 'assembled_qty',
                        'available_qty', 'physical_available_qty', 'state',
                    ])
                    output._furniture_refresh_matching_groups()
        self._furniture_sync_handoff_inherited_costs(lines)
        self._furniture_notify_pending_handoff()
        return all_handoffs

    def _furniture_consume_required_handoffs(self, production_lines=False):
        self.ensure_one()
        required_lanes = self._furniture_handoff_required_lanes()
        if not required_lanes:
            return True
        lines = self._furniture_handoff_lines(production_lines)
        if not lines:
            return True
        self._furniture_reserve_required_handoffs(
            production_lines=lines,
        )
        self.invalidate_recordset(['upstream_handoff_ids'])
        for line in lines:
            for lane in required_lanes:
                handoffs = self.upstream_handoff_ids.filtered(lambda handoff: (
                    handoff.downstream_line_id == line
                    and handoff.role == lane
                    and handoff.state != 'cancelled'
                ))
                required_qty = self._quantity_in_product_uom(
                    self._furniture_line_final_product(line),
                    line.product_qty,
                    line.product_uom_id or line.product_id.uom_id,
                )
                if float_compare(
                    sum(handoffs.mapped('quantity')),
                    required_qty,
                    precision_rounding=line.product_uom_id.rounding or 0.001,
                ) < 0:
                    raise UserError(_(
                        'التحويلات المحجوزة من %s لا تغطي كمية السطر.'
                    ) % self._furniture_lane_labels().get(lane, lane))

        upstream_productions = self.env['furniture.mrp.production']
        outputs_to_refresh = self.env['furniture.mrp.lane.output']
        selected_handoffs = self.upstream_handoff_ids.filtered(lambda row: (
            row.state == 'reserved'
            and row.downstream_line_id in lines
        )).sorted('id').sudo()
        if selected_handoffs:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_lane_handoff '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(selected_handoffs.ids)],
            )
            selected_handoffs.invalidate_recordset([
                'state', 'transfer_move_id', 'output_id',
            ])
        for handoff in selected_handoffs:
            move = handoff.transfer_move_id.sudo()
            move._action_assign()
            if move.state != 'assigned':
                raise UserError(_(
                    'رصيد التحويلة %s لم يعد متاحًا.'
                ) % handoff.display_name)
            move.quantity = handoff.quantity
            move.picked = True
            move._action_done()
            handoff._furniture_internal_write({'state': 'consumed'})
            outputs_to_refresh |= handoff.output_id
            upstream_productions |= handoff.output_id.production_id
        outputs_to_refresh._furniture_refresh_matching_groups()
        upstream_productions._furniture_auto_close_fully_assembled_orders()
        return True

    @api.model
    def _furniture_create_handoff_lane_order(
        self,
        lane,
        outputs_by_lane,
        quantity,
        note=False,
    ):
        """Create one downstream draft and reserve its exact FIFO inputs."""
        required_lanes_by_route = self._furniture_lane_handoff_required_lanes()
        if lane not in required_lanes_by_route:
            raise ValidationError(_('مسار التحويل غير مدعوم.'))
        required_lanes = required_lanes_by_route[lane]
        if set(outputs_by_lane) != set(required_lanes):
            raise ValidationError(_('مدخلات أمر المرحلة غير مكتملة.'))
        outputs = self.env['furniture.mrp.lane.output']
        for role in required_lanes:
            output = outputs_by_lane[role].exists()
            if len(output) != 1 or output.lane != role:
                raise ValidationError(_('دفعة التحويل لا تطابق المسار.'))
            outputs |= output
        anchor = outputs_by_lane[required_lanes[0]].exists()
        identity = (
            anchor.company_id,
            anchor.final_product_id,
            anchor.furniture_model_id,
            anchor.bom_id,
            anchor.uom_id,
        )
        if any((
            output.company_id,
            output.final_product_id,
            output.furniture_model_id,
            output.bom_id,
            output.uom_id,
        ) != identity for output in outputs):
            raise ValidationError(_('دفعات التحويل لا تخص نفس الصنف والموديل.'))
        dimensions = anchor._furniture_handoff_dimensions()
        if any(
            output._furniture_handoff_dimension_key()
            != anchor._furniture_handoff_dimension_key()
            for output in outputs
        ):
            raise ValidationError(_(
                'دفعات التحويل المختارة لا تخص نفس المقاس الفعلي.'
            ))
        company, product, furniture_model, bom, uom = identity
        quantity = max(float(quantity), 0.0)
        if float_compare(
            quantity,
            0.0,
            precision_rounding=uom.rounding or 0.001,
        ) <= 0:
            return self.env['furniture.mrp.production']

        now = fields.Datetime.now()
        values = {
            'company_id': company.id,
            'product_id': product.id,
            'furniture_order_model_id': furniture_model.id,
            'product_qty': quantity,
            'bom_id': bom.id,
            'width_cm': dimensions[0],
            'depth_cm': dimensions[1],
            'height_cm': dimensions[2],
            'date_planned_start': now,
            'date_planned_finish': now + timedelta(days=7),
            'production_lane': lane,
            'stage_plan_mode': 'custom',
            'handoff_generated': True,
            'notes': note or _(
                'أمر تابع مولد آليًا من تحويلات المراحل.'
            ),
        }
        values.update(_lane_route_values(self, lane))
        if 'stage_replenishment_generated' in self._fields:
            values.update({
                'stage_replenishment_generated': True,
                'stage_replenishment_created_at': now,
            })
        Production = self.sudo().with_company(company).with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        )
        if (
            values.get('stage_replenishment_generated')
            and hasattr(Production, '_stage_replenishment_internal_create')
        ):
            # The replenishment bridge deliberately protects its traceability
            # markers from normal ``create`` calls.  A handoff-generated order
            # is another trusted generator path, so route it through that
            # bridge's private API instead of weakening the public guard.
            production = Production._stage_replenishment_internal_create([
                values,
            ])
        else:
            production = Production.create(values)
        line_values = {
            'production_id': production.id,
            'sequence': 10,
            'product_id': product.id,
            'furniture_order_model_id': furniture_model.id,
            'product_qty': quantity,
            'bom_id': bom.id,
            'width_cm': dimensions[0],
            'depth_cm': dimensions[1],
            'height_cm': dimensions[2],
            'stage_selection_initialized': True,
        }
        line_values.update(_lane_route_values(
            self.env['furniture.mrp.production.line'], lane,
        ))
        self.env['furniture.mrp.production.line'].sudo().with_company(
            company
        ).with_context(
            furniture_preserve_explicit_bom=True,
            furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create(line_values)
        production._furniture_auto_confirm_generated_handoff_order()
        production._furniture_reserve_required_handoffs(
            source_outputs_by_lane=outputs_by_lane,
        )
        production.message_post(body=_(
            'تم إنشاء الأمر آليًا بعد حجز: %s.'
        ) % '، '.join(
            self._furniture_lane_labels().get(role, role)
            for role in required_lanes
        ))
        return production

    def _furniture_prepare_packaging_finished_specs(self, production_lines):
        self.ensure_one()
        destination = (
            self.location_dest_id
            or self.env.ref(
                'furniture_mrp.location_finished_goods',
                raise_if_not_found=False,
            )
        )
        if not destination or not self.location_packaging_id:
            raise UserError(_(
                'مواقع التغليف والمنتج التام غير مكتملة.'
            ))
        lines = production_lines.exists()
        specs = []
        for line in lines.sorted(lambda row: (row.sequence, row.id)):
            product = self._furniture_line_final_product(line)
            line_specs = self._get_finished_output_specs_from_lines(line)
            if not line_specs:
                continue
            spec = dict(line_specs[0])
            spec_uom = spec.get('uom') or product.uom_id
            completed_moves = self.env['stock.move'].sudo().search([
                ('state', '=', 'done'),
                ('origin', '=', self.name),
                ('product_id', '=', product.id),
                ('location_dest_id', '=', destination.id),
                '|',
                ('furniture_source_production_line_id', '=', line.id),
                ('furniture_source_production_line_ids', 'in', [line.id]),
            ])
            completed_qty = sum(
                self._furniture_finished_move_quantity_for_line(
                    move,
                    line,
                    spec_uom,
                )
                for move in completed_moves
            )
            remaining_qty = max((spec.get('qty') or 0.0) - completed_qty, 0.0)
            if float_compare(
                remaining_qty,
                0.0,
                precision_rounding=spec_uom.rounding or 0.001,
            ) <= 0:
                continue
            spec.update({
                'qty': remaining_qty,
                'source_location': self.location_packaging_id,
                'source_production_line': line,
            })
            specs.append(spec)
        return specs

    def _furniture_finished_move_quantity_for_line(self, move, line, uom):
        """Attribute an old grouped finished move to one technical line."""
        self.ensure_one()
        move_qty = move.product_uom._compute_quantity(move.quantity, uom)
        allocated_lines = move.furniture_source_production_line_ids.exists()
        if not allocated_lines:
            return move_qty if move.furniture_source_production_line_id == line else 0.0
        if line not in allocated_lines:
            return 0.0
        if len(allocated_lines) == 1:
            return move_qty
        weights = {}
        for allocated_line in allocated_lines:
            source_uom = allocated_line.product_uom_id or uom
            weights[allocated_line.id] = source_uom._compute_quantity(
                allocated_line.product_qty,
                uom,
            )
        total_weight = sum(weights.values())
        return (
            move_qty * weights.get(line.id, 0.0) / total_weight
            if total_weight else 0.0
        )

    def _furniture_packaging_lines_fully_finished(
        self,
        production_lines=False,
        finished_location=False,
    ):
        self.ensure_one()
        lines = self._furniture_handoff_lines(production_lines)
        destination = (
            finished_location
            or self.location_dest_id
            or self.env.ref(
                'furniture_mrp.location_finished_goods',
                raise_if_not_found=False,
            )
        )
        if not lines or not destination:
            return False
        Move = self.env['stock.move'].sudo()
        for line in lines:
            product = self._furniture_line_final_product(line)
            required_qty = self._quantity_in_product_uom(
                product,
                line.product_qty,
                line.product_uom_id or product.uom_id,
            )
            completed_moves = Move.search([
                ('state', '=', 'done'),
                ('origin', '=', self.name),
                ('product_id', '=', product.id),
                ('location_dest_id', '=', destination.id),
                '|',
                ('furniture_source_production_line_id', '=', line.id),
                ('furniture_source_production_line_ids', 'in', [line.id]),
            ])
            completed_qty = sum(
                self._furniture_finished_move_quantity_for_line(
                    move,
                    line,
                    product.uom_id,
                )
                for move in completed_moves
            )
            if float_compare(
                completed_qty,
                required_qty,
                precision_rounding=product.uom_id.rounding or 0.001,
            ) < 0:
                return False
        return True

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
        if self.production_lane == 'frame' and stage_code == 'carpentry':
            # The base implementation has just materialised the physical frame
            # output.  Only now may the serial downstream bases/finishing order
            # be opened and tied to that exact FIFO batch.
            self._ensure_lane_outputs(
                production_lines
            )._furniture_auto_create_finish_orders()
        if self.production_lane == 'packaging' and stage_code == 'packaging':
            lines = (production_lines or self.production_line_ids).exists()
            specs = self._furniture_prepare_packaging_finished_specs(lines)
            if specs:
                moves = self.env['stock.move']
                # One call per technical line prevents the generic finished
                # move optimiser from coalescing lines and losing exact delta
                # attribution on a partial quality retry.
                for spec in specs:
                    moves |= self._move_finished_product(specs=[spec])
                self.message_post(body=_(
                    'تم إدخال %(count)s حركة من التغليف '
                    'إلى مخزن المنتج التام آليًا.'
                ) % {'count': len(moves)})
        return entries

    def _ensure_lane_outputs(self, production_lines=False):
        if self.production_lane == 'packaging':
            return self.env['furniture.mrp.lane.output']
        outputs = super()._ensure_lane_outputs(production_lines)
        if outputs and self.production_lane in ('upholstery', 'painting'):
            outputs._furniture_auto_create_packaging_orders()
        return outputs

    def _furniture_handoffs_cover_lines(self, production_lines=False):
        self.ensure_one()
        required_lanes = self._furniture_handoff_required_lanes()
        if not required_lanes:
            return True
        lines = self._furniture_handoff_lines(production_lines)
        if not lines:
            return False
        self.invalidate_recordset(['upstream_handoff_ids'])
        for line in lines:
            final_product = self._furniture_line_final_product(line)
            required_qty = self._quantity_in_product_uom(
                final_product,
                line.product_qty,
                line.product_uom_id or final_product.uom_id,
            )
            for lane in required_lanes:
                covered = sum(self.upstream_handoff_ids.filtered(
                    lambda handoff: (
                        handoff.downstream_line_id == line
                        and handoff.role == lane
                        and handoff.state in ('reserved', 'consumed')
                    )
                ).mapped('quantity'))
                if float_compare(
                    covered,
                    required_qty,
                    precision_rounding=final_product.uom_id.rounding or 0.001,
                ) < 0:
                    return False
        return True

    def _furniture_accepted_handoffs_cover_lines(self, production_lines=False):
        """True only after the destination supervisor moved every input."""
        self.ensure_one()
        required_lanes = self._furniture_handoff_required_lanes()
        if not required_lanes:
            return True
        lines = self._furniture_handoff_lines(production_lines)
        if not lines:
            return False
        self.invalidate_recordset(['upstream_handoff_ids'])
        for line in lines:
            final_product = self._furniture_line_final_product(line)
            required_qty = self._quantity_in_product_uom(
                final_product,
                line.product_qty,
                line.product_uom_id or final_product.uom_id,
            )
            for lane in required_lanes:
                consumed_qty = sum(self.upstream_handoff_ids.filtered(
                    lambda handoff: (
                        handoff.downstream_line_id == line
                        and handoff.role == lane
                        and handoff.state == 'consumed'
                    )
                ).mapped('quantity'))
                if float_compare(
                    consumed_qty,
                    required_qty,
                    precision_rounding=final_product.uom_id.rounding or 0.001,
                ) < 0:
                    return False
        return True

    def _furniture_require_accepted_handoffs(self, production_lines=False):
        self.ensure_one()
        if not self._furniture_handoff_required_lanes():
            return True
        lines = self._furniture_handoff_lines(production_lines)
        if lines and not self._furniture_accepted_handoffs_cover_lines(lines):
            _stage_code, stage_label = self._furniture_handoff_target_stage()
            raise UserError(_(
                'التحويل إلى %(stage)s ما زال بانتظار قبول مشرف المرحلة. '
                'إغلاق التنبيه لا ينفذ التحويل.'
            ) % {'stage': stage_label})
        return True

    def _start_first_stage_lines_from_stock(
        self,
        stage_order,
        stage_code,
        wizard_lines,
    ):
        self.ensure_one()
        if self._furniture_handoff_required_lanes():
            selected_rows = wizard_lines.filtered(
                lambda row: row.selected and row.production_line_id
            )
            selected_lines = self.env['furniture.mrp.production.line']
            for row in selected_rows:
                allocations = row._technical_qty_allocations()
                for line, quantity in allocations:
                    if line.production_id != self:
                        raise ValidationError(_(
                            'سطر البدء المختار لا يتبع أمر المرحلة.'
                        ))
                    if float_compare(
                        quantity,
                        line.product_qty,
                        precision_digits=3,
                    ) != 0:
                        raise UserError(_(
                            'أوامر تحويل المراحل تستخدم دفعات كاملة. '
                            'أنشئ أمر مرحلة مستقل للكمية الجزئية.'
                        ))
                    selected_lines |= line
            if selected_lines:
                self._furniture_require_accepted_handoffs(selected_lines)
        return super()._start_first_stage_lines_from_stock(
            stage_order,
            stage_code,
            wizard_lines,
        )

    def _move_stage_materials_for_lines(self, stage_code, production_lines):
        self.ensure_one()
        lines = self._furniture_handoff_lines(production_lines)
        if self._furniture_handoff_required_lanes() and lines:
            self._furniture_require_accepted_handoffs(lines)
        return super()._move_stage_materials_for_lines(
            stage_code,
            production_lines,
        )

    def _furniture_start_handoff_lane(self, method_name):
        self.ensure_one()
        # Creating the stage order / requesting its materials owns the FIFO
        # quantity but must not move it.  Only the destination supervisor's
        # explicit acceptance consumes the hand-off stock move.
        self._furniture_reserve_required_handoffs()
        return getattr(
            super(FurnitureMrpProductionLaneHandoff, self),
            method_name,
        )()

    def action_start_bases(self):
        result = True
        for production in self:
            if (
                production._furniture_handoff_target_stage()[0] == 'bases'
                and production._furniture_handoff_required_lanes()
            ):
                result = production._furniture_start_handoff_lane(
                    'action_start_bases'
                ) and result
            else:
                result = super(
                    FurnitureMrpProductionLaneHandoff,
                    production,
                ).action_start_bases() and result
        return result

    def action_start_finishing(self):
        result = True
        for production in self:
            if (
                production._furniture_handoff_target_stage()[0] == 'finishing'
                and production._furniture_handoff_required_lanes()
            ):
                result = production._furniture_start_handoff_lane(
                    'action_start_finishing'
                ) and result
            else:
                result = super(
                    FurnitureMrpProductionLaneHandoff,
                    production,
                ).action_start_finishing() and result
        return result

    def action_start_upholstery(self):
        result = True
        for production in self:
            if production.production_lane == 'upholstery':
                result = production._furniture_start_handoff_lane(
                    'action_start_upholstery'
                ) and result
            else:
                result = super(
                    FurnitureMrpProductionLaneHandoff,
                    production,
                ).action_start_upholstery() and result
        return result

    def action_start_packaging(self):
        result = True
        for production in self:
            if production.production_lane == 'packaging':
                result = production._furniture_start_handoff_lane(
                    'action_start_packaging'
                ) and result
            else:
                result = super(
                    FurnitureMrpProductionLaneHandoff,
                    production,
                ).action_start_packaging() and result
        return result

    def action_cancel(self):
        for production in self:
            outgoing = self.env[
                'furniture.mrp.lane.handoff'
            ].sudo().search_count([
                ('output_id.production_id', '=', production.id),
                ('state', 'in', ('reserved', 'consumed')),
            ])
            if outgoing:
                raise UserError(_(
                    'لا يمكن إلغاء أمر مصدر له تحويلات مراحل صادرة نشطة.'
                ))
            consumed = production.upstream_handoff_ids.filtered(
                lambda handoff: handoff.state == 'consumed'
            )
            if consumed:
                raise UserError(_(
                    'لا يمكن إلغاء أمر استهلك تحويلات من '
                    'المرحلة السابقة.'
                ))
            for handoff in production.upstream_handoff_ids.filtered(
                lambda row: row.state == 'reserved'
            ):
                move = handoff.transfer_move_id.sudo()
                if move.state not in ('done', 'cancel'):
                    move.with_context(
                        furniture_internal_cancel_lane_handoff=True,
                    )._action_cancel()
                handoff._furniture_internal_write({'state': 'cancelled'})
                handoff.output_id._furniture_refresh_matching_groups()
            production._furniture_sync_handoff_inherited_costs()
        return super().action_cancel()


class FurnitureMrpStageMixinLaneHandoff(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    def _furniture_handoff_start_lines(self):
        self.ensure_one()
        production = self.production_order_id
        expected_lanes = production._furniture_lane_handoff_stage_models().get(
            self._name, (),
        )
        Line = self.env['furniture.mrp.production.line']
        if not production or production.production_lane not in expected_lanes:
            return Line

        explicit_ids = self.env.context.get(
            'furniture_handoff_production_line_ids'
        )
        if explicit_ids:
            return production._furniture_handoff_lines(
                Line.browse(explicit_ids),
            )

        active_lines = self._get_stage_line_ids_data(
            'active_production_line_ids_data'
        )
        completed_lines = self._get_stage_line_ids_data(
            'completed_production_line_ids_data'
        )
        quality_lines = self._get_stage_line_ids_data(
            'quality_production_line_ids_data'
        )
        active_lines -= completed_lines | quality_lines
        start_mode = self.env.context.get('furniture_stage_start_mode')
        if start_mode == 'first_stage_selected':
            selected = self.first_stage_production_line_ids - completed_lines
            return production._furniture_handoff_lines(selected)
        if active_lines:
            return production._furniture_handoff_lines(active_lines)
        if start_mode in ('with_existing', 'selected_work', 'current_only'):
            # An explicit carry-over choice containing only historic hall stock
            # must not consume this order's still-reserved future batches.
            return Line
        return production._furniture_handoff_lines()

    def _has_startable_stage_work(self):
        self.ensure_one()
        if super()._has_startable_stage_work():
            return True
        lines = self._furniture_handoff_start_lines()
        return bool(
            lines
            and self.production_order_id._furniture_accepted_handoffs_cover_lines(
                lines,
            )
        )

    def _get_selected_labor_users(self):
        self.ensure_one()
        labor_users = super()._get_selected_labor_users()
        if (
            labor_users
            and self.env.context.get('furniture_lane_handoff_actual_start')
        ):
            lines = self._furniture_handoff_start_lines()
            if lines:
                self.production_order_id._furniture_require_accepted_handoffs(
                    lines,
                )
        return labor_users


class FurnitureMrpBasesHandoffStart(models.Model):
    _inherit = 'furniture.mrp.bases'

    def action_start(self):
        records = self.with_context(furniture_lane_handoff_actual_start=True)
        return super(FurnitureMrpBasesHandoffStart, records).action_start()


class FurnitureMrpFinishingHandoffStart(models.Model):
    _inherit = 'furniture.mrp.finishing'

    def action_start(self):
        records = self.with_context(furniture_lane_handoff_actual_start=True)
        return super(FurnitureMrpFinishingHandoffStart, records).action_start()


class FurnitureMrpUpholsteryHandoffStart(models.Model):
    _inherit = 'furniture.mrp.upholstery'

    def action_start(self):
        records = self.with_context(furniture_lane_handoff_actual_start=True)
        return super(FurnitureMrpUpholsteryHandoffStart, records).action_start()


class FurnitureMrpPackagingHandoffStart(models.Model):
    _inherit = 'furniture.mrp.packaging'

    def action_start(self):
        records = self.with_context(furniture_lane_handoff_actual_start=True)
        return super(FurnitureMrpPackagingHandoffStart, records).action_start()


class FurnitureMrpProductionLineHandoff(models.Model):
    _inherit = 'furniture.mrp.production.line'

    downstream_handoff_ids = fields.One2many(
        'furniture.mrp.lane.handoff',
        'downstream_line_id',
        string='مدخلات المرحلة المحجوزة',
        readonly=True,
    )

    def write(self, vals):
        candidate_fields = HANDOFF_LINE_IDENTITY_FIELDS & set(vals)
        if candidate_fields:
            active_handoffs = self.env[
                'furniture.mrp.lane.handoff'
            ].sudo().search([
                ('state', 'in', ('reserved', 'consumed')),
                '|',
                ('downstream_line_id', 'in', self.ids),
                ('output_id.production_line_id', 'in', self.ids),
            ])
            protected_ids = set(
                active_handoffs.mapped('downstream_line_id').ids
            ) | set(active_handoffs.mapped('output_id.production_line_id').ids)
            changed = self.filtered(lambda line: (
                line.id in protected_ids
                and any(
                    _furniture_value_changes(line, field_name, vals[field_name])
                    for field_name in candidate_fields
                )
            ))
            if changed:
                raise UserError(_(
                    'لا يمكن تغيير هوية الصنف أو كميته أو مقاسه بعد حجز '
                    'تحويلات المراحل. ألغِ الأمر وأنشئ أمرًا جديدًا.'
                ))
        return super().write(vals)


class FurnitureMrpLaneOutputHandoff(models.Model):
    _inherit = 'furniture.mrp.lane.output'

    handoff_ids = fields.One2many(
        'furniture.mrp.lane.handoff',
        'output_id',
        string='تحويلات المراحل',
        readonly=True,
    )

    def _furniture_handoff_dimensions(self):
        self.ensure_one()
        return self.production_id._furniture_line_handoff_dimensions(
            self.production_line_id,
        )

    def _furniture_handoff_dimension_key(self):
        self.ensure_one()
        return tuple(round(value or 0.0, 3) for value in (
            self._furniture_handoff_dimensions()
        ))

    @api.depends(
        'qty_ready',
        'allocation_ids.qty',
        'allocation_ids.assembly_id.state',
        'handoff_ids.quantity',
        'handoff_ids.state',
    )
    def _compute_allocation_quantities(self):
        super()._compute_allocation_quantities()
        for output in self:
            handoff_reserved = sum(
                output.handoff_ids.filtered(
                    lambda row: row.state == 'reserved'
                ).mapped('quantity')
            )
            handoff_consumed = sum(
                output.handoff_ids.filtered(
                    lambda row: row.state == 'consumed'
                ).mapped('quantity')
            )
            output.reserved_qty += handoff_reserved
            output.assembled_qty += handoff_consumed
            output.available_qty = max(
                output.qty_ready - output.reserved_qty - output.assembled_qty,
                0.0,
            )
            if float_compare(output.available_qty, 0.0, precision_digits=3) <= 0:
                output.state = 'exhausted'
            elif float_compare(
                output.reserved_qty + output.assembled_qty,
                0.0,
                precision_digits=3,
            ) > 0:
                output.state = 'partially_reserved'
            else:
                output.state = 'ready'

    def write(self, vals):
        candidate_fields = HANDOFF_OUTPUT_IMMUTABLE_FIELDS & set(vals)
        if candidate_fields:
            protected_ids = set(self.env[
                'furniture.mrp.lane.handoff'
            ].sudo().search([
                ('output_id', 'in', self.ids),
                ('state', 'in', ('reserved', 'consumed')),
            ]).mapped('output_id').ids)
            protected = self.filtered(
                lambda output: output.id in protected_ids
            )
            changed = protected.filtered(lambda output: any(
                _furniture_value_changes(output, field_name, vals[field_name])
                for field_name in candidate_fields
            ))
            if changed:
                raise AccessError(_(
                    'لا يمكن تغيير دفعة مرحلة بعد ربطها بتحويلة نشطة.'
                ))
        return super().write(vals)

    def _furniture_write_completion_values(self, values):
        """Finalize cost without weakening linked-output identity guards.

        A downstream handoff freezes the physical identity and stock trace of
        its source output.  The source stage's final cost, however, is only
        known when quality completion records the stage costs.  Permit that
        one trusted cost refresh and immediately propagate it to the exact
        downstream lines while keeping every other immutable field protected.
        """
        self.ensure_one()
        active_handoffs = self.handoff_ids.filtered(
            lambda handoff: handoff.state in ('reserved', 'consumed')
        )
        if not active_handoffs:
            return super()._furniture_write_completion_values(values)

        protected_fields = (
            HANDOFF_OUTPUT_IMMUTABLE_FIELDS - {'unit_cost'}
        ) & set(values)
        if any(
            _furniture_value_changes(self, field_name, values[field_name])
            for field_name in protected_fields
        ):
            raise AccessError(_(
                'لا يمكن تغيير دفعة مرحلة بعد ربطها بتحويلة نشطة.'
            ))

        final_unit_cost = values.get('unit_cost', self.unit_cost)
        safe_values = dict(values)
        safe_values['unit_cost'] = self.unit_cost
        result = super()._furniture_write_completion_values(safe_values)
        if _furniture_value_changes(self, 'unit_cost', final_unit_cost):
            super(
                FurnitureMrpLaneOutputHandoff, self.sudo()
            ).write({'unit_cost': final_unit_cost})
            for production in active_handoffs.mapped(
                'downstream_production_id'
            ):
                production_handoffs = active_handoffs.filtered(
                    lambda handoff: (
                        handoff.downstream_production_id == production
                    )
                )
                production._furniture_sync_handoff_inherited_costs(
                    production_handoffs.mapped('downstream_line_id')
                )
        return result

    def _furniture_auto_create_handoff_orders(
        self,
        downstream_lane,
        required_lanes,
        company=False,
        final_product=False,
        furniture_model=False,
        bom=False,
        maximum_quantity=False,
        candidate_output_ids=None,
    ):
        """Pair exact FIFO outputs and create downstream draft orders."""
        Production = self.env['furniture.mrp.production']
        if tuple(required_lanes) != Production._furniture_lane_handoff_required_lanes().get(downstream_lane):
            raise ValidationError(_('بوابة المرحلة التالية غير مطابقة.'))
        domain = [('lane', 'in', tuple(required_lanes))]
        if candidate_output_ids is not None:
            # Compatibility callers can continue an approved legacy chain
            # without taking outputs claimed by a new per-piece plan.
            domain.append(('id', 'in', candidate_output_ids))
        if company:
            domain.append(('company_id', '=', company.id))
        if final_product:
            domain.append(('final_product_id', '=', final_product.id))
        if furniture_model:
            domain.append(('furniture_model_id', '=', furniture_model.id))
        if bom:
            domain.append(('bom_id', '=', bom.id))
        candidates = self.sudo().search(
            domain,
            order='fifo_date, ready_at, id',
        )
        identities = sorted({(
            output.company_id.id,
            output.final_product_id.id,
            output.furniture_model_id.id,
            output.bom_id.id,
            output.uom_id.id,
            *output._furniture_handoff_dimension_key(),
        ) for output in candidates})
        remaining = (
            max(float(maximum_quantity), 0.0)
            if maximum_quantity is not False else False
        )
        productions = self.env['furniture.mrp.production']
        for identity in identities:
            if remaining is not False and float_compare(
                remaining, 0.0, precision_digits=3,
            ) <= 0:
                break
            self.env.cr.execute(
                'SELECT pg_advisory_xact_lock(hashtext(%s))',
                ['furniture-lane-handoff:%s:%s' % (
                    downstream_lane,
                    ':'.join(str(value) for value in identity),
                )],
            )
            group_domain = [
                ('company_id', '=', identity[0]),
                ('final_product_id', '=', identity[1]),
                ('furniture_model_id', '=', identity[2]),
                ('bom_id', '=', identity[3]),
                ('uom_id', '=', identity[4]),
                ('lane', 'in', tuple(required_lanes)),
            ]
            if candidate_output_ids is not None:
                group_domain.append(('id', 'in', candidate_output_ids))
            while True:
                group = self.sudo().search(
                    group_domain,
                    order='fifo_date, ready_at, id',
                ).filtered(lambda output: (
                    output._furniture_handoff_dimension_key() == identity[5:]
                ))
                if not group:
                    break
                fifo_group = group._furniture_fifo_group_outputs()
                fifo_group._furniture_lock_rows()
                group.invalidate_recordset([
                    'handoff_ids', 'reserved_qty', 'assembled_qty',
                    'available_qty', 'physical_available_qty', 'state',
                ])
                group._furniture_refresh_matching_groups()
                chosen = {}
                for lane in required_lanes:
                    chosen[lane] = group.filtered(lambda output, lane=lane: (
                        output.lane == lane
                        and float_compare(
                            output.physical_available_qty,
                            0.0,
                            precision_rounding=output.uom_id.rounding or 0.001,
                        ) > 0
                    )).sorted(lambda output: (
                        output.fifo_date or output.ready_at or fields.Datetime.now(),
                        output.ready_at or output.fifo_date or fields.Datetime.now(),
                        output.id,
                    ))[:1]
                if any(not output for output in chosen.values()):
                    break
                quantity = min(
                    output.physical_available_qty
                    for output in chosen.values()
                )
                if remaining is not False:
                    quantity = min(quantity, remaining)
                if float_compare(quantity, 0.0, precision_digits=3) <= 0:
                    break
                production = self.env[
                    'furniture.mrp.production'
                ]._furniture_create_handoff_lane_order(
                    downstream_lane,
                    chosen,
                    quantity,
                    note=_(
                        'أمر %(lane)s مولد آليًا بعد جاهزية %(inputs)s.'
                    ) % {
                        'lane': Production._furniture_lane_labels().get(
                            downstream_lane, downstream_lane,
                        ),
                        'inputs': '، '.join(
                            Production._furniture_lane_labels().get(role, role)
                            for role in required_lanes
                        ),
                    },
                )
                productions |= production
                if remaining is not False:
                    remaining -= quantity
        return productions

    def _furniture_auto_create_packaging_orders(self):
        created = self.env['furniture.mrp.production']
        seen = set()
        for output in self.exists():
            identity = (
                output.company_id.id,
                output.final_product_id.id,
                output.furniture_model_id.id,
                output.bom_id.id,
                *output._furniture_handoff_dimension_key(),
            )
            if identity in seen:
                continue
            seen.add(identity)
            created |= self._furniture_auto_create_handoff_orders(
                'packaging',
                ('upholstery', 'painting'),
                company=output.company_id,
                final_product=output.final_product_id,
                furniture_model=output.furniture_model_id,
                bom=output.bom_id,
            )
        return created

    def _furniture_auto_create_finish_orders(self):
        """Open bases/finishing only from physically ready frame output.

        A planned or merely confirmed frame order is not usable input.  The
        lane output is created only after carpentry has physically completed,
        so this callback is the single safe point at which the downstream
        finish order may exist.
        """
        created = self.env['furniture.mrp.production']
        seen = set()
        for output in self.exists().filtered(lambda row: row.lane == 'frame'):
            identity = (
                output.company_id.id,
                output.final_product_id.id,
                output.furniture_model_id.id,
                output.bom_id.id,
                *output._furniture_handoff_dimension_key(),
            )
            if identity in seen:
                continue
            seen.add(identity)
            created |= output._furniture_auto_create_handoff_orders(
                'finish',
                ('frame',),
                company=output.company_id,
                final_product=output.final_product_id,
                furniture_model=output.furniture_model_id,
                bom=output.bom_id,
            )
        return created

    def unlink(self):
        if self.mapped('handoff_ids'):
            raise UserError(_(
                'لا يمكن حذف مخرج مرحلة له تحويلات مسجلة.'
            ))
        return super().unlink()


class FurnitureMrpLaneHandoff(models.Model):
    _name = 'furniture.mrp.lane.handoff'
    _description = 'سجل تحويل FIFO بين أوامر مراحل الأثاث'
    _order = 'id'
    _rec_name = 'name'

    name = fields.Char(
        string='المرجع',
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code(
            'furniture.mrp.lane.handoff'
        ) or '/',
        index=True,
    )
    downstream_production_id = fields.Many2one(
        'furniture.mrp.production',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
        string='أمر المرحلة التالية',
    )
    downstream_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
        string='سطر المرحلة التالية',
    )
    output_id = fields.Many2one(
        'furniture.mrp.lane.output',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
        string='دفعة المرحلة السابقة',
    )
    company_id = fields.Many2one(
        'res.company',
        related='downstream_production_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    role = fields.Selection(
        PRODUCTION_OUTPUT_LANE_SELECTION,
        required=True,
        readonly=True,
        index=True,
        string='نوع المدخل',
    )
    quantity = fields.Float(
        string='الكمية',
        required=True,
        readonly=True,
        digits='Product Unit of Measure',
    )
    transfer_move_id = fields.Many2one(
        'stock.move',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
        string='حركة التحويل',
    )
    state = fields.Selection(
        HANDOFF_STATE_SELECTION,
        required=True,
        readonly=True,
        default='reserved',
        index=True,
        string='الحالة',
    )

    _sql_constraints = [
        (
            'furniture_lane_handoff_qty_positive',
            'check(quantity > 0)',
            'كمية التحويلة يجب أن تكون أكبر من صفر.',
        ),
        (
            'furniture_lane_handoff_move_uniq',
            'unique(transfer_move_id)',
            'حركة التحويل مسجلة بالفعل.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_('سجلات التحويل تُنشأ من النظام فقط.'))

    @api.model
    def _furniture_internal_create(self, vals_list):
        handoffs = super(FurnitureMrpLaneHandoff, self.sudo()).create(vals_list)
        for handoff in handoffs:
            handoff.transfer_move_id._furniture_internal_link_handoff(handoff)
        return handoffs

    def write(self, vals):
        raise AccessError(_('سجل التحويل غير قابل للتعديل اليدوي.'))

    def _furniture_internal_write(self, vals):
        if set(vals) != {'state'}:
            raise AccessError(_('المسموح داخليًا هو تغيير حالة التحويلة فقط.'))
        allowed_transitions = {
            'reserved': {'consumed', 'cancelled'},
            'consumed': set(),
            'cancelled': set(),
        }
        if any(
            vals['state'] not in allowed_transitions.get(handoff.state, set())
            for handoff in self
        ):
            raise ValidationError(_('انتقال حالة التحويلة غير مسموح.'))
        return super(FurnitureMrpLaneHandoff, self.sudo()).write(vals)

    def unlink(self):
        raise AccessError(_('سجل التحويل غير قابل للحذف.'))

    @api.constrains(
        'downstream_production_id',
        'downstream_line_id',
        'output_id',
        'role',
        'quantity',
        'transfer_move_id',
    )
    def _check_handoff_identity(self):
        for handoff in self:
            if handoff.downstream_line_id.production_id != handoff.downstream_production_id:
                raise ValidationError(_('سطر التحويلة لا يتبع أمره.'))
            if handoff.output_id.lane != handoff.role:
                raise ValidationError(_('دور التحويلة لا يطابق مسار المخرج.'))
            if handoff.role not in handoff.downstream_production_id._furniture_lane_handoff_required_lanes().get(
                handoff.downstream_production_id.production_lane,
                (),
            ):
                raise ValidationError(_(
                    'دور التحويلة ليس مدخلًا معتمدًا لمسار الأمر التالي.'
                ))
            if handoff.output_id.company_id != handoff.downstream_production_id.company_id:
                raise ValidationError(_('شركة المصدر لا تطابق الأمر التابع.'))
            line = handoff.downstream_line_id
            final_product = (
                line.production_id._furniture_line_final_product(line)
            )
            if (
                handoff.output_id.final_product_id != final_product
                or handoff.output_id.furniture_model_id
                != (line.furniture_order_model_id or line.production_id.furniture_order_model_id)
                or handoff.output_id.bom_id != (line.bom_id or line.production_id.bom_id)
            ):
                raise ValidationError(_('هوية دفعة التحويل لا تطابق السطر.'))
            if not handoff.downstream_production_id._furniture_handoff_dimensions_match(
                handoff.output_id,
                line,
            ):
                raise ValidationError(_('مقاس دفعة التحويل لا يطابق السطر.'))
            move = handoff.transfer_move_id
            destination = handoff.downstream_production_id._get_production_location()
            if (
                move.product_id != handoff.output_id.wip_product_id
                or move.product_uom != handoff.output_id.uom_id
                or float_compare(
                    move.product_uom_qty,
                    handoff.quantity,
                    precision_rounding=handoff.output_id.uom_id.rounding or 0.001,
                ) != 0
                or move.location_id != handoff.output_id.source_location_id
                or move.location_dest_id != destination
                or move.company_id != handoff.company_id
                or move.origin != handoff.downstream_production_id.name
                or move.furniture_lane_handoff_role != handoff.role
                or move.furniture_source_production_line_id
                != handoff.output_id.production_line_id
                or (
                    move.furniture_source_production_line_ids
                    and handoff.output_id.production_line_id
                    not in move.furniture_source_production_line_ids
                )
                or (
                    move.furniture_lane_handoff_id
                    and move.furniture_lane_handoff_id != handoff
                )
            ):
                raise ValidationError(_('حركة المخزون لا تطابق التحويلة.'))


class StockMoveLaneHandoff(models.Model):
    _inherit = 'stock.move'

    furniture_lane_handoff_id = fields.Many2one(
        'furniture.mrp.lane.handoff',
        string='تحويلة مرحلة الأثاث',
        copy=False,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    furniture_lane_handoff_role = fields.Selection(
        PRODUCTION_OUTPUT_LANE_SELECTION,
        string='دور حركة التحويل',
        copy=False,
        readonly=True,
        index=True,
    )

    _sql_constraints = [
        (
            'furniture_lane_handoff_backlink_uniq',
            'unique(furniture_lane_handoff_id)',
            'سجل التحويل مرتبط بحركة مخزون أخرى بالفعل.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if any(HANDOFF_MOVE_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(_('ربط حركة بتحويلة مرحلة يتم داخليًا فقط.'))
        return super().create(vals_list)

    def write(self, vals):
        if HANDOFF_MOVE_FIELDS & set(vals):
            raise AccessError(_('لا يمكن تعديل هوية حركة التحويل يدويًا.'))
        if (
            vals.get('state') == 'cancel'
            and self.filtered('furniture_lane_handoff_id')
            and not self.env.context.get(
                'furniture_internal_cancel_lane_handoff'
            )
        ):
            raise AccessError(_(
                'إلغاء حركة التحويل يتم فقط من إلغاء أمر المرحلة التابع.'
            ))
        candidate_fields = HANDOFF_MOVE_IDENTITY_FIELDS & set(vals)
        if candidate_fields:
            linked = self.filtered('furniture_lane_handoff_id')
            changed = linked.filtered(lambda move: any(
                _furniture_value_changes(move, field_name, vals[field_name])
                for field_name in candidate_fields
            ))
            if changed:
                raise AccessError(_(
                    'لا يمكن تغيير حركة مخزون بعد ربطها بتحويلة مرحلة.'
                ))
        return super().write(vals)

    def _action_cancel(self):
        linked = self.filtered('furniture_lane_handoff_id')
        if linked and not self.env.context.get(
            'furniture_internal_cancel_lane_handoff'
        ):
            raise AccessError(_(
                'إلغاء حركة التحويل يتم فقط من إلغاء أمر المرحلة التابع.'
            ))
        return super()._action_cancel()

    def unlink(self):
        if self.filtered(lambda move: (
            move.furniture_lane_handoff_id
            or move.furniture_lane_handoff_role
        )):
            raise AccessError(_('لا يمكن حذف حركة تحويل مرحلة مسجلة.'))
        return super().unlink()

    @api.model_create_multi
    def _furniture_internal_create_for_handoff(self, vals_list):
        allowed_roles = set(
            self.env['furniture.mrp.production']._furniture_lane_output_lanes().values()
        )
        for vals in vals_list:
            if vals.get('furniture_lane_handoff_role') not in allowed_roles:
                raise ValidationError(_('دور حركة التحويل غير صحيح.'))
            required_fields = {
                'product_id', 'product_uom', 'product_uom_qty',
                'location_id', 'location_dest_id', 'company_id',
                'origin',
                'furniture_source_production_line_id',
            }
            if required_fields - set(vals):
                raise ValidationError(_(
                    'هوية حركة تحويل المرحلة غير مكتملة.'
                ))
        records = self.sudo().with_context(
            furniture_pending_lane_handoff_link=True,
        )
        return super(StockMoveLaneHandoff, records).create(vals_list)

    def _furniture_internal_link_handoff(self, handoff):
        self.ensure_one()
        handoff.ensure_one()
        if handoff.transfer_move_id != self:
            raise ValidationError(_('الحركة ليست حركة التحويلة المحددة.'))
        if (
            self.furniture_lane_handoff_id
            and self.furniture_lane_handoff_id != handoff
        ):
            raise ValidationError(_('الحركة مرتبطة بتحويلة أخرى بالفعل.'))
        if self.furniture_lane_handoff_role != handoff.role:
            raise ValidationError(_('دور الحركة لا يطابق التحويلة.'))
        if self.furniture_lane_handoff_id == handoff:
            return True
        return super(StockMoveLaneHandoff, self.sudo()).write({
            'furniture_lane_handoff_id': handoff.id,
        })

    @api.constrains(
        'furniture_lane_handoff_id',
        'furniture_lane_handoff_role',
        'product_id',
        'product_uom',
        'product_uom_qty',
        'location_id',
        'location_dest_id',
        'company_id',
        'origin',
        'furniture_source_production_line_id',
        'furniture_source_production_line_ids',
    )
    def _check_lane_handoff_move_identity(self):
        for move in self:
            handoff = move.furniture_lane_handoff_id
            if not handoff:
                if (
                    move.furniture_lane_handoff_role
                    and not self.env.context.get(
                        'furniture_pending_lane_handoff_link'
                    )
                ):
                    raise ValidationError(_(
                        'حركة التحويل الداخلية يجب ربطها بسجل التحويل.'
                    ))
                continue
            output = handoff.output_id
            destination = handoff.downstream_production_id._get_production_location()
            if (
                move.furniture_lane_handoff_role != handoff.role
                or handoff.transfer_move_id != move
                or move.product_id != output.wip_product_id
                or move.product_uom != output.uom_id
                or float_compare(
                    move.product_uom_qty,
                    handoff.quantity,
                    precision_rounding=output.uom_id.rounding or 0.001,
                ) != 0
                or move.location_id != output.source_location_id
                or move.location_dest_id != destination
                or move.company_id != handoff.company_id
                or move.origin != handoff.downstream_production_id.name
                or move.furniture_source_production_line_id
                != output.production_line_id
                or (
                    move.furniture_source_production_line_ids
                    and output.production_line_id
                    not in move.furniture_source_production_line_ids
                )
            ):
                raise ValidationError(_('دور الحركة لا يطابق التحويلة.'))


class FurnitureMrpPackagingHandoff(models.Model):
    _inherit = 'furniture.mrp.packaging'

    def action_start(self):
        records = self.with_context(furniture_lane_handoff_actual_start=True)
        return super(FurnitureMrpPackagingHandoff, records).action_start()

    def action_approve_quality(self):
        acting_partner = self.env.user.partner_id
        result = super().action_approve_quality()
        default_finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        for stage_order in self:
            production = stage_order.production_order_id
            if (
                not production
                or production.production_lane != 'packaging'
                or stage_order.state != 'done'
                or production.state not in ('confirmed', 'in_production')
            ):
                continue
            finished_location = (
                production.location_dest_id or default_finished_location
            )
            if not finished_location:
                raise UserError(_(
                    'لا يوجد مخزن منتج تام لإغلاق أمر التغليف.'
                ))
            production_sudo = production.sudo()
            lines = production_sudo.production_line_ids.filtered(
                lambda line: line.active and line.product_id and line.product_qty > 0
            )
            all_finished = production_sudo._furniture_packaging_lines_fully_finished(
                lines,
                finished_location,
            )
            if lines and all_finished:
                # ``super`` above enforced the real operator's stage access.
                # Elevate only this bounded technical close after proving the
                # exact finished quantity for every line; retain real author.
                production_sudo.write({
                    'state': 'done',
                    'date_finish': fields.Datetime.now(),
                })
                production_sudo.message_post(
                    body=_(
                        'أُغلق أمر التغليف آليًا بعد دخول '
                        'كل الكميات مخزن المنتج التام.'
                    ),
                    author_id=acting_partner.id,
                )
        return result
