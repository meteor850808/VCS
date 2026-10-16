## Task 1: 安装依赖并验证环境

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: 无
- Produces: pyodbc 依赖安装

- [ ] **Step 1: 添加 pyodbc 到 requirements.txt**

```bash
# 在 requirements.txt 末尾添加
pyodbc>=5.1.0
```

- [ ] **Step 2: 安装依赖**

```bash
pip install pyodbc>=5.1.0
```

- [ ] **Step 3: 验证 pyodbc 安装**

```python
import pyodbc
print(f"pyodbc version: {pyodbc.version}")
print(f"Available drivers: {pyodbc.drivers()}")
```

- [ ] **Step 4: 验证 ODBC 驱动可用**

```python
# 检查是否有 Access ODBC 驱动
drivers = [d for d in pyodbc.drivers() if 'Access' in d]
if not drivers:
    print("Warning: No Access ODBC driver found. Install Microsoft Access Database Engine.")
else:
    print(f"Access drivers: {drivers}")
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "deps: add pyodbc for MDB support"
```