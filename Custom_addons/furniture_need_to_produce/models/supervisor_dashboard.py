# -*- coding: utf-8 -*-
"""Read-only planned/history sections beside the existing supervisor workflow.

Future work is deliberately NOT an executable product-batch candidate.  Every
request/receipt/start continues through the existing exact-line arrival guards.
"""

from datetime import datetime, time, timedelta
import hashlib

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.furniture_mrp.models.mrp_stage_dashboard import (
    FURNITURE_STAGE_DASHBOARD_BATCH_CODES,
)


class FurnitureMrpNeedSupervisorDashboard(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _need_supervisor_date_domain(self, date_from=False, date_to=False):
        try:
            start = fields.Date.to_date(date_from) if date_from else False
            end = fields.Date.to_date(date_to) if date_to else False
        except (ValueError, TypeError):
            raise ValidationError(_('صيغة فترة متابعة المراحل غير صحيحة.'))
        if start and end and start > end:
            raise ValidationError(_('تاريخ البداية يجب أن يسبق تاريخ النهاية.'))
        try:
            timezone = pytz.timezone(self.env.user.tz or 'UTC')
        except pytz.UnknownTimeZoneError:
            timezone = pytz.UTC

        def utc(day):
            return timezone.localize(datetime.combine(day, time.min)).astimezone(
                pytz.UTC
            ).replace(tzinfo=None)

        result = []
        if start:
            result.append(('date_planned_start', '>=', utc(start)))
        if end:
            result.append(('date_planned_start', '<', utc(end + timedelta(days=1))))
        return result

    @api.model
    def _need_supervisor_line_card(self, line, stage_code, status, finished_at=False):
        """No order/customer identifiers or executable batch tokens leave here."""
        identity = self.env[
            'furniture.mrp.stage.product.batch'
        ]._identity_values_from_line(line)
        model = identity['model']
        product = identity['product']
        key = hashlib.sha256(('%s:%s:%s:%s' % (
            self.env.cr.dbname, stage_code, line.id, status,
        )).encode()).hexdigest()
        return {
            'key': key,
            'stage_code': stage_code,
            'status': status,
            'product_name': product.display_name,
            'model_name': model.display_name if model else False,
            'model_id': model.id if model else False,
            'dimension_label': identity['dimension_label'],
            'quantity': max(line.product_qty or 0.0, 0.0),
            'uom_name': identity['uom'].display_name,
            'finished_at': fields.Datetime.to_string(finished_at) if finished_at else False,
            'message': (
                _('أمر مؤكد ومخطط؛ ينتظر انتهاء المرحلة السابقة واستلام المنتج. لا يمكن بدء التشغيل قبل وصوله.')
                if status == 'waiting_upstream' else _('اكتمل تشغيل الصنف في هذه المرحلة.')
            ),
            'can_request_materials': False,
            'can_receive_materials': False,
            'can_start': False,
            'can_finish': False,
        }

    @api.model
    def _need_supervisor_is_linked_plan(self, production):
        return bool(
            'need_to_produce_stage_id' in production._fields
            and production.need_to_produce_stage_id
        )

    @api.model
    def _need_supervisor_waiting_lines(self, production, stage_code, buckets):
        """Display future confirmed plan work without relaxing its action scope."""
        empty = self.env['furniture.mrp.production.line']
        if (
            stage_code not in FURNITURE_STAGE_DASHBOARD_BATCH_CODES
            or production.state not in ('confirmed', 'in_production')
            or not self._need_supervisor_is_linked_plan(production)
            or stage_code not in production._required_stage_codes()
        ):
            return empty
        candidates = buckets['not_started_lines'].filtered(lambda line: (
            line.active and not line.consolidated_into_line_id
        ))
        ready = self.env[
            'furniture.mrp.stage.product.batch'
        ]._stage_ready_candidate_lines(stage_code, candidates)
        return candidates - ready

    @api.model
    def get_need_supervisor_stage_sections(
        self, stage_code, date_from=False, date_to=False,
    ):
        """Separate history never changes the manager's live workload metrics."""
        profile = self._stage_dashboard_check_stage_access(stage_code)
        result = {
            'stage_code': stage_code,
            'completed': [],
            'waiting': [],
            'completed_quantity': 0.0,
            'waiting_quantity': 0.0,
            'planned_order_count': 0,
        }
        date_domain = self._need_supervisor_date_domain(date_from, date_to)
        if not profile['is_supervisor']:
            return result
        productions = self.search([
            ('company_id', 'in', self.env.companies.ids),
            ('state', 'in', ('confirmed', 'in_production', 'done')),
        ] + date_domain, order='date_planned_start desc, id desc')
        for production in productions:
            stage_order = production._stage_order_record(stage_code)
            # Stage-only source stock is not a supervisor-completed job.
            if not stage_order and stage_code not in production._required_stage_codes():
                continue
            buckets = self._stage_dashboard_line_buckets(
                production, stage_code, stage_order,
            )
            waiting_lines = self._need_supervisor_waiting_lines(production, stage_code, buckets)
            if production.state in ('confirmed', 'in_production'):
                ready_lines = self.env['furniture.mrp.stage.product.batch']._stage_ready_candidate_lines(
                    stage_code, buckets['not_started_lines'],
                ) if stage_code in FURNITURE_STAGE_DASHBOARD_BATCH_CODES else buckets['not_started_lines']
                # One distinct production, even when it contributes multiple products/batches.
                if ready_lines or buckets['working_lines'] or waiting_lines:
                    result['planned_order_count'] += 1
            for line in buckets['completed_lines']:
                finished_at = (
                    stage_order.end_date
                    if stage_order and 'end_date' in stage_order._fields
                    else False
                )
                card = self._need_supervisor_line_card(
                    line, stage_code, 'completed', finished_at,
                )
                result['completed'].append(card)
                result['completed_quantity'] += card['quantity']
            for line in waiting_lines:
                card = self._need_supervisor_line_card(
                    line, stage_code, 'waiting_upstream',
                )
                result['waiting'].append(card)
                result['waiting_quantity'] += card['quantity']
        result['completed_quantity'] = round(result['completed_quantity'], 3)
        result['waiting_quantity'] = round(result['waiting_quantity'], 3)
        return result
