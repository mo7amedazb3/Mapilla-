from contextlib import contextmanager
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.model
    def _factory_can_review_overtime(self):
        return self.env.user.has_group("base.group_system") and self.env.user.has_group(
            "hr_attendance.group_hr_attendance_manager"
        )

    @api.model
    def _factory_overtime_check_access(self):
        self._factory_attendance_check_access()
        if not self._factory_can_review_overtime():
            raise AccessError(_("مراجعة الأوفر تايم وتسجيل الانصراف من هنا متاحة للأدمن فقط."))

    @contextmanager
    def _factory_overtime_preserve_final_payroll(self, employee):
        """Protect final payroll only during this administrative correction.

        MRP's stored payroll computes depend on *all* employee attendances,
        including for already-confirmed periods. A draft-only explicit refresh
        is therefore insufficient. Use the ORM's compute protection, without
        overwriting financial snapshots or changing ordinary payroll actions.
        """
        if "simple.payroll.slip" not in self.env:
            yield
            return
        payroll = self.env["simple.payroll.slip"].sudo()
        final = payroll.search([
            ("employee_id", "=", employee.id),
            ("state", "in", ["confirmed", "paid"]),
        ], order="id")
        if not final:
            yield
            return
        final.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM simple_payroll_slip WHERE id IN %s ORDER BY id FOR UPDATE",
            (tuple(final.ids),),
        )
        computed = [field for field in payroll._fields.values() if field.store and field.compute]
        with self.env.protecting(computed, final):
            yield
            # Drain deferred attendance dependencies while protection is active.
            self.env.flush_all()

    @api.model
    def factory_attendance_dashboard_data(self, department_id=False, status="all", selected_date=False, pay_basis=False):
        data = super().factory_attendance_dashboard_data(department_id, status, selected_date, pay_basis=pay_basis)
        data["can_review_overtime"] = self._factory_can_review_overtime()
        # A punch received shortly after midnight belongs to the attendance
        # which the cron closed at 00:00. Show that real punch on the daily
        # dashboard as well as in the approval screen.
        if data.get("employees") and data.get("date"):
            selected = fields.Date.to_date(data["date"])
            employee_ids = [row["id"] for row in data["employees"]]
            candidates = self.env["hr.attendance"].sudo().search([
                ("employee_id", "in", employee_ids),
                ("factory_overtime_detected_checkout", "!=", False),
            ])
            by_employee = {}
            for attendance in candidates:
                shift = attendance.employee_id._factory_overtime_shift(attendance)
                if shift and shift["day"] == selected:
                    current = by_employee.get(attendance.employee_id.id)
                    if not current or attendance.factory_overtime_detected_checkout > current.factory_overtime_detected_checkout:
                        by_employee[attendance.employee_id.id] = attendance
            for row in data["employees"]:
                attendance = by_employee.get(row["id"])
                if attendance:
                    zone = attendance.employee_id._factory_overtime_shift(attendance)["zone"]
                    row["check_out"] = self._factory_attendance_display_time(
                        attendance.factory_overtime_detected_checkout, zone
                    )
        return data

    def _factory_overtime_shift(self, attendance):
        """Use the dated contract/calendar, matching factory payroll's day boundary.

        The shift is the day of this open check-in, NOT today's date (a worker
        may have forgotten yesterday's checkout). No invented 20:00 fallback.
        """
        self.ensure_one()
        zone = pytz.timezone(self._get_tz() or "Africa/Cairo")
        day = pytz.UTC.localize(attendance.check_in).astimezone(zone).date()
        contract = self._furniture_contract_for_date(day) if hasattr(self, "_furniture_contract_for_date") else False
        calendar = (contract and contract.resource_calendar_id) or self.resource_calendar_id or self.company_id.resource_calendar_id
        if not calendar:
            return False
        zone = pytz.timezone(calendar.tz or self._get_tz() or "Africa/Cairo")
        day = pytz.UTC.localize(attendance.check_in).astimezone(zone).date()
        start = zone.localize(datetime.combine(day, time.min))
        end = zone.localize(datetime.combine(day + timedelta(days=1), time.min))
        intervals = calendar.sudo()._work_intervals_batch(
            start, end, resources=self.resource_id.sudo(), tz=zone, compute_leaves=True,
        ).get(self.resource_id.id, ())
        if not intervals:
            return False
        scheduled_end = max(interval[1] for interval in intervals).astimezone(pytz.UTC).replace(tzinfo=None)
        return {"start": min(interval[0] for interval in intervals).astimezone(pytz.UTC).replace(tzinfo=None), "end": scheduled_end, "day": day, "zone": zone}

    @api.model
    def _factory_overtime_row(self, attendance, shift, now):
        zone = shift["zone"]
        def local(value, pattern):
            return pytz.UTC.localize(value).astimezone(zone).strftime(pattern)
        seconds = max(0, (now - max(shift["end"], attendance.check_in)).total_seconds())
        return {
            "attendance_id": attendance.id,
            "employee_id": attendance.employee_id.id,
            "employee_name": attendance.employee_id.name,
            "department": attendance.employee_id.department_id.name or "—",
            "shift_date": str(shift["day"]),
            "timezone": zone.zone,
            "check_in": local(attendance.check_in, "%d/%m/%Y %H:%M"),
            "scheduled_checkout": local(shift["end"], "%d/%m/%Y %H:%M"),
            "scheduled_time": local(shift["end"], "%H:%M"),
            "custom_checkout": local(shift["end"], "%Y-%m-%dT%H:%M"),
            "max_checkout": local(now, "%Y-%m-%dT%H:%M"),
            "can_close_scheduled": shift["end"] > attendance.check_in,
            "potential_overtime_minutes": int(seconds // 60),
            "potential_overtime_label": self._factory_attendance_minutes_label(seconds / 60),
            "expected_version": attendance.write_date.isoformat(),
            "expected_check_in": fields.Datetime.to_string(attendance.check_in),
            "expected_shift_end": fields.Datetime.to_string(shift["end"]),
        }

    @api.model
    def factory_overtime_review_data(self, selected_date=False):
        """Today's overdue open rows by default; an explicit date selects another shift day."""
        self._factory_overtime_check_access()
        selected_date, today = self._factory_attendance_selected_date(selected_date)
        now = fields.Datetime.now()
        attendances = self.env["hr.attendance"].sudo().search([
            ("check_out", "=", False), ("check_in", "<=", now),
            ("employee_id.active", "=", True),
            ("employee_id.company_id", "=", self.env.company.id),
            ("employee_id", "not in", self._factory_attendance_excluded_employee_ids()),
        ], order="check_in, employee_id, id")
        rows, unscheduled = [], 0
        for attendance in attendances:
            shift = attendance.employee_id._factory_overtime_shift(attendance)
            if not shift:
                zone = pytz.timezone(attendance.employee_id._get_tz() or "Africa/Cairo")
                if pytz.UTC.localize(attendance.check_in).astimezone(zone).date() == selected_date:
                    unscheduled += 1
            elif shift["day"] == selected_date and shift["end"] <= now:
                rows.append(self._factory_overtime_row(attendance, shift, now))
        return {"rows": rows, "unscheduled_count": unscheduled,
                "date": str(selected_date), "today": str(today),
                "server_time": fields.Datetime.context_timestamp(self, now).strftime("%d/%m/%Y %H:%M")}

    @api.model
    def factory_overtime_review_checkout(self, attendance_id, mode, expected_version,
                                         expected_check_in, expected_shift_end,
                                         checkout_local=False, approve=False):
        """Validate/preview first; only explicit admin approval writes a checkout.

        Re-read under the SAME employee lock as biometric ingestion, then a row
        lock. Refuse stale/double submissions instead of replacing a real punch.
        """
        self._factory_overtime_check_access()
        if type(attendance_id) is not int or attendance_id <= 0 or mode not in {"scheduled", "custom"}:
            raise ValidationError(_("طلب مراجعة الانصراف غير صحيح."))
        if type(approve) is not bool:
            raise ValidationError(_("قيمة تأكيد الانصراف غير صحيحة."))
        attendance = self.env["hr.attendance"].sudo().browse(attendance_id).exists()
        if not attendance:
            raise ValidationError(_("سجل الحضور غير موجود؛ حدّث التقرير."))
        employee = attendance.employee_id
        if (not employee.active or employee.company_id != self.env.company
                or employee.id in self._factory_attendance_excluded_employee_ids()):
            raise AccessError(_("هذا العامل غير متاح للمراجعة في الشركة الحالية."))
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (82462, employee.id))
        self.env.cr.execute("SELECT id FROM hr_attendance WHERE id = %s FOR UPDATE", (attendance.id,))
        attendance.invalidate_recordset()
        if attendance.employee_id != employee:
            raise ValidationError(_("تغيّر العامل المرتبط بالحضور؛ حدّث التقرير."))
        if attendance.check_out:
            raise ValidationError(_("العامل سجّل انصراف بالفعل. لم يتم تغيير بصمته؛ حدّث التقرير."))
        if (not expected_version or not expected_check_in
                or attendance.write_date.isoformat() != expected_version
                or fields.Datetime.to_string(attendance.check_in) != expected_check_in):
            raise ValidationError(_("سجل الحضور اتغيّر بعد فتح التقرير؛ حدّثه وراجع الوقت مرة أخرى."))
        now = fields.Datetime.now()
        shift = employee._factory_overtime_shift(attendance)
        if not shift or shift["end"] > now:
            raise ValidationError(_("ميعاد انتهاء الشيفت لم يمر بعد، أو لا يوجد موعد انصراف محدد."))
        if fields.Datetime.to_string(shift["end"]) != expected_shift_end:
            raise ValidationError(_("موعد الشيفت اتغيّر؛ حدّث التقرير قبل تسجيل الانصراف."))
        checkout = shift["end"] if mode == "scheduled" else self._factory_manual_datetime_to_utc(
            checkout_local, shift["zone"].zone)
        if not checkout or checkout <= attendance.check_in:
            raise ValidationError(_("وقت الانصراف لازم يكون بعد وقت الحضور لهذه الفترة."))
        if checkout > now:
            raise ValidationError(_("لا يمكن تسجيل انصراف في المستقبل."))
        local = pytz.UTC.localize(checkout).astimezone(shift["zone"])
        overtime_seconds = employee._factory_net_overtime(attendance, shift, now, checkout=checkout)["attendance_minutes"] * 60
        result = {
            "attendance_id": attendance.id, "employee_name": employee.name,
            "checkout": local.strftime("%d/%m/%Y %H:%M"), "timezone": shift["zone"].zone,
            "overtime_minutes": overtime_seconds / 60,
            "overtime_label": self._factory_attendance_minutes_label(overtime_seconds / 60),
            "approved": False,
        }
        if not approve:
            return result
        # Preserve the morning biometric check-in and every earlier lunch interval.
        with self._factory_overtime_preserve_final_payroll(employee):
            attendance.write({
                "check_out": checkout, "out_mode": "manual",
                "factory_overtime_review_state": "approved",
                "factory_overtime_approved_minutes": overtime_seconds / 60,
                "factory_overtime_reviewed_by_id": self.env.user.id,
                "factory_overtime_reviewed_at": now,
                "factory_manual_edited_by_id": self.env.user.id,
                "factory_manual_edited_at": now,
            })
        attendance.message_post(
            body=_("مراجعة الأوفر تايم بواسطة %(admin)s: انصراف يدوي %(checkout)s (%(zone)s). "
                   "الاختيار: %(mode)s. إضافي هذه الفترة: %(overtime)s.") % {
                "admin": self.env.user.name, "checkout": result["checkout"],
                "zone": result["timezone"], "mode": _("ميعاد الشيفت") if mode == "scheduled" else _("وقت يحدده الأدمن"),
                "overtime": result["overtime_label"],
            },
            subtype_xmlid="mail.mt_note",
        )
        # Existing draft payroll previews should reflect the decision; never
        # reopen/recalculate confirmed or paid payroll as a side effect.
        if "simple.payroll.slip" in self.env:
            payroll = self.env["simple.payroll.slip"].sudo()
            if hasattr(payroll, "action_recompute_factory_payroll"):
                slips = payroll.search([
                    ("employee_id", "=", employee.id), ("company_id", "=", self.env.company.id),
                    ("state", "=", "draft"), ("date_from", "<=", local.date()),
                    ("date_to", ">=", shift["day"]),
                ])
                if slips:
                    slips.action_recompute_factory_payroll()
        result["approved"] = True
        return result

    @api.model
    def _factory_overtime_candidate_checkout(self, attendance, now):
        return (
            attendance.factory_overtime_detected_checkout
            or attendance.check_out
            or now
        )

    def _factory_net_overtime(self, attendance, shift, now, checkout=False, include_open=True):
        """Offset morning lateness once across the day's actual work intervals."""
        self.ensure_one()
        zone, start, end = self._factory_attendance_day_bounds(shift["day"], shift["zone"].zone)
        rows = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', self.id), ('check_in', '>=', start), ('check_in', '<', end)
        ], order='check_in,id')
        late = max(0.0, (rows[0].check_in - shift['start']).total_seconds()) if rows else 0.0
        remaining = late
        allocated = {}
        raw_total = 0.0
        for row in rows:
            candidate = checkout if row == attendance and checkout else self._factory_overtime_candidate_checkout(row, now)
            if not include_open and not row.check_out and not row.factory_overtime_detected_checkout and not (row == attendance and checkout):
                candidate = row.check_in
            # An unclosed historical interval cannot accrue multiple days.
            candidate = min(candidate, end) if not row.check_out and not row.factory_overtime_detected_checkout and not (row == attendance and checkout) else candidate
            raw = max(0.0, (candidate - max(shift['end'], row.check_in)).total_seconds())
            raw_total += raw
            offset = min(remaining, raw)
            remaining -= offset
            allocated[row.id] = raw - offset
        net = max(0.0, raw_total - late)
        eligible = net > 20 * 60
        return {'late_minutes': late / 60, 'remaining_late_minutes': remaining / 60,
                'raw_minutes': raw_total / 60, 'net_minutes': net / 60,
                'eligible_minutes': net / 60 if eligible else 0.0,
                'attendance_minutes': allocated.get(attendance.id, 0.0) / 60 if eligible else 0.0}

    @api.model
    def _factory_overtime_approval_row(self, attendance, shift, now):
        candidate = self._factory_overtime_candidate_checkout(attendance, now)
        zone = shift["zone"]

        def local(value, pattern="%H:%M"):
            if not value:
                return "—"
            return pytz.UTC.localize(value).astimezone(zone).strftime(pattern)

        net = attendance.employee_id._factory_net_overtime(attendance, shift, now)
        overtime_minutes = net['attendance_minutes']
        state = attendance.factory_overtime_review_state or "pending"
        is_open = not attendance.check_out
        source = "auto" if attendance.factory_overtime_auto_checkout else (
            "biometric" if attendance.out_mode == "biometric" else "manual"
        )
        if attendance.factory_overtime_detected_checkout:
            source = "late_biometric"
        return {
            "attendance_id": attendance.id,
            "employee_id": attendance.employee_id.id,
            "employee_name": attendance.employee_id.name,
            "department": attendance.employee_id.department_id.display_name or "—",
            "avatar": "/web/image/hr.employee/%s/avatar_128" % attendance.employee_id.id,
            "shift_date": str(shift["day"]),
            "timezone": zone.zone,
            "check_in": local(attendance.check_in),
            "scheduled_checkout": local(shift["end"]),
            "detected_checkout": local(candidate),
            "detected_checkout_full": local(candidate, "%d/%m/%Y %H:%M"),
            "custom_checkout": local(candidate, "%Y-%m-%dT%H:%M"),
            "max_checkout": local(now, "%Y-%m-%dT%H:%M"),
            "overtime_minutes": overtime_minutes,
            "overtime_label": self._factory_attendance_minutes_label(overtime_minutes),
            "approved_minutes": min(attendance.factory_overtime_approved_minutes or 0, overtime_minutes),
            "approved_label": self._factory_attendance_minutes_label(
                min(attendance.factory_overtime_approved_minutes or 0, overtime_minutes)
            ),
            "state": state,
            "is_open": is_open,
            "source": source,
            "source_label": {
                "auto": _("إغلاق آلي 00:00"),
                "late_biometric": _("بصمة بعد منتصف الليل"),
                "biometric": _("بصمة"),
                "manual": _("يدوي"),
            }[source],
            "reviewer": attendance.factory_overtime_reviewed_by_id.name or "",
            "reviewed_at": local(attendance.factory_overtime_reviewed_at, "%d/%m/%Y %H:%M"),
            "note": attendance.factory_overtime_review_note or "",
            "expected_version": attendance.write_date.isoformat(),
        }

    @api.model
    def factory_overtime_approval_data(self, selected_date=False):
        self._factory_overtime_check_access()
        selected_date, today = self._factory_attendance_selected_date(selected_date)
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        company = self.env.company
        company_tz = self.env.user.tz or company.resource_calendar_id.tz or "Africa/Cairo"
        _, day_start, day_end = self._factory_attendance_day_bounds(
            selected_date, company_tz
        )
        attendances = self.env["hr.attendance"].sudo().search([
            ("employee_id.active", "=", True),
            ("employee_id.company_id", "=", company.id),
            ("employee_id", "not in", self._factory_attendance_excluded_employee_ids()),
            ("check_in", "<=", now),
            ("check_in", ">=", day_start - timedelta(hours=14)),
            ("check_in", "<", day_end + timedelta(hours=14)),
        ], order="employee_id, check_in, id")
        rows = []
        for attendance in attendances:
            shift = attendance.employee_id._factory_overtime_shift(attendance)
            if not shift or shift["day"] != selected_date or shift["end"] > now:
                continue
            row = self._factory_overtime_approval_row(attendance, shift, now)
            if row["overtime_minutes"] > 0:
                rows.append(row)
        counts = {
            "all": len(rows),
            "pending": len([row for row in rows if row["state"] == "pending"]),
            "approved": len([row for row in rows if row["state"] == "approved"]),
            "rejected": len([row for row in rows if row["state"] == "rejected"]),
            "open": len([row for row in rows if row["is_open"]]),
        }
        return {
            "rows": rows,
            "counts": counts,
            "date": str(selected_date),
            "date_label": selected_date.strftime("%d/%m/%Y"),
            "today": str(today),
            "server_time": fields.Datetime.context_timestamp(self, now).strftime("%H:%M"),
        }

    @api.model
    def _factory_overtime_refresh_draft_payroll(self, employee, shift_day):
        if "simple.payroll.slip" not in self.env:
            return
        payroll = self.env["simple.payroll.slip"].sudo()
        if not hasattr(payroll, "action_recompute_factory_payroll"):
            return
        slips = payroll.search([
            ("employee_id", "=", employee.id),
            ("company_id", "=", self.env.company.id),
            ("state", "=", "draft"),
            ("date_from", "<=", shift_day),
            ("date_to", ">=", shift_day),
        ])
        if slips:
            slips.action_recompute_factory_payroll()

    @api.model
    def factory_overtime_approval_decide(
        self, attendance_id, decision, expected_version, checkout_local=False
    ):
        self._factory_overtime_check_access()
        if type(attendance_id) is not int or attendance_id <= 0:
            raise ValidationError(_("سجل الحضور غير صحيح."))
        if decision not in {"approve", "reject", "manual"}:
            raise ValidationError(_("قرار اعتماد الإضافي غير صحيح."))
        attendance = self.env["hr.attendance"].sudo().browse(attendance_id).exists()
        if not attendance:
            raise ValidationError(_("سجل الحضور غير موجود؛ حدّث القائمة."))
        employee = attendance.employee_id
        if (
            not employee.active
            or employee.company_id != self.env.company
            or employee.id in self._factory_attendance_excluded_employee_ids()
        ):
            raise AccessError(_("هذا العامل غير متاح للمراجعة في الشركة الحالية."))
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (82462, employee.id))
        self.env.cr.execute("SELECT id FROM hr_attendance WHERE id = %s FOR UPDATE", (attendance.id,))
        attendance.invalidate_recordset()
        if not expected_version or attendance.write_date.isoformat() != expected_version:
            raise ValidationError(_("بيانات الانصراف اتغيّرت؛ حدّث القائمة قبل اتخاذ القرار."))
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        shift = employee._factory_overtime_shift(attendance)
        if not shift or shift["end"] > now:
            raise ValidationError(_("لا يوجد شيفت منتهٍ صالح للمراجعة."))
        if not attendance.check_out and not attendance.factory_overtime_detected_checkout:
            raise ValidationError(_("العامل مازال حاضرًا؛ انتظر بصمة الانصراف أو الإغلاق عند منتصف الليل."))

        checkout = self._factory_overtime_candidate_checkout(attendance, now)
        if decision == "manual":
            checkout = self._factory_manual_datetime_to_utc(
                checkout_local, shift["zone"].zone
            )
            if not checkout:
                raise ValidationError(_("حدد وقت الانصراف اليدوي."))
        if checkout <= attendance.check_in:
            raise ValidationError(_("وقت الانصراف لازم يكون بعد وقت الحضور."))
        if checkout > now:
            raise ValidationError(_("لا يمكن تسجيل انصراف في المستقبل."))
        next_attendance = self.env["hr.attendance"].sudo().search([
            ("employee_id", "=", employee.id),
            ("id", "!=", attendance.id),
            ("check_in", ">", attendance.check_in),
        ], order="check_in, id", limit=1)
        if next_attendance and checkout > next_attendance.check_in:
            raise ValidationError(_("وقت الانصراف يتعارض مع حضور أحدث للعامل."))

        overtime_minutes = employee._factory_net_overtime(
            attendance, shift, now, checkout=checkout)['attendance_minutes']
        if decision == "reject":
            state = "rejected"
            approved_minutes = 0.0
            note = _("رفض الأدمن احتساب الوقت الإضافي.")
        else:
            state = "approved"
            approved_minutes = overtime_minutes
            note = _("اعتمد الأدمن وقت الانصراف الظاهر.") if decision == "approve" else _(
                "سجل الأدمن وقت انصراف يدوي واعتمد الإضافي الناتج عنه."
            )

        values = {
            "factory_overtime_review_state": state,
            "factory_overtime_approved_minutes": approved_minutes,
            "factory_overtime_reviewed_by_id": self.env.user.id,
            "factory_overtime_reviewed_at": now,
            "factory_overtime_review_note": note,
        }
        if decision != "reject":
            values.update({
                "check_out": checkout,
                "out_mode": "biometric" if decision == "approve" and attendance.factory_overtime_detected_checkout else "manual" if decision == "manual" else attendance.out_mode,
            })
            if decision == "manual":
                values.update({
                    "factory_manual_edited_by_id": self.env.user.id,
                    "factory_manual_edited_at": now,
                })
        with self._factory_overtime_preserve_final_payroll(employee):
            attendance.with_context(factory_biometric_sync=True).write(values)
        local_checkout = pytz.UTC.localize(checkout).astimezone(shift["zone"])
        attendance.message_post(
            body=_(
                "اعتماد الإضافي بواسطة %(admin)s: %(decision)s — الانصراف %(checkout)s — الإضافي المعتمد %(overtime)s."
            ) % {
                "admin": self.env.user.name,
                "decision": {"approve": _("اعتماد"), "reject": _("رفض"), "manual": _("وقت يدوي")}[decision],
                "checkout": local_checkout.strftime("%d/%m/%Y %H:%M"),
                "overtime": self._factory_attendance_minutes_label(approved_minutes),
            },
            subtype_xmlid="mail.mt_note",
        )
        self._factory_overtime_refresh_draft_payroll(employee, shift["day"])
        return {
            "attendance_id": attendance.id,
            "employee_name": employee.name,
            "state": state,
            "checkout": local_checkout.strftime("%d/%m/%Y %H:%M"),
            "approved_minutes": approved_minutes,
            "approved_label": self._factory_attendance_minutes_label(approved_minutes),
        }

    @api.model
    def factory_overtime_approval_bulk_decide(self, items, decision):
        """Apply one approval decision to a validated list of visible rows.

        Keep the existing single-row methods as the source of truth so bulk
        actions retain their access, freshness, eligibility, audit and payroll
        safeguards.  One RPC/transaction also means a stale row rolls the whole
        selection back instead of leaving the administrator with a half-applied
        batch.
        """
        self._factory_overtime_check_access()
        if decision not in {"approve", "reject"}:
            raise ValidationError(_("قرار الاعتماد الجماعي غير صحيح."))
        if not isinstance(items, list) or not items:
            raise ValidationError(_("حدد عاملًا واحدًا على الأقل."))
        if len(items) > 200:
            raise ValidationError(_("لا يمكن اعتماد أكثر من 200 عامل في المرة الواحدة."))

        normalized = []
        attendance_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError(_("بيانات أحد العمال غير صحيحة؛ حدّث القائمة."))
            attendance_id = item.get("attendance_id")
            kind = item.get("kind") or "normal"
            expected_version = item.get("expected_version")
            if (
                type(attendance_id) is not int
                or attendance_id <= 0
                or kind not in {"normal", "lunch"}
                or not isinstance(expected_version, str)
                or not expected_version
            ):
                raise ValidationError(_("بيانات أحد العمال غير صحيحة؛ حدّث القائمة."))
            if attendance_id in attendance_ids:
                raise ValidationError(_("لا يمكن تكرار نفس العامل في الاعتماد الجماعي."))
            attendance_ids.add(attendance_id)
            normalized.append((attendance_id, kind, expected_version))

        results = []
        for attendance_id, kind, expected_version in normalized:
            if kind == "lunch":
                result = self.factory_lunch_overtime_decide(
                    attendance_id, decision, expected_version
                )
            else:
                result = self.factory_overtime_approval_decide(
                    attendance_id, decision, expected_version
                )
            results.append(dict(result, attendance_id=attendance_id, kind=kind))
        return {
            "count": len(results),
            "state": "approved" if decision == "approve" else "rejected",
            "results": results,
        }

    def _furniture_attendance_details(self, date_from, date_to):
        """Make only explicitly approved overtime payable in factory payroll."""
        details = super()._furniture_attendance_details(date_from, date_to)
        self.ensure_one()
        if not details.get("lines") or self.env.context.get("factory_skip_quarter_absence"):
            return details
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        start_utc, stop_utc = self._furniture_period_utc(date_from, date_to)
        approved = self.env["hr.attendance"].sudo().search([
            ("employee_id", "=", self.id),
            ("check_in", ">=", start_utc),
            ("check_in", "<", stop_utc),
            ("factory_overtime_review_state", "=", "approved"),
            ("factory_overtime_approved_minutes", ">", 0),
        ])
        zone = self._furniture_timezone(False)
        by_day = {}
        for attendance in approved:
            day = pytz.UTC.localize(attendance.check_in).astimezone(zone).date()
            shift = self._factory_overtime_shift(attendance)
            eligible = self._factory_net_overtime(attendance, shift, fields.Datetime.now())['attendance_minutes'] if shift else 0.0
            by_day[day] = by_day.get(day, 0.0) + min(attendance.factory_overtime_approved_minutes, eligible) / 60.0
        from odoo.addons.furniture_mrp.models.hr_employee_compensation import FURNITURE_LATE_GRACE_MINUTES
        total = 0.0
        for line in details["lines"]:
            _zone, day_start, day_end = self._factory_attendance_day_bounds(line['date'], zone.zone)
            last = self.env['hr.attendance'].sudo().search([
                ('employee_id', '=', self.id), ('check_in', '>=', day_start), ('check_in', '<', day_end)
            ], order='check_in desc,id desc', limit=1)
            shift = self._factory_overtime_shift(last) if last else False
            if shift:
                net = self._factory_net_overtime(last, shift, fields.Datetime.now(), include_open=False)
                lunch_delay_hours = line.get('lunch_delay_hours', 0.0)
                line['deductible_late_hours'] = (
                    max(0.0, line['late_hours'] - net['raw_minutes'] / 60)
                    if line['late_hours'] * 60 > FURNITURE_LATE_GRACE_MINUTES else 0.0
                ) + lunch_delay_hours
            line["overtime_hours"] = by_day.get(line["date"], 0.0)
            total += line["overtime_hours"]
        details["overtime_hours"] = total
        details['deductible_late_hours'] = sum(line['deductible_late_hours'] for line in details['lines'])
        details['late_days'] = sum(line['deductible_late_hours'] > 0 for line in details['lines'])
        return details
