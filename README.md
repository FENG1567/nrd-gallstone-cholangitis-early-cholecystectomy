# Reproducibility package: staged biliary intervention in gallstone cholangitis

This repository contains the public, reproducibility-oriented materials for the
gallstone-related acute cholangitis NRD study. It is designed for researchers
who are separately authorized to use the relevant Healthcare Cost and
Utilization Project (HCUP) Nationwide Readmissions Database (NRD) files.

## Data access and privacy

The licensed HCUP NRD files are **not** redistributed here. The 2018–2020
annual NRD files used by this paper must be obtained directly from HCUP under
the applicable data-use agreement. No patient-level records, raw archives,
parquet/SAS/Stata files, credentials, or private logs are included.

The repository contains only code, protocols, ontology/configuration, synthetic
test fixtures, aggregate-only result tables, and publication figures. The
public aggregate outputs are subject to the project disclosure gates and must
not be used to reconstruct individual records.

## Repository map

- `code/preprocessing/`: authorized-user input checks and public preprocessing
  entry points; raw-data extraction remains local to the authorized user. See
  `code/preprocessing/README.md` for the outcome-blind execution order.
- `code/analysis/`: frozen v4.7 analysis plus Amendment 005 and Amendment 006
  code, refactored to use relative paths/CLI arguments.
- `code/figures/`: final publication-figure generation from public aggregate
  source data.
- `code/validation/`: aggregate-output, disclosure, and manifest validation.
- `config/ontology/`: diagnosis/procedure ontology and variable dictionaries.
- `protocol/`: analysis plans, amendment notes, and target-trial definitions.
- `data/processed/`: aggregate-only public outputs; no row-level patient data.
- `figures/`: public PDF/PNG figures; their aggregate source data are in
  `data/processed/`.
- `code/analysis/`: synthetic/static tests that run without HCUP data; the
  empty `tests/` directory is retained only as a conventional repository
  location marker.
- `docs/`: data availability, input schema, inclusion/exclusion documentation,
  and reproducibility notes.

## Reproducing the analysis

1. Create the documented environment and confirm that the active interpreter
   is Python 3.10 or newer (do not rely on an unrelated legacy `python` found
   earlier on the system `PATH`):

   ```bash
   conda env create -f environment.yml
   conda activate nrd-biliary-reproducibility
   python --version
   ```

2. Obtain the appropriate annual NRD files and HCUP documentation under an
   active HCUP data-use agreement.
3. Create a local input manifest using the schema in `INPUT_DATA_SCHEMA.md`;
   never commit the raw files or patient-level extracts.
4. Read `REPRODUCIBILITY.md` and the frozen SAP before running code.
5. Run the synthetic/static checks first:

   ```text
   python -m pytest -q
   python code/validation/validate_aggregate_outputs.py --repo .
   ```

6. For an authorized full run, pass local input and output directories through
   the documented CLI arguments or environment variables. The packaged gates
   stop if a required input is absent, if a path points inside this repository,
   or if an output is not aggregate-only.

## License and citation

Original software and code in this repository are released under the MIT
License. HCUP data, documentation, and restricted source materials remain
governed by HCUP terms and are not covered by the MIT License.

Repository: https://github.com/FENG1567/nrd-gallstone-cholangitis-early-cholecystectomy

Stable release: https://github.com/FENG1567/nrd-gallstone-cholangitis-early-cholecystectomy/releases/tag/v1.0.0

Please cite the accompanying study and the exact repository release or commit
used for an analysis. Add a Zenodo DOI only if one is subsequently issued.

## Reproducibility status

The aggregate validation and Python static checks are intended to run in a
clean environment without licensed data. R-based figure regeneration requires
an R installation and the packages listed in `requirements-r.txt`; if R is not
available, the released PDF/PNG figures remain the public reference artifacts.

The encrypted nested-archive preprocessing path in
`code/preprocessing/nested_zip_frequency_v7.py` is intended for the Linux/POSIX
analysis server: it uses a pseudo-terminal, anonymous memory file descriptors,
and an external `7z`, `7zz`, or `7za` binary. Windows users can run the public
validation and synthetic analysis checks, but should run this archive-ingestion
self-test and the licensed-data extraction on the authorized Linux/POSIX host.
On that host, the fixture-only check is:

```bash
python code/preprocessing/nested_zip_frequency_v7.py self-test
```
