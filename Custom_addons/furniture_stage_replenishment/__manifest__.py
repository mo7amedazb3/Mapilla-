# -*- coding: utf-8 -*-

{
    'name': 'Min / Max',
    'version': '18.0.3.0.15',
    'icon': '/furniture_stage_replenishment/static/description/icon.svg',
    'summary': 'Min/Max للنجارة والدهانات والقواعد/التجهيز والتفصيل',
    'category': 'Manufacturing',
    'author': 'Custom Development',
    'license': 'LGPL-3',
    'depends': [
        'furniture_mrp',
    ],
    'data': [
        'security/stage_replenishment_security.xml',
        'security/ir.model.access.csv',
        'data/stage_replenishment_cron.xml',
        'views/stage_replenishment_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'furniture_stage_replenishment/static/src/js/stage_replenishment_dashboard.js',
            'furniture_stage_replenishment/static/src/js/final_replenishment_dashboard.js',
            'furniture_stage_replenishment/static/src/js/stage_timer_dashboard_patch.js',
            'furniture_stage_replenishment/static/src/xml/stage_replenishment_dashboard.xml',
            'furniture_stage_replenishment/static/src/xml/final_replenishment_dashboard.xml',
            'furniture_stage_replenishment/static/src/css/stage_replenishment_dashboard.css',
            'furniture_stage_replenishment/static/src/css/final_replenishment_dashboard.css',
            'furniture_stage_replenishment/static/src/css/stage_timer_dashboard.css',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'auto_install': False,
}
