# Test location

The frozen v4.7 tests are kept beside the implementation in
`code/analysis/`, because the execution allowlist binds those exact source
files and their hashes. Run them from the repository root with:

```bash
python -m pytest -q code/analysis
```

They are synthetic/static checks and do not require HCUP data.
