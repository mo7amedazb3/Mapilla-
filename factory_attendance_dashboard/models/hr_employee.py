# -*- coding: utf-8 -*-

from collections import defaultdict
from datetime import datetime, time, timedelta
import json

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import format_date


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.model
    def _factory_attendance_excluded_employee_ids(self):
        """Database-local display exemptions, without archiving real employees."""
        value = self.env["ir.config_parameter"].sudo().get_param(
            "factory_attendance_dashboard.excluded_employee_ids", "[]"
        )
        try:
            employee_ids = json.loads(value)
        except (TypeError, ValueError):
            employee_ids = None
        if not isinstance(employee_ids, list) or any(
            type(employee_id) is not int or employee_id <= 0
            for employee_id in employee_ids
        ):
            raise ValidationError(_("إعداد الموظفين المستثنين من لوحة الحضور غير صحيح."))
        return employee_ids

    @api.model
    def _factory_attendance_check_access(self):
        if not self.env.user.has_group(
            "hr_attendance.group_hr_attendance_officer"
        ):
            raise AccessError(_("لوحة الحضور متاحة لمسؤولي الحضور فقط."))

    @api.model
    def _factory_attendance_selected_date(self, selected_date=False):
        today = fields.Date.context_today(self)
        value = fields.Date.to_date(selected_date) if selected_date else today
        if value > today:
            raise ValidationError(_("لا يمكن عرض أو تعديل تاريخ في المستقبل."))
        return value, today

    @api.model
    def _factory_attendance_day_bounds(self, selected_date, timezone_name):
        try:
            zone = pytz.timezone(timezone_name or "Africa/Cairo")
        except pytz.UnknownTimeZoneError:
            zone = pytz.timezone("Africa/Cairo")
        local_start = zone.localize(datetime.combine(selected_date, time.min))
        local_end = local_start + timedelta(days=1)
        return (
            zone,
            local_start.astimezone(pytz.UTC).replace(tzinfo=None),
            local_end.astimezone(pytz.UTC).replace(tzinfo=None),
        )

    @api.model
    def _factory_attendance_local_datetime(self, value, zone):
        if not value:
            return False
        return pytz.UTC.localize(fields.Datetime.to_datetime(value)).astimezone(zone)

    @api.model
    def _factory_attendance_format_hour(self, hour):
        total_minutes = int(round((hour or 0.0) * 60))
        hours, minutes = divmod(total_minutes, 60)
        return "%02d:%02d" % (min(hours, 24), minutes)

    def _factory_attendance_schedule(self, selected_date):
        self.ensure_one()
        calendar = self.resource_calendar_id or self.company_id.resource_calendar_id
        if not calendar:
            return {
                "label": _("غير محدد"),
                "start_hour": False,
                "end_hour": False,
                "is_workday": False,
            }
        week_type = str(
            self.env["resource.calendar.attendance"].get_week_type(selected_date)
        )
        lines = calendar.attendance_ids.filtered(
            lambda line: not line.display_type
            and line.day_period != "lunch"
            and line.dayofweek == str(selected_date.weekday())
            and (not line.date_from or line.date_from <= selected_date)
            and (not line.date_to or line.date_to >= selected_date)
            and (not calendar.two_weeks_calendar or line.week_type == week_type)
        )
        if not lines:
            return {
                "label": _("راحة أسبوعية"),
                "start_hour": False,
                "end_hour": False,
                "is_workday": False,
            }
        start_hour = min(lines.mapped("hour_from"))
        end_hour = max(lines.mapped("hour_to"))
        return {
            "label": "%s - %s"
            % (
                self._factory_attendance_format_hour(start_hour),
                self._factory_attendance_format_hour(end_hour),
            ),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "is_workday": True,
        }

    @api.model
    def _factory_attendance_minutes_label(self, minutes):
        minutes = max(0, int(round(minutes or 0)))
        if not minutes:
            return "—"
        hours, remainder = divmod(minutes, 60)
        if hours and remainder:
            return _("%(hours)s س %(minutes)s د", hours=hours, minutes=remainder)
        if hours:
            return _("%(hours)s ساعة", hours=hours)
        return _("%(minutes)s دقيقة", minutes=remainder)

    @api.model
    def factory_attendance_dashboard_data(
        self, department_id=False, status="all", selected_date=False, pay_basis=False
    ):
        self._factory_attendance_check_access()
        selected_date, today = self._factory_attendance_selected_date(selected_date)
        now = fields.Datetime.now()
        company = self.env.company
        company_tz = (
            self.env.user.tz
            or company.resource_calendar_id.tz
            or "Africa/Cairo"
        )
        company_zone, day_start_utc, day_end_utc = self._factory_attendance_day_bounds(
            selected_date, company_tz
        )

        employee_domain = [
            ("active", "=", True),
            ("company_id", "=", company.id),
            ("id", "not in", self._factory_attendance_excluded_employee_ids()),
        ]
        if pay_basis in ('time', 'production') and 'furniture_pay_basis' in self._fields:
            employee_domain.append(('furniture_pay_basis', '=', pay_basis))
        if department_id:
            employee_domain.append(("department_id", "=", int(department_id)))
        employees = self.sudo().search(employee_domain, order="name, id")

        departments = self.env["hr.department"].sudo().search(
            [("company_id", "in", [False, company.id])], order="name, id"
        )
        if not employees:
            return self._factory_attendance_dashboard_payload(
                selected_date,
                today,
                department_id,
                status,
                departments,
                [],
            )

        attendances = self.env["hr.attendance"].sudo().search(
            [
                ("employee_id", "in", employees.ids),
                ("check_in", ">=", day_start_utc - timedelta(days=1)),
                ("check_in", "<", day_end_utc + timedelta(days=1)),
            ],
            order="check_in, id",
        )
        attendance_by_employee = defaultdict(lambda: self.env["hr.attendance"])
        for attendance in attendances:
            attendance_by_employee[attendance.employee_id.id] |= attendance

        leaves = self.env["hr.leave"].sudo().search(
            [
                ("employee_id", "in", employees.ids),
                ("state", "=", "validate"),
                ("date_from", "<", day_end_utc),
                ("date_to", ">", day_start_utc),
            ]
        )
        leave_by_employee = {
            leave.employee_id.id: leave.holiday_status_id.display_name
            for leave in leaves
        }

        identities = self.env["factory.biometric.identity"].sudo().search(
            [
                ("employee_id", "in", employees.ids),
                ("active", "=", True),
                ("device_id.state", "=", "active"),
            ],
            order="employee_id, id",
        )
        pin_by_employee = {}
        for identity in identities:
            pin_by_employee.setdefault(identity.employee_id.id, identity.device_user_id)

        rows = []
        next_status_change_delay_ms = False
        for employee in employees:
            employee_tz = employee._get_tz() or company_tz
            zone, employee_day_start_utc, employee_day_end_utc = self._factory_attendance_day_bounds(
                selected_date, employee_tz
            )
            schedule = employee._factory_attendance_schedule(selected_date)
            # The daily dashboard belongs to the local day on which the
            # attendance started.  Never pull an older open attendance into a
            # later day: its old check-in would turn the day gap into bogus
            # early-arrival overtime (40+ hours in the UI).
            employee_attendances = attendance_by_employee[employee.id].filtered(
                lambda item: employee_day_start_utc
                <= item.check_in
                < employee_day_end_utc
            )
            leave_type = leave_by_employee.get(employee.id, "")
            first_check_in = (
                min(employee_attendances.mapped("check_in"))
                if employee_attendances
                else False
            )
            closed_check_outs = employee_attendances.filtered("check_out").mapped(
                "check_out"
            )
            last_check_out = max(closed_check_outs) if closed_check_outs else False
            has_open = bool(employee_attendances.filtered(lambda item: not item.check_out))
            scheduled_start = (
                zone.localize(
                    datetime.combine(selected_date, time.min)
                    + timedelta(hours=schedule["start_hour"])
                ) if schedule["is_workday"] else False
            )

            if has_open:
                row_status = "at_work"
            elif employee_attendances:
                row_status = "checked_out"
            elif leave_type or not schedule["is_workday"]:
                row_status = "leave"
                leave_type = leave_type or _("راحة أسبوعية")
            elif self._factory_attendance_local_datetime(now, zone) < scheduled_start:
                # Report-only status: no absence before this employee's shift.
                # Use absolute instants so older dates and timezones stay correct.
                row_status = "not_started"
                remaining = scheduled_start - self._factory_attendance_local_datetime(now, zone)
                delay_ms = int(remaining.total_seconds() * 1000) + 1
                next_status_change_delay_ms = min(next_status_change_delay_ms or delay_ms, delay_ms)
            else:
                row_status = "absent"

            late_minutes = 0
            early_departure_minutes = 0
            overtime_minutes = 0
            if first_check_in and schedule["is_workday"]:
                local_check_in = self._factory_attendance_local_datetime(
                    first_check_in, zone
                )
                scheduled_end = zone.localize(
                    datetime.combine(selected_date, time.min)
                    + timedelta(hours=schedule["end_hour"])
                )
                late_minutes = max(
                    0, (local_check_in - scheduled_start).total_seconds() / 60
                )
                overtime_minutes += max(
                    0, (scheduled_start - local_check_in).total_seconds() / 60
                )
                if last_check_out:
                    local_check_out = self._factory_attendance_local_datetime(
                        last_check_out, zone
                    )
                    early_departure_minutes = max(
                        0, (scheduled_end - local_check_out).total_seconds() / 60
                    )
                    overtime_minutes += max(
                        0, (local_check_out - scheduled_end).total_seconds() / 60
                    )

            if employee_attendances and hasattr(employee, '_factory_net_overtime'):
                last = employee_attendances.sorted(lambda r: (r.check_in, r.id))[-1]
                shift = employee._factory_overtime_shift(last)
                if shift:
                    net = employee._factory_net_overtime(last, shift, now)
                    late_minutes = net['remaining_late_minutes']
                    overtime_minutes = net['eligible_minutes']

            delay_parts = []
            if late_minutes >= 16:
                delay_parts.append(
                    _("تأخير %s")
                    % self._factory_attendance_minutes_label(late_minutes)
                )
            if early_departure_minutes:
                delay_parts.append(
                    _("انصراف مبكر %s")
                    % self._factory_attendance_minutes_label(
                        early_departure_minutes
                    )
                )

            # The source describes the displayed first check-in. Device IDs are
            # historical links and do not make a manually corrected time a punch.
            first_attendance = employee_attendances.sorted(lambda item: (item.check_in, item.id))[:1]
            biometric_source = bool(first_attendance and first_attendance.in_mode == "biometric")
            rows.append(
                {
                    "id": employee.id,
                    "name": employee.name,
                    "department": employee.department_id.display_name or "—",
                    "employee_code": employee.barcode or "—",
                    "fingerprint_pin": pin_by_employee.get(employee.id, "—"),
                    "work_schedule": schedule["label"],
                    "check_in": self._factory_attendance_display_time(
                        first_check_in, zone
                    ),
                    "check_out": "—"
                    if has_open
                    else self._factory_attendance_display_time(last_check_out, zone),
                    "late_minutes": int(round(late_minutes)),
                    "show_lateness": late_minutes >= 16,
                    "early_departure_minutes": int(
                        round(early_departure_minutes)
                    ),
                    "delay_label": " · ".join(delay_parts),
                    "overtime_minutes": int(round(overtime_minutes)),
                    "overtime_label": self._factory_attendance_minutes_label(
                        overtime_minutes
                    ),
                    "status": row_status,
                    "is_currently_checked_in": has_open,
                    "manual_attendance_enabled": True,
                    "leave_type": leave_type,
                    "source": _("بصمة") if biometric_source else (
                        _("يدوي") if employee_attendances else "—"
                    ),
                    "source_code": "biometric" if biometric_source else (
                        "manual" if employee_attendances else "none"
                    ),
                }
            )

        counts = {
            "all": len(rows),
            "at_work": sum(row["status"] == "at_work" for row in rows),
            "checked_out": sum(row["status"] == "checked_out" for row in rows),
            "absent": sum(row["status"] == "absent" for row in rows),
            "not_started": sum(row["status"] == "not_started" for row in rows),
            "leave": sum(row["status"] == "leave" for row in rows),
            "late": sum(row["show_lateness"] for row in rows),
            "overtime": sum(row["overtime_minutes"] > 0 for row in rows),
        }
        allowed_statuses = {
            "all", "at_work", "checked_out", "absent", "leave", "late", "overtime",
        }
        status = status if status in allowed_statuses else "all"
        if status in {"at_work", "checked_out", "absent", "leave"}:
            rows = [row for row in rows if row["status"] == status]
        elif status == "late":
            rows = [row for row in rows if row["show_lateness"]]
        elif status == "overtime":
            rows = [row for row in rows if row["overtime_minutes"] > 0]

        data = self._factory_attendance_dashboard_payload(
            selected_date,
            today,
            department_id,
            status,
            departments,
            rows,
            counts=counts,
        )
        data["next_status_change_delay_ms"] = next_status_change_delay_ms
        return data

    @api.model
    def _factory_attendance_display_time(self, value, zone):
        local_value = self._factory_attendance_local_datetime(value, zone)
        return local_value.strftime("%H:%M") if local_value else "—"

    @api.model
    def _factory_attendance_dashboard_payload(
        self,
        selected_date,
        today,
        department_id,
        status,
        departments,
        rows,
        counts=None,
    ):
        return {
            "date": fields.Date.to_string(selected_date),
            "date_label": format_date(self.env, selected_date),
            "today": fields.Date.to_string(today),
            "server_time": fields.Datetime.context_timestamp(
                self, fields.Datetime.now()
            ).strftime("%H:%M"),
            "selected_department_id": int(department_id) if department_id else False,
            "selected_status": status or "all",
            "can_configure_absence_allowance": self.env.user.has_group('base.group_system'),
            "quarter_absence_enabled": self.env['ir.config_parameter'].sudo().get_param(
                'factory_attendance.quarter_absence.%s' % self.env.company.id, 'True') == 'True',
            "can_manage_manual_attendance": self.env.user.has_group(
                "hr_attendance.group_hr_attendance_manager"
            ),
            "departments": [
                {"id": department.id, "name": department.display_name}
                for department in departments
            ],
            "counts": counts
            or {
                "all": 0,
                "at_work": 0,
                "checked_out": 0,
                "absent": 0,
                "not_started": 0,
                "leave": 0,
                "late": 0,
                "overtime": 0,
            },
            "employees": rows,
        }

    @api.model
    def factory_manual_attendance_details(self, employee_id, selected_date=False):
        self._factory_attendance_check_access()
        if not self.env.user.has_group(
            "hr_attendance.group_hr_attendance_manager"
        ):
            raise AccessError(_("التعديل اليدوي متاح لمدير الحضور فقط."))
        selected_date, today = self._factory_attendance_selected_date(selected_date)
        employee = self.sudo().browse(int(employee_id)).exists()
        if not employee or employee.company_id != self.env.company:
            raise ValidationError(_("الموظف غير موجود في الشركة الحالية."))
        timezone_name = employee._get_tz() or "Africa/Cairo"
        zone, day_start_utc, day_end_utc = self._factory_attendance_day_bounds(
            selected_date, timezone_name
        )
        attendances = self.env["hr.attendance"].sudo().search(
            [
                ("employee_id", "=", employee.id),
                ("check_in", ">=", day_start_utc),
                ("check_in", "<", day_end_utc),
            ],
            order="check_in desc, id desc",
        )
        # Never offer a previous-day row as today's editable attendance.
        records = []
        for attendance in attendances.sorted("check_in", reverse=True):
            local_in = self._factory_attendance_local_datetime(attendance.check_in, zone)
            local_out = self._factory_attendance_local_datetime(attendance.check_out, zone)
            records.append(
                {
                    "id": attendance.id,
                    "check_in": local_in.strftime("%Y-%m-%dT%H:%M"),
                    "check_out": local_out.strftime("%Y-%m-%dT%H:%M") if local_out else "",
                    "label": "%s — %s"
                    % (
                        local_in.strftime("%Y-%m-%d %H:%M"),
                        local_out.strftime("%H:%M") if local_out else _("مفتوح"),
                    ),
                    "is_stale": bool(
                        not attendance.check_out and attendance.check_in < day_start_utc
                    ),
                }
            )
        return {
            "employee_name": employee.name,
            "selected_date": fields.Date.to_string(selected_date),
            "today": fields.Date.to_string(today),
            "timezone": timezone_name,
            "records": records,
            "default_attendance_id": records[0]["id"] if records else False,
        }

    @api.model
    def _factory_manual_datetime_to_utc(self, value, timezone_name):
        value = (value or "").strip()
        if not value:
            return False
        parsed = False
        for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
        if not parsed:
            raise ValidationError(_("صيغة التاريخ والوقت غير صحيحة."))
        zone = pytz.timezone(timezone_name or "Africa/Cairo")
        try:
            localized = zone.localize(parsed, is_dst=None)
        except pytz.AmbiguousTimeError:
            localized = zone.localize(parsed, is_dst=False)
        except pytz.NonExistentTimeError:
            localized = zone.localize(parsed + timedelta(hours=1), is_dst=True)
        return localized.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def factory_save_manual_attendance(
        self, employee_id, attendance_id, check_in, check_out=False
    ):
        self._factory_attendance_check_access()
        if not self.env.user.has_group(
            "hr_attendance.group_hr_attendance_manager"
        ):
            raise AccessError(_("التعديل اليدوي متاح لمدير الحضور فقط."))
        employee = self.sudo().browse(int(employee_id)).exists()
        if not employee or employee.company_id != self.env.company:
            raise ValidationError(_("الموظف غير موجود في الشركة الحالية."))
        timezone_name = employee._get_tz() or "Africa/Cairo"
        check_in_utc = self._factory_manual_datetime_to_utc(check_in, timezone_name)
        check_out_utc = self._factory_manual_datetime_to_utc(check_out, timezone_name)
        if not check_in_utc:
            raise ValidationError(_("وقت الحضور مطلوب."))
        if check_in_utc > fields.Datetime.now() + timedelta(minutes=5):
            raise ValidationError(_("وقت الحضور لا يمكن أن يكون في المستقبل."))
        if check_out_utc and check_out_utc <= check_in_utc:
            raise ValidationError(_("وقت الانصراف يجب أن يكون بعد وقت الحضور."))

        attendance_model = self.env["hr.attendance"].sudo()
        values = {
            "employee_id": employee.id,
            "check_in": check_in_utc,
            "check_out": check_out_utc or False,
            "in_mode": "manual",
            "out_mode": "manual" if check_out_utc else False,
            "biometric_in_device_id": False,
            "biometric_out_device_id": False,
            "factory_manual_edited_by_id": self.env.user.id,
            "factory_manual_edited_at": fields.Datetime.now(),
        }
        if attendance_id:
            attendance = attendance_model.browse(int(attendance_id)).exists()
            if not attendance or attendance.employee_id != employee:
                raise ValidationError(_("سجل الحضور لا يخص هذا الموظف."))
            old_day = self._factory_attendance_local_datetime(attendance.check_in, pytz.timezone(timezone_name)).date()
            new_day = self._factory_attendance_local_datetime(check_in_utc, pytz.timezone(timezone_name)).date()
            if old_day != new_day:
                raise ValidationError(_("لا تنقل سجل حضور من يوم إلى يوم آخر؛ أضف حضورًا جديدًا لليوم المطلوب."))
            attendance.write(values)
            action = "updated"
        else:
            attendance = attendance_model.create(values)
            action = "created"
        return {
            "action": action,
            "attendance_id": attendance.id,
            "employee_name": employee.name,
        }

    @api.model
    def factory_set_quarter_absence_enabled(self, enabled):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('هذا الإعداد متاح للأدمن فقط.'))
        if type(enabled) is not bool:
            raise ValidationError(_('قيمة الإعداد غير صحيحة.'))
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (82470, self.env.company.id))
        key = 'factory_attendance.quarter_absence.%s' % self.env.company.id
        self.env['ir.config_parameter'].sudo().set_param(key, 'True' if enabled else 'False')
        if 'simple.payroll.slip' in self.env:
            slips = self.env['simple.payroll.slip'].sudo().search([
                ('company_id', '=', self.env.company.id), ('state', '=', 'draft')])
            for employee in slips.employee_id:
                with self._factory_overtime_preserve_final_payroll(employee):
                    slips.filtered(lambda s: s.employee_id == employee).action_recompute_factory_payroll()
        return {'enabled': enabled}
