#!/usr/bin/env python3
"""Aggregate-only year heterogeneity and leave-one-year-out sensitivity."""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np, pandas as pd
for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS","BLIS_NUM_THREADS"):
    os.environ.setdefault(k,"1")
# Use the frozen v4.7 implementation packaged beside this script.  The
# licensed NRD-derived input root is supplied explicitly at run time.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from frozen_v4_7 import (cohort_structure, load_official_frame, structure_probe,
    design_gate, attach_outcomes_after_gate, administrative_followup_window,
    ccw, raowu_resample)
SEED=20260920; BOOT=500; _D=None; _F=None
SEED_OFFSET={"calendar_year_2018":101,"calendar_year_2019":202,"calendar_year_2020":303,"leave_out_2020":404}
def _init(d,f):
    global _D,_F; _D,_F=d,f
def _one(seed):
    rng=np.random.default_rng(int(seed)); b=raowu_resample(_D,rng,official_frame=_F)
    q=ccw(b,endpoint="composite",horizon=30,grace=3)
    return float(q["rd"]),float(q["rr"])
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest()
def _dataset(root,ys):
    structs=[]; frames=[]
    for y in ys:
        ff=load_official_frame(root,y); d,_=cohort_structure(root,y,ff)
        structs.append(d); frames.append(ff)
    structure=pd.concat(structs,ignore_index=True); frame=pd.concat(frames,ignore_index=True)
    structure=structure.loc[~structure.same_day].copy()
    d30s,_=administrative_followup_window(structure,30)
    if design_gate(structure_probe(d30s,30)).get("status")!="PASS":
        raise RuntimeError("year-sensitivity outcome-blind structure gate failed")
    observed=pd.concat([attach_outcomes_after_gate(structure.loc[structure.YEAR.eq(y)].copy(),root,y) for y in ys],ignore_index=True)
    d30,w=administrative_followup_window(observed,30)
    if not len(d30): raise RuntimeError("empty year-sensitivity cohort")
    return d30,frame,w
def _estimate(label,d,frame,boot,threads):
    q=ccw(d,endpoint="composite",horizon=30,grace=3)
    seeds=np.random.default_rng(SEED+SEED_OFFSET[label]).integers(1,2**31-1,boot)
    with ProcessPoolExecutor(max_workers=min(8,int(threads)),initializer=_init,initargs=(d,frame)) as ex:
        vals=np.asarray(list(ex.map(_one,[int(x) for x in seeds],chunksize=1)),float)
    if vals.shape!=(boot,2) or not np.isfinite(vals).all(): raise RuntimeError(f"non-finite/incomplete bootstrap for {label}")
    return {"analysis":label,"status":"PASS","years":",".join(map(str,sorted(set(d.YEAR.astype(int))))),
      "input_n":int(len(d)),"early_label_n":int(d.complete_by_day3.sum()),
      "not_completed_label_n":int(len(d)-d.complete_by_day3.sum()),
      "risk_early":float(q["risk_completed_by_day3"]),"risk_not_completed":float(q["risk_not_completed_by_day3"]),
      "rd":float(q["rd"]),"rr":float(q["rr"]),
      "ci_rd_low":float(np.quantile(vals[:,0],.025)),"ci_rd_high":float(np.quantile(vals[:,0],.975)),
      "ci_rr_low":float(np.quantile(vals[:,1],.025)),"ci_rr_high":float(np.quantile(vals[:,1],.975)),
      "bootstrap_successful":int(boot)}
def build(a):
    pub,priv=Path(a.public_out),Path(a.private_out)
    if pub.exists() or priv.exists(): raise RuntimeError("immutable amendment006 destination exists")
    pub.mkdir(parents=True); priv.mkdir(parents=True); root=Path(a.private_root)
    rows=[]; audits=[]
    for label,ys in [("calendar_year_2018",(2018,)),("calendar_year_2019",(2019,)),("calendar_year_2020",(2020,)),("leave_out_2020",(2018,2019))]:
        try:
            d,f,w=_dataset(root,ys); row=_estimate(label,d,f,a.boot,a.threads)
            row["administrative_window_months"]=f"{w['month_min']}-{w['month_max']}"; rows.append(row)
            audits.append({"analysis":label,"status":"PASS","input_n":int(len(d)),"frame_hospitals":int(f.HOSP_NRD.nunique()),"frame_strata":int(f.NRD_STRATUM.nunique())})
        except Exception as e:
            rows.append({"analysis":label,"status":"NOT_ESTIMABLE","reason":str(e)[:300],"bootstrap_successful":0})
            audits.append({"analysis":label,"status":"FAIL","reason":str(e)[:300]})
    if not rows or any(x.get("status")!="PASS" for x in rows):
        (priv/"RUN_PRIVATE.json").write_text(json.dumps({"status":"FAIL","audits":audits},indent=2)+"\n")
        raise RuntimeError("one or more year analyses failed; no public outputs released")
    pd.DataFrame(rows).to_csv(pub/"year_sensitivity.csv",index=False)
    (pub/"year_sensitivity_metadata.json").write_text(json.dumps({"status":"PASS","analysis":"Amendment006","bootstrap_requested":int(a.boot),"estimand_language":"adjusted longitudinal association in the common overlap target population","analyses":[x["analysis"] for x in rows],"aggregate_only":True},indent=2)+"\n")
    (pub/"year_sensitivity_audit.json").write_text(json.dumps({"status":"PASS","audits":audits,"bootstrap_successful":int(a.boot)},indent=2)+"\n")
    forbidden=["KEY_NRD","NRD_VISITLINK","HOSP_NRD"]
    text="\n".join(p.read_text(errors="ignore") for p in pub.iterdir() if p.is_file()); bad=[x for x in forbidden if x.lower() in text.lower()]
    (pub/"DISCLOSURE_GATE.json").write_text(json.dumps({"status":"PASS" if not bad else "FAIL","forbidden_tokens_found":bad},indent=2)+"\n")
    if bad: raise RuntimeError(f"disclosure failure: {bad}")
    files=sorted(pub.iterdir()); (pub/"PUBLIC_SHA256_MANIFEST.json").write_text(json.dumps({"status":"PASS","files":[{"file":p.name,"sha256":sha(p),"bytes":p.stat().st_size} for p in files]},indent=2)+"\n")
    (priv/"RUN_PRIVATE.json").write_text(json.dumps({"status":"PASS","bootstrap_requested":int(a.boot),"bootstrap_successful":int(a.boot),"analyses":audits},indent=2)+"\n")
    print("AMENDMENT006_PASS",flush=True)
if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--private-root",required=True); p.add_argument("--public-out",required=True); p.add_argument("--private-out",required=True); p.add_argument("--boot",type=int,default=BOOT); p.add_argument("--threads",type=int,default=8); build(p.parse_args())
