# Figure generation

`make_figures.R` is the authoritative current figure generator for the final
2018–2020 staged-intervention release. It reads `data/processed/primary_results.csv`
and the Amendment 005/006 aggregate outputs; it does not embed a private path
or read patient-level data.

`make_formal500_figures.R` is retained as a compact v4.7-specific generator for
the primary forest/QC panels. `make_figure5_timing_subgroup.R` generates the
Amendment 005 timing/subgroup figure. All scripts accept repository-relative
input/output paths via command-line arguments.
