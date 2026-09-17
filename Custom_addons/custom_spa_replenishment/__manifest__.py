# -*- coding: utf-8 -*-
{
    'name': 'Single Warehouse Central Purchase Replenishment',
    'version': '18.0.1.0.1',
    'summary': 'Central purchase replenishment dashboard for one Stock warehouse',
    'category': 'Inventory/Purchase',
    'author': 'Custom Development',
    'license': 'LGPL-3',
    'depends': [
        'furniture_purchase_request',
    ],
    'data': [
        'security/replenishment_security.xml',
        'security/ir.model.access.csv',
        'data/replenishment_sequence.xml',
        'views/replenishment_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_spa_replenishment/static/src/js/central_purchase_dashboard.js',
            'custom_spa_replenishment/static/src/xml/central_purchase_dashboard.xml',
            'custom_spa_replenishment/static/src/css/central_purchase_dashboard.css',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
