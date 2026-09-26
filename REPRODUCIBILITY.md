# Reproducibility instructions

This release separates the public computational record from the licensed
HCUP data. It is therefore reproducible by an authorized NRD holder, but it is
not a data-distribution repository.

## Public-only checks

Create/activate the environment in `environment.yml` (Python 3.10 or newer),
then run from the repository root:

```bash
python -m pytest -q
python code/validation/validate_aggregate_outputs.py --repo .
python code/validation/verify_public_package.py --repo .
```

These checks use only packaged aggregate files and a synthetic fixture. They do
not open, download, or reconstruct patient-level NRD records.

## Authorized NRD run

The primary v4.7 analysis uses 2018–2020 NRD-derived private inputs. Each year
must contain the exact structural, official-frame, and linked-outcome files
listed in `INPUT_DATA_SCHEMA.md`. Create a private directory outside the
repository and an empty `.authorization_receipts` directory beneath it.

The source/input allowlist is generated before a run:

```bash
python scripts/generate_v4_7_allowlist.py \
  --repo . \
  --private-root /path/to/private_nrd_root \
  --output /path/to/private_nrd_root/v4_7_allowlist.json
```

The resulting candidate is intentionally not executable. The operator should
inspect the source and input hashes, create a pre-existing run directory, and
regenerate with `--approve`, a unique authorization ID, and an exact run-root
binding. The v4.7 runner consumes that authorization once by atomically
creating the receipt; a second use is rejected.

```bash
python scripts/generate_v4_7_allowlist.py \
  --repo . \
  --private-root /path/to/private_nrd_root \
  --output /path/to/private_nrd_root/v4_7_allowlist.json \
  --authorized-run-id formal500-release-001 \
  --execution-authorization-id one-time-2026-001 \
  --authorized-run-root /path/to/run/formal500-release-001 \
  --mode formal --boot 500 --approve

python code/analysis/run_formal500_v4_7.py \
  --private-root /path/to/private_nrd_root \
  --private-out /path/to/run/formal500-release-001/private \
  --public-out /path/to/run/formal500-release-001/public \
  --sap protocol/SAP_v4_7.md \
  --hash-allowlist /path/to/private_nrd_root/v4_7_allowlist.json \
  --mode formal --boot 500 --threads 8
```

The public output is released only after the runner's disclosure gate passes.
Keep the private output, authorization receipt, raw archives, and all
patient-level Parquet files outside GitHub. Do not place them in a Git working
tree.

## Amendments

`code/analysis/run_amendment005.py` uses the packaged `frozen_v4_7` module and
produces aggregate timing, subgroup, and unmeasured-confounding outputs.
`code/analysis/run_amendment006.py` produces year-specific and leave-one-year-
out aggregate sensitivity outputs. Both scripts require explicit private and
public output paths and fail closed when a run destination already exists.

## Figures

The released PDF/PNG figures are in `figures/`. The authoritative generator
for the final Figure 1–5 set reads only packaged aggregate results:

```bash
Rscript code/figures/make_figures.R \
  data/processed figures/regenerated
```

The two narrower scripts retained for component-level verification can be run
as follows:

```bash
Rscript code/figures/make_formal500_figures.R \
  data/processed figures/regenerated
Rscript code/figures/make_figure5_timing_subgroup.R \
  data/processed/amendment005 figures/regenerated
```

The figure scripts may additionally create SVG/TIFF intermediates in the
specified output directory. Those generated intermediates are not required for
the public release.
