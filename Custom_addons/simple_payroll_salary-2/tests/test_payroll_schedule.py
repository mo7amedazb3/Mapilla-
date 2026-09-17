from datetime import date, datetime

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "payroll_schedule")
class TestSimplePayrollWorkSchedule(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "Saturday-Thursday, 10 Hours",
            "company_id": cls.company.id,
            "tz": "UTC",
            "attendance_ids": [
                (0, 0, {
                    "name": "10-hour workday",
                    "dayofweek": dayofweek,
                    "hour_from": 8.0,
                    "hour_to": 18.0,
                    "day_period": "morning",
                })
                for dayofweek in ("5", "6", "0", "1", "2", "3")
            ],
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "Saturday-Thursday Employee",
            "company_id": cls.company.id,
            "resource_calendar_id": cls.calendar.id,
        })
        cls.contract = cls.env["hr.contract"].create({
            "name": "Saturday-Thursday Contract",
            "employee_id": cls.employee.id,
            "company_id": cls.company.id,
            "resource_calendar_id": cls.calendar.id,
            "date_start": date(2026, 8, 1),
            "wage": 6000.0,
            "state": "open",
        })

    def _create_slip(self, date_from=date(2026, 8, 1), date_to=date(2026, 8, 7)):
        return self.env["simple.payroll.slip"].create({
            "employee_id": self.employee.id,
            "company_id": self.company.id,
            "date_from": date_from,
            "date_to": date_to,
        })

    def test_friday_outside_calendar_is_not_an_absence(self):
        self.env["hr.attendance"].create([
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 8, day, 8, 0),
                "check_out": datetime(2026, 8, day, 18, 0),
            }
            for day in range(1, 7)
        ])

        slip = self._create_slip()

        self.assertEqual(slip.contract_id, self.contract)
        self.assertAlmostEqual(slip.expected_working_days, 6.0)
        self.assertAlmostEqual(slip.hours_per_day, 10.0)
        self.assertAlmostEqual(slip.expected_hours, 60.0)
        self.assertAlmostEqual(slip.attendance_hours, 60.0)
        self.assertAlmostEqual(slip.absence_days, 0.0)
        self.assertAlmostEqual(slip.absence_deduction, 0.0)

    def test_friday_only_period_has_no_expected_work_or_deduction(self):
        slip = self._create_slip(
            date_from=date(2026, 8, 7),
            date_to=date(2026, 8, 7),
        )

        self.assertAlmostEqual(slip.expected_working_days, 0.0)
        self.assertAlmostEqual(slip.hours_per_day, 10.0)
        self.assertAlmostEqual(slip.expected_hours, 0.0)
        self.assertAlmostEqual(slip.absence_days, 0.0)
        self.assertAlmostEqual(slip.absence_deduction, 0.0)

    def test_schedule_is_limited_to_contract_dates(self):
        self.contract.date_start = date(2026, 8, 3)

        slip = self._create_slip()

        self.assertAlmostEqual(slip.expected_working_days, 4.0)
        self.assertAlmostEqual(slip.hours_per_day, 10.0)
        self.assertAlmostEqual(slip.expected_hours, 40.0)

    def test_calendar_hours_recompute_existing_slip(self):
        slip = self._create_slip()
        self.calendar.attendance_ids.write({"hour_to": 16.0})

        self.assertAlmostEqual(slip.expected_working_days, 6.0)
        self.assertAlmostEqual(slip.hours_per_day, 8.0)
        self.assertAlmostEqual(slip.expected_hours, 48.0)
