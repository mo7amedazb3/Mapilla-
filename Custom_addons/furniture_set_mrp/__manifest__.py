{
    "name": "Furniture Set Manufacturing",
    "version": "18.0.1.3.0",
    "summary": "Variable sofa/armchair quantities and controlled extra materials",
    "category": "Manufacturing/Manufacturing",
    "license": "LGPL-3",
    "depends": ["mrp", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/mrp_bom_views.xml",
        "views/mrp_production_views.xml",
        "wizard/extra_material_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
