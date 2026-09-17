# -*- coding: utf-8 -*-
{
    "name": "Factory Payroll",
    "version": "18.0.1.0.0",
    "summary": "Factory payroll based on biometric attendance, paid leave, and contracts",
    "category": "Human Resources/Payroll",
    "author": "Custom Development",
    "license": "LGPL-3",
    "depends": [
        "simple_payroll_salary-2",
        "employee_advance_accounting",
        "factory_biometric_attendance",
        "hr_holidays",
    ],
    "data": [
        "security/factory_payroll_security.xml",
        "security/ir.model.access.csv",
        "wizard/payroll_batch_wizard_views.xml",
        "views/payroll_slip_views.xml",
        "report/payroll_slip_report.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
