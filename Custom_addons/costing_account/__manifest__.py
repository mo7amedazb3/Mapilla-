# -*- coding: utf-8 -*-
{
    'name': "Costing Account",
    'summary': "Service Costing and Material Consumption",
    'description': """
        Costing Account Management
        ==========================
        This module provides an automated system for calculating service costs, 
        material consumption, and allocating conversion costs per department based on posted invoices.
    """,
    'author': "Your Company",
    'website': "https://www.yourcompany.com",
    'category': 'Accounting',
    'version': '18.0.1.0.0',
    'depends': ['base', 'product', 'sale', 'account', 'mrp'],
    'data': [
        'security/ir.model.access.csv',
        'views/costing_views.xml',
        'views/product_template_views.xml',
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
