"""Compile once, run repeatedly. Explicit trust boundary and language-specific runtime.

Host mode is ONLY for trusted calibration fixtures. It is not a sandbox.
Docker mode has no fallback, never pulls images, and expects image IDs from preflight.
"""
from __future__ import annotations
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .execution import (ProcessResult, clean_env, run_bounded, docker_binary,
                        docker_entrypoint, DOCKER_READY, remove_container)
from .workspace import materialize, sanitized

ENTRY = {'python':'main.py','typescript':'main.ts','go':'main.go'}
COMPILE_SECONDS = 120
EXECUTION_SECONDS = 8


def runtime_manifest() -> dict:
    commands = {'python':[sys.executable,'--version'], 'node':['node','--version'],
                'typescript':['tsc','--version'], 'go':['go','version']}
    result={'platform':platform.platform(),'machine':platform.machine(),'tools':{}}
    for name, command in commands.items():
        binary=shutil.which(command[0])
        if not binary:result['tools'][name]={'available':False};continue
        try:
            r=subprocess.run([binary,*command[1:]],capture_output=True,text=True,timeout=10)
            result['tools'][name]={'available':r.returncode==0,'version':(r.stdout+r.stderr).strip(),
                                    'path':binary,'binary_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest()}
        except (OSError,subprocess.TimeoutExpired) as e:result['tools'][name]={'available':False,'error':type(e).__name__}
    result['manifest_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def _env(home: Path, state: Path | None = None) -> dict:
    env=clean_env(str(home));env.update(TZ='UTC',PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',
        GO111MODULE='off',GOPROXY='off',GOSUMDB='off',GOTOOLCHAIN='local',CGO_ENABLED='0',GOMAXPROCS='1')
    # Calibration-only cache amortizes stdlib compilation. Compile time is never a score.
    cache=Path(tempfile.gettempdir())/'changebench-trusted-go-cache'
    cache.mkdir(mode=0o700,exist_ok=True)
    env['GOCACHE']=str(cache)
    if state is not None:env['CB_STATE_DIR']=str(state)
    return env


def docker_command(source: Path, build: Path, image: str, name: str, argv: list[str],
                   state: Path | None = None, compiling=False) -> list[str]:
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
        raise ValueError('Poly runtime requires a pre-resolved immutable image ID')
    flags=[docker_binary(),'run','--entrypoint=/bin/sh','--name',name,'--network=none','--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--pids-limit=128','--memory=2g','--memory-swap=2g',
        '--cpus=1','--user=65534:65534','--tmpfs=/tmp:rw,exec,size=512m',
        '--mount',f'type=bind,src={source.resolve()},dst=/src,readonly',
        '--mount',f'type=bind,src={build.resolve()},dst=/build'+('' if compiling else ',readonly'),
        '--workdir=/src','--interactive']
    env={'HOME':'/tmp','TZ':'UTC','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONHASHSEED':'0',
         'GOCACHE':'/tmp/go-cache','GO111MODULE':'off','GOPROXY':'off','GOSUMDB':'off',
         'GOTOOLCHAIN':'local','CGO_ENABLED':'0','GOMAXPROCS':'1'}
    if state is not None:
        flags+=['--mount',f'type=bind,src={state.resolve()},dst=/state'];env['CB_STATE_DIR']='/state'
    for key,value in env.items():flags+=['--env',key+'='+value]
    session=os.getenv('CHANGEBENCH_RUN_ID','')
    if re.fullmatch('[0-9a-f]{32}',session):flags+=['--label','changebench.session='+session]
    return flags+[image,*docker_entrypoint(argv)]


class PreparedProgram:
    def __init__(self, files: dict[str,str], language: str, backend='docker', image=None,
                 trusted_local=False, compile_timeout=COMPILE_SECONDS, startup_timeout=30):
        if language not in ENTRY:raise ValueError('Unsupported language')
        if backend not in ('docker','local'):raise ValueError('Unsupported backend')
        if backend=='local' and not trusted_local:raise ValueError('Host execution requires trusted_local=True')
        if backend=='docker' and not docker_binary():raise RuntimeError('Docker unavailable; no host fallback')
        self.files=sanitized(files)
        if ENTRY[language] not in self.files:raise ValueError('Required entrypoint missing: '+ENTRY[language])
        self.language,self.backend,self.image=language,backend,image
        self.compile_timeout=compile_timeout;self.startup_timeout=startup_timeout
        self.temp=None;self.compilation=None

    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory(prefix='changebench-poly-')
        self.root=Path(self.temp.name);self.root.chmod(0o755)
        self.source=self.root/'source';self.build=self.root/'build'
        materialize(self.files,self.source);self.build.mkdir(mode=0o777);self.build.chmod(0o777)
        for p in [self.source,*self.source.rglob('*')]:p.chmod(0o755 if p.is_dir() else 0o644)
        try:
            self.compilation=self._compile()
            return self
        except BaseException:
            self.temp.cleanup();raise

    def __exit__(self,*args):
        if self.temp:self.temp.cleanup()

    def _binary(self,name):
        if self.backend=='docker':return name
        value=shutil.which(name)
        if not value:raise RuntimeError('Missing runtime: '+name)
        return value

    def _invoke(self, argv, data, state=None, timeout=EXECUTION_SECONDS, compiling=False):
        name='changebench-poly-'+uuid.uuid4().hex
        command=argv if self.backend=='local' else docker_command(self.source,self.build,self.image,name,argv,state,compiling)
        result=ProcessResult("infra_error",None,"","Execution did not start",0)
        try:
            result=run_bounded(command,data,self.source,timeout=timeout,output_limit=512000,
                               env=_env(self.root,state) if self.backend=='local' else clean_env(str(self.root)),
                               ready_prefix=DOCKER_READY if self.backend=='docker' else None,
                               startup_timeout=self.startup_timeout)
        finally:
            if self.backend=='docker':
                remove_container(name,result)
        return result

    def _compile(self):
        build=str(self.build) if self.backend=='local' else '/build'
        if self.language=='python':
            return ProcessResult('ok',0,'','No separate Python compilation step.',0.0)
        if self.language=='typescript':
            tsc=self._binary('tsc')
            # tsc has an env-node shebang; invoke the resolved Node explicitly in host mode.
            command=[self._binary('node'),tsc] if self.backend=='local' else [tsc]
            sources=sorted(k for k in self.files if k.endswith('.ts'))
            command+=['--target','ES2022','--module','commonjs','--strict','--skipLibCheck',
                      '--noEmitOnError','--outDir',build,*sources]
        else:command=[self._binary('go'),'build','-trimpath','-buildvcs=false','-o',build+'/app','.']
        result=self._invoke(command,b'',timeout=self.compile_timeout,compiling=True)
        if result.status=='runtime_error':result.status='compile_error'
        return result

    def run(self, data: bytes, state: Path, timeout=EXECUTION_SECONDS):
        if not self.compilation or self.compilation.status!='ok':return self.compilation
        if state.is_symlink():raise ValueError("State root must not be a symlink")
        state.mkdir(parents=True,exist_ok=True);state.chmod(0o777)
        if self.language=='python':
            source=str(self.source) if self.backend=='local' else '/src'
            command=[sys.executable if self.backend=='local' else 'python','-I','-S','-B','-c',
                     'import sys,runpy;sys.path.insert(0,'+repr(source)+');runpy.run_path("main.py",run_name="__main__")']
        elif self.language=='typescript':command=[self._binary('node'),str(self.build/'main.js') if self.backend=='local' else '/build/main.js']
        else:command=[str(self.build/'app') if self.backend=='local' else '/build/app']
        return self._invoke(command,data,state,timeout)

    def run_tests(self, timeout=30):
        state=self.root/'public-tests-state';state.mkdir(exist_ok=True);state.chmod(0o777)
        if self.language=='python':
            source=str(self.source) if self.backend=='local' else '/src'
            command=[sys.executable if self.backend=='local' else 'python','-I','-S','-B','-c',
                'import sys,unittest;sys.path.insert(0,'+repr(source)+');s=unittest.defaultTestLoader.discover(".");r=unittest.TextTestRunner(verbosity=2).run(s);sys.exit(not r.wasSuccessful())']
        elif self.language=='typescript':
            tests=sorted(k for k in self.files if k.endswith(('.test.ts','_test.ts')))
            if not tests:return ProcessResult('no_tests',None,'','No .test.ts or _test.ts tests found.',0.0)
            prefix=str(self.build) if self.backend=='local' else '/build'
            command=[self._binary('node'),'--test',*[prefix+'/'+k[:-3]+'.js' for k in tests]]
        else:command=[self._binary('go'),'test','-count=1','-timeout=20s','.']
        return self._invoke(command,b'',state,timeout,compiling=self.language=='go')


def execute_public(files, data, language, image, timeout=120, action='run', backend='docker', trusted_local=False):
    """Model tool: only caller input/tests, never controller cases or oracles."""
    if action not in ('run','test'):raise ValueError('Unknown public action')
    if not isinstance(data,str) or len(data.encode())>256000:raise ValueError('Invalid public input')
    with PreparedProgram(files,language,backend,image,trusted_local,compile_timeout=timeout) as program:
        if program.compilation.status!='ok':result=program.compilation
        elif action=='test':result=program.run_tests(timeout=min(timeout,30))
        else:result=program.run(data.encode(),program.root/'public-state',timeout=min(timeout,20))
        return {'status':result.status,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                'compilation_seconds':program.compilation.wall_seconds,'execution_seconds':result.wall_seconds}


def preflight_runtime_images(images, execution_timeout=8, compile_timeout=120, startup_timeout=30):
    """Exercise a known native probe before any paid model call. No implicit pulls."""
    from .execution import strict_json
    probes={
        'python':{'main.py':'import json,sys\nfor line in sys.stdin: print(json.dumps({"ready":True}))\n'},
        'typescript':{'main.ts':'declare function require(n:string):any; declare const process:any;\nfor(const line of require("fs").readFileSync(0,"utf8").trim().split("\\n")) process.stdout.write("{\\"ready\\":true}\\n");\n'},
        'go':{'main.go':'package main\nimport("bufio";"fmt";"os")\nfunc main(){s:=bufio.NewScanner(os.Stdin);for s.Scan(){fmt.Println(`{"ready":true}`)}}\n'},
    }
    checks={}
    for lang,image in sorted(images.items()):
        with PreparedProgram(probes[lang],lang,image=image,compile_timeout=compile_timeout,
                             startup_timeout=startup_timeout) as program:
            if program.compilation.status!='ok':raise RuntimeError('Runtime compile preflight failed: '+lang+' '+json.dumps(program.compilation.__dict__))
            result=program.run(b'{}\n',program.root/'state',execution_timeout)
            try:ok=result.status=='ok' and strict_json(result.stdout)=={'ready':True}
            except ValueError:ok=False
            if not ok:raise RuntimeError('Runtime execution preflight failed: '+lang+' '+json.dumps(result.__dict__))
            checks[lang]={'image':image,'probe':'passed','compilation':program.compilation.__dict__,
                          'execution':result.__dict__}
    return checks
