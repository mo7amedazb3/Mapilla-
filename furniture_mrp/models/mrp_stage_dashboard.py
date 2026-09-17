# -*- coding: utf-8 -*-

from datetime import datetime, time, timedelta
import json

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .mrp_production_order import (
    FURNITURE_STAGE_FIELD_MAP,
    FURNITURE_STAGE_SELECTION,
)


_FURNITURE_STAGE_DASHBOARD_LABELS = dict(FURNITURE_STAGE_SELECTION)
FURNITURE_STAGE_DASHBOARD_DISPLAY_CODES = (
    'priming',
    'painting',
    'carpentry',
    'bases',
    'finishing',
    'upholstery',
    'tailoring',
    'packaging',
)
FURNITURE_STAGE_DASHBOARD_SELECTION = tuple(
    (code, _FURNITURE_STAGE_DASHBOARD_LABELS[code])
    for code in FURNITURE_STAGE_DASHBOARD_DISPLAY_CODES
)
FURNITURE_STAGE_DASHBOARD_FIELD_MAP = {
    code: FURNITURE_STAGE_FIELD_MAP[code]
    for code, _label in FURNITURE_STAGE_DASHBOARD_SELECTION
}

FURNITURE_STAGE_DASHBOARD_ICONS = {
    'priming': 'fa fa-paint-brush',
    'painting': 'fa fa-tint',
    'carpentry': 'fa fa-cubes',
    'bases': 'fa fa-th-large',
    'finishing': 'fa fa-wrench',
    'tailoring': 'fa fa-cut',
    'upholstery': 'fa fa-cube',
    'packaging': 'fa fa-archive',
}

FURNITURE_STAGE_DASHBOARD_QUANTITY_FIELDS = (
    'planned_qty',
    'started_qty',
    'working_qty',
    'quality_qty',
    'completed_qty',
    'not_started_qty',
    'remaining_qty',
)

FURNITURE_STAGE_DASHBOARD_TRACKING_FIELDS = (
    'active_production_line_ids_data',
    'quality_production_line_ids_data',
    'completed_production_line_ids_data',
)

FURNITURE_STAGE_DASHBOARD_SUPERVISOR_GROUPS = {
    'priming': 'furniture_mrp.group_furniture_mrp_supervisor_priming',
    'painting': 'furniture_mrp.group_furniture_mrp_supervisor_painting',
    'carpentry': 'furniture_mrp.group_furniture_mrp_supervisor_carpentry',
    'bases': 'furniture_mrp.group_furniture_mrp_supervisor_bases',
    'finishing': 'furniture_mrp.group_furniture_mrp_supervisor_finishing',
    'tailoring': 'furniture_mrp.group_furniture_mrp_supervisor_tailoring',
    'upholstery': 'furniture_mrp.group_furniture_mrp_supervisor_upholstery',
    'packaging': 'furniture_mrp.group_furniture_mrp_supervisor_packaging',
}

# These five departments run their complete planned quantity as one batch.
# Their supervisor dashboard is deliberately a compact touch-first surface.
FURNITURE_STAGE_DASHBOARD_BATCH_CODES = (
    'priming',
    'painting',
    'carpentry',
    'bases',
    'finishing',
)

# These departments keep the normal production-order workflow.  Their compact
# supervisor cards are order-scoped (unlike the product batches above) and need
# the saved tailoring setup facts for each production line.
FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES = (
    'tailoring',
    'upholstery',
    'packaging',
)

# The stage plan is operational only after confirmation.  Finished production
# orders are historical and would otherwise keep inflating the live factory
# load forever; completed *stages* of an active order remain visible below.
FURNITURE_STAGE_DASHBOARD_EXCLUDED_PRODUCTION_STATES = (
    'draft',
    'done',
    'cancelled',
)


class FurnitureMrpProductionStageDashboard(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _stage_dashboard_lane_output_covers_lines(self, production, lines):
        """True when a stage-less lane order is already physical stock.

        Scenario imports and historical migrations can seed an auditable lane
        output directly, without fabricating completed stage-order records.
        Such a source is stock, not pending shop-floor work. Treating its
        route as unstarted exposes a bogus start button which then correctly
        fails the upstream hand-off gate (for example finish without frame).
        """
        lines = lines.filtered(lambda line: (
            line.active
            and line.product_id
            and (line.product_qty or 0.0) > 0.0
        ))
        # The dashboard already scoped production/lines to the caller's stage
        # and company. Read the technical stock ledger only for this boolean;
        # supervisors must not gain general access to lane outputs or costs.
        outputs = production.sudo().lane_output_ids
        if not lines or not outputs:
            return False
        completed_outputs = outputs.filtered(
            lambda output: (
                output.production_line_id in lines
                and output.origin_receipt_move_id.state == 'done'
            )
        )
        if not completed_outputs:
            return False
        for line in lines:
            line_outputs = completed_outputs.filtered(
                lambda output: output.production_line_id == line
            )
            ready_quantity = sum(
                output.uom_id._compute_quantity(
                    output.qty_ready,
                    line.product_uom_id or line.product_id.uom_id,
                    round=False,
                )
                for output in line_outputs
            )
            rounding = (
                line.product_uom_id.rounding
                or line.product_id.uom_id.rounding
                or 0.001
            )
            if float_compare(
                ready_quantity,
                line.product_qty,
                precision_rounding=rounding,
            ) < 0:
                return False
        return True

    @api.model
    def _stage_dashboard_access_profile(self):
        """Return the caller's server-enforced dashboard stage scope."""
        user = self.env.user
        is_manager = user._is_admin() or user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        )
        is_supervisor = user.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor'
        )
        if not is_manager and not is_supervisor:
            raise AccessError(_(
                'لوحة مراحل المصنع متاحة لمدير المصنع ومشرفي المراحل فقط.'
            ))

        all_codes = [
            code for code, _label in FURNITURE_STAGE_DASHBOARD_SELECTION
        ]
        allowed_codes = all_codes if is_manager else [
            code
            for code in all_codes
            if user.has_group(
                FURNITURE_STAGE_DASHBOARD_SUPERVISOR_GROUPS[code]
            )
        ]
        return {
            'is_manager': is_manager,
            'is_supervisor': is_supervisor and not is_manager,
            'allowed_stage_codes': allowed_codes,
            'has_batch_supervisor_stage': bool(
                set(allowed_codes) & set(FURNITURE_STAGE_DASHBOARD_BATCH_CODES)
            ) and not is_manager,
        }

    @api.model
    def _stage_dashboard_check_stage_access(
        self, stage_code, batch_only=False,
    ):
        if stage_code not in FURNITURE_STAGE_DASHBOARD_FIELD_MAP:
            raise ValidationError(_('مرحلة الإنتاج المطلوبة غير صحيحة.'))
        profile = self._stage_dashboard_access_profile()
        if stage_code not in profile['allowed_stage_codes']:
            raise AccessError(_(
                'غير مسموح لك بمتابعة أو تشغيل مرحلة %s.'
            ) % dict(FURNITURE_STAGE_DASHBOARD_SELECTION)[stage_code])
        if batch_only and stage_code not in FURNITURE_STAGE_DASHBOARD_BATCH_CODES:
            raise ValidationError(_(
                'تشغيل الدفعة المباشر متاح للتقديم وتصنيع الدهانات '
                'والتجميع والقواعد والتجهيز فقط.'
            ))
        return profile

    def _stage_dashboard_stage_order(self, stage_code):
        self.ensure_one()
        _use_field, order_field, _state_field = (
            FURNITURE_STAGE_DASHBOARD_FIELD_MAP[stage_code]
        )
        return self[order_field]

    def _stage_dashboard_assign_current_foreman(
        self, stage_order, stage_code,
    ):
        """Use the linked stage supervisor when a new row has no foreman."""
        self.ensure_one()
        if stage_order.foreman_id:
            return
        # A stage supervisor normally has public-employee access only.  The
        # dashboard has already validated the current user's stage group, so a
        # narrow sudo lookup of that same linked employee is safe here.
        employee = self.env['hr.employee'].sudo().search([
            ('active', '=', True),
            ('user_id', '=', self.env.user.id),
            ('furniture_mrp_role', '=', 'supervisor'),
            ('furniture_mrp_supervisor_stage_ids.code', '=', stage_code),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if employee:
            stage_order.foreman_id = employee

    @api.model
    def _stage_dashboard_batch_state(self, stage_order):
        if not stage_order or stage_order.state == 'pending':
            return 'not_started', _('لم يبدأ')
        if stage_order.state in ('in_progress', 'quality_check'):
            return 'in_progress', _('قيد التشغيل')
        if stage_order.state == 'done':
            return 'completed', _('مكتمل')
        return 'not_started', _('لم يبدأ')

    @api.model
    def get_overdue_dashboard_orders(self, limit=100):
        """Return orders whose planned finish passed and remain open."""
        profile = self._stage_dashboard_access_profile()
        if profile['has_batch_supervisor_stage']:
            return {'count': 0, 'orders': [], 'limited': False}
        try:
            normalized_limit = max(1, min(int(limit or 100), 200))
        except (TypeError, ValueError):
            normalized_limit = 100

        now = fields.Datetime.now()
        domain = [
            ('company_id', '=', self.env.company.id),
            ('date_planned_finish', '!=', False),
            ('date_planned_finish', '<=', now),
            ('state', 'not in', ('done', 'cancelled')),
        ]
        total_count = self.search_count(domain)
        productions = self.search(
            domain,
            order='date_planned_finish asc, dashboard_sequence asc, id asc',
            limit=normalized_limit,
        )
        state_labels = dict(
            self._fields['state']._description_selection(self.env)
        )
        orders = []
        for production in productions:
            production_lines = production.production_line_ids.filtered('active')
            furniture_model = (
                production.furniture_order_model_id
                or production_lines.mapped('furniture_order_model_id')[:1]
            )
            elapsed_seconds = max(
                0,
                int((now - production.date_planned_finish).total_seconds()),
            )
            elapsed_days = elapsed_seconds // 86400
            elapsed_hours = elapsed_seconds // 3600
            elapsed_minutes = max(1, elapsed_seconds // 60)
            if elapsed_days:
                overdue_label = _(
                    'متأخر %(count)s يوم', count=elapsed_days,
                )
            elif elapsed_hours:
                overdue_label = _(
                    'متأخر %(count)s ساعة', count=elapsed_hours,
                )
            elif elapsed_seconds >= 60:
                overdue_label = _(
                    'متأخر %(count)s دقيقة', count=elapsed_minutes,
                )
            else:
                overdue_label = _('موعده الآن')

            buyers = (
                production.buyer_partner_id
                | production.buyer_partner_ids
                | production_lines.mapped('buyer_partner_id')
            )
            beneficiaries = (
                production.beneficiary_partner_id
                | production.beneficiary_partner_ids
                | production_lines.mapped('beneficiary_partner_id')
            )
            orders.append({
                'id': production.id,
                'name': production.name,
            'delivery_notes': production._delivery_set_notes_payload(),
                'product_summary': (
                    production.header_product_summary
                    or production.product_id.display_name
                    or _('بدون أصناف')
                ),
                'model_name': (
                    furniture_model.display_name if furniture_model else False
                ),
                'buyer_summary': (
                    '، '.join(buyers.mapped('display_name')) or False
                ),
                'beneficiary_summary': (
                    '، '.join(beneficiaries.mapped('display_name')) or False
                ),
                'date_planned_finish': fields.Datetime.to_string(
                    production.date_planned_finish
                ),
                'overdue_label': overdue_label,
                'state': production.state,
                'state_label': state_labels.get(
                    production.state, production.state,
                ),
                'priority': production.priority,
            })
        return {
            'count': total_count,
            'orders': orders,
            'limited': total_count > len(orders),
        }

    dashboard_stage_label = fields.Char(
        string='مرحلة لوحة التحكم',
        compute='_compute_dashboard_stage_metrics',
        store=False,
    )
    dashboard_stage_state_label = fields.Char(
        string='حالة المرحلة في لوحة التحكم',
        compute='_compute_dashboard_stage_metrics',
        store=False,
    )
    dashboard_stage_planned_qty = fields.Float(
        string='الكمية المخططة للمرحلة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_started_qty = fields.Float(
        string='الكمية التي بدأت المرحلة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_working_qty = fields.Float(
        string='الكمية الجاري تشغيلها',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_quality_qty = fields.Float(
        string='الكمية تحت الجودة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_completed_qty = fields.Float(
        string='الكمية المكتملة في المرحلة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_not_started_qty = fields.Float(
        string='الكمية التي لم تبدأ المرحلة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_remaining_qty = fields.Float(
        string='الكمية المتبقية في المرحلة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 3),
        store=False,
    )
    dashboard_stage_progress = fields.Float(
        string='نسبة تقدم المرحلة',
        compute='_compute_dashboard_stage_metrics',
        digits=(16, 2),
        store=False,
    )

    @api.model
    def _stage_dashboard_line_quantity(self, lines):
        """Return the non-negative quantity of a unique production-line set."""
        return round(sum(max(line.product_qty or 0.0, 0.0) for line in lines), 3)

    @api.model
    def _stage_dashboard_tracking_map(self, stage_orders):
        """Read all persisted line sets with one existence query.

        ``_get_stage_line_ids_data`` correctly validates one JSON field, but
        calling it three times for every order turns a busy dashboard into
        hundreds of tiny ``exists`` queries.  This batch helper parses the
        already-prefetched text values, validates all referenced line ids once,
        then rebuilds the same recordsets per stage order.
        """
        raw_map = {}
        all_line_ids = set()
        for stage_order in stage_orders:
            if not stage_order:
                continue
            key = (stage_order._name, stage_order.id)
            field_ids = {}
            for field_name in FURNITURE_STAGE_DASHBOARD_TRACKING_FIELDS:
                try:
                    raw_ids = json.loads(stage_order[field_name] or '[]')
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_ids = []
                if not isinstance(raw_ids, list):
                    raw_ids = []
                line_ids = []
                for raw_id in raw_ids:
                    raw_text = str(raw_id)
                    if raw_text.isdigit():
                        line_ids.append(int(raw_id))
                field_ids[field_name] = line_ids
                all_line_ids.update(line_ids)
            raw_map[key] = field_ids

        Line = self.env['furniture.mrp.production.line']
        valid_line_ids = set(Line.browse(all_line_ids).exists().ids)
        return {
            key: {
                field_name: Line.browse(
                    line_id
                    for line_id in line_ids
                    if line_id in valid_line_ids
                )
                for field_name, line_ids in field_ids.items()
            }
            for key, field_ids in raw_map.items()
        }

    @api.model
    def _stage_dashboard_metrics(
        self,
        production,
        stage_code,
        stage_order,
        tracking_lines=False,
        line_buckets=False,
    ):
        """Build the authoritative quantity buckets for one production/stage.

        The stage state is deliberately not used to decide whether work started.
        A partially completed stage may legitimately return to ``pending`` while
        the remaining batch waits for a later transfer.  The three persisted
        tracking sets are the source of truth for actual progress.
        """
        line_buckets = line_buckets or self._stage_dashboard_line_buckets(
            production,
            stage_code,
            stage_order,
            tracking_lines=tracking_lines,
        )
        planned_scope_lines = line_buckets['planned_scope_lines']
        started_lines = line_buckets['started_lines']
        working_lines = line_buckets['working_lines']
        quality_lines = line_buckets['quality_lines']
        completed_lines = line_buckets['completed_lines']
        not_started_lines = line_buckets['not_started_lines']

        planned_qty = self._stage_dashboard_line_quantity(planned_scope_lines)
        started_qty = self._stage_dashboard_line_quantity(started_lines)
        working_qty = self._stage_dashboard_line_quantity(working_lines)
        quality_qty = self._stage_dashboard_line_quantity(quality_lines)
        completed_qty = self._stage_dashboard_line_quantity(completed_lines)
        not_started_qty = self._stage_dashboard_line_quantity(not_started_lines)
        # ``working_qty`` deliberately includes pieces waiting for/in quality:
        # they already started this stage and have not exited it yet.  Quality
        # remains a useful subset KPI, not a fourth additive bucket.  This
        # keeps the operational contract exact for every order and product:
        # planned = working + completed + remaining (not started).
        remaining_qty = not_started_qty
        progress = (
            round(min(100.0, (completed_qty / planned_qty) * 100.0), 2)
            if planned_qty > 0.0
            else 0.0
        )

        uoms = planned_scope_lines.mapped('product_uom_id')
        return {
            'planned_qty': planned_qty,
            'started_qty': started_qty,
            'working_qty': working_qty,
            'quality_qty': quality_qty,
            'completed_qty': completed_qty,
            'not_started_qty': not_started_qty,
            'remaining_qty': remaining_qty,
            'progress': progress,
            'uom': uoms.display_name if len(uoms) == 1 else False,
            'uoms': sorted(uoms.mapped('display_name')),
            'has_mixed_uom': len(uoms) > 1,
            'has_started': started_qty > 0.0,
            'is_complete': (
                planned_qty > 0.0 and completed_qty >= planned_qty
            ),
        }

    @api.model
    def _stage_dashboard_line_buckets(
        self,
        production,
        stage_code,
        stage_order,
        tracking_lines=False,
    ):
        """Return the authoritative line sets behind one stage dashboard row."""
        planned_lines = production.production_line_ids.filtered(
            lambda line: (
                line.product_id
                and (line.product_qty or 0.0) > 0.0
                and stage_code in line._selected_stage_codes()
            )
        )
        if tracking_lines is False:
            if not stage_order:
                tracking_lines = {}
            else:
                tracking_lines = {
                    field_name: stage_order._get_stage_line_ids_data(field_name)
                    for field_name in FURNITURE_STAGE_DASHBOARD_TRACKING_FIELDS
                }
        empty_lines = self.env['furniture.mrp.production.line']

        def belongs_to_production(line):
            return (
                line.product_id
                and line.production_id == production
                and (line.product_qty or 0.0) > 0.0
            )

        active_lines = tracking_lines.get(
            'active_production_line_ids_data', empty_lines
        ).filtered(belongs_to_production)
        quality_lines = tracking_lines.get(
            'quality_production_line_ids_data', empty_lines
        ).filtered(belongs_to_production)
        completed_lines = tracking_lines.get(
            'completed_production_line_ids_data', empty_lines
        ).filtered(belongs_to_production)

        # Recordset unions keep split/parallel tracking deterministic and avoid
        # counting a line twice when it is present in more than one JSON set.
        started_lines = active_lines | quality_lines | completed_lines
        # Every line which started and has not completed is still operationally
        # "working", including the quality-check subset.  This is the meaning
        # used by the user-facing equation and avoids losing quality quantities
        # between planned and completed.
        working_lines = started_lines - completed_lines
        quality_lines = quality_lines - completed_lines

        # A tracked line remains operational evidence even if its route was
        # edited after starting.  Including it in the denominator prevents a
        # real partial batch from disappearing from the dashboard.
        planned_scope_lines = planned_lines | started_lines
        not_started_lines = planned_lines - started_lines
        return {
            'planned_scope_lines': planned_scope_lines,
            'started_lines': started_lines,
            'working_lines': working_lines,
            'quality_lines': quality_lines,
            'completed_lines': completed_lines,
            'not_started_lines': not_started_lines,
        }

    @api.model
    def _stage_dashboard_tailoring_material_items(
        self, production_line, material_kind,
    ):
        """Return the saved/default setup facts without exposing binary data.

        ``_tailoring_material_editor_values`` is already the authoritative
        resolver used by the manager setup screen: it returns persisted manual
        allocations after editing and recipe defaults before the first edit.
        Keeping this dashboard adapter read-only prevents a supervisor payload
        from becoming a second material-calculation implementation.
        """
        size_labels = dict(
            self.env['furniture.mrp.tailoring.material.allocation']
            ._fields['piece_size']._description_selection(self.env)
        )
        items = []
        for values in production_line._tailoring_material_editor_values(
            material_kind
        ):
            product = self.env['product.product'].browse(
                values.get('product_id')
            ).exists()
            product_uom = self.env['uom.uom'].browse(
                values.get('product_uom_id')
            ).exists()
            if not product:
                continue
            items.append({
                'product_id': product.id,
                'product_name': product.display_name,
                'qty': round(max(values.get('qty') or 0.0, 0.0), 3),
                'uom_id': product_uom.id or False,
                'uom': product_uom.display_name if product_uom else False,
                'piece_size': (
                    size_labels.get(
                        values.get('piece_size'),
                        values.get('piece_size'),
                    )
                    if material_kind == 'takawe'
                    else False
                ),
            })
        return items

    @api.model
    def _stage_dashboard_tailoring_line_payload(self, production_line):
        """Build the touch-card fabric/takawe/notes payload for one line."""
        fabric_items = self._stage_dashboard_tailoring_material_items(
            production_line, 'fabric'
        )
        takawe_items = self._stage_dashboard_tailoring_material_items(
            production_line, 'takawe'
        )

        takawe_sizes = list(dict.fromkeys(
            item['piece_size']
            for item in takawe_items
            if item.get('piece_size')
        ))
        return {
            'fabric_items': fabric_items,
            'fabric_summary': self._stage_dashboard_tailoring_items_summary(
                production_line.production_id, fabric_items,
            ),
            'takawe_items': takawe_items,
            'takawe_summary': self._stage_dashboard_tailoring_items_summary(
                production_line.production_id, takawe_items,
            ),
            'takawe_piece_size': '، '.join(takawe_sizes) or False,
            'notes': (production_line.kit_piece_note or '').strip() or False,
        }

    @api.model
    def _stage_dashboard_tailoring_items_summary(self, production, items):
        """Format the already-scaled material totals shown on a product row."""
        return ' | '.join(
            '%(product)s · %(quantity)s%(uom)s%(size)s' % {
                'product': item['product_name'],
                'quantity': production._format_dimension_value(item['qty']),
                'uom': ' %s' % item['uom'] if item['uom'] else '',
                'size': (
                    ' · %s %s' % (_('مقاس'), item['piece_size'])
                    if item.get('piece_size')
                    else ''
                ),
            }
            for item in items
        ) or False

    @api.model
    def _stage_dashboard_merge_tailoring_material_items(
        self, production, target_items, source_items,
    ):
        """Sum identical saved material facts when visible product rows merge."""
        merged = {
            (
                item['product_id'], item['uom_id'] or False,
                item.get('piece_size') or False,
            ): dict(item)
            for item in target_items
        }
        order = list(merged)
        for item in source_items:
            key = (
                item['product_id'], item['uom_id'] or False,
                item.get('piece_size') or False,
            )
            if key not in merged:
                merged[key] = dict(item)
                order.append(key)
                continue
            merged[key]['qty'] = round(
                max(merged[key].get('qty') or 0.0, 0.0)
                + max(item.get('qty') or 0.0, 0.0),
                3,
            )
        items = [merged[key] for key in order]
        return (
            items,
            self._stage_dashboard_tailoring_items_summary(production, items),
        )

    @api.model
    def _stage_dashboard_tailoring_line_signature(self, payload):
        """Keep visually different setup rows separate while summing twins."""
        def item_signature(items):
            return tuple(
                (
                    item['product_id'], item['qty'],
                    item['uom_id'] or False,
                    item.get('piece_size') or False,
                )
                for item in items
            )

        return (
            item_signature(payload['fabric_items']),
            item_signature(payload['takawe_items']),
            payload['takawe_piece_size'] or False,
            payload['notes'] or False,
        )

    @api.model
    def _stage_dashboard_product_payloads(
        self,
        line_buckets,
        stage_code=False,
        include_order_supervisor_details=False,
        startable_lines=False,
    ):
        """Group stage lines for a compact product-first operational report.

        Kit instances and split batches remain independent in production and
        inventory.  The dashboard only groups rows that share the same visible
        product facts, then sums their mutually exclusive stage quantities.
        """
        planned_lines = line_buckets['planned_scope_lines']
        shortage_rows_by_line = {}
        if stage_code == 'tailoring' and planned_lines:
            production = planned_lines[:1].production_id
            tailoring_order = production.tailoring_order_id
            if tailoring_order:
                for row in tailoring_order._material_shortage_rows():
                    shortage_rows_by_line.setdefault(
                        row['production_line_id'], [],
                    ).append(row)
        bucket_ids = {
            bucket_name: set(lines.ids)
            for bucket_name, lines in line_buckets.items()
            if bucket_name != 'planned_scope_lines'
        }
        startable_line_ids = set(
            (startable_lines or self.env['furniture.mrp.production.line']).ids
        )
        grouped = {}
        for line in planned_lines.sorted(lambda item: (item.sequence, item.id)):
            model = line.furniture_order_model_id
            buyer = line.buyer_partner_id
            beneficiary = line.beneficiary_partner_id
            kit_bom = line.kit_bom_id
            dimension_label = line.dimension_label or False
            tailoring_payload = (
                self._stage_dashboard_tailoring_line_payload(line)
                if include_order_supervisor_details
                else {}
            )
            key = (
                line.product_id.id,
                model.id or False,
                dimension_label,
                buyer.id or False,
                beneficiary.id or False,
                kit_bom.id or False,
                line.bom_id.id or False,
                line.product_uom_id.id or False,
                (
                    self._stage_dashboard_tailoring_line_signature(
                        tailoring_payload
                    )
                    if tailoring_payload else False
                ),
            )
            payload = grouped.get(key)
            if payload is None:
                payload = {
                    'production_line_id': line.id,
                    'production_line_ids': [],
                    'startable_production_line_ids': [],
                    'product_id': line.product_id.id,
                    'product_name': line.product_id.display_name,
                    'model_id': model.id or False,
                    'model_name': model.display_name if model else False,
                    'dimension_label': dimension_label,
                    'buyer_id': buyer.id or False,
                    'buyer_name': buyer.display_name if buyer else False,
                    'beneficiary_id': beneficiary.id or False,
                    'beneficiary_name': (
                        beneficiary.display_name if beneficiary else False
                    ),
                    'kit_id': kit_bom.id or False,
                    'kit_name': kit_bom.display_name if kit_bom else False,
                    'bom_id': line.bom_id.id or False,
                    'bom_name': (
                        line.bom_id.display_name if line.bom_id else False
                    ),
                    'route_summary': line.stage_summary or False,
                    'uom_id': line.product_uom_id.id or False,
                    'uom': line.product_uom_id.display_name or False,
                    'planned_qty': 0.0,
                    'started_qty': 0.0,
                    'working_qty': 0.0,
                    'quality_qty': 0.0,
                    'completed_qty': 0.0,
                    'not_started_qty': 0.0,
                    'remaining_qty': 0.0,
                    'has_material_shortage': False,
                    'material_shortage_details': [],
                    **tailoring_payload,
                }
                grouped[key] = payload
            elif tailoring_payload:
                for item_field, summary_field in (
                    ('fabric_items', 'fabric_summary'),
                    ('takawe_items', 'takawe_summary'),
                ):
                    items, summary = (
                        self._stage_dashboard_merge_tailoring_material_items(
                            line.production_id,
                            payload[item_field],
                            tailoring_payload[item_field],
                        )
                    )
                    payload[item_field] = items
                    payload[summary_field] = summary
            for shortage_row in shortage_rows_by_line.get(line.id, []):
                payload['has_material_shortage'] = True
                detail = _('%(material)s: ناقص %(quantity)s %(uom)s') % {
                    'material': shortage_row['material_label'],
                    'quantity': line.production_id._format_dimension_value(
                        shortage_row['shortage_qty']
                    ),
                    'uom': shortage_row['uom_label'],
                }
                if detail not in payload['material_shortage_details']:
                    payload['material_shortage_details'].append(detail)
            quantity = max(line.product_qty or 0.0, 0.0)
            payload['production_line_ids'].append(line.id)
            if line.id in startable_line_ids:
                payload['startable_production_line_ids'].append(line.id)
            payload['planned_qty'] += quantity
            for payload_field, bucket_name in (
                ('started_qty', 'started_lines'),
                ('working_qty', 'working_lines'),
                ('quality_qty', 'quality_lines'),
                ('completed_qty', 'completed_lines'),
                ('not_started_qty', 'not_started_lines'),
            ):
                if line.id in bucket_ids[bucket_name]:
                    payload[payload_field] += quantity

        product_payloads = []
        for payload in grouped.values():
            for field_name in FURNITURE_STAGE_DASHBOARD_QUANTITY_FIELDS:
                if field_name == 'remaining_qty':
                    payload[field_name] = payload['not_started_qty']
                payload[field_name] = round(payload[field_name], 3)
            payload['material_shortage_details'] = '\n'.join(
                payload['material_shortage_details']
            ) or False
            payload['can_start_stage'] = bool(
                payload['startable_production_line_ids']
            )
            product_payloads.append(payload)
        return product_payloads

    @api.model
    def _stage_dashboard_state_label(self, stage_order):
        if not stage_order or not stage_order.state:
            return _('لم يبدأ')
        if (
            stage_order._name == 'furniture.mrp.tailoring'
            and stage_order.has_material_shortage
        ):
            return _('مواد ناقصة')
        selection = stage_order._fields['state']._description_selection(
            stage_order.env
        )
        return dict(selection).get(stage_order.state, stage_order.state)

    @api.depends_context('furniture_dashboard_stage')
    def _compute_dashboard_stage_metrics(self):
        stage_code = self.env.context.get('furniture_dashboard_stage')
        stage_label = dict(FURNITURE_STAGE_DASHBOARD_SELECTION).get(
            stage_code, False,
        )
        field_map = FURNITURE_STAGE_DASHBOARD_FIELD_MAP.get(stage_code)
        metric_field_map = {
            'dashboard_stage_planned_qty': 'planned_qty',
            'dashboard_stage_started_qty': 'started_qty',
            'dashboard_stage_working_qty': 'working_qty',
            'dashboard_stage_quality_qty': 'quality_qty',
            'dashboard_stage_completed_qty': 'completed_qty',
            'dashboard_stage_not_started_qty': 'not_started_qty',
            'dashboard_stage_remaining_qty': 'remaining_qty',
            'dashboard_stage_progress': 'progress',
        }
        if field_map:
            _use_field, order_field, _state_field = field_map
            stage_orders = [production[order_field] for production in self]
        else:
            stage_orders = []
        tracking_map = self._stage_dashboard_tracking_map(stage_orders)
        for production in self:
            production.dashboard_stage_label = stage_label
            production.dashboard_stage_state_label = False
            for field_name in metric_field_map:
                production[field_name] = 0.0
            if not field_map:
                continue
            stage_order = production[order_field]
            metrics = self._stage_dashboard_metrics(
                production,
                stage_code,
                stage_order,
                tracking_lines=(
                    tracking_map.get((stage_order._name, stage_order.id), {})
                    if stage_order
                    else {}
                ),
            )
            production.dashboard_stage_state_label = (
                self._stage_dashboard_state_label(stage_order)
            )
            for field_name, metric_name in metric_field_map.items():
                production[field_name] = metrics[metric_name]

    @api.model
    def _stage_dashboard_order_payload(
        self,
        production,
        stage_code,
        stage_label,
        stage_order,
        metrics,
        line_buckets,
        include_order_supervisor_details=False,
        include_order_display_details=False,
    ):
        responsible = production.responsible_id
        # Resolve only the relation ids with elevated rights.  Accessing a
        # private ``hr.employee`` many2many as a non-HR supervisor already
        # triggers Odoo's active-record prefetch before we can switch to the
        # public model below.
        private_stage_order = stage_order.sudo() if stage_order else False
        foreman = private_stage_order.foreman_id if private_stage_order else False
        workers = (
            private_stage_order.worker_ids
            if private_stage_order
            else self.env['hr.employee']
        )
        # A production supervisor is allowed to see employee public profiles,
        # but not the private ``hr.employee`` model.  Reading ``display_name``
        # from the private records makes Odoo prefetch our private employee
        # classification fields and rejects the whole dashboard RPC.  Resolve
        # the same employee ids through the public profile model; the payload
        # still exposes only ids and display names and keeps its record rules.
        public_foreman = self.env['hr.employee.public'].search([
            ('id', 'in', foreman.ids),
        ]) if foreman else self.env['hr.employee.public']
        public_workers = self.env['hr.employee.public'].search([
            ('id', 'in', workers.ids),
        ]) if workers else self.env['hr.employee.public']
        public_worker_by_id = {
            employee.id: employee for employee in public_workers
        }
        foreman_name = (
            public_foreman.display_name
            or (foreman.sudo().display_name if foreman else False)
        )
        worker_names = [
            (
                public_worker_by_id.get(worker.id).display_name
                if public_worker_by_id.get(worker.id)
                and public_worker_by_id.get(worker.id).display_name
                else worker.sudo().display_name
            )
            for worker in workers
        ]
        production_lines = production.production_line_ids.filtered('active')
        line_models = production_lines.mapped('furniture_order_model_id')
        furniture_model = (
            production.furniture_order_model_id
            or line_models[:1]
        )
        buyers = (
            production.buyer_partner_id
            | production.buyer_partner_ids
            | production_lines.mapped('buyer_partner_id')
        )
        beneficiaries = (
            production.beneficiary_partner_id
            | production.beneficiary_partner_ids
            | production_lines.mapped('beneficiary_partner_id')
        )
        batch_state, batch_state_label = self._stage_dashboard_batch_state(
            stage_order
        )
        is_order_supervisor_stage = bool(
            include_order_supervisor_details
            and
            stage_code
            in FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES
        )
        # Share the rich order presentation without enabling supervisor actions
        # or changing the manager's stage/quantity dashboard mode.
        include_card_details = bool(
            include_order_display_details or is_order_supervisor_stage
        )
        advance_release_stage = (
            self.env['furniture.mrp.advance.material.release']
            .find_active_stage_release(production, stage_code)
            if is_order_supervisor_stage else False
        )
        if stage_order:
            store_request_state = stage_order.store_request_state
        elif advance_release_stage:
            store_request_state = (
                'approved'
                if (
                    advance_release_stage.state in ('issued', 'started')
                    and advance_release_stage.receipt_confirmed
                )
                else 'awaiting_receipt'
                if advance_release_stage.state in ('issued', 'started')
                else 'pending'
            )
        else:
            store_request_state = 'none'
        has_order_image = bool(
            include_card_details
            and (
                production.tailoring_set_image_token
                or production.with_context(
                    bin_size=True
                ).tailoring_set_image_1920
            )
        )
        if not stage_order:
            start_button_label = _('تجهيز المرحلة وطلب الخامات')
        elif store_request_state == 'pending':
            start_button_label = _('بانتظار اعتماد الخامات')
        elif store_request_state == 'awaiting_receipt':
            start_button_label = _('استلام الخامات')
        else:
            start_button_label = _('بدء المرحلة')
        startable_product_lines = self.env[
            'furniture.mrp.production.line'
        ]
        active_product_lines = self.env[
            'furniture.mrp.production.line'
        ]
        if (
            is_order_supervisor_stage
            and stage_order
            and stage_order.state in ('pending', 'in_progress')
            and store_request_state == 'approved'
        ):
            startable_product_lines = (
                production._get_stage_pending_start_line_candidates(
                    stage_order, stage_code,
                )
            )
        if is_order_supervisor_stage and stage_order:
            active_product_lines = stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ).filtered(lambda line: (
                line.active
                and line.production_id == production
                and stage_code in line._selected_stage_codes()
            ))
        return {
            'id': production.id,
            'name': production.name,
            'delivery_notes': production._delivery_set_notes_payload(),
            'model_id': furniture_model.id or False,
            'model_name': (
                furniture_model.display_name if furniture_model else False
            ),
            'buyer_ids': buyers.ids,
            'buyer_names': buyers.mapped('display_name'),
            'buyer_summary': '، '.join(buyers.mapped('display_name')) or False,
            'beneficiary_ids': beneficiaries.ids,
            'beneficiary_names': beneficiaries.mapped('display_name'),
            'beneficiary_summary': (
                '، '.join(beneficiaries.mapped('display_name')) or False
            ),
            'stage_code': stage_code,
            'stage_label': stage_label,
            'stage_order_model': stage_order._name if stage_order else False,
            'stage_order_id': stage_order.id if stage_order else False,
            'stage_order_name': stage_order.name if stage_order else False,
            'state': stage_order.state if stage_order else 'not_started',
            'state_label': self._stage_dashboard_state_label(stage_order),
            'batch_state': batch_state,
            'batch_state_label': batch_state_label,
            'store_request_state': store_request_state,
            'start_button_label': start_button_label,
            'can_request_materials': bool(
                is_order_supervisor_stage
                and (
                    (
                        not stage_order
                        and store_request_state
                        in ('none', 'rejected', 'cancelled')
                        and production._can_start_stage_code(stage_code)
                    )
                    or (
                        stage_order
                        and stage_order.state == 'pending'
                        and store_request_state
                        in ('none', 'rejected', 'cancelled')
                    )
                )
            ),
            'can_start_stage': bool(startable_product_lines),
            'can_finish_stage': bool(
                active_product_lines
                and stage_order.state in ('in_progress', 'quality_check')
            ),
            'can_receive_materials': bool(
                is_order_supervisor_stage
                and store_request_state == 'awaiting_receipt'
            ),
            'can_open_material_request': bool(
                is_order_supervisor_stage
                and store_request_state
                in ('pending', 'approved', 'awaiting_receipt')
            ),
            'has_order_image': has_order_image,
            'order_image_url': (
                '/web/image/furniture.mrp.production/%s/'
                'tailoring_set_image_1920/256x256' % production.id
                if has_order_image else False
            ),
            'order_image_preview_url': (
                '/web/image/furniture.mrp.production/%s/'
                'tailoring_set_image_1920' % production.id
                if has_order_image else False
            ),
            'can_start_batch': bool(
                stage_code in FURNITURE_STAGE_DASHBOARD_BATCH_CODES
                and (not stage_order or stage_order.state == 'pending')
            ),
            'can_finish_batch': bool(
                stage_code in FURNITURE_STAGE_DASHBOARD_BATCH_CODES
                and stage_order
                and stage_order.state in ('in_progress', 'quality_check')
            ),
            'priority': production.priority,
            'dashboard_sequence': production.dashboard_sequence,
            'product_summary': (
                (stage_order.production_product_summary if stage_order else False)
                or production.header_product_summary
                or production.display_name
            ),
            'product_count': production.production_line_count,
            'product_lines': self._stage_dashboard_product_payloads(
                line_buckets,
                stage_code=stage_code,
                include_order_supervisor_details=include_card_details,
                startable_lines=startable_product_lines,
            ),
            'responsible_id': responsible.id or False,
            'responsible_name': responsible.display_name if responsible else False,
            'foreman_id': public_foreman.id if public_foreman else False,
            'foreman_name': foreman_name,
            'worker_ids': public_workers.ids,
            'worker_names': worker_names,
            'company_id': production.company_id.id,
            'company_name': production.company_id.display_name,
            'date_planned_start': fields.Datetime.to_string(
                production.date_planned_start
            ) if production.date_planned_start else False,
            'date_planned_finish': fields.Datetime.to_string(
                production.date_planned_finish
            ) if production.date_planned_finish else False,
            'stage_date_start': fields.Datetime.to_string(
                stage_order.date_start
            ) if stage_order and stage_order.date_start else False,
            'stage_date_finish': fields.Datetime.to_string(
                stage_order.date_finish
            ) if stage_order and stage_order.date_finish else False,
            **{
                field_name: metrics[field_name]
                for field_name in FURNITURE_STAGE_DASHBOARD_QUANTITY_FIELDS
            },
            'progress': metrics['progress'],
            'uom': metrics['uom'],
            'uoms': metrics['uoms'],
            'has_mixed_uom': metrics['has_mixed_uom'],
            'has_material_shortage': bool(
                stage_code == 'tailoring'
                and stage_order
                and stage_order.has_material_shortage
            ),
            'material_shortage_details': (
                stage_order.material_shortage_details
                if stage_code == 'tailoring' and stage_order else False
            ),
        }

    @api.model
    def get_stage_dashboard_data(
        self,
        stage_code=False,
        date_from=False,
        date_to=False,
    ):
        """Return the confirmed production plan for every active stage.

        The eight active stage summaries are always returned.  Their ``orders`` count
        includes confirmed/in-production orders whose product lines select the
        stage, even before a stage record is created.  Completed stages of an
        active production remain part of the plan.  Searches and record reads
        intentionally use the caller's environment (no ``sudo``), so regular
        model ACLs, record rules and the allowed-company context remain
        effective.
        """
        profile = self._stage_dashboard_access_profile()
        allowed_stage_codes = profile['allowed_stage_codes']
        allowed_stage_selection = tuple(
            (code, label)
            for code, label in FURNITURE_STAGE_DASHBOARD_SELECTION
            if code in allowed_stage_codes
        )
        if not allowed_stage_selection:
            raise AccessError(_(
                'حساب المشرف غير مربوط بأي مرحلة إنتاج. راجع صفحة الموظف.'
            ))
        stage_labels = dict(allowed_stage_selection)
        if stage_code and stage_code not in FURNITURE_STAGE_DASHBOARD_FIELD_MAP:
            raise ValidationError(_('مرحلة الإنتاج المطلوبة غير صحيحة.'))
        if stage_code and stage_code not in allowed_stage_codes:
            raise AccessError(_('المرحلة المطلوبة غير مسموحة لهذا المشرف.'))

        try:
            normalized_date_from = (
                fields.Date.to_date(date_from) if date_from else False
            )
            normalized_date_to = (
                fields.Date.to_date(date_to) if date_to else False
            )
        except (TypeError, ValueError):
            raise ValidationError(_('صيغة فترة متابعة المراحل غير صحيحة.'))
        if (
            normalized_date_from
            and normalized_date_to
            and normalized_date_from > normalized_date_to
        ):
            raise ValidationError(_(
                'تاريخ بداية فترة المتابعة لازم يكون قبل تاريخ النهاية.'
            ))

        try:
            user_timezone = pytz.timezone(self.env.user.tz or 'UTC')
        except pytz.UnknownTimeZoneError:
            user_timezone = pytz.UTC

        def _local_day_start_as_utc(day):
            local_value = user_timezone.localize(
                datetime.combine(day, time.min),
                is_dst=False,
            )
            return local_value.astimezone(pytz.UTC).replace(tzinfo=None)

        company_ids = self.env.companies.ids
        production_domain = [
            ('company_id', 'in', company_ids),
            (
                'state',
                'not in',
                FURNITURE_STAGE_DASHBOARD_EXCLUDED_PRODUCTION_STATES,
            ),
        ]
        if normalized_date_from:
            production_domain.append((
                'date_planned_start',
                '>=',
                _local_day_start_as_utc(normalized_date_from),
            ))
        if normalized_date_to:
            production_domain.append((
                'date_planned_start',
                '<',
                _local_day_start_as_utc(
                    normalized_date_to + timedelta(days=1)
                ),
            ))
        productions = self.search(
            production_domain,
            order='dashboard_sequence asc, id asc',
        )

        payload_rows_by_stage = {
            code: [] for code, _label in allowed_stage_selection
        }
        summaries_by_stage = {
            code: {
                'code': code,
                'label': label,
                'icon': FURNITURE_STAGE_DASHBOARD_ICONS[code],
                'order_count': 0,
                'planned_qty': 0.0,
                'started_qty': 0.0,
                'working_qty': 0.0,
                'quality_qty': 0.0,
                'completed_qty': 0.0,
                'not_started_qty': 0.0,
                'remaining_qty': 0.0,
                'progress': 0.0,
            }
            for code, label in allowed_stage_selection
        }

        # Build and prefetch the cross-model stage rows first.  Tracking ids are
        # then validated in one query for the entire dashboard request.
        stage_rows = []
        visible_by_stage = {code: set(productions._visible_for_stage(code).ids)
                            for code, _label in allowed_stage_selection}
        for production in productions:
            planned_stage_codes = {
                selected_code
                for line in production.production_line_ids
                if line.product_id and (line.product_qty or 0.0) > 0.0
                for selected_code in line._selected_stage_codes()
            }
            for code, label in allowed_stage_selection:
                if production.id not in visible_by_stage[code]:
                    continue
                _use_field, order_field, _state_field = (
                    FURNITURE_STAGE_DASHBOARD_FIELD_MAP[code]
                )
                stage_order = production[order_field]
                has_planned_lines = code in planned_stage_codes
                if not stage_order and not has_planned_lines:
                    continue
                if not stage_order and has_planned_lines:
                    stage_lines = production.production_line_ids.filtered(
                        lambda line: (
                            line.active
                            and line.product_id
                            and (line.product_qty or 0.0) > 0.0
                            and code in line._selected_stage_codes()
                        )
                    )
                    if self._stage_dashboard_lane_output_covers_lines(
                        production,
                        stage_lines,
                    ):
                        continue
                stage_rows.append((production, code, label, stage_order))
        tracking_map = self._stage_dashboard_tracking_map(
            [row[3] for row in stage_rows]
        )

        # Each stage is evaluated independently.  The same production can
        # therefore appear in several tabs when parallel stages are running.
        for production, code, label, stage_order in stage_rows:
            tracking_lines = (
                tracking_map.get((stage_order._name, stage_order.id), {})
                if stage_order
                else {}
            )
            line_buckets = self._stage_dashboard_line_buckets(
                production,
                code,
                stage_order,
                tracking_lines=tracking_lines,
            )
            metrics = self._stage_dashboard_metrics(
                production,
                code,
                stage_order,
                tracking_lines=tracking_lines,
                line_buckets=line_buckets,
            )
            if metrics['planned_qty'] <= 0.0:
                continue

            payload_rows_by_stage[code].append((
                production, label, stage_order, metrics, line_buckets,
            ))
            summary = summaries_by_stage[code]
            summary['order_count'] += 1
            for quantity_field in (
                FURNITURE_STAGE_DASHBOARD_QUANTITY_FIELDS
            ):
                summary[quantity_field] += metrics[quantity_field]

        stages = []
        for code, _label in allowed_stage_selection:
            summary = summaries_by_stage[code]
            for quantity_field in FURNITURE_STAGE_DASHBOARD_QUANTITY_FIELDS:
                summary[quantity_field] = round(summary[quantity_field], 3)
            summary['progress'] = (
                round(
                    min(
                        100.0,
                        (
                            summary['completed_qty']
                            / summary['planned_qty']
                        ) * 100.0,
                    ),
                    2,
                )
                if summary['planned_qty'] > 0.0
                else 0.0
            )
            stages.append(summary)

        selected_stage = stage_code
        if not selected_stage:
            selected_stage = next(
                (
                    summary['code']
                    for summary in stages
                    if summary['order_count']
                ),
                stages[0]['code'] if stages else False,
            )
        selected_orders = []
        for (
            production, label, stage_order, metrics, line_buckets,
        ) in payload_rows_by_stage.get(selected_stage, []):
            payload = self._stage_dashboard_order_payload(
                production,
                selected_stage,
                label,
                stage_order,
                metrics,
                line_buckets,
                include_order_supervisor_details=bool(
                    profile['is_supervisor']
                    and selected_stage
                    in FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES
                ),
                include_order_display_details=not profile['is_supervisor'],
            )
            is_batch_supervisor_mode = bool(
                profile['is_supervisor']
                and selected_stage in FURNITURE_STAGE_DASHBOARD_BATCH_CODES
            )
            if is_batch_supervisor_mode:
                for customer_field in (
                    'buyer_ids', 'buyer_names', 'buyer_summary',
                    'beneficiary_ids', 'beneficiary_names',
                    'beneficiary_summary',
                ):
                    payload.pop(customer_field, None)
                for product_payload in payload['product_lines']:
                    for customer_field in (
                        'buyer_id', 'buyer_name',
                        'beneficiary_id', 'beneficiary_name',
                    ):
                        product_payload.pop(customer_field, None)
            selected_orders.append(payload)
        return {
            'selected_stage': selected_stage,
            'stages': stages,
            'orders': selected_orders,
            'stage_labels': stage_labels,
            'supervisor_mode': profile['is_supervisor'],
            'batch_supervisor_mode': bool(
                profile['is_supervisor']
                and selected_stage in FURNITURE_STAGE_DASHBOARD_BATCH_CODES
            ),
            'order_supervisor_mode': bool(
                profile['is_supervisor']
                and selected_stage
                in FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES
            ),
            'allowed_stage_codes': allowed_stage_codes,
            'date_filter': {
                'date_from': fields.Date.to_string(normalized_date_from),
                'date_to': fields.Date.to_string(normalized_date_to),
                'field': 'date_planned_start',
            },
        }

    def _stage_dashboard_validate_order_action(self, stage_code):
        """Lock and validate a dashboard mutation against current server state."""
        self.ensure_one()
        self._stage_dashboard_check_stage_access(stage_code)
        if stage_code not in FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES:
            raise ValidationError(_(
                'تشغيل أمر المرحلة من اللوحة متاح للتفصيل والكسوة '
                'والتغليف فقط.'
            ))
        if self.company_id.id not in self.env.companies.ids:
            raise AccessError(_(
                'أمر الإنتاج المطلوب لا يتبع إحدى الشركات المفعلة لحسابك.'
            ))

        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [self.id],
        )
        use_field, order_field, _state_field = (
            FURNITURE_STAGE_DASHBOARD_FIELD_MAP[stage_code]
        )
        self.invalidate_recordset(['state', use_field, order_field])
        if self.state not in ('confirmed', 'in_production'):
            raise UserError(_(
                'أمر الإنتاج لازم يكون مؤكد أو قيد الإنتاج قبل تشغيل المرحلة.'
            ))
        if not self[use_field]:
            raise UserError(_(
                'المرحلة المطلوبة لم تعد مختارة في أمر الإنتاج الحالي.'
            ))
        matching_lines = self.production_line_ids.filtered(
            lambda line: (
                line.active
                and line.product_id
                and (line.product_qty or 0.0) > 0.0
                and stage_code in line._selected_stage_codes()
            )
        )
        if not matching_lines:
            raise UserError(_(
                'لا توجد أصناف فعالة مطلوبة في المرحلة الحالية.'
            ))
        return self[order_field]

    def _stage_dashboard_ensure_order_stage(self, stage_code):
        """Create only this production's pending order when first requested."""
        self.ensure_one()
        stage_order = self._stage_dashboard_validate_order_action(stage_code)
        if stage_order:
            return stage_order
        # The dashboard RPC has already validated the caller's company and
        # exact supervisor stage.  Creating the technical stage order also
        # updates protected production-line planning fields, which supervisors
        # intentionally cannot edit directly.  Elevate only that internal
        # creation step; the resulting order is assigned to the real caller by
        # the action method immediately afterwards.
        start_method = getattr(
            self.sudo(), 'action_start_%s' % stage_code, None,
        )
        if not start_method:
            raise UserError(_('تعذر إنشاء أمر المرحلة المطلوبة.'))
        start_method()
        order_field = FURNITURE_STAGE_DASHBOARD_FIELD_MAP[stage_code][1]
        self.invalidate_recordset([order_field])
        stage_order = self._stage_dashboard_stage_order(stage_code)
        if not stage_order:
            raise UserError(_('تعذر إنشاء أمر المرحلة المطلوبة.'))
        return stage_order

    def action_stage_dashboard_request_order_materials(self, stage_code):
        """Request/receive this order's materials without leaving the dashboard."""
        self.ensure_one()
        stage_order = self._stage_dashboard_ensure_order_stage(stage_code)
        self._stage_dashboard_assign_current_foreman(stage_order, stage_code)
        if stage_order.state != 'pending':
            raise UserError(_(
                'طلب الخامات متاح قبل بدء أمر المرحلة فقط.'
            ))
        stage_order.invalidate_recordset(['store_request_state'])
        if stage_order.store_request_state in (
            'pending', 'approved', 'awaiting_receipt',
        ):
            advance_stage = getattr(
                stage_order, 'advance_material_release_stage_id', False,
            )
            if advance_stage:
                if (
                    advance_stage.production_id != self
                    or advance_stage.stage_code != stage_code
                ):
                    raise AccessError(_(
                        'إذن الخامات لا يخص أمر الإنتاج والمرحلة الحاليين.'
                    ))
                if advance_stage.state == 'issued' and not (
                    advance_stage.receipt_confirmed
                ):
                    # The supervisor is deliberately allowed to read only the
                    # stage slice, not the combined release header.  The
                    # action has already validated the exact stage/company;
                    # elevate only the internal locking/write path while
                    # retaining the real supervisor as the receiving user.
                    advance_stage.sudo()._confirm_production_receipt(
                        {
                            line.id: line.issued_qty
                            for line in advance_stage.material_line_ids
                        },
                        receiver_user=self.env.user,
                    )
                    stage_order.invalidate_recordset([
                        'store_request_state',
                        'advance_material_release_stage_id',
                        'advance_material_release_state',
                        'advance_material_receipt_state',
                        'advance_material_receipt_confirmed',
                    ])
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('تم الاستلام من المخزن'),
                            'message': _(
                                'تم تسجيل استلام كل الكميات المصروفة '
                                'لخامات المرحلة، ويمكن بدء المرحلة الآن.'
                            ),
                            'type': 'success',
                            'sticky': False,
                        },
                    }
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('متابعة خامات المرحلة'),
                        'message': (
                            _('إذن الخامات بانتظار أمين المخزن.')
                            if advance_stage.state == 'pending'
                            else _('خامات المرحلة مستلمة وجاهزة للبدء.')
                        ),
                        'type': (
                            'warning'
                            if advance_stage.state == 'pending' else 'success'
                        ),
                        'sticky': False,
                    },
                }
            request = stage_order._get_store_request(
                ('pending', 'approved'),
            ).with_user(self.env.user)
            if request:
                self._stage_dashboard_check_material_request(request, stage_code)
                if (
                    request.state == 'approved'
                    and not request.receipt_confirmed
                    and request.material_line_ids.mapped('issue_move_ids')
                ):
                    # Use the existing receipt validation and the actual
                    # supervisor's permissions/audit identity.  This accepts
                    # only what the storekeeper issued; stage start remains a
                    # separate action.  The client refreshes the selected card.
                    request._confirm_production_receipt({
                        line.id: line.issued_qty
                        for line in request.material_line_ids
                    })
                    stage_order.invalidate_recordset(['store_request_state'])
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('تم الاستلام من المخزن'),
                            'message': _(
                                'تم تسجيل استلام الكميات المصروفة لخامات '
                                'المرحلة. بدء المرحلة يتم من زر البدء.'
                            ),
                            'type': 'success',
                            'sticky': False,
                        },
                    }
                return request._display_request_notification(request, existing=True)

        action = stage_order.action_request_store_approval()
        # Another request may have arrived after the status lookup.  Keep
        # existing-request responses in place too, while preserving the modal
        # used to choose products/quantities for a new request.
        if (
            isinstance(action, dict)
            and action.get('type') == 'ir.actions.act_window'
            and action.get('res_model') == 'furniture.mrp.store.request'
        ):
            request = self.env['furniture.mrp.store.request'].browse(
                action['res_id'],
            ).exists()
            self._stage_dashboard_check_material_request(request, stage_code)
            return request._display_request_notification(request, existing=True)
        return action

    def _stage_dashboard_check_material_request(self, request, stage_code):
        self.ensure_one()
        if not request or (
            request.production_id != self or request.stage_code != stage_code
        ):
            raise AccessError(_(
                'إذن الخامات لا يخص أمر الإنتاج والمرحلة الحاليين.'
            ))

    def action_stage_dashboard_start_order_stage(self, stage_code):
        """Start this exact production/stage through the approved workflow."""
        self.ensure_one()
        stage_order = self._stage_dashboard_validate_order_action(stage_code)
        if not stage_order:
            raise UserError(_(
                'اطلب خامات أمر المرحلة أولًا قبل بدء التشغيل.'
            ))
        self._stage_dashboard_assign_current_foreman(stage_order, stage_code)
        if stage_order.state == 'done':
            raise UserError(_('أمر المرحلة مكتمل بالفعل.'))
        if stage_order.state != 'pending':
            raise UserError(_('أمر المرحلة قيد التشغيل بالفعل.'))
        return stage_order.action_start()

    def action_stage_dashboard_start_order_product(
        self, production_line_ids, stage_code,
    ):
        """Start only the product rows tapped by an order-stage supervisor."""
        self.ensure_one()
        stage_order = self._stage_dashboard_validate_order_action(stage_code)
        if not stage_order:
            raise UserError(_(
                'اطلب خامات أمر المرحلة أولًا قبل بدء تشغيل الصنف.'
            ))
        self._stage_dashboard_assign_current_foreman(stage_order, stage_code)
        stage_order.invalidate_recordset(['state', 'store_request_state'])
        if stage_order.state not in ('pending', 'in_progress'):
            raise UserError(_(
                'أمر المرحلة ليس في حالة تسمح ببدء صنف جديد.'
            ))
        if stage_order.store_request_state != 'approved':
            raise UserError(_(
                'استلم خامات أمر المرحلة من المخزن قبل بدء تشغيل الصنف.'
            ))

        raw_line_ids = (
            production_line_ids
            if isinstance(production_line_ids, (list, tuple, set))
            else [production_line_ids]
        )
        requested_ids = {
            int(line_id)
            for line_id in raw_line_ids
            if str(line_id).isdigit() and int(line_id) > 0
        }
        selected_lines = self.production_line_ids.filtered(
            lambda line: line.active and line.id in requested_ids
        )
        if not requested_ids or set(selected_lines.ids) != requested_ids:
            raise AccessError(_(
                'الصنف المطلوب لا يخص أمر الإنتاج الحالي أو لم يعد فعالًا.'
            ))

        pending_lines = self._get_stage_pending_start_line_candidates(
            stage_order, stage_code,
        )
        invalid_lines = selected_lines - pending_lines
        if invalid_lines:
            raise UserError(_(
                'الصنف المختار بدأ المرحلة بالفعل أو لم يصل إليها بعد.'
            ))

        execution_context = dict(
            self.env.context,
            furniture_storekeeper_approval_bypass=True,
            furniture_skip_stage_start_prompt=True,
        )
        advance_stage = self.env[
            'furniture.mrp.advance.material.release'
        ].find_active_stage_release(self, stage_code)
        if advance_stage:
            advance_stage.release_id._lock()
            advance_stage.invalidate_recordset([
                'state', 'receipt_confirmed',
            ])
            if (
                advance_stage.state not in ('issued', 'started')
                or not advance_stage.receipt_confirmed
            ):
                raise UserError(_(
                    'إذن خامات المرحلة لم يعد جاهزًا لبدء هذا الصنف.'
                ))
            execution_context[
                'furniture_advance_material_release_stage_id'
            ] = advance_stage.id

        # The caller was already locked and validated against the exact stage,
        # company and production rows above.  The internal start writes stock
        # moves and chatter messages which supervisors intentionally cannot
        # create directly, so elevate only this bounded execution path while
        # keeping the real caller in the environment for audit attribution.
        execution_production = self.sudo().with_context(execution_context)
        execution_stage_order = (
            execution_production._stage_dashboard_stage_order(stage_code)
        )

        if execution_production._is_material_only_stage(stage_code):
            # Tailoring/painting/sewing consume their own recipe materials;
            # they do not receive a semi-finished product through the physical
            # stage-hall carryover flow.  Keep the dashboard selection exact
            # and start it directly from the already received material release.
            # Treating these rows as carryover made cover-lane tailoring look
            # for a sofa in the tailoring hall and reject a valid start.
            selected_execution_lines = (
                execution_production.production_line_ids.filtered(
                    lambda line: line.id in selected_lines.ids
                )
            )
            execution_stage_order.with_context(
                furniture_skip_line_consolidation=True,
            )._add_stage_active_lines(selected_execution_lines)
            material_lines = (
                execution_production._get_or_create_first_stage_material_lines(
                    selected_execution_lines,
                    stage_code,
                )
            )
            if execution_stage_order.state == 'pending':
                execution_stage_order.with_context(
                    execution_context,
                    furniture_skip_line_consolidation=True,
                    furniture_stage_start_mode='direct',
                ).action_start()
            else:
                # The stage timer and worker logs are already running.  A
                # second product only needs its exact received recipe rows
                # staged and attached to the same active stage order.
                execution_production._move_materials_to_stage_wip(
                    execution_stage_order._name,
                    material_lines=material_lines,
                    allow_material_line_link_sudo=True,
                )
            result = {
                'started': True,
                'stage_code': stage_code,
                'production_line_ids': selected_lines.ids,
            }
            warning = self.env[
                'furniture.mrp.product.production.warning'
            ]._consume_next_for_lines(
                stage_code,
                selected_lines,
                batch_token='order:%s:%s' % (self.id, stage_code),
            )
            if warning:
                result['production_warning'] = warning
            return result

        entry_lines = selected_lines.filtered(lambda line: (
            not line.first_stage_started
            and self._production_line_start_stage_code(line) == stage_code
        ))
        if entry_lines:
            action = execution_production._open_first_stage_start_wizard(
                execution_stage_order, stage_code, entry_lines.sudo(),
            )
            wizard = self.env[
                'furniture.mrp.first.stage.start.wizard'
            ].sudo().browse(action.get('res_id')).exists()
            if not wizard:
                raise UserError(_('تعذر تجهيز الصنف المحدد لبدء المرحلة.'))
            wizard.with_context(
                execution_context
            ).action_start_selected_from_stock()

        carryover_lines = selected_lines - entry_lines
        if carryover_lines:
            carryover_payloads = [
                payload
                for payload in execution_stage_order._get_startable_stage_work_payloads(
                    stage_code,
                )
                if payload.get('source_production_line') in carryover_lines
            ]
            covered_lines = self.env['furniture.mrp.production.line']
            for payload in carryover_payloads:
                covered_lines |= payload.get(
                    'source_production_line'
                ) or self.env['furniture.mrp.production.line']
            if carryover_lines - covered_lines:
                raise UserError(_(
                    'الصنف المختار لم يعد موجودًا في صالة المرحلة.'
                ))
            action = execution_production._open_stage_start_carryover_wizard(
                execution_stage_order, stage_code, carryover_payloads,
            )
            wizard = self.env[
                'furniture.mrp.stage.start.carryover.wizard'
            ].sudo().browse(action.get('res_id')).exists()
            if not wizard:
                raise UserError(_('تعذر تجهيز الصنف المحدد لبدء المرحلة.'))
            wizard.with_context(execution_context)._action_start_with_mode(
                'selected_work'
            )

        result = {
            'started': True,
            'stage_code': stage_code,
            'production_line_ids': selected_lines.ids,
        }
        warning = self.env[
            'furniture.mrp.product.production.warning'
        ]._consume_next_for_lines(
            stage_code,
            selected_lines,
            batch_token='order:%s:%s' % (self.id, stage_code),
        )
        if warning:
            result['production_warning'] = warning
        return result

    def action_stage_dashboard_finish_order_stage(self, stage_code):
        """Finish every active product row for this exact production order."""
        self.ensure_one()
        stage_order = self._stage_dashboard_validate_order_action(stage_code)
        if not stage_order:
            raise UserError(_('المرحلة لم تبدأ حتى يمكن إنهاؤها.'))
        self._stage_dashboard_assign_current_foreman(stage_order, stage_code)
        stage_order.invalidate_recordset(['state'])
        if stage_order.state == 'done':
            raise UserError(_('أمر المرحلة مكتمل بالفعل.'))
        if stage_order.state not in ('in_progress', 'quality_check'):
            raise UserError(_('ابدأ المرحلة أولًا قبل إنهائها.'))

        active_lines = stage_order._get_stage_line_ids_data(
            'active_production_line_ids_data'
        ).filtered(lambda line: (
            line.active
            and line.production_id == self
            and stage_code in line._selected_stage_codes()
        ))
        if not active_lines:
            raise UserError(_('لا توجد أصناف شغالة حاليًا لإنهاء المرحلة.'))

        execution_stage_order = stage_order.sudo().with_context(
            furniture_skip_stage_quality_prompt=True,
            furniture_skip_line_consolidation=True,
        )
        complete_internal_steps = getattr(
            execution_stage_order,
            '_complete_internal_substages_for_dashboard_finish',
            False,
        )
        if complete_internal_steps:
            complete_internal_steps()
        if execution_stage_order.state == 'in_progress':
            execution_stage_order._send_selected_lines_to_quality(
                active_lines.sudo()
            )
        execution_stage_order.action_approve_quality()
        return {
            'finished': True,
            'stage_code': stage_code,
            'production_line_ids': active_lines.ids,
        }

    def action_open_stage_dashboard_bom(
        self, production_line_id, stage_code,
    ):
        """Open one product recipe after validating the supervisor's stage."""
        self.ensure_one()
        self._stage_dashboard_check_stage_access(stage_code)
        line = self.production_line_ids.filtered(
            lambda candidate: candidate.id == int(production_line_id or 0)
        )[:1]
        if not line or stage_code not in line._selected_stage_codes():
            raise AccessError(_('الصنف المطلوب لا يخص أمر الإنتاج أو المرحلة.'))
        return self.env[
            'furniture.mrp.stage.product.batch.bom.wizard'
        ]._action_for_production_line(line, stage_code)

    def action_stage_dashboard_start_batch(self, stage_code):
        """Prepare/request materials, then start the full operational batch."""
        self.ensure_one()
        self._stage_dashboard_check_stage_access(stage_code, batch_only=True)
        stage_order = self._stage_dashboard_stage_order(stage_code)
        if not stage_order:
            start_method = getattr(self, 'action_start_%s' % stage_code, None)
            if not start_method:
                raise UserError(_('تعذر إنشاء أمر المرحلة المطلوبة.'))
            start_method()
            self.invalidate_recordset([
                FURNITURE_STAGE_DASHBOARD_FIELD_MAP[stage_code][1]
            ])
            stage_order = self._stage_dashboard_stage_order(stage_code)
        if not stage_order:
            raise UserError(_('تعذر إنشاء أمر المرحلة المطلوبة.'))
        self._stage_dashboard_assign_current_foreman(stage_order, stage_code)
        if stage_order.state == 'done':
            raise UserError(_('المرحلة مكتملة بالفعل.'))
        if stage_order.state in ('in_progress', 'quality_check'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('المرحلة قيد التشغيل'),
                    'message': _('العداد شغال بالفعل لهذه الدفعة.'),
                    'type': 'info',
                },
            }

        request_state = stage_order.store_request_state
        if request_state in ('pending', 'awaiting_receipt'):
            return stage_order.action_open_store_request()
        if request_state == 'approved':
            result = stage_order.with_context(
                furniture_skip_stage_start_prompt=True,
            ).action_start()
        else:
            result = stage_order.with_context(
                furniture_skip_stage_start_prompt=True,
            ).action_request_store_approval()
        return result or {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم بدء المرحلة'),
                'message': _('بدأ تشغيل الكمية المخططة بالكامل.'),
                'type': 'success',
            },
        }

    def action_stage_dashboard_finish_batch(self, stage_code):
        """Finish the active planned batch through the existing quality flow."""
        self.ensure_one()
        self._stage_dashboard_check_stage_access(stage_code, batch_only=True)
        stage_order = self._stage_dashboard_stage_order(stage_code)
        if not stage_order:
            raise UserError(_('المرحلة لم تبدأ حتى يمكن إنهاؤها.'))
        if stage_order.state == 'done':
            raise UserError(_('المرحلة مكتملة بالفعل.'))
        if stage_order.state not in ('in_progress', 'quality_check'):
            raise UserError(_('ابدأ المرحلة أولًا قبل إنهائها.'))

        # Access to this exact production/stage was already checked above.
        # Quality completion performs bounded technical writes on material
        # reservations, stock moves and chatter which stage supervisors do not
        # own directly.  Execute that internal path in sudo while retaining the
        # real user in the environment for authorship and audit attribution.
        execution_stage_order = stage_order.sudo().with_context(
            furniture_skip_stage_quality_prompt=True,
        )

        if (
            stage_code == 'bases'
            and execution_stage_order.state == 'in_progress'
        ):
            substage_values = {
                field_name: 'done'
                for _code, _label, field_name
                in execution_stage_order._get_internal_substage_fields()
                if execution_stage_order[field_name] != 'done'
            }
            if substage_values:
                execution_stage_order.write(substage_values)
                execution_stage_order.message_post(
                    body=_('✅ تم إنهاء مراحل دفعة القواعد من لوحة المشرف.')
                )

        if execution_stage_order.state == 'in_progress':
            execution_stage_order.action_send_to_quality()
        result = execution_stage_order.action_approve_quality()
        return result or {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم إنهاء المرحلة'),
                'message': _('اكتملت الكمية المخططة بالكامل وتوقف العداد.'),
                'type': 'success',
            },
        }
