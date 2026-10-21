import copy
import json
from collections import Counter,defaultdict
from pathlib import Path
from unittest.mock import patch
import pytest

from changebench.poly_catalog import TASKS,STAGES,FAMILIES,LANGUAGES,metadata,public_spec,suites,suite_fingerprint,oracle,reference_state
from changebench.poly_fixtures import reference_files,initial_files
from changebench.poly_execution import evaluate
from changebench.poly_validation import semantic_mutant,grade_fingerprint
from changebench.design import make_plan,validate_plan,paired_bootstrap,DEFAULT_BUDGETS
from changebench.experiment import ReplayAdapter,run_experiment,make_request
from changebench.fairness import audit_run,seal_run,sensitivity_diagnostics
from changebench.reporting import summarize
from changebench.runtimes import PreparedProgram,docker_command,runtime_manifest
from changebench.live_agent import run_request,POLY_TOOLS


@pytest.mark.parametrize('task',TASKS)
@pytest.mark.parametrize('stage',STAGES)
def test_native_positive_compiles_and_runs(task,stage):
    result=evaluate(reference_files(task,stage),task,stage,backend='local',trusted_local=True,random_count=3)
    assert result['success'],result
    assert result['total']>0
    if metadata(task)['language']!='python':assert result['compilation']['status']=='ok'


@pytest.mark.parametrize('task',TASKS)
def test_real_semantic_faults_are_not_compile_failures(task):
    files,fault=semantic_mutant(task)
    result=evaluate(files,task,'initial',backend='local',trusted_local=True,random_count=3)
    assert not result['success'],fault
    assert result['status']=='failed',result


@pytest.mark.parametrize('family',FAMILIES)
@pytest.mark.parametrize('stage',STAGES)
def test_cross_language_oracle_contract_identical(family,stage):
    hashes=[suite_fingerprint(suites(f'{family}-{lang}',stage,999,4)) for lang in LANGUAGES]
    assert len(set(hashes))==1


@pytest.mark.parametrize('task',TASKS)
def test_public_initial_does_not_include_future_or_hidden_material(task):
    initial=public_spec(task,'initial')
    assert 'CHANGE REQUEST' not in initial
    files=initial_files(task)
    assert not any(word in str(files) for word in ('poly_catalog','poly_validation','evaluation_seed','suite_sha256','oracle('))
    assert metadata(task)['language']!='typescript' or 'node-shim.d.ts' in files
    baseline=suites(task,'initial',811,4)
    for stage in ('local','extension'):
        assert [s for s in suites(task,stage,811,4) if s.origin=='initial']==baseline


def test_hand_derived_oracle_money_and_post_aggregation():
    assert oracle('invoice','initial',{'items':[{'price':199,'qty':3}],'bps':3333})[0]=={'subtotal':597,'discount':198,'total':399}
    assert oracle('events','local',{'records':[{'customer':'a','cents':4},{'customer':'b','cents':9},{'customer':'a','cents':4}],'min_cents':7})[0]=={'rows':[{'customer':'a','cents':8},{'customer':'b','cents':9}],'total':17}


def test_hand_derived_oracle_rollback_receipts():
    transfer={'op':'transfer','id':'x','from':'a','to':'b','amount':3}
    request={'balances':{'a':5,'b':0},'actions':[{'op':'batch','items':[transfer,{**transfer,'id':'y','amount':9}]},transfer]}
    assert oracle('ledger','extension',request)[0]=={'balances':{'a':2,'b':3},'results':['rejected','applied']}


def test_hand_derived_oracle_stale_ack():
    state=reference_state('outbox')
    seq=[{'op':'enqueue','id':'a','payload':'x'},{'op':'claim','now':0,'lease':10},
         {'op':'claim','now':10,'lease':10},{'op':'ack','id':'a','token':1},{'op':'ack','id':'a','token':2}]
    got=[]
    for request in seq:
        value,state=oracle('outbox','extension',request,state);got.append(value)
    assert got==[{'status':'queued'},{'id':'a','payload':'x','token':1,'until':10},
                 {'id':'a','payload':'x','token':2,'until':20},{'status':'stale'},{'status':'acked'}]


def test_metamorphic_invoice_order_and_ledger_total():
    items=[{'price':3,'qty':2},{'price':17,'qty':3},{'price':99,'qty':2}]
    assert oracle('invoice','extension',{'items':items,'tax_bps':73})==oracle('invoice','extension',{'items':items[::-1],'tax_bps':73})
    for scenario in suites('ledger-python','initial',713,60):
        original=scenario.sessions[0][0]
        value=scenario.expected[0]
        if 'balances' in value:assert sum(value['balances'].values())==sum(original['balances'].values())


def test_counterbalance_exact_with_three_repeats_per_stratum():
    plan=make_plan(3,tasks=TASKS);validate_plan(plan)
    assert len(plan['trials'])==216
    groups=defaultdict(Counter)
    for trial in plan['trials']:
        groups[(trial['track'],trial['language'],trial['level'])][(trial['condition'],trial['block_position'])]+=1
    assert len(groups)==12
    for counts in groups.values():assert set(counts.values())=={2} and len(counts)==9


def test_language_ports_share_seed_but_cluster_as_family():
    plan=make_plan(1,tasks=TASKS)
    for family in FAMILIES:
        trials=[t for t in plan['trials'] if t['family']==family]
        assert len({t['evaluation_seed'] for t in trials})==1
    rows=[{**t,'x':float(t['condition']=='B')} for t in plan['trials'] if t['condition'] in ('A','B')]
    interval=paired_bootstrap(rows,'A','B','x')
    assert interval['task_clusters']==8
    assert interval['difference']==1


@pytest.mark.parametrize('field,value',[('language','rust'),('level',0),('block_position',7),('evaluation_seed',-1)])
def test_rehashed_tampering_still_fails_plan_reconstruction(field,value):
    import hashlib
    plan=make_plan(1,tasks=TASKS);plan['trials'][0][field]=value
    plan.pop('plan_sha256');plan['plan_sha256']=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    with pytest.raises(ValueError):validate_plan(plan)


def test_neutral_maintenance_identical_and_language_aware():
    plan=make_plan(1,tasks=('ledger-go',))
    requests=[make_request(t,'extension',reference_files('ledger-go','initial'),'fixed') for t in plan['trials']]
    assert requests[0]==requests[1]==requests[2]
    assert requests[0]['language']=='go'
    assert requests[0]['protocol']=='poly-jsonl'
    assert 'Python' not in requests[0]['instructions']
    assert not requests[0]['skill_files'] and 'evaluation_seed' not in requests[0]


def test_poly_live_worker_keeps_same_native_tools_for_arms():
    from changebench.policy import DESIGN
    for arm in ('A','B','C'):
        trial=next(t for t in make_plan(1,tasks=('invoice-typescript',))['trials'] if t['condition']==arm)
        request=make_request(trial,'initial',initial_files('invoice-typescript'),'test-model')
        captured=[]
        def post(payload,timeout):
            captured.append(copy.deepcopy(payload))
            return {'model':'test-model','status':'completed','output':[], 'usage':{'input_tokens':5,'output_tokens':1}}
        response=run_request(request,post=post)
        assert captured[0]['tools']==POLY_TOOLS
        assert 'run_python' not in {x['name'] for x in captured[0]['tools']}
        if arm=='C':assert DESIGN not in json.dumps(captured[0])
        assert response['status']=='completed'


def test_model_snapshot_drift_is_infrastructure_error():
    trial=make_plan(1,tasks=('invoice-python',))['trials'][0]
    request=make_request(trial,'initial',initial_files('invoice-python'),'pinned')
    result=run_request(request,post=lambda p,t:{'model':'other','status':'completed','output':[], 'usage':{'input_tokens':5,'output_tokens':1}})
    assert result['status']=='infra_error'
    assert result['usage']['input_tokens'] is None


def test_native_sandbox_requires_optin_and_never_falls_back():
    with pytest.raises(ValueError):PreparedProgram({'main.py':'pass'},'python',backend='local')
    with patch('changebench.runtimes.shutil.which',return_value=None):
        with pytest.raises(RuntimeError):PreparedProgram({'main.go':'package main'},'go')


def test_native_docker_flags_and_hash_only_images(tmp_path):
    for lang in LANGUAGES:
        argv=docker_command(tmp_path,tmp_path,'sha256:'+'a'*64,'test',['echo','ok'],tmp_path)
        for flag in ('--network=none','--read-only','--cap-drop=ALL','--memory-swap=2g','--user=65534:65534'):
            assert flag in argv
        assert not any('OPENAI_API_KEY' in s or 'docker.sock' in s for s in argv)
    with pytest.raises(ValueError):docker_command(tmp_path,tmp_path,'node:latest','test',['node'])


@pytest.mark.parametrize('language',('typescript','go'))
def test_invalid_native_types_cannot_bypass_compiler(language):
    task='invoice-'+language;files=reference_files(task,'initial')
    if language=='typescript':files['logic.ts']+='\nconst invalidType: string = 1;\n'
    else:files['main.go']+='\nvar invalidType string = 1\n'
    result=evaluate(files,task,'initial',backend='local',trusted_local=True)
    assert result['status']=='compile_error' and not result['success']


@pytest.mark.parametrize('language',LANGUAGES)
def test_empty_program_cannot_claim_success(language):
    task='settings-'+language
    files={'python':{'main.py':'pass\n'},'typescript':{'main.ts':'export {};\n'},'go':{'main.go':'package main\nfunc main(){}\n'}}[language]
    result=evaluate(files,task,'initial',backend='local',trusted_local=True)
    assert result['status']=='protocol_error' and not result['success']


@pytest.fixture
def poly_run(tmp_path):
    plan=make_plan(1,tasks=('invoice-python','store-go'))
    root=tmp_path/'run'
    rows=run_experiment(plan,ReplayAdapter(),ReplayAdapter(),root,backend='local',trusted_local=True)
    return root,plan,rows


def test_run_seal_neutral_forks_and_fixture_not_sensitivity(poly_run):
    root,plan,rows=poly_run
    audit=audit_run(root)
    assert audit['integrity_passed'] and not audit['live_model_evidence']
    assert audit['sealed_snapshots']==18
    assert sensitivity_diagnostics(plan,rows)['status']=='not_measured'
    result=summarize(plan,rows)
    assert len(result['strata'])==6
    assert result['comparisons']==[]


def test_modified_artifact_and_forged_counts_are_rejected(poly_run):
    root,plan,rows=poly_run
    path=next((root/'snapshots').glob('*/*.json'));path.write_text('{}')
    with pytest.raises(ValueError):audit_run(root)
    rows[0]['stages']['initial']['evaluation']['passed']-=1
    with pytest.raises(ValueError):summarize(plan,rows)


def test_treatment_budget_override_is_blocked_before_invocation(tmp_path):
    class Never:
        model_id='never'
        def run(self,request):raise AssertionError('must not call')
    with pytest.raises(ValueError):run_experiment(make_plan(1,tasks=('invoice-python',)),Never(),Never(),tmp_path/'x',
        backend='local',trusted_local=True,budget={'wall_seconds':1,'total_tokens':1,'tool_calls':1})


@pytest.mark.parametrize('language',LANGUAGES)
def test_prototype_keys_preserve_receipts_and_profiles(language):
    from changebench.runtimes import execute_public
    def run(family,stage,rows):
        r=execute_public(reference_files(family+'-'+language,stage),''.join(json.dumps(d)+'\n' for d in rows),language,None,backend='local',trusted_local=True)
        assert r['status']=='ok',r
        return [json.loads(v) for v in r['stdout'].splitlines()]
    assert run('settings','extension',[{'config':{'profiles':{}},'profile':'__proto__'}])==[{'error':'invalid'}]
    p={'op':'put','id':'__proto__','key':'__proto__','value':'safe'}
    assert run('store','initial',[p,p,{'op':'get','key':'__proto__'}])==[{'status':'stored','version':1},{'status':'stored','version':1},{'value':'safe','version':1}]


@pytest.mark.parametrize('language',LANGUAGES)
def test_caller_authored_native_tests(language):
    from changebench.runtimes import execute_public
    files=reference_files('invoice-'+language,'initial')
    if language=='python':files['test_candidate.py']='import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n'
    elif language=='typescript':files['candidate.test.ts']='const test=require("node:test");const assert=require("node:assert/strict");test("ok",()=>assert.equal(2+2,4));\n'
    else:files['candidate_test.go']='package main\nimport "testing"\nfunc TestCandidate(t *testing.T){if 2+2!=4{t.Fatal("broken")}}\n'
    r=execute_public(files,'',language,None,backend='local',trusted_local=True,action='test')
    assert r['status']=='ok',r


def test_preflight_probe_sources_run_in_local_native_tools():
    from changebench.runtimes import preflight_runtime_images
    def local(files,language,**kw):return PreparedProgram(files,language,backend='local',trusted_local=True)
    with patch('changebench.runtimes.PreparedProgram',side_effect=local):
        checks=preflight_runtime_images({lang:'sha256:'+'a'*64 for lang in LANGUAGES})
    assert set(checks)==set(LANGUAGES)


def test_failed_runtime_preflight_blocks_model_calls(tmp_path):
    class Never:
        model_id='never'
        def run(self,request):raise AssertionError('must not call')
    with patch('shutil.which',return_value='/fake/docker'),patch('changebench.experiment.resolve_docker_image',return_value='sha256:'+'a'*64),patch('changebench.experiment.preflight_runtime_images',side_effect=RuntimeError('broken image')):
        with pytest.raises(RuntimeError,match='broken image'):
            run_experiment(make_plan(1,tasks=('invoice-go',)),Never(),Never(),tmp_path/'never',image={'go':'fake'})


def test_resealed_missing_case_cannot_claim_success(poly_run):
    from changebench.fairness import seal_run
    root,plan,rows=poly_run
    ev=rows[0]['stages']['initial']['evaluation']
    removed=ev['cases'].pop();ev['passed']-=1;ev['total']-=1
    if removed['origin']=='initial':ev['regression_total']-=1;ev['regression_passed']-=1
    (root/'records.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));seal_run(root)
    with pytest.raises(ValueError,match='observation'):audit_run(root)


@pytest.mark.parametrize('task',[t for t in TASKS if metadata(t)['kind']=='brownfield'])
def test_public_brownfield_seed_is_native_and_does_not_reveal_future(task):
    texts='\n'.join(initial_files(task).values())
    assert 'STAGE' not in texts and 'const stage ' not in texts
    forbidden={'events':['min_cents','currency','schema'], 'routing':['max_attempts','sms'],
       'ledger':['reverse','batch'], 'scheduler':['max_parallel','blocked','failed'],
       'store':['expected','batch'], 'outbox':['claim','cancel','token','lease','ack']}
    # Check code, not generic README wording or identifiers in common validators.
    code='\n'.join(v for k,v in initial_files(task).items() if not k.endswith('.md'))
    import re
    for word in forbidden[metadata(task)['family']]:
        assert not re.search(r'(?<![A-Za-z_])'+word+r'(?![A-Za-z_])',code),word
    r=evaluate(initial_files(task),task,'initial',backend='local',trusted_local=True,random_count=2)
    assert r['status']=='failed' and r['passed']>0 and not r['success'],r
