"""Fail-closed integrity checks, not a claim of absolute statistical fairness."""
from __future__ import annotations
import hashlib
import json
from collections import Counter,defaultdict
from pathlib import Path
from .execution import strict_json
from .workspace import digest


def _write(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+'\n')


def seal_run(root: Path):
    paths=[root/n for n in ('plan.json','environment.json','records.jsonl','runtime-after.json')]
    paths+=sorted((root/'snapshots').glob('*/*.json')) if (root/'snapshots').exists() else []
    for directory in ('artifacts','inputs','checkpoints','evidence','handoff'):
        paths+=sorted(p for p in (root/directory).rglob('*') if p.is_file() and not p.is_symlink()) if (root/directory).exists() else []
    paths += [root/n for n in ('RUN-STATE.json','progress.json') if (root/n).exists()]
    manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    body={'schema_version':1,'files':manifest,
          'threat_model':'Detect accidental changes and result substitution. Not a signature against a malicious owner able to reseal everything.'}
    _write(root/'RUN-SEAL.json',body)
    return body


def check_evaluation(evaluation: dict, stage: dict, task: str, phase: str, plan: dict):
    """Recompute cached fields from per-case and process evidence."""
    ev=evaluation
    if ev.get('task')!=task or ev.get('stage')!=phase:raise ValueError('Evaluation identity mismatch')
    if type(ev.get('success')) is not bool:raise ValueError('Evaluation success must be Boolean')
    combined=ev['success'] and stage.get('upgrade',{}).get('success',True) and stage.get('upgrade_seed',{}).get('success',True)
    if stage.get('upgrade_error'):combined=False
    if stage['success'] != combined:raise ValueError('Stage claims contradict evaluation/upgrade')
    if ev.get('random_count') != plan['evaluation']['random_count']:raise ValueError('Unplanned case count')
    cases=ev.get('cases',[])
    if any(type(c.get('passed')) is not bool for c in cases):raise ValueError('Per-case result must be Boolean')
    if len({c['id'] for c in cases})!=len(cases):raise ValueError('Duplicate case observations')
    passed=sum(c['passed'] for c in cases)
    if ev.get('passed')!=passed:raise ValueError('Forged passed counter')
    if ev['success']:
        if len(cases)!=ev['total'] or passed!=ev['total'] or ev.get('status')!='passed':
            raise ValueError('False success without complete passing evidence')
        processes=ev.get('executions',[]) or ([ev['execution']] if 'execution' in ev else [])
        if not processes or any(p['status']!='ok' or p['returncode']!=0 for p in processes):
            raise ValueError('Success after a failed or absent process')
    for prefix,isold in (('regression',True),('new',False)):
        relevant=[c for c in cases if (c['origin']=='initial')==isold]
        if prefix+'_total' in ev and (ev[prefix+'_total']!=len(relevant) or ev[prefix+'_passed']!=sum(c['passed'] for c in relevant)):
            raise ValueError('Regression/new counters do not match evidence')


def audit_run(root: Path) -> dict:
    from .design import validate_plan
    from .reporting import summarize
    from .poly_catalog import TASKS as POLY, suites, suite_fingerprint
    seal=strict_json((root/'RUN-SEAL.json').read_text())
    required={'plan.json','environment.json','records.jsonl','runtime-after.json','RUN-STATE.json','progress.json'}
    if not required <= seal['files'].keys():raise ValueError('Incomplete run seal')
    for relative,expected in seal['files'].items():
        p=root/relative
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or not p.is_file():raise ValueError('Unsafe or missing sealed file')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('Sealed file modified: '+relative)
    plan=strict_json((root/'plan.json').read_text());validate_plan(plan)
    state=strict_json((root/'RUN-STATE.json').read_text())
    if state!={'status':'completed','plan_sha256':plan['plan_sha256']}:raise ValueError('Interrupted run is not complete evidence')
    environment=strict_json((root/'environment.json').read_text())
    after=strict_json((root/'runtime-after.json').read_text())
    if environment['runtime_manifest']!=after:raise ValueError('Toolchain/environment changed during run')
    rows=[strict_json(s) for s in (root/'records.jsonl').read_text().splitlines() if s]
    summarize(plan,rows)
    expected_snapshots=set()
    for row in rows:
        for phase,stage in row['stages'].items():
            if stage.get('invoked'):
                artifact=stage.get('artifact',{})
                if artifact.get('kind') not in ('submitted','partial','unavailable'):
                    raise ValueError('Invoked stage missing artifact evidence')
                if artifact.get('kind')!='unavailable':
                    relative=artifact['path']
                    if relative not in seal['files'] or artifact['sha256']!=seal['files'][relative]:
                        raise ValueError('Missing or changed stage artifact')
                    if artifact['kind']=='partial' and stage['success']:
                        raise ValueError('Partial work cannot be a success')
                elif not artifact.get('reason'):raise ValueError('Missing artifact requires a reason')
                from .experiment import total_tokens
                tokens=total_tokens(stage['usage'])
                limit=plan['budgets_by_level'][str(row['level'])]
                if stage['success'] and ((tokens is not None and tokens>limit['total_tokens']) or
                    (stage['usage'].get('tool_calls') is not None and stage['usage']['tool_calls']>limit['tool_calls'])):
                    raise ValueError('Success exceeds frozen budget')
                receipts=stage.get('metadata',{}).get('api_calls',[])
                if receipts:
                    for target,alternate in (('input_tokens','inputTokens'),('output_tokens','outputTokens')):
                        values=[c.get('usage',{}).get(target,c.get('usage',{}).get(alternate)) if isinstance(c.get('usage'),dict) else None for c in receipts]
                        if stage['usage'].get(target) is not None and (any(type(v) is not int or v<0 for v in values) or sum(values)!=stage['usage'][target]):
                            raise ValueError('Usage differs from request receipts')
                receipt_path=stage.get('metadata',{}).get('receipt_path')
                if receipt_path and receipt_path not in seal['files']:raise ValueError('Unsealed request receipt')
                input_path=stage.get('input_artifact_path')
                if input_path not in seal['files'] or digest(strict_json((root/input_path).read_text()))!=stage['input_snapshot_sha256']:
                    raise ValueError('Missing or changed stage input')
            if type(stage.get('success')) is not bool:raise ValueError('Stage success must be Boolean')
            if plan['persistent_upgrade'] and row.get('family') in ('store','outbox'):
                seed=row['stages']['initial'].get('upgrade_seed')
                upgrade=stage.get('upgrade') if phase!='initial' else None
                if stage['success'] and not (seed and seed.get('success') and (phase=='initial' or upgrade and upgrade.get('success'))):
                    raise ValueError('Missing required disk handoff evidence')
                evidence=seed if phase=='initial' else upgrade
                if evidence and evidence.get('success'):
                    path=evidence['archive_path']
                    if path not in seal['files'] or seal['files'][path]!=evidence['archive_sha256']:
                        raise ValueError('Unsealed or changed T0 disk archive')
                    if phase!='initial':
                        if evidence['archive_sha256']!=seed['archive_sha256'] or not evidence.get('restored_identical'):
                            raise ValueError('Disk branches did not fork from identical T0')
                        if evidence['actual']!=evidence['expected'] or any(p['status']!='ok' or p['returncode']!=0 for p in evidence['processes']):
                            raise ValueError('False upgrade success')
            if 'evaluation' in stage:
                ev=stage['evaluation'];check_evaluation(ev,stage,row['task'],phase,plan)
                if row['task'] in POLY and 'suite_sha256' in ev:
                    scheduled=suites(row['task'],phase,row['evaluation_seed'],plan['evaluation']['random_count'])
                    if ev['suite_sha256']!=suite_fingerprint(scheduled):
                        raise ValueError('Wrong hidden suite/seed')
                    expected_cases={f"{case.id}/{i}":case.origin for case in scheduled for i in range(len(case.expected))}
                    if ev['total']!=len(expected_cases):raise ValueError('Wrong expected observation count')
                    if ev.get('cases') and {case['id']:case['origin'] for case in ev['cases']}!=expected_cases:
                        raise ValueError('Wrong or missing scheduled observations')
            if 'output_snapshot_sha256' in stage:
                path=f"snapshots/{row['trial_id']}/{phase}.json";expected_snapshots.add(path)
                if path not in seal['files']:raise ValueError('Unsealed snapshot')
                if digest(strict_json((root/path).read_text()))!=stage['output_snapshot_sha256']:raise ValueError('Artifact hash mismatch')
            if phase!='initial' and stage.get('invoked'):
                if stage.get('input_snapshot_sha256')!=row['stages']['initial'].get('output_snapshot_sha256'):
                    raise ValueError('Maintenance branches did not fork from identical T0')
            if 'budget' in stage and stage['budget']!=plan['budgets_by_level'][str(row['level'])]:
                raise ValueError('Unequal or unplanned budget')
    if {k for k in seal['files'] if k.startswith('snapshots/')}!=expected_snapshots:raise ValueError('Unexpected snapshots in seal')
    ordered=defaultdict(list)
    for trial in plan['trials']:ordered[trial['block_id']].append(trial['condition'])
    orders=Counter(''.join(v) for v in ordered.values())
    live=all(r['evidence_kind']=='live_model' for r in rows)
    limitations=['Public authored suite: contamination-free holdout not established.',
                 'No guarantee against arbitrary sandbox escape or malicious resealing.',
                 'Designed difficulty has not been empirically calibrated by fixture playback.']
    if not live:limitations.append('Fixture replay is not evidence of model performance or Skill benefit.')
    if environment['backend']!='docker':limitations.append('Trusted host execution only: container isolation has not been exercised.')
    return {'integrity_passed':True,'planned_trials':len(plan['trials']),'observed_trials':len(rows),
            'sealed_snapshots':len(expected_snapshots),'condition_order_counts':dict(sorted(orders.items())),
            'runtime_manifest_unchanged':True,'absolute_fairness_claim':False,
            'live_model_evidence':live,'limitations':limitations}


def descriptive_strata(plan,rows):
    """Equal task weights within strata. Never pool ports as independent tasks."""
    grouped=defaultdict(list)
    for row in rows:
        meta=plan['task_metadata'][row['task']]
        key=(meta['track'],meta['language'],meta['level'],row['condition'])
        grouped[key].append(row)
    output=[]
    for (track,language,level,arm),group in sorted(grouped.items()):
        infra=any(any(s['status'] in ('infra_error','invalid_response','launch_error','startup_timeout') for s in r['stages'].values()) for r in group)
        output.append({'track':track,'language':language,'level':level,'condition':arm,
           'planned_trials':len(group),'independent_families':len({r['family'] for r in group}),
           'initial_success_rate':None if infra else sum(r['initial_success'] for r in group)/len(group),
           'joint_success_rate':None if infra else sum(r['joint_success'] for r in group)/len(group),
           'difficulty_status':'provisional_unmeasured'})
    return output


def sensitivity_diagnostics(plan,rows):
    """Diagnostics only. Thresholds do not establish construct validity or a winner."""
    if not rows or any(r['evidence_kind']!='live_model' for r in rows):
        return {'status':'not_measured','reason':'Requires real fixed-model runs; fixtures cannot calibrate model difficulty.'}
    strata=descriptive_strata(plan,rows);flags=[]
    for s in strata:
        if s['joint_success_rate'] is None:tag='infrastructure_invalid'
        elif s['joint_success_rate']>=.95:tag='possible_ceiling'
        elif s['joint_success_rate']<=.10:tag='possible_floor'
        else:tag='observed_nonextreme'
        flags.append({**s,'flag':tag,'precision':'exploratory: only two authored families per new level/language'})
    return {'status':'exploratory','strata':flags,'relabel_difficulty_automatically':False,
            'warning':'Freeze tasks before comparisons; tuning on evaluated arms requires a new holdout.'}
