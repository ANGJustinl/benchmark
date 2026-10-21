import pytest
from changebench.catalog import TASKS,STAGES,cases,initial_files,public_spec
from changebench.fixtures import reference_files
from changebench.execution import evaluate

@pytest.mark.parametrize('task',TASKS)
@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('layout',['flat','modular'])
def test_valid_implementations(task,stage,layout):
    result=evaluate(reference_files(task,stage,layout),task,stage,backend='local',trusted_local=True)
    assert result['success'],result

@pytest.mark.parametrize('task',TASKS)
def test_semantic_mutants_are_killed(task):
    result=evaluate(reference_files(task,'initial',mutant=True),task,'initial',backend='local',trusted_local=True)
    assert not result['success']
    assert result['status']=='failed'

@pytest.mark.parametrize('task',TASKS)
@pytest.mark.parametrize('stage',['local','extension'])
def test_nochange_cannot_pass_new_requirements(task,stage):
    result=evaluate(reference_files(task,'initial'),task,stage,backend='local',trusted_local=True)
    assert not result['success']
    assert result['regression_passed']==result['regression_total']
    assert result['new_passed']<result['new_total']

@pytest.mark.parametrize('task',TASKS)
def test_hidden_future_not_in_initial_exports(task):
    initial=public_spec(task,'initial')
    assert 'CHANGE REQUEST' not in initial
    files=initial_files(task)
    assert not any('fixtures' in n or 'catalog' in n for n in files)
    assert files['README.md']==initial
    assert 'test_public.py' in files

@pytest.mark.parametrize('task',TASKS)
def test_forks_retain_exact_regression_cases(task):
    old=cases(task,'initial',719)
    for stage in ['local','extension']:
        actual=[c for c in cases(task,stage,719) if c.origin=='initial']
        assert actual==old


def test_seed_reproducibility_and_randomization():
    assert cases('config','initial',1)==cases('config','initial',1)
    assert cases('config','initial',1)!=cases('config','initial',2)


def test_invalid_catalog_arguments():
    with pytest.raises(ValueError):cases('unknown','initial',1)
    with pytest.raises(ValueError):public_spec('config','future')
