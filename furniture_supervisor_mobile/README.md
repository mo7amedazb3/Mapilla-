# Mapilla Supervisors

Independent React Native / Expo application for factory supervisors. The Android,
iOS and web targets share native React Native screen components. No WebView,
iframe, Odoo form renderer or Odoo backend bundle is used by the new interface.

Web entry: `https://mapilla.net/supervisor-app?db=yasser3`.
The standalone app is accessible at `/supervisor-app` or `/supervisor`.
The ERP menu added during development is disabled; `/odoo/supervisors` now
returns to `/odoo`. The ERP must never automatically open the mobile app.

Home shows the supervisor's current summary followed by assigned work. Separate
screens handle work, material requests and receipts, production warnings,
quality/handoffs and the account. Operations include start/finish, timer pause/
resume, BOM inspection, material selection, full/partial receipt with a note, shared-hall replenishment requests,
manual quality and textile kit/loose-piece execution. Availability follows the
same stage/company and workflow rules as the original system. Photos and delivery
notes are shown when present. Completed/upstream work has its own work filters.

## Authentication and permissions

Sign in with existing Odoo credentials. Authentication uses Odoo's password
policy, rate limiting and TOTP; the app receives a separate revocable session,
not an authenticated ERP browser session. Passwords are never saved by the app.
Web sessions use a Secure, HttpOnly, SameSite=Strict cookie. Native tokens use
Expo SecureStore. The database stores only a token hash, a password-bound session
fingerprint and a seven-day expiry. Account/stage permissions are checked on each
request. Expired tokens are rejected and logout revokes the app session.

`controllers/app.py` exposes a closed operation list. `models/app_api.py` adapts
existing production methods and their transient wizards to independent forms;
it never accepts arbitrary model names, method names, user IDs or caller context.
Wizard grants are scoped to the authenticated app session. Mutations are
serialized with a row lock and deduplicated by command ID and payload. Images
are served through the authenticated API after stage/record visibility checks.

The web preview uses real data and real operations. Internet is required. There
are no offline writes or background push notifications. Refresh is available
manually and every minute while the app is active. The totals are current stage
totals, not a historical report limited to the current calendar day.

## Build

The application source is in `app/`, including generated `android/` and `ios/`
projects. Use Node 22 or another version supported by Expo SDK 54.

```sh
cd app
npm ci
npm run check
npx expo export --platform web --output-dir ../static/app
npx expo export --platform android --platform ios --output-dir native-bundles
```

For Android, install Java 17 and Android SDK 36/Build Tools 36/NDK 27.1.12297006.
Set `android/local.properties` for the local SDK. A release build requires
`MAPILLA_SIGNING_PROPERTIES` pointing to a private Java properties file containing
`storeFile`, `storePassword`, `keyAlias`, `keyPassword`.

```sh
MAPILLA_SIGNING_PROPERTIES=/private/mapilla.properties \
  ./android/gradlew -p android assembleRelease \
  -PreactNativeArchitectures=arm64-v8a --max-workers=2 --no-daemon
```

Do not commit or publish signing files. The factory's key is stored outside the
application in `/opt/odoo/mobile-build-tools/signing/`; keep a private backup to
sign future updates. Re-running Expo prebuild can replace native signing setup;
review generated changes before building again. The Android preview targets
ARM64 devices. iOS distribution still requires an Apple Developer account and
Apple signing/provisioning; no installable iOS binary is supplied yet.

Upgrade the Odoo addon `furniture_supervisor_mobile`, then restart Odoo to load
its Python controllers. On a multi-database server, include this addon in
`server_wide_modules = base,web,furniture_supervisor_mobile` so anonymous app
routes are available before the browser selects an ERP database. It depends on `furniture_need_to_produce` and
`mapilla_backend_theme`. Built web files are served directly by the addon.

The npm audit findings currently concern Expo/Metro image/CSS processing and
build tooling. These tools are not exposed by the deployed static app. SDK
migration and its native compatibility checks should be planned separately.

See `VALIDATION.md` for verification evidence and current limitations.
