"""Freeze the audited association fits for Mentat's dated scenario calculator.

No new variable search, external collection, or production-workbook reads.
Run from the Codex research workspace. Runtime calculation uses only stdlib.
"""
from pathlib import Path
import sys,json,hashlib,argparse
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import t

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--review-root',type=Path,required=True,help='Review workspace containing work/stepwise and outputs/stepwise')
parser.add_argument('--source-root',type=Path,required=True,help='Original research outputs containing OpenSecrets and lobbying fits')
parser.add_argument('--election-input',type=Path,required=True,help='Election forecasting workbook with current_2026 input sheet')
parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'app/data/giving-models-v1.json')
args=parser.parse_args()
W=args.review_root.resolve()
BASE=args.source_root.resolve()
DEST=args.output.resolve()
sys.path.insert(0,str(W/'work/stepwise'))
from analyze import matrices

panels=pd.read_csv(W/'work/stepwise/analysis_case_panels.csv')
selected=pd.read_csv(W/'outputs/stepwise/selected_models.csv')
growth=pd.read_csv(BASE/'opensecrets_tests/regression_panel.csv')
money=pd.read_csv(BASE/'opensecrets_partisanship/money_panel.csv').query('account=="congressional"')
labels={'commercial_banks':'Commercial banks','credit_unions':'Credit unions','auto_light_duty_manufacturing':'Auto manufacturers'}
codes={'commercial_banks':'F03','credit_unions':'F05','auto_light_duty_manufacturing':'T2100'}

def clean(v):
    if isinstance(v,dict):return {k:clean(a) for k,a in v.items()}
    if isinstance(v,list):return [clean(a) for a in v]
    if isinstance(v,np.generic):v=v.item()
    if isinstance(v,float) and not np.isfinite(v):return None
    return v

def model(X,y,names):
    m=sm.OLS(y,X).fit();return dict(terms=names,beta=m.params.tolist(),covariance=m.cov_params().tolist(),ordinary_p=dict(zip(names,m.pvalues)),critical_95=float(t.ppf(.975,m.df_resid)),residual_df=int(m.df_resid),n=len(y),adjusted_r2=float(m.rsquared_adj),residual_sd=float(np.sqrt(m.scale)))

bundle=dict(schema_version=1,model_version='2026-09-27-association-v1',built_on='2026-09-27',currency='2025 USD',inference='Ordinary OLS; conditional on model selection',validation='Historical associations. Scenario intervals cover coefficients, not future outcomes.',industries={},share_models={},evidence=[])
for sector,label in labels.items():
    g=growth[growth.sector==sector].sort_values('cycle_end');hist=[]
    for _,r in g.iterrows():
        hist.append(dict(cycle=int(r.cycle_end),growth_pct=r.cycle_revenue_growth_pct,prior_growth_pct=r.prior_cycle_revenue_growth_pct,pac_real_2025=r.pac_real_2025,individual_real_2025=r.individual_real_2025,total_real_2025=r.total_real_2025,source_url=r.source_url_x))
    bundle['industries'][sector]=dict(id=sector,label=label,code=codes[sector],growth_mean=g.cycle_revenue_growth_pct.mean(),growth_sd=g.cycle_revenue_growth_pct.std(ddof=1),growth_min=g.cycle_revenue_growth_pct.min(),growth_max=g.cycle_revenue_growth_pct.max(),history=hist,scope=f'OpenSecrets {codes[sector]} whole-industry accounting; separate from company/subindustry report scopes')

for signal in ['control','seats']:
 for chamber in ['House','Senate']:
    case=f'{signal}__{chamber}__pooled__RShare';d=panels[panels.case==case];row=selected[selected.case==case].iloc[0]
    original=matrices(d);mask=int(row.selected_mask) if signal=='control' else 15
    for varying in [False,True]:
        m=original[15] if varying else original[mask];X=m['X'];names=m['names'].copy()
        if varying:
            X=np.column_stack([X]+[d.Forecast.to_numpy()*d.sector.eq(s).to_numpy() for s in sorted(d.sector.unique())[1:]])
            names+=['Forecast:'+s for s in sorted(d.sector.unique())[1:]]
        fit=model(X,d.RShare.to_numpy(),names);ident=f'{signal}_{chamber}'+('_industry' if varying else '')
        fit.update(id=ident,signal=signal,chamber=chamber,industry_slopes=varying,specification='Original full model with industry-specific forecast slopes' if varying else 'AICc-selected pooled model' if signal=='control' else 'Original full seat-gain sensitivity; seat gain was not retained by selected pooled share model',training_cycles=sorted(d.cycle_end.unique().tolist()),history=[])
        for _,r in d.iterrows():
            mm=money[(money.sector==r.sector)&(money.chamber==chamber)&(money.cycle_end==r.cycle_end)].iloc[0]
            fit['history'].append(dict(industry=r.sector,cycle=int(r.cycle_end),share=r.RShare,forecast=r.Forecast,current_control=int(r.RMajNow),growth_pct=r.cycle_revenue_growth_pct,prior_growth_pct=r.prior_cycle_revenue_growth_pct,pres=int(r.Pres),candidate_total_real_2025=(mm.D_nominal+mm.R_nominal)*mm.multiplier_to_2025_dollars))
        bundle['share_models'][ident]=fit

pac=pd.read_csv(BASE/'opensecrets_forward/pac_selected_design.csv');X=pac.drop(columns=['sector','cycle_end','y']);fit=model(X.to_numpy(),pac.y.to_numpy(),list(X.columns));j=fit['terms'].index('G_current')
bundle['pac_model']=dict(beta=fit['beta'][j],ordinary_se=float(np.sqrt(fit['covariance'][j][j])),ordinary_p=fit['ordinary_p']['G_current'],critical_95=fit['critical_95'],n=21,cycles=list(range(2012,2025,2)),specification='Forward-selected PAC growth model with industry and cycle fixed effects; the reference cycle effect is held fixed for scenarios',selection_repeated_p=.101851851851852)
lobby=pd.read_csv(BASE/'r2_lobbying_audit/designs/year_effects__all71.csv');X=lobby.drop(columns=['sector','year','y']);fit=model(X.to_numpy(),lobby.y.to_numpy(),list(X.columns));j=fit['terms'].index('dg')
bundle['lobby_model']=dict(beta_million=fit['beta'][j],ordinary_se_million=float(np.sqrt(fit['covariance'][j][j])),ordinary_p=fit['ordinary_p']['dg'],critical_95=fit['critical_95'],n=len(lobby),industry_pair_counts=lobby.groupby('sector').size().to_dict(),specification='Change in annual real lobbying on change in forecast annual growth, year effects; same pooled slope per industry, calendar effects held fixed',wild_p=.0306927297668038,CR2_p=.0840463212361729)
bundle['evidence']=clean(pd.read_csv(W/'outputs/stepwise/interpretable_effects.csv').query('scope=="pooled"').to_dict('records'))
bundle['industry_slopes']=clean(pd.read_csv(W/'outputs/stepwise/industry_forecast_slopes.csv').to_dict('records'))
bundle['validation_metrics']=clean(pd.read_csv(W/'outputs/stepwise/nested_validation_metrics.csv').query('`case`.str.contains("__pooled__RShare")',engine='python').to_dict('records'))
outlook_path=args.election_input.resolve()
outlook=pd.read_excel(outlook_path,sheet_name='current_2026')
bundle['saved_outlook']=dict(cycle=2026,available_on='2026-09-25',control=dict(description='Saved Sept. 24/25, 2026 Kalshi/Polymarket normalized average; cross-provider sensitivity, not IEM or a live feed'),seats=dict(description='Saved Sept. 18 House / Sept. 22 Senate, 2026 ratings-based expected R seat changes; not a live feed'))
for chamber in ('House','Senate'):
    for signal,table,field in [('control','current_2026_control_probabilities.csv','p_rep_control'),('seats','current_2026_seat_predictions.csv','pred_rep_net_change')]:
        row=outlook[(outlook.table==table)&(outlook.chamber==chamber)].iloc[0]
        bundle['saved_outlook'][signal][chamber]=dict(value=float(row[field])*(100 if signal=='control' else 1),current_control=1,as_of=row['as_of'],source=row.get('sources') if signal=='control' else row.get('source'),notes=row['notes'])
paths=[W/'outputs/stepwise/selected_models.csv',W/'work/stepwise/analysis_case_panels.csv',BASE/'opensecrets_tests/regression_panel.csv',BASE/'opensecrets_forward/pac_selected_design.csv',BASE/'r2_lobbying_audit/designs/year_effects__all71.csv',BASE/'opensecrets_partisanship/money_panel.csv',W/'outputs/stepwise/industry_forecast_slopes.csv',W/'outputs/stepwise/nested_validation_metrics.csv',outlook_path]
bundle['sources']=[dict(file=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
DEST.parent.mkdir(exist_ok=True);DEST.write_text(json.dumps(clean(bundle),indent=2,allow_nan=False),encoding='utf-8')
print('Saved',DEST,'bytes',DEST.stat().st_size)
