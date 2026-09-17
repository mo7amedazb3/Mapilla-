{
    "name": "Factory Attendance Lunch Breaks",
    "version": "18.0.1.2.2",
    "license": "LGPL-3",
    "summary": "Report paid lunch duration without rewriting attendance punches",
    "depends": ["factory_attendance_dashboard", "furniture_mrp"],
    "data": ["views/hr_employee.xml"],
    "assets": {
        "web.assets_backend": [
            "factory_attendance_lunch/static/src/js/manual_lunch.js",
            "factory_attendance_lunch/static/src/xml/attendance_lunch.xml",
            "factory_attendance_lunch/static/src/scss/attendance_lunch.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
