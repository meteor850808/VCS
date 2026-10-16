# SDD ledger — plan: docs/superpowers/plans/2026-08-27-mdb-support.md

## Task Status

- [x] Task 1: 安装依赖并验证环境 (commits 2271705, review clean)
- [x] Task 2: 创建 MDB 处理器核心功能 (commits c0e04f8..eff8cbe, fix round 1/5 - all addressed)
- [x] Task 3: 实现 StoredFile 转换功能 (completed in Task 2)
- [x] Task 4: 修改格式分类 (commits ccb638c, fix round 1/5 - all addressed)
- [x] Task 5: 修改上传端点支持 MDB (commits 7c8281f, fix round 1/5 - all addressed)
- [x] Task 6: 集成测试和验证 (commits ebc798b, fix round 1/5 - all addressed)
- [x] Task 7: 文档更新 (commits 05e6d87, review clean)
- [x] Task 8: 最终验证和清理 (commits 05e6d87, review complete)

## Rulings

Task 1: Minor finding M1 (report header mismatch) - deferred to final review
Task 2: fix round 1/5 (4 addressed, 0 open; commits c0e04f8..eff8cbe)
Task 4: fix round 1/5 (2 addressed, 0 open; commits ccb638c + fix commit)
Task 5: fix round 1/5 (1 addressed, 0 open; commits 7c8281f + fix commit)
Task 6: fix round 1/5 (3 addressed, 0 open; commits ebc798b + fix commit)

## Final Review

**Status:** APPROVED_WITH_CONCERNS
**Quality:** Good
**Key Finding:** Double GeoJSON parsing (I1) - performance/robustness concern, not blocking for merge
**Recommendation:** Ready to merge; address I1 in v2.12.1

## v2.12.1 Follow-ups (2026-08-28, E2E verification session)

- [x] I1 addressed + superseded: MDB 分支直接传 features 列表给 `_load_mdb_features_async`
      跳过 Fiona 重新解析（commit c3779c3）；同会话独立验证
- [x] Fix: esri_shapebin 环方向 — CW=outer/CCW=hole 经 shoelace 判定（`_ring_is_cw`），
      2 环 → Polygon+holes 而非 MultiPolygon；test_esri_shapebin.py 12 tests
- [x] Fix: shapebin 类型/flag 校验（拒绝 WKB 伪装 0x00000101）
- [x] Feat: CRS 提取 `_load_crs_map`（GDB_GeomColumns × GDB_SpatialRefs），SRTEXT 入库
- [x] Fix: `custom_crs._fix_swapped_false_origin` 误判 zoned-GK false_easting（41,500,000）
      导致 ST_Transform "outside projection domain" — 排除 `(x0-500000)%1000000==0`
- [x] Fix: MDB 分支父记录元数据回填（feature_count/layer_count/detected_crs/geometry_type）
      及子记录 feature_count + file_data 清理（与压缩包分支 P0-1 行为对齐）
- [x] Fix: `db.expire_all()` 后 `await db.refresh(archive_record)` 防 MissingGreenlet
- [x] E2E 通过: `tests/_e2e_upload_check.py` — 上传 201、94 要素、SRID 967453 (VCS-CUSTOM)、
      质心 ST_Transform → POINT(121.705 31.391) 上海闵行；幂等清理孤儿分区
- [x] 清理: 删除 tests/debug_mdb_*.py、test_mdb_real_file.py、_e2e_crs_check.py、_check_crs_parse.py
- [x] full suite: 67 passed (excl. test_api.py 预存 asyncpg loop 问题)