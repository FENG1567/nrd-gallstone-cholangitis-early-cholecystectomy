# Official annual hospital frame

The variance design uses the official annual NRD hospital frame, not a frame
reconstructed from the analytic cohort. For each annual file, an authorized
data holder must obtain the official hospital-to-stratum mapping from the
corresponding HCUP documentation or licensed data delivery and save only the
three-column aggregate mapping required by the runner:

```text
YEAR,HOSP_NRD,NRD_STRATUM
```

The mapping is not included in this public repository because it is derived
from licensed material. It must be generated locally and checked with:

```bash
python code/validation/validate_official_frames.py --root /path/to/private-root
```

The allowlist generator hashes the supplied files before execution. If an
annual frame is missing, duplicated, or inconsistent with the retained
structural cohort, the v4.7 runner stops before outcome attachment or public
result generation.
