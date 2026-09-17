{
    'name': 'Global Kanban Hover Animation',
    'version': '18.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': 'Adds a smooth hover animation (scale and shadow) to all Kanban cards in Odoo.',
    'description': """
Global Kanban Hover Animation
=============================
This module applies a sleek CSS hover effect to Kanban cards across the entire Odoo system.
When hovered, the cards will gracefully lift up slightly and gain a darker, softer shadow for a more premium feel.
    """,
    'author': 'Antigravity',
    'website': '',
    'depends': ['web'],
    'assets': {
        'web.assets_backend': [
            'global_kanban_hover_animation/static/src/css/kanban_hover.css',
        ],
    },
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
