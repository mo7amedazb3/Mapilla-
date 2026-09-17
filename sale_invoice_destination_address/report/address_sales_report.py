# -*- coding: utf-8 -*-

from odoo import api, models
from odoo.tools import format_date


class AddressSalesReport(models.AbstractModel):
    _name = 'report.sale_invoice_destination_address.address_sales'
    _description = 'Address Sales PDF Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env['sale.destination.address.report.wizard'].browse(docids)
        wizard.ensure_one()
        report_data = wizard._get_address_sales_data()
        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'company': wizard.company_id,
            'currency': wizard.company_id.currency_id,
            'date_from': format_date(self.env, wizard.date_from),
            'date_to': format_date(self.env, wizard.date_to),
            **report_data,
        }
