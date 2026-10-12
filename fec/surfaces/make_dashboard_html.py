import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import os as _oT
def _tpl(n): return open(_oT.path.join(_oT.path.dirname(_oT.path.abspath(__file__)),'templates',n),encoding='utf-8').read()
import json,math,os
from moneyball_core import json_for_script,write_text_atomic


def percentile_ranks(values):
    """Tie-aware midrank percentiles as integer values from 0 through 100."""
    scored=[]
    for key,value in values.items():
        if value is None:
            continue
        if isinstance(value,bool):
            raise ValueError('percentile values must be numeric')
        number=float(value)
        if not math.isfinite(number):
            raise ValueError('percentile values must be finite')
        scored.append((key,number))
    scored.sort(key=lambda item:(item[1],str(item[0])))
    out={}
    i=0
    while i<len(scored):
        j=i
        while j+1<len(scored) and scored[j+1][1]==scored[i][1]: j+=1
        percentile=100.0 if len(scored)==1 else 100.0*((i+j)/2.0)/(len(scored)-1)
        rank=int(math.floor(percentile+0.5))
        for k in range(i,j+1): out[scored[k][0]]=rank
        i=j+1
    return out


def efficiency_percentiles(efficiency):
    """Build surface-only percentiles from sparse columns; never mutate the artifact."""
    members=efficiency.get('members') or {}
    investment={name:row for name,row in members.items()
                if (row.get('investment') or {}).get('score') is not None}
    lobbying={name:row for name,row in members.items()
              if (row.get('lobbying') or {}).get('score') is not None
              and (row.get('lobbying') or {}).get('eligible')
              and not (row.get('lobbying') or {}).get('adversary')}
    inv_cols={
        'engine_value':percentile_ranks({n:r['investment']['engine_value_z'] for n,r in investment.items()}),
        'crowding':percentile_ranks({n:r['contest_index'] for n,r in investment.items()}),
        'score':percentile_ranks({n:r['investment']['score'] for n,r in investment.items()}),
    }
    lob_cols={
        'openness':percentile_ranks({n:r['lobbying']['receptivity_z'] for n,r in lobbying.items()}),
        'alignment':percentile_ranks({n:r['lobbying']['alignment_z'] for n,r in lobbying.items()}),
        'room_to_grow':percentile_ranks({n:r['lobbying']['headroom_z'] for n,r in lobbying.items()}),
        'crowding':percentile_ranks({n:r['contest_index'] for n,r in lobbying.items()}),
        'score':percentile_ranks({n:r['lobbying']['score'] for n,r in lobbying.items()}),
    }
    display={}
    for name in members:
        display[name]={
            'investment':({key:column.get(name) for key,column in inv_cols.items()}
                          if name in investment else None),
            'lobbying':({key:column.get(name) for key,column in lob_cols.items()}
                        if name in lobbying else None),
        }
    return {
        'method':'tie-aware midrank percentile, rounded to the nearest integer; higher means more of the named quality',
        'populations':{'investment':len(investment),'lobbying':len(lobbying)},
        'members':display,
    }


def main():
    dash_path=os.environ.get('DASHBOARD_DATA_PATH') or MB_ROOT+r'\dashboard_data.json'
    with open(dash_path,encoding='utf-8') as fh: data=json.load(fh)
    from stance.honors_views import load_honors
    from surfaces.dashboard_honors import verify_dashboard_honors, honors_generation_marker
    honors=load_honors(MB_ROOT)
    verify_dashboard_honors(data,honors)
    from surfaces.staffer_live_integration import inject_navigation
    html=inject_navigation(_tpl('make_dashboard_html_00.tpl'))
    if honors is not None:
        html=html.replace('<head>','<head>\n'+honors_generation_marker(honors),1)
    out=os.environ.get('DASHBOARD_HTML_OUT') or MB_ROOT+r'\Moneyball Dashboard.html'
    write_text_atomic(html.replace('__DATA__',json_for_script(data,separators=(',',':'))),out)
    print('wrote',out,'(%.0f KB)'%(os.path.getsize(out)/1024))
    return 0


if __name__=='__main__':
    _s.exit(main())
