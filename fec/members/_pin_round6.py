# -*- coding: utf-8 -*-
"""One-shot fingerprint pinning for the round-6 lock (read-only)."""
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
import json, sqlite3
import numpy as np
import members.b5_behavioral_instrument as BI
import members.b5_n_matrix as NM
import members.b5_a_stratum_diag as DIAG
B = MB_ROOT
for f in ('coop_v2_candidate.json', 'behavioral_dispositions.json', 'data/members/voteview_consistency.json',
          'behavior_panel.json', 'data/members/b5_emb_cache_r6_st.npz', 'data/members/b5_emb_meta_r6_st.json'):
    print('%-32s %s' % (f, NM.sha(B + '\\' + f)))
con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
print('speeches %d posts %d' % (con.execute('select count(*) from speeches').fetchone()[0],
                                con.execute('select count(*) from posts').fetchone()[0]))
con.close()
BD, VV, P, MEM, alltext = BI.load_corpus()
CV = json.load(open(B + r'\coop_v2_candidate.json'))['members']
mask = np.array([(CV.get(m) or {}).get('cooperation_v2_z') is not None for m in MEM])
X, keep = DIAG.member_matrix('st_mean', MEM)
print('MEM %d MEM_A %d | st_mean keep %d (== MEM: %s) dims %d' % (len(MEM), int(mask.sum()),
      len(keep), keep == MEM, X.shape[1]))
