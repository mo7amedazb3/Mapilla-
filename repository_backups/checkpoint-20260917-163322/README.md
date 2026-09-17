# Checkpoint 2026-09-17 16:33 UTC

Git tag: `checkpoint-20260917-163322`.

This checkpoint records the tracked live server code and an encrypted snapshot
of the factory database with its available referenced attachments. See
`checkpoint.json` for exact source revisions, snapshot time, restore verification,
scope and exclusions. The encryption key remains outside GitHub.

Decrypt and verify into a **new private directory outside the checkout**:

```bash
python3 ops/decrypt_backup.py \
  --snapshot repository_backups/checkpoint-20260917-163322 \
  --key-file /private/repository-backup.key \
  --output /private/mapilla-checkpoint-20260917-163322
```

This command extracts and verifies the backup; it does not modify PostgreSQL or
the running application. Restoring code and restoring production data are separate
actions. Follow the recovery instructions in `server/README.md` for database and
filestore recovery. The 22 SVG avatar files already missing from the source server
remain documented in the encrypted manifest; this checkpoint cannot recover them.
