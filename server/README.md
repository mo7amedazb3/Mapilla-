# Current server environment and recovery

These are templates captured from the running server; private passwords are
replaced with placeholders. The active service configuration remains in `/etc`.
The repository now directly tracks the live code in `/opt/odoo`; importing Git
does not change the systemd units, Nginx, database or running application.

Runtime: Ubuntu 24.04, Python 3.12, PostgreSQL 16, Odoo 18 at the commit recorded
in `SERVER_VERSION.json` and the `odoo18` submodule. `requirements.lock` records
the current Python environment. The standard Odoo prerequisites (compiler,
Python headers, PostgreSQL/LDAP/SASL development libraries and report fonts) are
needed before installing these requirements. Reports require wkhtmltopdf with
patched Qt. The supervisors app build instructions remain inside
`Custom_addons/furniture_supervisor_mobile/README.md`.

## Recovery on another server

1. Clone recursively. Provision PostgreSQL 16 and a dedicated Odoo role (not the
   PostgreSQL superuser). Use a **new empty database**, never overwrite a live DB.
2. Create a Python 3.12 virtual environment and install `server/requirements.lock`.
   Match the paths in the templates, or deliberately adapt them to your server.
3. Decrypt the backup with `ops/decrypt_backup.py`. This authenticates and checks
   the dump and every referenced attachment before recovery.
4. Create the new database owned by the Odoo role, using `template0`, UTF8. Restore
   using PostgreSQL 16 `pg_restore --exit-on-error --no-owner --no-acl -d NEW_DB
   /private/restored/database.dump`. Supply credentials through a private `.pgpass`
   file or your normal secure connection configuration.
5. Copy the contents of `/private/restored/filestore/` to
   `DATA_DIR/filestore/NEW_DB/`; make the directory owned by the Odoo service user.
6. Create a private config based on `odoo.conf.example`, setting private credentials,
   `data_dir`, selected `db_name`, exact `dbfilter`, addon paths and free local ports.
   Do not reuse production network endpoints for a test copy.
7. Before starting a test copy, neutralize the **new database** with Odoo's
   `odoo-bin neutralize -c /private/new.conf -d NEW_DB`; also disable custom WhatsApp
   and biometric connections and scheduled jobs. Keep the test environment's
   outbound network blocked until integrations have been reviewed. The encrypted
   snapshot deliberately preserves real production credentials/settings inside
   the database; neutralization is not anonymization.
8. Start Odoo against the restored database without `-u all`. Check login, ERP menus,
   attachments, reports and the separate `/supervisor-app` before enabling access.
   Keep the original ERP/mobile separation unchanged.
9. For production migration, arrange the database cutover, HTTPS, scheduled tasks,
   integrations and device addresses deliberately. This repository does not switch
   production DNS or start a second sender automatically.

## WhatsApp bridge

The live `whatsapp-bridge/` source and package lock are tracked directly. Install
Node.js and run `npm ci` there, following the Puppeteer/Chromium OS requirements;
voice messages additionally need `ffmpeg`. Create a private configuration from
`whatsapp-bridge.json.example`; configure the same callback token in the target
Odoo database and bridge, and keep the service bound to localhost. Use a writable
state directory with the permissions from the unit template. Existing linked
device authentication and pending runtime state are not public source files;
pair a migrated instance deliberately. Never run a test copy as the same active
WhatsApp sender.

References: [Odoo source installation](https://www.odoo.com/documentation/18.0/administration/on_premise/source.html),
[Odoo CLI / neutralize](https://www.odoo.com/documentation/18.0/developer/cli.html).
