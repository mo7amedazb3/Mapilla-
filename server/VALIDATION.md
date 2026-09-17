# Live repository import — 2026-09-17

- Git worktree: `/opt/odoo`, directly containing the live addon and WhatsApp source.
- All 43 custom addons are tracked. Staged source bytes were compared against the
  actual live files with Git blob hashes: zero differences.
- Odoo core has no local modifications; submodule commit:
  `f4c76be062bec47b68ee42505d7d42fed31ac0f2`.
- Production service configuration and live application code were not changed by
  the repository import. No production service restart was needed.
- ERP login and standalone supervisors web entry both returned HTTP 200 with a
  normal cookie session. All three production services remained active.
- The encrypted database backup was decrypted and all included file hashes
  verified. PostgreSQL 16 restored the dump successfully into a newly created
  temporary database: **893 tables and 124 installed modules**. Its attachment
  references matched **560 included files plus 22 pre-existing missing SVG avatar
  files**, recorded inside the encrypted manifest.
- The temporary verification database and decrypted verification files were
  removed. The live database was not restored over or modified.
- A scan of tracked source/configuration found only known test password literals
  and the XML password-field flag. Production passwords, tokens and the backup
  decryption key are excluded. Configuration examples contain placeholders.

The encrypted snapshot reflects its timestamp in `encrypted-manifest.json`;
ongoing production transactions are not automatically copied into Git history.
