#!/usr/bin/env python3
"""Amendment 005: prespecified timing, subgroup and unmeasured-confounding analyses.

Only aggregate outputs are written to the public directory.  The script imports
the frozen Amendment 004 cohort and estimator from the immutable remote code
directory; the original formal500 directory is never modified.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd

for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
          "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ.setdefault(k, "1")

# Import the explicitly uploaded frozen estimator that sits beside this runner.
# The prior candidate accidentally imported a stale module from the v4
# diagnostic directory, which produced the obsolete n=5,191 cohort.  Binding
# the import to the runner directory makes the deployed code and estimator a
# single immutable candidate and prevents silent cross-candidate drift.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from frozen_v4_7 import (cohort_structure, load_official_frame, structure_probe,
                          design_gate, attach_outcomes_after_gate,
                          administrative_followup_window, ccw, raowu_resample,
                          sup, n)  # noqa: E402

PRIMARY_YEARS = (2018, 2019, 2020)
SEED = 20260919
DEFAULT_BOOT = 500
SMALL = 10

def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()

def safe_int(x):
    try:
        return int(x)
    except Exception:
        return None

def effect(q, label, n_input, n_a, n_b, status="PASS", reason=""):
    return {
        "analysis": label,
        "status": status,
        "reason": reason,
        "input_n": int(n_input),
        "early_label_n": int(n_a),
        "not_completed_label_n": int(n_b),
        "risk_early": float(q["risk_completed_by_day3"]),
        "risk_not_completed": float(q["risk_not_completed_by_day3"]),
        "rd": float(q["rd"]),
        "rr": float(q["rr"]),
        "events_early": q["events_completed_by_day3"],
        "events_not_completed": q["events_not_completed_by_day3"],
        "ess_early": float(q["ess_completed_by_day3"]),
        "ess_not_completed": float(q["ess_not_completed_by_day3"]),
        "bootstrap_successful": 0,
        "ci_rd_low": np.nan,
        "ci_rd_high": np.nan,
        "ci_rr_low": np.nan,
        "ci_rr_high": np.nan,
    }

_G_D = None
_G_FRAME = None
SUBGROUP_SPECS = [
    ("age", "<65"), ("age", "65+"), ("sex", "female"), ("sex", "male"),
    ("comorbidity", "0-1 chronic-code proxies"), ("comorbidity", "2+ chronic-code proxies"),
    ("ERCP procedure day", "day 0"), ("ERCP procedure day", "day 1+"),
    ("hospital teaching status", "non-teaching"), ("hospital teaching status", "teaching"),
]

def _init_worker(d, frame):
    global _G_D, _G_FRAME
    _G_D, _G_FRAME = d, frame

def timing_one(args):
    grace, seed = args
    d = _G_D
    rng = np.random.default_rng(seed)
    b = raowu_resample(d, rng, official_frame=_G_FRAME)
    q = ccw(b, endpoint="composite", horizon=30, grace=grace)
    return float(q["rd"]), float(q["rr"])

def subgroup_mask(x, family, level):
    if family == "age": return n(x, "AGE").lt(65) if level == "<65" else n(x, "AGE").ge(65)
    if family == "sex": return n(x, "FEMALE").eq(1) if level == "female" else n(x, "FEMALE").eq(0)
    if family == "comorbidity":
        c = x[[z for z in x.columns if z.startswith("CHRONIC_")]].sum(axis=1)
        return c.le(1) if level.startswith("0-1") else c.ge(2)
    if family == "ERCP procedure day": return n(x, "ERCP_DAY_MIN").eq(0) if level == "day 0" else n(x, "ERCP_DAY_MIN").ge(1)
    if family == "hospital teaching status": return n(x, "HOSP_UR_TEACH").eq(0) if level == "non-teaching" else n(x, "HOSP_UR_TEACH").ge(1)
    raise ValueError((family, level))

def one_subgroup_boot(seed):
    d = _G_D
    rng = np.random.default_rng(seed)
    b = raowu_resample(d, rng, official_frame=_G_FRAME)
    out = {}
    for family, level in SUBGROUP_SPECS:
        key = f"{family}|{level}"
        z = b.loc[subgroup_mask(b, family, level)].copy()
        if len(z) < 50 or z.complete_by_day3.nunique() < 2:
            out[key] = (np.nan, np.nan)
            continue
        try:
            q = ccw(z, endpoint="composite", horizon=30, grace=3)
            out[key] = (float(q["rd"]), float(q["rr"]))
        except Exception:
            out[key] = (np.nan, np.nan)
    return out

def evalue(rr):
    if not np.isfinite(rr):
        return np.nan
    x = max(float(rr), 1.0 / float(rr))
    return x + np.sqrt(x * (x - 1.0)) if x > 1 else 1.0

def build(a):
    pub, priv = Path(a.public_out), Path(a.private_out)
    if pub.exists() or priv.exists():
        raise RuntimeError("immutable amendment005 destination already exists")
    pub.mkdir(parents=True)
    priv.mkdir(parents=True)
    frames, official_frames = [], []
    for y in PRIMARY_YEARS:
        ff = load_official_frame(Path(a.private_root), y)
        d, _ = cohort_structure(Path(a.private_root), y, ff)
        frames.append(d)
        official_frames.append(ff)
    structure = pd.concat(frames, ignore_index=True)
    official_frame = pd.concat(official_frames, ignore_index=True)
    structure = structure.loc[~structure.same_day].copy()
    d30_struct, _ = administrative_followup_window(structure, 30)
    d90_struct, _ = administrative_followup_window(structure, 90)
    if design_gate(structure_probe(d30_struct, 30)).get("status") != "PASS" or design_gate(structure_probe(d90_struct, 90)).get("status") != "PASS":
        raise RuntimeError("independent outcome-blind v4.7 structure gate failed")
    observed = pd.concat([attach_outcomes_after_gate(structure.loc[structure.YEAR.eq(y)].copy(), Path(a.private_root), y) for y in PRIMARY_YEARS], ignore_index=True)
    d30, _ = administrative_followup_window(observed, 30)
    d90, _ = administrative_followup_window(observed, 90)
    if len(d30) == 0:
        raise RuntimeError("empty primary calendar-complete cohort")

    timing_rows = []
    seeds = np.random.default_rng(SEED).integers(1, 2**31 - 1, a.boot)
    with ProcessPoolExecutor(max_workers=min(8, a.threads), initializer=_init_worker, initargs=(d30, official_frame)) as ex:
      for grace in (1, 2, 3):
        # ccw labels completion by the prespecified decision window; this is a
        # timing-window gradient, not a mutually exclusive day-of-surgery effect.
        q = ccw(d30, endpoint="composite", horizon=30, grace=grace)
        label = f"completion by procedure-day {grace} vs not completed by day {grace}"
        observed_by_window = np.isfinite(n(d30, "early_day")) & n(d30, "early_day").le(grace)
        row = effect(q, label, len(d30), int(observed_by_window.sum()),
                     int(len(d30) - observed_by_window.sum()))
        vals = []
        for rd, rr in ex.map(timing_one, [(grace, int(s)) for s in seeds], chunksize=1):
            vals.append((rd, rr))
        vals = np.asarray(vals, float)
        good = np.isfinite(vals).all(axis=1)
        if int(good.sum()) != a.boot:
            row["status"] = "BOOTSTRAP_NOT_STABLE"
            row["reason"] = f"finite bootstrap replicates {int(good.sum())}/{a.boot}"
        else:
            row["bootstrap_successful"] = int(good.sum())
            row["ci_rd_low"], row["ci_rd_high"] = np.quantile(vals[:, 0], [0.025, 0.975])
            row["ci_rr_low"], row["ci_rr_high"] = np.quantile(vals[:, 1], [0.025, 0.975])
        row["decision_window_days"] = grace
        timing_rows.append(row)

    masks = SUBGROUP_SPECS
    compiled = []
    subgroup_rows = []
    for family, level in masks:
        z = d30.loc[subgroup_mask(d30, family, level)].copy()
        n_a = int(z.complete_by_day3.sum()) if len(z) else 0
        n_b = int(len(z) - n_a)
        key = f"{family}|{level}"
        if len(z) < 50 or z.complete_by_day3.nunique() < 2:
            subgroup_rows.append({"analysis": "prespecified subgroup", "family": family,
                                  "level": level, "status": "NOT_ESTIMABLE",
                                  "reason": "fewer than 50 records or one treatment label",
                                  "input_n": len(z), "early_label_n": n_a,
                                  "not_completed_label_n": n_b})
            continue
        try:
            q = ccw(z, endpoint="composite", horizon=30, grace=3)
            row = effect(q, "prespecified subgroup", len(z), n_a, n_b)
        except Exception as e:
            subgroup_rows.append({"analysis": "prespecified subgroup", "family": family,
                                  "level": level, "status": "NOT_ESTIMABLE",
                                  "reason": str(e)[:200], "input_n": len(z),
                                  "early_label_n": n_a, "not_completed_label_n": n_b})
            continue
        row.update({"family": family, "level": level})
        subgroup_rows.append(row)
        compiled.append((key, family, level))

    # The subgroup bootstrap uses the same hospital resampling draw for every
    # level, enabling a transparent descriptive RD contrast within each family.
    if compiled:
        vals = {key: [] for key, *_ in compiled}
        with ProcessPoolExecutor(max_workers=min(8, a.threads), initializer=_init_worker, initargs=(d30, official_frame)) as ex:
            for out in ex.map(one_subgroup_boot, [int(s) for s in seeds], chunksize=1):
                for key in vals: vals[key].append(out.get(key, (np.nan, np.nan)))
        for row in subgroup_rows:
            if row.get("status") != "PASS":
                continue
            key = f"{row['family']}|{row['level']}"
            arr = np.asarray(vals[key], float)
            good = np.isfinite(arr).all(axis=1)
            if int(good.sum()) == a.boot:
                row["bootstrap_successful"] = int(good.sum())
                row["ci_rd_low"], row["ci_rd_high"] = np.quantile(arr[:, 0], [0.025, 0.975])
                row["ci_rr_low"], row["ci_rr_high"] = np.quantile(arr[:, 1], [0.025, 0.975])
            else:
                row["status"] = "BOOTSTRAP_NOT_STABLE"
                row["reason"] = f"finite bootstrap replicates {int(good.sum())}/{a.boot}"

    # Approximate descriptive heterogeneity contrasts are added only when both
    # levels of a family have stable bootstrap arrays.  They are not causal
    # interaction effects and are not used to select the primary estimate.
    hetero = []
    for fam in sorted(set(x[1] for x in compiled)):
        levs = [x for x in compiled if x[1] == fam]
        if len(levs) != 2:
            continue
        k0, _, l0 = levs[0]; k1, _, l1 = levs[1]
        a0, a1 = np.asarray(vals[k0], float), np.asarray(vals[k1], float)
        good = np.isfinite(a0).all(axis=1) & np.isfinite(a1).all(axis=1)
        if int(good.sum()) != a.boot:
            continue
        diff = a0[good, 0] - a1[good, 0]
        hetero.append({"family": fam, "contrast": f"{l0} minus {l1}",
                       "rd_difference_point": float(diff.mean()),
                       "rd_difference_ci_low": float(np.quantile(diff, .025)),
                       "rd_difference_ci_high": float(np.quantile(diff, .975)),
                       "bootstrap_successful": int(good.sum()),
                       "interpretation": "descriptive effect-modification contrast; no causal interaction claim"})

    # E-value and deterministic symmetric confounding-strength curve use the
    # already validated formal500 RR. The CI values are bound to the public
    # formal500 aggregate result supplied as immutable inputs to this amendment.
    primary_rr, primary_upper = float(a.primary_rr), float(a.primary_rr_upper)
    sens = []
    for name, rr in (("point estimate", primary_rr), ("95% CI upper bound", primary_upper)):
        sens.append({"analysis": "E-value", "quantity": name, "rr": rr,
                     "e_value": evalue(rr),
                     "source": "formal500 public primary_results.csv; not re-estimated here"})
    for q in (1.25, 1.50, 1.75, 2.00, 2.50, 3.00):
        bf = q*q/(2*q-1)
        sens.append({"analysis": "symmetric confounding curve", "quantity": f"RR_UA=RR_UY={q:.2f}",
                     "rr": primary_rr, "bias_factor": bf, "bias_adjusted_rr": primary_rr*bf,
                     "source": "deterministic sensitivity formula; not a causal estimate"})

    plan = """# Amendment 005 added analyses\n\nThe added analyses were prespecified after completion of the frozen formal500 primary analysis. The timing analysis compares completion windows by procedure-day 1, 2, and 3 with the corresponding not-completed window using the same common-overlap-target clone-censor-IPCW estimator and hospital bootstrap. These are cumulative timing-window associations, not mutually exclusive day-of-surgery effects.\n\nSubgroups were age (<65/65+), sex, chronic-code proxy burden (0-1/2+), ERCP procedure day (0/1+), and hospital teaching status. Each estimate refits the overlap and censoring nuisance models within the subgroup and uses the same year-by-NRD-stratum Rao-Wu resampling. Sparse or unstable subgroup estimates are labelled and are not promoted to confirmatory claims.\n\nThe unmeasured-confounding analysis reports E-values and a deterministic symmetric risk-ratio confounding curve for the validated formal500 30-day composite RR. These quantities describe the strength of an unmeasured confounder needed to move the association toward the null; they do not prove absence of residual confounding.\n"""
    (pub / "ADDED_ANALYSIS_SAP.md").write_text(plan, encoding="utf8")
    pd.DataFrame(timing_rows).to_csv(pub / "timing_gradient.csv", index=False)
    pd.DataFrame(subgroup_rows).to_csv(pub / "subgroup_results.csv", index=False)
    pd.DataFrame(hetero).to_csv(pub / "subgroup_heterogeneity.csv", index=False)
    pd.DataFrame(sens).to_csv(pub / "sensitivity_unmeasured.csv", index=False)
    meta = {"status": "PASS", "analysis": "Amendment005", "primary_years": list(PRIMARY_YEARS),
            "timing_windows": [1, 2, 3], "subgroup_families": sorted(set(x[0] for x in masks)),
            "bootstrap_requested": int(a.boot), "public_outputs_aggregate_only": True,
            "estimand_language": "adjusted longitudinal association in the common overlap target population",
            "formal500_primary_rr": primary_rr, "formal500_primary_rr_upper_ci": primary_upper}
    (pub / "added_analysis_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf8")
    forbidden = ["KEY_NRD", "NRD_VISITLINK", "HOSP_NRD"]
    text = "\n".join(p.read_text(errors="ignore") for p in pub.iterdir() if p.is_file())
    bad = [x for x in forbidden if x.lower() in text.lower()]
    (pub / "DISCLOSURE_GATE.json").write_text(json.dumps({"status": "PASS" if not bad else "FAIL", "forbidden_tokens_found": bad}, indent=2) + "\n", encoding="utf8")
    if bad:
        raise RuntimeError(f"disclosure gate failure: {bad}")
    files = [p for p in sorted(pub.iterdir()) if p.is_file()]
    (pub / "PUBLIC_SHA256_MANIFEST.json").write_text(json.dumps({"status": "PASS", "files": [{"file": p.name, "sha256": sha(p), "bytes": p.stat().st_size} for p in files]}, indent=2) + "\n", encoding="utf8")
    (priv / "RUN_PRIVATE.json").write_text(json.dumps({"status": "PASS", "bootstrap_requested": int(a.boot)}, indent=2) + "\n", encoding="utf8")
    print("AMENDMENT005_PASS", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", required=True, help="licensed local NRD-derived input root; never commit this directory")
    ap.add_argument("--public-out", required=True)
    ap.add_argument("--private-out", required=True)
    ap.add_argument("--boot", type=int, default=DEFAULT_BOOT)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--primary-rr", type=float, required=True)
    ap.add_argument("--primary-rr-upper", type=float, required=True)
    build(ap.parse_args())
