# Independent application validation — 2026-09-17

Published web application:
https://mapilla.net/supervisor-app?db=yasser3

Signed Android preview (ARM64, Android 7/API 24 or newer):
https://mapilla.net/furniture_supervisor_mobile/static/downloads/mapilla-supervisors-1.0.0.apk

SHA-256: `52aba64010c7190c47f194169406dcf58d6116eb1b37a0465368721a964dd419`

## Completed checks

- TypeScript check, production web export and final iOS Hermes export pass.
- Android native release builds pass with a dedicated private signing key.
  APK signature verification passes (v2). Package `com.mapilla.supervisors`,
  version 1.0.0/code 1, min SDK 24, target SDK 36, ARM64. No WebView wrapper.
  APK download returns HTTP 200 with the Android package MIME type.
- 12 Odoo tests pass with zero failures/errors on an isolated production clone.
  They cover denied accounts/stages, minimal/scoped data, hashed and expired
  sessions, password invalidation, logout tokens, command deduplication, wizard
  ownership and a real stock/receipt/kit lifecycle through the app API.
- Lifecycle test confirms receipt, start, pause/resume, refusal to finish before
  quality, quality acceptance, finish and a retried finish without extra stock
  moves. The unaffected sibling piece stays separate. Fixture hall stock is
  supplied separately, following the factory's shared-hall model.
- A hall supply test verifies allowed products, denied substitutions, original
  requester identity, submitted quantities and visibility in the request list.
- HTTP/native bearer smoke passes for all eight supervisor stages. BOM forms
  render for all eight; eligible supervisors send real warning wizards in the
  clone and retried submissions are deduplicated. Unauthorized stages, generic
  RPC, unauthenticated access and cross-origin requests are rejected.
- Clone material requests open/submit existing native business operations.
  Tailoring opens the independent quantity-selection form. Upholstery/packaging
  correctly refuse requests when upstream inputs are not ready. Six configured
  supervisors successfully send hall requisitions using an Arabic-digit quantity;
  two without material policies receive the existing configuration message.
- Browser smoke passes for eight supervisor logins on a 390×844 viewport:
  independent sign-in, summary, work/materials/updates/account tabs and logout.
  BOM details and desktop sign-in were also inspected. No iframe and no Odoo
  backend asset bundle are loaded by these screens.
- Live HTTPS checks pass for independent anonymous sign-in, the old bookmark
  redirect, read-only API and app rendering for two actual supervisor scopes,
  and existing ERP login pages on yasser3/Wood. No live production/stock/warning
  mutations were used for verification. Actual passwords were unchanged.

## Deployment and limits

Upgraded `furniture_supervisor_mobile` to 18.0.2.0.0 on `yasser3`; restarted Odoo.
The addon is included in `server_wide_modules` so its anonymous app/API entry
works before an ERP session selects a database in this multi-database server.
Database allowlisting and ordinary user/company/stage checks remain in force.

Fixed one existing receipt notification lookup in
`furniture_mrp/models/mrp_advance_material_release.py`: after the receipt's
permission, quantity and stock checks, only the private warehouse header's
notification recipient is read with sudo. The supervisor's permissions and
receipt actor remain unchanged. This was exercised by the real lifecycle test.

Physical Android installation has not been tested in this environment. The APK
is an internal preview. iOS source/native project is provided; no signed IPA or
TestFlight build exists because the factory has no Apple Developer account.
The app requires network connectivity and does not implement offline mutations
or background push notifications. This is an independent supervisor application;
manager-only ERP administration remains in the original system.

Pre-change database backup:
`/opt/odoo/backups/supervisor_standalone_20260917/yasser3_before.dump`.
Receipt source/config backups are in the same private backup directory.
Reports/screenshots: `/opt/odoo/reports/supervisor_standalone_20260917/`.

Production cookies explicitly require HTTPS independently of an intermediate
proxy's reported scheme; only loopback development hosts permit insecure cookies.
Fresh HTTPS password login against the isolated clone also passed on the live
multi-database entry: Secure/HttpOnly app cookie, successful app profile, no ERP
browser authentication, and revoked access after logout. Downloaded APK SHA-256
matches the verified local release. Temporary live verification sessions were
revoked; the test database, filestore and credential files were removed.

## ERP navigation restored at user request — 2026-09-17

Disabled the added top-level supervisor app menu (sequence 3), which preceded
the original ERP apps and caused default navigation to open the standalone app.
The legacy ERP URL/client action now returns to `/odoo`; the legacy URL action
no longer targets the standalone app. Module application flag is false.
Pre-app backup confirms the original root menu order starts at sequence 5 and
user home actions were never changed; the live menu order now matches.
The standalone app and API remain available only through their own links.

Verified live authenticated desktop browser opens `/odoo/discuss` with the normal
ERP navigation and zero JavaScript errors. Permission-filtered menus exclude
the added supervisor menu. Standalone app still responds independently; old
ERP bookmark redirects to `/odoo`. Temporary ERP verification session removed.
