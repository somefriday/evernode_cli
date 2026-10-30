"""Export public README-run evidence, refusing known test secrets."""
import json
import os
from pathlib import Path
import shutil

from readme_workflows import CASES

base = Path(os.environ['E2E_BASE'])
target = Path(os.environ['E2E_RESULTS'])
secret_values = set()
for directory in {base / CASES[case]['registry'] for case in CASES}:
    for path in directory.glob('nodes/*/keys/**/*'):
        if not path.is_file():
            continue
        if 'seed' in path.name:
            value = path.read_text().strip()
            if len(value.split()) >= 12:
                secret_values.add(value)
        if path.suffix == '.json':
            try:
                data = json.loads(path.read_text())
            except (ValueError, UnicodeError):
                continue
            if isinstance(data, dict):
                for field in ('secret', 'private', 'private_key', 'pvt_key', 'seed'):
                    value = data.get(field)
                    if isinstance(value, str) and len(value) >= 32:
                        secret_values.add(value)

files = [(base / 'readme-trace.jsonl', Path('readme-trace.jsonl'))]
files += [(p, Path('evidence') / p.name) for p in (base / 'evidence').iterdir()
          if p.is_file() and p.suffix in ('.json', '.log')]
files += [(p, Path('commands') / p.name) for p in (base / 'logs').glob('*.log')]
for case, item in CASES.items():
    p = base / item['registry'] / 'nodes' / item['name'] / 'logs/validator/readme-election.log'
    if p.exists():
        files.append((p, Path('elections') / (case + '.log')))
for source, relative in files:
    content = source.read_text()
    if any(value in content for value in secret_values):
        raise SystemExit('Secret found in ' + str(relative))
    if any(token in content for token in ('"secret":', '"pvt_key":', '"private_key":')):
        raise SystemExit('Private key field found in ' + str(relative))
# Validate every source before exporting anything.
for source, relative in files:
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
print(json.dumps({'exported_files': len(files), 'known_secrets_checked': len(secret_values),
                  'contains_known_secrets': False}))
