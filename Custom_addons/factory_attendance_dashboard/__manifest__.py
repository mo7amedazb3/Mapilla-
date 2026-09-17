# -*- coding: utf-8 -*-
{
    "name": "Factory Attendance Dashboard",
    "version": "18.0.1.0.12",
    "summary": "لوحة حضور يومية للمصنع مرتبطة مباشرة بجهاز البصمة",
    "category": "Human Resources/Attendances",
    "author": "Custom Development",
    "license": "LGPL-3",
    "depends": [
        "web",
        "hr_attendance",
        "hr_holidays",
        "factory_biometric_attendance",
    ],
    "data": [
        "views/attendance_menu_cleanup.xml",
        "views/attendance_dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "factory_attendance_dashboard/static/src/js/attendance_dashboard.js",
            "factory_attendance_dashboard/static/src/xml/attendance_dashboard.xml",
            "factory_attendance_dashboard/static/src/scss/attendance_dashboard.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
