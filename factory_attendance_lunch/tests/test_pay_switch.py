from odoo.tests import TransactionCase
class TestSwitch(TransactionCase):
 def test_filter(self):
  dep=self.env['hr.department'].create({'name':'Switch isolated test'})
  employees=self.env['hr.employee'].create([{'name':basis,'department_id':dep.id,'furniture_pay_basis':basis} for basis in ['time','production']])
  for basis in ['time','production']:
   data=self.env['hr.employee'].factory_attendance_dashboard_data(dep.id,'all','2026-09-13',basis)
   self.assertEqual([r['id'] for r in data['employees']],employees.filtered(lambda e:e.furniture_pay_basis==basis).ids)
   self.assertEqual(data['counts']['all'],1)
   data=self.env['hr.employee'].factory_attendance_dashboard_data(dep.id,'lunch','2026-09-13',basis)
   self.assertEqual(data['employees'],[])
   self.assertEqual(data['counts']['all'],1)
