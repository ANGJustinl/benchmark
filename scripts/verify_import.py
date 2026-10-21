"""Verify imported bytes without the original workspace. Never calls a model."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from changebench.design import source_fingerprint

EXPECTED = '0a93357cc5e0261512e6a402457c0ef419e80a0f16726ba5ef1d7c8384a61b3b'


def main():
    manifest = json.loads((ROOT / 'docs/provenance/imported-files.sha256.json').read_text())
    mismatches = []
    for name, expected in manifest.items():
        path = ROOT / name
        if (not path.resolve().is_relative_to(ROOT) or path.is_symlink()
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            mismatches.append(name)
    observed = source_fingerprint()
    result = {'imported_files': len(manifest), 'mismatches': mismatches,
              'expected_source_sha256': EXPECTED, 'observed_source_sha256': observed,
              'passed': not mismatches and observed == EXPECTED}
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
