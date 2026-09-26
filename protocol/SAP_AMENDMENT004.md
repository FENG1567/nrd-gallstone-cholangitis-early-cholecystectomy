# Amendment 004 frozen analysis plan

Primary NRD years are 2018--2020. Time zero is the recorded therapeutic ERCP
procedure-day. The observed dynamic labels are complete cholecystectomy on
procedure-days 1--3 versus no complete cholecystectomy through the end of
procedure-day 3, followed by usual care thereafter. This is an adjusted
longitudinal association in an administrative overlap population, not a causal
effect, active planned-delay comparison, or treatment recommendation.

Same-procedure-day ERCP/cholecystectomy is excluded because ordering is
unobservable. Two clones are made at ERCP day zero. The early clone is adherent
on days 1--2 without surgery, and is censored before day 4 if still incomplete;
a live discharge before completion censors it on the next day. The
not-completed clone censors at the first complete operation on days 1--3, then
follows usual care. An observed endpoint wins a same-day event/censor tie.

The fixed propensity model is survey-mass weighted L2 logistic regression
(C=1.0), with cubic four-knot splines for age and ERCP day, one-hot fixed
administrative/hospital/year covariates and fixed chronic-code proxies. It
excludes `NRD_STRATUM`, which is used only for variance. Missing numeric and
categorical values use median and most-frequent imputation. Support is fixed at
0.05 <= e(X) <= 0.95. Both clones receive the identical factor
`DISCWT × e(X) × [1-e(X)]`; this defines one target density proportional to
`f_survey(X)e(X)[1-e(X)]`.

The artificial-censoring pooled logistic model uses the same time-zero basis,
procedure day, regime, and regime-by-day terms. It excludes APR-DRG, LOS,
charges, I10_NDX, death, endpoints, and post-ERCP covariates. The weight is the
common target factor times the product of stabilized regime/day adherence
probability ratios. Only IPCW is truncated at the 1st/99th percentiles.

Effect output is blocked unless the frozen outcome-blind structural probe shows
at days 1--3 maximum SMD <=0.10, per-arm day-4 count and ESS >=200, and 99th
percentile stabilized IPCW <=10. Endpoint-reduced risk-set SMDs are diagnostic
only and cannot change the model.

Primary outcome is observed in-hospital death or same-state non-elective
inpatient readmission within 30 days. Secondary outcomes are 90-day composite,
30-day observed readmission and 30-day observed in-hospital death. Biliary
readmission is exploratory; LOS and charges are descriptive only. Uncertainty
uses 500 YEAR × NRD_STRATUM Rao--Wu rescaled-hospital bootstraps. Every
replicate resamples whole patients/both clones and refits support, e(X), IPCW
and truncation. Any failed/non-finite replicate fails the estimand.

Residual clinical severity, frailty, ERCP success, surgical suitability,
within-day order, lack of diagnosis-level POA, out-of-hospital death,
outpatient events and cross-state events remain central limitations.
