"""Experimental Codex CLI app-server transport for ChangeBench's controlled tools.

The host holds authentication. Model-visible file operations only touch an in-memory
public snapshot, and candidate execution always goes through offline Docker.
No package source files are patched. Real transport acceptance is still required.
"""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from changebench.experiment import AgentResult, blank_usage, total_tokens, validate_usage
from changebench.live_agent import POLY_TOOLS
from changebench.runtimes import execute_public
from changebench.workspace import sanitized, validate_files

ROOT = Path(os.getenv('CHANGEBENCH_CODEX_RUNTIME', str(Path.home()/'.cache/changebench-codex')))
CATALOG = Path(__file__).with_name('data')/'codex-model-catalog.json' 
MODEL = 'gpt-6.1-sol'
DISABLED = (
    'apps', 'plugins', 'multi_agent', 'shell_tool', 'unified_exec',
    'browser_use', 'browser_use_external', 'computer_use', 'view_image',
    'image_generation', 'sleep_tool', 'hooks', 'memories',
    'unbounded_connection_retries', 'skill_search',
)


class Server:
    def __init__(self, cwd: Path, log_path: Path, deadline: float):
        self.deadline = deadline
        self.stderr = log_path.open('w')
        argv = [shutil.which('codex') or '/opt/codex/bin/codex', 'app-server', '--stdio']
        for flag in DISABLED:
            argv += ['--disable', flag]
        argv += ['--enable', 'code_mode_host', '--enable', 'skip_host_skill_discovery']
        config = {
            'sqlite_home': str(ROOT / 'codex-state'),
            'log_dir': str(ROOT / 'codex-logs'),
            'history.persistence': 'none', 'project_doc_max_bytes': 0,
            'web_search': 'disabled', 'model_reasoning_effort': 'medium',
            'model_catalog_json': str(CATALOG),
            'include_apps_instructions': False,
            'tools.update_plan.enabled': False,
            'model_provider': 'changebench_apizn',
            'model_providers.changebench_apizn.name': 'ChangeBench APIZN transport',
            'model_providers.changebench_apizn.base_url': 'https://ai.apizn.com/v1',
            'model_providers.changebench_apizn.env_key': 'APIZN_API_KEY',
            'model_providers.changebench_apizn.requires_openai_auth': False,
            'model_providers.changebench_apizn.wire_api': 'responses',
            'model_providers.changebench_apizn.supports_websockets': False,
            'model_providers.changebench_apizn.request_max_retries': 0,
            'model_providers.changebench_apizn.stream_max_retries': 0,
        }
        for key, value in config.items():
            argv += ['-c', key + '=' + json.dumps(value)]
        self.process = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, start_new_session=True,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b''

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        self.process.stdin.flush()

    def receive(self):
        while b'\n' not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Frozen wall-time budget exhausted')
            if not self.selector.select(min(remaining, 1)):
                if self.process.poll() is not None:
                    raise RuntimeError('Codex app-server exited; see trusted-worker stderr')
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('Codex app-server closed its output')
            self.buffer += chunk
            if len(self.buffer) > 4_000_000:
                raise RuntimeError('Codex protocol item exceeds bounded transport size')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def rpc(self, call_id, method, params):
        self.send({'id': call_id, 'method': method, 'params': params})
        while True:
            message = self.receive()
            if message.get('id') == call_id and ('result' in message or 'error' in message):
                if 'error' in message:
                    raise RuntimeError('Codex ' + method + ': ' + json.dumps(message['error']))
                return message['result']
            if 'id' in message and 'method' in message:
                raise RuntimeError('Unexpected server request during initialization')

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()
        self.stderr.close()


def start_thread(server, request):
    server.rpc(1, 'initialize', {
        'clientInfo': {'name': 'changebench_codex_pilot', 'version': '0.1.0'},
        'capabilities': {'experimentalApi': True},
    })
    server.send({'method': 'initialized', 'params': {}})
    tools = [{'type': 'function', 'name': t['name'], 'description': t['description'],
              'inputSchema': t['parameters']} for t in POLY_TOOLS]
    result = server.rpc(2, 'thread/start', {
        'model': request['model_id'], 'allowProviderModelFallback': False,
        'cwd': str(ROOT / 'cli-probe'), 'ephemeral': True,
        'environments': [], 'runtimeWorkspaceRoots': [],
        'dynamicTools': tools, 'approvalPolicy': 'never', 'sandbox': 'danger-full-access',
        'developerInstructions': request['instructions'],
        'experimentalRawEvents': True,
        'config': {'project_doc_max_bytes': 0, 'model_reasoning_effort': 'medium'},
    })
    if result['model'] != request['model_id']:
        raise RuntimeError('Codex selected a different model')
    if result.get('instructionSources'):
        raise RuntimeError('Unexpected host instruction sources: refusing contaminated pilot')
    return result


class PublicWorkspace:
    def __init__(self, request, deadline):
        if request.get('protocol') != 'poly-jsonl':
            raise ValueError('This pilot adapter supports only poly tasks')
        self.request = request
        self.deadline = deadline
        self.files = validate_files(request['files'])
        self.checkpoint()
        self.skills = []
        self.verifications = []

    def checkpoint(self):
        if self.request.get('checkpoint_path'):
            from changebench.experiment import write_json
            write_json(Path(self.request['checkpoint_path']),self.files)

    def dispatch(self, name, args, call_id):
        if name == 'list_files':
            return sorted(self.files)
        if name == 'read_file':
            return self.files[args['path']]
        if name == 'write_file':
            updated = validate_files({**self.files, args['path']: args['content']})
            if sanitized(updated) != updated:
                raise ValueError('Experiment control paths are not editable')
            self.files = updated
            self.checkpoint()
            return 'written'
        if name == 'delete_file':
            del self.files[args['path']]
            self.checkpoint()
            return 'deleted'
        if name == 'read_skill':
            entry = next((s for s in self.request['skill_catalog'] if s['name'] == args['name']), None)
            if entry is None:
                raise ValueError('Skill unavailable')
            self.skills.append({'name': entry['name'], 'call_id': call_id})
            return self.request['skill_files'][entry['path']]
        if name in ('run_project', 'test_project'):
            remaining = self.deadline - time.monotonic()
            if remaining <= 1:
                raise TimeoutError('Frozen wall-time budget exhausted')
            result = execute_public(
                self.files, args.get('stdin', ''), self.request['language'],
                self.request['tool_image'], timeout=min(120, remaining),
                action='test' if name == 'test_project' else 'run',
            )
            self.verifications.append({
                'language': self.request['language'], 'status': result['status'],
                'returncode': result['returncode'],
                'stdout': result.get('stdout', '')[:4096],
            })
            return result
        raise ValueError('Unknown tool')


def measured_usage(completions, tool_calls):
    usage = blank_usage()
    usage['tool_calls'] = tool_calls
    if not completions or any(c.get('usage') is None for c in completions):
        return usage
    mapping = {'input_tokens': 'inputTokens', 'output_tokens': 'outputTokens',
               'cached_input_tokens': 'cachedInputTokens', 'reasoning_tokens': 'reasoningOutputTokens'}
    for target, source in mapping.items():
        values = [c['usage'].get(source) for c in completions]
        usage[target] = sum(values) if all(type(v) is int and v >= 0 for v in values) else None
    return validate_usage(usage)


class CodexAdapter:
    model_id = MODEL

    def __init__(self, receipts):
        ROOT.mkdir(parents=True,exist_ok=True)
        (ROOT/'cli-probe').mkdir(exist_ok=True)
        self.receipts = Path(receipts)
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.config = {
            'model_id': self.model_id, 'transport': 'codex-cli-app-server',
            'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'settings': {'reasoning_effort': 'medium'},
            'tool_counting': 'Unique raw function/custom calls plus nested dynamic RPC calls; deduplicate call IDs',
            'native_environment_access': False,
            'provider_base_url': 'https://ai.apizn.com/v1',
            'credential_variable_name': 'APIZN_API_KEY',
            'cli_sandbox': 'danger-full-access',
            'candidate_execution': 'offline Docker via controlled tools',
            'acceptance': 'experimental; real tool/prompt isolation gate still required',
        }

    def run(self, request):
        start = time.monotonic()
        deadline = start + request['budget']['wall_seconds']
        workspace = PublicWorkspace(request, deadline)
        completions = []
        tool_events = []
        response_ids, tool_ids = set(), set()
        status, error, thread_id = 'infra_error', None, None
        log_id = str(time.time_ns()) + '-' + request['task_key'] + '-' + request['stage']
        server = None
        try:
            with tempfile.TemporaryDirectory(prefix='changebench-codex-') as temp:
                server = Server(Path(temp), self.receipts / (log_id + '.stderr'), deadline)
                thread = start_thread(server, request)
                thread_id = thread['thread']['id']
                catalog = request.get('skill_catalog', [])
                prompt = request['public_task'] + '\n\nAvailable project paths:\n' + '\n'.join(sorted(workspace.files))
                prompt += '\n\nAvailable skills (load with read_skill):\n' + json.dumps(catalog)
                server.rpc(3, 'turn/start', {
                    'threadId': thread_id, 'model': self.model_id, 'effort': 'medium',
                    'environments': [], 'approvalPolicy': 'never',
                    'input': [{'type': 'text', 'text': prompt}],
                })
                while True:
                    message = server.receive()
                    method, params = message.get('method', ''), message.get('params', {})
                    if method == 'rawResponse/completed':
                        if params['responseId'] not in response_ids:
                            response_ids.add(params['responseId'])
                            completions.append({k: params.get(k) for k in ('responseId', 'usage')})
                    elif method == 'rawResponseItem/completed':
                        item = params.get('item', {})
                        if item.get('type') in ('function_call', 'custom_tool_call'):
                            tool_ids.add(item.get('call_id', item.get('id', str(len(tool_ids)))))
                    elif method == 'item/tool/call':
                        call_id, name, args = params['callId'], params['tool'], params['arguments']
                        tool_ids.add(call_id)
                        usage = measured_usage(completions, len(tool_ids))
                        if len(tool_ids) > request['budget']['tool_calls'] or (total_tokens(usage) or 0) >= request['budget']['total_tokens']:
                            status = 'budget_exhausted'
                            break
                        try:
                            result = workspace.dispatch(name, args, call_id)
                            tool_ok = True
                        except (ValueError, KeyError, TypeError) as exc:
                            result = {'error': str(exc)}
                            tool_ok = False
                        tool_events.append({'name': name, 'call_id': call_id,
                                            'succeeded': tool_ok,
                                            'result_sha256': hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest(),
                                            'arguments_sha256': hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()})
                        server.send({'id': message['id'], 'result': {
                            'contentItems': [{'type': 'inputText', 'text': json.dumps(result)}],
                            'success': True,
                        }})
                    elif method == 'turn/completed':
                        turn = params['turn']
                        if turn['status'] == 'completed':
                            status = 'completed'
                        else:
                            error = json.dumps(turn.get('error'))
                        break
                    elif method == 'error':
                        raise RuntimeError('Codex transport error; no retry: ' + json.dumps(params))
                    elif 'id' in message and 'method' in message:
                        raise RuntimeError('Unexpected native tool/approval request: ' + method)
                    usage = measured_usage(completions, len(tool_ids))
                    if len(tool_ids) > request['budget']['tool_calls'] or (total_tokens(usage) or 0) > request['budget']['total_tokens']:
                        status = 'budget_exhausted'
                        break
        except TimeoutError as exc:
            status, error = 'budget_exhausted', str(exc)
        except (RuntimeError, OSError, KeyError, ValueError) as exc:
            status, error = 'infra_error', str(exc)
        finally:
            if server is not None:
                server.close()
        usage = measured_usage(completions, len(tool_ids))
        known_usage=dict(usage)
        if status == 'infra_error' or error is not None:
            for k in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens'):
                usage[k] = None
        if status == 'completed' and total_tokens(usage) is None:
            status, error = 'infra_error', 'Missing exact upstream usage; refusing valid cost comparison'
        metadata = {
            'model_id': self.model_id, 'resolved_snapshot': None, 'thread_id': thread_id,
            'skill_events': workspace.skills, 'verification_events': workspace.verifications,
            'tool_events': tool_events,
            'api_calls': completions, 'seed_supported': False,
            'known_usage': known_usage, 'usage_complete': error is None and status != 'infra_error',
            'adapter_wall_seconds': time.monotonic() - start, 'error': error,
        }
        receipt_path=self.receipts / (log_id + '.json')
        receipt_path.write_text(json.dumps({
            'status': status, 'usage': usage, 'metadata': metadata, 'adapter_config': self.config,
        }, indent=2) + '\n')
        metadata['receipt_path']=str(receipt_path)
        return AgentResult(workspace.files, status, usage, metadata, 'live_model')
