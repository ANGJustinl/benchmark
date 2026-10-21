"""Independent controller-side checks with cumulative regressions and restart scenarios."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import tempfile
from .execution import strict_json, equal_json
from .poly_catalog import metadata, suites, suite_fingerprint
from .runtimes import PreparedProgram


def evaluate(files,task,stage,seed=101,backend='docker',trusted_local=False,image=None,
             random_count=16,timeout=8,repeat_order=False,compile_timeout=120,startup_timeout=30):
    m=metadata(task);suite=suites(task,stage,seed,random_count)
    base={'task':task,'stage':stage,'language':m['language'],'level':m['level'],'backend':backend,
          'seed':seed,'random_count':random_count,'suite_sha256':suite_fingerprint(suite),'total':sum(len(c.expected) for c in suite),
          'passed':0,'success':False,'cases':[]}
    try:
        program=PreparedProgram(files,m['language'],backend,image,trusted_local,
                                compile_timeout=compile_timeout,startup_timeout=startup_timeout)
    except ValueError as exc:return {**base,'status':'invalid_submission','error':str(exc)}
    details=[];executions=[];protocol=True;status='passed'
    with program:
        compile_info=asdict(program.compilation)
        if program.compilation.status!='ok':return {**base,'status':program.compilation.status,'compilation':compile_info}
        ordered=list(reversed(suite)) if repeat_order else suite
        # Stateless scenarios share one real process; stateful scenarios start isolated.
        if not m['persistent']:
            inputs=[request for c in ordered for session in c.sessions for request in session]
            expected=[(c,i,value) for c in ordered for i,value in enumerate(c.expected)]
            batches=[('stateless',inputs,expected,program.root/'state',None)]
        else:
            batches=[]
            for ix,c in enumerate(ordered):
                state=program.root/f'state-{ix}';state.mkdir()
                if c.legacy is not None:
                    (state/'legacy.json').write_text(json.dumps(c.legacy));(state/'legacy.json').chmod(0o666)
                offset=0
                for j,session in enumerate(c.sessions):
                    expected=[(c,offset+i,c.expected[offset+i]) for i in range(len(session))]
                    batches.append((f'{c.id}-restart-{j}',session,expected,state,c.legacy));offset+=len(session)
        for name,inputs,expected,state,_ in batches:
            raw=''.join(json.dumps(d,ensure_ascii=True,allow_nan=False)+'\n' for d in inputs).encode()
            result=program.run(raw,state,timeout)
            executions.append({'session':name,**asdict(result),'stdout':None})
            lines=result.stdout.splitlines()
            if len(lines)!=len(expected):protocol=False
            if result.status!='ok':status=result.status
            for index,(case,i,value) in enumerate(expected):
                try:
                    actual=strict_json(lines[index]);passed=equal_json(actual,value)
                except (ValueError,IndexError,TypeError):passed=False;actual=None;protocol=False
                details.append({'id':f'{case.id}/{i}','origin':case.origin,'passed':passed,'actual':actual if not passed else None})
    count=sum(c['passed'] for c in details)
    if status=='passed' and (count!=base['total'] or not protocol):status='failed' if protocol else 'protocol_error'
    return {**base,'status':status,'passed':count,'success':status=='passed','protocol_ok':protocol,'cases':details,
            'compilation':compile_info,'executions':executions,
            'regression_total':sum(c['origin']=='initial' for c in details),
            'regression_passed':sum(c['origin']=='initial' and c['passed'] for c in details),
            'new_total':sum(c['origin']!='initial' for c in details),
            'new_passed':sum(c['origin']!='initial' and c['passed'] for c in details)}
