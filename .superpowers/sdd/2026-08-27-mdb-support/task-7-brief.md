## Task 7: 文档更新

**Files:**
- Modify: `BASELINE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 所有实现
- Produces: 更新的文档

- [ ] **Step 1: 更新 BASELINE.md**

```markdown
## 3. 数据库表结构

### 3.4 文件与空间存储

#### `stored_files` — 文件存储

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| ... | ... | ... | ... |
| data_type | String(20) | INDEX, DEFAULT 'unknown' | 数据类型（新增 'spatial' for MDB） |
| file_format | String(32) | INDEX, DEFAULT 'unknown' | 文件格式（新增 'GeoJSON' for converted MDB） |
```

- [ ] **Step 2: 更新 README.md**

```markdown
## 支持格式

| 格式 | 说明 | 状态 |
|------|------|------|
| ... | ... | ... |
| MDB | ESRI Personal Geodatabase | ✅ 支持 |
```

- [ ] **Step 3: 更新 BASELINE.md 版本号**

```markdown
> **当前版本**: v2.12.0  # 从 v2.11.0 升级
> **最后更新**: 2026-08-27  # 更新日期
```

- [ ] **Step 4: Commit**

```bash
git add BASELINE.md README.md
git commit -m "docs: update documentation for MDB support"
```