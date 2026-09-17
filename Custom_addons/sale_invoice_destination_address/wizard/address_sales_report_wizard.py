# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AddressSalesReportWizard(models.TransientModel):
    _name = 'sale.destination.address.report.wizard'
    _description = 'Address Sales Report Wizard'

    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    date_from = fields.Date(
        string='From',
        required=True,
        default=lambda self: fields.Date.start_of(
            fields.Date.context_today(self), 'month'
        ),
    )
    date_to = fields.Date(
        string='To',
        required=True,
        default=fields.Date.context_today,
    )

    @api.constrains('date_from', 'date_to')
    def _check_date_range(self):
        for wizard in self:
            if wizard.date_from and wizard.date_to and wizard.date_from > wizard.date_to:
                raise ValidationError(_('From date must not be after To date.'))

    def _get_address_sales_data(self):
        self.ensure_one()
        move_groups = self.env['account.move']._read_group(
            domain=[
                ('state', '=', 'posted'),
                ('move_type', '=', 'out_invoice'),
                ('invoice_date', '>=', self.date_from),
                ('invoice_date', '<=', self.date_to),
                ('company_id', '=', self.company_id.id),
            ],
            groupby=['destination_address_id'],
            aggregates=[
                'amount_untaxed_signed:sum',
                '__count',
            ],
        )

        stats_by_address = {}
        for address, untaxed, move_count in move_groups:
            address_id = address.id if address else False
            stats_by_address[address_id] = {
                'invoice_count': 0,
                'untaxed_sales': 0.0,
            }
            stats_by_address[address_id]['invoice_count'] = move_count
            stats_by_address[address_id]['untaxed_sales'] = untaxed

        address_model = self.env['sale.destination.address']
        active_addresses = address_model.search([
            ('company_id', '=', self.company_id.id),
        ])
        archived_sales_ids = [
            address_id
            for address_id in stats_by_address
            if address_id and address_id not in active_addresses.ids
        ]
        archived_addresses = address_model.with_context(active_test=False).search([
            ('id', 'in', archived_sales_ids),
            ('company_id', '=', self.company_id.id),
        ])

        rows = []
        for address in active_addresses | archived_addresses:
            stats = stats_by_address.get(address.id, {})
            rows.append({
                'address_id': address.id,
                'name': address.name,
                'active': address.active,
                'is_unassigned': False,
                'invoice_count': stats.get('invoice_count', 0),
                'untaxed_sales': stats.get('untaxed_sales', 0.0),
            })

        unassigned_stats = stats_by_address.get(False)
        if unassigned_stats:
            rows.append({
                'address_id': False,
                'name': _('No Address'),
                'active': True,
                'is_unassigned': True,
                **unassigned_stats,
            })

        total_untaxed_sales = sum(row['untaxed_sales'] for row in rows)
        total_invoice_count = sum(row['invoice_count'] for row in rows)

        for row in rows:
            row['percentage'] = (
                row['untaxed_sales'] / total_untaxed_sales * 100.0
                if total_untaxed_sales
                else 0.0
            )
            row['bar_percentage'] = max(0.0, min(row['percentage'], 100.0))

        rows.sort(key=lambda row: (
            row['is_unassigned'],
            -row['untaxed_sales'],
            row['name'].casefold(),
        ))
        return {
            'rows': rows,
            'configured_address_count': len(active_addresses),
            'total_invoice_count': total_invoice_count,
            'total_untaxed_sales': total_untaxed_sales,
            'total_percentage': 100.0 if total_untaxed_sales else 0.0,
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'sale_invoice_destination_address.action_report_address_sales'
        ).report_action(self, config=False)
