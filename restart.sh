#!/bin/bash
# VCS One Click Start
PORT=45679
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
PYTHON="$VENV_DIR/Scripts/python"
PIP="$VENV_DIR/Scripts/pip"
UVICORN="$VENV_DIR/Scripts/uvicorn"

if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == CYGWIN* ]]; then
    PYTHON="$VENV_DIR/Scripts/python.exe"
    PIP="$VENV_DIR/Scripts/pip.exe"
    UVICORN="$VENV_DIR/Scripts/uvicorn.exe"
fi

echo ""
echo "=== VCS One Click Start ==="
echo ""

echo "[1/6] Checking Python venv..."
if [ ! -f "$PYTHON" ]; then
    echo "      Creating venv..."
    python -m venv "$VENV_DIR"
fi
echo "      OK"

echo "[2/6] Checking dependencies..."
"$PYTHON" -c "import fastapi" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "      Installing dependencies..."
    "$PIP" install -r "$APP_DIR/requirements.txt" -q
fi
echo "      OK"

echo "[3/6] Stopping old processes on ports 45678, 45679, 45680..."
pkill -f "app.main:app" 2>/dev/null
for P in 45678 45679 45680; do
  lsof -ti:$P 2>/dev/null | xargs kill -9 2>/dev/null || true
done
sleep 2
echo "      OK"

echo "[4/6] Checking directories..."
mkdir -p "$APP_DIR/data/uploads" "$APP_DIR/data/outputs" "$APP_DIR/data/quality_reports"
echo "      OK"

echo "[5/6] Running migration..."
cd "$APP_DIR"
ALEMBIC_EXE="$VENV_DIR/Scripts/alembic"
if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == CYGWIN* ]]; then
    ALEMBIC_EXE="$VENV_DIR/Scripts/alembic.exe"
fi
if [ -f "$ALEMBIC_EXE" ]; then
    "$ALEMBIC_EXE" upgrade head 2>/dev/null || true
else
    "$PYTHON" -m alembic upgrade head 2>/dev/null || true
fi
echo "      OK"

echo "[6/6] Starting service on port $PORT..."
nohup "$UVICORN" app.main:app --host 127.0.0.1 --port $PORT > "$APP_DIR/server_out.log" 2>&1 &
echo "      PID: $!"
sleep 5

if curl -s http://127.0.0.1:$PORT/health > /dev/null 2>&1; then
    echo ""
    echo "  SUCCESS - http://127.0.0.1:$PORT/app/"
    echo ""
    cmd.exe /c start http://127.0.0.1:$PORT/app/ 2>/dev/null || true
else
    echo "  FAILED - check: cat $APP_DIR/server_out.log"
fi
