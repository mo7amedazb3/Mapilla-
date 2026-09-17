from collections import defaultdict

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    is_furniture_set = fields.Boolean(related="bom_id.is_furniture_set", store=True)
    sofa_qty = fields.Float(string="Large Sofas", default=0.0, copy=True)
    small_sofa_qty = fields.Float(string="Small Sofas", default=0.0, copy=True)
    armchair_qty = fields.Float(string="Armchairs", default=0.0, copy=True)

    sofa_width = fields.Float(string="Large Sofa Width (cm)")
    sofa_depth = fields.Float(string="Large Sofa Depth (cm)")
    sofa_height = fields.Float(string="Large Sofa Height (cm)")
    small_sofa_width = fields.Float(string="Small Sofa Width (cm)")
    small_sofa_depth = fields.Float(string="Small Sofa Depth (cm)")
    small_sofa_height = fields.Float(string="Small Sofa Height (cm)")
    armchair_width = fields.Float(string="Armchair Width (cm)")
    armchair_depth = fields.Float(string="Armchair Depth (cm)")
    armchair_height = fields.Float(string="Armchair Height (cm)")

    extra_material_count = fields.Integer(compute="_compute_extra_material_count")
    can_request_extra_material = fields.Boolean(
        compute="_compute_can_request_extra_material"
    )

    @api.depends("move_raw_ids.is_extra_material")
    def _compute_extra_material_count(self):
        for production in self:
            production.extra_material_count = len(
                production.move_raw_ids.filtered("is_extra_material")
            )

    @api.depends("create_uid", "state")
    def _compute_can_request_extra_material(self):
        for production in self:
            production.can_request_extra_material = (
                production.create_uid == self.env.user
                and production.state not in ("done", "cancel")
            )

    @api.onchange("bom_id")
    def _onchange_furniture_set_bom(self):
        for production in self:
            if not production.bom_id.is_furniture_set:
                continue
            production.sofa_qty = production.bom_id.default_sofa_qty
            production.small_sofa_qty = production.bom_id.default_small_sofa_qty
            production.armchair_qty = production.bom_id.default_armchair_qty
            production.sofa_width = (
                production.bom_id.set_sofa_width
                or production.bom_id.sofa_bom_id.reference_width
            )
            production.sofa_depth = (
                production.bom_id.set_sofa_depth
                or production.bom_id.sofa_bom_id.reference_depth
            )
            production.sofa_height = (
                production.bom_id.set_sofa_height
                or production.bom_id.sofa_bom_id.reference_height
            )
            production.small_sofa_width = (
                production.bom_id.set_small_sofa_width
                or production.bom_id.small_sofa_bom_id.reference_width
            )
            production.small_sofa_depth = (
                production.bom_id.set_small_sofa_depth
                or production.bom_id.small_sofa_bom_id.reference_depth
            )
            production.small_sofa_height = (
                production.bom_id.set_small_sofa_height
                or production.bom_id.small_sofa_bom_id.reference_height
            )
            production.armchair_width = (
                production.bom_id.set_armchair_width
                or production.bom_id.armchair_bom_id.reference_width
            )
            production.armchair_depth = (
                production.bom_id.set_armchair_depth
                or production.bom_id.armchair_bom_id.reference_depth
            )
            production.armchair_height = (
                production.bom_id.set_armchair_height
                or production.bom_id.armchair_bom_id.reference_height
            )

    @api.constrains("sofa_qty", "small_sofa_qty", "armchair_qty")
    def _check_piece_quantities(self):
        for production in self:
            if (
                production.sofa_qty < 0
                or production.small_sofa_qty < 0
                or production.armchair_qty < 0
            ):
                raise ValidationError(_("Piece quantities cannot be negative."))

    def _get_furniture_recipe_move_values(self, child_bom, piece_qty):
        self.ensure_one()
        if not child_bom or not piece_qty:
            return []
        product = child_bom.product_id or child_bom.product_tmpl_id.product_variant_id
        factor = piece_qty / child_bom.product_qty
        _boms, lines = child_bom.explode(
            product,
            factor,
            picking_type=child_bom.picking_type_id,
        )
        values = []
        for bom_line, line_data in lines:
            if (
                (bom_line.child_bom_id and bom_line.child_bom_id.type == "phantom")
                or bom_line.product_id.type != "consu"
            ):
                continue
            operation = (
                bom_line.operation_id.id
                or line_data["parent_line"]
                and line_data["parent_line"].operation_id.id
            )
            values.append(
                self._get_move_raw_values(
                    bom_line.product_id,
                    line_data["qty"],
                    bom_line.product_uom_id,
                    operation,
                    bom_line,
                )
            )
        return values

    def action_recalculate_furniture_components(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(
                _("Piece quantities can only rebuild components while the order is in draft.")
            )
        if not self.bom_id.is_furniture_set:
            raise UserError(_("The selected recipe is not a furniture set recipe."))
        if not self.sofa_qty and not self.small_sofa_qty and not self.armchair_qty:
            raise UserError(_("Enter at least one furniture piece."))

        values = []
        values += self._get_furniture_recipe_move_values(
            self.bom_id.sofa_bom_id, self.sofa_qty
        )
        values += self._get_furniture_recipe_move_values(
            self.bom_id.small_sofa_bom_id, self.small_sofa_qty
        )
        values += self._get_furniture_recipe_move_values(
            self.bom_id.armchair_bom_id, self.armchair_qty
        )

        grouped = defaultdict(lambda: {"qty": 0.0, "value": None})
        for value in values:
            key = (
                value["product_id"],
                value["product_uom"],
                value.get("operation_id") or False,
                value.get("location_id"),
                value.get("location_dest_id"),
            )
            grouped[key]["qty"] += value["product_uom_qty"]
            grouped[key]["value"] = value

        commands = [Command.clear()]
        for item in grouped.values():
            value = dict(item["value"])
            value["product_uom_qty"] = item["qty"]
            value["bom_line_id"] = False
            commands.append(Command.create(value))
        self.move_raw_ids = commands
        self.message_post(
            body=_(
                "Components recalculated for %(large)s large sofa(s), %(small)s small sofa(s), and %(armchairs)s armchair(s).",
                large=self.sofa_qty,
                small=self.small_sofa_qty,
                armchairs=self.armchair_qty,
            )
        )
        return True

    def action_open_extra_material_wizard(self):
        self.ensure_one()
        if self.create_uid != self.env.user:
            raise UserError(
                _("Only the user who created this manufacturing order can request extra materials.")
            )
        if self.state in ("done", "cancel"):
            raise UserError(_("Extra materials cannot be added to a closed order."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Extra Material Request"),
            "res_model": "mrp.extra.material.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_production_id": self.id},
        }

    def action_view_extra_materials(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Extra Materials"),
            "res_model": "stock.move",
            "view_mode": "list,form",
            "domain": [
                ("raw_material_production_id", "=", self.id),
                ("is_extra_material", "=", True),
            ],
            "context": {"create": False},
        }
