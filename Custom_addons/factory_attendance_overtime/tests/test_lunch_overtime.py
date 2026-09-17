from datetime import datetime
from unittest.mock import patch
import pytz
from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

@tagged("post_install", "-at_install")
class TestLunchOvertime(TransactionCase):
    def test_lunch_bonus_eligibility_and_payroll(self):
        env = self.env
        z=pytz.timezone('Africa/Cairo')
        def utc(t):return z.localize(datetime.strptime('2026-08-31 '+t,'%Y-%m-%d %H:%M')).astimezone(pytz.UTC).replace(tzinfo=None)
        cal=env['resource.calendar'].create({'name':'Lunch overtime verification','tz':z.zone,'attendance_ids':[(5,0,0),(0,0,{'name':'Monday','dayofweek':'0','hour_from':9,'hour_to':19,'day_period':'morning'})]})
        e=env['hr.employee'].create({'name':'Lunch overtime verification','tz':z.zone,'resource_calendar_id':cal.id,'furniture_pay_basis':'time'})
        a=env['hr.attendance'].create({'employee_id':e.id,'check_in':utc('09:00'),'check_out':utc('19:00')})
        def row():
         d=env['hr.employee'].factory_overtime_approval_data('2026-08-31')
         return next((r for r in d['lunch_rows'] if r['employee_id']==e.id),None)
        with patch.object(fields.Datetime,'now',return_value=utc('15:59')):assert not row()
        with patch.object(fields.Datetime,'now',return_value=utc('20:00')):
         r=row();assert r and r['overtime_minutes']==60
         e.furniture_pay_basis='production';assert not row()
         e.furniture_pay_basis='time';e.factory_lunch_enabled=False;assert not row()
         e.factory_lunch_enabled=True
         a.check_out=utc('14:00');assert not row()
         a.check_out=utc('19:00')
         before=e._furniture_attendance_details('2026-08-31','2026-08-31')['overtime_hours']
         r=row();result=env['hr.employee'].factory_overtime_approval_bulk_decide([
             {'attendance_id':a.id,'kind':'lunch','expected_version':r['expected_version']}
         ],'approve');assert result['state']=='approved' and result['count']==1
         after=e._furniture_attendance_details('2026-08-31','2026-08-31')['overtime_hours'];assert after==before+1,(before,after)
         r=row();env['hr.employee'].factory_lunch_overtime_decide(a.id,'approve',r['expected_version'])
         assert e._furniture_attendance_details('2026-08-31','2026-08-31')['overtime_hours']==after
         r=row();env['hr.employee'].factory_lunch_overtime_decide(a.id,'reject',r['expected_version'])
         assert e._furniture_attendance_details('2026-08-31','2026-08-31')['overtime_hours']==before
         try:env['hr.employee'].factory_lunch_overtime_decide(a.id,'manual',row()['expected_version'])
         except ValidationError:pass
         else:raise AssertionError('Manual modification permitted')
         assert a.check_in==utc('09:00') and a.check_out==utc('19:00')
         a.check_out=False;assert row() and not row()['is_open']
