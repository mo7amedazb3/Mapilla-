# -*- coding: utf-8 -*-

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    address_model = env['sale.destination.address'].with_context(active_test=False)

    for model_name in ('sale.order', 'account.move'):
        records = env[model_name].with_context(active_test=False).search([
            ('destination_address', '!=', False),
            ('destination_address_id', '=', False),
        ])
        for record in records:
            address_name = (record.destination_address or '').strip()
            if not address_name:
                continue
            address = address_model.search([
                ('company_id', '=', record.company_id.id),
                ('name', '=ilike', address_name),
            ], limit=1)
            if not address:
                address = address_model.create({
                    'name': address_name,
                    'company_id': record.company_id.id,
                })
            record.write({
                'destination_address_id': address.id,
                'destination_address': record.destination_address,
            })
