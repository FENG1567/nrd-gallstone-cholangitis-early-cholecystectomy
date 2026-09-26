# Data availability and access restrictions

The study uses Healthcare Cost and Utilization Project (HCUP) Nationwide
Readmissions Database (NRD) files. The licensed annual files are not included
in this repository and cannot be redistributed by the authors. Researchers
must obtain the relevant years directly from HCUP, complete the applicable
data-use training/agreement, and follow HCUP disclosure rules.

The primary v4.7 analysis and the packaged amendments use NRD-derived private
inputs from 2018, 2019, and 2020. These are the only study years represented by
the public aggregate release. No later-year validation dataset is represented
in this package; adding one would require a separately licensed file and a
new, disclosure-reviewed release.

No raw archive, SAS/Stata file, Parquet patient-level extract, direct
identifier, credential, or server log is part of this repository. The files in
`data/processed/` are aggregate-only and are retained to make the published
estimands and figure source data inspectable without exposing individual
records.

HCUP terms control the source data. They are not replaced by the software
license decision recorded in `LICENSE_DECISION.md`.
