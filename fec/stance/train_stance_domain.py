# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""DOMAIN STANCE CLASSIFIER — Khan et al. (IECE TACS 2025, doi 10.62762/TACS.2025.928069) pipeline
adjusted from Roman Urdu political tweets to the CU press/social domain.  Their recipe, kept faithfully:
  * TF-IDF features -> classical classifiers (Logistic Regression 93%, Decision Tree 92%, SVM 94%,
    Random Forest 95%, Naive Bayes 89% on their 3-class task), then
  * a fine-tuned RoBERTa with PAIRED input ([CLS] context [SEP] text [SEP]) beating them all at 97%,
  * class balancing/augmentation for the rare class, per-class P/R/F1 + macro-F1 reporting.
Domain adjustments: the 'context' half of the pair is the TARGET (Stanceformer/Burnham target
conditioning: 'target: CFPB.'), classes are the user's composed support/oppose/neutral (task=composed)
or hop1 stance-toward-target (task=hop1), the split is the USER'S OWN train/test from the augmented
workbook (never reshuffled — their 80/20 discipline, but user-controlled), and class imbalance is
handled with class_weight='balanced' (their augmentation analog until Sheet 3 candidates are labeled).
Usage:
  python train_stance_domain.py                      # classical baselines (sklearn, CPU, seconds)
  python train_stance_domain.py --task hop1
  python train_stance_domain.py --transformer        # fine-tuned RoBERTa (needs: pip install transformers torch)
  python train_stance_domain.py --transformer --model roberta-base   # paper-exact (distilroberta default: CPU-practical)"""
import json, sys, argparse
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
ap=argparse.ArgumentParser()
ap.add_argument('--task',default='composed',choices=['composed','hop1'])
ap.add_argument('--transformer',action='store_true'); ap.add_argument('--model',default='distilroberta-base')
ap.add_argument('--epochs',type=int,default=4); ap.add_argument('--save',action='store_true')
ap.add_argument('--augment',action='store_true',help='Khan-style dataset augmentation (TRAIN only; test untouched)')
ap.add_argument('--tune',action='store_true',help='5-fold CV hyperparameter sweep on TRAIN; report on untouched test')
a=ap.parse_args()
rows=[json.loads(l) for l in open(B+r'\data\stance\stance_training.jsonl',encoding='utf-8')]
LBL='label_composed' if a.task=='composed' else 'hop1_stance'
def inp(r):   # Khan paired input, target-conditioned (Stanceformer-style explicit target)
    tgt=r.get('target') or 'Credit Unions (direct)'
    src=r.get('speaker_source') or 'member'
    return 'target: %s. speaker: %s. %s'%(tgt,src,(r.get('stance_span') or '')+' '+r['text'][:1500])
tr=[r for r in rows if r['split']=='train' and r.get(LBL)]
te=[r for r in rows if r['split']=='test'  and r.get(LBL)]
CLS=sorted({r[LBL] for r in tr})
print('task=%s | train %d / test %d | classes %s'%(a.task,len(tr),len(te),
      {c:sum(1 for r in tr if r[LBL]==c) for c in CLS}))
Xtr=[inp(r) for r in tr]; ytr=[r[LBL] for r in tr]; Xte=[inp(r) for r in te]; yte=[r[LBL] for r in te]
groups=list(range(len(tr)))   # H3 fix 2026-07-02: group id = base row, so augmented views of one row can never straddle CV folds
if a.augment:
    # Khan et al.'s augmentation, text-domain analog — MULTI-VIEW: each training row also contributes a
    # title-only view and a stance-span-only view (when distinct).  Teaches robustness to SHORT form
    # (tweets!) and lifts the rare classes; test set is never touched.
    Xa,ya,ga=[],[],[]
    for gi,(r,y) in enumerate(zip(tr,ytr)):
        tgt=r.get('target') or 'Credit Unions (direct)'; src=r.get('speaker_source') or 'member'
        views=[]
        if (r.get('title') or '').strip(): views.append(r['title'])
        sp=(r.get('stance_span') or '').strip()
        if sp and sp!=(r.get('title') or '').strip(): views.append(sp)
        for v in views:
            Xa.append('target: %s. speaker: %s. %s'%(tgt,src,v[:1500])); ya.append(y); ga.append(gi)
        if y=='oppose':                       # rare-class upweighting via the extra views twice
            for v in views: Xa.append('target: %s. speaker: %s. %s'%(tgt,src,v[:1500])); ya.append(y); ga.append(gi)
    Xtr=Xtr+Xa; ytr=ytr+ya; groups=groups+ga
    print('augmented: train %d rows (multi-view + oppose upsampling) | classes %s'%(len(Xtr),
          {c:ytr.count(c) for c in CLS}))
def report(name,pred):
    from sklearn.metrics import classification_report,f1_score
    print('--- %s ---'%name)
    print(classification_report(yte,pred,digits=3,zero_division=0))
    return f1_score(yte,pred,average='macro',zero_division=0)
if not a.transformer:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.svm import LinearSVC
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.naive_bayes import MultinomialNB
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.pipeline import make_pipeline,Pipeline
    from sklearn.pipeline import FeatureUnion
    vec=lambda:TfidfVectorizer(ngram_range=(1,2),min_df=2,max_features=40000,sublinear_tf=True)
    def wc_union():   # word + char_wb n-grams: the standard lift for short/informal text
        return FeatureUnion([('w',TfidfVectorizer(ngram_range=(1,2),min_df=2,max_features=40000,sublinear_tf=True)),
                             ('c',TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),min_df=2,max_features=60000,sublinear_tf=True))])
    if a.tune:
        # CV sweep on TRAIN ONLY (the test split stays untouched until the single final report).
        # H3: GroupKFold keyed on base-row id — augment views of one row stay in one fold (no view leakage).
        from sklearn.model_selection import cross_val_score,GroupKFold
        import numpy as np
        grid=[('SVM w C=%.2f'%C,make_pipeline(vec(),LinearSVC(C=C,class_weight='balanced'))) for C in (0.25,0.5,1.0,2.0)]
        grid+=[('SVM w+c C=%.2f'%C,Pipeline([('f',wc_union()),('m',LinearSVC(C=C,class_weight='balanced'))])) for C in (0.25,0.5,1.0,2.0)]
        grid+=[('LogReg w+c C=%.1f'%C,Pipeline([('f',wc_union()),('m',LogisticRegression(C=C,max_iter=3000,class_weight='balanced'))])) for C in (1.0,4.0)]
        bestcv=(None,-1,None)
        for nm,p in grid:
            f1=float(np.mean(cross_val_score(p,Xtr,ytr,groups=groups,cv=GroupKFold(5),scoring='f1_macro')))
            print('  CV5 %-18s macro-F1 %.3f'%(nm,f1))
            if f1>bestcv[1]: bestcv=(nm,f1,p)
        print('CV winner: %s (%.3f) — final fit on full train, ONE report on the untouched test:'%(bestcv[0],bestcv[1]))
        bestcv[2].fit(Xtr,ytr); f1=report(bestcv[0],bestcv[2].predict(Xte)); best=(bestcv[0],f1,bestcv[2])
    else:
        models=[('LogisticRegression',LogisticRegression(max_iter=2000,class_weight='balanced')),
                ('LinearSVM',LinearSVC(class_weight='balanced')),
                ('RandomForest',RandomForestClassifier(n_estimators=400,class_weight='balanced',random_state=7)),
                ('NaiveBayes',MultinomialNB()),
                ('DecisionTree',DecisionTreeClassifier(class_weight='balanced',random_state=7))]
        best=(None,-1)
        for nm,m in models:
            p=make_pipeline(vec(),m); p.fit(Xtr,ytr); f1=report(nm,p.predict(Xte))
            if f1>best[1]: best=(nm,f1,p)
        print('BEST classical: %s macro-F1 %.3f  (Khan et al. ordering: RF>SVM>LogReg>DT>NB; transformer should beat all)'%(best[0],best[1]))
    if a.save:
        import pickle; pickle.dump(best[2],open(B+r'\stance_clf_%s.pkl'%a.task,'wb'))
        from lane_file_io import write_sha256_sidecar; write_sha256_sidecar(B+r'\stance_clf_%s.pkl'%a.task)   # loaders verify this before unpickling
        print('saved stance_clf_%s.pkl + .sha256 sidecar (pipeline: TF-IDF + %s)'%(a.task,best[0]))
else:
    try:
        import torch,numpy as np
        from transformers import (AutoTokenizer,AutoModelForSequenceClassification,TrainingArguments,Trainer)
    except ImportError:
        print('transformers/torch not installed.  pip install transformers torch  (CPU wheel is fine; '
              'distilroberta-base fine-tunes ~450 rows in tens of minutes on CPU).'); sys.exit(1)
    tok=AutoTokenizer.from_pretrained(a.model)
    lab2id={c:i for i,c in enumerate(CLS)}
    class DS(torch.utils.data.Dataset):
        def __init__(s,X,y): s.e=tok(X,truncation=True,padding=True,max_length=256); s.y=[lab2id[v] for v in y]
        def __len__(s): return len(s.y)
        def __getitem__(s,i): return {**{k:torch.tensor(v[i]) for k,v in s.e.items()},'labels':torch.tensor(s.y[i])}
    md=AutoModelForSequenceClassification.from_pretrained(a.model,num_labels=len(CLS))
    # their class-balancing: weight the loss by inverse class frequency
    w=torch.tensor([len(ytr)/max(1,ytr.count(c)) for c in CLS],dtype=torch.float)
    class WT(Trainer):
        def compute_loss(s,model,inputs,return_outputs=False,**kw):
            lb=inputs.pop('labels'); out=model(**inputs)
            loss=torch.nn.functional.cross_entropy(out.logits,lb,weight=w.to(out.logits.device))
            return (loss,out) if return_outputs else loss
    args=TrainingArguments(output_dir=B+r'\_stance_ft',num_train_epochs=a.epochs,per_device_train_batch_size=8,
        learning_rate=2e-5,logging_steps=50,save_strategy='no',report_to=[])
    t=WT(model=md,args=args,train_dataset=DS(Xtr,ytr))
    t.train()
    pred=t.predict(DS(Xte,yte)).predictions.argmax(-1)
    f1=report('%s fine-tuned (%d epochs)'%(a.model,a.epochs),[CLS[i] for i in pred])
    print('transformer macro-F1 %.3f'%f1)
    if a.save: md.save_pretrained(B+r'\stance_ft_%s'%a.task); tok.save_pretrained(B+r'\stance_ft_%s'%a.task); print('saved stance_ft_%s/'%a.task)
