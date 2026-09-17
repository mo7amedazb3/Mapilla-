from odoo import models, fields

class WhatsappLabel(models.Model):
    _name = 'whatsapp.label'
    _description = 'WhatsApp Chat Label'
    _order = 'name'

    name = fields.Char(string='Label Name', required=True)
    color = fields.Char(string='Color Hex', default='#8e8e93', help="Choose a color for the label")
