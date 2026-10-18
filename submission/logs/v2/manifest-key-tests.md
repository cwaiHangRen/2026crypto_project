# v2 Manifest/KeyManager smoke
- workspace: gm-provenance-v2-20261018
- command: `$env:TEMP=.tmp-test; $env:TMP=.tmp-test; ..\gm-provenance\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp-test\pytest tests/v2/test_manifest_v2.py tests/v2/test_key_manager_v2.py`
- result: 6 passed in 5.07s
- note: Windows default pytest temp root was PermissionError; project-local .tmp-test passed.
- private key bytes are generated under pytest temp and not committed; SQLite stores public metadata and private path only.
