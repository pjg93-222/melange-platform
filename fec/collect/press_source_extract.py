"""Verified official-site article containers and publication-date extraction."""
import re
from datetime import datetime, date
from urllib.parse import urlsplit

MONTH_DATE = re.compile(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}\b',re.I)

def extract_source_article(html, url, expected=''):
    from bs4 import BeautifulSoup
    host=(urlsplit(url).hostname or '').removeprefix('www.')
    if not host.endswith(('.house.gov','.senate.gov')): return None
    soup=BeautifulSoup(html,'html.parser')
    title_tokens=set(re.findall(r'[a-z]{4,}',(expected or '').lower()))
    headings=[h.get_text(' ',strip=True) for h in soup.select('h1')]
    title=max(headings,key=lambda h:len(title_tokens & set(re.findall(r'[a-z]{4,}',h.lower()))),default='')
    if not title or re.search(r'page not found|access denied|just a moment|download file',title,re.I): return None
    if title_tokens and len(title_tokens & set(re.findall(r'[a-z]{4,}',title.lower())))<min(2,len(title_tokens)): return None
    published='';date_source=''
    for selector,attribute in [('meta[property="article:published_time"]','content'),('meta[name="date"]','content'),('time[datetime]','datetime')]:
        node=soup.select_one(selector)
        if node and re.match(r'^\d{4}-\d{2}-\d{2}',node.get(attribute,'')):
            published=node[attribute][:10];date_source=selector;break
    if not published:
        for node in soup.select('.elementor-heading-title, .posted-on, .press-date, .date, time'):
            match=MONTH_DATE.search(node.get_text(' ',strip=True))
            if not match: continue
            try:
                published=datetime.strptime(match.group().replace(',',''),'%B %d %Y').date().isoformat()
                date_source='visible_article_date';break
            except ValueError: pass
    if published:
        try:
            if not date(1900,1,1)<=date.fromisoformat(published)<=date.today(): published='';date_source=''
        except ValueError: published='';date_source=''
    selectors=['.elementor-widget-theme-post-content','.evo-press-release__body','.js-press-release']
    if host in ('huffman.house.gov','takano.house.gov'): selectors.insert(0,'#press')
    if host=='jeffries.house.gov': selectors.insert(0,'div.main')
    for selector in selectors:
        container=soup.select_one(selector)
        if container is None: continue
        for node in container.select('script,style,nav,footer,aside,form,.pum-content,.share,.social-share'):
            node.decompose()
        body=re.sub(r'\s+',' ',container.get_text(' ',strip=True)).strip()
        if len(body.split())<20 or len(body)<200: continue
        if re.search(r'verify you are human|enable javascript and cookies|access denied',body[:350],re.I): continue
        return dict(body=body,title_detail=title,date_detail=published,date_source=date_source,
                    extractor='source:'+selector)
    return None
