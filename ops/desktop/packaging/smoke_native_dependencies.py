"""Verify private interpreters, numeric/geospatial wheels, R packages and browser."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-browser', action='store_true')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('PYTHON','R_PROFILE','R_ENVIRON','NODE_'))}
    env['PYTHONNOUSERSITE'] = '1'
    run = lambda command, **kwargs: subprocess.check_output(command, env=env, text=True, timeout=120, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), **kwargs)
    with tempfile.TemporaryDirectory(prefix='private-runtime-deps-') as temporary:
        root = Path(temporary)
        script = root/'copied-job.py'
        script.write_text('import sys,json,subprocess,numpy,pandas,pyarrow,pyproj,shapely,pyogrio\nprint(json.dumps({"python":sys.executable,"proj":pyproj.datadir.get_data_dir(),"subprocess":subprocess.check_output([sys.executable,"-s","-c","print(6*7)"],text=True).strip()}))', encoding='utf-8')
        result = {'pythonJob': json.loads(run([str(runtime/'python/python.exe'), '-E', '-s', str(script)]))}
        assert result['pythonJob']['subprocess'] == '42'
        assert Path(result['pythonJob']['python']).resolve().is_relative_to(runtime)
        assert Path(result['pythonJob']['proj']).resolve().is_relative_to(runtime)
        r = runtime/'native/R'
        env.update(R_LIBS_USER=str(r/'library'), R_LIBS_SITE=str(r/'library'), R_PROFILE=os.devnull, R_PROFILE_USER=os.devnull, R_ENVIRON=os.devnull, R_ENVIRON_USER=os.devnull)
        expr = 'p<-jsonlite::fromJSON(commandArgs(TRUE)[1])$requiredPackages; bad<-p[!vapply(p,requireNamespace,logical(1),quietly=TRUE)];cat(jsonlite::toJSON(list(home=R.home(),packages=length(p),failed=bad),auto_unbox=TRUE));if(length(bad))quit(status=1)'
        result['r'] = json.loads(run([str(r/'bin/Rscript.exe'), '--vanilla', '-e', expr, str(r/'dependency-inventory.json')]))
        assert Path(result['r']['home']).resolve() == r.resolve()
        result['node'] = run([str(runtime/'native/Node/node.exe'), '--version']).strip()
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        if args.skip_browser:
            print(json.dumps(result))
            return
        html = root/'empty.html'
        html.write_text('<!doctype html><title>private-runtime-smoke</title><p>native-browser-ready</p>', encoding='utf-8')
        # Avoid pipe handles inherited by a browser's detached crash reporter.
        browser_log = root/'browser.log'
        with browser_log.open('w', encoding='utf-8') as log:
            browser = subprocess.Popen([str(runtime/'native/Chromium/chrome.exe'), '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--user-data-dir='+str(root/'browser-profile'), '--dump-dom', html.as_uri()], env=env, stdout=log, stderr=log, text=True, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                browser.wait(timeout=60)
            finally:
                if os.name == 'nt':
                    subprocess.run(['taskkill.exe','/PID',str(browser.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        browser.stdout = browser_log.read_text(encoding='utf-8',errors='replace')
        browser.stderr = browser.stdout
        result['browser'] = {'exitCode': browser.returncode, 'localPageRendered': 'native-browser-ready' in browser.stdout}
        if not result['browser']['localPageRendered']:
            raise RuntimeError('Bundled browser did not render synthetic local page: '+browser.stderr[-500:])
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
