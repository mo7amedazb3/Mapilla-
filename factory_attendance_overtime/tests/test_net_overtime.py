from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestNetOvertime(TransactionCase):
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
        cls.admin = new_test_user(cls.env, login="net_overtime_admin",
                                 groups="base.group_system,hr_attendance.group_hr_attendance_manager")
        cls.officer = new_test_user(cls.env, login="net_overtime_officer",
                                   groups="hr_attendance.group_hr_attendance_officer")
        cls.manager = new_test_user(cls.env, login="net_overtime_manager",
                                   groups="hr_attendance.group_hr_attendance_manager")
        cls.ordinary = new_test_user(cls.env, login="net_overtime_ordinary", groups="base.group_user")
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


    def net(self, attendance):
        return self.employee._factory_net_overtime(attendance, self.employee._factory_overtime_shift(attendance), self.utc('22:00:00'))

    def test_boundaries_and_offset(self):
        for start,end,remaining,eligible in [
            ('09:05:00','20:05:00',0,0),
            ('09:00:00','20:20:00',0,0),
            ('09:10:00','20:30:00',0,0),
            ('09:10:00','20:35:00',0,25),
            ('09:25:00','20:05:00',20,0),
            ('09:00:00','20:21:00',0,21)]:
            with self.subTest(start=start,end=end), self.env.cr.savepoint():
                a=self.attendance(start,end)
                net=self.net(a)
                self.assertEqual(net['remaining_late_minutes'],remaining)
                self.assertEqual(net['eligible_minutes'],eligible)
                data=self.api.factory_overtime_approval_data(str(self.day))
                self.assertEqual(any(r['attendance_id']==a.id for r in data['rows']),bool(eligible))
                a.unlink()

    def test_lunch_uses_morning_lateness_once(self):
        self.attendance('09:10:00','14:00:00')
        a=self.attendance('15:00:00','20:35:00')
        self.assertEqual(self.net(a)['attendance_minutes'],25)
        row=self.api._factory_overtime_approval_row(a,self.employee._factory_overtime_shift(a),self.utc('22:00:00'))
        self.api.factory_overtime_approval_decide(a.id,'approve',row['expected_version'])
        self.assertEqual(a.factory_overtime_approved_minutes,25)
        self.assertAlmostEqual(self.employee._furniture_attendance_details(self.day,self.day)['overtime_hours'],25/60)

    def test_payroll_offset_without_approval_and_reject(self):
        a=self.attendance('09:25:00','20:05:00')
        details=self.employee._furniture_attendance_details(self.day,self.day)
        self.assertAlmostEqual(details['deductible_late_hours'],20/60)
        self.assertEqual(details['overtime_hours'],0)
        a.write({'check_out':self.utc('21:00:00')})
        row=self.api._factory_overtime_approval_row(a,self.employee._factory_overtime_shift(a),self.utc('22:00:00'))
        self.api.factory_overtime_approval_decide(a.id,'reject',row['expected_version'])
        details=self.employee._furniture_attendance_details(self.day,self.day)
        self.assertEqual(details['deductible_late_hours'],0)
        self.assertEqual(details['overtime_hours'],0)

    def test_manual_time_recalculates_net_and_threshold(self):
        a=self.attendance('09:10:00','21:00:00')
        self.api.factory_overtime_approval_decide(a.id,'manual',a.write_date.isoformat(),str(self.day)+'T20:35:00')
        self.assertEqual(a.factory_overtime_approved_minutes,25)
        self.api.factory_overtime_approval_decide(a.id,'manual',a.write_date.isoformat(),str(self.day)+'T20:30:00')
        self.assertEqual(a.factory_overtime_approved_minutes,0)

    def test_dashboard_agrees(self):
        self.attendance('09:10:00','20:35:00')
        data=self.api.factory_attendance_dashboard_data(self.department.id,'all',str(self.day))
        self.assertEqual(data['employees'][0]['overtime_minutes'],25)
        self.assertEqual(data['employees'][0]['late_minutes'],0)
