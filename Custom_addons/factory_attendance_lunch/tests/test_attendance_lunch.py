from datetime import date, datetime
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..models.lunch_policy import summarize_day


@tagged("post_install", "-at_install")
class TestAttendanceLunch(TransactionCase):
    day = date(2026, 8, 31)  # Monday, Cairo summer time.
    zone = pytz.timezone("Africa/Cairo")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.department = cls.env["hr.department"].create({"name": "Lunch policy tests"})
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "Lunch actual punches 09–19", "tz": "Africa/Cairo",
            "attendance_ids": [(5, 0, 0), (0, 0, {
                "name": "Monday", "dayofweek": "0", "hour_from": 9,
                "hour_to": 19, "day_period": "morning",
            })],
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "Lunch test employee", "department_id": cls.department.id,
            "resource_calendar_id": cls.calendar.id, "tz": "Africa/Cairo",
        })
        cls.officer = new_test_user(cls.env, login="lunch_officer_test",
                                   groups="hr_attendance.group_hr_attendance_officer")
        cls.ordinary = new_test_user(cls.env, login="lunch_ordinary_test", groups="base.group_user")

    @classmethod
    def utc(cls, clock, day=None):
        value = datetime.combine(day or cls.day, datetime.strptime(clock, "%H:%M:%S").time())
        return cls.zone.localize(value).astimezone(pytz.UTC).replace(tzinfo=None)

    def summary(self, intervals, now="20:00:00"):
        return summarize_day([(self.utc(start), self.utc(end) if end else None)
                              for start, end in intervals], self.day, self.zone, self.utc(now))

    def attendance(self, start, end=False):
        return self.env["hr.attendance"].create({
            "employee_id": self.employee.id, "check_in": self.utc(start),
            "check_out": self.utc(end) if end else False,
            "in_mode": "biometric", "out_mode": "biometric" if end else False,
        })

    def dashboard(self, now="20:00:00", status="all", user=None):
        with patch.object(fields.Datetime, "now", return_value=self.utc(now)):
            return self.env["hr.employee"].with_user(user or self.officer).factory_attendance_dashboard_data(
                department_id=self.department.id, selected_date=str(self.day), status=status)

    def test_return_preserves_morning_arrival_and_pays_lunch(self):
        morning = self.attendance("09:00:00", "14:00:00")
        afternoon = self.attendance("15:00:00", "19:00:00")
        before = (morning | afternoon).read(["check_in", "check_out", "write_date", "in_mode", "out_mode"])
        row = self.dashboard()["employees"][0]
        self.assertEqual(row["check_in"], "09:00")
        self.assertEqual(row["check_out"], "19:00")
        self.assertEqual(row["lunch_state"], "returned")
        self.assertEqual(row["lunch_seconds"], 3600)
        self.assertEqual(row["net_worked_hours"], 9)
        self.assertEqual(row["paid_attendance_hours"], 10)
        self.assertEqual(row["late_minutes"], 0)
        self.assertEqual(row["early_departure_minutes"], 0)
        self.assertEqual((morning | afternoon).read(["check_in", "check_out", "write_date", "in_mode", "out_mode"]), before)

    def test_pending_does_not_create_attendance_or_credit_lunch(self):
        morning = self.attendance("09:00:00", "14:00:00")
        row = self.dashboard("15:59:59")["employees"][0]
        self.assertEqual(row["lunch_state"], "pending")
        self.assertFalse(row["is_currently_checked_in"])
        self.assertEqual(row["net_worked_hours"], 5)
        self.assertEqual(row["paid_attendance_hours"], 5)
        self.assertEqual(row["check_out"], "—")
        self.assertEqual(row["early_departure_minutes"], 0)
        self.assertEqual(morning.check_out, self.utc("14:00:00"))
        self.assertEqual(self.env["hr.attendance"].search_count([("employee_id", "=", self.employee.id)]), 1)

    def test_cutoff_changes_display_without_a_write_or_cron(self):
        morning = self.attendance("09:00:00", "14:00:00")
        before = morning.write_date
        self.assertEqual(self.dashboard("15:59:59")["counts"]["lunch"], 1)
        data = self.dashboard("16:00:00")
        row = data["employees"][0]
        self.assertEqual(data["counts"]["lunch"], 0)
        self.assertFalse(row["lunch_needs_return"])
        self.assertEqual(row["lunch_state"], "departed")
        self.assertEqual(row["check_out"], "14:00")
        self.assertEqual(row["early_departure_minutes"], 300)
        self.assertEqual(morning.write_date, before)

    def test_same_day_return_is_lunch_even_after_cutoff(self):
        for return_time, expected in [("15:59:59", "returned"), ("16:00:00", "returned"), ("17:00:00", "returned")]:
            with self.subTest(return_time=return_time):
                result = self.summary([("09:00:00", "14:00:00"), (return_time, "19:00:00")])
                self.assertEqual(result["state"], expected)

    def test_checkout_window_boundaries(self):
        for exit_time, expected in [("12:59:59", "none"), ("13:00:00", "returned"), ("14:00:00", "returned")]:
            with self.subTest(exit_time=exit_time):
                result = self.summary([("09:00:00", exit_time), ("15:00:00", "19:00:00")])
                self.assertEqual(result["state"], expected)
        self.assertEqual(self.summary([("09:00:00", "16:00:00")])["state"], "none")

    def test_two_hours_away_pays_only_one_lunch_hour(self):
        result = self.summary([("09:00:00", "13:30:00"), ("15:30:00", "19:00:00")])
        self.assertEqual(result["lunch_seconds"], 3600)
        self.assertEqual(result["unpaid_lunch_seconds"], 3600)
        self.assertEqual(result["worked_seconds"], 8 * 3600)
        self.assertEqual(result["paid_seconds"], 9 * 3600)

    def test_short_break_is_actual_duration_not_a_minimum_hour(self):
        result = self.summary([("09:00:00", "14:00:00"), ("14:20:00", "19:00:00")])
        self.assertEqual(result["lunch_seconds"], 1200)
        self.assertEqual(result["worked_seconds"], 10 * 3600 - 1200)
        self.assertEqual(result["paid_seconds"], 10 * 3600)

    def test_no_punch_gap_no_automatic_hour_deducted(self):
        result = self.summary([("09:00:00", "19:00:00")])
        self.assertEqual(result["state"], "none")
        self.assertEqual(result["worked_seconds"], 10 * 3600)

    def test_multiple_gaps_counted_once(self):
        result = self.summary([("09:00:00", "13:00:00"), ("13:30:00", "14:00:00"), ("15:00:00", "19:00:00")])
        self.assertEqual(result["lunch_seconds"], 3600)
        self.assertEqual(result["unpaid_lunch_seconds"], 1800)
        self.assertEqual(result["worked_seconds"], 8.5 * 3600)
        self.assertEqual(result["paid_seconds"], 9.5 * 3600)

    def test_overlapping_legacy_rows_do_not_credit_occupied_time(self):
        result = self.summary([("09:00:00", "14:00:00"), ("10:00:00", "13:00:00"),
                               ("15:00:00", "19:00:00")])
        self.assertEqual(result["lunch_seconds"], 3600)
        self.assertEqual(result["paid_seconds"], 10 * 3600)

    def test_open_return_keeps_original_lateness_and_no_early_departure(self):
        self.attendance("09:25:00", "14:00:00")
        self.attendance("15:00:00")
        row = self.dashboard("16:00:00")["employees"][0]
        self.assertEqual(row["check_in"], "09:25")
        self.assertEqual(row["late_minutes"], 25)
        self.assertEqual(row["early_departure_minutes"], 0)
        self.assertTrue(row["is_currently_checked_in"])
        self.assertAlmostEqual(row["net_worked_hours"], 5 + 35 / 60, places=4)

    def test_historical_non_return_is_departure(self):
        now = self.utc("09:00:00", date(2026, 9, 1))
        result = summarize_day([(self.utc("09:00:00"), self.utc("14:00:00"))], self.day, self.zone, now)
        self.assertEqual(result["state"], "departed")
        self.assertEqual(result["worked_seconds"], 5 * 3600)

    def test_next_day_return_cannot_be_lunch(self):
        result = summarize_day([
            (self.utc("09:00:00"), self.utc("14:00:00")),
            (self.utc("09:00:00", date(2026, 9, 1)), None),
        ], self.day, self.zone, self.utc("10:00:00", date(2026, 9, 1)))
        self.assertEqual(result["state"], "departed")
        self.assertEqual(result["lunch_seconds"], 0)

    def test_filter_counts_and_exclusion_preserved(self):
        self.attendance("09:00:00", "14:00:00")
        data = self.dashboard("15:00:00", "lunch")
        self.assertEqual(data["selected_status"], "lunch")
        self.assertEqual(data["counts"]["all"], 1)
        self.assertEqual(len(data["employees"]), 1)
        self.assertFalse(self.dashboard("16:00:00", "lunch")["employees"])
        self.env["ir.config_parameter"].sudo().set_param(
            "factory_attendance_dashboard.excluded_employee_ids", "[%s]" % self.employee.id)
        data = self.dashboard("15:00:00", "lunch")
        self.assertEqual(data["counts"]["all"], 0)
        self.assertFalse(data["employees"])

    def test_read_access_not_widened(self):
        with self.assertRaises(AccessError):
            self.dashboard(user=self.ordinary)

    def test_winter_timezone_uses_factory_clock(self):
        day = date(2026, 1, 5)
        result = summarize_day([(self.utc("09:00:00", day), self.utc("14:00:00", day))],
                               day, self.zone, self.utc("15:59:59", day))
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["worked_seconds"], 5 * 3600)

    def test_actual_biometric_ingestion_lunch_and_duplicate_card(self):
        device = self.env["factory.biometric.device"].create({
            "name": "Lunch isolated device", "serial_number": "TEST-LUNCH-ONLY",
            "state": "active", "timezone": "Africa/Cairo", "debounce_seconds": 300,
            "expected_source_ip": "127.0.0.1",
            "accept_events_from": self.utc("00:00:00"),
        })
        self.env["factory.biometric.identity"].create({
            "device_id": device.id, "employee_id": self.employee.id, "device_user_id": "1001",
        })
        for clock, mode in [("09:00:00", "1"), ("14:00:00", "4"), ("14:04:59", "15")]:
            self.env["factory.biometric.event"]._ingest_attlog(
                device, "1001\t2026-08-31 %s\t0\t%s\t0\t0\t0" % (clock, mode), stamp=clock)
        self.assertEqual(device.event_ids.sorted("id")[-1].resolved_action, "duplicate")
        self.assertEqual(self.dashboard("14:30:00")["employees"][0]["lunch_state"], "pending")
        for clock, mode in [("15:00:00", "15"), ("19:00:00", "4")]:
            self.env["factory.biometric.event"]._ingest_attlog(
                device, "1001\t2026-08-31 %s\t0\t%s\t0\t0\t0" % (clock, mode), stamp=clock)
        before = device.event_ids.read(["state", "resolved_action", "attendance_id", "write_date"])
        row = self.dashboard()["employees"][0]
        self.assertEqual(row["lunch_state"], "returned")
        self.assertEqual(row["check_in"], "09:00")
        self.assertEqual(row["net_worked_hours"], 9)
        self.assertEqual(len(device.event_ids.attendance_id), 2)
        self.assertEqual(device.event_ids.read(["state", "resolved_action", "attendance_id", "write_date"]), before)

    def test_real_payroll_and_mrp_credit_lunch_once(self):
        if "simple.payroll.slip" not in self.env or not hasattr(self.employee, "_furniture_attendance_details"):
            self.skipTest("Optional factory payroll / MRP not installed")
        self.attendance("09:00:00", "14:00:00")
        self.attendance("15:00:00", "19:00:00")
        self.env["hr.contract"].create({
            "name": "Lunch hours contract", "employee_id": self.employee.id,
            "resource_calendar_id": self.calendar.id, "date_start": date(2026, 8, 1),
            "wage": 3000, "state": "open",
        })
        slip = self.env["simple.payroll.slip"].create({
            "employee_id": self.employee.id, "date_from": self.day, "date_to": self.day,
        })
        metrics = slip._factory_payroll_attendance_metrics()
        self.assertEqual(metrics["attendance_hours"], 10)
        self.assertEqual(metrics["attendance_days"], 1)
        self.assertEqual(slip._get_attendance_data(self.employee, self.day, self.day), (10, 1))
        details = self.employee._furniture_attendance_details(self.day, self.day)
        self.assertEqual(details["lines"][0]["worked_hours"], 10)
        self.assertEqual(details["lines"][0]["paid_lunch_hours"], 1)
        self.assertEqual(slip.attendance_hours, 10)
        self.assertEqual(slip.attendance_deduction, 0)
        self.assertEqual(sum(self.employee.attendance_ids.mapped("worked_hours")), 9)
        self.assertEqual(details["lines"][0]["late_hours"], 0)
        self.assertEqual(details["lines"][0]["overtime_hours"], 0)

    def test_paid_lunch_does_not_erase_lateness_or_add_overtime(self):
        self.attendance("09:25:00", "13:30:00")
        self.attendance("15:30:00", "19:30:00")
        details = self.employee._furniture_attendance_details(self.day, self.day)
        line = details["lines"][0]
        self.assertAlmostEqual(line["worked_hours"], 9 + 5 / 60)
        self.assertEqual(line["paid_lunch_hours"], 1)
        self.assertEqual(line["lunch_delay_hours"], 1)
        # The existing net-overtime policy offsets the 25-minute morning delay
        # with the 30 minutes worked after the shift, but it must never erase
        # the confirmed one-hour excess beyond the paid lunch entitlement.
        self.assertEqual(line["deductible_late_hours"], 1)
        # Post-shift time only becomes payable overtime after explicit approval.
        self.assertEqual(line["overtime_hours"], 0)

    def test_payroll_helper_non_return_late_return_and_outside_window(self):
        for exit_time, return_time, expected in [
            ("14:00:00", None, 0), ("14:00:00", "16:00:00", 1),
            ("12:59:59", "15:00:00", 0), ("13:00:00", "15:59:59", 1),
        ]:
            with self.subTest(exit_time=exit_time, return_time=return_time):
                rows = self.attendance("09:00:00", exit_time)
                if return_time:
                    rows |= self.attendance(return_time, "19:00:00")
                self.assertAlmostEqual(self.employee._factory_paid_lunch_hours(
                    self.utc("00:00:00"), self.utc("23:59:59")), expected)
                rows.unlink()

    def test_three_hour_lunch_gap_pays_one_hour_and_deducts_two(self):
        self.attendance("09:00:00", "14:00:00")
        self.attendance("17:00:00", "19:00:00")

        row = self.dashboard()["employees"][0]
        self.assertEqual(row["lunch_state"], "returned")
        self.assertEqual(row["lunch_seconds"], 3600)
        self.assertEqual(row["lunch_delay_seconds"], 7200)
        self.assertEqual(row["net_worked_hours"], 7)
        self.assertEqual(row["paid_attendance_hours"], 8)

        details = self.employee._furniture_attendance_details(self.day, self.day)
        line = details["lines"][0]
        self.assertEqual(line["paid_lunch_hours"], 1)
        self.assertEqual(line["lunch_delay_hours"], 2)
        self.assertEqual(line["deductible_late_hours"], 2)

        self.env["hr.contract"].create({
            "name": "Late lunch payroll contract",
            "employee_id": self.employee.id,
            "resource_calendar_id": self.calendar.id,
            "date_start": date(2026, 8, 1),
            "wage": 3000,
            "state": "open",
        })
        slip = self.env["simple.payroll.slip"].create({
            "employee_id": self.employee.id,
            "date_from": self.day,
            "date_to": self.day,
        })
        self.assertEqual(slip.delay_hours, 2)
        self.assertGreater(slip.delay_deduction, 0)

    def test_open_return_is_paid_but_does_not_invent_checkout(self):
        self.attendance("09:00:00", "14:00:00")
        afternoon = self.attendance("15:00:00")
        with patch.object(fields.Datetime, "now", return_value=self.utc("18:00:00")):
            details = self.employee._furniture_attendance_details(self.day, self.day)
            self.assertEqual(details["lines"][0]["worked_hours"], 9)
            self.assertEqual(details["lines"][0]["overtime_hours"], 0)
        self.assertFalse(afternoon.check_out)

    def test_paid_credit_is_clipped_to_requested_period(self):
        self.attendance("09:00:00", "14:00:00")
        self.attendance("15:00:00", "19:00:00")
        self.assertEqual(self.employee._factory_paid_lunch_hours(
            self.utc("14:15:00"), self.utc("14:45:00")), 0.5)

    def test_dashboard_template_has_duration_only(self):
        from pathlib import Path
        template = (Path(__file__).parents[1] / "static/src/xml/attendance_lunch.xml").read_text()
        self.assertIn("مدة الغدا", template)
        self.assertIn("مدفوع الأجر", template)
        self.assertNotIn("net_worked_label", template)
        self.assertNotIn("صافي العمل", template)
        self.assertNotIn("غير محسوب", template)

    def test_disabled_lunch_is_normal_checkout_and_no_paid_gap(self):
        self.employee.factory_lunch_enabled = False
        morning = self.attendance("09:00:00", "15:00:00")
        data = self.dashboard(now="15:10:00")
        row = data["employees"][0]
        self.assertEqual(row["lunch_state"], "none")
        self.assertEqual(row["check_out"], "15:00")
        self.assertEqual(row["status"], "checked_out")
        self.assertEqual(data["counts"]["lunch"], 0)
        self.attendance("15:30:00", "19:00:00")
        row = self.dashboard()["employees"][0]
        self.assertEqual(row["lunch_seconds"], 0)
        self.assertEqual(row["paid_attendance_hours"], row["net_worked_hours"])
        self.assertEqual(self.employee._factory_paid_lunch_hours(
            self.utc("00:00:00"), self.utc("23:59:59"), self.zone), 0)
        self.assertEqual(self.employee._factory_paid_lunch_seconds([
            (self.utc("09:00:00"), self.utc("15:00:00")),
            (self.utc("15:30:00"), self.utc("19:00:00")),
        ], self.day, self.zone, self.utc("20:00:00")), 0)
        self.assertEqual(morning.check_out, self.utc("15:00:00"))
