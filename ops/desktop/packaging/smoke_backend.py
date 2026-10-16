"""Launch a real native/backend entrypoint with empty independent data and test its boundary.

An empty-data startup is not a full feature or data acceptance result.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.error import HTTPError, URLError


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app', required=True, choices=['melange','terrier','mentat'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--output', type=Path)
    options=parser.parse_args()
    if options.runtime:
        options.runtime = options.runtime.resolve()
        options.source = options.runtime / 'sources'
    if not options.source:
        parser.error('--source or --runtime is required')
    with tempfile.TemporaryDirectory(prefix='desktop-native-smoke-') as temporary:
        root=Path(temporary)
        (root/'data').mkdir()
        (root/'state').mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port=listener.getsockname()[1]
        token=secrets.token_urlsafe(32)
        environment={k:v for k,v in os.environ.items() if not k.startswith(('CU_','ACCESS_','MENTAT_','MONEYBALL_','MELANGE_'))}
        environment['DESKTOP_LAUNCH_TOKEN']=token
        command = ([str(options.runtime / 'python' / 'python.exe'), '-E', '-s', str(options.runtime / 'bootstrap' / 'desktop_backend.py')]
                   if options.runtime else [str(options.binary)] if options.binary else [sys.executable,str(Path(__file__).with_name('desktop_backend.py'))])
        command+=['--app',options.app,'--port',str(port),'--source-root',str(options.source.resolve()),'--data-root',str(root/'data'),'--state-root',str(root/'state')]
        log=root/'backend.log'
        with log.open('w',encoding='utf-8') as output:
            process=subprocess.Popen(command,env=environment,stdout=output,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result={'appId':options.app,'nativeBinary':bool(options.binary or options.runtime),'runtimeKind':'private-cpython' if options.runtime else 'legacy', 'boundaryReady':False,'fullFeatureAcceptance':False}
        try:
            deadline=time.monotonic()+120
            url=f'http://127.0.0.1:{port}/__desktop/health'
            while time.monotonic()<deadline and process.poll() is None:
                try:
                    request=urllib.request.Request(url,headers={'X-Desktop-Token':token})
                    with urllib.request.urlopen(request,timeout=1) as response:
                        answer=json.load(response)
                    if answer.get('appId')==options.app:
                        result['boundaryReady']=True
                        break
                except (OSError,URLError,ValueError):
                    time.sleep(.25)
            if result['boundaryReady']:
                try:
                    urllib.request.urlopen(url,timeout=2)
                    result['unauthorizedRefused']=False
                except HTTPError as error:
                    result['unauthorizedRefused']=error.code==403
                try:
                    with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/',headers={'X-Desktop-Token':token}),timeout=60) as response:
                        result['interfaceStatus']=response.status
                        result['interfaceBytes']=len(response.read())
                except HTTPError as error:
                    result['interfaceStatus']=error.code
                except OSError as error:
                    result['interfaceError']=type(error).__name__
                    result['failureTail']=log.read_text(encoding='utf-8',errors='replace')[-2200:]
            else:
                result['exitCode']=process.poll()
                result['failureTail']=log.read_text(encoding='utf-8',errors='replace')[-2200:]
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        print(json.dumps(result,indent=2))
        if options.output:
            options.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        return 0 if result.get('boundaryReady') and result.get('unauthorizedRefused') and result.get('interfaceStatus') == 200 else 1


if __name__=='__main__':
    raise SystemExit(main())
