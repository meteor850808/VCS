#!/bin/sh
# 通用入口：派生 DB 连接串 → 装载 secrets → 建数据目录 → exec 实际命令。
# 设计：非敏感项走 .env；敏感项优先 /run/secrets/*（存在才覆盖，保证本地/CI 无 secrets 仍可跑）。
set -eu

_read_secret() {
    # $1=secret 文件名  $2=目标 env 名；文件存在则以文件内容覆盖
    if [ -f "/run/secrets/$1" ]; then
        _v="$(cat "/run/secrets/$1")"
        export "$2=$_v"
    fi
}

# ── secrets → env ────────────────────────────────────────────────
_read_secret postgres_password POSTGRES_PASSWORD
_read_secret admin_init_key     ADMIN_INIT_KEY
_read_secret flower_basic_auth  FLOWER_BASIC_AUTH

# ── 由 POSTGRES_* 派生 DATABASE_URL（单一真源）─────────────────────
# 说明：容器内 POSTGRES_HOST 恒有值；此时**始终**派生以覆盖 env_file(.env) 注入的
# 本地连接串（.env 的 DATABASE_URL 指向 localhost，容器内不可用）。
if [ -n "${POSTGRES_HOST:-}" ]; then
    : "${POSTGRES_PORT:=5432}"
    : "${POSTGRES_USER:=postgres}"
    : "${POSTGRES_DB:=vcs_db}"
    : "${POSTGRES_PASSWORD:=postgres}"
    export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
    export DATABASE_URL_SYNC="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

# ── 数据目录（命名卷首挂为空，兜底建目录）─────────────────────────
if [ -d /data ]; then
    mkdir -p "${UPLOAD_DIR:-/data/uploads}" \
             "${OUTPUT_DIR:-/data/outputs}" \
             "${QUALITY_REPORT_DIR:-/data/quality_reports}" \
             "$(dirname "${LOG_FILE:-/data/logs/vcs.log}")" 2>/dev/null || true
fi

exec "$@"
