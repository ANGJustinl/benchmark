import json
import time
from pathlib import Path

import pytest

from changebench.design import make_plan
from changebench.experiment import make_request
from changebench.policy import DESIGN
from changebench.poly_fixtures import initial_files
from changebench.codex_adapter import PublicWorkspace, measured_usage, start_thread


def request(condition='A', stage='initial'):
    trial = next(t for t in make_plan(1, tasks=('settings-python',))['trials'] if t['condition'] == condition)
    return make_request(trial, stage, initial_files('settings-python'), 'gpt-6.1-sol')


@pytest.mark.parametrize('condition,available', [('A', False), ('B', False), ('C', True)])
def test_only_c_can_load_skill(condition, available):
    r = request(condition)
    w = PublicWorkspace(r, time.monotonic() + 10)
    if available:
        assert DESIGN in w.dispatch('read_skill', {'name': 'design-check'}, 'call1')
        assert w.skills == [{'name': 'design-check', 'call_id': 'call1'}]
    else:
        with pytest.raises(ValueError, match='unavailable'):
            w.dispatch('read_skill', {'name': 'design-check'}, 'call1')


@pytest.mark.parametrize('condition', ['A', 'B', 'C'])
def test_maintenance_is_neutral(condition):
    r = request(condition, 'extension')
    assert r['skill_catalog'] == []
    assert r['skill_files'] == {}
    assert DESIGN not in r['instructions']


def test_file_reads_cannot_access_host_or_hidden_files(tmp_path):
    secret = tmp_path / 'host-only.txt'
    secret.write_text('not part of public snapshot')
    w = PublicWorkspace(request(), time.monotonic() + 10)
    for name in [str(secret), '../../changebench/poly_catalog.py']:
        with pytest.raises(KeyError):
            w.dispatch('read_file', {'path': name}, 'call1')
    with pytest.raises(ValueError):
        w.dispatch('write_file', {'path': '../escape.py', 'content': 'bad'}, 'call2')
    with pytest.raises(ValueError):
        w.dispatch('write_file', {'path': 'AGENTS.md', 'content': 'bad'}, 'call3')
    assert w.files == initial_files('settings-python')


def test_native_execution_and_shell_are_not_dispatchable():
    w = PublicWorkspace(request(), time.monotonic() + 10)
    for name in ['exec_command', 'shell', 'run_python', 'read_hidden_tests']:
        with pytest.raises(ValueError, match='Unknown tool'):
            w.dispatch(name, {}, 'call1')


def test_usage_is_exact_and_subsets_are_not_double_counted():
    completions = [{'usage': {'inputTokens': 100, 'outputTokens': 20, 'cachedInputTokens': 50, 'reasoningOutputTokens': 10}},
                   {'usage': {'inputTokens': 200, 'outputTokens': 40, 'cachedInputTokens': 100, 'reasoningOutputTokens': 30}}]
    assert measured_usage(completions, 7) == {
        'input_tokens': 300, 'output_tokens': 60, 'cached_input_tokens': 150,
        'reasoning_tokens': 40, 'tool_calls': 7,
    }
    completions[0]['usage']['cachedInputTokens'] = 500
    with pytest.raises(ValueError):
        measured_usage(completions, 7)


@pytest.mark.parametrize('completions', [[], [{'usage': None}], [{'usage': {'inputTokens': 100}}]])
def test_missing_usage_stays_unknown(completions):
    usage = measured_usage(completions, 2)
    assert usage['output_tokens'] is None
    assert usage['tool_calls'] == 2


class FakeServer:
    def __init__(self, contaminated=False, wrong_model=False):
        self.messages = []
        self.contaminated = contaminated
        self.wrong_model = wrong_model

    def rpc(self, call_id, method, params):
        self.messages.append((method, params))
        if method == 'thread/start':
            return {
                'model': 'wrong' if self.wrong_model else 'gpt-6.1-sol',
                'instructionSources': ['/hidden/AGENTS.md'] if self.contaminated else [],
                'thread': {'id': 'fresh-thread'},
            }
        return {}

    def send(self, message):
        self.messages.append(('notification', message))


def test_initialization_disables_environment_and_uses_only_public_tool_schemas():
    s = FakeServer()
    start_thread(s, request('C'))
    params = next(p for method, p in s.messages if method == 'thread/start')
    assert params['environments'] == []
    assert params['runtimeWorkspaceRoots'] == []
    assert params['ephemeral'] is True
    assert params['sandbox'] == 'danger-full-access'
    assert params['allowProviderModelFallback'] is False
    assert 'run_project' in {t['name'] for t in params['dynamicTools']}
    assert 'exec_command' not in {t['name'] for t in params['dynamicTools']}
    assert DESIGN not in json.dumps(params)


@pytest.mark.parametrize('contaminated,wrong_model', [(True, False), (False, True)])
def test_unexpected_instructions_or_model_fail_closed(contaminated, wrong_model):
    with pytest.raises(RuntimeError):
        start_thread(FakeServer(contaminated, wrong_model), request())
