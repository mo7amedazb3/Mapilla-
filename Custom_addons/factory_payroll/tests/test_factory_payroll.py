# -*- coding: utf-8 -*-

from datetime import date, datetime

import pytz

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFactoryPayroll(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env["resource.calendar"].create(
            {
                "name": "Factory Payroll 8 Hours",
                "company_id": cls.env.company.id,
                "tz": "Africa/Cairo",
                "attendance_ids": [
                    (
                        0,
                        0,
                        {
                            "name": day,
                            "dayofweek": dayofweek,
                            "hour_from": 8.0,
                            "hour_to": 16.0,
                            "day_period": "morning",
                        },
                    )
                    for day, dayofweek in (("Monday", "0"), ("Tuesday", "1"))
                ],
            }
        )
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Factory Payroll Worker",
                "company_id": cls.env.company.id,
                "resource_calendar_id": cls.calendar.id,
                "tz": "Africa/Cairo",
            }
        )
        cls.contract = cls.env["hr.contract"].create(
            {
                "name": "Factory Payroll Contract",
                "employee_id": cls.employee.id,
                "company_id": cls.env.company.id,
                "resource_calendar_id": cls.calendar.id,
                "date_start": date(2026, 8, 1),
                "wage": 3100.0,
                "state": "open",
            }
        )

    @classmethod
    def _utc(cls, value):
        local = pytz.timezone("Africa/Cairo").localize(
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        )
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    def _create_slip(self):
        return self.env["simple.payroll.slip"].create(
            {
                "employee_id": self.employee.id,
                "company_id": self.env.company.id,
                "date_from": date(2026, 8, 31),
                "date_to": date(2026, 9, 1),
                "factory_bonus_amount": 100.0,
                "factory_other_deduction_amount": 50.0,
            }
        )

    def test_biometric_attendance_drives_factory_payroll(self):
        self.env["hr.attendance"].create(
            {
                "employee_id": self.employee.id,
                "check_in": self._utc("2026-08-31 08:00:00"),
                "check_out": self._utc("2026-08-31 15:00:00"),
                "in_mode": "biometric",
                "out_mode": "biometric",
            }
        )
        leave_type = self.env["hr.leave.type"].create(
            {
                "name": "Factory Paid Leave",
                "requires_allocation": "no",
                "leave_validation_type": "no_validation",
                "unpaid": False,
            }
        )
        leave = self.env["hr.leave"].create(
            {
                "name": "Paid leave",
                "employee_id": self.employee.id,
                "holiday_status_id": leave_type.id,
                "request_date_from": date(2026, 9, 1),
                "request_date_to": date(2026, 9, 1),
                "date_from": self._utc("2026-09-01 08:00:00"),
                "date_to": self._utc("2026-09-01 16:00:00"),
            }
        )
        self.assertEqual(leave.state, "validate")
        slip = self._create_slip()
        slip.action_recompute_factory_payroll()

        self.assertAlmostEqual(slip.expected_hours, 16.0)
        self.assertAlmostEqual(slip.attendance_hours, 7.0)
        self.assertAlmostEqual(slip.factory_paid_leave_hours, 8.0)
        self.assertAlmostEqual(slip.absence_days, 0.0)
        self.assertAlmostEqual(slip.delay_hours, 1.0)
        self.assertEqual(slip.factory_biometric_attendance_count, 1)
        self.assertAlmostEqual(slip.delay_deduction, 193.75)
        self.assertAlmostEqual(slip.net_salary, 2956.25)

    def test_batch_generation_creates_drafts_and_skips_duplicates(self):
        wizard = self.env["factory.payroll.batch.wizard"].create(
            {
                "date_from": date(2026, 8, 31),
                "date_to": date(2026, 9, 1),
                "employee_ids": [(6, 0, self.employee.ids)],
            }
        )
        result = wizard.action_generate()
        self.assertEqual(result["tag"], "display_notification")
        slips = self.env["simple.payroll.slip"].search(
            [
                ("employee_id", "=", self.employee.id),
                ("date_from", "=", date(2026, 8, 31)),
                ("date_to", "=", date(2026, 9, 1)),
            ]
        )
        self.assertEqual(len(slips), 1)
        self.assertEqual(slips.state, "draft")

        wizard.action_generate()
        self.assertEqual(
            self.env["simple.payroll.slip"].search_count(
                [
                    ("employee_id", "=", self.employee.id),
                    ("date_from", "=", date(2026, 8, 31)),
                    ("date_to", "=", date(2026, 9, 1)),
                ]
            ),
            1,
        )

    def test_confirm_requires_contract_and_recomputes(self):
        slip = self._create_slip()
        slip.action_confirm()
        self.assertEqual(slip.state, "confirmed")
        self.assertTrue(slip.factory_last_calculated_at)
        self.assertEqual(slip.factory_last_calculated_by_id, self.env.user)
