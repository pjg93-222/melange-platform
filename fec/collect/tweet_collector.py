# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
from moneyball_core import dump_atomic   # S2-03: crash-safe artifact write
import roster
"""CONGRESSIONAL SOCIAL-MEDIA COLLECTOR + DATABASE (user-directed 2026-07-01: "was the data collector
for politicians tweets included? We need a very robust database").

HONEST CONTEXT: Fink et al. (2023) shipped their collection code (tweet_fetcher.py etc. on Zenodo,
https://zenodo.org/record/8253486), but it targets the 2022-era Twitter API v2 academic track that no
longer exists — X now sells read access in paid tiers, and the community mirrors (e.g. alexlitel/
congresstweets) stopped when that happened.  So this module IS the collector, built for today's reality:
  * X (Twitter) API v2 adapter    — works when X_BEARER_TOKEN is set (paid tier; incremental since_id).
  * Bluesky adapter               — PUBLIC AppView API, NO KEY NEEDED; a growing share of members post
                                     there (coverage skews D — flagged in `status`).
  * Handle registry               — seeded from the canonical unitedstates/congress-legislators
                                     legislators-social-media.json, joined to our roster by bioguide.
Storage: SQLite `social_media.db` (WAL, UNIQUE-keyed idempotent upserts, per-handle checkpoints) with a
normalized `interactions` table (retweet/quote/reply/mention events) so the Fink influence network and
the Zhou-Elejalde stance-dynamics windows can be built straight off the DB.

Usage:
  python fec/collect/tweet_collector.py init                       # create db + seed handles (roster members)
  python fec/collect/tweet_collector.py fetch --platform bluesky   # keyless; --limit-members N for a smoke run
  python fec/collect/tweet_collector.py fetch --platform x         # needs env X_BEARER_TOKEN
  python fec/collect/tweet_collector.py status                     # coverage + freshness report
  python fec/collect/tweet_collector.py build-influence            # Fink-normalized influence net -> tweet_influence.json
"""
import json,sys,os,sqlite3,time,argparse,datetime,collections,hashlib,urllib.request,urllib.parse
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
DB=B+r'\social_media.db'
UA={'User-Agent':'MelangeSystems-collector/1.0 (research)'}
def db():
    c=sqlite3.connect(DB,timeout=60); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
    try: c.execute('ALTER TABLE members ADD COLUMN truth TEXT')   # migration for pre-truth databases
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE posts ADD COLUMN media_json TEXT')  # image/video metadata; no binary blobs
    except sqlite3.OperationalError: pass
    return c
SCHEMA='''
CREATE TABLE IF NOT EXISTS members(bioguide TEXT PRIMARY KEY, name TEXT, party TEXT, chamber TEXT,
  twitter TEXT, twitter_id TEXT, bluesky TEXT, truth TEXT, facebook TEXT, youtube TEXT, instagram TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS posts(platform TEXT, post_id TEXT, bioguide TEXT, created_at TEXT, text TEXT,
  reply_to TEXT, quote_of TEXT, repost_of TEXT, like_count INT, repost_count INT, reply_count INT,
  fetched_at TEXT, raw TEXT, media_json TEXT, PRIMARY KEY(platform,post_id));
CREATE TABLE IF NOT EXISTS interactions(platform TEXT, kind TEXT, src_bioguide TEXT, dst_bioguide TEXT,
  post_id TEXT, created_at TEXT, PRIMARY KEY(platform,kind,post_id,dst_bioguide));
CREATE TABLE IF NOT EXISTS fetch_log(platform TEXT, bioguide TEXT, checkpoint TEXT, last_run TEXT,
  n_total INT DEFAULT 0, status TEXT, PRIMARY KEY(platform,bioguide));
CREATE INDEX IF NOT EXISTS ix_posts_member ON posts(bioguide,created_at);
CREATE INDEX IF NOT EXISTS ix_inter ON interactions(src_bioguide,dst_bioguide);'''
def get(url,tries=3,timeout=25):
    for t in range(tries):
        try:
            rq=urllib.request.Request(url,headers=UA)
            if os.environ.get('X_BEARER_TOKEN') and 'api.twitter.com' in url:
                rq.add_header('Authorization','Bearer '+os.environ['X_BEARER_TOKEN'])
            return json.load(urllib.request.urlopen(rq,timeout=timeout))
        except urllib.error.HTTPError as e:
            if e.code==429:
                # `continue` on the LAST iteration fell off the end of the function and returned
                # None; every caller then did d.get(...) and logged "ERR:'NoneType' object has no
                # attribute 'g'", masking a rate limit as a code bug. Raise instead, and actually
                # honour Retry-After (the old comment claimed to and never read the header).
                if t==tries-1: raise
                _ra=(e.headers.get('Retry-After') or '').strip()
                time.sleep(min(60,int(_ra) if _ra.isdigit() else 15*(t+1))); continue
            if e.code<500 or t==tries-1: raise   # 4xx (other than 429) is definitive: retrying cannot change it
            time.sleep(3)
        except Exception:
            if t==tries-1: raise
            time.sleep(3)
def cmd_init():
    c=db(); c.executescript(SCHEMA)
    cong={m['member']:m for m in json.load(open(B+r'\data\engine\congestion.json'))}
    bio={m['bioguide']:n for n,m in cong.items() if m.get('bioguide')}
    smp=B+r'\data\stance\legislators_social_media.json'
    if not os.path.exists(smp):   # canonical registry (unitedstates/congress-legislators)
        d=urllib.request.urlopen('https://unitedstates.github.io/congress-legislators/legislators-social-media.json',timeout=30).read()
        open(smp,'wb').write(d)
    reg=json.load(open(smp,encoding='utf-8'))
    n=tw=bs=0
    for e in reg:
        bg=e.get('id',{}).get('bioguide')
        if bg not in bio: continue
        s=e.get('social',{})
        c.execute('INSERT INTO members(bioguide,name,twitter,twitter_id,bluesky,facebook,youtube,instagram,updated) '
                  'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(bioguide) DO UPDATE SET twitter=excluded.twitter,'
                  'twitter_id=excluded.twitter_id,bluesky=excluded.bluesky,updated=excluded.updated',
                  (bg,bio[bg],s.get('twitter'),str(s.get('twitter_id') or ''),s.get('bluesky'),
                   s.get('facebook'),s.get('youtube'),s.get('instagram'),datetime.datetime.now().isoformat()))
        n+=1; tw+=bool(s.get('twitter')); bs+=bool(s.get('bluesky'))
    c.commit()
    print('seeded %d/%d members from the canonical registry: %d twitter handles, %d bluesky handles'%(n,roster.count(),tw,bs))
    if bs==0: print('  (registry carries no bluesky field yet — add handles manually: UPDATE members SET bluesky=? WHERE name=?)')
    print('db: %s'%DB)
def _upsert_post(c,plat,pid,bg,created,text,reply_to,quote_of,repost_of,likes,reposts,replies,raw,media=None):
    c.execute('INSERT INTO posts(platform,post_id,bioguide,created_at,text,reply_to,quote_of,'
              'repost_of,like_count,repost_count,reply_count,fetched_at,raw,media_json) '
              'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,post_id) DO UPDATE SET '   # refetch refreshes the engagement counts only
              'like_count=excluded.like_count,repost_count=excluded.repost_count,reply_count=excluded.reply_count',
              (plat,pid,bg,created,text,reply_to,quote_of,repost_of,likes,reposts,replies,
               datetime.datetime.now().isoformat(),json.dumps(raw,ensure_ascii=True)[:4000],
               json.dumps(media or [],ensure_ascii=True)[:12000]))
def _log(c,plat,bg,ck,n,status):
    c.execute('INSERT INTO fetch_log VALUES(?,?,?,?,?,?) ON CONFLICT(platform,bioguide) DO UPDATE SET '
              'checkpoint=excluded.checkpoint,last_run=excluded.last_run,n_total=n_total+excluded.n_total,status=excluded.status',
              (plat,bg,ck,datetime.datetime.now().isoformat(),n,status))
def verified_bluesky_handle(value):
    text=str(value or '').strip()
    return bool(text and text.lower()!='none-found' and 'UNVERIFIED' not in text.upper()
                and not any(char.isspace() for char in text))


def bluesky_post_url(uri):
    """Use the original AT record authority, never the reposting member's handle.

    AT Protocol: https://atproto.com/specs/at-uri-scheme
    """
    match=_re.fullmatch(r'at://([^/?#]+)/app\.bsky\.feed\.post/([A-Za-z0-9._~:-]+)',str(uri or ''))
    if not match: return ''
    actor,key=match.groups()
    if not (actor.startswith('did:') or verified_bluesky_handle(actor)) or key in ('.','..'):
        return ''
    return 'https://bsky.app/profile/'+urllib.parse.quote(actor,safe=':.-_~')+'/post/'+key


def bluesky_repost_event_id(actor_did, original_uri, reason):
    """Preserve the publisher's repost URI, with an explicit local-event fallback."""
    uri=str(reason.get('uri') or '')
    if uri.startswith('at://'+actor_did+'/app.bsky.feed.repost/'):
        return uri
    key=hashlib.sha256((original_uri+'\n'+str(reason.get('indexedAt') or '')).encode('utf-8')).hexdigest()
    return 'event:bluesky-repost:'+actor_did+':'+key


def _bluesky_optional_object(value, field):
    if value is None:
        return {}
    if not isinstance(value,dict):
        raise ValueError('Bluesky '+field+' must be an object or null')
    return value


def bluesky_post_references(record, *, strict=False):
    """AppView may serialize unused reply/embed union fields as explicit nulls."""
    reply=_bluesky_optional_object(record.get('reply'),'record.reply')
    parent=_bluesky_optional_object(reply.get('parent'),'record.reply.parent')
    reply_uri=parent.get('uri')
    embed=_bluesky_optional_object(record.get('embed'),'record.embed')
    embedded_record=_bluesky_optional_object(embed.get('record'),'record.embed.record')
    embed_type=embed.get('$type')
    if embed_type=='app.bsky.embed.recordWithMedia':
        embedded_record=_bluesky_optional_object(embedded_record.get('record'),
                                                'record.embed.record.record')
    quote_uri=embedded_record.get('uri')
    if strict:
        if record.get('reply') is not None and not bluesky_post_url(reply_uri):
            raise ValueError('Bluesky reply lacks its parent post identity')
        if ((embed_type in ('app.bsky.embed.record','app.bsky.embed.recordWithMedia')
             or quote_uri is not None) and not bluesky_post_url(quote_uri)):
            raise ValueError('Bluesky quote embed lacks its quoted post identity')
    return reply_uri,quote_uri


def fetch_bluesky(limit_members=None,pages=3,*,verified_only=False,strict=False,since=None):
    """Public AppView — no auth.  Captures posts + reply/quote/mention edges via facets."""
    c=db(); rows=c.execute("SELECT bioguide,name,bluesky FROM members WHERE bluesky IS NOT NULL AND bluesky!='' AND bluesky!='none-found'").fetchall()
    all_rows=rows   # full registry: mention targets must not shrink with the verified/skip/limit batch filters
    h2bg={h.split()[0].lower():bg for bg,_,h in all_rows if (h or '').split() and (not verified_only or verified_bluesky_handle(h))}
    registered=len(rows)
    if verified_only:
        rows=[row for row in rows if verified_bluesky_handle(row[2])]
    unverified_excluded=registered-len(rows)
    if os.environ.get('BSKY_SKIP_FETCHED') and not strict:   # strict daily collection always refreshes the current window
        done={r[0] for r in c.execute("SELECT bioguide FROM fetch_log WHERE platform='bluesky' AND status='ok'")}
        rows=[r for r in rows if r[0] not in done]
    rows=[(bg,nm,(h or '').split()[0]) for bg,nm,h in rows]   # strip the ' [UNVERIFIED — confirm]' review tag
    if limit_members: rows=rows[:limit_members]
    results=[]
    print('bluesky fetch: %d members with handles'%len(rows))
    for bg,name,handle in rows:
        got=0; cursor=None; finished=False
        try:
            actor_did=None
            if verified_only:
                profile=get('https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor='+urllib.parse.quote(handle))
                if (str(profile.get('handle') or '').casefold()!=handle.casefold()
                        or not str(profile.get('did') or '').startswith('did:')):
                    raise ValueError('registered Bluesky handle did not resolve to its authenticated profile')
                actor_did=profile['did']
            for _ in range(pages):
                u='https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=%s&limit=100'%urllib.parse.quote(handle)
                if cursor: u+='&cursor='+urllib.parse.quote(cursor)
                d=get(u)
                if not isinstance(d,dict) or not isinstance(d.get('feed'),list):
                    raise ValueError('Bluesky feed response is incomplete')
                older=False
                for it in d.get('feed',[]):
                    p=it.get('post',{}); rec=p.get('record',{})
                    pid=p.get('uri',''); created=rec.get('createdAt',''); text=rec.get('text','')
                    reason=it.get('reason') or {}
                    repost=reason.get('$type','').endswith('#reasonRepost')
                    pinned=reason.get('$type','').endswith('#reasonPin')
                    event_time=reason.get('indexedAt') if repost else created
                    if since and str(event_time or '')[:10] < since:
                        if not event_time: raise ValueError('Bluesky item has no collection-window timestamp')
                        if not pinned: older=True
                        continue
                    if verified_only and not repost and (p.get('author') or {}).get('did')!=actor_did:
                        raise ValueError('Bluesky feed item author differs from the verified member')
                    if verified_only and repost and (reason.get('by') or {}).get('did')!=actor_did:
                        raise ValueError('Bluesky repost actor differs from the verified member')
                    if strict and (not bluesky_post_url(pid) or not created or (repost and not event_time)):
                        raise ValueError('Bluesky feed item lacks a post identity or timestamp')
                    reply,quote=bluesky_post_references(rec,strict=strict)
                    event_id=bluesky_repost_event_id(actor_did or (reason.get('by') or {}).get('did') or bg,pid,reason) if repost else pid
                    provenance={'original_post_uri':pid,'author_did':(p.get('author') or {}).get('did'),
                                'actor_did':actor_did,'repost_uri':reason.get('uri') if repost else None,
                                'repost_indexed_at':event_time if repost else None}
                    _upsert_post(c,'bluesky',event_id,bg,event_time if repost else created,text,reply,quote,pid if repost else None,
                                 p.get('likeCount',0),p.get('repostCount',0),p.get('replyCount',0),provenance,
                                 p.get('embed') or rec.get('embed') or {})
                    # mention edges from facets (handle -> member)
                    for f in ([] if repost else rec.get('facets',[]) or []):
                        for feat in f.get('features',[]):
                            hm=(feat.get('did') and None) or feat.get('handle') or ''
                            if feat.get('$type','').endswith('#mention'):
                                tgt=h2bg.get((feat.get('handle') or '').lower())
                                if tgt and tgt!=bg:
                                    c.execute('INSERT OR IGNORE INTO interactions VALUES(?,?,?,?,?,?)',
                                              ('bluesky','mention',bg,tgt,pid,created))
                    got+=1
                cursor=d.get('cursor')
                if not cursor or older:
                    finished=True
                    break
            if strict and not finished:
                raise RuntimeError('Bluesky page budget exhausted before the requested window completed')
            _log(c,'bluesky',bg,cursor or '',got,'ok'); c.commit()
            results.append({'bioguide':bg,'handle':handle,'status':'COMPLETE','posts_observed':got})
            print('  %-24s +%d posts'%(name,got))
        except Exception as e:
            _log(c,'bluesky',bg,'',got,'ERR:'+str(e)[:80]); c.commit(); print('  %-24s ERROR %s'%(name,str(e)[:80]))
            results.append({'bioguide':bg,'handle':handle,'status':'FAIL','posts_observed':got,
                            'error':type(e).__name__+': '+str(e)[:500]})
    c.close()
    failures=sum(row['status']!='COMPLETE' for row in results)
    return {'schema':'bluesky-source-receipt-v1','status':'FAIL' if failures else 'COMPLETE',
            'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'verified_only':verified_only,'strict':strict,'since':since,'page_budget':pages,
            'registered_handles':registered,'selected_handles':len(rows),
            'unverified_handles_excluded':unverified_excluded,'error_count':failures,'results':results}
def fetch_x(limit_members=None,first_pages=1):
    """X API v2 — needs env X_BEARER_TOKEN (paid tier).  Incremental via since_id checkpoints; captures
    referenced_tweets (retweet/quote/reply) + entity mentions, mirroring Fink et al.'s request shape.
    Follows meta.next_token so a gap of >100 tweets is read in full before the checkpoint advances."""
    if not os.environ.get('X_BEARER_TOKEN'):
        print('X_BEARER_TOKEN not set — X fetch skipped.  (X read access is paid-tier only since 2023;'); print(' the Bluesky adapter needs no key.)'); return
    c=db(); rows=c.execute("SELECT bioguide,name,twitter_id,twitter FROM members WHERE twitter_id IS NOT NULL AND twitter_id!=''").fetchall()
    id2bg={r[2]:r[0] for r in rows}   # full registry, built before the limit slice
    if limit_members: rows=rows[:limit_members]
    for bg,name,uid,handle in rows:
        ck=c.execute('SELECT checkpoint FROM fetch_log WHERE platform=? AND bioguide=?',('x',bg)).fetchone()
        since_id=ck[0] if ck and ck[0] else None
        cap=35 if since_id else first_pages   # X serves at most 3200 timeline tweets = 32 pages, so 35 is a loop guard; the first fetch is a bounded backfill
        u=('https://api.twitter.com/2/users/%s/tweets?max_results=100&tweet.fields=created_at,referenced_tweets,entities,public_metrics,attachments'
           '&expansions=referenced_tweets.id.author_id,entities.mentions.username,attachments.media_keys'
           '&media.fields=media_key,type,url,preview_image_url,alt_text,width,height,duration_ms,variants'%uid)+('&since_id='+since_id if since_id else '')
        got=0; newest=None; token=None
        try:
            for _ in range(cap):
                d=get(u+('&pagination_token='+urllib.parse.quote(token) if token else ''))
                media_by_key={str(item.get('media_key')):item for item in
                              (d.get('includes',{}).get('media') or []) if item.get('media_key')}
                for t in d.get('data',[]) or []:
                    pid=t['id']; newest=pid if newest is None or int(pid)>int(newest) else newest   # ids compare numerically, not as strings
                    rt=q=rp=None
                    for ref in t.get('referenced_tweets',[]) or []:
                        if ref['type']=='retweeted': rt=ref['id']
                        elif ref['type']=='quoted': q=ref['id']
                        elif ref['type']=='replied_to': rp=ref['id']
                    pm=t.get('public_metrics',{})
                    media=[media_by_key[key] for key in (t.get('attachments',{}).get('media_keys') or [])
                           if key in media_by_key]
                    _upsert_post(c,'x',pid,bg,t.get('created_at',''),t.get('text',''),rp,q,rt,
                                 pm.get('like_count',0),pm.get('retweet_count',0),pm.get('reply_count',0),t,media)
                    # interaction edges: referenced authors + mentions that are members
                    for iu in (d.get('includes',{}).get('tweets') or []):
                        if iu['id'] in (rt,q,rp) and iu.get('author_id') in id2bg and id2bg[iu['author_id']]!=bg:
                            kind='retweet' if iu['id']==rt else ('quote' if iu['id']==q else 'reply')
                            c.execute('INSERT OR IGNORE INTO interactions VALUES(?,?,?,?,?,?)',
                                      ('x',kind,bg,id2bg[iu['author_id']],pid,t.get('created_at','')))
                    for mn in (t.get('entities',{}).get('mentions') or []):
                        tb=id2bg.get(str(mn.get('id','')))
                        if tb and tb!=bg:
                            c.execute('INSERT OR IGNORE INTO interactions VALUES(?,?,?,?,?,?)',
                                      ('x','mention',bg,tb,pid,t.get('created_at','')))
                    got+=1
                token=(d.get('meta') or {}).get('next_token')
                if not token: break
            # incremental gap not fully read -> keep the OLD checkpoint (ERR below); a first fetch (no checkpoint) is a bounded backfill and advances
            if token and since_id: raise RuntimeError('X page cap hit before reaching checkpoint')
            _log(c,'x',bg,newest or (ck[0] if ck else ''),got,'ok'); c.commit(); print('  %-24s +%d tweets'%(name,got))
        except Exception as e:
            _log(c,'x',bg,ck[0] if ck else '',got,'ERR:'+str(e)[:80]); c.commit(); print('  %-24s ERROR %s'%(name,str(e)[:80]))
# ---- CU-domain relevance (Khan et al.: domain keyword/hashtag scoping of the collection) ----
import re as _re
CU_TERMS=_re.compile(r'credit union|\bncua\b|\bcfpb\b|interchange|credit card competition|\bccca\b|durbin|'
    r'tax exempt|tax status|field of membership|\bcdfi\b|overdraft|swipe fee|late fee|debit card|'
    r'consumer financial protection|share insurance|community bank|big bank',_re.I)
def relevance(text): return 1 if CU_TERMS.search(text or '') else 0
def fetch_truth(limit_members=None,pages=3):
    """Truth Social — a Mastodon fork, so the Mastodon client API shapes apply
    (/api/v1/accounts/lookup, /api/v1/accounts/:id/statuses with max_id paging; reblog/reply fields;
    mentions in the status entities).  UNLIKE Bluesky there is no public unauthenticated AppView:
    you need an account token in env TRUTH_TOKEN (Authorization: Bearer), and Cloudflare may still
    challenge datacenter IPs — run from a residential connection.  Respect ToS/rate limits; this is
    low-volume research collection of PUBLIC official-account posts, mirroring the Twitter-API terms
    the Fink/Khan datasets operated under.  Handles: fill members.truth manually or via probe —
    coverage is heavily R-skewed (the D skew of Bluesky, mirrored), so use BOTH platforms before
    inferring anything population-level."""
    tok=os.environ.get('TRUTH_TOKEN')
    if not tok:
        print('TRUTH_TOKEN not set — Truth Social fetch skipped.  Create an account, pull the OAuth bearer'); print(' from the web app (Network tab), set TRUTH_TOKEN, and fill members.truth handles.'); return
    c=db(); rows=c.execute("SELECT bioguide,name,truth FROM members WHERE truth IS NOT NULL AND truth!=''").fetchall()
    rows=[(bg,nm,(h or '').split()[0].lstrip('@')) for bg,nm,h in rows]
    h2bg={r[2].lower():r[0] for r in rows}   # full registry, built before the limit slice
    if limit_members: rows=rows[:limit_members]
    def tget(u):
        rq=urllib.request.Request(u,headers={**UA,'Authorization':'Bearer '+tok})
        return json.load(urllib.request.urlopen(rq,timeout=25))
    print('truth social fetch: %d members with handles'%len(rows))
    for bg,name,handle in rows:
        got=0
        try:
            acct=tget('https://truthsocial.com/api/v1/accounts/lookup?acct='+urllib.parse.quote(handle))
            aid=acct['id']; max_id=None
            for _ in range(pages):
                u='https://truthsocial.com/api/v1/accounts/%s/statuses?limit=40&exclude_replies=false'%aid
                if max_id: u+='&max_id='+max_id
                sts=tget(u)
                if not sts: break
                for s in sts:
                    pid=s['id']; max_id=pid
                    txt=_re.sub(r'<[^>]+>',' ',s.get('content') or '')
                    rb=(s.get('reblog') or {}).get('id'); rp=s.get('in_reply_to_account_id')
                    _upsert_post(c,'truth',pid,bg,s.get('created_at',''),txt,rp,None,rb,
                                 s.get('favourites_count',0),s.get('reblogs_count',0),s.get('replies_count',0),{})
                    for mn in s.get('mentions',[]) or []:
                        tb=h2bg.get((mn.get('acct') or '').lower())
                        if tb and tb!=bg:
                            c.execute('INSERT OR IGNORE INTO interactions VALUES(?,?,?,?,?,?)',
                                      ('truth','mention',bg,tb,pid,s.get('created_at','')))
                    got+=1
                time.sleep(1.0)   # be gentle: no published rate limits; Mastodon default is 300/5min
            _log(c,'truth',bg,max_id or '',got,'ok'); c.commit(); print('  %-24s +%d truths'%(name,got))
        except Exception as e:
            _log(c,'truth',bg,'',got,'ERR:'+str(e)[:80]); c.commit(); print('  %-24s ERROR %s'%(name,str(e)[:80]))
def fetch_keywords(limit=100):
    """Khan et al.'s SECOND collection channel: domain keyword/hashtag search (their #ImranKhan/#PMLN ->
    our CU_TERMS), so the corpus captures the DEBATE around members, not just their own accounts.
    X recent-search endpoint (paid tier).  Posts land with bioguide=NULL; the stance classifier +
    mention extraction attribute them afterward."""
    if not os.environ.get('X_BEARER_TOKEN'): print('X_BEARER_TOKEN not set — keyword search skipped.'); return
    c=db()
    q=urllib.parse.quote('("credit union" OR NCUA OR CFPB OR interchange OR "Credit Card Competition Act") lang:en -is:retweet')
    d=get('https://api.twitter.com/2/tweets/search/recent?query=%s&max_results=%d&tweet.fields=created_at,author_id,public_metrics'%(q,min(100,limit)))
    got=0
    for t in d.get('data',[]) or []:
        pm=t.get('public_metrics',{})
        _upsert_post(c,'x',t['id'],None,t.get('created_at',''),t.get('text',''),None,None,None,
                     pm.get('like_count',0),pm.get('retweet_count',0),pm.get('reply_count',0),t); got+=1
    c.commit(); print('keyword search: +%d domain posts (unattributed; classify + attribute downstream)'%got)
def cmd_status():
    c=db()
    for plat in ('x','bluesky'):
        n=c.execute('SELECT COUNT(*),COUNT(DISTINCT bioguide) FROM posts WHERE platform=?',(plat,)).fetchone()
        i=c.execute('SELECT COUNT(*) FROM interactions WHERE platform=?',(plat,)).fetchone()[0]
        print('%-8s %6d posts from %3d members | %5d member-member interaction events'%(plat,n[0],n[1],i))
    h=c.execute("SELECT SUM(twitter IS NOT NULL AND twitter!=''),SUM(bluesky IS NOT NULL AND bluesky!='' AND bluesky!='none-found'),COUNT(*) FROM members").fetchone()
    print('handles: twitter %s / bluesky %s of %s members  (bluesky coverage skews D — check before inference)'%h)
    for r in c.execute("SELECT platform,COUNT(*) FROM fetch_log WHERE status LIKE 'ERR%' GROUP BY platform"): print('errors:',r)
def cmd_influence(min_posts=int(os.environ.get('TW_MIN_POSTS','25'))):
    """Fink et al. 2023, verbatim: P(i influences j) = (# of j's retweets/quotes/replies/mentions of i)
    / (# posts issued by i), with a minimum-activity floor (theirs: 100 tweets; dial TW_MIN_POSTS)."""
    c=db()
    items=dict(c.execute('SELECT bioguide,COUNT(*) FROM posts GROUP BY bioguide').fetchall())
    eng=collections.Counter()
    for src,dst in c.execute('SELECT src_bioguide,dst_bioguide FROM interactions'): eng[(dst,src)]+=1   # dst engaged BY src -> dst influences src
    cong={m['bioguide']:m['member'] for m in json.load(open(B+r'\data\engine\congestion.json')) if m.get('bioguide')}
    out=[]
    for (i,j),n in eng.items():
        if items.get(i,0)>=min_posts:
            out.append({'src':cong.get(i,i),'dst':cong.get(j,j),'p':round(min(1.0,n/items[i]),4),'events':n})
    dump_atomic({'method':'Fink et al. 2023: engagements normalized by author volume; floor %d posts'%min_posts,
                 'edges':sorted(out,key=lambda e:-e['p'])},B+r'\tweet_influence.json',indent=1)
    print('wrote tweet_influence.json (%d directed influence edges) — feed to build_multilayer_network.py when ready'%len(out))
def cmd_discover(limit=None):
    """Probe Bluesky for member handles.  Official congressional accounts verify via DOMAIN handles
    (@warren.senate.gov style) — those are cryptographically domain-verified, so a resolve hit IS the
    member and is auto-accepted.  Plain {name}.bsky.social pattern hits are recorded as 'unverified?'
    for the user to confirm (a fan account can squat a name)."""
    c=db(); rows=c.execute("SELECT bioguide,name,twitter FROM members WHERE bluesky IS NULL OR bluesky=''").fetchall()
    if limit: rows=rows[:limit]
    allnames=[r[1] for r in c.execute('SELECT bioguide,name FROM members')]
    surcount=collections.Counter(n.split()[-1].lower() for n in allnames)
    taken={ (r[0] or '').split()[0].lower() for r in c.execute("SELECT bluesky FROM members WHERE bluesky IS NOT NULL AND bluesky!=''") }
    found=0
    for bg,name,tw in rows:
        last=name.split()[-1].lower().replace("'",'')
        amb=surcount[last]>1     # surname shared by >1 member: bare-surname domain patterns are unsafe (both Smiths -> smith.senate.gov)
        cands=[(None if amb else '%s.senate.gov'%last,'verified'),(None if amb else 'rep%s.house.gov'%last,'verified'),
               (((tw or '').lower()+'.bsky.social') if tw else None,'unverified?'),
               (None if amb else 'rep%s.bsky.social'%last,'unverified?')]
        cands=[(cd,tg) for cd,tg in cands if cd and cd.lower() not in taken]
        hit=False; definitive=True   # definitive = every candidate got a clean 4xx (not 429) or a 200 without a did
        for cand,tag in cands:
            if not cand: continue
            try:
                d=get('https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle='+urllib.parse.quote(cand),tries=1,timeout=10)
                if d.get('did'):
                    hit=True
                    c.execute('UPDATE members SET bluesky=? WHERE bioguide=?',(cand+('' if tag=='verified' else ' [UNVERIFIED — confirm]'),bg))
                    c.commit()   # commit per hit: chunked/interrupted runs keep their progress
                    taken.add(cand.lower()); print('  %-24s -> @%s (%s)'%(name,cand,tag),flush=True); found+=1; break
            except urllib.error.HTTPError as e:
                if e.code==429 or not 400<=e.code<500: definitive=False   # rate limit / 5xx say nothing about the handle
                continue
            except Exception: definitive=False; continue   # network error / timeout: inconclusive
        if not hit and definitive:
            c.execute("UPDATE members SET bluesky='none-found' WHERE bioguide=?",(bg,)); c.commit()   # sentinel: don't re-probe on the next chunk
        elif not hit: print('  %-24s probe inconclusive, will re-probe'%name,flush=True)   # bluesky stays NULL so the next run retries
        time.sleep(0.15)
    c.commit(); print('discovered %d handles over %d probed members (verified = domain handle; confirm the unverified ones)'%(found,len(rows)))
def cmd_classify():
    """Score every collected post with the domain stance classifier (train_stance_domain.py --save) +
    the relevance filter -> member-level social stance signals, same shape stance_dynamics.py consumes."""
    import pickle; from lane_file_io import safe_to_unpickle   # refuse a swapped model before unpickling it
    pk=B+r'\data\stance\stance_clf_composed.pkl'
    if not os.path.exists(pk): print('train first: python fec/stance/train_stance_domain.py --save'); return
    if not safe_to_unpickle(pk): print('refusing to load %s: it fails its integrity sidecar; retrain: python fec/stance/train_stance_domain.py --save'%pk); return
    clf=pickle.load(open(pk,'rb')); c=db()
    rows=c.execute('SELECT platform,post_id,bioguide,created_at,text FROM posts').fetchall()
    rel=[(p,pid,bg,dt,tx) for p,pid,bg,dt,tx in rows if relevance(tx)]
    print('posts: %d total, %d CU-relevant (CU_TERMS filter)'%(len(rows),len(rel)))
    if not rel: return
    preds=clf.predict(['target: Credit Unions (direct). speaker: member. '+tx for *_,tx in rel])
    out=[{'platform':p,'post_id':pid,'bioguide':bg,'date':dt,'stance':st,'text':tx[:280]}
         for (p,pid,bg,dt,tx),st in zip(rel,preds)]
    dump_atomic(out,B+r'\social_stance_signals.json',indent=1)
    agg=collections.Counter((r['bioguide'],r['stance']) for r in out if r['bioguide'])
    print('wrote social_stance_signals.json (%d scored posts, %d member-stance cells) — a NEW evidence'%(len(out),len(agg)))
    print('channel for the press pillar / stance_dynamics, provenance=social (user adjudicates adoption)')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('cmd',choices=['init','fetch','status','build-influence','discover-bluesky','search','classify'])
    ap.add_argument('--platform',default='bluesky',choices=['bluesky','x','truth']); ap.add_argument('--limit-members',type=int)
    ap.add_argument('--verified-only',action='store_true')
    ap.add_argument('--strict',action='store_true')
    ap.add_argument('--since',help='earliest Bluesky activity date to collect (YYYY-MM-DD)')
    ap.add_argument('--pages',type=int,default=3)
    ap.add_argument('--receipt',type=Path)
    a=ap.parse_args(argv)
    if a.pages<1: ap.error('--pages must be positive')
    if a.since: datetime.date.fromisoformat(a.since)
    if (a.strict or a.verified_only or a.receipt or a.since) and (a.cmd!='fetch' or a.platform!='bluesky'):
        ap.error('strict collection receipts and verified handles are currently implemented for Bluesky only')
    if a.cmd=='init': cmd_init()
    elif a.cmd=='fetch':
        if a.platform=='bluesky':
            result=fetch_bluesky(a.limit_members,a.pages,verified_only=a.verified_only,strict=a.strict,since=a.since)
            if a.receipt:
                a.receipt.parent.mkdir(parents=True,exist_ok=True)
                dump_atomic(result,str(a.receipt),indent=2)
            return 1 if a.strict and result['error_count'] else 0
        {'x':fetch_x,'truth':fetch_truth}[a.platform](a.limit_members)
    elif a.cmd=='status': cmd_status()
    elif a.cmd=='discover-bluesky': cmd_discover(a.limit_members)
    elif a.cmd=='search': fetch_keywords()
    elif a.cmd=='classify': cmd_classify()
    else: cmd_influence()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
