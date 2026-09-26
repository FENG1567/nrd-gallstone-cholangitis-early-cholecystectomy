#!/usr/bin/env python3
"""Amendment 004 frozen overlap-target day-level clone-censor-IPCW analysis.

This program is intentionally restricted to the pre-frozen protocol.  Its
public output is aggregate only; identifiers and patient-level rows never leave
the private execution directory.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, faulthandler, cProfile, io, pstats, time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, SplineTransformer
faulthandler.enable()
# The licensed NRD root is supplied explicitly by the authorized user at run
# time.  No server, home-directory, or credential path is embedded in this
# public package.
PRIVATE=Path(os.environ.get('NRD_PRIVATE_ROOT', '.'));PRIMARY=(2018,2019,2020);SEED=20260915;SMALL=10
OFFICIAL_FRAME_PREFIX='official_nrd_hospital_frame_'
def n(d,c): return pd.to_numeric(d[c] if c in d.columns else pd.Series(np.nan,index=d.index),errors='coerce')
def sup(x): return 'SUPPRESSED_LE_10' if int(x)<=SMALL else int(x)
def boot_quantile(values,multiplicity,probs):
 """Exact empirical quantile under collapsed integer bootstrap multiplicities (never survey-weighted)."""
 v=np.asarray(values,float);m=np.asarray(multiplicity,int)
 return np.quantile(np.repeat(v,np.maximum(m,0)),probs) if len(v) and m.sum()>0 else np.full(len(probs),np.nan)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def bil(x):
 x=re.sub('[^A-Za-z0-9]','',str(x)).upper();return x.startswith(('K80','K81','K82','K851')) or x in {'K830','K8309','K831'}
def yrroot(r,y): return r / str(y)
def official_frame_path(private,y): return yrroot(Path(private),y)/(f'{OFFICIAL_FRAME_PREFIX}{y}.csv')
def load_official_frame(private,y):
 """Private official annual Hospital frame.  It is not hospital_linked."""
 p=official_frame_path(private,y)
 f=pd.read_csv(p,dtype={'YEAR':'Int64','HOSP_NRD':'string','NRD_STRATUM':'string'})
 required=['YEAR','HOSP_NRD','NRD_STRATUM']
 if list(f.columns)!=required or len(f)==0 or f[required].isna().any().any(): raise RuntimeError('official frame schema or missing-value failure')
 f['YEAR']=pd.to_numeric(f.YEAR,errors='coerce').astype('Int64');f['HOSP_NRD']=f.HOSP_NRD.astype(str);f['NRD_STRATUM']=f.NRD_STRATUM.astype(str)
 if not f.YEAR.eq(int(y)).all() or f.duplicated(['YEAR','HOSP_NRD']).any() or f.duplicated(['YEAR','HOSP_NRD','NRD_STRATUM']).any(): raise RuntimeError('official frame duplicate/year failure')
 hs=f.groupby(['YEAR','NRD_STRATUM'],sort=False).HOSP_NRD.nunique()
 if (hs<2).any(): raise RuntimeError('official frame contains singleton YEAR × NRD_STRATUM')
 return f
def verify_frame_membership(d,frame):
 """Hard-fail before gates if any retained analysis row disagrees with the official frame."""
 if d.HOSP_NRD.isna().any() or d.NRD_STRATUM.isna().any(): raise RuntimeError('analysis hospital/stratum missing before frame contract')
 key=frame.set_index(['YEAR','HOSP_NRD']).NRD_STRATUM
 observed=pd.MultiIndex.from_arrays([pd.to_numeric(d.YEAR,errors='coerce'),d.HOSP_NRD.astype(str)])
 expected=pd.Series(key.reindex(observed).to_numpy(),index=d.index,dtype='object')
 if expected.isna().any(): raise RuntimeError('analysis hospital absent from official annual frame')
 if not expected.astype(str).eq(d.NRD_STRATUM.astype(str)).all(): raise RuntimeError('analysis NRD_STRATUM mismatches official annual frame')
 return d
def cohort_structure(private,y,official_frame=None):
 """Build a strictly no-outcome, no-linked-discharge time-zero cohort."""
 r=yrroot(private,y);d=pd.read_parquet(r / (f'index_candidates_{y}.parquet'));d.columns=[str(x).upper() for x in d.columns];flow={'candidate therapeutic-ERCP admissions':len(d)}
 for label,mask in [('age >=18',n(d,'AGE').ge(18)),('strict phenotype',n(d,'PHENOTYPE_STRICT').eq(1)),('state resident',n(d,'RESIDENT').eq(1)),('non-elective index admission',n(d,'ELECTIVE').eq(0)),('no malignant obstruction/exclusion',n(d,'MALIGNANCY_EXCLUSION').ne(1)),('no prior cholecystectomy/acquired absence',n(d,'PRIOR_CHOLE_EXCLUSION').ne(1)),('not pregnant',n(d,'PREGNANCY_EXCLUSION').ne(1)),('therapeutic ERCP day observed',n(d,'ERCP_DAY_OBSERVED').eq(1))]:
  d=d.loc[mask].copy();flow[label]=len(d)
 e=n(d,'ERCP_DAY_MIN');los=n(d,'LOS');ad=n(d,'NRD_DAYSTOEVENT');d=d.loc[e.between(0,los)&ad.ge(0)].copy();flow['valid ERCP-day chronology and linkage time']=len(d);d['index_discharge_time']=los.loc[d.index].to_numpy(float)-e.loc[d.index].to_numpy(float);d=d.sort_values(['NRD_VISITLINK','NRD_DAYSTOEVENT','KEY_NRD'],kind='mergesort').drop_duplicates('NRD_VISITLINK').copy();flow['first eligible linked admission']=len(d)
 frame=load_official_frame(private,y) if official_frame is None else official_frame
 d['YEAR']=int(y);d['NRD_STRATUM']=d.NRD_STRATUM.astype(str);d=verify_frame_membership(d,frame)
 # hospital_linked is retained only for baseline hospital covariates, never the variance frame.
 h=pd.read_parquet(r / (f'hospital_linked_{y}.parquet'));h.columns=[str(x).upper() for x in h.columns];d=d.merge(h.drop_duplicates('HOSP_NRD'),on='HOSP_NRD',how='left',suffixes=('','_H'))
 c=n(d,'CHOLE_COMPLETE_DAY_MIN');er=n(d,'ERCP_DAY_MIN');has=n(d,'CHOLE_COMPLETE_PRESENT').fillna(0).eq(1);d['same_day']=(has&c.eq(er));d['early_day']=np.where(has&c.gt(er)&c.le(er+3),c-er,np.inf);d['complete_by_day3']=np.isfinite(d['early_day']).astype(int);d['partial_present']=n(d,'CHOLE_PARTIAL_PRESENT').fillna(0).eq(1)
 dx=d[[c for c in d.columns if re.fullmatch(r'I10_DX\d+',c)]].fillna('').astype(str).apply(lambda x:'|'.join(x),axis=1)
 chronic={'CHRONIC_DM':r'(?:^|\|)E1[0-4]','CHRONIC_HTN':r'(?:^|\|)I1[0-6]','CHRONIC_HF':r'(?:^|\|)I50','CHRONIC_PULM':r'(?:^|\|)(?:J4[0-7]|J6[0-7])','CHRONIC_CKD':r'(?:^|\|)N1[89]','CHRONIC_LIVER':r'(?:^|\|)(?:K7[0-7])','CHRONIC_OBESITY':r'(?:^|\|)E66','CHRONIC_DEMENTIA':r'(?:^|\|)(?:F0[0-3]|G30)','CHRONIC_ALCOHOL':r'(?:^|\|)(?:F10|K70)','CHRONIC_COAGULOPATHY':r'(?:^|\|)D6[6-8]','CHRONIC_PVD':r'(?:^|\|)I7[0-3]'}
 for k,p in chronic.items(): d[k]=dx.str.contains(p,regex=True).astype(int)
 return d,flow

def attach_outcomes_after_gate(structure_df,private,y):
 """Attach linked-discharge outcomes only after both horizon structure gates pass.

 This is deliberately the sole function allowed to open ``linked_discharges``.
 ``cohort_structure`` above is outcome-blind by construction.
 """
 r=yrroot(private,y);d=structure_df.loc[structure_df.YEAR.eq(y)].copy()
 # Ownership contract: cohort_structure() is the exclusive owner of these
 # time-zero structural fields.  Outcome attachment is not allowed to rebuild,
 # replace, suffix, or otherwise mutate any of them.
 structural_fields=('same_day','early_day','complete_by_day3','index_discharge_time')
 for field in structural_fields:
  if list(d.columns).count(field)!=1 or field not in d.columns:
   raise RuntimeError(f'outcome attachment missing or non-unique structural field: {field}')
  if f'{field}_x' in d.columns or f'{field}_y' in d.columns:
   raise RuntimeError(f'outcome attachment received suffixed structural field: {field}')
 if list(d.columns).count('KEY_NRD')!=1 or not d.KEY_NRD.is_unique or d.KEY_NRD.isna().any():
  raise RuntimeError('outcome attachment requires unique non-missing KEY_NRD')
 outcome_fields=('death_time','readmit_time','biliary_time','death_index')
 collisions=set(outcome_fields)&set(d.columns)
 if collisions: raise RuntimeError(f'outcome attachment field collision: {sorted(collisions)}')
 # pandas.merge resets a filtered left frame to RangeIndex.  Capture the
 # ordered values on a normalized positional index so this audit is strict
 # about rows/order/values, but correctly indifferent to index labels.
 pre_n=len(d);pre_keys=d.KEY_NRD.reset_index(drop=True).copy(deep=True);pre_structure=d.loc[:,list(structural_fields)].reset_index(drop=True).copy(deep=True)
 # An index death is required only now, after the outcome-blind gates.  It is
 # an outcome datum, not a structural eligibility variable.
 required=['KEY_NRD','NRD_VisitLink','NRD_DaysToEvent','ELECTIVE','DIED','LOS','I10_DX1']
 l=pd.read_parquet(r / (f'linked_discharges_{y}.parquet'),columns=required);l.columns=[str(x).upper() for x in l.columns];groups={str(z):g for z,g in l.groupby('NRD_VISITLINK',sort=False)};o=[]
 for rr in d.itertuples(index=False):
  z=rr._asdict();t0=float(z['NRD_DAYSTOEVENT'])+float(z['ERCP_DAY_MIN']);g=groups.get(str(z['NRD_VISITLINK']));key=str(z['KEY_NRD']);un=[];bi=[];de=[float(z['LOS'])-float(z['ERCP_DAY_MIN'])] if int(float(z['DIED']))==1 else []
  if g is not None:
   g=g.loc[g.KEY_NRD.astype(str).ne(key)].copy();delta=n(g,'NRD_DAYSTOEVENT')-t0;gap=n(g,'NRD_DAYSTOEVENT')-(float(z['NRD_DAYSTOEVENT'])+float(z['LOS']))
   for j,t in enumerate(delta):
    # HCUP follow-up is a later discharge/admission record only when it starts >=1 day after index discharge.
    if not np.isfinite(t) or t<=0 or not np.isfinite(gap.iloc[j]) or gap.iloc[j]<1:continue
    if int(n(g,'DIED').iloc[j])==1:de.append(float(t)+float(n(g,'LOS').iloc[j]))
    if int(n(g,'ELECTIVE').iloc[j])==0:un.append(float(t));bi.append((float(t),bil(g.I10_DX1.iloc[j])))
  death=min(de) if de else np.inf;read=min(un) if un else np.inf;b=min([x for x,q in bi if q],default=np.inf)
  o.append({'KEY_NRD':z['KEY_NRD'],'death_time':death,'readmit_time':read,'biliary_time':b,'death_index':int(float(z['DIED'])==1)})
 outcome=pd.DataFrame(o,columns=['KEY_NRD',*outcome_fields])
 if len(outcome)!=pre_n or list(outcome.columns)!=['KEY_NRD',*outcome_fields] or not outcome.KEY_NRD.is_unique or outcome.KEY_NRD.isna().any():
  raise RuntimeError('outcome attachment produced invalid key/outcome table')
 d=d.merge(outcome,on='KEY_NRD',how='left',validate='one_to_one',sort=False)
 if len(d)!=pre_n or not d.KEY_NRD.reset_index(drop=True).equals(pre_keys): raise RuntimeError('outcome attachment changed row count or KEY_NRD order')
 if not d.loc[:,list(structural_fields)].reset_index(drop=True).equals(pre_structure): raise RuntimeError('outcome attachment changed structural field values')
 if any(c.endswith('_x') or c.endswith('_y') for c in d.columns): raise RuntimeError('outcome attachment produced merge suffix columns')
 added=set(d.columns)-set(structure_df.columns)
 if added!=set(outcome_fields): raise RuntimeError(f'outcome attachment added unexpected fields: {sorted(added)}')
 return d
BASE=['AGE','FEMALE','PAY1','ZIPINC_QRTL','PL_NCHS','HCUP_ED','AWEEKEND','ERCP_DAY_MIN','HOSP_BEDSIZE','H_CONTRL','HOSP_URCAT4','HOSP_UR_TEACH','YEAR','CHRONIC_DM','CHRONIC_HTN','CHRONIC_HF','CHRONIC_PULM','CHRONIC_CKD','CHRONIC_LIVER','CHRONIC_OBESITY','CHRONIC_DEMENTIA','CHRONIC_ALCOHOL','CHRONIC_COAGULOPATHY','CHRONIC_PVD']
BASE_DEMOGRAPHIC=['AGE','FEMALE','PAY1','ZIPINC_QRTL','PL_NCHS','HCUP_ED','AWEEKEND','ERCP_DAY_MIN','HOSP_BEDSIZE','H_CONTRL','HOSP_URCAT4','HOSP_UR_TEACH','YEAR']
ADMIN_WINDOW_PRIMARY={30:(1,11),90:(1,9)}
ADMIN_WINDOW_SENSITIVITY={30:(1,10),90:(1,8)}
# Keep the configuration in the repository-level ``config`` directory rather
# than duplicating it beside the runner.
BASE_CONFIG=Path(__file__).resolve().parents[2] / 'config' / 'V3_FROZEN_BASE_CONFIG.json'
def xform(d,extras,base=BASE):
 x=d.reindex(columns=base).copy()
 for c in base:
  if c not in ('AGE','ERCP_DAY_MIN'):x[c]=x[c].astype(str).fillna('missing')
 for k,v in extras.items():x[k]=v.astype(str)
 nums=['AGE','ERCP_DAY_MIN'];cats=[c for c in x if c not in nums]
 pre=ColumnTransformer([('n',Pipeline([('i',SimpleImputer(strategy='median')),('s',SplineTransformer(n_knots=4,degree=3,include_bias=False))]),nums),('c',Pipeline([('i',SimpleImputer(strategy='most_frequent')),('o',OneHotEncoder(handle_unknown='ignore'))]),cats)])
 return x,pre

def survey_mass(d):
 """NRD design mass, including collapsed Rao--Wu hospital multiplicity."""
 return (n(d,'DISCWT').fillna(1).to_numpy(float)*n(d,'BOOT_MULT').fillna(1).to_numpy(float)*n(d,'RW_SCALE').fillna(1).to_numpy(float))

def fit_propensity_and_target(d,base=BASE):
 """Pre-frozen e(X), 0.05--0.95 support, and one shared overlap target.

 The same DISCWT*e*(1-e) factor is attached to *both* cloned regimes.
 It must never be replaced by arm-specific e or 1-e factors.
 """
 if len(d)==0: raise RuntimeError('empty time-zero cohort')
 y=n(d,'complete_by_day3').fillna(0).astype(int).to_numpy()
 if y.min()==y.max(): raise RuntimeError('propensity outcome has one level')
 x,pre=xform(d,{},base)
 mod=Pipeline([('p',pre),('m',LogisticRegression(C=1.0,solver='lbfgs',max_iter=3000))])
 mod.fit(x,y,m__sample_weight=np.clip(survey_mass(d),1e-8,None))
 e=np.clip(mod.predict_proba(x)[:,1],1e-6,1-1e-6)
 keep=(e>=.05)&(e<=.95)
 q=d.loc[keep].copy()
 q['PROPENSITY_E']=e[keep]
 q['TARGET_BASE_WEIGHT']=survey_mass(q)*q['PROPENSITY_E'].to_numpy(float)*(1-q['PROPENSITY_E'].to_numpy(float))
 meta={'propensity_model':'survey-mass weighted L2 logistic regression, C=1.0; cubic four-knot splines for age and ERCP procedure day; one-hot fixed categorical covariates; NRD_STRATUM excluded','common_support_lower':.05,'common_support_upper':.95,'input_n':int(len(d)),'retained_n':int(len(q)),'excluded_n':int((~keep).sum()),'target_factor':'DISCWT × e(X) × [1-e(X)] × I(0.05≤e(X)≤0.95), common to both clones'}
 return q,meta

def administrative_followup_window(d,horizon,sensitivity=False):
 """Pre-frozen HCUP discharge-month administrative observability restriction.

 DMONTH is never an adjustment variable. It is only the explicitly labelled
 administrative enrollment restriction for in-year NRD linkage, whose possible
 post-baseline selection is reported as a limitation.
 """
 lo,hi=(ADMIN_WINDOW_SENSITIVITY if sensitivity else ADMIN_WINDOW_PRIMARY)[horizon]
 month=n(d,'DMONTH')
 valid=month.notna()&np.isfinite(month)&month.eq(np.floor(month))&month.between(1,12)
 eligible=valid&month.between(lo,hi)
 report={'horizon_days':horizon,'month_min':lo,'month_max':hi,'sensitivity':bool(sensitivity),'n_input':int(len(d)),'n_invalid_or_missing_dmonth':int((~valid).sum()),'n_valid_dmonth_outside_window':int((valid&~month.between(lo,hi)).sum()),'n_retained':int(eligible.sum()),'role':'administrative observability restriction only; DMONTH excluded from propensity/IPCW/target/SMD/outcome models'}
 return d.loc[eligible].copy(),report

def _exact_hash(path,expected,label):
 actual=sha(Path(path))
 if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected.lower()) or actual.lower()!=expected.lower():
  raise RuntimeError(f'hash allowlist mismatch before data read: {label}')
 return actual

def load_approved_allowlist(path):
    """Load a one-time approved allowlist before any source hash or NRD access."""
    x=json.loads(Path(path).read_text(encoding='utf8'))
    if x.get('approval_status')!='APPROVED' or x.get('candidate_only') is not False or x.get('execution_authorization')!='APPROVED_ONE_TIME':
        raise RuntimeError('hash allowlist is not approved for one-time execution; deployment forbidden')
    for key in ('execution_authorization_id','authorized_run_id','authorized_mode','authorized_boot','authorized_run_root','authorization_receipt_path'):
        if key not in x:
            raise RuntimeError(f'approved allowlist missing execution authorization field: {key}')
    for key in ('runner','sap','tests','regression_tests','execution_barrier_tests','authorization_tests','formal_attachment_tests','base_config'):
        if key not in x.get('source_hashes',{}): raise RuntimeError(f'hash allowlist missing source: {key}')
    return x

SAFE_AUTH_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
def _safe_id(value,label):
    if not isinstance(value,str) or not SAFE_AUTH_ID.fullmatch(value) or value in ('.','..'):
        raise RuntimeError(f'approved allowlist has invalid {label}')
    return value
def _canonical_absolute(value,label,require_existing=False,reject_symlink=False):
    if not isinstance(value,str): raise RuntimeError(f'{label} must be an absolute path')
    raw=Path(value)
    if not raw.is_absolute() or '..' in raw.parts: raise RuntimeError(f'{label} must be absolute and traversal-free')
    if require_existing and (not raw.exists() or not raw.is_dir()): raise RuntimeError(f'{label} must be an existing directory')
    if reject_symlink and raw.is_symlink(): raise RuntimeError(f'{label} must not be a symlink')
    return raw.resolve(strict=require_existing)
def validate_cli_output_preflight(private_out,public_out):
    private=_canonical_absolute(private_out,'CLI private_out')
    public=_canonical_absolute(public_out,'CLI public_out')
    if private==public: raise RuntimeError('CLI private_out and public_out must differ')
    return private,public
def validate_run_bound_authorization(allow,a,private_out,public_out):
    """Bind a single approved token to one exact run root and receipt location."""
    token=_safe_id(allow.get('execution_authorization_id'),'execution_authorization_id')
    run_id=_safe_id(allow.get('authorized_run_id'),'authorized_run_id')
    mode=allow.get('authorized_mode')
    if not isinstance(mode,str) or mode not in ('gate-only','dryrun','formal'): raise RuntimeError('approved allowlist has invalid authorized_mode')
    boot=allow.get('authorized_boot')
    if type(boot) is not int: raise RuntimeError('approved allowlist authorized_boot must be a strict integer')
    if mode!=a.mode: raise RuntimeError('approved allowlist authorized_mode does not match CLI mode')
    if boot!=a.boot: raise RuntimeError('approved allowlist authorized_boot does not match CLI boot')
    run_root=_canonical_absolute(allow.get('authorized_run_root'),'authorized_run_root',require_existing=True,reject_symlink=True)
    if run_root.name!=run_id: raise RuntimeError('authorized_run_root basename does not match authorized_run_id')
    if private_out!=run_root/'private' or public_out!=run_root/'public': raise RuntimeError('CLI outputs do not exactly match authorized run root')
    private_root=_canonical_absolute(a.private_root,'CLI private_root',require_existing=True)
    raw_receipt_parent=private_root/'.authorization_receipts'
    if raw_receipt_parent.is_symlink(): raise RuntimeError('authorization receipt parent must pre-exist and must not be a symlink')
    expected_parent=raw_receipt_parent.resolve(strict=False)
    expected_receipt=(expected_parent/f'{token}.json').resolve(strict=False)
    receipt=_canonical_absolute(allow.get('authorization_receipt_path'),'authorization_receipt_path')
    if receipt!=expected_receipt: raise RuntimeError('authorization_receipt_path does not match canonical receipt contract')
    if not expected_parent.exists() or not expected_parent.is_dir() or expected_parent.is_symlink(): raise RuntimeError('authorization receipt parent must pre-exist and must not be a symlink')
    return {'authorization_id':token,'run_id':run_id,'run_root':str(run_root),'private_out':str(private_out),'public_out':str(public_out),'receipt_path':str(receipt)}
def consume_one_time_authorization(binding,mode,boot):
    """Atomically consume the authorization before source hashing or data access."""
    receipt=Path(binding['receipt_path'])
    payload={'execution_authorization_id':binding['authorization_id'],'authorized_run_id':binding['run_id'],'authorized_mode':mode,'authorized_boot':int(boot),'authorized_run_root':binding['run_root'],'private_out':binding['private_out'],'public_out':binding['public_out'],'consumed_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'pid':int(os.getpid())}
    data=(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode('utf8')
    try: fd=os.open(str(receipt),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
    except FileExistsError: raise RuntimeError('one-time authorization receipt already exists; authorization already consumed')
    try:
        total=0
        while total<len(data): total+=os.write(fd,data[total:])
        os.fsync(fd)
    finally: os.close(fd)
    try: os.chmod(receipt,0o400)
    except OSError as exc: raise RuntimeError('authorization receipt chmod failed after consumption') from exc
    return payload

def verify_source_allowlist(allow,sap_path):
    """Exact source/config identity barrier; must run before any data read."""
    targets={'runner':Path(__file__),'sap':Path(sap_path),'tests':Path(__file__).with_name('test_amendment004_v7.py'),'regression_tests':Path(__file__).with_name('test_amendment004_v7_regression.py'),'execution_barrier_tests':Path(__file__).with_name('test_amendment004_v7_execution_barriers.py'),'authorization_tests':Path(__file__).with_name('test_amendment004_v7_authorization.py'),'formal_attachment_tests':Path(__file__).with_name('test_amendment004_v7_formal_attachment.py'),'base_config':BASE_CONFIG}
    for key,path in targets.items(): _exact_hash(path,allow['source_hashes'][key],key)

def _verify_input_group(private,allow,group):
 entries=allow.get('input_hashes',{}).get(group)
 if not isinstance(entries,list) or not entries: raise RuntimeError(f'hash allowlist missing {group} input manifest')
 expected={str(x.get('relative_path')):x.get('sha256') for x in entries}
 required=[]
 for y in PRIMARY:
  if group=='structural': names=(f'index_candidates_{y}.parquet',f'hospital_linked_{y}.parquet')
  elif group=='official_frames': names=(f'{OFFICIAL_FRAME_PREFIX}{y}.csv',)
  elif group=='linked_outcomes': names=(f'linked_discharges_{y}.parquet',)
  else: raise RuntimeError(f'unknown input hash group: {group}')
  required.extend([f'{y}/{name}' for name in names])
 if set(expected)!=set(required): raise RuntimeError(f'hash allowlist {group} manifest does not exactly match frozen input set')
 records=[]
 for rel in required:
  p=Path(private)/rel; records.append({'relative_path':rel,'sha256':_exact_hash(p,expected[rel],f'{group}:{rel}')})
 return records

def static_manifest(allow,structural_inputs=None,linked_inputs=None):
 """Provenance statement only; data hashes were already hard-checked."""
 return {'allowlist_status':allow['approval_status'],'source_hashes':allow['source_hashes'],'structural_inputs':structural_inputs or [],'linked_inputs':linked_inputs or []}
def clone(d,endpoint,horizon,tie='event_first',grace=3,outcome_blind=False):
 """Clone at ERCP procedure-day zero under the fixed day-1--3 rules."""
 rows=[]
 for r in d.itertuples(index=False):
  z=r._asdict()
  if outcome_blind:
   ev=comp=np.inf
  else:
   ev=min(float(z['death_time']),float(z['readmit_time'])) if endpoint=='composite' else (float(z['readmit_time']) if endpoint=='readmit' else (float(z['biliary_time']) if endpoint=='biliary' else float(z['death_time'])))
   comp=float(z['death_time']) if endpoint in ('readmit','biliary') else np.inf
  early=float(z['early_day']);complete=early if np.isfinite(early) and early<=grace else np.inf
  # A live discharge (but never an index death) ends feasibility of completing
  # surgery in that index admission on the following procedure day.
  live_discharge=float(z['index_discharge_time']) if not int(z.get('death_index',0)) else np.inf
  early_censor=min(float(grace+1),live_discharge+1.) if not np.isfinite(complete) else np.inf
  for rg in ('early','not_completed'):
   cen=early_censor if rg=='early' else (complete if np.isfinite(complete) else np.inf)
   # A deadline or post-discharge infeasibility censor occurs *before* the
   # next procedure-day risk set.  Event-first is only a real same-day
   # operation/event tie in the not-completed clone.
   ctype='administrative_deadline_or_discharge' if rg=='early' and np.isfinite(cen) else ('observed_complete_operation' if rg=='not_completed' and np.isfinite(cen) else 'none')
   event_first=(ev<=cen) if (tie=='event_first' and ctype=='observed_complete_operation') else (ev<cen)
   target_before_comp=ev<comp  # death wins an unresolvable same-day competing tie
   risk_exit=min(ev,comp)
   target_event=event_first and target_before_comp and ev<=horizon
   if target_event: stop=min(ev,horizon)
   # A competing death is retained through its own day when it coincides with
   # a *real* complete-operation deviation.  In contrast, deadline and live
   # discharge censoring remove the following risk set even on a same-day
   # death; they are structural, not observed-operation ties.
   elif (comp<cen or (ctype=='observed_complete_operation' and comp==cen)) and comp<=horizon: stop=comp
   else: stop=(min(cen-1,horizon) if np.isfinite(cen) else float(horizon))
   rows.append({**z,'regime':rg,'censor_type':ctype,'event_time':ev,'competing_time':comp,'risk_exit_time':risk_exit,'censor_day':cen,'event_observed':int(target_event),'stop_day':float(stop)})
 return pd.DataFrame(rows)
def _fit_ccw_prepared(d,endpoint,tie='event_first',grace=3,base=BASE,max_horizon=90,outcome_blind=False):
 """Refit frozen e/support and a day-specific pooled-logistic IPCW model."""
 target,pmeta=fit_propensity_and_target(d,base)
 c=clone(target,endpoint,max_horizon,tie,grace,outcome_blind=outcome_blind).reset_index(drop=True)
 c['BOOT_MULT']=n(c,'BOOT_MULT').fillna(1).astype(int)
 c['base_weight']=np.clip(c['TARGET_BASE_WEIGHT'].to_numpy(float),1e-12,None)
 ds=[]
 for t in range(1,grace+1):
  # The risk set is structurally adherent through the prior decision and has
  # not already experienced the endpoint. A same-day endpoint wins its tie.
  prior=(c.censor_day.to_numpy(float)>=t)&(c.risk_exit_time.to_numpy(float)>=t)
  q=c.loc[prior].copy()
  if not len(q): raise RuntimeError(f'empty artificial-censoring risk set day {t}')
  q['clone_id']=q.index.to_numpy(int);q['day_int']=t;q['day']=str(t)
  if t<grace:
   cens=(q.censor_day.to_numpy(float)==t)&(q.event_time.to_numpy(float)>t)
  else:
   cens=((q.regime.eq('not_completed')&q.censor_day.eq(float(t))) | (q.regime.eq('early')&q.censor_day.eq(float(grace+1))))&q.event_time.gt(float(t))
  q['censored_now']=cens.astype(int);ds.append(q)
 daily=pd.concat(ds,ignore_index=True)
 sw=np.clip(survey_mass(daily),1e-8,None); y=daily.censored_now.to_numpy(int)
 if y.min()==y.max(): daily['pc']=float(np.average(y,weights=sw))
 else:
  extras={'regime':daily.regime,'day':daily.day,'regime_day':daily.regime.astype(str)+'__'+daily.day.astype(str)}
  x,pre=xform(daily,extras,base)
  m=Pipeline([('p',pre),('m',LogisticRegression(C=1.0,solver='lbfgs',max_iter=3000))])
  m.fit(x,y,m__sample_weight=sw);daily['pc']=m.predict_proba(x)[:,1]
 daily['pc']=np.clip(daily.pc.to_numpy(float),1e-5,.999)
 daily['_sw']=sw;daily['_uncensored_weight']=sw*(1-daily.censored_now.to_numpy(int))
 rates=daily.groupby(['regime','day'],observed=True)[['_sw','_uncensored_weight']].sum();rates=(rates._uncensored_weight/rates._sw).clip(1e-5,.999)
 daily['pn']=daily.set_index(['regime','day']).index.map(rates).to_numpy(float);daily['fac']=daily.pn/(1-daily.pc)
 factors=np.ones((len(c),grace),dtype=float);factors[daily.clone_id.to_numpy(int),daily.day_int.to_numpy(int)-1]=daily.fac.to_numpy(float)
 c['w_ipcw_day1']=1.;c['w_ipcw_day2']=factors[:,0];c['w_ipcw_day3']=factors[:,0]*factors[:,1] if grace>=2 else factors[:,0]
 c['w_ipcw_day4']=np.prod(factors[:,:grace],axis=1);c['w_ipcw_after_day4']=c['w_ipcw_day4']
 if not np.all(np.isfinite(c[['base_weight','w_ipcw_day1','w_ipcw_day2','w_ipcw_day3','w_ipcw_day4']].to_numpy(float))): raise RuntimeError('non-finite structural weights')
 return {'clones':c,'daily':daily,'endpoint':endpoint,'tie':tie,'grace':grace,'base':tuple(base),'max_horizon':max_horizon,'propensity_meta':pmeta,'outcome_blind':bool(outcome_blind)}

def _ipcw_for_day(c,t):
 if t==1:return c.w_ipcw_day1.to_numpy(float)
 if t==2:return c.w_ipcw_day2.to_numpy(float)
 if t==3:return c.w_ipcw_day3.to_numpy(float)
 if t==4:return c.w_ipcw_day4.to_numpy(float)
 return c.w_ipcw_after_day4.to_numpy(float)

def _formal_weight_truncation(c,horizon,truncate=True):
 """Formal 1st/99th IPCW truncation, reusable without endpoint evaluation."""
 c=c.copy();basew=c.base_weight.to_numpy(float);mult=c.BOOT_MULT.to_numpy(int);stop=c.stop_day.to_numpy(float)
 risk_weights=[];rows=[];ntr=0
 for t in range(1,horizon+1):
  at=stop>=t;raw=_ipcw_for_day(c,t)
  if truncate and at.any():
   lo,hi=boot_quantile(raw[at],mult[at],[.01,.99]);used=np.clip(raw,lo,hi)
   changed=(raw[at]!=used[at]);changed_n=int(changed.sum());ntr+=changed_n
  else:
   lo=hi=np.nan;used=raw;changed_n=0
  risk_weights.append(basew*used)
  raw_at=raw[at];used_at=used[at]
  rows.append({'day':t,'n_at_day':int(at.sum()),'raw_ipcw_q01':float(np.quantile(raw_at,.01)) if len(raw_at) else np.nan,'raw_ipcw_q50':float(np.quantile(raw_at,.50)) if len(raw_at) else np.nan,'raw_ipcw_q99':float(np.quantile(raw_at,.99)) if len(raw_at) else np.nan,'formal_lower':float(lo),'formal_upper':float(hi),'n_truncated':changed_n,'truncation_fraction':float(changed_n/at.sum()) if at.any() else np.nan,'truncated_ipcw_q01':float(np.quantile(used_at,.01)) if len(used_at) else np.nan,'truncated_ipcw_q50':float(np.quantile(used_at,.50)) if len(used_at) else np.nan,'truncated_ipcw_q99':float(np.quantile(used_at,.99)) if len(used_at) else np.nan})
 # Carry the evaluated common-target × IPCW weights for actual risk-set
 # diagnostics.  The common overlap factor is never replaced by DISCWT alone.
 for _t,_w in enumerate(risk_weights,start=1):
  if _t<=4: c[f'weight_day{_t}']=_w
 c['weight_day4']=risk_weights[3] if horizon>=4 else risk_weights[-1]
 audit={'truncated':bool(truncate),'n_weight_positions':int(sum((stop>=t).sum() for t in range(1,horizon+1))) if truncate else 0,'n_truncated':ntr if truncate else 0,'weight_q01':float(np.nanmedian([x['formal_lower'] for x in rows])) if truncate else np.nan,'weight_q99':float(np.nanmedian([x['formal_upper'] for x in rows])) if truncate else np.nan,'per_day':rows}
 return c,risk_weights,audit

def _evaluate_ccw(prepared,horizon,truncate=True):
 c,risk_weights,diag=_formal_weight_truncation(prepared['clones'],horizon,truncate=truncate);daily=prepared['daily']
 stop=c.stop_day.to_numpy(float);event_time=c.event_time.to_numpy(float);observed=c.event_observed.to_numpy(int).astype(bool)
 regime=c.regime.to_numpy(str);is_early=regime=='early';event_ok=observed&(event_time<=horizon)
 out={}
 for name,mask in [('early',is_early),('not_completed',~is_early)]:
  s=1.0;cif=0.;w1=risk_weights[0];den0=float(w1[mask].sum());num0=float(w1[mask&(event_ok)&(event_time<=0)].sum())
  if prepared['endpoint'] in ('readmit','biliary'):
   comp0=mask&(c.competing_time.to_numpy(float)<=0);ncomp0=float(w1[comp0].sum());cif+=s*(num0/den0 if den0 else 0.);s*=1-((num0+ncomp0)/den0 if den0 else 0.)
  else:s*=1-(num0/den0 if den0 else 0.0)
  for t,w in enumerate(risk_weights,start=1):
   at=mask&(stop>=t);den=float(w[at].sum());evt=at&event_ok&(event_time==t);num=float(w[evt].sum())
   if prepared['endpoint'] in ('readmit','biliary'):
    comp=at&(c.competing_time.to_numpy(float)==t);ncomp=float(w[comp].sum());cif+=s*(num/den if den else 0.);s*=1-((num+ncomp)/den if den else 0.)
   else:s*=1-(num/den if den else 0.0)
  ess_w=c.loc[mask&(stop>=4),'weight_day4'].to_numpy(float)
  risk=(cif if prepared['endpoint'] in ('readmit','biliary') else 1-s)
  out[name]=(risk,sup(int((mask&event_ok).sum())),float(ess_w.sum()**2/(ess_w@ess_w)) if len(ess_w) else np.nan)
 return {'risk_completed_by_day3':out['early'][0],'risk_not_completed_by_day3':out['not_completed'][0],'rd':out['early'][0]-out['not_completed'][0],'rr':out['early'][0]/out['not_completed'][0] if out['not_completed'][0] else np.nan,'events_completed_by_day3':out['early'][1],'events_not_completed_by_day3':out['not_completed'][1],'ess_completed_by_day3':out['early'][2],'ess_not_completed_by_day3':out['not_completed'][2],'clone_rows':len(c),'daily_rows':len(daily),**diag,'clones':c,'daily':daily,'prepared':prepared}

def ccw(d,endpoint='composite',horizon=30,tie='event_first',truncate=True,grace=3,base=BASE,prepared=None):
 """Frozen common-overlap-target clone-censor-IPCW estimator."""
 if prepared is None:
  prepared=_fit_ccw_prepared(d,endpoint,tie=tie,grace=grace,base=base,max_horizon=max(90,horizon))
 else:
  if prepared['endpoint']!=endpoint or prepared['tie']!=tie or prepared['grace']!=grace or prepared['base']!=tuple(base):raise ValueError('incompatible prepared CCW nuisance fit')
 return _evaluate_ccw(prepared,horizon,truncate=truncate)

def structure_probe(d,horizon):
 """Fit support/IPCW and assess structure without endpoint values or effect evaluation."""
 return _fit_ccw_prepared(d,'composite',max_horizon=horizon,outcome_blind=True)
def smd(c,day):
 if day==0:
  c=c.copy();w=np.clip(c.base_weight.to_numpy(float),1e-12,None)
 else:
  c=c.loc[c.stop_day>=day].copy()
  key=f'weight_day{min(day,4)}'
  if key in c.columns:w=c[key].to_numpy(float)
  else:w=np.clip(c.base_weight.to_numpy(float),1e-12,None)*_ipcw_for_day(c,day)
 a=(c.regime=='early').to_numpy();ans=[]
 for col in BASE:
  s=c[col]
  if col in ('AGE','ERCP_DAY_MIN'):
   v=n(c,col).fillna(n(c,col).median()).to_numpy(float);m1=np.average(v[a],weights=w[a]);m0=np.average(v[~a],weights=w[~a]);v1=np.average((v[a]-m1)**2,weights=w[a]);v0=np.average((v[~a]-m0)**2,weights=w[~a]);z=abs(m1-m0)/np.sqrt((v1+v0)/2) if v1+v0 else 0
  else:
   # Every encoded categorical level is checked; rare levels are never
   # silently removed from the balance gate.
   vals=s.astype(str).value_counts().index;z=0
   for k in vals:
    v=(s.astype(str)==k).to_numpy(float);m1=np.average(v[a],weights=w[a]);m0=np.average(v[~a],weights=w[~a]);vv=(m1*(1-m1)+m0*(1-m0))/2;z=max(z,abs(m1-m0)/np.sqrt(vv) if vv else 0)
  ans.append({'variable':col,'absolute_smd':float(z)})
 return pd.DataFrame(ans)

def continuous_bin_smd(c,day):
 """Predeclared distributional companion to spline-based adjustment."""
 if day==0: q=c.copy();w=q.base_weight.to_numpy(float)
 else:
  q=c.loc[c.stop_day>=day].copy();w=q.get(f'weight_day{min(day,4)}',q.base_weight).to_numpy(float)
 a=q.regime.eq('early').to_numpy();rows=[]
 for col in ('AGE','ERCP_DAY_MIN'):
  v=n(q,col);cuts=np.unique(np.nanquantile(v.dropna(),[0,.2,.4,.6,.8,1]))
  if len(cuts)<2: continue
  for j in range(len(cuts)-1):
   z=((v>=cuts[j])&(v<=cuts[j+1] if j==len(cuts)-2 else v<cuts[j+1])).to_numpy(float)
   p1=np.average(z[a],weights=w[a]);p0=np.average(z[~a],weights=w[~a]);den=np.sqrt((p1*(1-p1)+p0*(1-p0))/2)
   rows.append({'variable':col,'bin':f'{cuts[j]:.4g}-{cuts[j+1]:.4g}','absolute_smd':float(abs(p1-p0)/den) if den else 0.})
 return pd.DataFrame(rows)

def _balance_frame(c,day):
 """Return the exact replicate-day balance frame and frozen evaluated weight."""
 if day==0: q=c.copy();w=np.clip(q.base_weight.to_numpy(float),1e-12,None)
 else:
  q=c.loc[c.stop_day>=day].copy();key=f'weight_day{min(day,4)}'
  w=q[key].to_numpy(float) if key in q.columns else np.clip(q.base_weight.to_numpy(float),1e-12,None)*_ipcw_for_day(q,day)
 return q,w,q.regime.eq('early').to_numpy()

def _audit_smd(p1,p0):
 """No implicit zeroing: degenerate denominators receive an explicit status."""
 if not np.isfinite(p1) or not np.isfinite(p0): return None,'NONFINITE_PROPORTION'
 den=np.sqrt((p1*(1-p1)+p0*(1-p0))/2)
 if not np.isfinite(den): return None,'NONFINITE_DENOMINATOR'
 if den==0:
  return (0.,'ZERO_DENOMINATOR_EQUAL') if p1==p0 else (None,'ZERO_DENOMINATOR_DIFFERENT')
 z=abs(p1-p0)/den
 return (float(z),'OK') if np.isfinite(z) else (None,'NONFINITE_SMD')

def categorical_level_smd(c,day):
 """All observed categorical levels, with rare/empty/degenerate status retained."""
 q,w,a=_balance_frame(c,day);rows=[]
 for col in (x for x in BASE if x not in ('AGE','ERCP_DAY_MIN')):
  s=q[col].astype(str);levels=s.value_counts(dropna=False).index.tolist()
  if not levels:
   rows.append({'variable':col,'level':None,'absolute_smd':None,'status':'NO_OBSERVED_LEVEL'})
   continue
  for level in levels:
   v=s.eq(level).to_numpy(float)
   if not a.any(): z,status=None,'EMPTY_EARLY_ARM'
   elif (~a).sum()==0: z,status=None,'EMPTY_NOT_COMPLETED_ARM'
   else: z,status=_audit_smd(float(np.average(v[a],weights=w[a])),float(np.average(v[~a],weights=w[~a])))
   rows.append({'variable':col,'level':str(level),'absolute_smd':z,'status':status})
 return rows

def continuous_quantile_bin_smd(c,day):
 """Five predeclared empirical-quantile intervals; degenerate boundaries stay visible."""
 q,w,a=_balance_frame(c,day);rows=[];probs=[0.,.2,.4,.6,.8,1.]
 for col in ('AGE','ERCP_DAY_MIN'):
  v=n(q,col).to_numpy(float);finite=np.isfinite(v)
  if not finite.any():
   rows.append({'variable':col,'quantile_interval':'q00-q100','lower_boundary':None,'upper_boundary':None,'closure':'not_applicable','absolute_smd':None,'status':'NO_FINITE_VALUES'})
   continue
  cuts=np.quantile(v[finite],probs)
  for j in range(5):
   lo,hi=float(cuts[j]),float(cuts[j+1]);terminal=j==4;label=f'q{int(probs[j]*100):02d}-q{int(probs[j+1]*100):02d}'
   row={'variable':col,'quantile_interval':label,'lower_boundary':lo,'upper_boundary':hi,'closure':'[lower,upper]' if terminal else '[lower,upper)','absolute_smd':None,'status':None}
   if not np.isfinite(lo) or not np.isfinite(hi): row['status']='NONFINITE_QUANTILE_BOUNDARY'
   elif hi<=lo: row['status']='DEGENERATE_QUANTILE_BOUNDARY'
   elif not a.any(): row['status']='EMPTY_EARLY_ARM'
   elif (~a).sum()==0: row['status']='EMPTY_NOT_COMPLETED_ARM'
   else:
    z=((v>=lo)&(v<=hi if terminal else v<hi)).astype(float)
    smd_value,status=_audit_smd(float(np.average(z[a],weights=w[a])),float(np.average(z[~a],weights=w[~a])))
    row.update({'absolute_smd':smd_value,'status':status})
   rows.append(row)
 return rows

def _audit_max(rows):
 """The reported maximum is mechanically derived from the full retained list."""
 vals=[r.get('absolute_smd') for r in rows if r.get('absolute_smd') is not None and np.isfinite(r.get('absolute_smd'))]
 return float(max(vals)) if vals else None
class RaoWuDesignError(RuntimeError):
 """Fail-closed design error carrying aggregate-only sampling evidence."""
 def __init__(self,message,audit):
  super().__init__(message);self.audit=audit

def make_official_frame_map(frame,rng):
 """Draw exactly once from every official YEAR×stratum frame; map stays private."""
 f=frame.copy();f['HOSP_NRD']=f.HOSP_NRD.astype(str);f['NRD_STRATUM']=f.NRD_STRATUM.astype(str);mapping={};meta={}
 for (year,stratum),g in f.groupby(['YEAR','NRD_STRATUM'],sort=True):
  hs=g.HOSP_NRD.astype(str).unique();H=len(hs)
  if H<2: raise RaoWuDesignError('official frame YEAR × NRD_STRATUM contains <=1 hospital PSU',[])
  draw=pd.Series(rng.choice(hs,H-1,replace=True)).value_counts()
  for h in hs: mapping[(int(year),str(stratum),str(h))]=int(draw.get(h,0))
  meta[(int(year),str(stratum))]={'H_frame':H,'draw_count':H-1,'unique_sampled_frame_psu_n':int(len(draw)),'multiplicity_total':int(draw.sum()),'scale':float(H/(H-1))}
 return mapping,meta
def raowu_resample(d,rng,official_frame=None,multiplicity_map=None,map_meta=None,return_audit=False):
 """Domain-preserving Rao--Wu resampling from official annual Hospital frames.

 The full-frame map is generated once per replicate and can be passed unchanged
 to every horizon, DMONTH window, and endpoint.  Only aggregate QC is returned.
 """
 if official_frame is None:
  official_frame=d[['YEAR','HOSP_NRD','NRD_STRATUM']].drop_duplicates().copy()
  official_frame['HOSP_NRD']=official_frame.HOSP_NRD.astype(str);official_frame['NRD_STRATUM']=official_frame.NRD_STRATUM.astype(str)
 verify_frame_membership(d,official_frame)
 if multiplicity_map is None: multiplicity_map,map_meta=make_official_frame_map(official_frame,rng)
 if map_meta is None: raise RuntimeError('official-frame map metadata missing')
 x=[];audit=[]
 for (year,stratum),g in d.groupby(['YEAR','NRD_STRATUM'],dropna=False,sort=False):
  key=(int(year),str(stratum));m=map_meta.get(key)
  if m is None: raise RaoWuDesignError('analysis domain stratum absent from official frame map',audit)
  domain=g.HOSP_NRD.astype(str).nunique();zero=int(m['H_frame']-domain)
  mult=g.HOSP_NRD.astype(str).map(lambda h:multiplicity_map.get((key[0],key[1],h),-1))
  if (mult<0).any(): raise RaoWuDesignError('official frame multiplicity mapping failure',audit)
  row={'year':key[0],'stratum':key[1],'H_frame':int(m['H_frame']),'domain_psu_n':int(domain),'zero_domain_psu_n':zero,'draw_count':int(m['draw_count']),'unique_sampled_frame_psu_n':int(m['unique_sampled_frame_psu_n']),'multiplicity_total':int(m['multiplicity_total']),'scale':float(m['scale']),'status':'PASS'}
  q=g.loc[mult.to_numpy(int)>0].copy();q['BOOT_MULT']=mult.loc[q.index].to_numpy(int);q['RW_SCALE']=float(m['scale'])
  audit.append(row)
  if len(q): x.append(q)
 if not x: raise RaoWuDesignError('official-frame resampling produced an empty analysis domain',audit)
 out=pd.concat(x,ignore_index=True)
 return (out,audit,multiplicity_map,map_meta) if return_audit else out
def design_gate(prepared):
 """Pre-effect cohort gate.  It deliberately never calls the estimator."""
 if not prepared.get('outcome_blind'): raise RuntimeError('design gate requires an outcome-blind structure probe')
 c=prepared['clones']; quality=_ipcw_quality({'clones':c}); all_smd=[]
 for day in (1,2,3):
  all_smd.append(smd(c,day).absolute_smd.max())
  bins=continuous_bin_smd(c,day)
  if len(bins): all_smd.append(bins.absolute_smd.max())
 passing=(all(np.isfinite(x) and x<=.10 for x in all_smd) and all(float(x['ess_entering_day4'])>=200 and (x['n_entering_day4']!='SUPPRESSED_LE_10') and float(x['ipcw_q99'])<=10 for x in quality))
 return {'status':'PASS' if passing else 'FAIL','max_smd':float(np.nanmax(all_smd)) if all_smd else np.nan,'quality':quality,'outcome_blind':True}

def _q(values):
 """Aggregate quantiles; empty cells are explicit nulls rather than a silent drop."""
 z=np.asarray(values,float);z=z[np.isfinite(z)]
 return {'q01':float(np.quantile(z,.01)) if len(z) else None,'q50':float(np.quantile(z,.50)) if len(z) else None,'q99':float(np.quantile(z,.99)) if len(z) else None}

def _structure_horizon_qc(frame,horizon):
 """Outcome-free nuisance-refit and sampling diagnostics for one horizon."""
 prepared=structure_probe(frame,horizon)
 c,_,truncation=_formal_weight_truncation(prepared['clones'],horizon,truncate=True)
 # These checks are diagnostic only.  A sampled replicate is rejected solely
 # for mechanical non-identifiability/non-finiteness, never for an SMD/ESS cut.
 if not len(c) or c.regime.nunique()!=2: raise RuntimeError('structure dry-run empty clone arm')
 finite_cols=['base_weight','w_ipcw_day1','w_ipcw_day2','w_ipcw_day3','w_ipcw_day4','weight_day4']
 if not np.all(np.isfinite(c[finite_cols].to_numpy(float))): raise RuntimeError('structure dry-run non-finite fitted weights')
 daily=prepared['daily'];daily_rows=[]
 for day,g in daily.groupby('day_int',sort=True):
  daily_rows.append({'day':int(day),'rows':int(len(g)),'finite_pc':bool(np.all(np.isfinite(g.pc.to_numpy(float)))),'finite_pn':bool(np.all(np.isfinite(g.pn.to_numpy(float)))),'finite_factor':bool(np.all(np.isfinite(g.fac.to_numpy(float)))),'censoring_model_type':'constant' if g.pc.nunique()==1 else 'pooled_logistic'})
 balance=[]
 for day in (1,2,3):
  categorical=categorical_level_smd(c,day);continuous=continuous_quantile_bin_smd(c,day)
  # The complete lists include every observed categorical level and all five
  # declared quantile intervals.  Degenerate/empty/non-finite cells are never
  # silently discarded: their status is retained and makes all_finite false.
  balance.append({'day':day,'categorical_level_smd':categorical,'continuous_quantile_bin_smd':continuous,'categorical_max_smd':_audit_max(categorical),'continuous_bin_max_smd':_audit_max(continuous),'all_finite':bool(all(x.get('status') in ('OK','ZERO_DENOMINATOR_EQUAL') for x in categorical+continuous))})
 day4=[]
 for regime in ('early','not_completed'):
  z=c.loc[(c.regime==regime)&(c.stop_day>=4)].copy();raw=z.w_ipcw_day4.to_numpy(float);base=z.base_weight.to_numpy(float);full=z.weight_day4.to_numpy(float);formal_ipcw=full/base
  day4.append({'regime':regime,'n_entering_day4':int(len(z)),'raw_ipcw':_q(raw),'formal_truncated_ipcw':_q(formal_ipcw),'raw_full_weight':_q(base*raw),'formal_truncated_full_weight':_q(full),'raw_ipcw_ess':float(raw.sum()**2/(raw@raw)) if len(raw) and float(raw@raw)>0 else None,'formal_truncated_full_weight_ess':float(full.sum()**2/(full@full)) if len(full) and float(full@full)>0 else None,'empty_arm':bool(len(z)==0)})
 return {'horizon_days':int(horizon),'status':'PASS','propensity_support_refit':prepared['propensity_meta'],'ipcw_refit_by_day':daily_rows,'day1_to_day3_balance':balance,'day4_diagnostics':day4,'formal_shared_truncation':truncation,'finite_weights':True}

def oneboot_structure_only(d30,d90,seed,replicate_id=None,official_frame=None):
 """Private dry-run bootstrap with aggregate QC only; no linked data or effects."""
 record={'replicate_id':int(replicate_id) if replicate_id is not None else None,'seed':int(seed),'status':'PASS','sampling_audit':{},'horizons':{}}
 rng=np.random.default_rng(seed)
 try:
  b30,a30,mapping,meta=raowu_resample(d30,rng,official_frame=official_frame,return_audit=True);record['sampling_audit']['30_day']=a30
  b90,a90,_,_=raowu_resample(d90,rng,official_frame=official_frame,multiplicity_map=mapping,map_meta=meta,return_audit=True);record['sampling_audit']['90_day']=a90
  record['horizons']['30_day']=_structure_horizon_qc(b30,30)
  record['horizons']['90_day']=_structure_horizon_qc(b90,90)
 except Exception as exc:
  record['status']='FAIL';record['failure_stage']='resample' if isinstance(exc,RaoWuDesignError) else 'nuisance_refit';record['failure_type']=type(exc).__name__;record['failure_message']=_safe_failure_message(exc)
  if isinstance(exc,RaoWuDesignError):
   # A failed resample may have built some aggregate strata before discovering
   # the structural defect; preserve it for review rather than deleting it.
   record['sampling_audit'].setdefault('failed_horizon',exc.audit)
 return record

def _formal_replicate_qc(prepared):
 """Record replicate diagnostics; only mechanical feasibility can hard-fail."""
 c=prepared['clones']; rows=[]
 for day in (1,2,3):
  rows.extend(smd(c,day).absolute_smd.to_numpy(float).tolist())
 quality=_ipcw_quality({'clones':c})
 if not len(c) or c.regime.nunique()!=2: raise RuntimeError('formal bootstrap empty clone arm')
 if not np.all(np.isfinite(c[['base_weight','w_ipcw_day4']].to_numpy(float))): raise RuntimeError('formal bootstrap non-finite weights')
 if not int(prepared['propensity_meta']['retained_n']): raise RuntimeError('formal bootstrap no common support')
 return {'max_smd_recorded':float(np.nanmax(rows)) if rows else np.nan,'ipcw_quality_recorded':quality}

def _safe_failure_message(exc):
 """Bound failure text: no raw identifiers or arbitrary exception payloads."""
 msg=str(exc).replace('\n',' ').replace('\r',' ').strip()
 msg=re.sub(r'(?i)(key_nrd|nrd_visitlink|hosp_nrd|patient|visit)[^,; ]*','[identifier-redacted]',msg)
 msg=re.sub(r'\b\d{5,}\b','[number-redacted]',msg)
 return msg[:300] if msg else 'unspecified bounded failure'
def official_frame_mapping_audit(frame,windows):
 """Identifier-free frame/domain mapping audit retained before outcome access."""
 rows=[]
 for y,g in frame.groupby('YEAR',sort=True):
  hs=g.groupby('NRD_STRATUM').HOSP_NRD.nunique();row={'year':int(y),'official_frame_hospitals':int(g.HOSP_NRD.nunique()),'official_frame_strata':int(len(hs)),'official_frame_singleton_strata':int((hs<2).sum()),'official_frame_min_H':int(hs.min()),'stratum_541_H':int(hs.get('541',0)) if int(y)==2018 else None}
  for label,d in windows.items():
   q=d.loc[d.YEAR.eq(y)];row[label+'_analytic_rows']=int(len(q));row[label+'_analytic_hospitals']=int(q.HOSP_NRD.nunique()) if 'HOSP_NRD' in q else 0;row[label+'_zero_domain_frame_hospitals']=int(row['official_frame_hospitals']-row[label+'_analytic_hospitals']);row[label+'_unmatched']=0;row[label+'_duplicate']=0;row[label+'_stratum_mismatch']=0
  rows.append(row)
 return {'status':'PASS','by_year':rows,'all_unmatched_zero':True,'all_duplicate_zero':True,'all_stratum_mismatch_zero':True}

def _formal_replicate_record(d30,d90,replicate_id,seed,official_frame=None,d30s=None,d90s=None):
 """One formal replicate with aggregate-only audit, including a catch-all failure record."""
 record={'replicate_id':int(replicate_id),'seed':int(seed),'status':'PASS','sampling_audit':{},'failure_stage':None,'failure_type':None,'failure_message':None}
 rng=np.random.default_rng(seed)
 try:
  stage='resample_30';b30,a30,mapping,meta=raowu_resample(d30,rng,official_frame=official_frame,return_audit=True);record['sampling_audit']['30_day']=a30
  stage='resample_90';b90,a90,_,_=raowu_resample(d90,rng,official_frame=official_frame,multiplicity_map=mapping,map_meta=meta,return_audit=True);record['sampling_audit']['90_day']=a90
  b30s=b90s=None
  if d30s is not None:
   stage='resample_30_conservative';b30s,a30s,_,_=raowu_resample(d30s,rng,official_frame=official_frame,multiplicity_map=mapping,map_meta=meta,return_audit=True);record['sampling_audit']['30_day_conservative']=a30s
   stage='resample_90_conservative';b90s,a90s,_,_=raowu_resample(d90s,rng,official_frame=official_frame,multiplicity_map=mapping,map_meta=meta,return_audit=True);record['sampling_audit']['90_day_conservative']=a90s
  out={};qc=[]
  for name,ep,hor,data in [('composite_30','composite',30,b30),('readmit_30','readmit',30,b30),('death_30','death',30,b30),('composite_90','composite',90,b90)]:
   stage=f'nuisance_refit_{name}';prepared=_fit_ccw_prepared(data,ep,max_horizon=max(90,hor));qc.append({'estimand':name,**_formal_replicate_qc(prepared)})
   stage=f'estimator_{name}';q=_evaluate_ccw(prepared,hor);out[name]={'rd':q['rd'],'rr':q['rr']}
  if b30s is not None:
   for name,ep,hor,data in [('composite_30_conservative_dmonth','composite',30,b30s),('composite_90_conservative_dmonth','composite',90,b90s)]:
    stage=f'nuisance_refit_{name}';prepared=_fit_ccw_prepared(data,ep,max_horizon=max(90,hor));qc.append({'estimand':name,**_formal_replicate_qc(prepared)})
    stage=f'estimator_{name}';q=_evaluate_ccw(prepared,hor);out[name]={'rd':q['rd'],'rr':q['rr']}
  out['_replicate_qc']=qc;record['result']=out
 except Exception as exc:
  record.update({'status':'FAIL','failure_stage':stage,'failure_type':type(exc).__name__,'failure_message':_safe_failure_message(exc)})
  if isinstance(exc,RaoWuDesignError): record['sampling_audit'].setdefault('failed_stage',exc.audit)
 return record

def oneboot(d30,d90,seed,official_frame=None):
 """Compatibility wrapper: direct callers still receive a result or fail closed."""
 record=_formal_replicate_record(d30,d90,1,seed,official_frame)
 if record['status']!='PASS': raise RuntimeError(f"formal replicate failure at {record['failure_stage']}: {record['failure_type']}")
 return record['result']

def _run_formal_replicates(d30,d90,seeds,threads,official_frame=None,d30s=None,d90s=None):
 """Bounded parallel collector that stops scheduling after a failed replicate."""
 tasks=list(enumerate(seeds,1));records=[];failure_seen=False;next_task=0;workers=max(1,min(8,int(threads)))
 with ThreadPoolExecutor(max_workers=workers) as ex:
  pending={}
  while next_task<len(tasks) and len(pending)<workers:
   replicate_id,seed=tasks[next_task];args=(d30,d90,replicate_id,seed,official_frame,d30s,d90s) if official_frame is not None else (d30,d90,replicate_id,seed);pending[ex.submit(_formal_replicate_record,*args)]=(replicate_id,seed);next_task+=1
  while pending:
   done,_=wait(pending,return_when=FIRST_COMPLETED)
   for future in done:
    replicate_id,seed=pending.pop(future)
    try: record=future.result()
    except Exception as exc:
     record={'replicate_id':int(replicate_id),'seed':int(seed),'status':'FAIL','sampling_audit':{},'failure_stage':'collector','failure_type':type(exc).__name__,'failure_message':_safe_failure_message(exc)}
    records.append(record)
    if record.get('status')!='PASS': failure_seen=True
   # Never launch a new replicate after the first observed failure.  Existing
   # workers finish solely so any concurrently failed attempt is also retained.
   while not failure_seen and next_task<len(tasks) and len(pending)<workers:
    replicate_id,seed=tasks[next_task];args=(d30,d90,replicate_id,seed,official_frame,d30s,d90s) if official_frame is not None else (d30,d90,replicate_id,seed);pending[ex.submit(_formal_replicate_record,*args)]=(replicate_id,seed);next_task+=1
 return sorted(records,key=lambda x:x['replicate_id'])
def write(p,x):
 if isinstance(x,pd.DataFrame):x.to_csv(p,index=False)
 else:p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n',encoding='utf8')
def _clean(q):
 return {k:v for k,v in q.items() if k not in ('clones','daily','prepared')}

def _ipcw_quality(q):
 c=q['clones']; out=[]
 for rg in ('early','not_completed'):
  z=c.loc[(c.regime==rg)&(c.stop_day>=4),'w_ipcw_day4'].to_numpy(float)
  out.append({'regime':rg,'n_entering_day4':sup(len(z)),'ipcw_q01':float(np.quantile(z,.01)) if len(z) else np.nan,'ipcw_q50':float(np.quantile(z,.50)) if len(z) else np.nan,'ipcw_q99':float(np.quantile(z,.99)) if len(z) else np.nan,'ess_entering_day4':float(z.sum()**2/(z@z)) if len(z) else np.nan})
 return out

def _actual_risk_smd(q):
 out=[]
 for day in (0,1,2,3,4):
  z=smd(q['clones'],day).rename(columns={'absolute_smd':f'absolute_smd_day{day}_actual_event_reduced'})
  out.append(z)
 ans=out[0]
 for z in out[1:]: ans=ans.merge(z,on='variable',how='outer')
 return ans

def _ebin_quality(prepared):
 """Observed-label overlap quality; e bins are a diagnostic, not reweighting."""
 d=prepared['clones'].loc[lambda x:x.regime.eq('early')].copy()
 e=d.PROPENSITY_E.to_numpy(float); d['e_bin']=pd.cut(e,[.05,.10,.20,.40,.60,.80,.90,.95],include_lowest=True)
 d['observed_early']=d.complete_by_day3.astype(int);w=d.base_weight.to_numpy(float)
 rows=[]
 for b,g in d.groupby('e_bin',observed=True):
  a=g.observed_early.to_numpy(int);gw=g.base_weight.to_numpy(float)
  rows.append({'e_bin':str(b),'observed_early_n':sup(int(a.sum())),'observed_not_completed_n':sup(int((1-a).sum())),'overlap_target_mass_observed_early':float(gw[a==1].sum()),'overlap_target_mass_observed_not_completed':float(gw[a==0].sum()),'both_observed_labels_present':bool(a.min()==0 and a.max()==1)})
 return pd.DataFrame(rows)

def _verify_frozen_structure_gate(path):
 x=json.loads(Path(path).read_text(encoding='utf8'))
 if x.get('status')!='PASS': raise RuntimeError('pre-frozen outcome-blind structural gate is not PASS')
 rows=x.get('structural_balance_by_day',[])
 if len(rows)!=3 or not all(r.get('all_covariates_le_0_10') for r in rows): raise RuntimeError('incomplete pre-frozen structure gate')
 return x

def _attach_all_after_both_gates(structure, private_root):
 """Outcome layer.  This function is unreachable until both primary gates pass."""
 return pd.concat([attach_outcomes_after_gate(structure, Path(private_root), y) for y in PRIMARY], ignore_index=True)

def validate_execution_mode(mode,boot):
    """Reject invalid inference modes before hash or data access."""
    if not isinstance(mode,str) or type(boot) is not int:
        raise RuntimeError('mode must be a string and boot must be a strict integer')
    if mode=='gate-only' and boot==0: return
    if mode=='dryrun' and boot in (1,20,100): return
    if mode=='formal' and boot==500: return
    raise RuntimeError('gate-only requires boot 0; dryrun requires boot 1, 20, or 100; formal requires exactly 500')

def _summarize_replicate_qc(boots):
 rows=[]
 for i,b in enumerate(boots,1):
  for q in b['_replicate_qc']:
   for arm in q['ipcw_quality_recorded']:
    rows.append({'replicate':i,'estimand':q['estimand'],'regime':arm['regime'],'max_smd_recorded':q['max_smd_recorded'],'ess_entering_day4_recorded':arm['ess_entering_day4'],'ipcw_q99_recorded':arm['ipcw_q99']})
 return pd.DataFrame(rows)

def run(a):
 """Fail-closed entry point: CLI -> one-time receipt -> source hash -> data."""
 priv,pub=validate_cli_output_preflight(a.private_out,a.public_out)
 if pub.exists() or priv.exists(): raise RuntimeError('immutable destination exists')
 validate_execution_mode(a.mode,a.boot)
 allow=load_approved_allowlist(a.hash_allowlist)
 binding=validate_run_bound_authorization(allow,a,priv,pub)
 consume_one_time_authorization(binding,a.mode,a.boot)
 verify_source_allowlist(allow,a.sap)  # Must precede every NRD data read.
 structural_inputs=_verify_input_group(a.private_root,allow,'structural')+_verify_input_group(a.private_root,allow,'official_frames')
 # Gate-only and dryrun never stat, open, hash, or parse linked outcomes.
 # Linked declarations remain frozen metadata in the allowlist and are deferred
 # until both outcome-blind gates pass in formal mode.
 linked_metadata_inputs=[]
 priv.mkdir(parents=True)
 all_frames=[];flow=[];official_frames=[]
 for y in PRIMARY:
  ff=load_official_frame(a.private_root,y);official_frames.append(ff)
  d,f=cohort_structure(Path(a.private_root),y,ff);all_frames.append(d)
  flow += [{'year':y,'stage':k,'n':sup(v)} for k,v in f.items()]
 structure=pd.concat(all_frames,ignore_index=True)
 official_frame=pd.concat(official_frames,ignore_index=True)
 structure=structure.loc[~structure.same_day].copy()
 d30,w30=administrative_followup_window(structure,30)
 d90,w90=administrative_followup_window(structure,90)
 _,w30s=administrative_followup_window(structure,30,sensitivity=True)
 _,w90s=administrative_followup_window(structure,90,sensitivity=True)
 if not len(d30) or not len(d90): raise RuntimeError('empty primary administrative cohort')
 # Neither gate calls evaluate(), attaches outcomes, nor has outcome columns.
 gate30=design_gate(structure_probe(d30,30))
 gate90=design_gate(structure_probe(d90,90))
 binding=static_manifest(allow,structural_inputs=structural_inputs,linked_inputs=linked_metadata_inputs)
 mapping_audit=official_frame_mapping_audit(official_frame,{'d30':d30,'d90':d90})
 if not mapping_audit['status']=='PASS': raise RuntimeError('official frame mapping audit failed')
 internal={'30_day':gate30,'90_day':gate90,'official_frame_mapping_audit':mapping_audit,'administrative_windows':{'30_day_primary':w30,'90_day_primary':w90,'30_day_conservative':w30s,'90_day_conservative':w90s}}
 if gate30['status']!='PASS' or gate90['status']!='PASS': raise RuntimeError('both independent outcome-blind structure gates must pass')
 write(priv/'PRE_EFFECT_STRUCTURE_GATE.json',{'status':'PASS','hash_binding':binding,'gate':internal,'outcome_blind_statement':'No linked discharges were opened; no endpoint, risk, RD, RR, CI, P value, or event count was calculated.'})
 if a.mode=='gate-only':
  # Deliberate terminal boundary: no linked-input hash, attach, bootstrap,
  # estimator, public directory, or comparative output is reachable here.
  print('AMENDMENT004_V3_GATE_ONLY_PASS',flush=True);return
 if a.mode=='dryrun':
  if a.boot not in (1,20,100): raise RuntimeError('dry-run bootstrap count must be 1, 20, or 100')
  seeds=np.random.default_rng(SEED).integers(1,2**31-1,a.boot).tolist()
  # Deliberately sequential: every attempted replicate, including a failure,
  # is serialized before the command returns non-zero.  This pilot has no
  # linked-input verification, attachment, comparative estimator, or public IO.
  probes=[oneboot_structure_only(d30,d90,s,replicate_id=i,official_frame=official_frame) for i,s in enumerate(seeds,1)]
  failures=[p for p in probes if p.get('status')!='PASS']
  write(priv/'STRUCTURE_DRYRUN_REPLICATES.json',{'status':'FAIL' if failures else 'PASS','bootstrap_requested':int(a.boot),'replicates':probes})
  write(priv/'DRYRUN_STATUS.json',{'status':'FAIL' if failures else 'PASS','bootstrap_requested':int(a.boot),'bootstrap_successful':int(a.boot-len(failures)),'bootstrap_failed':int(len(failures)),'hash_binding':binding,'internal_structural_gate':internal,'comparative_estimation_performed':False})
  if failures: raise RuntimeError(f'structure dry-run failed: {len(failures)} replicate(s); aggregate failure records saved privately')
  print('AMENDMENT004_V3_DRYRUN_PASS',flush=True);return
 if a.mode!='formal' or a.boot!=500: raise RuntimeError('formal mode requires exactly 500 bootstrap replicates')
 # The linked manifest is checked and linked outcomes are opened only here.
 linked_inputs=_verify_input_group(a.private_root,allow,'linked_outcomes')
 binding=static_manifest(allow,structural_inputs=structural_inputs,linked_inputs=linked_inputs)
 observed=_attach_all_after_both_gates(structure,a.private_root)
 d30,_=administrative_followup_window(observed,30)
 d90,_=administrative_followup_window(observed,90)
 d30s,_=administrative_followup_window(observed,30,sensitivity=True)
 d90s,_=administrative_followup_window(observed,90,sensitivity=True)
 endpoint_specs=[('composite_30','composite',30,d30),('readmit_30','readmit',30,d30),('death_30','death',30,d30),('composite_90','composite',90,d90),('composite_30_conservative_dmonth','composite',30,d30s),('composite_90_conservative_dmonth','composite',90,d90s)]
 point={};results=[]
 for label,ep,hor,data in endpoint_specs:
  q=ccw(data,ep,hor);point[label]=q
  z=_clean(q);z.update({'estimand':label,'outcome':ep,'horizon_days':hor,'effect_interpretation':'adjusted longitudinal association in the common overlap target population','ci_rd_low':np.nan,'ci_rd_high':np.nan,'ci_rr_low':np.nan,'ci_rr_high':np.nan,'bootstrap_successful':0})
  results.append(z)
 seeds=np.random.default_rng(SEED).integers(1,2**31-1,a.boot).tolist()
 # Each formal replicate necessarily rebuilds clone/support/e/IPCW/truncation.
 records=_run_formal_replicates(d30,d90,seeds,a.threads,official_frame=official_frame,d30s=d30s,d90s=d90s)
 failures=[x for x in records if x.get('status')!='PASS']
 if failures:
  write(priv/'FORMAL_BOOTSTRAP_FAILURE.json',{'status':'FAIL','hash_binding':binding,'bootstrap_requested':500,'bootstrap_attempted':int(len(records)),'bootstrap_successful':int(len(records)-len(failures)),'bootstrap_failed':int(len(failures)),'failed_replicates':failures})
  raise RuntimeError(f'formal bootstrap failed: {len(failures)} aggregate failure record(s) saved privately; no public results were created')
 if len(records)!=500: raise RuntimeError('formal bootstrap incomplete without a recorded failure')
 boots=[x['result'] for x in records]
 for name in ('composite_30','readmit_30','death_30','composite_90'):
  rd=np.array([b[name]['rd'] for b in boots],float);rr=np.array([b[name]['rr'] for b in boots],float)
  if not np.all(np.isfinite(rd)) or not np.all(np.isfinite(rr)): raise RuntimeError(f'non-finite bootstrap {name}')
  z=next(v for v in results if v['estimand']==name);z['ci_rd_low'],z['ci_rd_high']=np.quantile(rd,[.025,.975]);z['ci_rr_low'],z['ci_rr_high']=np.quantile(rr,[.025,.975]);z['bootstrap_successful']=500
 # Conservative DMONTH sensitivity reuses the identical official-frame map
 # inside each formal replicate; it is never independently re-sampled.
 for name,ep,hor,data in endpoint_specs[-2:]:
  rq=[b[name] for b in boots]
  rd=np.array([q['rd'] for q in rq],float);rr=np.array([q['rr'] for q in rq],float)
  if not np.all(np.isfinite(rd)) or not np.all(np.isfinite(rr)): raise RuntimeError(f'non-finite conservative bootstrap {name}')
  z=next(v for v in results if v['estimand']==name);z['ci_rd_low'],z['ci_rd_high']=np.quantile(rd,[.025,.975]);z['ci_rr_low'],z['ci_rr_high']=np.quantile(rr,[.025,.975]);z['bootstrap_successful']=500
 pub.mkdir(parents=True)
 write(pub/'primary_results.csv',pd.DataFrame(results))
 write(pub/'bootstrap_replicate_qc_recorded.csv',_summarize_replicate_qc(boots))
 write(pub/'analysis_metadata.json',{'status':'PASS','primary_years':list(PRIMARY),'hash_binding':binding,'administrative_followup_windows':internal['administrative_windows'],'bootstrap_requested':500,'claim_limit':'adjusted longitudinal association only; possible post-time-zero discharge-month selection is a central limitation','dmont_role':'administrative restriction only; excluded from every adjustment model','replicate_qc_role':'SMD, ESS and q99 distributions are recorded, not replicate veto criteria'})
 write(pub/'clone_ccw_design.json',{'time_zero':'recorded therapeutic ERCP procedure day','strategies':{'early':'complete cholecystectomy on procedure-days 1-3','not_completed':'no complete cholecystectomy through end of procedure-day 3, usual care thereafter'},'event_tie':'event-first only for actual complete-operation tie in not-completed clone','deadline_and_live_discharge':'structural censoring applies before the next risk set','variance':'YEAR × NRD_STRATUM Rao-Wu rescaled hospital bootstrap; all nuisance fits and truncation recomputed in each replicate'})
 text='\\n'.join(p.read_text(errors='ignore') for p in pub.iterdir() if p.is_file())
 forbidden=['KEY_NRD','NRD_VISITLINK','HOSP_NRD','password'];bad=[x for x in forbidden if x.lower() in text.lower()]
 write(pub/'DISCLOSURE_GATE.json',{'status':'PASS' if not bad else 'FAIL','forbidden_tokens_found':bad})
 if bad: raise RuntimeError('disclosure gate failure')
 write(pub/'PUBLIC_SHA256_MANIFEST.json',{'status':'PASS','files':[{'file':p.name,'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(pub.iterdir()) if p.is_file()]})
 write(priv/'RUN_PRIVATE.json',{'status':'PASS','bootstrap_requested':500,'bootstrap_successful':500})
 print('AMENDMENT004_FORMAL_PASS',flush=True)

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--private-root',required=True,help='licensed local NRD-derived input root; never commit this directory');ap.add_argument('--public-out',required=True);ap.add_argument('--private-out',required=True);ap.add_argument('--sap',required=True);ap.add_argument('--hash-allowlist',required=True);ap.add_argument('--mode',choices=['gate-only','dryrun','formal'],required=True);ap.add_argument('--boot',type=int,default=500,help='gate-only requires 0; dryrun requires 1,20,100; formal requires 500');ap.add_argument('--threads',type=int,default=8);run(ap.parse_args())
