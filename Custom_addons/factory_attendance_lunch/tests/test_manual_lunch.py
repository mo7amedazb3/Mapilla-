from datetime import date, datetime
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..models.lunch_policy import summarize_day


@tagged("post_install", "-at_install")
class TestManualLunch(TransactionCase):
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

    def details(self):
        return self.env['hr.employee'].factory_manual_lunch_details(self.employee.id, str(self.day))

    def save_lunch(self, departure, returned=False, record=None):
        record = record or self.details()['records'][0]
        return self.env['hr.employee'].factory_save_manual_lunch(
            self.employee.id, str(self.day), record['id'],
            str(self.day)+'T'+departure, str(self.day)+'T'+returned if returned else False, record['version'])

    def test_manual_pending_return(self):
        morning = self.attendance('09:00:00', '14:00:00')
        self.save_lunch('14:10:00', '15:00:00')
        rows = self.env['hr.attendance'].search([('employee_id','=',self.employee.id)],order='check_in')
        self.assertEqual(len(rows),2)
        self.assertEqual(morning.check_in,self.utc('09:00:00'))
        self.assertEqual(rows[-1].check_in,self.utc('15:00:00'))
        self.assertFalse(rows[-1].check_out)
        self.assertEqual(morning.factory_manual_edited_by_id,self.env.user)
        self.assertEqual(self.dashboard()['employees'][0]['lunch_seconds'],3000)

    def test_manual_preserves_endpoints(self):
        morning = self.attendance('09:00:00','14:00:00')
        afternoon = self.attendance('15:00:00','19:00:00')
        self.save_lunch('13:10:00','13:45:00')
        self.assertEqual(morning.check_in,self.utc('09:00:00'))
        self.assertEqual(afternoon.check_out,self.utc('19:00:00'))
        self.assertEqual(morning.in_mode,'biometric')
        self.assertEqual(afternoon.out_mode,'biometric')
        self.assertEqual(afternoon.in_mode,'manual')

    def test_manual_stale_punch(self):
        morning=self.attendance('09:00:00','14:00:00')
        record=self.details()['records'][0]
        self.attendance('15:00:00')
        with self.assertRaises(ValidationError):
            self.save_lunch('14:10:00','15:10:00',record)
        self.assertEqual(morning.check_out,self.utc('14:00:00'))

    def test_manual_permissions(self):
        for user in (self.officer,self.ordinary):
            with self.assertRaises(AccessError):
                self.env['hr.employee'].with_user(user).factory_manual_lunch_details(self.employee.id,str(self.day))
            with self.assertRaises(AccessError):
                self.env['hr.employee'].with_user(user).factory_save_manual_lunch(self.employee.id,str(self.day),0,'',False,'')

    def test_manual_invalid_and_clear(self):
        morning=self.attendance('09:00:00','14:00:00')
        self.attendance('15:00:00','19:00:00')
        for departure,returned in [('12:00:00','15:00:00'),('14:00:00','13:00:00'),('14:00:00',False),('14:00:00','20:00:00')]:
            with self.assertRaises(ValidationError):
                self.save_lunch(departure,returned)
        self.assertEqual(morning.check_out,self.utc('14:00:00'))

    def test_missing_return_stays_available_after_cutoff(self):
        morning = self.attendance('09:00:00', '14:00:00')
        data = self.dashboard('17:00:00', status='lunch')
        self.assertEqual(data['counts']['lunch'], 1)
        self.assertEqual(data['employees'][0]['lunch_state'], 'departed')
        self.assertEqual(data['employees'][0]['check_out'], '14:00')
        self.assertEqual(morning.check_out, self.utc('14:00:00'))
        self.save_lunch('14:00:00', '15:00:00')
        self.assertEqual(self.dashboard('17:00:00', status='lunch')['employees'], [])

    def test_actual_late_return_not_waiting_for_manual_return(self):
        self.attendance('09:00:00', '14:00:00')
        self.attendance('16:30:00')
        data = self.dashboard('17:00:00', status='lunch')
        self.assertEqual(data['counts']['lunch'], 0)
        self.assertEqual(data['employees'], [])

    def test_late_visibility_boundary(self):
        for start,visible in [('09:05:00',False),('09:15:00',False),('09:15:59',False),('09:16:00',True)]:
            a=self.attendance(start)
            data=self.dashboard('12:00:00')
            self.assertEqual(data['counts']['late'],int(visible))
            row=data['employees'][0]
            self.assertEqual(bool(row['delay_label']),visible)
            self.assertEqual(len(self.dashboard('12:00:00',status='late')['employees']),int(visible))
            a.unlink()

    def test_create_lunch_from_open_attendance(self):
        employee = self.employee
        morning = self.env['hr.attendance'].create({
            'employee_id': employee.id, 'check_in': self.utc('09:00:00'),
        })
        api = self.env['hr.employee'].sudo()
        details = api.factory_manual_lunch_details(employee.id, str(self.day))
        record = next(r for r in details['records'] if r['id'] == morning.id)
        api.factory_save_manual_lunch(employee.id, str(self.day), morning.id,
            str(self.day) + 'T14:00', False, record['version'])
        self.assertEqual(morning.check_in, self.utc('09:00:00'))
        self.assertEqual(morning.check_out, self.utc('14:00:00'))
        details = api.factory_manual_lunch_details(employee.id, str(self.day))
        record = next(r for r in details['records'] if r['id'] == morning.id)
        api.factory_save_manual_lunch(employee.id, str(self.day), morning.id,
            str(self.day) + 'T14:00', str(self.day) + 'T15:00', record['version'])
        returned = self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id), ('id', '!=', morning.id),
        ])
        self.assertEqual(returned.check_in, self.utc('15:00:00'))
        self.assertFalse(returned.check_out)
