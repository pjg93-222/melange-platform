"""Windows-owned loopback request pool for one collecting container.

Only the exact descriptor is mounted. The enclosing existing secret directory
contains owner control and remains private; keys come from the standard loader.
"""
from pathlib import Path
import argparse,json,os,sys,time
sys.path.append(str(Path(__file__).resolve().parents[1]/'app'))
import proclock
from api_request_budget import budget_path,legacy_budget_paths
from api_request_pool import RequestPool,ENDPOINT
from fec_key_store import load_keys


def host_job_database(value, project_root, environment=None):
    environment=os.environ if environment is None else environment
    mappings={'/collect':environment.get('COLLECT_HOST',''),'/mentat':str(Path(project_root).parent)}
    for prefix,host in mappings.items():
        if host and str(value).startswith(prefix+'/'):
            root=Path(host).resolve();target=root.joinpath(str(value)[len(prefix)+1:]).resolve()
            if not target.is_relative_to(root) or not target.is_file():break
            return target
    raise ValueError('Job source database cannot be mapped to an exact existing host path.')


def descriptor(pool,url):
    value=dict(schema='mentat-request-pool-v1',scope='schedule_a_batch_only',endpoint=ENDPOINT,
               url=url,token=pool.token,width=len(pool.keys),key_ids=pool.ids)
    encoded=json.dumps(value)
    if any(key and key in encoded for key in pool.keys):
        raise ValueError('Host pool descriptor refused credential content.')
    return encoded


def serve(directory,watch_pid,source_database,project_root,*,key_loader=load_keys,
          owner_started=proclock.process_started,owner_alive=proclock.alive,transport=None,cap=1000):
    directory=Path(directory)
    if not directory.is_dir() or not (directory/'fec_api_keys').is_file():
        raise ValueError('An existing owner-only compute secret directory is required.')
    started=owner_started(watch_pid)
    if not isinstance(started,int) or started<=0:
        raise ValueError('Wrapper process creation identity is unavailable.')
    keys=key_loader()
    if not keys:raise ValueError('No enabled API keys are available.')
    shared=budget_path(project_root);shared.parent.mkdir(parents=True,exist_ok=True)
    kwargs={} if transport is None else {'transport':transport,'quarantine':False}
    pool=RequestPool(keys,shared,len(keys),legacy_paths=legacy_budget_paths(project_root,extra=(source_database,)),cap=cap,**kwargs)
    config=directory/'request-pool.json'
    if config.exists():
        pool.close();raise ValueError('A host pool descriptor already exists.')
    try:
        encoded=descriptor(pool,pool.start())
        temporary=directory/'request-pool.pending'
        temporary.write_text(encoded,encoding='utf-8');temporary.replace(config)
        while not (directory/'owner.stop').exists() and owner_alive(watch_pid,started):time.sleep(.2)
    finally:
        pool.close()
        config.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory',type=Path,required=True)
    parser.add_argument('--watch-pid',type=int,required=True)
    parser.add_argument('--source-database',required=True)
    parser.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    try:
        source=host_job_database(args.source_database,args.project_root)
        serve(args.run_directory,args.watch_pid,source,args.project_root)
        return 0
    except Exception:
        # No descriptor, token, key, transport exception or credential-bearing URL.
        print('Host request pool refused or failed; collection cannot use a local fallback.',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
