# Mapilla — Custom Odoo Addons and Supervisors App

Source snapshot of the factory's custom Odoo 18 addons, including the independent
React Native supervisors app. See [ADDONS.md](ADDONS.md) for the 43 included modules.

## Layout

Each top-level module directory is an Odoo addon. Install Odoo 18 and the required
Python/system dependencies separately, add this repository to `addons_path`, and
install or upgrade only the modules needed by the target database. Existing
module manifests define their dependencies and licenses; those licenses remain
in effect for each module and its assets.

The supervisors app source, Android/iOS projects and build instructions are in
[furniture_supervisor_mobile/README.md](furniture_supervisor_mobile/README.md).
The standalone app is separate from the normal ERP interface. Do not re-enable
its retired ERP root menu or redirect the main ERP entry to the mobile app.

## Local and private files

This repository intentionally excludes production database dumps, filestores,
server configuration, credential files, signing keys, operational CSV exports,
logs, installed dependencies, caches and native build outputs. The generated
web app assets are included so the Odoo addon can serve its independent web UI.
Android APK/iOS IPA files belong in release artifacts, not the source tree.

Keep database/filestore backups and signing keys in a separate private backup.
This source repository is not a complete data backup of the running factory.

The export was prepared separately from the running system; preparing it did
not modify the live addon files or the production database.
