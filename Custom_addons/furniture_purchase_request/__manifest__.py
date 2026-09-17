# -*- coding: utf-8 -*-
{
    'name': 'Furniture Warehouse Purchase Requests',
    'version': '18.0.1.1.10',
    'summary': 'Warehouse requests that Purchasing converts into supplier RFQs',
    'category': 'Inventory/Purchase',
    'author': 'Custom Development',
    'license': 'LGPL-3',
    'depends': [
        'furniture_mrp',
        'purchase_stock',
        'partner_autocomplete',
        'whatsapp_integration',
    ],
    'data': [
        'security/purchase_request_security.xml',
        'security/ir.model.access.csv',
        'data/purchase_request_sequence.xml',
        'views/purchase_request_views.xml',
        'views/purchase_order_views.xml',
        'views/purchase_design_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'furniture_purchase_request/static/src/js/partner_many2one_avatar.js',
            'furniture_purchase_request/static/src/xml/partner_many2one_avatar.xml',
            'furniture_purchase_request/static/src/scss/purchase_design.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
