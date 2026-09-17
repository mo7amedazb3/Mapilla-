from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    is_extra_material = fields.Boolean(string="Extra Material", copy=False)
    extra_material_reason = fields.Text(string="Extra Material Reason", copy=False)
    extra_material_user_id = fields.Many2one(
        "res.users", string="Requested By", copy=False
    )
