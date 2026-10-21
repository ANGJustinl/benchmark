"""Real Docker tests. Run explicitly with CB_REPAIR_IMAGES=/absolute/images.json.

No model/API calls. Fail rather than skip if the selected images are unavailable.
"""
import json
import os
from pathlib import Path
import tempfile

import pytest

from changebench.design import make_plan
from changebench.execution import evaluate, resolve_docker_image
from changebench.experiment import run_experiment, ReplayAdapter, write_json
from changebench.fairness import audit_run
from changebench.fixtures import reference_files
from changebench.runtimes import preflight_runtime_images, PreparedProgram
from changebench.upgrade import create_seed, check_upgrade, transfer, StateFormatError

LIMITS={'random_count':18,'execution_seconds':8,'compile_seconds':120,'startup_seconds':30}
RESULTS=Path(os.environ.get('CB_REPAIR_RESULTS','/tmp/changebench-repair-docker'))


@pytest.fixture(scope='session')
def images():
    names=json.loads(Path(os.environ['CB_REPAIR_IMAGES']).read_text())
    result={key:resolve_docker_image(value) for key,value in names.items()}
    RESULTS.mkdir(parents=True,exist_ok=True)
    write_json(RESULTS/'images.json',result)
    return result


@pytest.mark.parametrize('language',['python','typescript','go'])
def test_real_native_preflight_and_contract(language,images):
    preflight=preflight_runtime_images({language:images[language]})
    observed=evaluate(reference_files('settings-'+language,'initial'),'settings-'+language,'initial',
                      image=images[language],backend='docker')
    write_json(RESULTS/f'native-{language}.json',{'preflight':preflight,'evaluation':observed})
    assert observed['success'] and observed['passed']==observed['total']
    for process in observed['executions']:
        assert process['ready'] is True and process['startup_seconds']>0
        assert process['execution_seconds']>=0 and process['cleanup_error'] is None


@pytest.mark.parametrize('code,status',[
    ('import sys;sys.exit(127)\n','runtime_error'),
    ('while True: pass\n','timeout'),
])
def test_candidate_failure_is_not_infrastructure(code,status,images):
    with PreparedProgram({'main.py':code},'python',image=images['python']) as program:
        process=program.run(b'{}\n',program.root/'state',timeout=.5)
    write_json(RESULTS/f'{status}.json',process.__dict__)
    assert process.status==status and process.ready is True and process.cleanup_error is None


@pytest.mark.parametrize('language',['python','typescript','go'])
def test_frozen_actual_disk_into_both_native_branches(language,images):
    trial=make_plan(1,conditions=('A',),tasks=('store-'+language,))['trials'][0]
    out=RESULTS/('handoff-'+language);out.mkdir(exist_ok=True)
    seed=create_seed(reference_files('store-'+language,'initial'),trial,out,'docker',images,LIMITS,False)
    assert seed['success']
    branches={}
    for phase in ('local','extension'):
        branches[phase]=check_upgrade(reference_files('store-'+language,phase),trial,phase,out,seed,'docker',images,LIMITS,False)
        assert branches[phase]['success'] and branches[phase]['restored_identical']
        assert branches[phase]['archive_sha256']==seed['archive_sha256']
    write_json(out/'summary.json',{'seed':seed,'branches':branches})


def test_real_disk_upgrade_gate_rejects_fresh_only_format(images):
    trial=make_plan(1,conditions=('A',),tasks=('store-python',))['trials'][0]
    out=RESULTS/'format-negative';out.mkdir(exist_ok=True)
    seed=create_seed(reference_files('store-python','initial'),trial,out,'docker',images,LIMITS,False)
    mutant=reference_files('store-python','local')
    mutant['main.py']=mutant['main.py'].replace("'snapshot.json'","'new-format.json'")
    result=check_upgrade(mutant,trial,'local',out,seed,'docker',images,LIMITS,False)
    write_json(out/'negative.json',result)
    assert not result['success'] and result['status']=='failed'


def test_complete_trajectory_audits_with_upgrade_gate(images,tmp_path):
    out=tmp_path/'out'
    rows=run_experiment(make_plan(1,conditions=('A',),tasks=('outbox-python',),persistent_upgrade=True),
                        ReplayAdapter(),ReplayAdapter(),out,image=images)
    audit=audit_run(out)
    write_json(RESULTS/'full-trajectory.json',{'joint_success':rows[0]['joint_success'],'audit':audit})
    assert rows[0]['joint_success'] and audit['integrity_passed']


@pytest.mark.parametrize('make_link',[False,True])
def test_state_transfer_handles_private_files_and_rejects_links(images,tmp_path,make_link):
    state=tmp_path/'state';state.mkdir(mode=0o777);state.chmod(0o777)
    code=('import os,pathlib\n'
          'root=pathlib.Path(os.environ["CB_STATE_DIR"]);d=root/"private";d.mkdir(mode=0o700)\n'
          '(d/"state").write_bytes(b"durable");(d/"state").chmod(0o600)\n')
    if make_link:code+='(root/"link").symlink_to("/etc/passwd")\n'
    try:
        with PreparedProgram({'main.py':code},'python',image=images['python']) as program:
            result=program.run(b'',state)
        assert result.status=='ok'
        if make_link:
            with pytest.raises(StateFormatError,match='links'):
                transfer(state,{'op':'capture'},'docker',images['state'],LIMITS,False)
        else:
            archive=transfer(state,{'op':'capture'},'docker',images['state'],LIMITS,False)
            assert archive['files'][0]['mode']==0o600
            write_json(RESULTS/'private-state.json',archive)
    finally:
        transfer(state,{'op':'clear'},'docker',images['state'],LIMITS,False)
    assert list(state.iterdir())==[]


@pytest.mark.parametrize('language',['typescript','go'])
def test_outbox_upgrade_for_other_native_languages(language,images):
    trial=make_plan(1,conditions=('A',),tasks=('outbox-'+language,))['trials'][0]
    out=RESULTS/('outbox-'+language);out.mkdir(exist_ok=True)
    seed=create_seed(reference_files('outbox-'+language,'initial'),trial,out,'docker',images,LIMITS,False)
    assert seed['success']
    branches={}
    for phase in ('local','extension'):
        result=check_upgrade(reference_files('outbox-'+language,phase),trial,phase,out,seed,'docker',images,LIMITS,False)
        assert result['success'] and result['archive_sha256']==seed['archive_sha256']
        branches[phase]=result
    write_json(out/'summary.json',{'seed':seed,'branches':branches})
