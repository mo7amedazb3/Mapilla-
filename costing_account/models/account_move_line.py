# -*- coding: utf-8 -*-

from odoo import api, fields, models, _

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    spa_bom_id = fields.Many2one(
        'mrp.bom',
        string="Consumed BoM",
        readonly=True,
        copy=False,
    )
    spa_material_cost = fields.Monetary(
        string="Material Cost Snapshot",
        currency_field='currency_id',
        readonly=True,
        copy=False,
    )
    spa_material_consumed = fields.Boolean(
        string="BoM Materials Consumed",
        readonly=True,
        copy=False,
    )
    spa_consumption_move_ids = fields.One2many(
        'stock.move',
        'spa_invoice_line_id',
        string="Material Consumption Moves",
        readonly=True,
    )
