from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessDenied, AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestIndependentApp(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env(context=dict(cls.env.context, no_reset_password=True)),
                                login='native_app_test', password='test-only-password',
                                groups='furniture_mrp.group_furniture_mrp_supervisor_carpentry')
        cls.Session = cls.env['furniture.supervisor.app.session']
        cls.Production = cls.env['furniture.mrp.production'].with_user(cls.user)

    def test_tokens_are_hashed_expire_and_revoke(self):
        session, token = self.Session._issue(self.user)
        self.assertNotEqual(token, session.token_hash)
        self.assertEqual(self.Session._authenticate_token(token), session)
        session.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        with self.assertRaises(AccessDenied):
            self.Session._authenticate_token(token)
        session.unlink()
        with self.assertRaises(AccessDenied):
            self.Session._authenticate_token(token)

    def test_password_change_invalidates_token(self):
        _session, token = self.Session._issue(self.user)
        self.user.password = 'different-test-password'
        with self.assertRaises(AccessDenied):
            self.Session._authenticate_token(token)

    def test_regular_employee_cannot_get_app_session(self):
        employee = new_test_user(self.env(context=dict(self.env.context, no_reset_password=True)),
                                 login='not_a_supervisor', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.Session._issue(employee)

    def test_no_generic_rpc_or_foreign_stage(self):
        session, _token = self.Session._issue(self.user)
        with self.assertRaises(AccessError):
            self.Production._supervisor_app_dispatch('unlink', {}, session)
        with self.assertRaises(AccessError):
            self.Production._supervisor_app_dispatch('action', {
                'request_id': 'test-invalid-scope-12345', 'stage': 'packaging', 'action': 'start',
            }, session)
        with self.assertRaises(AccessError):
            self.Production._supervisor_app_dispatch('wizard', {
                'request_id': 'test-foreign-wizard-12345', 'stage': 'carpentry', 'grant': 'not-issued',
            }, session)

    def test_repeated_command_runs_once(self):
        session, _token = self.Session._issue(self.user)
        payload = {'request_id': 'test-idempotency-123456', 'stage': 'carpentry', 'action': 'materials'}
        with patch.object(type(self.Production), '_supervisor_app_action', return_value={'ok': True}) as action:
            one = self.Production._supervisor_app_dispatch('action', payload, session)
            two = self.Production._supervisor_app_dispatch('action', payload, session)
            self.assertEqual(one, two)
            self.assertEqual(action.call_count, 1)
            with self.assertRaises(ValidationError):
                self.Production._supervisor_app_dispatch('action', dict(payload, action='start'), session)

    def test_independent_profile_and_requests_keep_scope(self):
        session, _token = self.Session._issue(self.user)
        profile = self.Production._supervisor_app_dispatch('profile', {}, session)
        self.assertEqual([row['code'] for row in profile['stages']], ['carpentry'])
        requests = self.Production._supervisor_app_dispatch('requests', {}, session)
        self.assertTrue(all(row['stage_code'] == 'carpentry' for row in requests['rows']))
        warnings = self.Production._supervisor_app_dispatch('warnings', {}, session)
        self.assertTrue(all(row['stage'] == 'carpentry' or row['outgoing'] for row in warnings))

    def test_app_receipt_kit_lifecycle_quality_and_duplicate_finish(self):
        """Real stock/quality operations through the app, isolated fixture cards."""
        import uuid
        from odoo.exceptions import UserError
        from odoo.addons.furniture_mrp.tests.test_mrp_stage_dashboard import TestFurnitureMrpStageDashboard
        case = TestFurnitureMrpStageDashboard('test_cover_tailoring_dashboard_starts_received_products_without_hall_carryover')
        case.env = self.env
        case.Production = self.env['furniture.mrp.production']
        case.ProductionLine = self.env['furniture.mrp.production.line']
        case.product = self.env['product.product'].create({'name': 'APP LIFECYCLE PIECE', 'type': 'consu', 'is_storable': True})
        user = case._create_supervisor_user(('tailoring',))
        production, stages = case._create_production(('tailoring', 'upholstery'), production_lane='cover', name='APP-LIFECYCLE')
        model = self.env['furniture.product.model'].create({'name': 'APP LIFECYCLE MODEL'})
        lines = case._create_lines(production, (1.0, 1.0), ('tailoring', 'upholstery'), extra_values={'furniture_order_model_id': model.id})
        lines.write({'first_stage_started': True, 'first_stage_started_stage': 'upholstery', 'planned_start_stage': 'upholstery'})
        production._ensure_stage_locations()
        production._move_stage_work_to_stock(stages['upholstery']._name, production_lines=lines)
        stages['upholstery'].with_context(furniture_skip_line_consolidation=True)._set_stage_line_ids_data('completed_production_line_ids_data', lines)
        stages['upholstery'].state = 'done'
        raw = self.env['product.product'].create({'name': 'APP RAW MATERIAL', 'type': 'consu', 'is_storable': True})
        for line in lines:
            self.env['furniture.mrp.material.line'].create({'production_id': production.id, 'production_line_id': line.id, 'product_id': raw.id, 'product_uom_id': raw.uom_id.id, 'qty_needed': 2, 'stage': 'tailoring'})
        self.env['stock.quant']._update_available_quantity(raw, production.location_src_id or self.env.ref('stock.stock_location_stock'), 4)
        release = self.env['furniture.mrp.advance.material.release'].create_from_stage_codes(production, ('tailoring',), notify_storekeeper=False)
        release.action_issue()
        api = case.Production.with_user(user)
        session, _ = self.Session._issue(user)
        def command(action, **data):
            return api._supervisor_app_dispatch('action', dict(stage='tailoring', action=action, request_id=str(uuid.uuid4()), **data), session)
        row = release.stage_line_ids.filtered(lambda item: item.stage_code == 'tailoring')
        form = command('receipt', request_key='release:%s' % row.id)['form']
        self.assertEqual(form['kind'], 'receipt')
        api._supervisor_app_dispatch('wizard', {'stage': 'tailoring', 'grant': form['grant'], 'lines': form['lines'], 'request_id': str(uuid.uuid4())}, session)
        self.assertTrue(row.receipt_confirmed)
        production.with_user(user).action_stage_dashboard_request_order_materials('tailoring')
        # Shared hall stock is supplied by a separate warehouse requisition.
        if hasattr(production, '_shared_hall_location'):
            self.env['stock.quant']._update_available_quantity(raw, production._shared_hall_location('tailoring'), 4)
        Kit = self.env['furniture.textile.kit']
        kit = Kit.create({'token': 'app-lifecycle-kit', 'company_id': self.env.company.id, 'stage_code': 'tailoring', 'model_id': model.id, 'recipe_id': self.env['mrp.bom'].search([('type', '=', 'phantom')], limit=1).id, 'stage_timer_planned_qty': 1, 'stage_timer_planned_hours': 2, 'stage_timer_piece_hours': 2, 'member_ids': [(0, 0, {'production_line_id': lines[0].id, 'snapshot': Kit._line_snapshot(lines[0])})]})
        card = {'id': 2000000000000 + kit.id, 'textile_kit': True, 'textile_kit_token': kit.token}
        with patch.object(type(api), 'get_stage_dashboard_data', return_value={'orders': [card]}):
            command('start', order_id=card['id'])
            self.assertEqual(kit.state, 'in_progress')
            command('pause', order_id=card['id'])
            self.assertTrue(kit.stage_timer_paused_at)
            command('resume', order_id=card['id'])
            self.assertFalse(kit.stage_timer_paused_at)
            with self.assertRaises(UserError), self.env.cr.savepoint():
                command('finish', order_id=card['id'])
            command('quality', order_id=card['id'], line_ids=lines[:1].ids, decision='pass')
            payload = dict(stage='tailoring', action='finish', order_id=card['id'], request_id=str(uuid.uuid4()))
            api._supervisor_app_dispatch('action', payload, session)
            self.assertEqual(kit.state, 'done')
            count = self.env['stock.move'].search_count([])
            api._supervisor_app_dispatch('action', payload, session)
            self.assertEqual(self.env['stock.move'].search_count([]), count)
            self.assertEqual(stages['tailoring']._get_stage_line_ids_data('completed_production_line_ids_data'), lines[:1])

    def test_hall_supply_uses_allowed_products_and_real_requester(self):
        if 'furniture.assembly.requisition' not in self.env:
            self.skipTest('Optional hall requisition module not installed')
        import uuid
        raw = self.env['product.product'].create({'name': 'APP ALLOWED HALL MATERIAL', 'type': 'consu', 'is_storable': True})
        self.env['furniture.assembly.supervisor.material'].create({
            'company_id': self.env.company.id, 'supervisor_id': self.user.id,
            'stage_code': 'carpentry', 'product_ids': [(6, 0, raw.ids)],
        })
        session, _ = self.Session._issue(self.user)
        def command(action, **payload):
            return self.Production._supervisor_app_dispatch('action', dict(
                action=action, stage='carpentry', request_id=str(uuid.uuid4()), **payload), session)
        form = command('supply_form')['form']
        self.assertEqual([line['id'] for line in form['lines']], raw.ids)
        with self.assertRaises(AccessError):
            command('supply', lines=[{'id': 0, 'quantity': 5}])
        command('supply', lines=[{'id': raw.id, 'quantity': 5}])
        record = self.env['furniture.assembly.requisition'].search([
            ('requested_by_id', '=', self.user.id), ('line_ids.product_id', '=', raw.id)], limit=1)
        self.assertEqual(record.state, 'pending')
        self.assertEqual(record.line_ids.requested_qty, 5)
        rows = self.Production._supervisor_app_dispatch('requests', {}, session)['rows']
        self.assertIn('supply:%s' % record.id, [row['key'] for row in rows])
