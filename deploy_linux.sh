#!/usr/bin/env bash
# ============================================================
#  VCS 矢量转换服务 — Linux 一键部署 / 重启
#  功能：检查/安装 Redis + PostgreSQL + GDAL → venv → 依赖
#        → 迁移 → 启动 uvicorn(API) + celery worker + flower
#  用法：sudo bash deploy_linux.sh
# ============================================================
set -u

PORT=45679
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
UVICORN="$VENV_DIR/bin/uvicorn"
CELERY="$VENV_DIR/bin/celery"
FLOWER="$VENV_DIR/bin/flower"
ALEMBIC="$VENV_DIR/bin/alembic"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}OK${NC} - $1"; }
warn() { echo -e "  ${YELLOW}WARN${NC} - $1"; }
err()  { echo -e "  ${RED}ERROR${NC} - $1"; }

echo ""
echo "========================================"
  echo "  VCS v2.23.0 One-Click Deploy (Linux)"
echo "  Code : $APP_DIR/app"
echo "  Port : $PORT"
echo "========================================"
echo ""

# 需要 root 才能 apt/redis-server/systemctl
if [ "$(id -u)" -ne 0 ]; then
  warn "非 root 运行：安装依赖/系统服务管理将被跳过（请用 sudo 重新执行以启用全部功能）"
fi

# ── Step 1: Redis ───────────────────────────────────────────
echo "[1/7] Checking Redis..."
if command -v redis-server >/dev/null 2>&1; then
  ok "redis-server $(redis-server --version | awk '{print $3}')"
else
  warn "Redis 未安装，尝试 apt 安装..."
  if [ "$(id -u)" -eq 0 ]; then
    apt-get update -qq && apt-get install -y -qq redis-server || err "redis 安装失败"
  else
    err "请手动安装: sudo apt-get install redis-server"
  fi
fi

# 确保 Redis 运行
if ! redis-cli ping >/dev/null 2>&1; then
  warn "Redis 未运行，尝试启动..."
  if command -v systemctl >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    systemctl start redis-server 2>/dev/null || redis-server --daemonize yes
  else
    redis-server --daemonize yes 2>/dev/null || err "Redis 启动失败，请手动启动"
  fi
  sleep 1
fi
redis-cli ping >/dev/null 2>&1 && ok "Redis running: $(redis-cli ping)" || warn "Redis 状态未知"

# ── Step 2: PostgreSQL ──────────────────────────────────────
echo "[2/7] Checking PostgreSQL..."
if command -v psql >/dev/null 2>&1; then
  ok "psql $(psql --version | awk '{print $3}')"
else
  warn "PostgreSQL 未安装，尝试 apt 安装（含 PostGIS）..."
  if [ "$(id -u)" -eq 0 ]; then
    apt-get install -y -qq postgresql postgresql-contrib postgis || err "postgres 安装失败"
  else
    err "请手动安装: sudo apt-get install postgresql postgresql-contrib postgis"
  fi
fi

# ── Step 3: GDAL（可选，ogr2ogr 转换需要）──────────────────
echo "[3/7] Checking GDAL (optional)..."
if command -v ogr2ogr >/dev/null 2>&1; then
  ok "ogr2ogr $(ogr2ogr --version)"
else
  warn "ogr2ogr 未安装（可选，Fiona 脚本仍可用）"
  if [ "$(id -u)" -eq 0 ]; then
    apt-get install -y -qq gdal-bin || warn "gdal-bin 安装失败"
  fi
fi

# ── Step 4: venv ────────────────────────────────────────────
echo "[4/7] Checking Python venv..."
if [ ! -f "$PYTHON" ]; then
  ok "创建 venv..."
  python3 -m venv "$VENV_DIR" || { err "venv 创建失败（需 python3-venv）"; exit 1; }
fi
ok "venv ready"

# ── Step 5: 依赖 ────────────────────────────────────────────
echo "[5/7] Checking dependencies..."
if ! "$PYTHON" -c "import fastapi" >/dev/null 2>&1; then
  ok "安装依赖..."
  "$PIP" install -r "$APP_DIR/requirements.txt" -q || { err "依赖安装失败"; exit 1; }
fi
ok "dependencies ready"

# ── Step 6: 停止旧进程 + 数据目录 + 迁移 ───────────────────
echo "[6/7] Stopping old processes..."
pkill -f "app.main:app" 2>/dev/null
pkill -f "celery.*worker" 2>/dev/null
pkill -f "flower" 2>/dev/null
for P in 45678 45679 45680 5555; do
  fuser -k "$P/tcp" 2>/dev/null
done
sleep 2

echo "      Preparing directories..."
mkdir -p "$APP_DIR/data/uploads" "$APP_DIR/data/outputs" "$APP_DIR/data/quality_reports"

echo "      Running migration..."
cd "$APP_DIR" || exit 1
if [ -f "$ALEMBIC" ]; then
  "$ALEMBIC" upgrade head 2>/dev/null || { "$ALEMBIC" stamp head 2>/dev/null; }
else
  "$PYTHON" -m alembic upgrade head 2>/dev/null || { "$PYTHON" -m alembic stamp head 2>/dev/null; }
fi
ok "migration done"

# ── Step 7: 启动服务 ────────────────────────────────────────
echo "[7/7] Starting services on port $PORT..."
cd "$APP_DIR" || exit 1

nohup "$UVICORN" app.main:app --host 127.0.0.1 --port "$PORT" \
  > "$APP_DIR/server_out.log" 2>&1 &
API_PID=$!

nohup "$CELERY" -A app.workers.celery_app worker --loglevel=info --pool=solo \
  > "$APP_DIR/worker_out.log" 2>&1 &
WORKER_PID=$!

# Flower（Celery 监控，5555）—— flower 2.x 注册为 celery 子命令（无独立 flower 命令）
if ! "$PYTHON" -c "import flower" >/dev/null 2>&1; then
  ok "安装 flower..."
  "$PIP" install flower -q || warn "flower 安装失败"
fi
nohup "$PYTHON" -m celery -A app.workers.celery_app flower --port=5555 \
  > "$APP_DIR/flower_out.log" 2>&1 &
FLOWER_PID=$!

echo "      PIDs: api=$API_PID worker=$WORKER_PID${FLOWER_PID:+ flower=$FLOWER_PID}"
sleep 6

# ── 验证 ────────────────────────────────────────────────────
if curl -s -m 5 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo ""
  echo "========================================"
  echo "  SUCCESS - Service is running!"
  echo "  Frontend : http://127.0.0.1:$PORT/app/"
  echo "  API Docs : http://127.0.0.1:$PORT/docs"
  echo "  Flower   : http://127.0.0.1:5555"
  echo "========================================"
  curl -s -m 5 "http://127.0.0.1:$PORT/health"; echo ""
else
  echo ""
  echo "  FAILED - check:"
  echo "    tail -50 $APP_DIR/server_out.log"
  echo "    tail -50 $APP_DIR/server_err.log"
fi
