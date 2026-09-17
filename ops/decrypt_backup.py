#!/usr/bin/env python3
"""Decrypt and verify a backup outside the checkout; never restore over a live DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', default=str(Path(__file__).resolve().parents[1] / 'repository_backups/initial'))
    p.add_argument('--key-file', required=True)
    p.add_argument('--output', required=True, help='New private folder outside the repository')
    args = p.parse_args()
    os.umask(0o077)
    backup = Path(args.snapshot).resolve()
    output = Path(args.output).resolve()
    if output.exists() or output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise SystemExit('Choose a new output folder outside the repository.')
    manifest = json.loads((backup / 'encrypted-manifest.json').read_text())
    if manifest.get('format') != 'mapilla-encrypted-v1' or not manifest.get('parts'):
        raise SystemExit('Unsupported encrypted snapshot format.')
    with tempfile.TemporaryDirectory(prefix='mapilla-decrypt-') as tmp:
        root = Path(tmp)
        encrypted = root / 'backup.gpg'
        with encrypted.open('wb') as stream:
            for item in manifest['parts']:
                part = (backup / item['name']).resolve()
                if part.parent != backup or not part.is_file() or digest(part) != item['sha256']:
                    raise SystemExit('Encrypted backup integrity check failed.')
                with part.open('rb') as source:
                    shutil.copyfileobj(source, stream)
        plain = root / 'backup.tar.gz'
        subprocess.run(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback',
            '--no-symkey-cache', '--passphrase-file', str(Path(args.key_file).resolve()),
            '--output', str(plain), '--decrypt', str(encrypted)], check=True)
        stage = root / 'verified'
        stage.mkdir()
        with tarfile.open(plain, 'r:gz') as tar:
            for member in tar.getmembers():
                relative = Path(member.name)
                if relative.is_absolute() or '..' in relative.parts or not (member.isfile() or member.isdir()):
                    raise SystemExit('Unsafe archive member.')
            tar.extractall(stage, filter='data')
        content = json.loads((stage / 'manifest.json').read_text())
        if content.get('format') != 'mapilla-backup-v1':
            raise SystemExit('Unsupported backup content.')
        for name, expected in content['sha256'].items():
            path = (stage / name).resolve()
            if not path.is_relative_to(stage) or not path.is_file() or digest(path) != expected:
                raise SystemExit('Decrypted backup integrity check failed.')
        shutil.copytree(stage, output)
    print('Verified backup extracted to ' + str(output))
    if content.get('missing_filestore'):
        print(f"WARNING: {len(content['missing_filestore'])} files were already missing on the source server at backup time.")


if __name__ == '__main__':
    main()
