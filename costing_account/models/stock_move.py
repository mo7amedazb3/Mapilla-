# -*- coding: utf-8 -*-

from odoo import fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    spa_invoice_line_id = fields.Many2one(
        'account.move.line',
        string="SPA Invoice Line",
        index=True,
        copy=False,
        ondelete='set null',
    )
    spa_consumption_reversal = fields.Boolean(
        string="SPA Consumption Reversal",
        copy=False,
    )
    spa_consumption_reversed = fields.Boolean(
        string="SPA Consumption Reversed",
        copy=False,
    )
