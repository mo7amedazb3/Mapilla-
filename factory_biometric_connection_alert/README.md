# Factory Biometric Connection Alert

Read-only connection monitoring for Settings administrators (`base.group_system`).
A compact Arabic warning appears at the bottom-right of the Odoo backend while
a device has stopped contacting the server. The top close button dismisses it
for the current open page only; polling and in-app navigation do not reopen the
same outage. Reloading or reopening the database shows it again if still offline.
No dismissal is stored in cookies, browser storage, or the database. A confirmed
recovery also clears dismissal so a later outage can warn again.

- Checks every 15 seconds and on browser focus/reconnection.
- A device is offline after 120 seconds without `last_seen`, inclusive.
- Uses accepted PUSH contact timestamps, **not** attendance creation timestamps.
- An active device that has never connected gets a 120-second creation grace.
- Monitors active, approved devices in the current user's selected companies.
- Multiple disconnected devices share one notification.
- The next successful status check after a fresh device heartbeat removes it.
- RPC failure retains the last offline state and reports that status is unknown;
  it is never treated as recovery. Stalled requests time out after 10 seconds.
- No polling, UI, or data exposure for non-administrators.
- Initial installation binds `factory_biometric_connection_alert.database` to the
  current database name. Copied databases stay disabled unless explicitly
  rebound by an administrator. Uninstall removes that parameter and the addon.

This adds no cron jobs, device commands, attendance records, verification-mode
changes, device resets, or fingerprint modifications. It detects missing server
contact; it cannot determine whether the cause is LAN, internet, power, or the
device. An Odoo tab must be open to display the notification; this is not an
email/SMS or operating-system push service.
