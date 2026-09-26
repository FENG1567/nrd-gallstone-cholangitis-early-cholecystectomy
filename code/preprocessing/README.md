# Licensed-data preprocessing

These scripts are public, but their inputs are licensed HCUP NRD files. Run
them only on an authorized Linux/POSIX host, with raw archives, HCUP SAS load
programs, and every patient-level output kept outside the Git repository.
Passwords are requested interactively and must not be supplied on a command
line, stored in an environment variable, or committed.

## Which scripts feed the frozen v4.7 analysis

For each of 2018, 2019, and 2020:

- `private_nrd_extract_v1.py index` creates
  `<private-root>/<year>/index_candidates_<year>.parquet`;
- `private_nrd_extract_v1.py followup` creates
  `<private-root>/<year>/linked_discharges_<year>.parquet`;
- `private_nrd_aux_extract_v1.py hospital` creates
  `<private-root>/<year>/hospital_linked_<year>.parquet`;
- the authorized researcher separately derives the official annual
  `YEAR,HOSP_NRD,NRD_STRATUM` frame described in
  `docs/official_hospital_frame.md`.

Those four annual inputs are the files consumed by the frozen runner. The
`severity` and `dxpr` auxiliary modes and
`private_build_analysis_cohort_v1.py` are retained for documented private QC
and optional cohort-building work; they are not substitutes for the frozen
runner's inputs.

## Outcome-blind order of operations

1. Inspect the annual archive members with `list_nrd_inner_members.py` and
   match each member to the corresponding HCUP SAS load program. Member names
   differ by delivery and must not be guessed.
2. Run the archive fixture before opening licensed inputs:

   ```bash
   python code/preprocessing/nested_zip_frequency_v7.py self-test
   ```

3. Run the outcome-blind frequency/ontology audit with
   `run_stage2_frequency.sh` and validate its public output with
   `validate_stage2_v8.py`.
4. Create the structural index file first, then the hospital covariate file.
5. Create the linked-discharge file only after the documented structural gates
   have passed. Keep it private.
6. Supply the official annual hospital frame and run
   `code/validation/validate_official_frames.py`.
7. Generate and explicitly approve the hash allowlist before using the
   frozen v4.7 runner. See `REPRODUCIBILITY.md`.

## Generic command templates

The following placeholders must be replaced with paths in the authorized
environment. They are intentionally not hard-coded here.

```bash
python code/preprocessing/private_nrd_extract_v1.py index \
  --year YEAR --archive /licensed/NRD_YEAR.zip \
  --outer-member CORE_INNER_ARCHIVE_NAME \
  --ontology config/ontology/ontology_v2.0.csv \
  --sas-load-program /licensed/SASLoad_NRD_YEAR_Core.SAS \
  --private-root /private/nrd-root --project-root /public/qc-root \
  --threads 8 --memory-gib 20

python code/preprocessing/private_nrd_aux_extract_v1.py hospital \
  --year YEAR --archive /licensed/NRD_YEAR.zip \
  --outer-member HOSPITAL_INNER_ARCHIVE_NAME \
  --sas-load-program /licensed/SASLoad_NRD_YEAR_Hospital.SAS \
  --private-root /private/nrd-root --project-root /public/qc-root \
  --threads 8 --memory-gib 20

python code/preprocessing/private_nrd_extract_v1.py followup \
  --year YEAR --archive /licensed/NRD_YEAR.zip \
  --outer-member CORE_INNER_ARCHIVE_NAME \
  --ontology config/ontology/ontology_v2.0.csv \
  --sas-load-program /licensed/SASLoad_NRD_YEAR_Core.SAS \
  --private-root /private/nrd-root --project-root /public/qc-root \
  --threads 8 --memory-gib 20
```

All three commands fail closed rather than overwrite an existing output. Use a
new private run directory for a deliberate rerun; do not delete or replace a
completed run in place.
