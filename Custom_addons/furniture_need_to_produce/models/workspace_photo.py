from odoo import fields, models


class FurnitureModelPhoto(models.Model):
    _inherit = 'furniture.product.model'

    image_1920 = fields.Image(string='صورة الطقم', max_width=1920, max_height=1920,
                              attachment=True)
