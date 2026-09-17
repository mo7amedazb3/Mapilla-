from odoo import fields, models, _
from odoo.exceptions import UserError


class MrpExtraMaterialWizard(models.TransientModel):
    _name = "mrp.extra.material.wizard"
    _description = "Extra Material Request"

    production_id = fields.Many2one(
        "mrp.production", string="Manufacturing Order", required=True, readonly=True
    )
    product_id = fields.Many2one(
        "product.product",
        string="Material",
        required=True,
        domain="[('type', '=', 'consu')]",
    )
    product_uom_category_id = fields.Many2one(
        related="product_id.uom_id.category_id"
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        required=True,
        domain="[('category_id', '=', product_uom_category_id)]",
    )
    quantity = fields.Float(string="Extra Quantity", required=True)
    reason = fields.Text(string="Reason", required=True)

    def action_add_extra_material(self):
        self.ensure_one()
        production = self.production_id
        if production.create_uid != self.env.user:
            raise UserError(
                _("Only the user who created this manufacturing order can request extra materials.")
            )
        if self.quantity <= 0:
            raise UserError(_("Extra quantity must be greater than zero."))
        quantity = self.product_uom_id._compute_quantity(
            self.quantity, self.product_id.uom_id
        )
        values = production._get_move_raw_values(
            self.product_id,
            quantity,
            self.product_id.uom_id,
        )
        values.update(
            {
                "is_extra_material": True,
                "extra_material_reason": self.reason,
                "extra_material_user_id": self.env.user.id,
            }
        )
        move = self.env["stock.move"].create(values)
        if production.state not in ("draft", "cancel", "done"):
            move._action_confirm(merge=False)
            production.action_assign()
        production.message_post(
            body=_(
                "Extra material requested: %(product)s — %(qty)s %(uom)s. Reason: %(reason)s",
                product=self.product_id.display_name,
                qty=self.quantity,
                uom=self.product_uom_id.name,
                reason=self.reason,
            )
        )
        return {"type": "ir.actions.act_window_close"}
