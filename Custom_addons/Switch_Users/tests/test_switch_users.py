import json
import time

from odoo import Command, http
from odoo.exceptions import AccessDenied
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from ..models.switch_users import DIRECT_USERS_PARAM, GRANT_KEY


@tagged('post_install', '-at_install')
class TestSwitchUsers(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(cls.env, login='switch_test_admin', groups='base.group_system')
        cls.worker = new_test_user(cls.env, login='switch_test_worker', groups='base.group_user')
        cls.second = new_test_user(cls.env, login='switch_test_second', groups='base.group_user')
        cls.unlinked = new_test_user(cls.env, login='switch_test_unlinked', groups='base.group_user')
        cls.portal = new_test_user(cls.env, login='switch_test_portal', groups='base.group_portal')
        cls.Employee = cls.env['hr.employee'].with_context(tracking_disable=True)
        cls.env['ir.config_parameter'].sudo().set_param(DIRECT_USERS_PARAM, '[]')
        for user in (cls.worker, cls.second):
            values = {'name': user.name, 'user_id': user.id, 'company_id': cls.admin.company_id.id}
            if 'furniture_mrp_role' in cls.Employee._fields:
                values['furniture_mrp_role'] = 'worker'
            cls.Employee.create(values)

    def authenticate(self, *args, **kwargs):
        session = super().authenticate(*args, **kwargs)
        # Use the same host-scoped cookie as the real HTTP response so SID
        # rotation replaces it instead of leaving a duplicate test-helper cookie.
        for cookie in list(self.opener.cookies):
            if cookie.name == 'session_id':
                self.opener.cookies.clear(cookie.domain, cookie.path, cookie.name)
        self.opener.cookies.set('session_id', session.sid, domain='127.0.0.1', path='/')
        return session

    def _rpc(self, route, params=None):
        response = self.url_open(route, data=json.dumps({
            'jsonrpc': '2.0', 'method': 'call', 'params': params or {}, 'id': 1,
        }), headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _accounts(self):
        result = self._rpc('/switch_users/accounts')
        self.assertNotIn('error', result)
        return result['result']

    def _switch(self, user):
        result = self._rpc('/switch_users/switch', {
            'user_id': user.id, 'csrf_token': self._accounts()['csrf_token'],
        })
        self.assertNotIn('error', result)
        return result['result']

    def _stored_session(self):
        sid = next(cookie.value for cookie in self.opener.cookies if cookie.name == 'session_id')
        return http.root.session_store.get(sid)

    def _assert_denied(self, result):
        self.assertIn('error', result)
        self.assertIn(result['error']['data']['name'], (
            'odoo.exceptions.AccessDenied', 'odoo.http.SessionExpiredException',
        ))

    def test_admin_switch_restore_real_permissions_and_rotation(self):
        self.authenticate(self.admin.login, self.admin.login)
        before = self._stored_session()
        groups = self.worker.groups_id.ids
        self.env.cr.execute('SELECT password FROM res_users WHERE id=%s', [self.worker.id])
        password = self.env.cr.fetchone()[0]
        self.assertTrue(self._switch(self.worker)['changed'])
        after = self._stored_session()
        self.assertNotEqual(before.sid, after.sid)
        self.assertEqual(after.uid, self.worker.id)
        self.assertEqual(after.db, self.env.cr.dbname)
        self.assertFalse(after.get('su'))
        self.assertNotIn('allowed_company_ids', after.context)
        self.assertNotIn('pre_uid', after)
        self.assertEqual(after[GRANT_KEY]['origin_uid'], self.admin.id)
        status = self._rpc('/web/session/get_session_info')['result']
        self.assertEqual(status['uid'], self.worker.id)
        self.assertFalse(status['is_admin'])
        self.assertTrue(status['switch_users']['switched'])
        self.assertNotIn('proof', json.dumps(self._accounts()))
        self.assertFalse(self.worker.with_user(self.worker)._is_system())
        self.assertTrue(self._switch(self.second)['changed'])
        self.assertEqual(self._stored_session().uid, self.second.id)
        result = self._rpc('/switch_users/restore', {'csrf_token': self._accounts()['csrf_token']})
        self.assertNotIn('error', result)
        restored = self._stored_session()
        self.assertEqual(restored.uid, self.admin.id)
        self.assertNotIn(GRANT_KEY, restored)
        self.assertEqual(self.worker.groups_id.ids, groups)
        self.env.cr.execute('SELECT password FROM res_users WHERE id=%s', [self.worker.id])
        self.assertEqual(self.env.cr.fetchone()[0], password)

    def test_regular_worker_and_anonymous_are_denied(self):
        for user in (None, self.worker):
            self.authenticate(user.login if user else None, '')
            self._assert_denied(self._rpc('/switch_users/accounts'))
            self._assert_denied(self._rpc('/switch_users/restore', {'csrf_token': 'invalid'}))

    def test_csrf_and_ineligible_targets(self):
        self.authenticate(self.admin.login, '')
        self._assert_denied(self._rpc('/switch_users/switch', {
            'user_id': self.worker.id, 'csrf_token': 'invalid',
        }))
        csrf = self._accounts()['csrf_token']
        for uid in (1, self.admin.id, self.unlinked.id, self.portal.id, -1, True, '12'):
            self._assert_denied(self._rpc('/switch_users/switch', {'user_id': uid, 'csrf_token': csrf}))
            self.assertEqual(self._stored_session().uid, self.admin.id)

    def test_archived_and_cross_company_targets_excluded(self):
        self.authenticate(self.admin.login, '')
        self.worker.active = False
        other_company = self.env['res.company'].create({'name': 'Switch Users isolated company'})
        self.second.company_ids = [Command.link(other_company.id)]
        accounts = self._accounts()
        ids = [row['user_id'] for row in accounts['workers'] + accounts['supervisors']]
        self.assertNotIn(self.worker.id, ids)
        self.assertNotIn(self.second.id, ids)

    def test_expired_grant_ends_session(self):
        self.authenticate(self.admin.login, '')
        self._switch(self.worker)
        session = self._stored_session()
        session[GRANT_KEY] = dict(session[GRANT_KEY], expires=time.time() - 1)
        http.root.session_store.save(session)
        self._assert_denied(self._rpc('/switch_users/accounts'))
        self.assertFalse(self._stored_session().uid)

    def test_origin_password_change_revokes_grant(self):
        self.authenticate(self.admin.login, '')
        self._switch(self.worker)
        self.admin.password = 'changed-only-in-isolated-test'
        self._assert_denied(self._rpc('/switch_users/accounts'))
        self.assertFalse(self._stored_session().uid)

    def test_origin_admin_role_removal_revokes_grant(self):
        self.authenticate(self.admin.login, '')
        self._switch(self.worker)
        self.admin.groups_id = [Command.set([self.env.ref('base.group_user').id])]
        self._assert_denied(self._rpc('/switch_users/accounts'))

    def test_grant_bound_to_database_and_target(self):
        self.authenticate(self.admin.login, '')
        self._switch(self.worker)
        session = self._stored_session()
        service = self.env['switch.users.service']
        saved = dict(session[GRANT_KEY])
        for changes in ({'db': 'another_database'}, {'target_uid': self.second.id}, {'proof': 'invalid'}):
            session[GRANT_KEY] = dict(saved, **changes)
            self.assertFalse(service._origin(session))
        session[GRANT_KEY] = saved
        self.assertEqual(service._origin(session), self.admin)

    def test_normal_login_discards_admin_capability(self):
        self.authenticate(self.admin.login, '')
        self._switch(self.worker)
        result = self._rpc('/web/session/authenticate', {
            'db': self.env.cr.dbname, 'login': self.second.login, 'password': self.second.login,
        })
        self.assertNotIn('error', result)
        self.assertEqual(result['result']['uid'], self.second.id)
        self.assertNotIn(GRANT_KEY, self._stored_session())
        self._assert_denied(self._rpc('/switch_users/accounts'))

    def test_private_service_not_callable_by_rpc(self):
        self.authenticate(self.worker.login, '')
        result = self._rpc('/web/dataset/call_kw/switch.users.service/_switch', {
            'model': 'switch.users.service', 'method': '_switch', 'args': [], 'kwargs': {},
        })
        self.assertIn('error', result)

    def test_logout_discards_admin_capability(self):
        self.authenticate(self.admin.login, '')
        self._switch(self.worker)
        self.url_open('/web/session/logout', allow_redirects=False)
        session = self._stored_session()
        self.assertFalse(session.uid)
        self.assertNotIn(GRANT_KEY, session)

    def test_direct_account_outside_roles_can_switch_and_restore(self):
        values = {'name': 'محمد الجزار', 'user_id': self.unlinked.id,
                  'company_id': self.admin.company_id.id}
        if 'furniture_mrp_role' in self.Employee._fields:
            values['furniture_mrp_role'] = 'storekeeper'
        self.Employee.create(values)
        self.env['ir.config_parameter'].sudo().set_param(
            DIRECT_USERS_PARAM, json.dumps([self.unlinked.id, self.unlinked.id]))
        self.authenticate(self.admin.login, '')
        accounts = self._accounts()
        self.assertEqual([row['user_id'] for row in accounts['direct_accounts']], [self.unlinked.id])
        self.assertEqual(accounts['direct_accounts'][0]['name'], 'محمد الجزار')
        self.assertNotIn(self.unlinked.id, [row['user_id'] for row in accounts['workers'] + accounts['supervisors']])
        self.assertTrue(self._switch(self.unlinked)['changed'])
        self.assertEqual(self._stored_session().uid, self.unlinked.id)
        self.assertTrue(self._accounts()['direct_accounts'][0]['current'])
        result = self._rpc('/switch_users/restore', {'csrf_token': self._accounts()['csrf_token']})
        self.assertNotIn('error', result)
        self.assertEqual(self._stored_session().uid, self.admin.id)

    def test_direct_shortcut_does_not_bypass_target_checks(self):
        self.authenticate(self.admin.login, '')
        self.worker.active = False
        other_company = self.env['res.company'].create({'name': 'Direct shortcut excluded company'})
        self.second.company_ids = [Command.link(other_company.id)]
        self.env['ir.config_parameter'].sudo().set_param(DIRECT_USERS_PARAM, json.dumps([
            self.admin.id, self.portal.id, self.unlinked.id, self.worker.id, self.second.id,
        ]))
        self.assertEqual(self._accounts()['direct_accounts'], [])
        csrf = self._accounts()['csrf_token']
        for user in (self.admin, self.portal, self.unlinked, self.worker, self.second):
            self._assert_denied(self._rpc('/switch_users/switch', {'user_id': user.id, 'csrf_token': csrf}))
        self.assertEqual(self._stored_session().uid, self.admin.id)

    def test_direct_shortcut_invalid_configuration_fails_closed(self):
        service = self.env['switch.users.service']
        for value in ('not json', '{}', 'null', '[true]', '[1]', '["157"]', '[2, null]'):
            self.env['ir.config_parameter'].sudo().set_param(DIRECT_USERS_PARAM, value)
            self.assertEqual(service._direct_user_ids(), [])
