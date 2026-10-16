"""API Key / Group 管理接口。

所有接口需要 X-Admin-Init-Key 验证。
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.security import generate_api_key
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.key_group import KeyGroup
from app.schemas.admin import (
    ApiKeyBrief,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    KeyGroupBrief,
    KeyGroupCreateRequest,
    KeyGroupDetail,
    KeyGroupUpdateRequest,
    SetKeyGroupRequest,
)

router = APIRouter()


# ── Helper ──────────────────────────────────────────────────────────────────

async def _verify_admin(admin_init_key: str = Header(None, alias="X-Admin-Init-Key")):
    if not admin_init_key or admin_init_key != settings.admin_init_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无效的管理员密钥")
    return admin_init_key


def _enrich_key_brief(key: ApiKey) -> ApiKeyBrief:
    """将 ApiKey ORM 对象转为带 group_name 的 Brief。"""
    return ApiKeyBrief(
        id=key.id,
        name=key.name,
        description=key.description,
        is_active=key.is_active,
        created_at=key.created_at,
        group_id=key.group_id,
        group_name=key.group.name if key.group else None,
    )


# ── API Key CRUD ────────────────────────────────────────────────────────────

@router.post("/admin/apikeys", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    # 如果指定了 group_id，验证 group 存在
    if body.group_id:
        group = await db.get(KeyGroup, body.group_id)
        if not group:
            raise HTTPException(status_code=404, detail="分组不存在")

    raw_key, key_hash = generate_api_key()
    key = ApiKey(
        key_hash=key_hash,
        name=body.name,
        description=body.description,
        group_id=body.group_id,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    return ApiKeyCreateResponse(
        id=key.id,
        name=key.name,
        api_key=raw_key,
        message="请保管好此密钥，仅显示一次",
    )


@router.get("/admin/apikeys", response_model=list[ApiKeyBrief])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """列出所有 API Key（含 group 信息）。"""
    result = await db.execute(
        select(ApiKey)
        .options(selectinload(ApiKey.group))
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [_enrich_key_brief(k) for k in keys]


@router.delete("/admin/apikeys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """吊销 API Key（软删除，is_active=False）。"""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    key.is_active = False
    await db.commit()


@router.put("/admin/apikeys/{key_id}/activate", status_code=status.HTTP_200_OK)
async def activate_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """重新启用已吊销的 API Key（is_active=True）。"""
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    key.is_active = True
    await db.commit()
    return {"ok": True, "key_id": key_id, "is_active": True}


@router.post(
    "/admin/apikeys/{key_id}/regenerate",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_200_OK,
)
async def regenerate_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """重新生成 API Key：旧密钥立即失效，新密钥仅展示一次。

    服务端只保存哈希、无法二次查看明文，
    此接口用于「密钥遗忘」场景下的安全替换。
    """
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    raw_key, key_hash = generate_api_key()
    key.key_hash = key_hash
    key.is_active = True
    await db.commit()
    return ApiKeyCreateResponse(
        id=key.id,
        name=key.name,
        api_key=raw_key,
        message="密钥已重新生成，旧密钥立即失效，请保管好新密钥（仅显示一次）",
    )


@router.delete("/admin/apikeys/{key_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """永久删除 API Key（从数据库彻底移除，不可恢复）。"""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    await db.delete(key)
    await db.commit()


# ── Key-Group Assignment ────────────────────────────────────────────────────

@router.put("/admin/apikeys/{key_id}/group", status_code=status.HTTP_200_OK)
async def set_key_group(
    key_id: str,
    body: SetKeyGroupRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """设置 API Key 所属分组（body.group_id=null 则移出分组）。"""
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    if body.group_id:
        group = await db.get(KeyGroup, body.group_id)
        if not group:
            raise HTTPException(status_code=404, detail="分组不存在")
        key.group_id = body.group_id
    else:
        key.group_id = None

    await db.commit()
    return {"ok": True, "key_id": key_id, "group_id": key.group_id}


# ── Group CRUD ──────────────────────────────────────────────────────────────

@router.post("/admin/groups", response_model=KeyGroupDetail, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: KeyGroupCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    # 检查名称唯一性
    existing = await db.execute(
        select(KeyGroup).where(KeyGroup.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="分组名称已存在")

    group = KeyGroup(name=body.name, description=body.description)
    db.add(group)
    await db.commit()
    await db.refresh(group, ["api_keys"])
    return KeyGroupDetail(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        api_keys=[_enrich_key_brief(k) for k in (group.api_keys or [])],
    )


@router.get("/admin/groups", response_model=list[KeyGroupBrief])
async def list_groups(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    """列出所有分组（含成员数）。"""
    # 使用子查询统计成员数
    subq = (
        select(ApiKey.group_id, func.count(ApiKey.id).label("cnt"))
        .where(ApiKey.is_active == True)
        .group_by(ApiKey.group_id)
        .subquery()
    )
    result = await db.execute(
        select(KeyGroup, subq.c.cnt)
        .outerjoin(subq, KeyGroup.id == subq.c.group_id)
        .order_by(KeyGroup.created_at.desc())
    )
    rows = result.all()
    return [
        KeyGroupBrief(
            id=row.KeyGroup.id,
            name=row.KeyGroup.name,
            description=row.KeyGroup.description,
            member_count=row.cnt or 0,
            created_at=row.KeyGroup.created_at,
        )
        for row in rows
    ]


@router.get("/admin/groups/{group_id}", response_model=KeyGroupDetail)
async def get_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    group = await db.get(KeyGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    # 加载成员
    result = await db.execute(
        select(ApiKey)
        .options(selectinload(ApiKey.group))
        .where(ApiKey.group_id == group_id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return KeyGroupDetail(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        api_keys=[_enrich_key_brief(k) for k in keys],
    )


@router.put("/admin/groups/{group_id}", response_model=KeyGroupDetail)
async def update_group(
    group_id: str,
    body: KeyGroupUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    group = await db.get(KeyGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")

    if body.name is not None:
        # 检查名称唯一性（排除自身）
        existing = await db.execute(
            select(KeyGroup).where(KeyGroup.name == body.name, KeyGroup.id != group_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="分组名称已存在")
        group.name = body.name
    if body.description is not None:
        group.description = body.description

    await db.commit()
    await db.refresh(group, ["api_keys"])
    return KeyGroupDetail(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        api_keys=[_enrich_key_brief(k) for k in (group.api_keys or [])],
    )


@router.delete("/admin/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_verify_admin),
):
    group = await db.get(KeyGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")

    # 将组内 Key 的 group_id 置空（外键 ON DELETE SET NULL）
    await db.execute(
        ApiKey.__table__.update().where(ApiKey.group_id == group_id).values(group_id=None)
    )
    await db.delete(group)
    await db.commit()
