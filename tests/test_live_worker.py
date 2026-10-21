import copy
import json
import urllib.error
from unittest.mock import patch
import pytest
from changebench.catalog import initial_files
from changebench.design import make_plan
from changebench.experiment import make_request,total_tokens
from changebench.live_agent import run_request,TOOLS,execute_python
from changebench.policy import DESIGN,SKILL
from changebench.execution import ProcessResult


def request(arm='C'):
    trial=next(t for t in make_plan(1)['trials'] if t['condition']==arm and t['task']=='config')
    return make_request(trial,'initial',initial_files('config'),'test-model',
                        {'wall_seconds':20,'total_tokens':10000,'tool_calls':20})


def response(output, n=10, status='completed'):
    return {'id':'resp-test','model':'test-model','status':status,'output':output,
            'usage':{'input_tokens':n,'output_tokens':5,
                     'input_tokens_details':{'cached_tokens':2},
                     'output_tokens_details':{'reasoning_tokens':3}}}


def call(name,args,index=1):
    return {'type':'function_call','call_id':f'call-{index}','name':name,'arguments':json.dumps(args)}


def test_lazy_skill_loading_no_eager_body():
    calls=[]
    sequence=[response([call('read_skill',{'name':'design-check'})]),response([])]
    def post(payload,timeout):calls.append(copy.deepcopy(payload));return sequence.pop(0)
    result=run_request(request('C'),post=post)
    assert DESIGN not in json.dumps(calls[0])
    assert calls[0]['instructions'].find(DESIGN)==-1
    assert any(x.get('type')=='function_call_output' and SKILL in json.loads(x['output']) for x in calls[1]['input'])
    assert len(result['skill_events'])==1
    assert total_tokens(result['usage'])==30
    assert result['usage']['cached_input_tokens']==4
    assert result['usage']['reasoning_tokens']==6
    assert result['usage']['tool_calls']==1


def test_b_gets_same_policy_without_loading_tool():
    captured=[]
    def post(p,t):captured.append(p);return response([])
    result=run_request(request('B'),post=post)
    assert DESIGN in captured[0]['instructions']
    assert result['skill_events']==[]
    assert captured[0]['tools']==TOOLS


def test_a_cannot_access_uninstalled_skill():
    captured=[];sequence=[response([call('read_skill',{'name':'design-check'})]),response([])]
    def post(p,t):captured.append(copy.deepcopy(p));return sequence.pop(0)
    result=run_request(request('A'),post=post)
    assert result['skill_events']==[]
    assert 'Skill unavailable' in captured[1]['input'][-1]['output']


def test_write_execute_delete_tools_and_verification_log():
    sequence=[response([call('write_file',{'path':'lib.py','content':'VALUE=1'},1),
                        call('run_python',{'args':['-m','unittest']},2),
                        call('delete_file',{'path':'lib.py'},3)]),response([])]
    seen=[]
    def execute(files,args,image,timeout):
        seen.append((dict(files),args))
        return {'status':'ok','returncode':0,'stdout':'OK','stderr':''}
    result=run_request(request('A'),post=lambda p,t:sequence.pop(0),execute=execute)
    assert seen[0][0]['lib.py']=='VALUE=1'
    assert 'lib.py' not in result['files']
    assert result['verification_events'][0]['returncode']==0
    assert result['usage']['tool_calls']==3

@pytest.mark.parametrize('path',['../escape.py','AGENTS.md','.agents/x.py','.','x'*256])
def test_tool_write_guard(path):
    captured=[];sequence=[response([call('write_file',{'path':path,'content':'x'})]),response([])]
    def post(p,t):captured.append(copy.deepcopy(p));return sequence.pop(0)
    result=run_request(request(),post=post)
    assert path not in result['files']
    assert 'error' in json.loads(captured[1]['input'][-1]['output'])


def test_api_failure_cost_is_unknown_not_zero():
    def fail(payload,timeout):raise urllib.error.URLError('timeout after request may have been accepted')
    result=run_request(request(),post=fail)
    assert result['status']=='infra_error'
    assert total_tokens(result['usage']) is None


def test_budget_overrun_is_not_a_success():
    req=request();req['budget']['total_tokens']=20
    result=run_request(req,post=lambda p,t:response([],n=30))
    assert result['status']=='budget_exhausted'
    assert total_tokens(result['usage'])==35


def test_missing_api_usage_not_fabricated():
    result=run_request(request(),post=lambda p,t:{'output':[],'status':'completed'})
    assert result['status']=='infra_error'
    assert total_tokens(result['usage']) is None


def test_api_reasoning_items_preserved_but_not_exported():
    reasoning={'type':'reasoning','id':'reasoning-test','encrypted_content':'opaque-test','summary':[]}
    captured=[];sequence=[response([reasoning,call('list_files',{})]),response([])]
    def post(p,t):captured.append(copy.deepcopy(p));return sequence.pop(0)
    result=run_request(request(),post=post)
    assert reasoning in captured[1]['input']
    assert 'opaque-test' not in json.dumps(result)


def test_execution_never_silently_uses_host_python():
    with patch('changebench.live_agent.shutil.which',return_value=None):
        with pytest.raises(RuntimeError):execute_python({'main.py':'pass'},['main.py'],'python:3.13-slim',1)


def test_docker_worker_flags_mocked_not_live():
    seen=[]
    def fake_run(argv,data,cwd,timeout,output_limit,**kwargs):
        seen.append(argv)
        return ProcessResult('ok',0,'OK','',0.1)
    with patch('changebench.live_agent.shutil.which',return_value='/usr/bin/docker'), \
         patch('changebench.live_agent.run_bounded',side_effect=fake_run), \
         patch('changebench.live_agent.subprocess.run',return_value=__import__('subprocess').CompletedProcess([],0,'','')):
        result=execute_python({'main.py':'pass'},['main.py'],'python:3.13-slim',1)
    assert result['status']=='ok'
    assert '--network=none' in seen[0] and '--read-only' in seen[0]
    assert not any('OPENAI_API_KEY' in x for x in seen[0])
    assert '--user=65534:65534' in seen[0]
