# -*- coding: utf-8 -*-
{
    "name": "Simple Payroll Salary (Contract + Attendance + Commission)",
    "version": "18.0.1.0.1",
    "category": "Human Resources",
    "summary": "Generate employee salary slips from contract wage + attendance hours + invoice commissions",
    "license": "LGPL-3",
    "depends": ["hr", "hr_contract", "hr_attendance"],
    "data": [
        "security/ir.model.access.csv",
        "report/payroll_slip_report.xml",
        "views/payroll_slip_views.xml",
        "views/payroll_reports_views.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "simple_payroll_salary-2/static/src/css/payroll_style.css",
        ],
    },
    "application": True,
    "installable": True,
}
