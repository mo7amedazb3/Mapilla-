from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models

from .lunch_policy import summarize_day


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    factory_lunch_enabled = fields.Boolean(
        string="له غداء", default=True,
        help="عند إيقافه تُعامل بصمات الخروج كانصراف عادي، ولا تُحسب استراحة أو ساعات غداء مدفوعة.",
    )

    def _factory_paid_lunch_seconds(self, intervals, day, zone, now=None):
        """Credit at most one confirmed paid lunch hour per day."""
        self.ensure_one()
        return summarize_day(intervals, day, zone, now or fields.Datetime.now(), self.factory_lunch_enabled)["lunch_seconds"]

    def _factory_unpaid_lunch_seconds(self, intervals, day, zone, now=None):
        """Return only the confirmed lunch-gap excess beyond the paid hour."""
        self.ensure_one()
        return summarize_day(
            intervals,
            day,
            zone,
            now or fields.Datetime.now(),
            self.factory_lunch_enabled,
        )["unpaid_lunch_seconds"]

    def _factory_paid_lunch_hours(self, start_utc, end_utc, zone=None):
        """Read-only payroll credit clipped to the caller's exact UTC period.

        Include open afternoon rows to recognize a real return. Never alter
        hr.attendance.worked_hours, punches, physical presence or wage rules.
        """
        self.ensure_one()
        if not self.factory_lunch_enabled or end_utc <= start_utc:
            return 0.0
        zone = zone or pytz.timezone(self._get_tz() or "Africa/Cairo")
        day = pytz.UTC.localize(start_utc).astimezone(zone).date()
        last_day = pytz.UTC.localize(end_utc - timedelta(microseconds=1)).astimezone(zone).date()
        lookup_start = zone.localize(datetime.combine(day, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
        lookup_end = zone.localize(datetime.combine(last_day + timedelta(days=1), time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
        rows = self.env["hr.attendance"].sudo().search([
            ("employee_id", "=", self.id), ("check_in", "<", lookup_end),
            "|", ("check_out", "=", False), ("check_out", ">", lookup_start),
        ], order="check_in, id")
        intervals = [(row.check_in, row.check_out) for row in rows]
        seconds = 0.0
        now = fields.Datetime.now()
        while day <= last_day:
            summary = summarize_day(intervals, day, zone, now)
            for gap in summary["gaps"]:
                if gap["state"] == "returned" and gap["paid_seconds"]:
                    paid_stop = gap["start"] + timedelta(seconds=gap["paid_seconds"])
                    seconds += max(0, (min(paid_stop, end_utc)
                                       - max(gap["start"], start_utc)).total_seconds())
            day += timedelta(days=1)
        return seconds / 3600.0

    def _factory_attendance_section(self):
        """Display grouping only; never change HR departments or stage assignments.

        Operational stages disambiguate the broad HR carpentry/finishing teams.
        A multi-stage worker/supervisor appears once, at their earliest stage.
        If stage data is absent, use the actual HR department before the legacy
        factory-department field (the latter may be out of date).
        """
        self.ensure_one()
        stages = [
            ("priming", _("التقديم")), ("carpentry", _("التجميع")),
            ("bases", _("القواعد")), ("finishing", _("التجهيز")),
            ("tailoring", _("التفصيل")), ("sewing", _("الخياطة")),
            ("painting", _("تصنيع الدهانات")), ("upholstery", _("الكسوة")),
            ("packaging", _("التغليف")),
        ]
        ranks = {code: (rank, label) for rank, (code, label) in enumerate(stages)}
        stage_field = ("furniture_mrp_supervisor_stage_ids"
                       if "furniture_mrp_role" in self._fields and self.furniture_mrp_role == "supervisor"
                       else "furniture_mrp_worker_stage_ids")
        codes = set(self[stage_field].filtered("active").mapped("code")) if stage_field in self._fields else set()
        matched = sorted(codes & ranks.keys(), key=lambda code: ranks[code][0])
        if matched:
            code = matched[0]
            rank, label = ranks[code]
            return rank, "stage:" + code, label

        aliases = {
            "تقديم": "priming", "التقديم": "priming",
            "تجميع": "carpentry", "التجميع": "carpentry", "قواعد": "bases", "القواعد": "bases",
            "تجهيز": "finishing", "التجهيز": "finishing", "تفصيل": "tailoring", "التفصيل": "tailoring",
            "خياطة": "sewing", "الخياطة": "sewing", "دهانات": "painting", "الدهانات": "painting",
            "تصنيع دهانات": "painting", "تصنيع الدهانات": "painting",
            "كسوة": "upholstery", "كسوه": "upholstery", "الكسوة": "upholstery",
            "تغليف": "packaging", "التغليف": "packaging",
        }
        department = self.department_id
        department_name = (department.with_context(lang="en_US").name or "").strip()
        if department_name in {"نجارة", "النجارة"}:
            return 0.5, "department:%s" % department.id, department.display_name
        code = aliases.get(department_name)
        if not department and "furniture_mrp_factory_department" in self._fields:
            code = self.furniture_mrp_factory_department
            # The administrative carpentry category is not the assembly stage.
            if code == "carpentry":
                return 0.5, "legacy:carpentry", _("نجارة")
        if code in ranks:
            rank, label = ranks[code]
            return rank, "stage:" + code, label
        return 100, "department:%s" % (department.id or 0), department.display_name or _("بدون قسم")

    @api.model
    def _factory_attendance_group_rows(self, rows, employee_map):
        """Keep time-paid teams first and all piece-paid workers at the bottom."""
        ordered = []
        for row in rows:
            employee = employee_map[row["id"]]
            rank, key, label = employee._factory_attendance_section()
            production = ("furniture_pay_basis" in employee._fields
                          and employee.furniture_pay_basis == "production")
            group_key = ("production:" if production else "time:") + key
            group_label = _("بالإنتاج / القطعة — %s") % label if production else label
            ordered.append(((int(production), rank, key, (row["name"] or "").casefold(), row["id"]),
                            group_key, group_label, row))
        ordered.sort(key=lambda item: item[0])
        groups = []
        for _sort, key, label, row in ordered:
            if not groups or groups[-1]["key"] != key:
                groups.append({"key": key, "label": label, "employees": []})
            groups[-1]["employees"].append(row)
        return [item[3] for item in ordered], groups

    @api.model
    def factory_attendance_dashboard_data(self, department_id=False, status="all", selected_date=False, pay_basis=False):
        # Parent enforces attendance-officer access, companies and display exclusions.
        data = super().factory_attendance_dashboard_data(
            department_id=department_id, status="all", selected_date=selected_date, pay_basis=pay_basis
        )
        day = fields.Date.to_date(data["date"])
        now = fields.Datetime.now()
        employees = self.sudo().browse([row["id"] for row in data["employees"]])
        by_employee = defaultdict(list)
        # Broad UTC bounds cover every employee timezone; policy clips per employee.
        start = datetime.combine(day, time.min) - timedelta(days=1)
        end = start + timedelta(days=3)
        attendances = self.env["hr.attendance"].sudo().search([
            ("employee_id", "in", employees.ids), ("check_in", "<", end),
            "|", ("check_out", "=", False), ("check_out", ">", start),
        ], order="check_in, id")
        for attendance in attendances:
            by_employee[attendance.employee_id.id].append((attendance.check_in, attendance.check_out))
        employee_map = {employee.id: employee for employee in employees}
        for row in data["employees"]:
            employee = employee_map[row["id"]]
            zone, _start, _end = self._factory_attendance_day_bounds(day, employee._get_tz())
            summary = summarize_day(by_employee[row["id"]], day, zone, now, employee.factory_lunch_enabled)
            pending = summary["state"] == "pending"
            gap_labels = []
            for gap in summary["gaps"]:
                exit_time = self._factory_attendance_display_time(gap["start"], zone)
                if gap["state"] == "returned":
                    return_time = self._factory_attendance_display_time(gap["end"], zone)
                    gap_labels.append(_("غدا %s ← %s") % (exit_time, return_time))
                elif gap["state"] == "pending":
                    gap_labels.append(_("خروج %s · في انتظار الرجوع قبل 16:00") % exit_time)
                else:
                    gap_labels.append(_("انصراف %s · لم يرجع قبل 16:00") % exit_time)
            worked_minutes = int(summary["worked_seconds"] // 60)
            row.update({
                "lunch_enabled": employee.factory_lunch_enabled,
                "lunch_state": summary["state"],
                "lunch_needs_return": pending,
                "lunch_seconds": summary["lunch_seconds"],
                "lunch_delay_seconds": summary["unpaid_lunch_seconds"],
                "lunch_pending_seconds": summary["pending_seconds"],
                "lunch_label": " · ".join(gap_labels) or "—",
                "lunch_duration_label": self._factory_attendance_minutes_label(
                    (summary["lunch_seconds"] + summary["pending_seconds"]) / 60
                ) if summary["lunch_seconds"] or pending else "—",
                "net_worked_hours": round(summary["worked_seconds"] / 3600, 4),
                "paid_attendance_hours": round(summary["paid_seconds"] / 3600, 4),
                "net_worked_label": _("%s س %s د") % divmod(worked_minutes, 60)
                    if row["status"] == "at_work" else "—",
            })
            if pending or row["is_currently_checked_in"]:
                # Earlier closed intervals aren't the day's final departure.
                # Keep real morning lateness and the raw employee check-in state.
                row["early_departure_minutes"] = 0
                row["delay_label"] = (_("تأخير %s") % self._factory_attendance_minutes_label(
                    row["late_minutes"])) if row.get("show_lateness") else ""
                if pending:
                    row["check_out"] = "—"

        rows = data["employees"]
        data["counts"]["lunch"] = sum(row["lunch_needs_return"] for row in rows)
        allowed = {"all", "at_work", "absent", "leave", "late", "overtime", "lunch"}
        status = status if status in allowed else "all"
        if status in {"at_work", "absent", "leave"}:
            rows = [row for row in rows if row["status"] == status]
        elif status == "late":
            rows = [row for row in rows if row.get("show_lateness")]
        elif status == "overtime":
            rows = [row for row in rows if row["overtime_minutes"] > 0]
        elif status == "lunch":
            rows = [row for row in rows if row["lunch_needs_return"]]
        rows, groups = self._factory_attendance_group_rows(rows, employee_map)
        data.update({"employees": rows, "attendance_groups": groups,
                     "selected_status": status, "lunch_policy": True, "lunch_paid": True})
        return data

    @api.model
    def _factory_lunch_editor_context(self, employee_id, selected_date):
        from odoo.exceptions import AccessError, ValidationError
        if not self.env.user.has_group('hr_attendance.group_hr_attendance_manager'):
            raise AccessError(_('تعديل الغداء متاح لمدير الحضور فقط.'))
        employee = self.sudo().browse(int(employee_id)).exists()
        if not employee or employee.company_id != self.env.company:
            raise ValidationError(_('الموظف غير موجود في الشركة الحالية.'))
        if not employee.factory_lunch_enabled:
            raise ValidationError(_('هذا الموظف ليس له غداء. استخدم تعديل الحضور والانصراف.'))
        day, today = self._factory_attendance_selected_date(selected_date)
        zone, start, end = self._factory_attendance_day_bounds(day, employee._get_tz())
        rows = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id), ('check_in', '>=', start), ('check_in', '<', end)
        ], order='check_in,id')
        return employee, day, today, zone, rows

    @api.model
    def _factory_lunch_editor_records(self, rows, day, zone):
        import hashlib
        version = hashlib.sha256(repr([(r.id, r.write_date, r.check_in, r.check_out) for r in rows]).encode()).hexdigest()
        records = []
        for index, row in enumerate(rows):
            if not row.check_out:
                records.append({'id': row.id, 'return_id': False,
                                'check_in': '', 'check_out': '',
                                'label': _('تسجيل خروج للغداء — سجل حضور مفتوح'),
                                'version': version})
                continue
            if row.out_mode == 'auto_check_out':
                continue
            out = pytz.UTC.localize(row.check_out).astimezone(zone)
            if out.date() != day or not (13 <= out.hour < 16):
                continue
            following = rows[index + 1:index + 2]
            returned = following.check_in if following else False
            def local(value):
                return pytz.UTC.localize(value).astimezone(zone).strftime('%Y-%m-%dT%H:%M:%S') if value else ''
            records.append({'id': row.id, 'return_id': following.id if following else False,
                            'check_in': local(row.check_out), 'check_out': local(returned),
                            'label': '%s — %s' % (out.strftime('%H:%M'), local(returned)[11:16] if returned else _('في انتظار الرجوع')),
                            'version': version})
        return records

    @api.model
    def factory_manual_lunch_details(self, employee_id, selected_date):
        employee, day, today, zone, rows = self._factory_lunch_editor_context(employee_id, selected_date)
        records = self._factory_lunch_editor_records(rows, day, zone)
        return {'employee_name': employee.name, 'selected_date': str(day), 'today': str(today),
                'timezone': zone.zone, 'records': records, 'default_attendance_id': records[-1]['id'] if records else False}

    @api.model
    def factory_save_manual_lunch(self, employee_id, selected_date, attendance_id, departure, returned, expected_version):
        from odoo.exceptions import ValidationError
        employee, day, today, zone, rows = self._factory_lunch_editor_context(employee_id, selected_date)
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s,%s)', (82462, employee.id))
        if rows:
            self.env.cr.execute('UPDATE hr_attendance SET write_date=write_date WHERE id IN %s', [tuple(rows.ids)])
            rows.invalidate_recordset()
        records = self._factory_lunch_editor_records(rows, day, zone)
        record = next((r for r in records if r['id'] == int(attendance_id or 0)), None)
        if not record or record['version'] != expected_version:
            raise ValidationError(_('سجل الاستراحة اتغير أو وصلت بصمة جديدة؛ أغلق النافذة وافتحها مجددًا.'))
        exit_time = self._factory_manual_datetime_to_utc(departure, zone.zone)
        return_time = self._factory_manual_datetime_to_utc(returned, zone.zone)
        if not exit_time:
            raise ValidationError(_('وقت الخروج للغداء مطلوب.'))
        local_exit = pytz.UTC.localize(exit_time).astimezone(zone)
        if local_exit.date() != day or not (13 <= local_exit.hour < 16):
            raise ValidationError(_('الخروج للغداء يكون في اليوم المختار من 13:00 إلى ما قبل 16:00.'))
        if exit_time > fields.Datetime.now() or (return_time and return_time > fields.Datetime.now()):
            raise ValidationError(_('لا يمكن تسجيل وقت غداء في المستقبل.'))
        if return_time and (return_time <= exit_time or pytz.UTC.localize(return_time).astimezone(zone).date() != day):
            raise ValidationError(_('الرجوع يجب أن يكون بعد الخروج وفي نفس اليوم.'))
        previous = rows.filtered(lambda r: r.id == record['id'])
        following = rows.filtered(lambda r: r.id == record['return_id'])
        if not return_time and following:
            raise ValidationError(_('يوجد حضور مسجل بعد الاستراحة؛ عدّل وقت الرجوع بدل مسحه.'))
        if exit_time <= previous.check_in or (following.check_out and return_time and return_time >= following.check_out):
            raise ValidationError(_('مواعيد الغداء يجب أن تكون بين حضور العامل وانصرافه.'))
        audit = {'factory_manual_edited_by_id': self.env.user.id, 'factory_manual_edited_at': fields.Datetime.now()}
        # Keep the original morning check-in and the final departure intact.
        # Raw device events remain available and are marked corrected by attendance.write.
        with self.env.cr.savepoint():
            # Set the return first when shortening a gap; constraints still protect overlap.
            if following and return_time < previous.check_out:
                previous.write(dict(audit, check_out=exit_time, out_mode='manual', biometric_out_device_id=False))
                following.write(dict(audit, check_in=return_time, in_mode='manual', biometric_in_device_id=False))
            else:
                if following:
                    following.write(dict(audit, check_in=return_time, in_mode='manual', biometric_in_device_id=False))
                previous.write(dict(audit, check_out=exit_time, out_mode='manual', biometric_out_device_id=False))
            if return_time and not following:
                self.env['hr.attendance'].sudo().create(dict(audit, employee_id=employee.id, check_in=return_time, in_mode='manual'))
        return {'employee_name': employee.name}
