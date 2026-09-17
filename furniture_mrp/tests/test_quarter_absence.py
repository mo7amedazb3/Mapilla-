from datetime import date, datetime
import pytz
from odoo.tests import TransactionCase, tagged

@tagged('post_install','-at_install')
class TestQuarterAbsence(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar=cls.env['resource.calendar'].create({'name':'Quarter allowance test','tz':'Africa/Cairo','attendance_ids':[(5,0,0)]+[(0,0,{'name':'Shift','dayofweek':str(i),'hour_from':9,'hour_to':17,'day_period':'morning'}) for i in range(7)]})
        cls.employee=cls.env['hr.employee'].create({'name':'Quarter allowance worker','tz':'Africa/Cairo','resource_calendar_id':cls.calendar.id,'furniture_pay_basis':'time'})
        cls.contract=cls.env['hr.contract'].create({'name':'Quarter test','employee_id':cls.employee.id,'resource_calendar_id':cls.calendar.id,'date_start':date(2026,4,1),'state':'open','wage':6000})

    def details(self,start,stop):
        return self.employee._furniture_attendance_details(date.fromisoformat(start),date.fromisoformat(stop))

    def test_four_then_deduct_and_recompute(self):
        for _ in range(2):
            d=self.details('2026-04-01','2026-04-06')
            self.assertEqual(d['absence_days'],6)
            self.assertEqual(d['absence_allowance_days'],4)
            self.assertEqual(d['deductible_absence_days'],2)
        d=self.details('2026-04-04','2026-04-06')
        self.assertEqual(d['absence_allowance_days'],1)
        self.assertEqual(d['deductible_absence_days'],2)

    def test_month_no_reset_quarter_resets(self):
        self.assertEqual(self.details('2026-05-01','2026-05-02')['absence_allowance_days'],0)
        self.assertEqual(self.details('2026-07-01','2026-07-04')['absence_allowance_days'],4)
        d=self.details('2026-06-30','2026-07-05')
        self.assertEqual(d['absence_allowance_days'],4)
        self.assertEqual(d['deductible_absence_days'],2)

    def test_no_consumption_before_contract_start(self):
        self.contract.date_start=date(2026,5,15)
        self.assertEqual(self.details('2026-05-15','2026-05-18')['absence_allowance_days'],4)

    def test_lateness_not_covered_and_payroll_deduction(self):
        tz=pytz.timezone('Africa/Cairo')
        def utc(hour,minute):
            return tz.localize(datetime(2026,4,1,hour,minute)).astimezone(pytz.UTC).replace(tzinfo=None)
        self.env['hr.attendance'].create({'employee_id':self.employee.id,'check_in':utc(9,25),'check_out':utc(17,0)})
        d=self.details('2026-04-01','2026-04-05')
        self.assertEqual(d['absence_allowance_days'],4)
        self.assertAlmostEqual(d['deductible_late_hours'],25/60)
        slip=self.env['simple.payroll.slip'].create({'employee_id':self.employee.id,'company_id':self.env.company.id,'date_from':date(2026,4,1),'date_to':date(2026,4,5)})
        slip.action_recompute_factory_payroll()
        self.assertEqual(slip.absence_days,4)
        self.assertEqual(slip.absence_deduction,0)
        self.assertGreater(slip.delay_deduction,0)

    def test_rest_days_do_not_consume(self):
        self.calendar.attendance_ids.filtered(lambda r:r.dayofweek in ['5','6']).unlink()
        d=self.details('2026-04-01','2026-04-07')
        self.assertEqual(d['absence_days'],5)
        self.assertEqual(d['absence_allowance_days'],4)
        self.assertEqual(d['deductible_absence_days'],1)

    def test_approved_calendar_leave_does_not_consume(self):
        tz=pytz.timezone('Africa/Cairo')
        self.env['resource.calendar.leaves'].create({
            'name':'Approved leave test','calendar_id':self.calendar.id,'resource_id':self.employee.resource_id.id,
            'date_from':tz.localize(datetime(2026,4,1,0,0)).astimezone(pytz.UTC).replace(tzinfo=None),
            'date_to':tz.localize(datetime(2026,4,2,0,0)).astimezone(pytz.UTC).replace(tzinfo=None),
        })
        d=self.details('2026-04-01','2026-04-05')
        self.assertEqual(d['absence_days'],4)
        self.assertEqual(d['absence_allowance_days'],4)
        self.assertEqual(d['deductible_absence_days'],0)

    def test_admin_can_disable_and_enable_allowance(self):
        from unittest.mock import patch
        from odoo.exceptions import AccessError
        from odoo.tests.common import new_test_user
        admin = self.env.ref('base.user_admin')
        api = self.env['hr.employee'].with_user(admin)
        with patch.object(type(self.env['simple.payroll.slip']), 'action_recompute_factory_payroll', return_value=True) as refresh:
            api.factory_set_quarter_absence_enabled(False)
            self.assertEqual(self.details('2026-04-01','2026-04-04')['deductible_absence_days'],4)
            api.factory_set_quarter_absence_enabled(True)
            self.assertEqual(self.details('2026-04-01','2026-04-04')['deductible_absence_days'],0)
        officer=new_test_user(self.env,login='quarter_toggle_officer',groups='hr_attendance.group_hr_attendance_officer')
        with self.assertRaises(AccessError):
            self.env['hr.employee'].with_user(officer).factory_set_quarter_absence_enabled(False)
