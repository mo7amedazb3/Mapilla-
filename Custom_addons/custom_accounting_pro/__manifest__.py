{
    'name': 'Custom Accounting Pro',
    'version': '18.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Enterprise-like Accounting Dashboard and Menus for Community',
    'description': """
        Adds full accounting menus and an interactive dashboard similar to Odoo Enterprise.
    """,
    'depends': ['account', 'web'],
    'data': [
        'views/menu_views.xml',
        'views/report_menus.xml',
        'report/pdf_template.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_accounting_pro/static/src/css/report_styles.css',
            'custom_accounting_pro/static/src/js/dynamic_report.js',
            'custom_accounting_pro/static/src/xml/dynamic_report_templates.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
