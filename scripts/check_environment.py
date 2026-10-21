"""Read tool readiness only; no credentials, model calls or automatic installation."""
import argparse
import json
import platform
import shutil
import subprocess
import sys

EXPECTED = {'node': 'v22.16.0', 'tsc': 'Version 5.8.3', 'go': 'go version go1.23.2 linux/amd64'}


def inspect_tools(docker=False):
    tools = {}
    for name, expected in EXPECTED.items():
        binary = shutil.which(name)
        item = {'available': False, 'expected': expected}
        if binary:
            try:
                result = subprocess.run([binary, 'version' if name == 'go' else '--version'],
                                        capture_output=True, text=True, timeout=10)
                item.update(available=result.returncode == 0, version=(result.stdout + result.stderr).strip())
            except (OSError, subprocess.TimeoutExpired) as exc:
                item['error'] = type(exc).__name__
        item['matches_pinned_version'] = item.get('version') == expected
        tools[name] = item
    result = {'python': platform.python_version(), 'python_supported': sys.version_info >= (3, 11),
              'platform': platform.system(), 'machine': platform.machine(), 'tools': tools,
              'model_api_calls': 0}
    result['ready'] = result['python_supported'] and all(t['matches_pinned_version'] for t in tools.values())
    if docker:
        binary = shutil.which('docker')
        ready = False
        if binary:
            try:
                probe = subprocess.run([binary, 'info', '--format', '{{.ServerVersion}}'],
                                       capture_output=True, text=True, timeout=10)
                ready = probe.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        result['docker_daemon_ready'] = ready
        result['ready'] = result['ready'] and ready
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true')
    args = parser.parse_args()
    result = inspect_tools(args.docker)
    print(json.dumps(result, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
