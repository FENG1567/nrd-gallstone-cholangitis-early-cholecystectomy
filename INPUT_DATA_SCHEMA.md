# Input schema for authorized users

The v4.7 runner expects a private root with the following relative files. The
files must be produced from the user's licensed NRD data and must remain
outside the Git repository.

```text
<private-root>/
  .authorization_receipts/
  2018/
    index_candidates_2018.parquet
    hospital_linked_2018.parquet
    linked_discharges_2018.parquet
    official_nrd_hospital_frame_2018.csv
  2019/
    index_candidates_2019.parquet
    hospital_linked_2019.parquet
    linked_discharges_2019.parquet
    official_nrd_hospital_frame_2019.csv
  2020/
    index_candidates_2020.parquet
    hospital_linked_2020.parquet
    linked_discharges_2020.parquet
    official_nrd_hospital_frame_2020.csv
```

The structural candidate files contain the time-zero phenotype, eligibility,
procedure-day, hospital, and baseline covariates required by
`code/analysis/frozen_v4_7.py`. The linked-discharge files contain the
follow-up variables required only after the two outcome-blind structure gates
pass. The annual hospital-frame files must have exactly these columns:

```text
YEAR,HOSP_NRD,NRD_STRATUM
```

`YEAR × HOSP_NRD` must be unique, every hospital must map to the documented
annual stratum, and each stratum must contain at least two hospitals. Validate
the frames before the allowlist is approved:

```bash
python code/validation/validate_official_frames.py --root /path/to/private-root
```

The stage-2 preprocessing scripts create private Parquet files beneath the
configured private root and only write aggregate frequency/funnel summaries to
the public output location. They request archive passwords interactively; a
password must never be placed in a command line, environment variable, source
file, or Git history.
