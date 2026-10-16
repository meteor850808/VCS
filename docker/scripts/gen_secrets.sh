#!/bin/sh
# 生成生产 secrets（幂等：已存在则跳过）。用法: sh docker/scripts/gen_secrets.sh [目录]
set -eu
DIR="${1:-secrets}"
mkdir -p "$DIR"

_gen() {  # $1=文件名
    f="$DIR/$1"
    if [ -f "$f" ]; then echo "skip   $f (已存在)"; return; fi
    openssl rand -base64 48 | tr -d '\n' > "$f"
    echo "create $f"
}

_gen postgres_password.txt
_gen admin_init_key.txt

# flower_basic_auth 必须为 user:password 形式（Flower --basic-auth 要求）
f="$DIR/flower_basic_auth.txt"
if [ -f "$f" ]; then
    echo "skip   $f (已存在)"
else
    printf 'admin:%s' "$(openssl rand -base64 24 | tr -d '\n')" > "$f"
    echo "create $f (user=admin，如需可自行编辑)"
fi
chmod 600 "$DIR"/*.txt 2>/dev/null || true
echo "完成。默认 Flower 用户为 admin（密码见 $DIR/flower_basic_auth.txt）。"
