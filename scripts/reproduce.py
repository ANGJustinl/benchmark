"""Explicit zero-model-call reproduction tiers, with commands and failure receipts."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tier', choices=('unit', 'reference', 'docker'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--images', type=Path, help='Existing immutable-image mapping, required for Docker')
    args = parser.parse_args()
    out = args.out.resolve()
    if args.tier == 'docker' and (not args.images or not args.images.is_file()):
        parser.error('--images must name an existing JSON file for Docker integration')
    if out.exists():
        parser.error('--out must not exist; existing evidence is never overwritten')
    out.mkdir(parents=True)
    env = dict(os.environ)
    # These assignments need no credentials; preserve network proxy/CA settings.
    for name in ('APIZN_API_KEY', 'OPENAI_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'):
        env.pop(name, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    summary = {'tier': args.tier, 'status': 'running', 'controller_pid': os.getpid(),
               'started_at': datetime.now(timezone.utc).isoformat(), 'model_api_calls': 0,
               'commands_completed': 0}

    def save():
        temporary = out / 'summary.tmp'
        temporary.write_text(json.dumps(summary, indent=2) + '\n')
        temporary.replace(out / 'summary.json')

    def run(*arguments):
        command = [sys.executable, *map(str, arguments)]
        event = {'command': command, 'cwd': str(ROOT), 'started_at': datetime.now(timezone.utc).isoformat()}
        print('Running:', ' '.join(command), flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=env)
        event.update(returncode=completed.returncode, finished_at=datetime.now(timezone.utc).isoformat())
        with (out / 'commands.jsonl').open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        summary['commands_completed'] += 1
        save()
        if completed.returncode:
            raise subprocess.CalledProcessError(completed.returncode, command)

    save()
    try:
        run('scripts/verify_import.py')
        probe = ['scripts/check_environment.py'] + (['--docker'] if args.tier == 'docker' else [])
        run(*probe)
        if args.tier == 'unit':
            run('-m', 'pytest', '-q', 'tests', '--junitxml=' + str(out / 'unit-tests.xml'))
        elif args.tier == 'reference':
            run('-m', 'changebench', 'calibrate', '--out', out / 'calibration')
            for suite in ('poly', 'legacy'):
                run('-m', 'changebench', 'replay', '--suite', suite, '--repeats', '1', '--out', out / suite)
                run('-m', 'changebench', 'audit-run', out / suite)
        else:
            env['CB_REPAIR_IMAGES'] = str(args.images.resolve())
            env['CB_REPAIR_RESULTS'] = str(out / 'docker-evidence')
            run('-m', 'pytest', '-q', 'integration', '--junitxml=' + str(out / 'docker-tests.xml'))
        summary['status'] = 'passed'
    except BaseException as exc:
        summary.update(status='failed', error_type=type(exc).__name__)
        if isinstance(exc, subprocess.CalledProcessError):
            summary['failed_returncode'] = exc.returncode
        save()
        raise
    finally:
        summary['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
