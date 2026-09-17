# Switch_Users (temporary testing tool)

Administrator avatar → **Switch account** → **المشرفين** / **العمال**.
Supervisor names include their assigned stages; workers include their department.
Only active internal accounts linked to active employees can be selected. In the
factory setup, `supervisor` and `worker` roles are included, along with explicitly
configured direct shortcuts. No accounts are
created and no passwords, groups, employee or operational records are changed.

### Direct account shortcuts

The administrator-managed `Switch_Users.direct_user_ids` system parameter holds
a JSON list of explicit user IDs for this database, e.g. `[157]`. These accounts
appear directly under **Switch account**, outside the supervisor/worker submenus.
The deployment shortcut is **محمد الجزار**. Identity is resolved before configuring
each database, not by matching a name during login. An active linked employee is
still required, and all existing target/company/security restrictions apply.
Invalid parameter values fail closed. Clearing the parameter removes shortcuts.

## Authorization and session behavior

- Starting a test session requires the Settings administrator group (`base.group_system`).
- The return/switch capability lives only in that authenticated server-side
  session, is bound to its database and current UID, and expires after four hours.
- Origin administrator credential/MFA/active changes or loss of administrator
  rights revoke the capability. Ordinary employee logins never gain it.
- All mutation endpoints are POST-only and explicitly validate Odoo CSRF tokens.
  Target UID, active status, employee role and company scope are rechecked each time.
- Target system admins, portal/public users and users with companies outside the
  origin admin's scope are excluded. Return is only to the verified origin admin.
- Switching clears the old session/company/elevated-mode context and uses Odoo
  session finalization/rotation. A full page reload uses the target's real rights.
- **TEST** appears next to the avatar. **الرجوع للأدمن** restores the starting admin.
- Switching affects other tabs sharing this browser session. Work performed in
  the target account is real work on this database, not a sandbox or dry run.
- Audit entries in the Odoo server log contain DB/origin UID/from UID/target UID.
- Logout and normal password login discard the capability. Removing this addon
  removes its menu and endpoints; sign out of testing sessions before uninstall.

Install/uninstall **Switch_Users** independently through Apps. Core Odoo and
manufacturing/HR business modules are not modified by this addon.
