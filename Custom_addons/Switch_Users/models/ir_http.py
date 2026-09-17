from odoo import models
from odoo.http import request, SessionExpiredException

from .switch_users import GRANT_KEY


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _authenticate(cls, endpoint):
        super()._authenticate(endpoint)
        if request.session.get(GRANT_KEY):
            service = request.env['switch.users.service']
            if not service._origin(request.session):
                request.session.logout(keep_db=True)
                # Authentication exceptions bypass normal dispatch/session-save.
                # Persist the logout now, deleting the previous SID as well.
                request._save_session()
                raise SessionExpiredException('The administrator testing session expired. Sign in again.')

    def session_info(self):
        result = super().session_info()
        result['switch_users'] = self.env['switch.users.service']._status(request.session)
        return result
