## Task 2 Fix Round 1: Address Review Findings

**Files:**
- Modify: `app/services/mdb_processor.py`
- Modify: `tests/test_mdb_processor.py`

**Interfaces:**
- Consumes: 无
- Produces: 修复后的代码

### Findings to Address

**Important (Should Fix)**

**1. `extract_mdb_to_stored_files` has zero test coverage**
`tests/test_mdb_processor.py` — the only test referencing this function is the import check at line 9–13. The function's logic (temp file write, `extract_mdb_features` call, GeoJSON serialization, `max_bytes` gating, `StoredFile` construction) is untested. At minimum, a test that writes a valid (or mock) MDB path and verifies the `max_bytes` skip path would confirm the branching logic.

**2. Outer `except Exception` is too broad**
`mdb_processor.py:101` catches `Exception` rather than `pyodbc.Error`. The brief's spec shows `pyodbc.Error`. Catching all exceptions here could mask unrelated bugs (e.g., a `KeyError` from malformed table data that should propagate). The inner `except Exception` at line 95 already handles per-table errors; the outer handler should be narrower.

**Minor (Nice to Have)**

**5. Module-level imports `tempfile`, `uuid`, `Path` unused at top scope**
`mdb_processor.py:14–16` — these are only used inside `extract_mdb_to_stored_files`. They could be deferred like `pyodbc` is, or left as-is for consistency. Low priority.

**6. Geometry column lookup uses hardcoded case variants**
`mdb_processor.py:63–66` checks `('Shape', 'shape', 'Geom', 'geom', 'Geometry', 'geometry')`. The column detection at line 51 already uses `.lower()` for the `has_shape` check, but the actual extraction loop uses explicit case variants. If an MDB has `SHAPE` or `GEOM`, it would be missed. A `.lower()` comparison here would be more robust.