from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    whatsapp_default_instance_id = fields.Many2one(
        'whatsapp.instance',
        string='Default WhatsApp Instance',
        config_parameter='whatsapp_integration.default_instance_id',
    )
    whatsapp_notification_window = fields.Integer(
        string='Notification Window (seconds)',
        config_parameter='whatsapp_integration.notification_window',
        default=60,
    )
