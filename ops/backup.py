#!/usr/bin/env python3
"""Create an encrypted, consistent PostgreSQL snapshot plus referenced Odoo files.

Run with the server's Odoo Python environment (psycopg2) and PostgreSQL client.
The passphrase file must be kept outside this repository. No production writes.
"""
import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

import psycopg2


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--database', required=True)
    p.add_argument('--filestore', required=True, help='The selected database filestore directory')
    p.add_argument('--key-file', required=True)
    p.add_argument('--output', required=True, help='New directory for encrypted parts')
    p.add_argument('--allow-missing-files', action='store_true',
        help='Record files already missing on the source server in the encrypted manifest')
    args = p.parse_args()
    os.umask(0o077)
    output = Path(args.output).resolve()
    key = Path(args.key_file).resolve()
    if not key.is_file() or key.stat().st_size < 32:
        raise SystemExit('Provide a strong passphrase file with at least 32 random bytes.')
    repo = Path(__file__).resolve().parents[1]
    if key.is_relative_to(repo):
        raise SystemExit('The encryption key must be outside the repository.')
    if output.exists():
        raise SystemExit('Output already exists; choose a new snapshot directory.')
    config = configparser.ConfigParser(interpolation=None)
    config.read(args.config)
    options = config['options']
    params = dict(dbname=args.database, user=options['db_user'],
        password=options.get('db_password'), host=options.get('db_host', 'localhost'),
        port=options.get('db_port', '5432'))
    source = Path(args.filestore).resolve()
    with tempfile.TemporaryDirectory(prefix='mapilla-backup-') as temp:
        root = Path(temp)
        payload = root / 'payload'
        payload.mkdir()
        (payload / 'filestore').mkdir()
        checksums = {}
        missing = []
        with psycopg2.connect(**params) as conn:
            conn.set_session(isolation_level='REPEATABLE READ', readonly=True)
            with conn.cursor() as cur:
                cur.execute('SELECT pg_export_snapshot()')
                snapshot = cur.fetchone()[0]
                cur.execute('SELECT DISTINCT store_fname FROM ir_attachment WHERE store_fname IS NOT NULL')
                filenames = sorted(row[0] for row in cur.fetchall())
                cur.execute("SELECT name,latest_version FROM ir_module_module WHERE state='installed' ORDER BY name")
                modules = dict(cur.fetchall())
                pg_env = dict(os.environ, PGPASSWORD=params['password'] or '')
                subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-acl',
                    '--snapshot=' + snapshot, '--host', params['host'], '--port', params['port'],
                    '--username', params['user'], '--file', str(payload / 'database.dump'),
                    args.database], env=pg_env, check=True)
                for name in filenames:
                    if not re.fullmatch(r'[a-f0-9]{2}/[a-f0-9]{40}', name):
                        raise SystemExit('Unexpected filestore path; backup not published.')
                    src = source / name
                    if not src.is_file():
                        if args.allow_missing_files:
                            missing.append(name)
                            continue
                        raise SystemExit('A referenced attachment is missing; backup not published.')
                    dest = payload / 'filestore' / name
                    dest.parent.mkdir(exist_ok=True)
                    shutil.copyfile(src, dest)
                    checksums['filestore/' + name] = digest(dest)
        checksums['database.dump'] = digest(payload / 'database.dump')
        manifest = dict(format='mapilla-backup-v1', database=args.database,
            created_at=datetime.now(timezone.utc).isoformat(), installed_modules=modules,
            attachment_files=len(filenames) - len(missing), missing_filestore=missing,
            sha256=checksums)
        (payload / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        archive = root / 'backup.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name in ['database.dump', 'filestore', 'manifest.json']:
                tar.add(payload / name, arcname=name)
        encrypted = root / 'backup.gpg'
        subprocess.run(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback',
            '--no-symkey-cache', '--passphrase-file', str(key), '--cipher-algo', 'AES256',
            '--s2k-mode', '3', '--s2k-digest-algo', 'SHA512', '--s2k-count', '65011712',
            '--compress-algo', 'none', '--output', str(encrypted), '--symmetric', str(archive)], check=True)
        output.mkdir(parents=True)
        parts = []
        with encrypted.open('rb') as stream:
            index = 0
            while block := stream.read(64 * 1024 * 1024):
                part = output / f'backup.gpg.part{index:03}'
                part.write_bytes(block)
                parts.append(dict(name=part.name, bytes=len(block), sha256=digest(part)))
                index += 1
        (output / 'encrypted-manifest.json').write_text(json.dumps(dict(
            format='mapilla-encrypted-v1', created_at=manifest['created_at'],
            encryption='GnuPG AES256 symmetric with integrity protection',
            parts=parts), indent=2) + '\n')
        print(f'Encrypted snapshot: {len(modules)} installed modules, {len(filenames) - len(missing)} attachment files, {len(parts)} parts.')
        if missing:
            print(f'WARNING: {len(missing)} referenced files were already missing on the source server; recorded in encrypted manifest.')


if __name__ == '__main__':
    main()
