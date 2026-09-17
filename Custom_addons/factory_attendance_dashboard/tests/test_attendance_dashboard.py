# -*- coding: utf-8 -*-

from datetime import datetime
import json
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFactoryAttendanceDashboard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.department = cls.env["hr.department"].create(
            {"name": "Dashboard Test", "company_id": cls.env.company.id}
        )
        cls.calendar = cls.env["resource.calendar"].create(
            {
                "name": "Dashboard 08-17",
                "tz": "Africa/Cairo",
                "company_id": cls.env.company.id,
                "attendance_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Monday",
                            "dayofweek": "0",
                            "hour_from": 8.0,
                            "hour_to": 17.0,
                            "day_period": "morning",
                        },
                    )
                ],
            }
        )
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Dashboard Worker",
                "company_id": cls.env.company.id,
                "department_id": cls.department.id,
                "resource_calendar_id": cls.calendar.id,
                "tz": "Africa/Cairo",
                "barcode": "DASH1",
            }
        )

    @classmethod
    def _utc(cls, value):
        local = pytz.timezone("Africa/Cairo").localize(
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        )
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    def test_dashboard_reads_attendance_and_computes_metrics(self):
        attendance = self.env["hr.attendance"].create(
            {
                "employee_id": self.employee.id,
                "check_in": self._utc("2026-08-31 08:30:00"),
                "check_out": self._utc("2026-08-31 18:00:00"),
                "in_mode": "biometric",
                "out_mode": "biometric",
            }
        )
        data = self.env["hr.employee"].factory_attendance_dashboard_data(
            self.department.id, "all", "2026-08-31"
        )
        self.assertEqual(data["counts"]["all"], 1)
        self.assertEqual(data["counts"]["checked_out"], 1)
        self.assertEqual(data["counts"]["late"], 1)
        self.assertEqual(data["counts"]["overtime"], 1)
        row = data["employees"][0]
        self.assertEqual(row["status"], "checked_out")
        self.assertEqual(row["check_in"], "08:30")
        self.assertEqual(row["check_out"], "18:00")
        self.assertEqual(row["late_minutes"], 30)
        self.assertEqual(row["overtime_minutes"], 60)
        self.assertEqual(row["source_code"], "biometric")
        self.assertEqual(row["employee_code"], "DASH1")
        attendance.unlink()

    def test_manual_edit_records_audit_user(self):
        attendance = self.env["hr.attendance"].create(
            {
                "employee_id": self.employee.id,
                "check_in": self._utc("2026-08-31 08:15:00"),
            }
        )
        result = self.env["hr.employee"].factory_save_manual_attendance(
            self.employee.id,
            attendance.id,
            "2026-08-31T08:10",
            "2026-08-31T17:05",
        )
        self.assertEqual(result["action"], "updated")
        self.assertEqual(attendance.in_mode, "manual")
        self.assertEqual(attendance.out_mode, "manual")
        self.assertEqual(attendance.factory_manual_edited_by_id, self.env.user)
        self.assertTrue(attendance.factory_manual_edited_at)
        self.assertEqual(
            attendance.check_out, self._utc("2026-08-31 17:05:00")
        )

    def test_manual_backdate_open_evening_attendance(self):
        attendance = self.env['hr.attendance'].create({
            'employee_id': self.employee.id,
            'check_in': self._utc('2026-08-31 21:00:00'),
        })
        self.env['hr.employee'].factory_save_manual_attendance(
            self.employee.id, attendance.id, '2026-08-31T07:00', False)
        self.assertEqual(attendance.check_in, self._utc('2026-08-31 07:00:00'))
        self.assertFalse(attendance.check_out)
        self.assertEqual(attendance.factory_manual_edited_by_id, self.env.user)

    def test_status_filter_uses_unfiltered_counts(self):
        data = self.env["hr.employee"].factory_attendance_dashboard_data(
            self.department.id, "absent", "2026-08-31"
        )
        self.assertEqual(data["counts"]["all"], 1)
        self.assertEqual(data["counts"]["absent"], 1)
        self.assertEqual(len(data["employees"]), 1)

    def _dashboard_at(self, clock, status="all", day="2026-08-31"):
        with patch.object(fields.Datetime, "now", return_value=self._utc(clock)):
            return self.env["hr.employee"].factory_attendance_dashboard_data(
                self.department.id, status, day,
            )

    def test_before_shift_has_no_absence_and_no_attendance_write(self):
        before = self.env["hr.attendance"].search_count([])
        data = self._dashboard_at("2026-08-31 07:59:59")
        self.assertEqual(data["employees"][0]["status"], "not_started")
        self.assertEqual(data["employees"][0]["check_in"], "—")
        self.assertEqual(data["employees"][0]["check_out"], "—")
        self.assertEqual(data["counts"]["absent"], 0)
        self.assertEqual(data["counts"]["not_started"], 1)
        self.assertEqual(data["counts"]["all"], 1)
        self.assertEqual(data["next_status_change_delay_ms"], 1001)
        filtered = self._dashboard_at("2026-08-31 07:59:59", "absent")
        self.assertFalse(filtered["employees"])
        self.assertEqual(filtered["counts"]["absent"], 0)
        self.assertEqual(self.env["hr.attendance"].search_count([]), before)

    def test_absence_starts_at_exact_shift_start_until_first_punch(self):
        for clock in ("08:00:00", "08:00:01", "12:00:00"):
            data = self._dashboard_at("2026-08-31 " + clock)
            self.assertEqual(data["employees"][0]["status"], "absent")
            self.assertEqual(data["counts"]["absent"], 1)
            self.assertEqual(data["counts"]["not_started"], 0)
            self.assertFalse(data["next_status_change_delay_ms"])
        self.env["hr.attendance"].create({
            "employee_id": self.employee.id,
            "check_in": self._utc("2026-08-31 08:05:00"),
        })
        data = self._dashboard_at("2026-08-31 08:05:01")
        self.assertEqual(data["employees"][0]["status"], "at_work")
        self.assertEqual(data["counts"]["absent"], 0)

    def test_early_checkin_is_present_even_before_shift_start(self):
        self.env["hr.attendance"].create({
            "employee_id": self.employee.id,
            "check_in": self._utc("2026-08-31 07:50:00"),
        })
        data = self._dashboard_at("2026-08-31 07:55:00")
        self.assertEqual(data["employees"][0]["status"], "at_work")
        self.assertEqual(data["counts"]["not_started"], 0)

    def test_stale_open_attendance_never_becomes_next_day_overtime(self):
        stale = self.env["hr.attendance"].create({
            "employee_id": self.employee.id,
            "check_in": self._utc("2026-08-30 15:00:00"),
        })

        data = self._dashboard_at("2026-08-31 12:00:00")
        row = data["employees"][0]

        self.assertEqual(row["status"], "absent")
        self.assertEqual(row["check_in"], "—")
        self.assertEqual(row["overtime_minutes"], 0)
        self.assertEqual(row["overtime_label"], "—")
        self.assertFalse(stale.check_out)

    def test_historical_absence_not_hidden_before_todays_shift(self):
        data = self._dashboard_at("2026-09-01 03:00:00")
        self.assertEqual(data["employees"][0]["status"], "absent")
        self.assertEqual(data["counts"]["absent"], 1)

    def test_non_workday_remains_leave_before_shift(self):
        data = self._dashboard_at("2026-08-30 03:00:00", day="2026-08-30")
        self.assertEqual(data["employees"][0]["status"], "leave")
        self.assertEqual(data["counts"]["absent"], 0)
        self.assertFalse(data["next_status_change_delay_ms"])

    def test_each_calendar_start_and_timezone_are_respected(self):
        later_calendar = self.calendar.copy({"name": "Later shift", "tz": "Europe/Berlin"})
        later_calendar.attendance_ids.write({"hour_from": 10.5, "hour_to": 19.5})
        later = self.employee.copy({"name": "Later worker", "barcode": "DASH2",
                                    "resource_calendar_id": later_calendar.id, "tz": "Europe/Berlin"})
        data = self._dashboard_at("2026-08-31 09:00:00")
        by_id = {row["id"]: row for row in data["employees"]}
        self.assertEqual(by_id[self.employee.id]["status"], "absent")
        self.assertEqual(by_id[later.id]["status"], "not_started")
        self.assertEqual(data["counts"]["absent"], 1)
        self.assertEqual(data["counts"]["not_started"], 1)
        self.assertEqual(data["next_status_change_delay_ms"], 9000001)
        at_start = self._dashboard_at("2026-08-31 11:30:00")
        self.assertEqual(at_start["counts"]["absent"], 2)

    def test_midnight_shift_does_not_get_treated_as_missing_start(self):
        self.calendar.attendance_ids.write({"hour_from": 0, "hour_to": 8})
        data = self._dashboard_at("2026-08-31 00:00:00")
        self.assertEqual(data["employees"][0]["status"], "absent")

    def test_exempt_employee_is_removed_from_rows_and_counts_not_records(self):
        attendance = self.env["hr.attendance"].create({
            "employee_id": self.employee.id,
            "check_in": self._utc("2026-08-31 08:30:00"),
            "check_out": self._utc("2026-08-31 18:00:00"),
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "factory_attendance_dashboard.excluded_employee_ids",
            json.dumps([self.employee.id]),
        )
        for status in (
            "all", "at_work", "checked_out", "absent", "late", "overtime",
        ):
            data = self.env["hr.employee"].factory_attendance_dashboard_data(
                self.department.id, status, "2026-08-31",
            )
            self.assertFalse(data["employees"])
            self.assertTrue(all(count == 0 for count in data["counts"].values()))
        self.assertTrue(self.employee.active)
        self.assertTrue(attendance.exists())
        self.assertEqual(attendance.check_in, self._utc("2026-08-31 08:30:00"))

        self.env["ir.config_parameter"].sudo().set_param(
            "factory_attendance_dashboard.excluded_employee_ids", "[]",
        )
        restored = self.env["hr.employee"].factory_attendance_dashboard_data(
            self.department.id, "all", "2026-08-31",
        )
        self.assertEqual(restored["counts"]["all"], 1)

    def test_display_exemptions_require_an_explicit_list_of_employee_ids(self):
        parameter = self.env["ir.config_parameter"].sudo()
        for value in ('false', '{"id": 1}', '[true]', '[-1]', 'invalid'):
            parameter.set_param(
                "factory_attendance_dashboard.excluded_employee_ids", value,
            )
            with self.assertRaises(ValidationError):
                self.env["hr.employee"]._factory_attendance_excluded_employee_ids()

    def test_unwanted_attendance_menus_are_hidden(self):
        hidden_menu_xmlids = (
            "hr_attendance.menu_hr_attendance_view_attendances",
            "hr_attendance.menu_hr_attendance_view_attendances_management",
            "hr_attendance.menu_action_open_form",
            "factory_biometric_attendance.menu_biometric_root",
        )
        for xmlid in hidden_menu_xmlids:
            self.assertFalse(self.env.ref(xmlid).active, xmlid)

        self.assertTrue(
            self.env.ref(
                "factory_attendance_dashboard.menu_factory_attendance_dashboard"
            ).active
        )
        self.assertTrue(
            self.env.ref("hr_attendance.menu_hr_attendance_reporting").active
        )
        self.assertTrue(
            self.env.ref("hr_attendance.menu_hr_attendance_settings").active
        )
