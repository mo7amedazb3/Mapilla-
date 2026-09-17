# -*- coding: utf-8 -*-
{
    "name": "Arabic Number Converter",
    "version": "18.0.1.0.0",
    "category": "Tools",
    "summary": "Automatically converts Arabic-Indic numerals (٠-٩) to standard English numerals (0-9) across the entire system.",
    "description": """
        This module provides a global JavaScript listener that intercepts user input in Odoo.
        Whenever a user types Arabic-Indic numerals (٠, ١, ٢, ٣, ٤, ٥, ٦, ٧, ٨, ٩), it instantly converts them to standard numerals (0, 1, 2, 3, 4, 5, 6, 7, 8, 9).
    """,
    "author": "Custom",
    "license": "LGPL-3",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "arabic_number_converter/static/src/js/arabic_to_english.js",
        ],
    },
    "application": False,
    "installable": True,
    "auto_install": False,
}
