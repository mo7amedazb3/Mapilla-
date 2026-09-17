# -*- coding: utf-8 -*-
{
    "name": "Employee Advances Accounting",
    "version": "18.0.1.4.3",
    "category": "Human Resources",
    "summary": "Employee advances with instant posted accounting entries and live balances",
    "license": "LGPL-3",
    "depends": [
        "account",
        "hr",
        "mail",
        "simple_payroll_salary-2",
    ],
    "data": [
        "security/employee_advance_security.xml",
        "security/ir.model.access.csv",
        "data/employee_advance_data.xml",
        "views/employee_advance_views.xml",
        "views/hr_employee_views.xml",
        "views/simple_payroll_slip_views.xml",
        "wizard/employee_advance_repayment_wizard_views.xml",
        "views/employee_advance_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "employee_advance_accounting/static/src/css/style.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
