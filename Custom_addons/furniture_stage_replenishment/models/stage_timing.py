# -*- coding: utf-8 -*-

"""Per-piece stage standards and live working-calendar countdowns."""

import math
from datetime import datetime, time, timedelta, timezone

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.furniture_mrp.models.mrp_production_order import (
    FURNITURE_STAGE_FIELD_MAP,
    FURNITURE_STAGE_SELECTION,
)


STAGE_TIME_CODES = tuple(code for code, _label in FURNITURE_STAGE_SELECTION)


class FurnitureMrpStageTimeStandard(models.Model):
    _name = 'furniture.mrp.stage.time.standard'
    _description = 'Furniture MRP Stage Time Standard'
    _order = 'sequence, id'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
        readonly=True, index=True, ondelete='cascade',
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True, index=True,
        string='المرحلة',
    )
    sequence = fields.Integer(default=10, readonly=True)
    hours_per_piece = fields.Float(
        string='ساعات القطعة', required=True, default=2.0, digits=(16, 3),
        help='المدة القياسية لقطعة واحدة. وقت الدفعة = الكمية × هذه المدة.',
    )
    active = fields.Boolean(default=True, readonly=True)

    _sql_constraints = [
        (
            'stage_time_company_stage_unique',
            'unique(company_id, stage_code)',
            'A time standard already exists for this company and stage.',
        ),
        (
            'stage_time_positive',
            'check(hours_per_piece > 0)',
            'Hours per piece must be greater than zero.',
        ),
    ]

    @api.model
    def _stage_time_check_manager(self):
        user = self.env.user
        if not (
            user._is_admin()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
        ):
            raise AccessError(_('Stage time standards are available to factory managers only.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._stage_time_check_manager()
        raise AccessError(_(
            'Stage time rows are generated automatically. Use the existing rows.'
        ))

    @api.model
    def _stage_time_internal_create(self, vals_list):
        return super(FurnitureMrpStageTimeStandard, self.sudo()).create(vals_list)

    def write(self, vals):
        if set(vals) - {'hours_per_piece'}:
            raise AccessError(_('Only hours per piece can be edited manually.'))
        self._stage_time_check_manager()
        return super().write(vals)

    def _stage_time_internal_write(self, vals):
        return super(FurnitureMrpStageTimeStandard, self.sudo()).write(vals)

    @api.constrains('hours_per_piece')
    def _check_hours_per_piece(self):
        for standard in self:
            if (
                not math.isfinite(standard.hours_per_piece)
                or standard.hours_per_piece <= 0
            ):
                raise ValidationError(_('Hours per piece must be a finite number above zero.'))

    @api.model
    def _stage_time_sync_company(self, company=None):
        company = company or self.env.company
        Standard = self.sudo().with_company(company).with_context(active_test=False)
        existing = {
            row.stage_code: row
            for row in Standard.search([('company_id', '=', company.id)])
        }
        for sequence, (stage_code, _label) in enumerate(
            FURNITURE_STAGE_SELECTION, start=1,
        ):
            row = existing.get(stage_code)
            values = {'active': True, 'sequence': sequence * 10}
            if row:
                changes = {
                    key: value for key, value in values.items()
                    if row[key] != value
                }
                if changes:
                    row._stage_time_internal_write(changes)
            else:
                Standard._stage_time_internal_create([{
                    'company_id': company.id,
                    'stage_code': stage_code,
                    'hours_per_piece': 2.0,
                    **values,
                }])
        return Standard.with_context(active_test=True).search([
            ('company_id', '=', company.id),
        ])

    @api.model
    def _stage_time_hours_per_piece(self, company, stage_code):
        row = self.sudo().with_company(company).search([
            ('company_id', '=', company.id),
            ('stage_code', '=', stage_code),
            ('active', '=', True),
        ], limit=1)
        if not row:
            self._stage_time_sync_company(company)
            row = self.sudo().with_company(company).search([
                ('company_id', '=', company.id),
                ('stage_code', '=', stage_code),
                ('active', '=', True),
            ], limit=1)
        return row.hours_per_piece if row else 2.0

    @api.model
    def _stage_timer_timezone(self, company):
        timezone_name = (
            company.resource_calendar_id.tz
            or self.env.user.tz
            or 'UTC'
        )
        try:
            return pytz.timezone(timezone_name)
        except pytz.UnknownTimeZoneError:
            return pytz.UTC

    @api.model
    def _stage_timer_factory_intervals(self, company, start, end):
        """Yield the agreed 08:00-18:00 six-day factory shifts.

        The factory works ten hours on every weekday except Friday.  This is
        intentionally isolated from the generic company calendar so changing
        Accounting/HR attendance cannot silently change production timers.
        """
        start = fields.Datetime.to_datetime(start)
        end = fields.Datetime.to_datetime(end)
        if not start or not end or end <= start:
            return []
        local_tz = self._stage_timer_timezone(company)
        aware_start = start.replace(tzinfo=timezone.utc)
        aware_end = end.replace(tzinfo=timezone.utc)
        first_day = aware_start.astimezone(local_tz).date()
        last_day = aware_end.astimezone(local_tz).date()
        intervals = []
        day = first_day
        while day <= last_day:
            # Python weekday(): Monday=0 ... Friday=4 ... Sunday=6.
            if day.weekday() != 4:
                local_start = local_tz.localize(datetime.combine(day, time(8, 0)))
                local_end = local_tz.localize(datetime.combine(day, time(18, 0)))
                interval_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
                interval_end = local_end.astimezone(timezone.utc).replace(tzinfo=None)
                if interval_end > start and interval_start < end:
                    intervals.append((max(interval_start, start), min(interval_end, end)))
            day += timedelta(days=1)
        return intervals

    @api.model
    def _stage_timer_work_hours(self, company, start, end):
        if not start or not end:
            return 0.0
        start = fields.Datetime.to_datetime(start)
        end = fields.Datetime.to_datetime(end)
        if end <= start:
            return 0.0
        return sum(
            (interval_end - interval_start).total_seconds() / 3600.0
            for interval_start, interval_end
            in self._stage_timer_factory_intervals(company, start, end)
        )

    @api.model
    def _stage_timer_uses_continuous_clock(self, record):
        """Keep temporary fixed-hour test orders independent of shifts."""
        batch_hours = getattr(
            record, '_stage_timer_temporary_fixed_hours', False,
        )
        if callable(batch_hours) and batch_hours():
            return True
        production = getattr(record, 'production_order_id', False)
        production_hours = (
            getattr(production, '_furniture_temporary_stage_hours', False)
            if production else False
        )
        return bool(callable(production_hours) and production_hours())

    @api.model
    def _stage_timer_elapsed_hours(self, record, company, start, end):
        """Return wall-clock hours for test orders, factory hours otherwise."""
        if self._stage_timer_uses_continuous_clock(record):
            start = fields.Datetime.to_datetime(start)
            end = fields.Datetime.to_datetime(end)
            if not start or not end or end <= start:
                return 0.0
            return (end - start).total_seconds() / 3600.0
        return self._stage_timer_work_hours(company, start, end)

    @api.model
    def _stage_timer_future_intervals(self, company, start, planned_hours):
        start = fields.Datetime.to_datetime(start or fields.Datetime.now())
        horizon_days = min(
            max(int(math.ceil(max(planned_hours, 1.0) / 6.0)) + 21, 35),
            365,
        )
        return [
            [
                fields.Datetime.to_string(interval_start),
                fields.Datetime.to_string(interval_end),
            ]
            for interval_start, interval_end
            in self._stage_timer_factory_intervals(
                company, start, start + timedelta(days=horizon_days),
            )
        ]

    @api.model
    def _stage_timer_payload(self, record, state):
        started = record.stage_timer_started_at
        if not started:
            return False
        company = getattr(record, 'company_id', False)
        if not company:
            production = getattr(record, 'production_order_id', False)
            company = production.company_id if production else False
        if not company:
            raise ValidationError(_(
                'تعذر تحديد شركة سجل المرحلة لحساب وقت التشغيل.'
            ))
        now = fields.Datetime.now()
        end = record.stage_timer_finished_at or now
        continuous_clock = self._stage_timer_uses_continuous_clock(record)
        paused_hours = max(record.stage_timer_paused_work_hours or 0.0, 0.0)
        if record.stage_timer_paused_at and not record.stage_timer_finished_at:
            paused_hours += self._stage_timer_elapsed_hours(
                record, company,
                record.stage_timer_paused_at,
                end,
            )
        elapsed_hours = max(
            self._stage_timer_elapsed_hours(record, company, started, end)
            - paused_hours,
            0.0,
        )
        planned_hours = max(record.stage_timer_planned_hours or 0.0, 0.0)
        remaining_seconds = int(round((planned_hours - elapsed_hours) * 3600))
        is_active = bool(
            not record.stage_timer_finished_at
            and state in ('in_progress', 'quality_check')
        )
        is_paused = bool(is_active and record.stage_timer_paused_at)
        return {
            'planned_qty': record.stage_timer_planned_qty or 0.0,
            'hours_per_piece': record.stage_timer_piece_hours or 0.0,
            'planned_hours': planned_hours,
            'elapsed_work_hours': elapsed_hours,
            'remaining_seconds': remaining_seconds,
            'started_at': fields.Datetime.to_string(started),
            'finished_at': (
                fields.Datetime.to_string(record.stage_timer_finished_at)
                if record.stage_timer_finished_at else False
            ),
            'paused_at': (
                fields.Datetime.to_string(record.stage_timer_paused_at)
                if record.stage_timer_paused_at else False
            ),
            'paused': is_paused,
            'running': bool(is_active and not is_paused),
            'continuous': continuous_clock,
            'overdue': remaining_seconds < 0,
            'can_pause': bool(is_active and not is_paused),
            'can_resume': is_paused,
            'snapshot_at': fields.Datetime.to_string(now),
            'work_intervals': (
                self._stage_timer_future_intervals(
                    company, now, planned_hours,
                ) if is_active and not is_paused else []
            ),
        }


class FurnitureMrpStageTimerMixin(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    stage_timer_planned_qty = fields.Float(readonly=True, copy=False, digits=(16, 3))
    stage_timer_piece_hours = fields.Float(readonly=True, copy=False, digits=(16, 3))
    stage_timer_planned_hours = fields.Float(readonly=True, copy=False, digits=(16, 3))
    stage_timer_started_at = fields.Datetime(readonly=True, copy=False)
    stage_timer_finished_at = fields.Datetime(readonly=True, copy=False)
    stage_timer_paused_at = fields.Datetime(readonly=True, copy=False)
    stage_timer_paused_work_hours = fields.Float(readonly=True, copy=False, default=0.0)

    def _stage_timer_stage_code(self):
        self.ensure_one()
        production = self.production_order_id
        return production._stage_model_to_code(self._name) if production else False

    def _stage_timer_start_values(self, planned_qty=False, started_at=False):
        self.ensure_one()
        stage_code = self._stage_timer_stage_code()
        quantity = max(
            float(planned_qty if planned_qty is not False else self.stage_batch_qty or self.product_qty or 0.0),
            0.0,
        )
        fixed_hours = self.production_order_id._furniture_temporary_stage_hours()
        if fixed_hours:
            rate = fixed_hours / quantity if quantity else fixed_hours
            planned_hours = fixed_hours
        else:
            rate = self.env[
                'furniture.mrp.stage.time.standard'
            ]._stage_time_hours_per_piece(
                self.production_order_id.company_id, stage_code,
            )
            planned_hours = quantity * rate
        return {
            'stage_timer_planned_qty': quantity,
            'stage_timer_piece_hours': rate,
            'stage_timer_planned_hours': planned_hours,
            'stage_timer_started_at': started_at or fields.Datetime.now(),
            'stage_timer_finished_at': False,
            'stage_timer_paused_at': False,
            'stage_timer_paused_work_hours': 0.0,
        }

    def _stage_timer_initialize_if_needed(self, planned_qty=False, started_at=False):
        for record in self:
            if not record.stage_timer_started_at and record.state in (
                'in_progress', 'quality_check',
            ):
                record.sudo().write(record._stage_timer_start_values(
                    planned_qty=planned_qty,
                    started_at=started_at or record.date_start,
                ))
        return True

    def _stage_timer_finish(self):
        Standard = self.env['furniture.mrp.stage.time.standard']
        now = fields.Datetime.now()
        for record in self.filtered(lambda item: (
            item.stage_timer_started_at and not item.stage_timer_finished_at
        )):
            paused_hours = record.stage_timer_paused_work_hours or 0.0
            if record.stage_timer_paused_at:
                paused_hours += Standard._stage_timer_elapsed_hours(
                    record, record.production_order_id.company_id,
                    record.stage_timer_paused_at,
                    now,
                )
            record.sudo().write({
                'stage_timer_finished_at': record.date_finish or now,
                'stage_timer_paused_at': False,
                'stage_timer_paused_work_hours': paused_hours,
            })
        return True

    def action_start(self):
        quantities = {record.id: record.stage_batch_qty or record.product_qty or 0.0 for record in self}
        result = super().action_start()
        for record in self:
            if record.state == 'in_progress':
                record._stage_timer_initialize_if_needed(
                    planned_qty=quantities.get(record.id, 0.0),
                    started_at=record.date_start,
                )
        return result

    def action_approve_quality(self):
        result = super().action_approve_quality()
        self.filtered(lambda record: record.state == 'done')._stage_timer_finish()
        return result

    def action_pause_stage_timer(self):
        self._check_stage_operation_access()
        now = fields.Datetime.now()
        for record in self:
            if record.state not in ('in_progress', 'quality_check'):
                raise UserError(_('Only a running stage can be paused.'))
            record._stage_timer_initialize_if_needed()
            if record.stage_timer_paused_at:
                continue
            record.sudo().write({'stage_timer_paused_at': now})
        return True

    def action_resume_stage_timer(self):
        self._check_stage_operation_access()
        Standard = self.env['furniture.mrp.stage.time.standard']
        now = fields.Datetime.now()
        for record in self:
            if not record.stage_timer_paused_at:
                continue
            paused_hours = (
                record.stage_timer_paused_work_hours or 0.0
            ) + Standard._stage_timer_elapsed_hours(
                record, record.production_order_id.company_id,
                record.stage_timer_paused_at,
                now,
            )
            record.sudo().write({
                'stage_timer_paused_at': False,
                'stage_timer_paused_work_hours': paused_hours,
            })
        return True

    def _stage_timer_dashboard_payload(self):
        self.ensure_one()
        return self.env[
            'furniture.mrp.stage.time.standard'
        ]._stage_timer_payload(self, self.state)


class FurnitureMrpStageProductBatchTimer(models.Model):
    _inherit = 'furniture.mrp.stage.product.batch'

    stage_timer_planned_qty = fields.Float(readonly=True, copy=False, digits=(16, 3))
    stage_timer_piece_hours = fields.Float(readonly=True, copy=False, digits=(16, 3))
    stage_timer_planned_hours = fields.Float(readonly=True, copy=False, digits=(16, 3))
    stage_timer_started_at = fields.Datetime(readonly=True, copy=False)
    stage_timer_finished_at = fields.Datetime(readonly=True, copy=False)
    stage_timer_paused_at = fields.Datetime(readonly=True, copy=False)
    stage_timer_paused_work_hours = fields.Float(readonly=True, copy=False, default=0.0)

    def _stage_timer_temporary_fixed_hours(self):
        """Use a fixed duration only when every batch member opted into it."""
        self.ensure_one()
        productions = self.member_ids.mapped('production_id').exists()
        if not productions:
            return 0.0
        hours = [
            production._furniture_temporary_stage_hours()
            for production in productions
        ]
        if not all(hours) or not all(
            math.isclose(value, hours[0], rel_tol=0.0, abs_tol=1e-6)
            for value in hours[1:]
        ):
            return 0.0
        return hours[0]

    def _stage_timer_initialize_if_needed(self, started_at=False):
        Standard = self.env['furniture.mrp.stage.time.standard']
        for batch in self:
            if batch.stage_timer_started_at or batch.state != 'in_progress':
                continue
            fixed_hours = batch._stage_timer_temporary_fixed_hours()
            if fixed_hours:
                rate = (
                    fixed_hours / batch.planned_qty
                    if batch.planned_qty else fixed_hours
                )
                planned_hours = fixed_hours
            else:
                rate = Standard._stage_time_hours_per_piece(
                    batch.company_id, batch.stage_code,
                )
                planned_hours = batch.planned_qty * rate
            batch.sudo().write({
                'stage_timer_planned_qty': batch.planned_qty,
                'stage_timer_piece_hours': rate,
                'stage_timer_planned_hours': planned_hours,
                'stage_timer_started_at': started_at or batch.started_at or fields.Datetime.now(),
                'stage_timer_finished_at': False,
                'stage_timer_paused_at': False,
                'stage_timer_paused_work_hours': 0.0,
            })
        return True

    def _start_batch(self):
        result = super()._start_batch()
        self._stage_timer_initialize_if_needed(started_at=self.started_at)
        return result

    def _finish_batch(self):
        result = super()._finish_batch()
        now = self.finished_at or fields.Datetime.now()
        Standard = self.env['furniture.mrp.stage.time.standard']
        paused_hours = self.stage_timer_paused_work_hours or 0.0
        if self.stage_timer_paused_at:
            paused_hours += Standard._stage_timer_elapsed_hours(
                self, self.company_id, self.stage_timer_paused_at, now,
            )
        if self.stage_timer_started_at and not self.stage_timer_finished_at:
            self.sudo().write({
                'stage_timer_finished_at': now,
                'stage_timer_paused_at': False,
                'stage_timer_paused_work_hours': paused_hours,
            })
        return result

    def _dashboard_payload(self):
        payload = super()._dashboard_payload()
        timer = self.env[
            'furniture.mrp.stage.time.standard'
        ]._stage_timer_payload(self, self._effective_state())
        payload['timer'] = timer
        return payload

    def action_pause_stage_timer(self):
        self.ensure_one()
        self._stage_timer_initialize_if_needed()
        if self._effective_state() != 'in_progress':
            raise UserError(_('Only a running product batch can be paused.'))
        if not self.stage_timer_paused_at:
            self.sudo().write({'stage_timer_paused_at': fields.Datetime.now()})
        return self._result_envelope()

    def action_resume_stage_timer(self):
        self.ensure_one()
        if not self.stage_timer_paused_at:
            return self._result_envelope()
        now = fields.Datetime.now()
        Standard = self.env['furniture.mrp.stage.time.standard']
        paused_hours = (
            self.stage_timer_paused_work_hours or 0.0
        ) + Standard._stage_timer_elapsed_hours(
            self, self.company_id, self.stage_timer_paused_at, now,
        )
        self.sudo().write({
            'stage_timer_paused_at': False,
            'stage_timer_paused_work_hours': paused_hours,
        })
        return self._result_envelope()


class FurnitureMrpProductionStageTimer(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _stage_dashboard_order_payload(self, *args, **kwargs):
        payload = super()._stage_dashboard_order_payload(*args, **kwargs)
        stage_order = args[3] if len(args) > 3 else kwargs.get('stage_order')
        payload['timer'] = (
            stage_order._stage_timer_dashboard_payload()
            if stage_order else False
        )
        return payload

    def _stage_timer_dashboard_order_record(self, stage_code):
        self.ensure_one()
        self._stage_dashboard_check_stage_access(stage_code)
        if stage_code not in FURNITURE_STAGE_FIELD_MAP:
            raise ValidationError(_('Invalid production stage.'))
        if self.company_id not in self.env.companies:
            raise AccessError(_('This production order is outside your active companies.'))
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [self.id],
        )
        order_field = FURNITURE_STAGE_FIELD_MAP[stage_code][1]
        self.invalidate_recordset([order_field])
        return self[order_field]

    def action_stage_dashboard_pause_order_timer(self, stage_code):
        self.ensure_one()
        stage_order = self._stage_timer_dashboard_order_record(stage_code)
        if not stage_order:
            raise UserError(_('The stage has not started yet.'))
        stage_order.action_pause_stage_timer()
        return True

    def action_stage_dashboard_resume_order_timer(self, stage_code):
        self.ensure_one()
        stage_order = self._stage_timer_dashboard_order_record(stage_code)
        if not stage_order:
            raise UserError(_('The stage has not started yet.'))
        stage_order.action_resume_stage_timer()
        return True

    @api.model
    def action_stage_dashboard_pause_product_batch_timer(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        batch, _group = Batch._resolve_batch_token(
            batch_token, stage_code, allow_virtual=False,
        )
        return batch.action_pause_stage_timer()

    @api.model
    def action_stage_dashboard_resume_product_batch_timer(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        batch, _group = Batch._resolve_batch_token(
            batch_token, stage_code, allow_virtual=False,
        )
        return batch.action_resume_stage_timer()
