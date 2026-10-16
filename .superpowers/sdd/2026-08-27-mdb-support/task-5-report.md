## Task 5: 修改上传端点支持 MDB — Report

**Status:** DONE

### What I implemented

Modified `app/api/v1/data.py` to add an MDB processing branch in the `upload_file` endpoint (lines 460–536). The branch:
- Detects `.mdb` extension on uploaded files
- Calls `extract_mdb_to_stored_files()` to convert MDB tables to StoredFile objects
- Creates `StoredFile` for each extracted spatial table
- Populates metadata via `_detect_and_populate()`
- Links to standard if `standard_id` provided
- Queues async feature loading via `_load_all_features_to_spatial_async()`
- Returns early with `StoredFileUploadResponse`, skipping archive/non-archive processing

### What I tested

- `tests/test_upload_mdb.py::test_upload_mdb_endpoint_exists` — PASSED
- `tests/test_mdb_processor.py` — 11/11 PASSED
- `tests/test_format_detection_mdb.py` — 7/7 PASSED
- Python syntax check on modified `data.py` — OK

### Files changed

- `app/api/v1/data.py` — added MDB branch (~77 lines)
- `tests/test_upload_mdb.py` — new test file

### Commit

- `7c8281f` — `feat: add MDB upload support to data endpoint`

### Self-review findings

- Fixed incorrect function call signature (`_load_all_features_to_spatial_async` takes `(stored_file_id, filename, content, api_key_id)`, not `(db, stored_file_id, ...)`) during implementation
- MDB branch correctly returns early to skip archive/non-archive processing as required
- All tests pass (19/19 across MDB-related test files)
