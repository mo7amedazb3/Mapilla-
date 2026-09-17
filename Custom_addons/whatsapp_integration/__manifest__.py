{
    'name': 'WhatsApp Integration API',
    'version': '18.0.1.0.6',
    'summary': 'Send and receive WhatsApp messages via local API',
    'category': 'Sales/CRM',
    'author': 'Skyline',
    'depends': ['base', 'mail', 'web', 'base_setup', 'whatsapp_connector', 'mapilla_backend_theme'],
    'data': [
        'security/ir.model.access.csv',
        'views/whatsapp_msg_views.xml',
        'views/whatsapp_session_views.xml',
        'views/whatsapp_livechat_template.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
