{
    'name': 'Switch_Users',
    'summary': 'Temporary administrator-only employee account switching for testing',
    'version': '18.0.1.0.1',
    'license': 'LGPL-3',
    'depends': ['web', 'hr'],
    'assets': {
        'web.assets_backend': [
            'Switch_Users/static/src/switch_users.js',
            'Switch_Users/static/src/switch_users.xml',
            'Switch_Users/static/src/switch_users.scss',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
