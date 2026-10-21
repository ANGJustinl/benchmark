"""Optional v0.3 gate: clone one actual T0 disk into independent T1 branches."""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile

from .execution import equal_json, strict_json
from .poly_catalog import oracle, reference_state
from .runtimes import PreparedProgram


CASES = {
    'store': {
        'seed': [{'op':'put','id':'upgrade-a','key':'upgrade-key','value':'old'},
                 {'op':'put','id':'upgrade-b','key':'upgrade-key','value':'latest'}],
        'after': [{'op':'get','key':'upgrade-key'},
                  {'op':'put','id':'upgrade-a','key':'upgrade-key','value':'old'},
                  {'op':'put','id':'upgrade-a','key':'upgrade-key','value':'different'},
                  {'op':'put','id':'upgrade-new','key':'upgrade-key','value':'new'},
                  {'op':'get','key':'upgrade-key'}],
        'restart': [{'op':'get','key':'upgrade-key'},
                    {'op':'put','id':'upgrade-new','key':'upgrade-key','value':'new'}]},
    'outbox': {
        'seed': [{'op':'enqueue','id':'upgrade-a','payload':'first'},
                 {'op':'enqueue','id':'upgrade-b','payload':'second'},{'op':'take'}],
        'after': [{'op':'enqueue','id':'upgrade-a','payload':'first'}, {'op':'take'}, {'op':'take'},
                  {'op':'enqueue','id':'upgrade-c','payload':'third'}, {'op':'take'}],
        'restart': [{'op':'take'},{'op':'enqueue','id':'upgrade-c','payload':'third'}]},
}


class StateFormatError(ValueError):
    pass


@contextmanager
def private_state(backend,images,limits,trusted_local):
    with tempfile.TemporaryDirectory(prefix='changebench-upgrade-') as temp:
        state=Path(temp)/'state';state.mkdir(mode=0o777);state.chmod(0o777)
        try:
            yield state
        finally:
            transfer(state,{'op':'clear'},backend,images.get('state'),limits,trusted_local)


def expectations(family, stage, requests, state):
    outputs=[]
    for request in requests:
        result,state=oracle(family,stage,request,state)
        outputs.append(result)
    return outputs,state


def observe(program, requests, state, timeout):
    wire=''.join(json.dumps(request)+'\n' for request in requests).encode()
    result=program.run(wire,state,timeout)
    try:
        actual=[strict_json(line) for line in result.stdout.splitlines()]
    except ValueError:
        actual=None
    return result,actual


def transfer(state, request, backend, image, limits, trusted_local):
    files={'main.py':Path(__file__).with_name('state_transfer.py').read_text()}
    with PreparedProgram(files,'python',backend,image,trusted_local,
                         compile_timeout=limits['compile_seconds'],startup_timeout=limits['startup_seconds']) as program:
        result=program.run(json.dumps(request).encode(),state,limits['execution_seconds'])
    if result.status!='ok':
        raise RuntimeError('Trusted state transfer failed: '+json.dumps(asdict(result)))
    value=strict_json(result.stdout)
    if 'state_error' in value:
        raise StateFormatError(value['state_error'])
    return value


def options(trial,backend,images,limits,trusted_local):
    return dict(language=trial['language'],backend=backend,image=images.get(trial['language']),
                trusted_local=trusted_local,compile_timeout=limits['compile_seconds'],
                startup_timeout=limits['startup_seconds'])


def create_seed(files,trial,out,backend,images,limits,trusted_local):
    from .experiment import write_json
    family=trial['family'];case=CASES[family]
    expected,_=expectations(family,'initial',case['seed'],reference_state(family))
    with private_state(backend,images,limits,trusted_local) as state:
        with PreparedProgram(files,**options(trial,backend,images,limits,trusted_local)) as program:
            if program.compilation.status!='ok':
                return {'success':False,'status':program.compilation.status,'compilation':asdict(program.compilation)}
            process,actual=observe(program,case['seed'],state,limits['execution_seconds'])
        success=process.status=='ok' and equal_json(actual,expected)
        evidence={'success':success,'status':'passed' if success else (process.status if process.status!='ok' else 'failed'),
                  'process':asdict(process),'inputs':case['seed'],'expected':expected,'actual':actual}
        if success:
            archive=transfer(state,{'op':'capture'},backend,images.get('state'),limits,trusted_local)
            relative=f"handoff/{trial['trial_id']}/t0-state.json"
            write_json(out/relative,archive)
            evidence.update(archive_path=relative,archive_sha256=hashlib.sha256((out/relative).read_bytes()).hexdigest())
    write_json(out/f"evidence/{trial['trial_id']}/upgrade-seed.json",evidence)
    return evidence


def check_upgrade(files,trial,stage,out,seed,backend,images,limits,trusted_local):
    from .experiment import write_json
    path=out/seed['archive_path']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=seed['archive_sha256']:
        raise RuntimeError('Frozen T0 disk changed')
    archive=strict_json(path.read_text());family=trial['family'];case=CASES[family]
    _,state_value=expectations(family,'initial',case['seed'],reference_state(family))
    expected,state_value=expectations(family,stage,case['after'],state_value)
    restart_expected,_=expectations(family,stage,case['restart'],state_value)
    with private_state(backend,images,limits,trusted_local) as state:
        transfer(state,{'op':'restore','archive':archive},backend,images.get('state'),limits,trusted_local)
        # Independently capture the restored state before the candidate can modify it.
        restored=transfer(state,{'op':'capture'},backend,images.get('state'),limits,trusted_local)
        if restored!=archive:raise RuntimeError('T1 did not receive an identical T0 disk archive')
        with PreparedProgram(files,**options(trial,backend,images,limits,trusted_local)) as program:
            if program.compilation.status!='ok':
                return {'success':False,'status':program.compilation.status,'compilation':asdict(program.compilation)}
            process,actual=observe(program,case['after'],state,limits['execution_seconds'])
            restart,restart_actual=observe(program,case['restart'],state,limits['execution_seconds'])
    processes=[asdict(process),asdict(restart)]
    success=all(p['status']=='ok' for p in processes) and equal_json(actual,expected) and equal_json(restart_actual,restart_expected)
    failed_process=next((p['status'] for p in processes if p['status']!='ok'),None)
    evidence={'success':success,'status':'passed' if success else (failed_process or 'failed'),
              'archive_path':seed['archive_path'],'archive_sha256':seed['archive_sha256'],
              'restored_identical':True,'inputs':[case['after'],case['restart']],
              'expected':[expected,restart_expected],'actual':[actual,restart_actual],'processes':processes}
    write_json(out/f"evidence/{trial['trial_id']}/upgrade-{stage}.json",evidence)
    return evidence
