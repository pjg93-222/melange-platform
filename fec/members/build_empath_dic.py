# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""EMPATH -> LIWC-format dictionary generator (user-directed 2026-07-03: "Go with Empath").
Replaces the eight GRADED CONTENT categories of big5_traits with Empath's large validated lexicons
(Fast, Chen & Bernstein 2016 — built from embeddings, validated against LIWC). The closed-class
function-word categories (i, we, you, article, negate) and the cognitive exemplars (insight, certain,
tentat) are NOT emitted — they stay on big5_traits' own lists (function words are already complete;
Empath has no cognitive-process categories). Output: fec/liwc/empath_categories.dic in LIWC .dic
format, which big5_traits' parity hook loads automatically (partial replacement by category NAME).
Each category is the UNION of the mapped Empath categories and the existing exemplar seed list, so
recall can only grow. Single-token [a-z'] terms only (the corpus tokenizer is single-word).
Rerun any time; deterministic output (sorted)."""
import io, os, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from empath import Empath

# our category name -> Empath categories to union
MAP = {
    'posemo':  ['positive_emotion', 'joy', 'optimism'],
    'negemo':  ['negative_emotion'],
    'anx':     ['nervousness', 'fear'],
    'anger':   ['anger'],
    'social':  ['communication', 'friends'],
    'family':  ['family', 'children'],
    'achieve': ['achievement'],
    'work':    ['work', 'business'],
    # expansion 2026-07-03 ("optimize as much as possible"): the classic LIWC-family categories from
    # Mairesse's original feature space that Empath carries — consumed only via FITTED weights
    'money':   ['money'],
    'relig':   ['religion'],
    'swear':   ['swearing_terms'],
    'leisure': ['leisure'],
    'body':    ['body'],
    'health':  ['health'],
    'death':   ['death'],
    'sad':     ['sadness'],
}
# exemplar seeds copied from big5_traits.CAT (keep in sync — big5_traits has no __main__ guard, so
# importing it would execute the whole corpus build; duplication is the lesser evil here)
SEED = {
 'posemo':"good great love happy hope proud thank thanks celebrate honor honored best wonderful glad support win wins success benefit benefits opportunity grateful appreciate excellent congratulations congrats delighted terrific amazing",
 'negemo':"bad wrong hate hurt nasty terrible awful fail failed failure crisis threat worry worried fear afraid angry anger sad loss lose damage harm danger attack disaster outrage shameful disgrace broken devastating reckless",
 'anx':"worried anxious afraid fear fearful nervous risk risky threat threatened threatens uncertain uncertainty alarm alarming danger dangerous crisis vulnerable insecure unstable",
 'anger':"angry anger outrage outrageous furious fight fighting attack attacks hate disgusting shameful corrupt corruption betray betrayal rigged radical disgrace hostile",
 'social':"talk talked share friend friends family families community communities neighbor neighbors together meeting met join joined joining team people folks everyone partnership colleagues constituents neighbors town",
 'family':"family families mother father mom dad son daughter kids children parents grandmother grandfather wife husband brother sister grandparents",
 'achieve':"work working achieve achieved achievement accomplish accomplished succeed success successful win won effort efforts goal goals deliver delivered results earn earned lead leader leadership fought secured secure passed",
 'work':"job jobs work working business businesses economy economic employment worker workers industry wages career manufacturing farmers",
 'money':"money cost costs price prices dollar dollars fund funds funding budget tax taxes pay paid spending afford debt",
 'relig':"god faith church prayer pray blessed bless religious christian jewish muslim worship holy",
 'swear':"damn hell crap",
 'leisure':"vacation game games sports fun play movie music concert hobby travel weekend",
 'body':"body hands hand head heart blood eyes feet skin",
 'health':"health healthy sick illness disease doctor hospital medicine medical care treatment patient patients",
 'death':"death die died dying dead killed kill funeral grief mourn",
 'sad':"sad sadness cry crying tears grief heartbroken sorrow disappointed loss",
}
TOKEN = re.compile(r"^[a-z']+$")

lex = Empath()
cats = {}
for name, srcs in MAP.items():
    terms = set(SEED[name].split())
    for s in srcs:
        for t in lex.cats[s]:
            t = t.strip().lower()
            if TOKEN.match(t): terms.add(t)
    cats[name] = sorted(terms)
    print('  %-8s <- %-40s %4d terms (seeds %d)' % (name, '+'.join(srcs), len(terms), len(SEED[name].split())))

outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc')
os.makedirs(outdir, exist_ok=True)
outp = os.path.join(outdir, 'empath_categories.dic')
ids = {name: str(i + 1) for i, name in enumerate(sorted(cats))}
word2ids = {}
for name, terms in cats.items():
    for t in terms: word2ids.setdefault(t, []).append(ids[name])
with io.open(outp, 'w', encoding='utf-8', newline='\n') as fh:
    fh.write('%\n')
    for name in sorted(cats): fh.write('%s\t%s\n' % (ids[name], name))
    fh.write('%\n')
    for w in sorted(word2ids): fh.write('%s\t%s\n' % (w, '\t'.join(sorted(word2ids[w]))))
print('wrote %s (%d categories, %d distinct terms)' % (outp, len(cats), len(word2ids)))
