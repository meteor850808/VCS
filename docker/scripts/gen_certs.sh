#!/bin/sh
# 生成自签证书（内网单机，无域名）。用法: sh docker/scripts/gen_certs.sh [目录] [CN]
set -eu
DIR="${1:-certs}"
CN="${2:-vcs.local}"
mkdir -p "$DIR"
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$DIR/vcs.key" -out "$DIR/vcs.crt" \
    -subj "/C=CN/O=VCS/CN=${CN}" \
    -addext "subjectAltName=DNS:${CN},DNS:localhost,IP:127.0.0.1"
chmod 600 "$DIR/vcs.key" 2>/dev/null || true
echo "已生成 $DIR/vcs.crt 与 $DIR/vcs.key（CN=${CN}）"
