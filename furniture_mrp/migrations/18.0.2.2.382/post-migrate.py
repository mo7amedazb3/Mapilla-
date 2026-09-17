from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Open the MRP II supervisor dashboard immediately after login."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['hr.employee']._sync_all_furniture_mrp_supervisor_groups()
