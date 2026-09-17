{
    "name": "Mapilla Read Only Sales & Purchase Operator",
    "version": "18.0.1.0.0",
    "license": "LGPL-3",
    "summary": "System-wide read-only users with normal Sales and Purchase operations",
    "depends": ["base", "web", "sale_management", "purchase", "stock", "account"],
    "data": ["security/readonly_groups.xml"],
    "installable": True,
    "application": False,
}
