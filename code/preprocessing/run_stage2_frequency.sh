#!/usr/bin/env bash
set -euo pipefail

# Public launcher for an authorized HCUP user.  Raw archives and SAS load
# programs stay outside this repository and are never copied to its output.
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 YEAR OUTPUT_DIR" >&2
  exit 2
fi
: "${NRD_RAW_ROOT:?Set NRD_RAW_ROOT to the separately licensed NRD archive directory}"
: "${NRD_PROJECT_ROOT:?Set NRD_PROJECT_ROOT to a private project/output directory}"

year="$1"
output_dir="$2"
script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
archive="${NRD_RAW_ROOT}/NRD_${year}.zip"
ontology="${script_root}/../../config/ontology/ontology_v2.0.csv"
sas_program="${NRD_PROJECT_ROOT}/sas_load/SASLoad_NRD_${year}_Core.SAS"

python3 "${script_root}/nested_zip_frequency_v7.py" run \
  --year "${year}" \
  --archive "${archive}" \
  --ontology "${ontology}" \
  --sas-load-program "${sas_program}" \
  --output-dir "${output_dir}" \
  --threads "${NRD_THREADS:-8}" \
  --memory-gib "${NRD_MEMORY_GIB:-20}"
