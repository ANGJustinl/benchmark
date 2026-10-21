import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from changebench.execution import evaluate,strict_json,equal_json,docker_argv,run_bounded,clean_env
from changebench.workspace import validate_files,sanitized,materialize,digest,diff_diagnostics

@pytest.mark.parametrize('path',['../x','/tmp/x','a/../x','a\\b','a//b','./main.py','a/','x:y','x\x00y'])
def test_reject_unsafe_paths(path):
    with pytest.raises(ValueError):validate_files({path:'x'})

def test_collision_and_sizes():
    with pytest.raises(ValueError):validate_files({'a':'x','a/b.py':'x'})
    with pytest.raises(ValueError):validate_files({'a':'x'*256001})
    with pytest.raises(ValueError):validate_files({})
    with pytest.raises(ValueError):validate_files({'a':123})

def test_symlink_rejected(tmp_path):
    outside=tmp_path/'outside';outside.mkdir()
    target=tmp_path/'work';target.mkdir();(target/'link').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):materialize({'link/x':'hello'},target)
    assert not (outside/'x').exists()

def test_sanitize_controls_not_natural_docs():
    files={'main.py':'pass','AGENTS.md':'B','notes/AGENTS.md':'B','.agents/skills/a/SKILL.md':'C',
           'README.md':'normal project documentation','notes/decision.md':'a real decision'}
    clean=sanitized(files)
    assert set(clean)=={'main.py','README.md','notes/decision.md'}
    assert digest(clean)==digest(dict(reversed(list(clean.items()))))

@pytest.mark.parametrize('raw',['{"x":1,"x":2}','NaN','Infinity','1e309','{bad'])
def test_strict_json(raw):
    with pytest.raises(ValueError):strict_json(raw)

def test_json_type_and_key_order():
    assert equal_json({'b':2,'a':1},{'a':1,'b':2})
    assert not equal_json({'n':True},{'n':1})
    assert not equal_json({'n':1.0},{'n':1})

def test_local_requires_opt_in():
    with pytest.raises(ValueError):evaluate({'main.py':'pass'},'config','initial',backend='local')

def test_no_unsafe_docker_fallback():
    with patch('changebench.execution.shutil.which',return_value=None):
        with pytest.raises(RuntimeError):evaluate({'main.py':'pass'},'config','initial')

def test_docker_flags_and_mounts(tmp_path):
    argv=docker_argv(tmp_path,'python:3.13-slim','test-name')
    for flag in ('--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64'):
        assert flag in argv
    assert any('dst=/workspace,readonly' in a for a in argv)
    assert not any('docker.sock' in a for a in argv)
    assert not any('OPENAI_API_KEY' in a for a in argv)
    assert str(Path(__file__).parent) not in str(argv)

@pytest.mark.parametrize('code,status',[
    ('pass\n','protocol_error'),
    ('import sys\nfor line in sys.stdin: print("{\\"passed\\":true}")\n','failed'),
    ('import sys\nfor line in sys.stdin: print("nonsense")\n','protocol_error'),
    ('raise RuntimeError("error")\n','runtime_error'),
    ('while True: pass\n','timeout'),
    ('import sys\nwhile True: sys.stdout.write("x"*8192);sys.stdout.flush()\n','output_limit'),
])
def test_bad_submissions(code,status):
    result=evaluate({'main.py':code},'config','initial',backend='local',trusted_local=True,timeout=.5)
    assert not result['success']
    assert result['status']==status

def test_missing_entrypoint():
    result=evaluate({'other.py':'pass'},'config','initial',backend='local',trusted_local=True)
    assert result['status']=='invalid_submission'

def test_launch_error(tmp_path):
    r=run_bounded(['/not/an/executable'],b'',tmp_path)
    assert r.status=='launch_error'

def test_limits_validation(tmp_path):
    with pytest.raises(ValueError):run_bounded(['x'],b'',tmp_path,timeout=float('nan'))
    with pytest.raises(ValueError):run_bounded([],b'',tmp_path)

def test_stdin_nonreader_cannot_block_forever(tmp_path):
    r=run_bounded([sys.executable,'-S','-c','import time;time.sleep(5)'],b'x'*200000,tmp_path,timeout=.2)
    assert r.status=='timeout'
    assert r.wall_seconds<3

def test_environment_has_no_secrets():
    assert set(clean_env('/tmp'))=={'PATH','HOME','LANG','LC_ALL'}

def test_diff_is_diagnostic():
    result=diff_diagnostics({'main.py':'pass'},{'main.py':'print(1)','lib.py':'pass'})
    assert result['changed_file_count']==2
    assert 'score' not in {k for k in result}


def test_docker_preflight_pins_local_image():
    from subprocess import CompletedProcess
    from changebench.execution import resolve_docker_image
    with patch('changebench.execution.shutil.which',return_value='/usr/bin/docker'), \
         patch('changebench.execution.subprocess.run',side_effect=[
             CompletedProcess([],0,'28.0',''),CompletedProcess([],0,'sha256:abc123\n','')]) as run:
        assert resolve_docker_image('python:3.13-slim')=='sha256:abc123'
        assert run.call_count==2


def test_docker_preflight_rejects_down_daemon():
    from subprocess import CompletedProcess
    from changebench.execution import resolve_docker_image
    with patch('changebench.execution.shutil.which',return_value='/usr/bin/docker'), \
         patch('changebench.execution.subprocess.run',return_value=CompletedProcess([],1,'','down')):
        with pytest.raises(RuntimeError):resolve_docker_image('python:3.13-slim')


def test_docker_preflight_does_not_implicitly_pull():
    from subprocess import CompletedProcess
    from changebench.execution import resolve_docker_image
    with patch('changebench.execution.shutil.which',return_value='/usr/bin/docker'), \
         patch('changebench.execution.subprocess.run',side_effect=[
             CompletedProcess([],0,'28.0',''),CompletedProcess([],1,'','not found')]) as run:
        with pytest.raises(RuntimeError):resolve_docker_image('missing-image')
        assert all('pull' not in c.args[0] for c in run.call_args_list)


def test_cleanup_targets_only_current_session():
    from subprocess import CompletedProcess
    from changebench.execution import cleanup_worker_containers
    token='a'*32
    with patch('changebench.execution.subprocess.run',side_effect=[
        CompletedProcess([],0,'123456abcdef\n',''),CompletedProcess([],0,'','')]) as run:
        assert cleanup_worker_containers(token) is None
        assert f'label=changebench.session={token}' in run.call_args_list[0].args[0]
        assert Path(run.call_args_list[1].args[0][0]).is_absolute()
        assert run.call_args_list[1].args[0][1:]==['rm','-f','123456abcdef']
    with pytest.raises(ValueError):cleanup_worker_containers('not a session')
