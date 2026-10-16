#!/bin/sh
# 生成 registry:2 basic auth 的 htpasswd（需要 apache2-utils 的 htpasswd；也可用 docker httpd:2）
# 用法：sh docker/registry/gen_htpasswd.sh [目录] [用户名] [密码（可选，留空自动生成）]
set -eu

DIR="${1:-$(dirname "$0")}"
USER="${2:-vcs}"
PASS="${3:-}"

mkdir -p "$DIR/auth"

if [ -z "$PASS" ]; then
    PASS="$(openssl rand -base64 18 | tr -d '\n')"
fi

# 优先用本机 htpasswd，否则用 httpd 镜像
if command -v htpasswd >/dev/null 2>&1; then
    htpasswd -Bbn "$USER" "$PASS" > "$DIR/auth/htpasswd"
else
    docker run --rm --entrypoint htpasswd httpd:2 -Bbn "$USER" "$PASS" > "$DIR/auth/htpasswd"
fi

chmod 600 "$DIR/auth/htpasswd" 2>/dev/null || true

echo "已生成 $DIR/auth/htpasswd"
echo "用户名：$USER"
echo "密码：  $PASS"
echo "注意：请同步修改 docker/registry/docker-compose.yml 启用 REGISTRY_AUTH 并挂载 ./auth:/auth:ro"
