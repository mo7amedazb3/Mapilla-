{
    'name': 'Factory manager stage presentation',
    'version': '18.0.1.0.3',
    'license': 'LGPL-3',
    'depends': ['furniture_need_to_produce', 'mapilla_backend_theme'],
    'data': ['views/production_design.xml'],
    'assets': {'web.assets_backend': [
        'furniture_dashboard_presentation/static/src/scss/production_design.scss',
    ]},
    'installable': True,
    'application': False,
}
