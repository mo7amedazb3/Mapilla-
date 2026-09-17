# -*- coding: utf-8 -*-

from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def action_print_warehouse_receipt(self):
        """Print an incoming stock receipt from its form header on A4."""
        self.ensure_one()
        if self.picking_type_code != 'incoming':
            raise UserError(_('هذا التقرير مخصص لأذونات الاستلام فقط.'))
        return self.env.ref(
            'furniture_mrp.action_report_furniture_stock_receipt'
        ).report_action(self, config=False)
