{
    'name': 'Conversion Cost Report',
    'version': '18.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Generate Conversion Cost PDF Report from Analytic Accounts',
    'description': """
        This module adds a wizard to calculate and print the Conversion Cost.
        Conversion Cost = Labour + Overhead.
    """,
    'author': 'Antigravity',
    'depends': ['base', 'analytic', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'wizards/conversion_cost_wizard_views.xml',
        'reports/conversion_cost_report.xml',
        'reports/conversion_cost_report_template.xml',
        'views/analytic_account_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
