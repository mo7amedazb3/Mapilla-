from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request
from odoo.addons.web.controllers.home import Home
from odoo.addons.web.controllers.session import Session

from ..models.switch_users import GRANT_KEY


class SwitchUsersController(http.Controller):
    @http.route('/switch_users/accounts', type='json', auth='user', methods=['POST'])
    def accounts(self):
        result = request.env['switch.users.service']._list_accounts(request.session)
        result['csrf_token'] = request.csrf_token()
        return result

    @http.route('/switch_users/switch', type='json', auth='user', methods=['POST'])
    def switch(self, user_id, csrf_token):
        if not request.validate_csrf(csrf_token):
            raise AccessDenied('Invalid CSRF token.')
        return request.env['switch.users.service']._switch(request, user_id=user_id)

    @http.route('/switch_users/restore', type='json', auth='user', methods=['POST'])
    def restore(self, csrf_token):
        if not request.validate_csrf(csrf_token):
            raise AccessDenied('Invalid CSRF token.')
        return request.env['switch.users.service']._switch(request, restore=True)


class SwitchUsersSession(Session):
    @http.route()
    def authenticate(self, *args, **kwargs):
        request.session.pop(GRANT_KEY, None)
        return super().authenticate(*args, **kwargs)


class SwitchUsersHome(Home):
    @http.route()
    def web_login(self, *args, **kwargs):
        if request.httprequest.method == 'POST':
            request.session.pop(GRANT_KEY, None)
        return super().web_login(*args, **kwargs)
