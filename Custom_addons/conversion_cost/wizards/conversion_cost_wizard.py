from odoo import models, fields, api, _

class ConversionCostWizard(models.TransientModel):
    _name = 'conversion.cost.wizard'
    _description = 'Conversion Cost Report Wizard'

    date_from = fields.Date(string='Date From', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='Date To', required=True, default=fields.Date.context_today)
    
    labour_account_id = fields.Many2one(
        'account.analytic.account', 
        string='Labour Account', 
        required=True,
        default=lambda self: self.env['account.analytic.account'].search([('name', 'ilike', 'labour')], limit=1)
    )
    overhead_account_id = fields.Many2one(
        'account.analytic.account', 
        string='Overhead Account', 
        required=True,
        default=lambda self: self.env['account.analytic.account'].search([('name', 'ilike', 'overhead')], limit=1)
    )

    def action_print_report(self):
        data = {
            'form': self.read()[0],
        }
        return self.env.ref('conversion_cost.action_report_conversion_cost').report_action(self, data=data)
