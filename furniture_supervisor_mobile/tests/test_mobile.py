from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestSupervisorMobile(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Production = cls.env['furniture.mrp.production']
        cls.supervisor = new_test_user(
            cls.env(context=dict(cls.env.context, no_reset_password=True)), login='mobile_supervisor_test',
            groups='furniture_mrp.group_furniture_mrp_supervisor_carpentry',
        )
        cls.other = new_test_user(cls.env(context=dict(cls.env.context, no_reset_password=True)), login='mobile_employee_test', groups='base.group_user')

    def test_employee_cannot_enter_supervisor_api(self):
        with self.assertRaises(AccessError):
            self.Production.with_user(self.other).get_supervisor_mobile_requests()

    def test_supervisor_scope_does_not_expand(self):
        production = self.Production.with_user(self.supervisor)
        self.assertEqual(production._stage_dashboard_access_profile()['allowed_stage_codes'], ['carpentry'])
        with self.assertRaises(AccessError):
            production.get_stage_dashboard_data(stage_code='packaging')

    def test_requests_are_scoped_and_payload_is_minimal(self):
        production = self.Production.with_context(
            furniture_skip_material_refresh=True, furniture_skip_stage_plan_sync=True,
        ).create({'name': 'MOBILE-TEST', 'use_carpentry': True, 'use_packaging': True})
        Request = self.env['furniture.mrp.store.request']
        records = Request.create([{
            'name': 'MOBILE-' + stage,
            'production_id': production.id, 'stage_code': stage,
            'stage_order_model': 'furniture.mrp.' + stage, 'stage_order_res_id': 0,
            'request_kind': 'direct', 'start_mode': 'direct',
            'requested_by_id': self.supervisor.id, 'assigned_to_id': self.env.user.id,
        } for stage in ('carpentry', 'packaging')])
        result = self.Production.with_user(self.supervisor).get_supervisor_mobile_requests()
        ids = {row['id'] for row in result['rows']}
        self.assertIn(records[0].id, ids)
        self.assertNotIn(records[1].id, ids)
        allowed = {'id', 'name', 'stage_code', 'state', 'receipt_state', 'requested_at'}
        for row in result['rows']:
            self.assertLessEqual(set(row), allowed)

    def test_manifest_and_action_match(self):
        action = self.env.ref('furniture_supervisor_mobile.action_supervisor_mobile')
        self.assertEqual(action.path, 'supervisors')
        self.assertEqual(action.tag, 'furniture_supervisor_mobile.app')
