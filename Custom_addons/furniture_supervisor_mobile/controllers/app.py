import json
from urllib.parse import urlparse

from odoo import api, http, SUPERUSER_ID
from odoo.exceptions import AccessDenied, AccessError, UserError
from odoo.http import request
from odoo.modules.registry import Registry
from odoo.tools import file_open

COOKIE = 'mapilla_supervisor_session'


def secure_app_cookie():
    # TLS may terminate ahead of Odoo's immediate reverse proxy. Production
    # app cookies must remain Secure even when that hop reports plain HTTP.
    host = urlparse('http://' + request.httprequest.host).hostname
    return host not in ('localhost', '127.0.0.1', '::1')


class IndependentSupervisorApp(http.Controller):
    @http.route('/supervisor-app', type='http', auth='none', methods=['GET'])
    def app(self, **kw):
        with file_open('furniture_supervisor_mobile/static/app/index.html', 'rb') as stream:
            html = stream.read()
        return request.make_response(html, [
            ('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
            ('X-Frame-Options', 'DENY'), ('X-Content-Type-Options', 'nosniff'),
            ('Referrer-Policy', 'same-origin'),
        ])

    @http.route('/supervisor-app/api/<string:operation>', type='json', auth='none', methods=['POST'])
    def dispatch(self, operation, db=None, **payload):
        request.future_response.headers['Cache-Control'] = 'no-store'
        # Non-simple header + same-origin enforcement protects cookie-authenticated
        # mutations. No CORS wildcard, no generic model/method or context proxy.
        if request.httprequest.headers.get('X-Mapilla-App') != '1':
            raise AccessDenied()
        origin = request.httprequest.headers.get('Origin')
        if origin and urlparse(origin).netloc != request.httprequest.host:
            raise AccessDenied()
        if not db or not http.db_filter([db]) or db not in http.db_list():
            raise AccessDenied('تعذر الاتصال بالنظام.')
        with Registry(db).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            if operation == 'login':
                return self._login(env, db, payload)
            token = request.httprequest.headers.get('Authorization', '')
            token = token[7:] if token.startswith('Bearer ') else request.httprequest.cookies.get(COOKIE)
            session = env['furniture.supervisor.app.session']._authenticate_token(token)
            user = session.user_id
            context = dict(user.with_user(user).context_get(), allowed_company_ids=[user.company_id.id])
            app_env = api.Environment(cr, user.id, context)
            app_env['furniture.mrp.production']._stage_dashboard_access_profile()
            if operation == 'logout':
                session.unlink()
                request.future_response.set_cookie(COOKIE, '', max_age=0, expires=0,
                                                   path='/supervisor-app/api', httponly=True,
                                                   secure=secure_app_cookie(),
                                                   samesite='Strict')
                return {'ok': True}
            return app_env['furniture.mrp.production']._supervisor_app_dispatch(operation, payload, session)

    def _login(self, env, db, payload):
        login = str(payload.get('login', '')).strip()
        password = payload.get('password', '')
        if not login or not password or len(login) > 256 or len(password) > 4096:
            raise AccessDenied('اكتب اسم المستخدم وكلمة المرور.')
        # Authenticate against Odoo's actual credential policy, with rate limiting
        # and MFA. This temporary session is never installed as the ERP session.
        auth_session = http.root.session_store.new()
        info = auth_session.authenticate(db, {'login': login, 'password': password, 'type': 'password'})
        user = env['res.users'].browse(info['uid'])
        if not auth_session.uid:
            if user._mfa_url() != '/web/login/totp' or not payload.get('otp'):
                return {'mfa_required': True}
            try:
                with user._assert_can_auth(user=user.id):
                    user._totp_check(int(str(payload['otp']).replace(' ', '')))
            except (ValueError, AccessDenied):
                raise AccessDenied('كود التحقق غير صحيح.')
            auth_session.finalize(env)
        if user.share or not user.active:
            raise AccessDenied('هذا الحساب غير متاح للمشرفين.')
        session, token = env['furniture.supervisor.app.session']._issue(user)
        result = {'ok': True}
        if payload.get('native') is True:
            result['token'] = token
        else:
            request.future_response.set_cookie(
                COOKIE, token, max_age=7 * 86400, httponly=True,
                secure=secure_app_cookie(), samesite='Strict',
                path='/supervisor-app/api',
            )
        return result
