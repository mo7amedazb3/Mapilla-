from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Purge any legacy order-piece image already left on a closed order."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    closed_orders = env['furniture.mrp.production'].with_context(
        active_test=False,
    ).search([
        ('state', 'in', ('done', 'cancelled')),
    ])
    closed_orders._clear_order_piece_images()
