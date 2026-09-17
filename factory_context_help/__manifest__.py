{
    'name': 'Factory Contextual Help',
    'version': '18.0.1.0.2',
    'license': 'LGPL-3',
    'category': 'Hidden',
    'summary': 'Optional inline guidance behind accessible ! explanation dialogs',
    'depends': ['furniture_need_to_produce', 'furniture_assembly_requisitions',
                'furniture_delivery_requests', 'factory_attendance_lunch', 'factory_attendance_overtime'],
    'data': ['views/context_help_views.xml'],
    'assets': {'web.assets_backend': [
        'factory_context_help/static/src/context_help.js',
        'factory_context_help/static/src/context_help.xml',
        'factory_context_help/static/src/context_help.css',
        'factory_context_help/static/src/dashboard_help.xml',
    ]},
    'installable': True,
    'application': False,
}
