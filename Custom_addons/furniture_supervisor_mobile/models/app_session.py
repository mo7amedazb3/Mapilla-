import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessDenied
from odoo.tools import consteq


class SupervisorAppSession(models.Model):
    _name = 'furniture.supervisor.app.session'
    _description = 'Independent supervisor app session'

    user_id = fields.Many2one('res.users', required=True, ondelete='cascade', index=True)
    token_hash = fields.Char(required=True, index=True)
    fingerprint = fields.Char(required=True)
    expires_at = fields.Datetime(required=True, index=True)
    wizard_grants = fields.Json(default=dict)
    command_results = fields.Json(default=dict)
    _sql_constraints = [('token_unique', 'unique(token_hash)', 'Duplicate session')]

    @api.model
    def _issue(self, user):
        profile = user.with_user(user).env['furniture.mrp.production']._stage_dashboard_access_profile()
        if not profile['allowed_stage_codes'] or user.share or not user.active:
            raise AccessDenied('حسابك غير مربوط بمرحلة إنتاج نشطة.')
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        session = self.sudo().create({
            'user_id': user.id, 'token_hash': digest,
            'fingerprint': user._compute_session_token(digest),
            'expires_at': fields.Datetime.now() + timedelta(days=7),
        })
        # Opportunistic cleanup, restricted to expired app sessions.
        self.sudo().search([('expires_at', '<', fields.Datetime.now())], limit=100).unlink()
        return session, token

    @api.model
    def _authenticate_token(self, token):
        if not token or not isinstance(token, str) or len(token) > 200:
            raise AccessDenied('انتهت الجلسة. سجّل الدخول مرة أخرى.')
        digest = hashlib.sha256(token.encode()).hexdigest()
        session = self.sudo().search([('token_hash', '=', digest)], limit=1)
        if not session or session.expires_at <= fields.Datetime.now() or not session.user_id.active:
            raise AccessDenied('انتهت الجلسة. سجّل الدخول مرة أخرى.')
        expected = session.user_id._compute_session_token(digest)
        if not expected or not consteq(expected, session.fingerprint):
            raise AccessDenied('تغيّرت بيانات الحساب. سجّل الدخول مرة أخرى.')
        return session
