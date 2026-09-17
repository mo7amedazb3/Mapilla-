# Mapilla Backend Theme — Odoo 18 Community

A self-contained, non-invasive backend theme based on Mapilla's **Direction Two** identity.
It changes only the presentation layer and does not modify Odoo core files, business models,
records, accounting logic, inventory valuation or manufacturing workflows. Screen-specific rules
use screen media queries; shared Odoo palette variables can also be inherited by report styles.
No printed-document template or business layout is replaced.

## Included

- Mapilla-branded login screen with the supplied furniture imagery.
- Oxford Blue top navigation with the Mapilla wordmark.
- Branded favicon and browser theme color.
- Refined control panels, search facets, forms, status bars, list views, kanban cards,
  tabs, fields, dialogs, dropdowns, notifications, chatter, settings and scrollbars.
- Responsive desktop/tablet/mobile behavior.
- Extra styling for Odoo 18's native dark asset bundle.
- Screen-specific styling; no replacement of report/PDF document templates.
- No external fonts, CDNs, remote images or trackers.

## Workspace and navigation (18.0.2.1.0)

The home launcher now has a Mapilla editorial header, original vector furniture
illustration, native command-palette search and responsive application cards.
The sidebar shares the same stroke icon system, active-app treatment and a Home
shortcut. Both continue to use MuK's permission-filtered, user-ordered menu
service; unknown applications keep their supplied icon. No menu records, access
groups or business actions are changed.

`brand_navigation.js` extends MuK/Odoo presentation components using their public
patch, dropdown-state and service hooks. `brand_navigation.xml` keeps native
DropdownItem selection/keyboard behavior. `brand_navigation.scss` is appended
at sequence 1002 so MuK's inline background cannot obscure the new launcher.
Since 18.0.2.1.1 the sidebar starts closed, replacing the large/compact/hidden
presentation with a fixed left-edge toggle and a 260px overlay on all widths.
It closes after app selection, outside click or Escape; Escape restores toggle
focus. Closed links are inert. Reload starts closed without changing user settings.
The launcher hides the toggle; native mobile section navigation remains available.
`brand_drawer.scss` reserves no content width and respects reduced motion.

Entry animations, hover feedback and a slow decorative orbit respect
`prefers-reduced-motion`. No animation timer runs per screen;
the shared drawer observes only navbar size to stay below it. No transforms are
applied to the action container, sticky toolbars or route
nodes. Core and custom screen surfaces share the motion treatment without
changing status colours or manufacturing operations.

## Unified identity (18.0.2.0.0)

Direction Two is selected because its primary white wordmark (presentation p20)
matches the user's supplied logo. The palette is documented on p24. The existing
transparent wordmarks are reused without redrawing or cropping the brand artwork.

`data/brand_assets.xml` appends the screen identity **after** other addon manifests
(sequence 1000/1001). This also corrects MuK's competing primary button colour.
`brand_identity.scss` owns native controls, typography, navigation and dialogs;
`brand_workspaces.scss` maps optional custom factory screens to the same tokens.
It does not depend on manufacturing modules, so Wood can install the theme alone.

Coverage: native list/form/kanban/search/tabs/settings/Discuss, shortages and stage
shortages, Min/Max, production dashboard, materials/BoM dialogs, purchasing,
attendance/overtime, screen accounting reports and shared help popups.
Existing card sizes, sticky toolbars, selection, routes, animation and actions
are unchanged. Unique furniture-model hues are retained at reduced saturation.
Waiting amber, completed green, production purple and dangerous actions remain
distinct; business status colours must not be globally replaced by the brand.

Arabic uses locally bundled Tajawal under the `Mapilla Arabic` family, with the
existing Lato/system sans stack for Latin text. No subset proprietary PDF font is
extracted, renamed or distributed. Icon fonts and barcode/monospace text are not
overridden. Light/dark and reduced-motion preferences have explicit support.

This is the internal ERP **screen** identity: existing printed documents and their
business layouts are not rewritten. No company/account/stock/attendance record is
changed. For a custom new screen, inherit `--mapilla-*` colours and standard button
variants; use a scoped adapter only for legacy hardcoded surfaces.

Standalone regression tests:
`/opt/odoo/odoo18-venv/bin/python3 mapilla_backend_theme/tests/test_brand_contract.py`.
Disable the five `mapilla_backend_theme.brand_*` assets and restore this module's
backup to roll back the unified layer without touching business records.

## Brand tokens

| Token | Value |
|---|---|
| Oxford Blue | `#0C2A44` |
| Timberwolf | `#E0D8D1` |
| Quick Silver | `#ADA696` |
| Platinum | `#E2E2E2` |
| Black | `#000000` |
| White | `#FFFFFF` |

## Installation

1. Copy the folder `mapilla_backend_theme` into a directory listed in Odoo's `addons_path`.
2. Set ownership to the Odoo service user.
3. Restart Odoo.
4. Update the Apps list, remove the default **Apps** filter if needed, search for
   **Mapilla Backend Theme**, and install it.

Equivalent CLI example — paths and service names must be adapted to the server:

```bash
sudo rsync -a mapilla_backend_theme/ /opt/odoo/custom-addons/mapilla_backend_theme/
sudo chown -R odoo:odoo /opt/odoo/custom-addons/mapilla_backend_theme
sudo systemctl restart odoo
sudo -u odoo /opt/odoo/odoo-bin \
  -c /etc/odoo.conf \
  -d yasser3 \
  -i mapilla_backend_theme \
  --stop-after-init
sudo systemctl restart odoo
```

For an update after changing files, use `-u mapilla_backend_theme` instead of `-i`.

## Asset refresh

### Compact textile orders (18.0.2.1.2)

Upholstery and tailoring order cards remain full-width. Compact rows prioritize
the model, then each product, buyer/consumer, fabric, takawe and notes. The image
keeps its native enlargement action. Short cards are approximately 136px high
on desktop; long notes, multiple products and supervisor actions expand naturally
without clamping data. Other stage cards and business workflows are unchanged.

After installing or upgrading:

- Hard-refresh the browser (`Cmd+Shift+R` on macOS / `Ctrl+Shift+R` elsewhere).
- In developer mode, use **Regenerate Assets Bundles** when available.
- If an old bundle remains, restart Odoo and update the module again.

## QA checklist

- Login, logout, password reset and database selector.
- Apps/home navigation and every systray dropdown.
- List, form, kanban, calendar, graph and pivot views.
- Search, filters, group-by and favorites menus.
- Manufacturing, Inventory, Purchase, Sales, Accounting and Settings screens that exist in the database.
- Chatter composer, attachments, dialogs, notifications and confirmation popups.
- Desktop, tablet and mobile widths.
- Left-to-right and right-to-left layouts if Arabic is enabled.
- Native light and dark appearance modes.
- Browser console and Odoo server log contain no new errors.

## Uninstall / rollback

Uninstalling the module through Odoo returns it to the remaining installed theme's assets.
For a failed bundle, keep the installed addon in place: deactivate the five brand asset
records through an Odoo shell, restore the known-good addon files, update this addon only,
then restart and regenerate its screen assets. Do not delete an installed addon directory
or restore a whole production database merely to undo a visual change. Keep database and
filestore backups before a production installation.
