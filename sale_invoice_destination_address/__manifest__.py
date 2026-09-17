# -*- coding: utf-8 -*-
{
    'name': 'Sales Invoice Destination Address',
    'version': '18.0.2.0.5',
    'summary': 'إدارة عناوين وجهات البيع وتحليل المبيعات حسب العنوان',
    'category': 'Sales',
    'author': 'Custom Development',
    'license': 'LGPL-3',
    'depends': ['sale', 'account'],
    'data': [
        'security/address_security.xml',
        'security/ir.model.access.csv',
        'wizard/address_sales_report_wizard_views.xml',
        'views/sale_destination_address_views.xml',
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'report/report_brand_header.xml',
        'report/account_invoice_report.xml',
        'report/address_sales_report.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
