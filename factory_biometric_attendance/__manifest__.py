# -*- coding: utf-8 -*-
{
    "name": "Factory Biometric Attendance",
    "version": "18.0.1.4.4",
    "summary": "ربط أجهزة ZKTeco بالحضور والانصراف مع سجل تدقيق كامل",
    "description": """
        تكامل مستقل بين أجهزة بصمة ZKTeco التي تدعم ADMS/PUSH وبين حضور Odoo.
        يحتفظ الجهاز بقوالب البصمة، بينما يحفظ Odoo رقم العامل والحركات فقط.
    """,
    "category": "Human Resources/Attendances",
    "author": "Custom Development",
    "license": "LGPL-3",
    "depends": ["mail", "hr_attendance"],
    "data": [
        "security/biometric_security.xml",
        "security/ir.model.access.csv",
        "data/biometric_sequence.xml",
        "views/biometric_device_views.xml",
        "views/biometric_identity_views.xml",
        "views/biometric_event_views.xml",
        "views/biometric_command_views.xml",
        "views/hr_employee_views.xml",
        "views/hr_attendance_views.xml",
        "report/biometric_directory_report.xml",
        "views/biometric_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
