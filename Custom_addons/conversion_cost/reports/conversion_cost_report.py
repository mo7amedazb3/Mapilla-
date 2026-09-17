from odoo import models, api

class ConversionCostReport(models.AbstractModel):
    _name = 'report.conversion_cost.report_conversion_cost_template'
    _description = 'Conversion Cost Report Logic'

    @api.model
    def _get_report_values(self, docids, data=None):
        form = data.get('form', {})
        date_from = form.get('date_from')
        date_to = form.get('date_to')
        labour_account_id = form.get('labour_account_id')
        overhead_account_id = form.get('overhead_account_id')

        # Get Labour Account Object
        labour_account = self.env['account.analytic.account'].browse(labour_account_id[0]) if labour_account_id else None
        # Get Overhead Account Object
        overhead_account = self.env['account.analytic.account'].browse(overhead_account_id[0]) if overhead_account_id else None

        # Calculate Balances manually to bypass ORM cache issues
        labour_total = 0.0
        if labour_account:
            plan_col = labour_account.plan_id._column_name()
            domain = [('date', '>=', date_from), ('date', '<=', date_to), (plan_col, '=', labour_account.id)]
            lines = self.env['account.analytic.line'].search(domain)
            labour_total = sum(lines.mapped('amount'))

        overhead_total = 0.0
        if overhead_account:
            plan_col = overhead_account.plan_id._column_name()
            domain = [('date', '>=', date_from), ('date', '<=', date_to), (plan_col, '=', overhead_account.id)]
            lines = self.env['account.analytic.line'].search(domain)
            overhead_total = sum(lines.mapped('amount'))

        # Conversion Cost
        conversion_cost = labour_total + overhead_total

        return {
            'doc_ids': docids,
            'doc_model': 'conversion.cost.wizard',
            'date_from': date_from,
            'date_to': date_to,
            'labour_name': labour_account_id[1] if labour_account_id else 'Labour',
            'overhead_name': overhead_account_id[1] if overhead_account_id else 'Overhead',
            'labour_total': labour_total,
            'overhead_total': overhead_total,
            'conversion_cost': conversion_cost,
            'company': self.env.company,
        }
