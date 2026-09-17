from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    is_furniture_set = fields.Boolean(
        string="Furniture Set Recipe",
        help="Use this recipe as a container for one-piece sofa and armchair recipes.",
    )
    sofa_bom_id = fields.Many2one(
        "mrp.bom",
        string="One Large Sofa Recipe",
        check_company=True,
        domain="[('id', '!=', id), ('is_furniture_set', '=', False)]",
    )
    small_sofa_bom_id = fields.Many2one(
        "mrp.bom",
        string="One Small Sofa Recipe",
        check_company=True,
        domain="[('id', '!=', id), ('is_furniture_set', '=', False)]",
    )
    armchair_bom_id = fields.Many2one(
        "mrp.bom",
        string="One Armchair Recipe",
        check_company=True,
        domain="[('id', '!=', id), ('is_furniture_set', '=', False)]",
    )
    default_sofa_qty = fields.Float(string="Default Large Sofas", default=1.0)
    default_small_sofa_qty = fields.Float(string="Default Small Sofas", default=1.0)
    default_armchair_qty = fields.Float(string="Default Armchairs", default=2.0)

    reference_width = fields.Float(string="Reference Width (cm)")
    reference_depth = fields.Float(string="Reference Depth (cm)")
    reference_height = fields.Float(string="Reference Height (cm)")
    set_sofa_width = fields.Float(string="Sofa Width (cm)")
    set_sofa_depth = fields.Float(string="Sofa Depth (cm)")
    set_sofa_height = fields.Float(string="Sofa Height (cm)")
    set_small_sofa_width = fields.Float(string="Small Sofa Width (cm)")
    set_small_sofa_depth = fields.Float(string="Small Sofa Depth (cm)")
    set_small_sofa_height = fields.Float(string="Small Sofa Height (cm)")
    set_armchair_width = fields.Float(string="Armchair Width (cm)")
    set_armchair_depth = fields.Float(string="Armchair Depth (cm)")
    set_armchair_height = fields.Float(string="Armchair Height (cm)")

    @api.onchange("sofa_bom_id", "small_sofa_bom_id", "armchair_bom_id")
    def _onchange_set_piece_dimensions(self):
        for bom in self:
            if bom.sofa_bom_id:
                bom.set_sofa_width = bom.sofa_bom_id.reference_width
                bom.set_sofa_depth = bom.sofa_bom_id.reference_depth
                bom.set_sofa_height = bom.sofa_bom_id.reference_height
            if bom.small_sofa_bom_id:
                bom.set_small_sofa_width = bom.small_sofa_bom_id.reference_width
                bom.set_small_sofa_depth = bom.small_sofa_bom_id.reference_depth
                bom.set_small_sofa_height = bom.small_sofa_bom_id.reference_height
            if bom.armchair_bom_id:
                bom.set_armchair_width = bom.armchair_bom_id.reference_width
                bom.set_armchair_depth = bom.armchair_bom_id.reference_depth
                bom.set_armchair_height = bom.armchair_bom_id.reference_height

    @api.constrains(
        "is_furniture_set",
        "sofa_bom_id",
        "small_sofa_bom_id",
        "armchair_bom_id",
        "default_sofa_qty",
        "default_small_sofa_qty",
        "default_armchair_qty",
    )
    def _check_furniture_set_configuration(self):
        for bom in self:
            if not bom.is_furniture_set:
                continue
            if (
                not bom.sofa_bom_id
                and not bom.small_sofa_bom_id
                and not bom.armchair_bom_id
            ):
                raise ValidationError(
                    "A furniture set recipe must contain at least one piece recipe."
                )
            if (
                bom.default_sofa_qty < 0
                or bom.default_small_sofa_qty < 0
                or bom.default_armchair_qty < 0
            ):
                raise ValidationError("Default piece quantities cannot be negative.")
