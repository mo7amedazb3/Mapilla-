{
    "name": "Mapilla Backend Theme",
    "summary": "Mapilla branded backend and login theme for Odoo 18 Community",
    "description": """
Mapilla Backend Theme
=====================
A non-invasive visual theme for the Odoo 18 Community web client.
It applies Mapilla's Oxford Blue and neutral palette to the backend,
adds Mapilla navigation branding, and replaces the login presentation.
    """,
    "version": "18.0.2.1.6",
    "category": "Themes/Backend",
    "author": "Mapilla",
    "website": "https://www.mapillafurniture.com",
    "license": "LGPL-3",
    # MuK Backend Theme is installed on the target deployment and otherwise
    # overwrites the primary palette after Mapilla's declarations. Depending on
    # it makes the asset order deterministic while leaving MuK itself untouched.
    "depends": ["web", "muk_web_theme"],
    "data": [
        "views/webclient_templates.xml",
        "data/brand_assets.xml",
    ],
    "assets": {
        # Re-assert the palette at the end of the primary-variable sub-bundle.
        # This is required for compatibility with the installed MuK Colors
        # module, whose variables use unconditional assignments.
        "web._assets_primary_variables": [
            "mapilla_backend_theme/static/src/scss/primary_variables.scss",
        ],
        # Prepend brand variables only to screen web-client bundles. This avoids
        # altering Odoo's report/PDF bundles while still compiling the backend
        # with Mapilla's palette.
        "web.assets_web": [
            ("prepend", "mapilla_backend_theme/static/src/scss/primary_variables.scss"),
        ],
        "web.assets_backend_lazy": [
            ("prepend", "mapilla_backend_theme/static/src/scss/primary_variables.scss"),
        ],
        "web.assets_backend": [
            "mapilla_backend_theme/static/src/scss/backend.scss",
            "mapilla_backend_theme/static/src/scss/brand_drawer.scss",
            "mapilla_backend_theme/static/src/scss/brand_order_cards.scss",
            "mapilla_backend_theme/static/src/scss/branded_date_picker.scss",
            "mapilla_backend_theme/static/src/js/brand_navigation.js",
            "mapilla_backend_theme/static/src/js/employee_search.js",
            "mapilla_backend_theme/static/src/js/branded_date_picker.js",
            "mapilla_backend_theme/static/src/xml/employee_search.xml",
            "mapilla_backend_theme/static/src/xml/navbar.xml",
            "mapilla_backend_theme/static/src/xml/brand_navigation.xml",
        ],
        "web.assets_web_dark": [
            "mapilla_backend_theme/static/src/scss/backend_dark.scss",
        ],
        "web.assets_frontend": [
            "mapilla_backend_theme/static/src/scss/login.scss",
        ],
    },
    "images": [
        "static/description/banner.png",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
