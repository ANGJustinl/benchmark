"""Trusted fixtures. These are controls, never model-performance evidence."""
from __future__ import annotations
import re
from pathlib import Path
from .poly_catalog import metadata, public_spec

ROOT=Path(__file__).with_name('data')/'poly'
SHIM='declare function require(name: string): any;\ndeclare const process: any;\n'
PY_MAIN='''import json,sys
from logic import solve
for line in sys.stdin:
    try:
        result=solve(json.loads(line))
    except (ValueError,KeyError,TypeError):
        result={"error":"invalid"}
    print(json.dumps(result,ensure_ascii=True,allow_nan=False),flush=True)
'''
TS_MAIN='''import {solve} from './logic';
const input: string = require('fs').readFileSync(0,'utf8');
for (const line of input.split('\\n')) {
    if (line === '') continue;
    let result: any;
    try { result = solve(JSON.parse(line)); } catch (_) { result = {error:'invalid'}; }
    process.stdout.write(JSON.stringify(result)+'\\n');
}
'''
GO_MAIN='''
func call(d M) (out any) {
    defer func(){if recover()!=nil{out=M{"error":"invalid"}}}()
    return solve(d)
}
func main(){
    scanner:=bufio.NewScanner(os.Stdin);scanner.Buffer(make([]byte,4096),2000000)
    for scanner.Scan(){var d M; var out any
        if json.Unmarshal(scanner.Bytes(),&d)!=nil{out=M{"error":"invalid"}}else{out=call(d)}
        b,e:=json.Marshal(out);if e!=nil{fmt.Fprintln(os.Stderr,e);os.Exit(1)};fmt.Println(string(b))
    }
    if scanner.Err()!=nil{fmt.Fprintln(os.Stderr,scanner.Err());os.Exit(1)}
}
'''


def reference_files(task: str, stage: str, layout='modular', *, public_seed=False) -> dict[str,str]:
    m=metadata(task);lang=m['language'];family=m['family']
    if stage not in ('seed','initial','local','extension') or layout not in ('flat','modular'):
        raise ValueError('Unknown fixture variant')
    raw=(ROOT/(('seed_' if public_seed else '')+lang+'.txt')).read_text()
    pieces=re.split(r'(?m)^# FAMILY: (\w+)\n|^// FAMILY: (\w+)\n',raw)
    common=pieces[0];bodies={}
    for i in range(1,len(pieces),3):bodies[pieces[i] or pieces[i+1]]=pieces[i+2]
    code=(common+bodies[family]).replace('__STAGE__',stage)
    if lang=='python':
        if layout=='modular':return {'main.py':PY_MAIN,'logic.py':code}
        return {'main.py':code+'\n'+PY_MAIN.replace('from logic import solve\n','')}
    if lang=='typescript':
        if layout=='modular':return {'main.ts':TS_MAIN,'logic.ts':code,'node-shim.d.ts':SHIM}
        return {'main.ts':code+'\n'+TS_MAIN.replace("import {solve} from './logic';\n",''),'node-shim.d.ts':SHIM}
    # Go package files compile together; split implementation/main without cross-language wrappers.
    if layout=='flat':return {'main.go':code+GO_MAIN}
    return {'logic.go':code+'\nvar _ = bufio.NewScanner\nvar _ = fmt.Println\n',
            'main.go':'package main\nimport ("bufio";"encoding/json";"fmt";"os")\n'+GO_MAIN}



def initial_files(task: str) -> dict[str,str]:
    m=metadata(task);lang=m['language']
    if m['kind']=='brownfield':
        files=reference_files(task,'seed',public_seed=True)
        issues={'events':'duplicate customers are not aggregated', 'routing':'3xx is incorrectly successful',
                'ledger':'successful idempotency receipts are ignored', 'scheduler':'dependency waves are flattened',
                'store':'state is not saved across requests or restarts','outbox':'state is not saved across requests or restarts'}
        files['README.md']='Legacy program: repair '+issues[m['family']]+'. Preserve valid behavior and public wire contracts.\n'
    elif lang=='python':files={'main.py':'# Implement the documented JSON-lines contract.\n'}
    elif lang=='typescript':files={'main.ts':'// Implement the documented JSON-lines contract.\nexport {};\n','node-shim.d.ts':SHIM}
    else:files={'main.go':'package main\nfunc main(){}\n'}
    files.setdefault('README.md','Small offline project. Internal structure is your choice.\n')
    return files
