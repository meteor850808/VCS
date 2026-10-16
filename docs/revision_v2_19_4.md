# v2.19.4 修订记录

**发布日期**: 2026-09-17 · **版本**: v2.19.4

## 背景

v2.19.3 修复后用户反馈：**MDB 生成成功，但 Microsoft Access 无法打开**。

服务端回归全绿（pyodbc 可正常建表/读表），但用户端打开失败 —— 说明文件结构与 Access 打开路径不兼容。

## 根因

用 `_create_empty_mdb`（ADOX + `Provider=Microsoft.ACE.OLEDB.12.0`）建库在本机（Office ClickToRun）产生了**错误的文件格式**：

- 系统实际只装有 **ACE 16.0**（Office 2016+ ClickToRun），注册表中 `Microsoft.ACE.OLEDB.12.0` 与 `16.0` 的 CLSID **均指向同一个 Office16 `ACEOLEDB.DLL`**（老 ProgID 被重定向）。
- ACE 16.0 的 `ADOX.Catalog.Create` 生成的是 **Access 2010+ 内部格式（.accdb 结构）**：
  - 我们的文件头：`Standard ACE DB` + 版本字节 `0x02`
  - Access 能打开的真实 mdb（Office 自带 DBSAMPLE.MDB、项目夹具）：`Standard Jet DB` + 版本字节 `0x01`（Jet4 / Access 2000）
- 结果：**.mdb 扩展名 + accdb 内容** → Microsoft Access / ArcGIS（个人地理数据库仅支持 Jet4）打开报「无法识别/不是有效数据库」。
- pyodbc 用 `Microsoft Access Driver (*.mdb, *.accdb)`（ACE 兼容读两种格式）测试全绿，恰好掩盖了问题。

## 修复方案：Jet4 模板复制 + pyodbc 原地写入

| 文件 | 改动 |
|------|------|
| `app/resources/empty_jet4.mdb`（新增） | 真 Jet4 / Access 2000 空库模板（640KB）。来源：`tests/fixtures/mdb/sample_migration.mdb`（ArcGIS 个人地理数据库，Jet4 格式）用 pyodbc 删除 17 张 Esri 用户表后固化，保留 MSys* 系统表 |
| `app/services/export_service.py` | 新增 `_jet4_template_path()`（定位模板：包内 resources → tests/fixtures 夹具）、`_is_jet4_mdb()`（头校验 `Standard Jet DB` + 0x01）、`_drop_all_user_tables()`（夹具回退时清空 Esri 表）；`_create_empty_mdb` 改为**复制模板 + pyodbc 原地写入**（实测 ACE 驱动读写 Jet4 库**不会升级格式**，头保持不变），模板缺失时回退旧 ADOX 建库 |
| `tests/test_data_download_export.py` | 新增 `test_is_jet4_mdb`（导出产物必须是 Jet4）+ 大整数保整用例并入 Jet4 断言 |

## 关键实测结论

- ACE 16 驱动打开真 Jet4 库并 `CREATE TABLE`/`INSERT`/中文写入后，**文件头保持 `Standard Jet DB` + 0x01**（不会就地升级格式）。
- 多步排障：文件头字节比对（DBSAMPLE.MDB vs 我们导出）→ 模板生成（清空 Esri 表）→ 模板被 `_JET4_TEMPLATE` str/bytes 比较 bug 挡住（切片 `head[4:19]` 15 字节 + str==bytes 恒 False）→ 修复后 Jet4 全流程复测通过。

## 验证

- 真实文件 `7e9d1302`（185 特征）/ `d9f3b0a7`（9 特征）：mdb 导出 **JET4_OK**（`Standard Jet DB` + ver 0x01），pyodbc 建表/读表/中文/大整数全部正常。
- 真实服务 HTTP：两文件 `GET /data/{id}/download?format=mdb` → **200**，`application/msaccess`，头校验 JET4_OK（2,486,272 B / 655,360 B）。
- `tests/test_data_download_export.py`：**31 passed**
- 全量回归：**1526 passed / 99.02%**（venv Python 3.13）

## 兼容性说明

- Access 97/2000/2003/2007/2010/2013/2016/365 均可打开 Jet4 / Access 2000 格式 mdb。
- ArcGIS 个人地理数据库（.mdb）同样依赖 Jet4 —— 本修复使 GIS 用户侧可正常加载属性表。