"""Auditable calibration: positives, no-op negatives, semantic controls, repeated grades."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .poly_catalog import TASKS, STAGES, metadata
from .poly_fixtures import reference_files
from .poly_execution import evaluate
from .runtimes import runtime_manifest


def semantic_mutant(task):
    """Named realistic faults, not syntax failures disguised as mutation kills."""
    m=metadata(task);f=m['family'];lang=m['language']
    if f not in ('settings','invoice'):
        return reference_files(task,'seed'),{
            'events':'omit aggregation','routing':'treat redirects as sent',
            'ledger':'ignore idempotency receipts','scheduler':'collapse dependency waves',
            'store':'omit persistence','outbox':'omit persistence'}[f]
    files=reference_files(task,'initial','flat')
    entry={'python':'main.py','typescript':'main.ts','go':'main.go'}[lang]
    replacements={
      ('settings','python'):("integer(values['port'],1,65535)","integer(values['port'],0,65535)"),
      ('settings','typescript'):('integer(out.port,1,65535)','integer(out.port,0,65535)'),
      ('settings','go'):('number(out["port"],1,65535)','number(out["port"],0,65535)'),
      ('invoice','python'):("subtotal * integer(data.get('bps',0),0,10000) // 10000","(subtotal * integer(data.get('bps',0),0,10000) + 9999) // 10000"),
      ('invoice','typescript'):('Math.floor(subtotal*integer(data.bps ?? 0,0,10000)/10000)','Math.ceil(subtotal*integer(data.bps ?? 0,0,10000)/10000)'),
      ('invoice','go'):('discount:=subtotal*number(optional(d,"bps",0),0,10000)/10000','discount:=(subtotal*number(optional(d,"bps",0),0,10000)+9999)/10000'),
    }
    a,b=replacements[f,lang]
    if files[entry].count(a)!=1:raise ValueError('Mutation location drift')
    files[entry]=files[entry].replace(a,b)
    return files,'allow zero port' if f=='settings' else 'round discounts upward'


def grade_fingerprint(result):
    body={'status':result['status'],'total':result['total'],'passed':result['passed'],
          'cases':sorted(result.get('cases',[]),key=lambda c:c['id'])}
    return hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()


def calibrate(out: Path, repeats=3, tasks=TASKS, random_count=16):
    if type(repeats) is not int or repeats<2:raise ValueError('At least two repeat verdicts required')
    if out.exists() and any(out.iterdir()):raise ValueError('Calibration destination must be empty')
    out.mkdir(parents=True,exist_ok=True)
    env=runtime_manifest();observations=[];checks=[];stability=[]
    def check(name,passed,**extra):checks.append({'name':name,'passed':bool(passed),**extra})
    with (out/'evaluations.jsonl').open('w') as log:
        def run(task,stage,files,seed,tag,reverse=False):
            result=evaluate(files,task,stage,seed,backend='local',trusted_local=True,
                            random_count=random_count,repeat_order=reverse)
            row={'task':task,'stage':stage,'control':tag,'seed':seed,'result':result}
            log.write(json.dumps(row,ensure_ascii=True)+'\n');log.flush();observations.append(row)
            return result
        for task in tasks:
            for stage in STAGES:
                fingerprints=[]
                for repetition in range(repeats):
                    result=run(task,stage,reference_files(task,stage,'flat'),101,f'positive-repeat-{repetition}',bool(repetition%2))
                    check(f'{task}/{stage}/repeat-{repetition}',result['success'],kind='positive')
                    fingerprints.append(grade_fingerprint(result))
                good=len(set(fingerprints))==1
                check(f'{task}/{stage}/stable-verdict',good,kind='stability')
                stability.append({'task':task,'stage':stage,'repeats':repeats,'unstable':not good,'grade_hashes':fingerprints})
                other=run(task,stage,reference_files(task,stage,'modular'),271,'positive-other-layout-and-seed',True)
                check(f'{task}/{stage}/other-layout-and-seed',other['success'],kind='positive')
            files,name=semantic_mutant(task)
            result=run(task,'initial',files,101,'mutation: '+name)
            check(f'{task}/semantic-mutant',not result['success'] and result['status']=='failed',kind='negative',fault=name)
            for stage in ('local','extension'):
                result=run(task,stage,reference_files(task,'initial'),101,'no-change')
                check(f'{task}/{stage}/no-change',not result['success'] and result['status']=='failed' and
                      result['regression_passed']==result['regression_total'] and result['new_passed']<result['new_total'],kind='negative')
    after=runtime_manifest();check('runtime-unchanged',env==after,kind='environment')
    counts={k:sum(c['kind']==k for c in checks) for k in ('positive','negative','stability','environment')}
    result={'schema_version':1,'evidence_kind':'trusted_reference_calibration','tasks':len(tasks),
            'evaluations':len(observations),'assertions':sum(r['result']['total'] for r in observations),
            'checks':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),
            'check_categories':counts,'unstable_artifact_stage_pairs':sum(s['unstable'] for s in stability),
            'stability_samples':len(stability),'repeats':repeats,'runtime_before':env,'runtime_after':after,
            'model_api_calls':0,'model_tokens':None,'check_results':checks,'stability':stability,
            'limitations':['Three source languages are handwritten, but authored in the same implementation effort.',
                           'Positive layout variants reuse the same business implementation.',
                           'Named faults do not measure exhaustive mutation coverage.',
                           'Observed deterministic grading is not absolute future stability.',
                           'No live model sensitivity, Docker isolation, concurrent writers, or power-loss durability measured.']}
    (out/'calibration.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    return result
