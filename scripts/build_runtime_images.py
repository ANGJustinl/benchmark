"""Build pinned offline runtime images from already installed, trusted toolchains."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

BASE='python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    docker=shutil.which('docker')
    if not docker:raise SystemExit('Docker required')
    # Base must be prepared explicitly; no implicit image pull during an experiment.
    subprocess.run([docker,'image','inspect',BASE],stdout=subprocess.DEVNULL,check=True)
    tools={name:shutil.which(name) for name in ('node','tsc','go')}
    if not all(tools.values()):raise SystemExit('Install Node, TypeScript and Go before building')
    versions={name:subprocess.check_output([path,'version'] if name=='go' else [path,'--version'],text=True).strip()
              for name,path in tools.items()}
    if versions!={'node':'v22.16.0','tsc':'Version 5.8.3','go':'go version go1.23.2 linux/amd64'}:
        raise SystemExit('Expected pinned Linux amd64 toolchains: '+json.dumps(versions))
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='changebench-image-build-') as temp:
        context=Path(temp);node=context/'tools/node_modules/node/bin/node'
        node.parent.mkdir(parents=True);shutil.copy2(Path(tools['node']).resolve(),node)
        tsc=Path(tools['tsc']).resolve().parent.parent
        shutil.copytree(tsc,context/'tools/node_modules/typescript')
        goroot=Path(subprocess.check_output([tools['go'],'env','GOROOT'],text=True).strip())
        shutil.copytree(goroot,context/'tools/go')
        result={}
        for language in ('python','typescript','go'):
            tag=f'changebench-repair-{language}:0.3.0'
            subprocess.run([docker,'build','--pull=false','--network=none','-f',str(root/f'containers/Dockerfile.{language}'),
                            '-t',tag,str(context)],check=True)
            result[language]=subprocess.check_output([docker,'image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
        result['state']=result['python']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
