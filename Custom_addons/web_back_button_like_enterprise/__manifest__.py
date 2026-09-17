# -*- coding: utf-8 -*-
{
    "name": "Web Back Button Like Enterprise",
    "version": "18.0.1.0.0",
    "category": "Hidden",
    "summary": "Adds a persistent back arrow to the web client Breadcrumbs like Odoo Enterprise.",
    "description": "Adds a persistent back arrow to the web client Breadcrumbs like Odoo Enterprise.",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "web_back_button_like_enterprise/static/src/xml/breadcrumbs_inherit.xml",
            "web_back_button_like_enterprise/static/src/css/back_button.css",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
