import copy
import json
import math
from pathlib import Path
from unittest.mock import patch
import pytest
from changebench.catalog import TASKS, initial_files
from changebench.design import make_plan,validate_plan,paired_bootstrap
from changebench.policy import BASE,DESIGN,SKILL,condition_payload
from changebench.experiment import (ReplayAdapter,CommandAdapter,AgentResult,blank_usage,make_request,
                                   run_experiment,total_tokens,validate_usage,write_json)
from changebench.reporting import summarize,blind_export


def test_policy_body_identical_b_c():
    b=condition_payload('B');c=condition_payload('C')
    assert b['instructions']==BASE+'\n'+DESIGN
    assert SKILL.split('---\n',2)[2].lstrip()==DESIGN
    assert DESIGN not in c['instructions']
    assert DESIGN in next(iter(c['skill_files'].values()))
    assert condition_payload('A')['skill_catalog']==[]


def test_maintenance_removes_treatment_and_future():
    plan=make_plan(1)
    requests=[]
    for condition in ('A','B','C'):
        trial=next(x for x in plan['trials'] if x['task']=='config' and x['condition']==condition)
        request=make_request(trial,'local',{**initial_files('config'),'AGENTS.md':'treatment'},'fixed-model')
        assert request['skill_catalog']==[]
        assert request['skill_files']=={}
        assert request['instructions']==BASE
        assert 'evaluation_seed' not in request
        assert 'condition' not in request
        assert 'profiles.NAME' not in request['public_task']
        assert 'AGENTS.md' not in request['files']
        requests.append(request)
    assert requests[0]==requests[1]==requests[2]


def test_plan_balanced_paired_and_frozen():
    plan=make_plan()
    assert plan==make_plan()
    assert len(plan['trials'])==36
    validate_plan(plan)
    for task in TASKS:
        for repeat in range(2):
            block=[t for t in plan['trials'] if (t['task'],t['repeat'])==(task,repeat)]
            assert {t['condition'] for t in block}=={'A','B','C'}
            assert len({t['evaluation_seed'] for t in block})==1
            assert len({t['generation_seed'] for t in block})==1
    changed=copy.deepcopy(plan);changed['trials'].pop()
    with pytest.raises(ValueError):validate_plan(changed)
    with patch('changebench.design.source_fingerprint',return_value='changed'):
        with pytest.raises(ValueError):validate_plan(plan)

@pytest.mark.parametrize('repeats',[0,-1,True,1.5])
def test_invalid_repeat_count(repeats):
    with pytest.raises(ValueError):make_plan(repeats)


def test_bootstrap_pairs_and_task_clusters():
    rows=[{'task':t,'repeat':r,'condition':c,'score':0 if c=='A' else 1}
          for t in ('t1','t2') for r in (0,1) for c in ('A','B')]
    result=paired_bootstrap(rows,'A','B','score')
    assert result['difference']==1
    assert result['ci95_exploratory']==[1,1]
    assert result['task_clusters']==2
    with pytest.raises(ValueError):paired_bootstrap(rows[:-1],'A','B','score')
    with pytest.raises(ValueError):paired_bootstrap(rows+[rows[0]],'A','B','score')


def test_usage_not_estimated_or_doublecounted():
    u=validate_usage({'input_tokens':100,'output_tokens':20,'cached_input_tokens':80,'reasoning_tokens':12,'tool_calls':3})
    assert total_tokens(u)==120
    assert total_tokens(blank_usage()) is None
    with pytest.raises(ValueError):validate_usage({'input_tokens':-1})
    with pytest.raises(ValueError):validate_usage({'input_tokens':True})
    with pytest.raises(ValueError):validate_usage({'output_tokens':5,'reasoning_tokens':6})


def test_command_adapter_protocol_and_fixture_barrier():
    plan=make_plan(1)
    request=make_request(plan['trials'][0],'initial',initial_files(plan['trials'][0]['task']),'reference-fixture')
    config={'model_id':'reference-fixture','command':['{python}','-I','-S','{repo}/examples/reference_adapter.py']}
    assert CommandAdapter(config).run(request).status=='invalid_response'
    allowed=CommandAdapter(config,allow_fixture=True).run(request)
    assert allowed.status=='completed'
    assert allowed.evidence_kind=='fixture'
    assert total_tokens(allowed.usage) is None


def test_command_adapter_no_shell():
    with pytest.raises(ValueError):CommandAdapter({'model_id':'x','command':'python evil.py'})


def test_end_to_end_replay_and_blind_export(tmp_path):
    out=tmp_path/'run'
    plan=make_plan(1)
    rows=run_experiment(plan,ReplayAdapter(),ReplayAdapter(),out,backend='local',trusted_local=True)
    assert len(rows)==18
    assert all(r['joint_success'] for r in rows)
    for r in rows:
        initial=r['stages']['initial']['output_snapshot_sha256']
        assert r['stages']['local']['input_snapshot_sha256']==initial
        assert r['stages']['extension']['input_snapshot_sha256']==initial
    summary=summarize(plan,rows)
    assert summary['evidence_kind']=='fixture'
    assert not summary['comparisons']
    assert not summary['model_ranking_supported']
    assert all(g['total_experiment_tokens'] is None for g in summary['groups'].values())
    with pytest.raises(ValueError):summarize(plan,rows[:-1])
    with pytest.raises(ValueError):summarize(plan,rows+[rows[0]])
    with pytest.raises(ValueError):run_experiment(plan,ReplayAdapter(),ReplayAdapter(),out,backend='local',trusted_local=True)
    info=blind_export(out,tmp_path/'blind')
    assert info['samples']==54
    assert not (tmp_path/'blind'/'blind-key.json').exists()
    assert (out/'blind-key.json').exists()
    assert (tmp_path/'blind'/'pairs.csv').exists()
    for p in (tmp_path/'blind').glob('sample-*'):
        assert (p/'REQUIREMENTS.md').exists()
        assert (p/'project'/'main.py').exists()


class FailingAdapter:
    model_id='test-fixture'
    def __init__(self):self.calls=[]
    def run(self,request):
        self.calls.append(request)
        return AgentResult({'main.py':'pass'},'completed',blank_usage(),{},'fixture')


def test_initial_failure_remains_in_denominator(tmp_path):
    builder=FailingAdapter();maintainer=FailingAdapter();plan=make_plan(1,conditions=('A',))
    rows=run_experiment(plan,builder,maintainer,tmp_path/'failure',backend='local',trusted_local=True)
    assert len(builder.calls)==6 and len(maintainer.calls)==0
    s=summarize(plan,rows)['groups']['A']
    assert s['planned_trials']==6 and s['joint_success_rate']==0
    assert all(r['stages']['local']['status']=='blocked_by_initial' for r in rows)


def test_infra_is_not_reported_as_model_failure(tmp_path):
    class Infra(FailingAdapter):
        def run(self,request):return AgentResult({},'infra_error',blank_usage(),{},'live_model')
    plan=make_plan(1,conditions=('A',))
    rows=run_experiment(plan,Infra(),Infra(),tmp_path/'infra',backend='local',trusted_local=True)
    s=summarize(plan,rows)['groups']['A']
    assert s['infrastructure_invalid_trials']==6
    assert s['joint_success_rate'] is None


def test_live_cost_includes_failed_attempts(tmp_path):
    plan=make_plan(1,conditions=('A',))
    class CostAdapter(FailingAdapter):
        def run(self,request):
            return AgentResult({'main.py':'pass'},'completed',validate_usage({'input_tokens':80,'output_tokens':20}),{},'live_model')
    rows=run_experiment(plan,CostAdapter(),CostAdapter(),tmp_path/'cost',backend='local',trusted_local=True)
    s=summarize(plan,rows)['groups']['A']
    assert s['total_experiment_tokens']==600
    assert s['weighted_lifecycle_tokens_all_attempts']==600
    assert s['tokens_per_successful_weighted_chain'] is None
    assert s['joint_success_rate']==0


def test_lifecycle_weighting_and_cached_result_guard(tmp_path):
    plan=make_plan(1,conditions=('A',))
    rows=run_experiment(plan,ReplayAdapter(),ReplayAdapter(),tmp_path/'weighted',backend='local',trusted_local=True)
    for row in rows:
        row['evidence_kind']='live_model'
        for stage,cost in [('initial',10),('local',20),('extension',40)]:
            row['stages'][stage]['usage']=validate_usage({'input_tokens':cost,'output_tokens':0})
    s=summarize(plan,rows)['groups']['A']
    assert s['total_experiment_tokens']==420
    assert s['weighted_lifecycle_tokens_all_attempts']==240
    assert s['tokens_per_successful_weighted_chain']==40
    rows[0]['joint_success']=False
    with pytest.raises(ValueError):summarize(plan,rows)
