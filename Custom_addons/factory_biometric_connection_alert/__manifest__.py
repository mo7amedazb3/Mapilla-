{
    "name": "Factory Biometric Connection Alert",
    "summary": "Persistent administrator warning while a biometric device is offline",
    "version": "18.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["web", "factory_biometric_attendance"],
    "assets": {
        "web.assets_backend": [
            "factory_biometric_connection_alert/static/src/connection_alert.js",
            "factory_biometric_connection_alert/static/src/connection_alert.xml",
            "factory_biometric_connection_alert/static/src/connection_alert.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "auto_install": False,
    "application": False,
}
