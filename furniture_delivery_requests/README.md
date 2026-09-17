# طلبات التسليم

Standalone, administrator-only Odoo application linked to `furniture_mrp` (MRP II).
It reuses the existing delivery request/order-line tables, reservation actions,
shortage-production links, forms and alerts; installation does not copy orders or
change stock. The existing pending-requests entry moves under the new app, with a
separate all-requests view and a dedicated SVG home-screen icon.

Access requires `base.group_system`. Global company-aware record rules also
restrict existing factory-manager grants. Non-admins receive no delivery bell,
counts or request details. Manufacturing/warehouse operational permissions are
not changed. Stock completion continues to reserve production output through the
existing internal MRP integration.

Install this opt-in addon on the intended database only. Uninstall restores the
previous MRP menu placement and action groups without removing delivery records.
Regression tests include the original finished-stock reservation and shortage
workflows, plus app visibility and server-side permission checks.
