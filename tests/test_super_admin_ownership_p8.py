# -*- coding: utf-8 -*-
"""超级管理员写归属列：所有 api_key_id 外键列必须落 NULL。

回归背景（2026-11-11）：超管的 ``api_key.id`` 是虚拟 ID
``00000000-0000-0000-0000-000000000001``，``api_keys`` 表没有这一行。
把它写进任何带外键的 ``api_key_id`` 列都会 500
（``ForeignKeyViolationError``）。``POST /api/v1/convert`` 已先修，
本文件锁定其余端点：一律写 ``api_key.owner_id``（超管 → NULL）。

同时用静态扫描兜住「新增代码又写回虚拟 ID」的回归。
"""

import ast
import io
import tokenize
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.models.data_standard import DataStandard
from app.models.quality_rule import QualityRule
from app.models.source_data import SourceTable, StandardVersion
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable

# 超管鉴权：X-API-Key 命中 ADMIN_INIT_KEY 即走 AuthContext.create_super_admin
ADMIN = {"X-API-Key": settings.admin_init_key}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_db(test_db):
    yield


@pytest_asyncio.fixture
async def tolerant_client():
    """不把服务端异常抛给测试：断言 HTTP 状态码，而不是接 IntegrityError。"""
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _admin_standard(db_session) -> DataStandard:
    """造一个归属于超管（api_key_id IS NULL）的数据标准。"""
    std = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=None,
        code=f"ADM-{uuid.uuid4().hex[:8]}",
        name=f"超管标准 {uuid.uuid4().hex[:6]}",
        category="custom",
        status="draft",
    )
    db_session.add(std)
    await db_session.flush()
    return std


async def _admin_field(db_session, std: DataStandard) -> StandardField:
    """给超管标准挂一个可生成 LENGTH_LIMIT 规则的字段。"""
    lg = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id,
        group_name="超管层组", group_code=f"ADM{uuid.uuid4().hex[:4]}",
    )
    db_session.add(lg)
    await db_session.flush()
    t = StandardTable(
        id=str(uuid.uuid4()), layer_id=lg.id, standard_id=std.id,
        table_name="超管表", table_code=f"ADM_T{uuid.uuid4().hex[:4]}",
        table_type="spatial",
    )
    db_session.add(t)
    await db_session.flush()
    f = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=t.id,
        field_name="道路等级", data_type="string", length=10,
        nullable=False, is_required=True,
    )
    db_session.add(f)
    await db_session.flush()
    return f


class TestQualityRuleRows:
    """质检规则表是唯一 NOT NULL 的 api_key_id 列，必须先放宽再允许超管写。"""

    async def test_created_rule_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/quality-rules",
            headers=ADMIN,
            json={"rule_type": "LENGTH_LIMIT", "rule_name": "超管规则",
                  "params": {"max_length": 10}},
        )
        assert resp.status_code == 201, resp.text
        rule = await db_session.get(QualityRule, resp.json()["id"])
        assert rule is not None
        assert rule.api_key_id is None, rule.api_key_id

    async def test_generated_rules_have_null_owner(self, tolerant_client, db_session):
        std = await _admin_standard(db_session)
        await _admin_field(db_session, std)
        await db_session.commit()

        resp = await tolerant_client.post(
            f"/api/v1/quality-rules/generate/{std.id}", headers=ADMIN,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json(), "标准含字段时应至少生成一条规则"
        for row in resp.json():
            saved = await db_session.get(QualityRule, row["id"])
            assert saved is not None
            assert saved.api_key_id is None, (row["rule_name"], saved.api_key_id)


class TestSourceDataRows:

    async def test_source_table_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/source-tables",
            headers=ADMIN,
            json={"table_name": f"超管源表 {uuid.uuid4().hex[:6]}"},
        )
        assert resp.status_code == 201, resp.text
        table = await db_session.get(SourceTable, resp.json()["id"])
        assert table is not None
        assert table.api_key_id is None, table.api_key_id

    async def test_dictionary_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/source-dictionaries",
            headers=ADMIN,
            json={"dict_name": f"超管字典 {uuid.uuid4().hex[:6]}"},
        )
        assert resp.status_code == 201, resp.text
        d = await db_session.get(StandardDictionary, resp.json()["id"])
        assert d is not None
        assert d.api_key_id is None, d.api_key_id


class TestStandardRows:

    async def test_created_standard_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/data-standards-v2",
            headers=ADMIN,
            json={"name": f"超管标准 {uuid.uuid4().hex[:6]}",
                  "code": f"ADM-{uuid.uuid4().hex[:8]}"},
        )
        assert resp.status_code == 201, resp.text
        std = await db_session.get(DataStandard, resp.json()["id"])
        assert std is not None
        assert std.api_key_id is None, std.api_key_id

    async def test_published_version_has_null_owner(self, tolerant_client, db_session):
        std = await _admin_standard(db_session)
        await db_session.commit()

        resp = await tolerant_client.post(
            f"/api/v1/data-standards-v2/{std.id}/publish",
            headers=ADMIN,
            json={"change_log": "超管发布"},
        )
        assert resp.status_code == 201, resp.text
        version = await db_session.get(StandardVersion, resp.json()["id"])
        assert version is not None
        assert version.api_key_id is None, version.api_key_id


class TestNoVirtualKeyIdWrites:
    """静态兜底：`app/` 下不得再出现 `api_key.id` / `auth.id` 引用。

    `AuthContext.id` 直接委托 `api_key.id`，超管下同样返回虚拟 ID
    `00000000-...-0001`，写进带外键的列会 500。唯一允许出现这两者的文件是
    `app/core/auth_context.py` —— 它负责产出 `id` 与 `owner_id`。

    扫描方式是 **AST**（见 `_violations`）：匹配的是「属性名 `id` + 基名 `auth` /
    `api_key`」的节点，不是文本子串。只有 `ast.parse` 失败时才退回
    tokenize 屏蔽 + 子串兜底（宁可误报，不可漏报）。
    """

    # AST 解析失败时的兜底子串（宁可误报，不可漏报）
    _FORBIDDEN_SUBSTRINGS = ("api_key.id", "auth.id")
    # 属性基名（**大小写敏感**）：`auth.id` / `api_key.id` 是请求级实例。
    # 不取小写匹配，因为 `ApiKey.id` 是 ORM 列表达式（app 里真实出现 4 次，合法），
    # `Auth.id` 是类引用——二者都不可能是「当前请求的虚拟 ID」。
    _FORBIDDEN_BASE_NAMES = frozenset({"auth", "api_key"})
    _EXEMPT_FILES = {"app/core/auth_context.py"}
    # 扫描面下限：`app/` 当前有 126 个 .py。若路径算错导致 rglob 扫空，
    # 守卫会静默变绿；用下限把「空扫描」变成显式失败。
    _MIN_SCANNED_FILES = 100
    # 需要屏蔽的 token：字符串、注释、以及 f-string 的字面量片段。
    _MASKED_TOKENS = frozenset(
        {tokenize.STRING, tokenize.COMMENT}
        | {
            getattr(tokenize, name)
            for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
            if hasattr(tokenize, name)
        }
    )

    @staticmethod
    def _mask_strings_and_comments(text: str) -> list[str]:
        """把字符串与注释替换成空格，保留行号与列位置。

        仅在 `ast.parse` 失败时作为兜底：文档/注释里提到 `auth.id` 不算违规，
        而真正写在表达式里的仍保留原样，可被子串匹配抓到。
        """
        lines = text.splitlines()
        chars = [list(line) for line in lines]
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type not in TestNoVirtualKeyIdWrites._MASKED_TOKENS:
                    continue
                (start_row, start_col), (end_row, end_col) = tok.start, tok.end
                for row in range(start_row, end_row + 1):
                    idx = row - 1
                    if idx < 0 or idx >= len(chars):
                        continue
                    begin = start_col if row == start_row else 0
                    finish = end_col if row == end_row else len(chars[idx])
                    for col in range(begin, min(finish, len(chars[idx]))):
                        chars[idx][col] = " "
        except (tokenize.TokenError, IndentationError, SyntaxError):
            # 文件不是合法 Python 时退回「不屏蔽」——宁可误报，不可漏报。
            pass
        return ["".join(row) for row in chars]

    @staticmethod
    def _dotted_name(node: ast.AST) -> str:
        """把属性链还原成 `a.b.c`；含调用/下标/字面量等无法静态确定的一律返回 ''。"""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return ""
        parts.append(node.id)
        return ".".join(reversed(parts))

    @classmethod
    def _violations(cls, tree: ast.AST) -> list[tuple[int, str]]:
        """在 AST 上找违规引用，返回 [(行号, 表达式)]。

        用 AST 而不是子串，正是为了修掉子串匹配的假阳性/假阴性：
        `oauth.id` 不是 `auth.id`（Name 相等而非包含）、`auth . id` 的空格、
        跨行属性链、`getattr(auth, "id")`，以及 `x = auth.id` 这种「中转」——
        读点本身即违规，与它后面流向哪里无关。
        """
        hits: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "id":
                base = cls._dotted_name(node.value)
                if base and base.split(".")[-1] in cls._FORBIDDEN_BASE_NAMES:
                    hits.append((node.lineno, cls._dotted_name(node)))
            elif isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Name)
                    and func.id == "getattr"
                    and len(node.args) >= 2
                ):
                    target = cls._dotted_name(node.args[0])
                    attr = node.args[1]
                    if (
                        target
                        and target.split(".")[-1] in cls._FORBIDDEN_BASE_NAMES
                        and isinstance(attr, ast.Constant)
                        and attr.value == "id"
                    ):
                        hits.append((node.lineno, f'getattr({target}, "id")'))
        return hits

    @staticmethod
    def _render(rel: str, lineno: int, raw_lines: list[str]) -> str:
        raw = raw_lines[lineno - 1] if lineno <= len(raw_lines) else ""
        return f"{rel}:{lineno}: {raw.strip()}"

    @classmethod
    def _scan(cls, root: Path) -> tuple[list[str], int]:
        """扫描 `root/app` 下的 .py，返回 (违规行列表, 实际扫描文件数)。"""
        offenders: list[str] = []
        scanned = 0
        for path in sorted((root / "app").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in cls._EXEMPT_FILES:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8")
            raw_lines = text.splitlines()
            try:
                tree = ast.parse(text)
            except SyntaxError:
                # 解析失败时信息不足，退回 tokenize 屏蔽 + 子串（宁可误报，不可漏报）
                for no, code in enumerate(cls._mask_strings_and_comments(text), 1):
                    if any(token in code for token in cls._FORBIDDEN_SUBSTRINGS):
                        offenders.append(cls._render(rel, no, raw_lines))
                continue
            seen: set[int] = set()
            for lineno, _expr in cls._violations(tree):
                if lineno in seen:  # 同一行多处只报一次
                    continue
                seen.add(lineno)
                offenders.append(cls._render(rel, lineno, raw_lines))
        return offenders, scanned

    @classmethod
    def _check(cls, root: Path) -> list[str]:
        """运行扫描并校验扫描面下限，返回违规行列表。"""
        offenders, scanned = cls._scan(root)
        assert scanned >= cls._MIN_SCANNED_FILES, (
            f"只扫到 {scanned} 个文件（下限 {cls._MIN_SCANNED_FILES}）："
            "路径算错或 app/ 不在预期位置，守卫会静默空转。"
        )
        return offenders

    def test_app_has_no_virtual_key_id_references(self):
        offenders = self._check(Path(__file__).resolve().parents[1])
        assert offenders == [], (
            "写归属用 auth.owner_id（超管落 NULL），读过滤用 auth.scope_clause：\n"
            + "\n".join(offenders)
        )

    def test_exemption_surface_is_single_file(self):
        assert TestNoVirtualKeyIdWrites._EXEMPT_FILES == {"app/core/auth_context.py"}

    def test_docstring_and_comment_mentions_are_not_offenders(self, tmp_path):
        app = tmp_path / "app" / "api"
        app.mkdir(parents=True)
        (app / "demo.py").write_text(
            '"""模块文档：不要写 api_key_id=auth.id，超管下会外键 500。"""\n'
            "# 注释里提到 auth.id 也不算违规\n"
            "X = 1\n",
            encoding="utf-8",
        )
        offenders, scanned = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert scanned == 1
        assert offenders == []

    def test_real_code_is_still_caught(self, tmp_path):
        app = tmp_path / "app" / "api"
        app.mkdir(parents=True)
        (app / "demo.py").write_text("X = auth.id\nY = api_key.id\n", encoding="utf-8")
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert offenders == [
            "app/api/demo.py:1: X = auth.id",
            "app/api/demo.py:2: Y = api_key.id",
        ]

    def test_expression_inside_fstring_is_caught(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "demo.py").write_text('Z = f"owner={auth.id}"\n', encoding="utf-8")
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert offenders == ['app/demo.py:1: Z = f"owner={auth.id}"']

    def test_oauth_id_is_not_an_offender(self, tmp_path):
        """`oauth.id` 曾是子串匹配的假阳性；AST 按 Name 相等比较后不再误报。"""
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "demo.py").write_text(
            "T = oauth.id\nU = my_auth.id\nV = token.api_key_id\n", encoding="utf-8"
        )
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert offenders == []

    def test_orm_column_and_class_refs_are_not_offenders(self, tmp_path):
        """`ApiKey.id` 是 ORM 列表达式、`Auth.id` 是类引用，都不是「当前请求的虚拟 ID」。

        真实回归依据：改造前的首版实现取小写匹配，在 `app/` 里误报 4 处
        `ApiKey.id`（`admin.py:105/166/236`、`deps.py:59`）——这类列表达式必须放行。
        """
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "demo.py").write_text(
            "W = select(ApiKey).where(ApiKey.id == key_id)\nX = Auth.id\n",
            encoding="utf-8",
        )
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert offenders == []

    def test_space_variant_is_caught(self, tmp_path):
        """`auth . id`（空格）等价于 `auth.id`，子串匹配曾漏。"""
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "demo.py").write_text("A = auth . id\nB = api_key .id\n", encoding="utf-8")
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert offenders == [
            "app/demo.py:1: A = auth . id",
            "app/demo.py:2: B = api_key .id",
        ]

    def test_cross_line_and_nested_chains_are_caught(self, tmp_path):
        """跨行属性链与 `self.auth.id` 嵌套链同样命中。"""
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "demo.py").write_text(
            "C = (\n    auth\n    .id\n)\nD = self.auth.id\n", encoding="utf-8"
        )
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert len(offenders) == 2
        assert all(o.startswith("app/demo.py:") for o in offenders)

    def test_getattr_variant_is_caught(self, tmp_path):
        """`getattr(auth, "id")` 绕过属性语法，AST 单独识别。"""
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "demo.py").write_text(
            'E = getattr(auth, "id")\nF = getattr(oauth, "id")\n', encoding="utf-8"
        )
        offenders, _ = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert offenders == ['app/demo.py:1: E = getattr(auth, "id")']

    def test_unparseable_file_falls_back_to_substring_scan(self, tmp_path):
        """解析失败的文件不能被静默跳过：退回子串扫描，宁可误报。"""
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "broken.py").write_text("X = auth.id\nG = (\n", encoding="utf-8")
        offenders, scanned = TestNoVirtualKeyIdWrites._scan(tmp_path)
        assert scanned == 1
        assert offenders == ["app/broken.py:1: X = auth.id"]

    def test_empty_scan_is_a_failure_not_a_pass(self, tmp_path):
        (tmp_path / "app").mkdir()
        with pytest.raises(AssertionError, match="只扫到 0 个文件"):
            TestNoVirtualKeyIdWrites._check(tmp_path)

    def test_real_app_surface_meets_lower_bound(self):
        root = Path(__file__).resolve().parents[1]
        _offenders, scanned = TestNoVirtualKeyIdWrites._scan(root)
        assert scanned >= TestNoVirtualKeyIdWrites._MIN_SCANNED_FILES
