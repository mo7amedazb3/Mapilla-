{
    'name': 'مشرفي المصنع',
    'version': '18.0.2.0.1',
    'summary': 'تطبيق المشرفين للموبايل مرتبط بإجراءات الإنتاج والمخزن',
    'category': 'Manufacturing',
    'license': 'LGPL-3',
    'depends': ['furniture_need_to_produce', 'mapilla_backend_theme'],
    'data': ['security/ir.model.access.csv', 'views/mobile.xml'],
    'assets': {'web.assets_backend': [
        'furniture_supervisor_mobile/static/src/mobile.js',
    ]},
    'application': False,
    'installable': True,
}
