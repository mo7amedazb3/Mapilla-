import hmac
import json
import logging
import secrets
import time

from odoo import _, models
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)
GRANT_KEY = 'Switch_Users_grant'
GRANT_LIFETIME = 4 * 60 * 60
DIRECT_USERS_PARAM = 'Switch_Users.direct_user_ids'


class SwitchUsersService(models.AbstractModel):
    _name = 'switch.users.service'
    _description = 'Administrator testing session switcher'

    def _is_admin(self, user):
        return bool(user and user.active and not user.share and user._is_system())

    def _origin(self, session):
        """Authority comes exclusively from the authenticated server session.

        The grant never goes to the browser. Its credential fingerprint revokes
        it after the origin admin's password/MFA/login/active status changes.
        Binding to both DB and current UID prevents reuse after another login.
        """
        if not session.uid or session.db != self.env.cr.dbname:
            return self.env['res.users']
        grant = session.get(GRANT_KEY)
        if grant:
            if not isinstance(grant, dict):
                return self.env['res.users']
            if (grant.get('db') != session.db
                    or grant.get('target_uid') != session.uid
                    or not isinstance(grant.get('expires'), (int, float))
                    or grant['expires'] <= time.time()
                    or type(grant.get('origin_uid')) is not int
                    or not isinstance(grant.get('nonce'), str)
                    or not isinstance(grant.get('proof'), str)):
                return self.env['res.users']
            origin = self.env['res.users'].sudo().browse(grant['origin_uid']).exists()
            if not self._is_admin(origin):
                return self.env['res.users']
            proof = origin._compute_session_token(grant['nonce'])
            return origin if proof and hmac.compare_digest(proof, grant['proof']) else self.env['res.users']
        current = self.env['res.users'].sudo().browse(session.uid).exists()
        return current if self._is_admin(current) else self.env['res.users']

    def _eligible_target(self, target, origin):
        return bool(
            target and target.active and not target.share
            and target.id != 1 and not target._is_system()
            and target.company_ids
            and set(target.company_ids.ids) <= set(origin.company_ids.ids)
        )

    def _employees(self, origin):
        Employee = self.env['hr.employee'].sudo()
        domain = [
            ('active', '=', True), ('company_id', 'in', origin.company_ids.ids),
            ('user_id', '!=', False), ('user_id.active', '=', True),
            ('user_id.share', '=', False),
        ]
        if 'furniture_mrp_role' in Employee._fields:
            domain.extend([
                '|', ('furniture_mrp_role', 'in', ['supervisor', 'worker']),
                ('user_id', 'in', self._direct_user_ids()),
            ])
        return Employee.search(domain, order='name,id').filtered(
            lambda employee: self._eligible_target(employee.user_id, origin)
        )

    def _direct_user_ids(self):
        """Explicit per-database shortcuts, never authorization by display name.

        Configuring a shortcut does not bypass active employee/account, company,
        portal, or system-administrator checks in _employees/_eligible_target.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(DIRECT_USERS_PARAM, '[]')
        try:
            ids = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(ids, list) or any(type(uid) is not int or uid <= 1 for uid in ids):
            return []
        return sorted(set(ids))

    def _status(self, session):
        origin = self._origin(session)
        return {
            'enabled': bool(origin),
            'switched': bool(origin and session.get(GRANT_KEY)),
            'origin_name': origin.name if origin else False,
            'current_name': self.env.user.name if origin else False,
            'db': self.env.cr.dbname,
        }

    def _list_accounts(self, session):
        origin = self._origin(session)
        if not origin:
            raise AccessDenied(_('Account switching is restricted to an administrator testing session.'))
        categories = {'supervisors': [], 'workers': [], 'direct_accounts': []}
        direct_ids = set(self._direct_user_ids())
        seen_direct = set()
        for employee in self._employees(origin):
            is_supervisor = ('furniture_mrp_role' in employee._fields
                             and employee.furniture_mrp_role == 'supervisor')
            stages = (employee.furniture_mrp_supervisor_stage_ids.mapped('name')
                      if 'furniture_mrp_supervisor_stage_ids' in employee._fields else [])
            category = 'supervisors' if is_supervisor else 'workers'
            if employee.user_id.id in direct_ids:
                if employee.user_id.id in seen_direct:
                    continue
                seen_direct.add(employee.user_id.id)
                category = 'direct_accounts'
            categories[category].append({
                'id': employee.id,
                'user_id': employee.user_id.id,
                'name': employee.name,
                'details': ' / '.join(stages) if is_supervisor else (employee.department_id.name or ''),
                'current': employee.user_id.id == session.uid,
            })
        return dict(self._status(session), **categories)

    def _switch(self, req, user_id=None, restore=False):
        origin = self._origin(req.session)
        if not origin:
            raise AccessDenied(_('Start account switching from your administrator account.'))
        if restore:
            if not req.session.get(GRANT_KEY):
                raise AccessDenied(_('No testing session to restore.'))
            target = origin
        else:
            if type(user_id) is not int:
                raise AccessDenied(_('Invalid account.'))
            employee = self._employees(origin).filtered(lambda row: row.user_id.id == user_id)[:1]
            if not employee:
                raise AccessDenied(_('This employee account is not available for switching.'))
            target = employee.user_id
        if target.id == req.session.uid:
            return {'redirect': '/odoo', 'changed': False}

        grant = req.session.get(GRANT_KEY)
        if not grant:
            nonce = secrets.token_hex(32)
            grant = {
                'db': req.session.db, 'origin_uid': origin.id,
                'nonce': nonce, 'proof': origin._compute_session_token(nonce),
                'expires': time.time() + GRANT_LIFETIME,
            }
        # Discard all old companies, MFA partial-login state, elevated-mode flags,
        # and arbitrary session context. Use Odoo's normal finalization and SID
        # rotation, but only after the explicit server-side admin authorization.
        previous_uid = req.session.uid
        req.session.logout(keep_db=True)
        req.session.pre_uid = target.id
        req.session.pre_login = target.login
        req.session.finalize(self.env(user=target.id, su=False))
        if not restore:
            req.session[GRANT_KEY] = dict(grant, target_uid=target.id)
        req.update_env(user=target.id, context=dict(req.session.context), su=False)
        # cids is also reset by web's _post_logout. Explicitly retain that behavior
        # if another module overrides the logout hook.
        req.future_response.set_cookie('cids', '', max_age=0)
        _logger.info('Switch_Users db=%s origin_uid=%s from_uid=%s target_uid=%s restore=%s',
                     req.db, origin.id, previous_uid, target.id, restore)
        return {'redirect': '/odoo', 'changed': True}
