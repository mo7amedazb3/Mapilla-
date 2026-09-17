# -*- coding: utf-8 -*-

from odoo import fields, models


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    product_tmpl_id = fields.Many2one(
        domain="[('type', 'in', ['consu', 'service'])]",
    )
    product_id = fields.Many2one(
        domain="['&', ('product_tmpl_id', '=', product_tmpl_id), ('type', 'in', ['consu', 'service'])]",
    )
