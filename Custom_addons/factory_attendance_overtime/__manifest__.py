{
    "name": "Factory Attendance Overtime Review",
    "version": "18.0.2.1.1",
    "license": "LGPL-3",
    "summary": "Full admin approval workflow for factory attendance overtime",
    "depends": ["factory_attendance_dashboard", "furniture_mrp", "factory_attendance_lunch"],
    "data": [
        "data/overtime_cron.xml",
        "views/overtime_review_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "factory_attendance_overtime/static/src/js/overtime_review.js",
            "factory_attendance_overtime/static/src/xml/overtime_review.xml",
            "factory_attendance_overtime/static/src/scss/overtime_review.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
