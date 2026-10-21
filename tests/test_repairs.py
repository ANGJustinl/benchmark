import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

import pytest

from changebench.design import make_plan, validate_plan, DEFAULT_BUDGETS
from changebench.execution import run_bounded, DOCKER_READY, docker_binary, evaluate
from changebench.experiment import AgentResult, ReplayAdapter, blank_usage, run_experiment, write_json
from changebench.fairness import audit_run
from changebench.fixtures import reference_files
from changebench.upgrade import create_seed, check_upgrade, transfer, StateFormatError


LIMITS={'random_count':18,'execution_seconds':8,'compile_seconds':120,'startup_seconds':30}


def bounded(tmp_path,code,timeout=.15,startup_timeout=2):
    return run_bounded([sys.executable,'-S','-c',code],b'payload\n',tmp_path,
                       timeout=timeout,ready_prefix=DOCKER_READY,startup_timeout=startup_timeout)


def ready_code(prefix=''):
    return prefix+'import sys,time;sys.stdout.write("CHANGEBENCH_READY_V1\\n");sys.stdout.flush();sys.stdin.readline();'


def test_slow_startup_has_separate_deadline(tmp_path):
    result=bounded(tmp_path,ready_code('import time;time.sleep(.3);')+'print(sys.stdin.readline().strip())')
    assert result.status=='ok' and result.stdout=='payload\n'
    assert result.startup_seconds>=.3 and result.execution_seconds<.15


def test_missing_handshake_is_startup_timeout(tmp_path):
    result=bounded(tmp_path,'import time;time.sleep(5)',startup_timeout=.05)
    assert result.status=='startup_timeout' and result.ready is False
    assert result.execution_seconds is None


def test_candidate_timeout_after_handshake(tmp_path):
    result=bounded(tmp_path,ready_code()+'time.sleep(5)',timeout=.05)
    assert result.status=='timeout' and result.ready is True


def test_second_marker_cannot_reset_deadline(tmp_path):
    result=bounded(tmp_path,ready_code()+'\nwhile True:\n print("CHANGEBENCH_READY_V1",flush=True);time.sleep(.01)',timeout=.08)
    assert result.status=='timeout' and result.stdout.startswith('CHANGEBENCH_READY_V1\n')
    assert result.execution_seconds<1


@pytest.mark.parametrize('code,status,ready',[
    ('import sys;sys.exit(127)','infra_error',False),
    (ready_code()+'sys.exit(127)','runtime_error',True),
    (ready_code()+'sys.exit(125)','runtime_error',True),
])
def test_exit_classification_requires_startup_evidence(tmp_path,code,status,ready):
    result=bounded(tmp_path,code)
    assert result.status==status and result.ready is ready


def test_resolved_docker_works_outside_clean_path(tmp_path,monkeypatch):
    binary=tmp_path/'docker';binary.write_text('#!/bin/sh\nprintf resolved\n');binary.chmod(0o755)
    monkeypatch.delenv('CHANGEBENCH_DOCKER_EXECUTABLE',raising=False)
    monkeypatch.setenv('PATH',str(tmp_path))
    result=run_bounded([docker_binary()],b'',tmp_path)
    assert result.stdout=='resolved' and result.status=='ok'


def test_compile_and_startup_limits_reach_native_program():
    class StopProbe(Exception):pass
    with patch('changebench.poly_execution.PreparedProgram',side_effect=StopProbe) as constructor:
        with pytest.raises(StopProbe):
            evaluate({'main.ts':'export {};'},'settings-typescript','initial',backend='local',trusted_local=True,
                     compile_timeout=37,startup_timeout=9)
    assert constructor.call_args.kwargs=={'compile_timeout':37,'startup_timeout':9}


def test_custom_budget_and_evaluation_are_frozen():
    budgets=copy.deepcopy(DEFAULT_BUDGETS);budgets['1']['total_tokens']=60000
    limits={**LIMITS,'compile_seconds':37}
    plan=make_plan(1,tasks=('settings-python',),budgets=budgets,evaluation=limits,
                   budget_profile={'name':'codex-diagnostic-v1','calibration_status':'uncalibrated'})
    validate_plan(plan)
    assert plan['budgets_by_level']['1']['total_tokens']==60000
    plan['evaluation']['compile_seconds']=38
    with pytest.raises(ValueError,match='hash'):validate_plan(plan)


class Never:
    model_id='never'
    def run(self,request):raise AssertionError('Model must not be called')


def test_client_model_drift_rejected_before_call(tmp_path):
    plan=make_plan(1,tasks=('settings-python',),client_profile={'model_id':'different'})
    with pytest.raises(ValueError,match='model'):
        run_experiment(plan,Never(),Never(),tmp_path/'out',backend='local',trusted_local=True)


def test_client_artifact_drift_rejected_before_call(tmp_path):
    asset=tmp_path/'client';asset.write_text('v1')
    plan=make_plan(1,tasks=('settings-python',),client_profile={'artifacts':{str(asset):hashlib.sha256(asset.read_bytes()).hexdigest()}})
    asset.write_text('v2')
    with pytest.raises(ValueError,match='artifact changed'):
        run_experiment(plan,Never(),Never(),tmp_path/'out',backend='local',trusted_local=True)


class Partial:
    model_id='fixture-partial'
    def __init__(self,status='budget_exhausted',files=None):
        self.status=status;self.files={'unfinished.py':'# work in progress'} if files is None else files
    def run(self,request):
        return AgentResult(self.files,self.status,blank_usage(),{},'fixture')


@pytest.mark.parametrize('status',['budget_exhausted','infra_error','model_error'])
def test_noncompleted_code_is_saved_and_audited(tmp_path,status):
    adapter=Partial(status);out=tmp_path/'out'
    rows=run_experiment(make_plan(1,conditions=('A',),tasks=('settings-python',)),adapter,adapter,out,
                        backend='local',trusted_local=True)
    stage=rows[0]['stages']['initial'];artifact=stage['artifact']
    assert artifact['kind']=='partial' and not stage['success']
    assert json.loads((out/artifact['path']).read_text())==adapter.files
    assert rows[0]['stages']['local']['status']=='blocked_by_initial'
    assert audit_run(out)['integrity_passed']
    (out/artifact['path']).write_text('{}')
    with pytest.raises(ValueError,match='modified'):audit_run(out)


def test_empty_completed_workspace_is_partial_failure(tmp_path):
    adapter=Partial('completed',{})
    rows=run_experiment(make_plan(1,conditions=('A',),tasks=('settings-python',)),adapter,adapter,tmp_path/'out',
                        backend='local',trusted_local=True)
    stage=rows[0]['stages']['initial']
    assert stage['status']=='invalid_submission' and stage['artifact']['kind']=='partial'
    assert audit_run(tmp_path/'out')['integrity_passed']


def test_snapshot_survives_evaluator_exception(tmp_path):
    out=tmp_path/'out'
    with patch('changebench.experiment.evaluate',side_effect=RuntimeError('daemon lost')):
        rows=run_experiment(make_plan(1,conditions=('A',),tasks=('settings-python',)),ReplayAdapter(),ReplayAdapter(),out,
                            backend='local',trusted_local=True)
    stage=rows[0]['stages']['initial']
    assert stage['status']=='infra_error' and (out/stage['artifact']['path']).is_file()
    assert 'output_snapshot_sha256' in stage and audit_run(out)['integrity_passed']


def test_stage_progress_and_checkpoint_survive_interrupt(tmp_path):
    class Interrupted(Never):
        def run(self,request):
            write_json(Path(request['checkpoint_path']),{'main.py':'# partial'})
            raise KeyboardInterrupt()
    out=tmp_path/'out'
    with pytest.raises(KeyboardInterrupt):
        run_experiment(make_plan(1,conditions=('A',),tasks=('settings-python',)),Interrupted(),Interrupted(),out,
                        backend='local',trusted_local=True)
    assert json.loads((out/'RUN-STATE.json').read_text())['status']=='running'
    assert json.loads((out/'progress.json').read_text())['active_stage']=='initial'
    checkpoint=next((out/'checkpoints').rglob('*.json'))
    assert json.loads(checkpoint.read_text())=={'main.py':'# partial'}
    assert not (out/'RUN-SEAL.json').exists()


@pytest.mark.parametrize('family',['store','outbox'])
def test_actual_disk_handoff_both_branches_and_restart(tmp_path,family):
    out=tmp_path/'out'
    rows=run_experiment(make_plan(1,conditions=('A',),tasks=(family+'-python',),persistent_upgrade=True),
                        ReplayAdapter(),ReplayAdapter(),out,backend='local',trusted_local=True)
    stages=rows[0]['stages'];seed=stages['initial']['upgrade_seed']
    assert rows[0]['joint_success'] and audit_run(out)['integrity_passed']
    assert stages['local']['upgrade']['archive_sha256']==seed['archive_sha256']==stages['extension']['upgrade']['archive_sha256']
    assert all(stages[p]['upgrade']['restored_identical'] for p in ('local','extension'))
    (out/seed['archive_path']).write_text('{}')
    with pytest.raises(ValueError,match='modified'):audit_run(out)


def test_gate_rejects_format_break_that_passes_fresh_version(tmp_path):
    trial=make_plan(1,conditions=('A',),tasks=('store-python',))['trials'][0]
    seed=create_seed(reference_files('store-python','initial'),trial,tmp_path,'local',{},LIMITS,True)
    mutant=reference_files('store-python','local')
    mutant['main.py']=mutant['main.py'].replace("'snapshot.json'","'new-format.json'")
    assert evaluate(mutant,'store-python','local',backend='local',trusted_local=True)['success']
    result=check_upgrade(mutant,trial,'local',tmp_path,seed,'local',{},LIMITS,True)
    assert not result['success'] and result['status']=='failed'


def test_state_transfer_rejects_symlink_without_reading_target(tmp_path):
    state=tmp_path/'state';state.mkdir()
    (state/'link').symlink_to('/etc/passwd')
    with pytest.raises(StateFormatError,match='links'):
        transfer(state,{'op':'capture'},'local',None,LIMITS,True)
    transfer(state,{'op':'clear'},'local',None,LIMITS,True)
    assert list(state.iterdir())==[]


def test_completed_over_budget_cannot_reach_grader(tmp_path):
    class OverBudget(Partial):
        def run(self,request):
            return AgentResult({'main.py':'pass'},'completed',
                               {'input_tokens':30001,'output_tokens':1,'cached_input_tokens':0,
                                'reasoning_tokens':0,'tool_calls':1},{},'fixture')
    out=tmp_path/'out';adapter=OverBudget()
    with patch('changebench.experiment.evaluate',side_effect=AssertionError('Must not grade')):
        rows=run_experiment(make_plan(1,conditions=('A',),tasks=('settings-python',)),adapter,adapter,out,
                            backend='local',trusted_local=True)
    stage=rows[0]['stages']['initial']
    assert stage['status']=='budget_exhausted' and stage['budget_excess_tokens']==2
    assert stage['artifact']['kind']=='partial' and audit_run(out)['integrity_passed']


def test_recover_last_checkpoint_when_adapter_has_no_files(tmp_path):
    class Disconnected(Partial):
        def run(self,request):
            write_json(Path(request['checkpoint_path']),{'main.py':'# last confirmed write'})
            return AgentResult({},'infra_error',blank_usage(),{},'fixture')
    out=tmp_path/'out';adapter=Disconnected()
    rows=run_experiment(make_plan(1,conditions=('A',),tasks=('settings-python',)),adapter,adapter,out,
                        backend='local',trusted_local=True)
    stage=rows[0]['stages']['initial']
    assert stage['metadata']['checkpoint_recovered']
    assert json.loads((out/stage['artifact']['path']).read_text())=={'main.py':'# last confirmed write'}


def test_codex_disconnect_preserves_files_known_receipts_and_unknown_total(tmp_path,monkeypatch):
    from changebench import codex_adapter
    from changebench.experiment import make_request,total_tokens
    trial=make_plan(1,conditions=('A',),tasks=('settings-python',))['trials'][0]
    request=make_request(trial,'initial',{'main.py':'# partial'},'gpt-6.1-sol')
    class FakeServer:
        def __init__(self,*args):
            self.messages=iter([
                {'method':'rawResponse/completed','params':{'responseId':'r1','usage':
                    {'inputTokens':100,'outputTokens':20,'cachedInputTokens':80,'reasoningOutputTokens':5}}},
                {'method':'error','params':{'message':'connection closed'}}])
        def rpc(self,*args):return {}
        def receive(self):return next(self.messages)
        def close(self):pass
    monkeypatch.setattr(codex_adapter,'ROOT',tmp_path/'runtime')
    monkeypatch.setattr(codex_adapter,'Server',FakeServer)
    monkeypatch.setattr(codex_adapter,'start_thread',lambda *args:{'thread':{'id':'thread1'}})
    result=codex_adapter.CodexAdapter(tmp_path/'receipts').run(request)
    assert result.status=='infra_error' and result.files==request['files']
    assert total_tokens(result.usage) is None and total_tokens(result.metadata['known_usage'])==120
    assert result.metadata['usage_complete'] is False
    assert Path(result.metadata['receipt_path']).is_file()
