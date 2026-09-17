from odoo import api, models


class SupervisorMobile(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def get_supervisor_mobile_requests(self, offset=0):
        """Read only, same stage/company rules as the operational screens."""
        profile = self._stage_dashboard_access_profile()
        offset = max(0, int(offset))
        domain = [
            ('company_id', 'in', self.env.companies.ids),
            ('stage_code', 'in', profile['allowed_stage_codes']),
        ]
        requests = self.env['furniture.mrp.store.request'].search(
            domain, order='requested_at desc, id desc', limit=31, offset=offset,
        )
        # Deliberately omit customer, costs and technical execution payloads.
        return {
            'has_more': len(requests) > 30,
            'rows': requests[:30].read([
                'name', 'stage_code', 'state', 'receipt_state', 'requested_at',
            ]),
        }
