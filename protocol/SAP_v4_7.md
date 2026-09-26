# Detailed v4.7 technical implementation protocol

This file records the implementation-level v4.7 correction and execution
barriers. The current public analysis plan is `SAP_AMENDMENT004.md`; this
detailed file is not a second prespecification and must be read together with
that public SAP.

## v4.7 formal-only outcome-attachment correction

The first v4.6 formal point-estimate preparation stopped before an estimate was
generated because its post-gate linked-outcome merge redundantly supplied
structure-owned fields (`same_day`, `early_day`, `complete_by_day3`, and
`index_discharge_time`).  Pandas therefore created merge-suffixed columns and
the run terminated.  No point estimate, bootstrap replicate, confidence
interval, P value, or public result was generated or inspected.  Version 4.7
changes only this field-source boundary: `cohort_structure()` remains the sole
owner of those four fields, while post-gate attachment contributes only
`death_time`, `readmit_time`, `biliary_time`, and `death_index`.  It hard-fails
on missing/non-unique structure fields, duplicate keys, collisions, suffixes,
or any changed row/key order/value.  This is an implementation correction, not
a prespecification claim, and changes no cohort, estimand, strategy, endpoint,
covariate, support rule, IPCW, truncation, DMONTH window, or Rao--Wu logic.

# Amendment 004 v4.2 outcome-blindness amendment statistical analysis plan

## v4.2 outcome-blindness and frame-audit correction

In gate-only and structure-dryrun modes, linked-outcome paths are neither stated, opened, hashed, nor parsed. Their allowlist declarations are deferred metadata only. In formal mode, both outcome-blind structure gates must pass before the linked files are hash-verified and then parsed for attachment. The pre-effect record retains an identifier-free annual-frame/domain mapping audit. This correction changes no statistical formula, estimand, weights, outcomes, or Rao--Wu design.

## v4.1 runtime-integrity correction

Version 4.1 corrects the candidate runtime identity chain after static audit. It binds both the core and regression test suites, separates structural, official-frame, and linked-outcome hash groups, verifies linked-outcome bytes without parsing outcomes before the outcome-blind gate, and sanitizes private dry-run failure messages before serialization. It does not change the cohort, official-frame Rao--Wu multiplicity formula, estimand, outcome definitions, weights, truncation, administrative windows, or bootstrap count.

## Outcome-blind official-frame bootstrap amendment

This is the first implementation amendment after the outcome-blind v3 dry run failed because an analysis-domain YEAR × NRD_STRATUM cell contained one observed hospital. No linked outcome, effect estimate, confidence interval, P value, or result was inspected to select this change. The prior `hospital_linked` file is a linked-Core hospital-attribute table, used only for covariate attachment. It is not the official annual Hospital frame.

For each replicate, the official annual Hospital file defines H hospitals in every YEAR × NRD_STRATUM. One multinomial multiplicity vector is drawn as Multinomial(H−1; 1/H,...,1/H), with scale H/(H−1). The same private map is generated once and applied to every primary 30/90-day window, conservative DMONTH window, and endpoint. Analysis-domain rows with multiplicity zero are omitted; no epsilon weight is introduced. Before any gate, every retained analysis hospital must uniquely match the official frame and have the same stratum. Any unmatched hospital, duplicate official key, stratum mismatch, official singleton, empty arm, or non-finite calculation is a hard failure.

The point estimator, eligibility, years, DMONTH rules, propensity/support model, DISCWT × multiplicity × scale × e(X)[1−e(X)] target factor, IPCW, 1st/99th-percentile truncation, endpoints, and 500-replicate requirement remain unchanged. Private QC records only aggregate frame/domain counts and never hospital identifiers. Methods, limitations, and the study flow diagram must disclose this implementation amendment at manuscript stage.

## Scope and claim boundary

The primary NRD years are 2018--2020. Time zero is the recorded therapeutic ERCP procedure-day. The labels are complete cholecystectomy on procedure-days 1--3 and no complete cholecystectomy through the end of day 3 followed by usual care. Results are **adjusted longitudinal associations** in the common overlap target population; they are not causal effects, treatment recommendations, active-comparator effects, or NNTs.

Same-procedure-day ERCP/cholecystectomy is excluded from the primary analysis because within-day ordering is unavailable. The early clone remains adherent on days 1--2 while surgery is pending and is structurally censored before the day-4 risk set if completion has not occurred. A live index discharge before completion likewise structurally censors before the next risk set. The not-completed clone is censored at a real complete operation on days 1--3 and then follows usual care. Event-first applies only to a real operation/event tie; a competing death tied to a real operation remains in that procedure-day competing-risk set. Neither rule overrides deadline or live-discharge structural censoring.

## DMONTH administrative observability rule (approved)

HCUP defines \`DMONTH\` as the **discharge month**, derived from discharge date, not an ERCP month or baseline covariate (HCUP 2022 NRD Introduction, pp. 23--24; HCUP DMONTH variable note: https://hcup-us.ahrq.gov/db/vars/dmonth/nrdnote.jsp). For in-year NRD linkage, the primary administratively observable cohorts are restricted to discharge months 1--11 for 30-day follow-up and 1--9 for 90-day follow-up. Conservative prespecified sensitivity cohorts use months 1--10 and 1--8 respectively. Missing, non-integer, and values outside 1--12 are excluded before window selection and counted separately.

\`DMONTH\` is never included in the BASE covariates, propensity model, IPCW model, target density, SMD calculations, or outcome model. The manuscript must state prominently that this administrative restriction may introduce post-time-zero selection and that NRD does not capture outpatient events, cross-state admissions, or out-of-hospital death.

## Frozen estimator and diagnostics

The propensity model is survey-mass weighted L2 logistic regression (C=1.0), cubic four-knot splines for age and ERCP day, and one-hot fixed baseline covariates. Support is 0.05--0.95. Both clones receive the identical baseline factor \`DISCWT × e(X) × [1-e(X)]\`; \`NRD_STRATUM\` is used only for variance, not clinical adjustment. The pooled logistic artificial-censoring model uses the same basis with regime/day/regime-by-day terms. Only IPCW is truncated at its 1st/99th percentiles.

The outcome-blind gates are separately evaluated in the 30-day and 90-day administrative cohorts before linked discharge data can be opened. They require all-level categorical and continuous-bin SMD <=0.10, per-arm day-4 count and ESS >=200, and IPCW q99 <=10. A structural gate fits nuisance models but never computes endpoint risks or calls the estimator. Its `index_discharge_time` is the deterministic structural index-row quantity `LOS − ERCP_DAY_MIN`, generated after valid ERCP-day chronology checks; it is not obtained from linked discharges or any death/end-point field. The distinct `gate-only` mode requires `--boot 0`, validates an explicitly approved allowlist, reads only structural inputs, writes the private paired structure-gate/hash-binding record, and exits before any linked-outcome hash, attach, bootstrap, public directory, risk, RD, RR, or CI path.

Formal inference requires exactly 500 Rao--Wu rescaled hospital bootstrap replicates within \`YEAR × NRD_STRATUM\`; every replicate rebuilds the clones, support, propensity model, IPCW, and truncation. Only mechanical failures (absent support, empty arm, non-finite weights, or model failure) stop formal inference. Replicate SMD, ESS, and q99 are recorded as distributions and are not vetoed merely for ordinary resampling variation.

The private 1/20/100-replicate dry run is a strictly structural implementation check, separate from formal inference. For each attempted replicate and for each horizon, it records an aggregate-only YEAR-by-stratum Rao--Wu audit (number of input PSUs, \(H-1\) draws, unique sampled PSUs, multiplicity total and scale); independently refitted propensity/support counts; daily artificial-censoring-model finite checks; the full day-1--3 observed categorical-level SMD list and all five declared continuous empirical-quantile-bin SMD rows (including explicit empty, degenerate-boundary, zero-denominator, and non-finite statuses); their mechanically derived maxima; and per-arm day-4 counts, raw-IPCW and formal-truncated full-weight quantiles/ESS. It invokes the identical reusable formal 1st/99th IPCW truncation routine, but never evaluates any endpoint. All attempts, including failures, are serialized to a private record before a mechanical failure returns a non-zero exit status. A dry run cannot open linked discharges, attach outcomes, calculate comparative measures, or create public output; its diagnostics are not replicate veto criteria.

Formal workers are pre-bound to a replicate identifier and seed. If a Rao--Wu sampling, nuisance-model, or estimator step fails, aggregate-only sampling evidence plus a bounded, identifier-sanitized failure record are written privately with the verified hash binding before formal inference exits non-zero. No later replicate is scheduled after the first observed failure, no failed replicate is replaced, and no public result is created. Public inference remains possible only when all four primary estimands have 500/500 finite replicates.

Primary outcome: observed in-hospital death or same-state non-elective inpatient readmission within 30 days. Secondary: 90-day composite, 30-day observed readmission (death as a competing event via CIF), and 30-day observed in-hospital death. The conservative 30- and 90-day DMONTH windows are prespecified sensitivity estimands.

## Immutable signing barrier

Before any data parsing, an approved allowlist must exactly match the runner, this SAP, tests, frozen BASE-config file, and the six structural parquet files. After both outcome-blind gates pass, the three linked-discharge files are exact-matched immediately before outcomes are attached. The distributed allowlist is explicitly candidate-only and cannot deploy until it is explicitly approved and bound on the server.
