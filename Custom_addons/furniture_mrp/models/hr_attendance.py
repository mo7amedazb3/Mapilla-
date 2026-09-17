# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

from .mrp_stage_mixin import STAGE_WORKER_LOG_MAP


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    furniture_mrp_waiting_stage_model = fields.Char(
        string='موديل آخر مرحلة محملة بالانتظار', readonly=True, copy=False,
    )
    furniture_mrp_waiting_stage_res_id = fields.Integer(
        string='رقم آخر مرحلة محملة بالانتظار', readonly=True, copy=False,
    )
    furniture_mrp_post_waiting_hours = fields.Float(
        string='انتظار بعد آخر تشغيل', readonly=True, copy=False, digits=(16, 2),
    )
    furniture_mrp_post_waiting_cost = fields.Float(
        string='تكلفة انتظار بعد آخر تشغيل', readonly=True, copy=False, digits=(16, 2),
    )
    furniture_mrp_cost_adjustment_json = fields.Json(
        string='تفاصيل تحميل انتظار الانصراف', readonly=True, copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        attendances = super().create(vals_list)
        if not self.env.context.get('furniture_skip_waiting_allocation'):
            attendances.filtered('check_out')._furniture_recompute_post_waiting()
        return attendances

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get('furniture_skip_waiting_allocation')
            and {'employee_id', 'check_in', 'check_out'} & set(vals)
        ):
            self._furniture_recompute_post_waiting()
        return result

    def unlink(self):
        if not self.env.context.get('furniture_skip_waiting_allocation'):
            for attendance in self:
                attendance._furniture_reverse_post_waiting()
        return super().unlink()

    def _furniture_allocated_stage(self):
        self.ensure_one()
        model_name = self.furniture_mrp_waiting_stage_model
        if not model_name or not self.furniture_mrp_waiting_stage_res_id:
            return False
        try:
            stage_model = self.env[model_name]
        except KeyError:
            return False
        return stage_model.sudo().browse(
            self.furniture_mrp_waiting_stage_res_id,
        ).exists()

    def _furniture_update_stage_waiting(self, stage_order, hours_delta, cost_delta):
        if not stage_order:
            return
        stage_order.sudo().write({
            'waiting_labor_hours': max((stage_order.waiting_labor_hours or 0.0) + hours_delta, 0.0),
            'waiting_labor_cost': max((stage_order.waiting_labor_cost or 0.0) + cost_delta, 0.0),
        })

    def _furniture_adjust_cost_entries(self, stage_order, cost_delta):
        """Apply checkout waiting to the latest accepted batch and return exact deltas."""
        if not stage_order or not cost_delta:
            return []
        CostEntry = self.env['furniture.mrp.stage.cost.entry'].sudo()
        entries = CostEntry.search([
            ('stage_order_model', '=', stage_order._name),
            ('stage_order_res_id', '=', stage_order.id),
        ], order='accepted_at desc, id desc')
        if not entries:
            # If quality has not accepted the batch yet, _record_stage_costs will
            # include the updated stage waiting total when it freezes the cost.
            return []

        latest_at = entries[0].accepted_at
        target_entries = entries.filtered(lambda entry: entry.accepted_at == latest_at)
        total_qty = sum(target_entries.mapped('quantity'))
        changes = []
        for entry in target_entries:
            share = (
                cost_delta * (entry.quantity or 0.0) / total_qty
                if total_qty else cost_delta / len(target_entries)
            )
            entry.write({
                'stage_labor_cost': max((entry.stage_labor_cost or 0.0) + share, 0.0),
                'cumulative_labor_cost': max((entry.cumulative_labor_cost or 0.0) + share, 0.0),
            })
            changes.append({
                'entry_id': entry.id,
                'stage_labor_delta': share,
                'previous_labor_delta': 0.0,
                'cumulative_labor_delta': share,
            })

            downstream = CostEntry.search([
                ('production_line_id', '=', entry.production_line_id.id),
                ('id', '!=', entry.id),
                ('accepted_at', '>', entry.accepted_at),
            ])
            for later_entry in downstream:
                later_entry.write({
                    'previous_labor_cost': max((later_entry.previous_labor_cost or 0.0) + share, 0.0),
                    'cumulative_labor_cost': max((later_entry.cumulative_labor_cost or 0.0) + share, 0.0),
                })
                changes.append({
                    'entry_id': later_entry.id,
                    'stage_labor_delta': 0.0,
                    'previous_labor_delta': share,
                    'cumulative_labor_delta': share,
                })
        return changes

    def _furniture_reverse_cost_adjustments(self, stage_order):
        self.ensure_one()
        changes = self.furniture_mrp_cost_adjustment_json or []
        Entry = self.env['furniture.mrp.stage.cost.entry'].sudo()
        if changes:
            for change in changes:
                entry = Entry.browse(change.get('entry_id')).exists()
                if not entry:
                    continue
                entry.write({
                    'stage_labor_cost': max(
                        (entry.stage_labor_cost or 0.0) - (change.get('stage_labor_delta') or 0.0), 0.0,
                    ),
                    'previous_labor_cost': max(
                        (entry.previous_labor_cost or 0.0) - (change.get('previous_labor_delta') or 0.0), 0.0,
                    ),
                    'cumulative_labor_cost': max(
                        (entry.cumulative_labor_cost or 0.0) - (change.get('cumulative_labor_delta') or 0.0), 0.0,
                    ),
                })
            return
        # The attendance may have been checked out before quality approval. In
        # that case the frozen entry later absorbed the waiting total normally.
        # Remove it from the latest batch if checkout is edited or cancelled.
        if stage_order and self.furniture_mrp_post_waiting_cost:
            self._furniture_adjust_cost_entries(
                stage_order, -(self.furniture_mrp_post_waiting_cost or 0.0),
            )

    def _furniture_reverse_post_waiting(self):
        self.ensure_one()
        stage_order = self._furniture_allocated_stage()
        hours = self.furniture_mrp_post_waiting_hours or 0.0
        cost = self.furniture_mrp_post_waiting_cost or 0.0
        if stage_order and (hours or cost):
            self._furniture_reverse_cost_adjustments(stage_order)
            self._furniture_update_stage_waiting(stage_order, -hours, -cost)
        self.with_context(furniture_skip_waiting_allocation=True).sudo().write({
            'furniture_mrp_waiting_stage_model': False,
            'furniture_mrp_waiting_stage_res_id': 0,
            'furniture_mrp_post_waiting_hours': 0.0,
            'furniture_mrp_post_waiting_cost': 0.0,
            'furniture_mrp_cost_adjustment_json': False,
        })

    def _furniture_latest_work_boundary(self, employee, check_in, check_out):
        user = employee.user_id
        if not user:
            return False, False, check_in
        latest_log = False
        latest_stage = False
        latest_boundary = check_in
        for log_model_name, inverse_field in STAGE_WORKER_LOG_MAP.values():
            logs = self.env[log_model_name].sudo().search([
                ('user_id', '=', user.id),
                ('start_datetime', '<', fields.Datetime.to_string(check_out)),
                '|',
                ('end_datetime', '=', False),
                ('end_datetime', '>', fields.Datetime.to_string(check_in)),
            ], order='start_datetime desc, id desc')
            for log in logs:
                log_start = fields.Datetime.to_datetime(log.start_datetime)
                effective_end = (
                    min(fields.Datetime.to_datetime(log.end_datetime), check_out)
                    if log.end_datetime else check_out
                )
                if log_start >= check_out or effective_end <= check_in:
                    continue
                if effective_end >= latest_boundary:
                    latest_boundary = effective_end
                    latest_log = log
                    latest_stage = log[inverse_field]
        return latest_log, latest_stage, latest_boundary

    def _furniture_recompute_post_waiting(self):
        for attendance in self:
            attendance._furniture_reverse_post_waiting()
            if not attendance.employee_id or not attendance.check_in or not attendance.check_out:
                continue
            check_in = fields.Datetime.to_datetime(attendance.check_in)
            check_out = fields.Datetime.to_datetime(attendance.check_out)
            if check_out <= check_in:
                continue
            latest_log, stage_order, latest_boundary = attendance._furniture_latest_work_boundary(
                attendance.employee_id.sudo(), check_in, check_out,
            )
            if not latest_log or not stage_order or latest_boundary >= check_out:
                continue
            waiting_hours = max((check_out - latest_boundary).total_seconds() / 3600.0, 0.0)
            if not waiting_hours:
                continue
            hourly_rate = latest_log.hourly_rate or stage_order._get_labor_user_hourly_rate(
                attendance.employee_id.sudo(), check_out,
            )
            waiting_cost = waiting_hours * (hourly_rate or 0.0)
            attendance._furniture_update_stage_waiting(stage_order, waiting_hours, waiting_cost)
            changes = attendance._furniture_adjust_cost_entries(stage_order, waiting_cost)
            attendance.with_context(furniture_skip_waiting_allocation=True).sudo().write({
                'furniture_mrp_waiting_stage_model': stage_order._name,
                'furniture_mrp_waiting_stage_res_id': stage_order.id,
                'furniture_mrp_post_waiting_hours': waiting_hours,
                'furniture_mrp_post_waiting_cost': waiting_cost,
                'furniture_mrp_cost_adjustment_json': changes or False,
            })
            stage_order.sudo().message_post(body=_(
                '⏳ تم تحميل انتظار ما بعد آخر تشغيل للعامل %(employee)s: '
                '%(hours).2f ساعة بتكلفة %(cost).2f عند تسجيل الانصراف.'
            ) % {
                'employee': attendance.employee_id.name,
                'hours': waiting_hours,
                'cost': waiting_cost,
            })
