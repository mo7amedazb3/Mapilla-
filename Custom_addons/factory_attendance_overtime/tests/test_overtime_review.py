from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestOvertimeReview(TransactionCase):
    day = date(2026, 8, 31)
    zone = pytz.timezone("Africa/Cairo")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "OT 09–20", "tz": "Africa/Cairo", "attendance_ids": [(5, 0, 0)] + [
                (0, 0, {"name": "Shift", "dayofweek": str(day), "hour_from": 9,
                        "hour_to": 20, "day_period": "morning"}) for day in range(7)
            ],
        })
        cls.department = cls.env["hr.department"].create({"name": "Overtime isolated tests"})
        cls.employee = cls.env["hr.employee"].create({
            "name": "Overtime worker", "tz": "Africa/Cairo", "resource_calendar_id": cls.calendar.id,
            "department_id": cls.department.id,
        })
        cls.admin = new_test_user(cls.env, login="overtime_review_admin",
                                 groups="base.group_system,hr_attendance.group_hr_attendance_manager")
        cls.officer = new_test_user(cls.env, login="overtime_review_officer",
                                   groups="hr_attendance.group_hr_attendance_officer")
        cls.manager = new_test_user(cls.env, login="overtime_review_manager",
                                   groups="hr_attendance.group_hr_attendance_manager")
        cls.ordinary = new_test_user(cls.env, login="overtime_review_ordinary", groups="base.group_user")
        cls.api = cls.env["hr.employee"].with_user(cls.admin).with_context(tz="Africa/Cairo")

    @classmethod
    def utc(cls, clock, day=None):
        value = datetime.combine(day or cls.day, datetime.strptime(clock, "%H:%M:%S").time())
        return cls.zone.localize(value).astimezone(pytz.UTC).replace(tzinfo=None)

    def attendance(self, start="09:00:00", end=False, day=None, employee=None):
        return self.env["hr.attendance"].create({
            "employee_id": (employee or self.employee).id, "check_in": self.utc(start, day),
            "check_out": self.utc(end, day) if end else False,
            "in_mode": "biometric", "out_mode": "biometric" if end else False,
        })

    def report(self, now="22:00:00", day=None, selected=None):
        with patch.object(fields.Datetime, "now", return_value=self.utc(now, day)):
            return self.api.factory_overtime_review_data(str(selected or self.day))

    def row(self, attendance, now="22:00:00", day=None):
        selected = pytz.UTC.localize(attendance.check_in).astimezone(self.zone).date()
        return next(row for row in self.report(now, day, selected)["rows"] if row["attendance_id"] == attendance.id)

    def close(self, row, mode="scheduled", custom=False, approve=True, now="22:00:00", day=None, user=None):
        model = self.api.with_user(user) if user else self.api
        with patch.object(fields.Datetime, "now", return_value=self.utc(now, day)):
            return model.factory_overtime_review_checkout(
                row["attendance_id"], mode, row["expected_version"], row["expected_check_in"],
                row["expected_shift_end"], checkout_local=custom, approve=approve)

    def test_only_open_overdue_shifts_and_reads_do_not_checkout(self):
        attendance = self.attendance()
        self.assertFalse(any(row["attendance_id"] == attendance.id for row in self.report("19:59:59")["rows"]))
        row = self.row(attendance)
        self.assertEqual(row["scheduled_time"], "20:00")
        self.assertEqual(row["potential_overtime_minutes"], 120)
        self.assertFalse(attendance.check_out)
        self.close(row)
        self.assertFalse(any(row["attendance_id"] == attendance.id for row in self.report()["rows"]))

    def test_default_today_does_not_mix_previous_day(self):
        yesterday = fields.Date.context_today(self.api) - timedelta(days=1)
        attendance = self.attendance(day=yesterday)
        result = self.api.factory_overtime_review_data()
        self.assertEqual(result["date"], result["today"])
        self.assertNotIn(attendance.id, [row["attendance_id"] for row in result["rows"]])
        result = self.api.factory_overtime_review_data(str(yesterday))
        self.assertIn(attendance.id, [row["attendance_id"] for row in result["rows"]])

    def test_selected_day_never_includes_another_day(self):
        attendance = self.attendance(day=self.day - timedelta(days=1))
        self.assertNotIn(attendance.id, [row["attendance_id"] for row in self.report()["rows"]])
        result = self.report(selected=self.day - timedelta(days=1))
        self.assertIn(attendance.id, [row["attendance_id"] for row in result["rows"]])

    def test_future_report_date_rejected(self):
        with self.assertRaises(ValidationError):
            self.api.factory_overtime_review_data(str(fields.Date.context_today(self.api) + timedelta(days=1)))

    def test_preview_and_cancel_are_read_only(self):
        attendance = self.attendance()
        before = attendance.read(["check_in", "check_out", "write_date", "in_mode"])
        result = self.close(self.row(attendance), mode="custom", custom="2026-08-31T20:30", approve=False)
        self.assertFalse(result["approved"])
        self.assertEqual(result["overtime_minutes"], 30)
        self.assertEqual(attendance.read(["check_in", "check_out", "write_date", "in_mode"]), before)

    def test_default_checkout_audited_without_changing_checkin(self):
        attendance = self.attendance()
        result = self.close(self.row(attendance))
        self.assertTrue(result["approved"])
        self.assertEqual(result["overtime_minutes"], 0)
        self.assertEqual(attendance.check_out, self.utc("20:00:00"))
        self.assertEqual(attendance.check_in, self.utc("09:00:00"))
        self.assertEqual(attendance.in_mode, "biometric")
        self.assertEqual(attendance.out_mode, "manual")
        self.assertEqual(attendance.factory_manual_edited_by_id, self.admin)
        self.assertEqual(attendance.factory_manual_edited_at, self.utc("22:00:00"))
        self.assertTrue(attendance.message_ids.filtered(lambda m: "مراجعة الأوفر تايم" in str(m.body)))

    def test_custom_half_hour_preserves_lunch_and_dashboard(self):
        morning = self.attendance(end="14:00:00")
        afternoon = self.attendance(start="15:00:00")
        before = morning.read(["check_in", "check_out", "write_date", "in_mode", "out_mode"])
        result = self.close(self.row(afternoon), mode="custom", custom="2026-08-31T20:30")
        self.assertEqual(result["overtime_minutes"], 30)
        self.assertEqual(morning.read(["check_in", "check_out", "write_date", "in_mode", "out_mode"]), before)
        self.assertEqual(afternoon.check_in, self.utc("15:00:00"))
        data = self.api.factory_attendance_dashboard_data(department_id=self.department.id, selected_date=str(self.day))
        self.assertEqual(data["employees"][0]["check_in"], "09:00")
        self.assertEqual(data["employees"][0]["overtime_minutes"], 30)
        if "net_worked_hours" in data["employees"][0]:
            self.assertEqual(data["employees"][0]["net_worked_hours"], 10.5)

    def test_every_employee_uses_own_shift_end(self):
        self.calendar.attendance_ids.write({"hour_to": 19})
        attendance = self.attendance()
        row = self.row(attendance)
        self.assertEqual(row["scheduled_time"], "19:00")
        self.close(row)
        self.assertEqual(attendance.check_out, self.utc("19:00:00"))

    def test_selected_old_day_uses_that_date_not_today(self):
        attendance = self.attendance()
        row = self.row(attendance, day=self.day + timedelta(days=1))
        self.assertEqual(row["shift_date"], "2026-08-31")
        self.close(row, day=self.day + timedelta(days=1))
        self.assertEqual(attendance.check_out, self.utc("20:00:00"))

    def test_no_unscheduled_2000_guess(self):
        self.calendar.attendance_ids.unlink()
        attendance = self.attendance()
        data = self.report()
        self.assertNotIn(attendance.id, [row["attendance_id"] for row in data["rows"]])
        self.assertGreaterEqual(data["unscheduled_count"], 1)

    def test_checkout_after_now_and_before_checkin_rejected(self):
        attendance = self.attendance()
        row = self.row(attendance)
        for value in ["2026-08-31T22:01", "2026-08-31T09:00", "2026-08-30T20:00", "invalid", False]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.close(row, mode="custom", custom=value)
        self.assertFalse(attendance.check_out)

    def test_real_checkout_wins_over_stale_popup(self):
        attendance = self.attendance()
        row = self.row(attendance)
        attendance.with_context(factory_biometric_sync=True).write({"check_out": self.utc("21:00:00"), "out_mode": "biometric"})
        with self.assertRaises(ValidationError):
            self.close(row)
        self.assertEqual(attendance.check_out, self.utc("21:00:00"))
        self.assertEqual(attendance.out_mode, "biometric")

    def test_double_click_cannot_rewrite_first_decision(self):
        attendance = self.attendance()
        row = self.row(attendance)
        self.close(row, mode="custom", custom="2026-08-31T20:30")
        with self.assertRaises(ValidationError):
            self.close(row)
        self.assertEqual(attendance.check_out, self.utc("20:30:00"))

    def test_shift_change_requires_new_preview(self):
        attendance = self.attendance()
        row = self.row(attendance)
        self.calendar.attendance_ids.write({"hour_to": 19})
        with self.assertRaises(ValidationError):
            self.close(row)
        self.assertFalse(attendance.check_out)

    def test_changed_checkin_requires_new_preview(self):
        attendance = self.attendance()
        row = self.row(attendance)
        attendance.write({"check_in": self.utc("09:30:00")})
        with self.assertRaises(ValidationError):
            self.close(row)
        self.assertFalse(attendance.check_out)

    def test_admin_only_button_and_rpc(self):
        attendance = self.attendance()
        row = self.row(attendance)
        for user in (self.ordinary, self.officer, self.manager):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self.api.with_user(user).factory_overtime_review_data(str(self.day))
                with self.assertRaises(AccessError):
                    self.close(row, user=user)
        self.assertFalse(self.api.with_user(self.officer).factory_attendance_dashboard_data(
            department_id=self.department.id, selected_date=str(self.day))["can_review_overtime"])
        self.assertTrue(self.api.factory_attendance_dashboard_data(
            department_id=self.department.id, selected_date=str(self.day))["can_review_overtime"])

    def test_excluded_archived_and_other_company_not_changed(self):
        attendance = self.attendance()
        row = self.row(attendance)
        self.env["ir.config_parameter"].sudo().set_param("factory_attendance_dashboard.excluded_employee_ids", "[%s]" % self.employee.id)
        with self.assertRaises(AccessError):
            self.close(row)
        self.assertNotIn(attendance.id, [r["attendance_id"] for r in self.report()["rows"]])
        self.env["ir.config_parameter"].sudo().set_param("factory_attendance_dashboard.excluded_employee_ids", "[]")
        self.employee.active = False
        with self.assertRaises(AccessError):
            self.close(row)
        self.employee.active = True
        company = self.env["res.company"].create({"name": "OT other company"})
        employee = self.env["hr.employee"].create({"name": "Other worker", "company_id": company.id, "tz": "Africa/Cairo"})
        foreign = self.attendance(employee=employee)
        foreign_row = dict(row, attendance_id=foreign.id)
        with self.assertRaises(AccessError):
            self.close(foreign_row)
        self.assertFalse(foreign.check_out)

    def test_late_evening_new_session_cannot_close_before_its_checkin(self):
        attendance = self.attendance(start="21:00:00")
        row = self.row(attendance)
        self.assertFalse(row["can_close_scheduled"])
        self.assertEqual(row["potential_overtime_minutes"], 60)
        with self.assertRaises(ValidationError):
            self.close(row)
        result = self.close(row, mode="custom", custom="2026-08-31T21:30")
        self.assertEqual(result["overtime_minutes"], 30)

    def test_raw_biometric_timestamp_preserved_and_duplicate_gate_still_works(self):
        device = self.env["factory.biometric.device"].create({
            "name": "OT isolated device", "serial_number": "TEST-OVERTIME-ONLY", "state": "active",
            "expected_source_ip": "127.0.0.1", "timezone": "Africa/Cairo", "debounce_seconds": 300,
            "accept_events_from": self.utc("00:00:00"),
        })
        self.env["factory.biometric.identity"].create({"device_id": device.id, "employee_id": self.employee.id, "device_user_id": "1001"})
        event_model = self.env["factory.biometric.event"]
        event_model._ingest_attlog(device, "1001\t2026-08-31 09:00:00\t0\t1\t0\t0\t0", stamp="ot-in")
        event = device.event_ids
        timestamp = event.punch_time
        attendance = event.attendance_id
        self.close(self.row(attendance), mode="custom", custom="2026-08-31T20:30")
        self.assertEqual(event.punch_time, timestamp)
        self.assertEqual(event.state, "corrected")
        event_model._ingest_attlog(device, "1001\t2026-08-31 20:34:59\t0\t4\t0\t0\t0", stamp="ot-duplicate")
        self.assertEqual(device.event_ids.sorted("id")[-1].resolved_action, "duplicate")
        self.assertEqual(attendance.check_out, self.utc("20:30:00"))

    def test_draft_payroll_half_hour_and_confirmed_snapshot_unchanged(self):
        if "simple.payroll.slip" not in self.env or not hasattr(self.employee, "_furniture_attendance_details"):
            self.skipTest("Factory payroll / MRP optional")
        self.env["hr.contract"].create({
            "name": "OT contract", "employee_id": self.employee.id, "resource_calendar_id": self.calendar.id,
            "date_start": date(2026, 8, 1), "state": "open", "wage": 3000,
        })
        self.attendance(end="14:00:00")
        attendance = self.attendance(start="15:00:00")
        draft = self.env["simple.payroll.slip"].create({"employee_id": self.employee.id, "date_from": self.day, "date_to": self.day})
        confirmed = self.env["simple.payroll.slip"].create({"employee_id": self.employee.id, "date_from": self.day - timedelta(days=1), "date_to": self.day, "state": "confirmed"})
        paid = self.env["simple.payroll.slip"].create({"employee_id": self.employee.id, "date_from": self.day - timedelta(days=2), "date_to": self.day, "state": "paid"})
        final = confirmed | paid
        frozen_fields = [name for name, field in final._fields.items()
                         if field.store and field.compute and field.type in ("float", "monetary", "integer")]
        frozen_fields += ["write_date", "state"]
        before = final.read(frozen_fields)
        self.close(self.row(attendance), mode="custom", custom="2026-08-31T20:30")
        self.assertEqual(draft.factory_overtime_hours, 0.5)
        self.assertEqual(draft.factory_last_calculated_by_id, self.admin)
        self.env.flush_all()
        final.invalidate_recordset()
        self.assertEqual(final.read(frozen_fields), before)
        details = self.employee._furniture_attendance_details(self.day, self.day)
        # The confirmed 14:00–15:00 lunch interval is paid by the lunch
        # module, so total paid attendance is 11.5h while overtime stays 0.5h.
        self.assertEqual(details["worked_hours"], 11.5)
        self.assertEqual(details["overtime_hours"], 0.5)

    def test_closed_overtime_requires_decision_and_rejection_pays_zero(self):
        attendance = self.attendance(end="21:00:00")
        with patch.object(fields.Datetime, "now", return_value=self.utc("22:00:00")):
            data = self.api.factory_overtime_approval_data(str(self.day))
            row = next(item for item in data["rows"] if item["attendance_id"] == attendance.id)
            self.assertEqual(row["state"], "pending")
            self.assertEqual(row["overtime_minutes"], 60)
            result = self.api.factory_overtime_approval_decide(
                attendance.id, "reject", row["expected_version"]
            )
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(attendance.factory_overtime_approved_minutes, 0)
        details = self.employee._furniture_attendance_details(self.day, self.day)
        self.assertEqual(details["overtime_hours"], 0)

    def test_approval_and_manual_checkout_are_payable(self):
        attendance = self.attendance(end="21:00:00")
        with patch.object(fields.Datetime, "now", return_value=self.utc("22:00:00")):
            row = next(item for item in self.api.factory_overtime_approval_data(str(self.day))["rows"] if item["attendance_id"] == attendance.id)
            self.api.factory_overtime_approval_decide(
                attendance.id, "approve", row["expected_version"]
            )
            self.assertEqual(attendance.factory_overtime_approved_minutes, 60)
            row = next(item for item in self.api.factory_overtime_approval_data(str(self.day))["rows"] if item["attendance_id"] == attendance.id)
            self.api.factory_overtime_approval_decide(
                attendance.id, "manual", row["expected_version"],
                checkout_local="2026-08-31T20:30",
            )
        self.assertEqual(attendance.check_out, self.utc("20:30:00"))
        self.assertEqual(attendance.factory_overtime_approved_minutes, 30)
        self.assertEqual(
            self.employee._furniture_attendance_details(self.day, self.day)["overtime_hours"],
            0.5,
        )

    def test_bulk_approval_applies_to_every_selected_normal_row(self):
        second_employee = self.env["hr.employee"].create({
            "name": "Second overtime worker",
            "tz": "Africa/Cairo",
            "resource_calendar_id": self.calendar.id,
            "department_id": self.department.id,
        })
        attendances = self.attendance(end="21:00:00") | self.attendance(
            end="21:00:00", employee=second_employee
        )
        with patch.object(fields.Datetime, "now", return_value=self.utc("22:00:00")):
            data = self.api.factory_overtime_approval_data(str(self.day))
            rows = [row for row in data["rows"] if row["attendance_id"] in attendances.ids]
            result = self.api.factory_overtime_approval_bulk_decide([
                {
                    "attendance_id": row["attendance_id"],
                    "kind": "normal",
                    "expected_version": row["expected_version"],
                }
                for row in rows
            ], "approve")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["state"], "approved")
        self.assertEqual(set(result["results"][0]), {
            "attendance_id", "employee_name", "state", "checkout",
            "approved_minutes", "approved_label", "kind",
        })
        self.assertTrue(all(attendance.factory_overtime_review_state == "approved" for attendance in attendances))

    def test_bulk_decision_rejects_invalid_or_duplicate_rows(self):
        attendance = self.attendance(end="21:00:00")
        with patch.object(fields.Datetime, "now", return_value=self.utc("22:00:00")):
            row = next(item for item in self.api.factory_overtime_approval_data(str(self.day))["rows"] if item["attendance_id"] == attendance.id)
            item = {
                "attendance_id": attendance.id,
                "kind": "normal",
                "expected_version": row["expected_version"],
            }
            with self.assertRaises(ValidationError):
                self.api.factory_overtime_approval_bulk_decide([], "approve")
            with self.assertRaises(ValidationError):
                self.api.factory_overtime_approval_bulk_decide([item, item], "approve")
        self.assertEqual(attendance.factory_overtime_review_state, "pending")

    def test_midnight_cron_closes_open_attendance(self):
        attendance = self.attendance()
        midnight = self.utc("00:00:00", self.day + timedelta(days=1))
        with patch.object(
            fields.Datetime,
            "now",
            return_value=self.utc("00:05:00", self.day + timedelta(days=1)),
        ):
            self.env["hr.attendance"]._factory_midnight_checkout_cron()
        self.assertEqual(attendance.check_out, midnight)
        self.assertTrue(attendance.factory_overtime_auto_checkout)
        self.assertEqual(attendance.factory_overtime_review_state, "pending")

    def test_after_midnight_punch_updates_prior_departure_without_new_checkin(self):
        attendance = self.attendance()
        with patch.object(
            fields.Datetime,
            "now",
            return_value=self.utc("00:05:00", self.day + timedelta(days=1)),
        ):
            self.env["hr.attendance"]._factory_midnight_checkout_cron()
        device = self.env["factory.biometric.device"].create({
            "name": "OT midnight device", "serial_number": "TEST-OT-MIDNIGHT",
            "state": "active", "expected_source_ip": "127.0.0.1",
            "timezone": "Africa/Cairo", "debounce_seconds": 30,
            "accept_events_from": self.utc("00:00:00"),
        })
        self.env["factory.biometric.identity"].create({
            "device_id": device.id, "employee_id": self.employee.id,
            "device_user_id": "9911",
        })
        self.env["factory.biometric.event"].sudo()._ingest_attlog(
            device,
            "9911\t2026-09-01 00:48:00\t0\t4\t0\t0\t0",
            stamp="midnight-out",
        )
        event = device.event_ids.sorted("id")[-1]
        self.assertEqual(event.attendance_id, attendance)
        self.assertEqual(event.resolved_action, "check_out")
        self.assertEqual(
            attendance.factory_overtime_detected_checkout,
            self.utc("00:48:00", self.day + timedelta(days=1)),
        )
        self.assertEqual(
            self.env["hr.attendance"].search_count([
                ("employee_id", "=", self.employee.id)
            ]),
            1,
        )
