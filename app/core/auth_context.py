"""AuthContext — 注入到路由的认证上下文，封装数据访问范围。

支持 Group 隔离：
- 无 group 的 Key → 只能看自己上传的数据（兼容现状）
- 有 group 的 Key → 能看到同组所有 Key 的数据

使用方式（向后兼容）：
    auth: AuthContext = Depends(authenticated)

    # 创建记录时标记归属（用当前 key 的 id）
    StoredFile(api_key_id=auth.id, ...)

    # 查询时做范围过滤（兼容 group 共享）
    select(StoredFile).where(auth.scope_clause(StoredFile))

    # 权属检查
    if not sf or not auth.owns(sf):
        raise HTTPException(404)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, true

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.api_key import ApiKey


class AuthContext:
    """认证上下文。

    兼容旧有 `api_key.id` / `api_key.name` 的直接访问模式（通过 __getattr__ 委托）。
    """

    def __init__(
        self,
        api_key: ApiKey,
        accessible_key_ids: list[str],
        is_super_admin: bool = False,
    ):
        self.api_key = api_key
        self._accessible_key_ids = accessible_key_ids
        self.is_super_admin = is_super_admin

    # ── 核心属性 ──────────────────────────────────────────────────────────

    @property
    def accessible_key_ids(self) -> list[str]:
        """当前 Key 可访问数据所属的 api_key_id 列表。

        - 无 group: [self.id]（仅自己）
        - 有 group: 同组所有活跃 Key 的 ID 列表
        """
        return self._accessible_key_ids

    @property
    def id(self) -> str:
        """当前认证的 Key ID（记录归属用）。

        注意：超级管理员为虚拟 ID（不存在于 api_keys 表），
        不可直接写入带外键的列。
        """
        return self.api_key.id

    @property
    def owner_id(self) -> str | None:
        """可安全写入 api_key_id 外键列的值。

        - 超级管理员：虚拟 ID 无对应 DB 行，返回 None（避免外键冲突）
        - 普通 Key：返回真实 ID
        """
        if self.is_super_admin:
            return None
        return self.api_key.id

    @property
    def group_id(self) -> str | None:
        """当前 Key 所属分组 ID。"""
        return self.api_key.group_id

    # ── 委托到 api_key（兼容 api_key.name / api_key.is_active 等属性访问） ──

    def __getattr__(self, name: str):
        """将未识别的属性委托到 ApiKey 对象。"""
        return getattr(self.api_key, name)

    # ── 辅助方法 ──────────────────────────────────────────────────────────

    def scope_clause(self, model):
        """生成数据范围过滤条件。

        用法: select(Model).where(auth.scope_clause(Model))
        等价于: select(Model).where(Model.api_key_id.in_([id1, id2, ...]))
        超级管理员: 返回 SQLAlchemy true()（不过滤，可访问所有数据，含其自身虚拟 ID 创建的数据）
        """
        if self.is_super_admin:
            return true()
        return model.api_key_id.in_(self._accessible_key_ids)

    def owns(self, record) -> bool:
        """判断当前用户是否有权访问某条记录。"""
        if record is None:
            return False
        if self.is_super_admin:
            return True
        return record.api_key_id in self._accessible_key_ids

    # ── 工厂方法 ──────────────────────────────────────────────────────────

    @classmethod
    async def resolve(
        cls, db: AsyncSession, api_key: ApiKey
    ) -> AuthContext:
        """从 ApiKey 解析出完整的 AuthContext（含可访问 ID 列表）。"""
        if api_key.group_id:
            result = await db.execute(
                select(type(api_key).id).where(
                    type(api_key).group_id == api_key.group_id,
                    type(api_key).is_active == True,
                )
            )
            accessible = [row[0] for row in result.all()]
        else:
            accessible = [api_key.id]
        return cls(api_key=api_key, accessible_key_ids=accessible)

    @classmethod
    async def create_super_admin(
        cls, db: AsyncSession
    ) -> AuthContext:
        """创建超级管理员上下文——可访问所有活跃 Key 的数据。

        当 X-API-Key 的值匹配 settings.admin_init_key 时使用。
        """
        from app.models.api_key import ApiKey
        result = await db.execute(
            select(ApiKey.id).where(ApiKey.is_active == True)
        )
        all_key_ids = [row[0] for row in result.all()]

        # 使用一个固定的虚拟 ID 标识管理员操作
        admin_api_key = ApiKey(
            id="00000000-0000-0000-0000-000000000001",
            key_hash="",
            name="__admin__",
            description="超级管理员（通过 ADMIN_INIT_KEY 鉴权）",
            is_active=True,
        )
        return cls(
            api_key=admin_api_key,
            accessible_key_ids=all_key_ids,
            is_super_admin=True,
        )
