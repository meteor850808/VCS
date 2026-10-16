#!/bin/sh
# 构建并推送 VCS 全部镜像到镜像仓库（Docker Hub / Harbor 等）
# 用法：
#   REGISTRY=docker.io NAMESPACE=meteor850808 TAG=2.24.0 sh docker/scripts/build_push.sh
# Docker Hub 登录（推荐用 PAT，避免账号密码）：
#   echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin
#
# 环境变量：
#   REGISTRY    镜像仓库，默认 docker.io
#   NAMESPACE   命名空间/用户名（必填）
#   TAG         镜像标签，默认 2.24.0
#   PLATFORM    目标平台，如 linux/amd64（可选）
#   LATEST      设为 1 时额外打推 latest
#   SKIP_PUSH   设为 1 时只构建不推送
set -eu

REGISTRY="${REGISTRY:-docker.io}"
NAMESPACE="${NAMESPACE:-}"
TAG="${TAG:-2.24.0}"
PLATFORM="${PLATFORM:-}"
LATEST="${LATEST:-0}"
SKIP_PUSH="${SKIP_PUSH:-0}"

if [ -z "$NAMESPACE" ]; then
    echo "错误：必须设置 NAMESPACE（示例：NAMESPACE=meteor850808）" >&2
    exit 1
fi

# 定位项目根（脚本位于 <root>/docker/scripts/）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

PF=""
if [ -n "$PLATFORM" ]; then
    PF="--platform $PLATFORM"
fi

APPS="api worker beat flower"
NGINX_IMG="vcs-nginx"

echo "==> registry=$REGISTRY namespace=$NAMESPACE tag=$TAG platform=${PLATFORM:-native} skip_push=$SKIP_PUSH latest=$LATEST"

for t in $APPS; do
    IMG="$REGISTRY/$NAMESPACE/vcs-$t:$TAG"
    echo "==> build $IMG (target=$t)"
    docker build $PF --target "$t" -t "$IMG" .

    if [ "$LATEST" = "1" ]; then
        LATEST_IMG="$REGISTRY/$NAMESPACE/vcs-$t:latest"
        docker tag "$IMG" "$LATEST_IMG"
    fi

    if [ "$SKIP_PUSH" != "1" ]; then
        echo "==> push  $IMG"
        docker push "$IMG"
        if [ "$LATEST" = "1" ]; then
            docker push "$LATEST_IMG"
        fi
    fi
done

# 构建并推送 nginx 镜像
IMG="$REGISTRY/$NAMESPACE/$NGINX_IMG:$TAG"
echo "==> build $IMG (docker/nginx/Dockerfile)"
docker build $PF -f docker/nginx/Dockerfile -t "$IMG" .

if [ "$LATEST" = "1" ]; then
    LATEST_IMG="$REGISTRY/$NAMESPACE/$NGINX_IMG:latest"
    docker tag "$IMG" "$LATEST_IMG"
fi

if [ "$SKIP_PUSH" != "1" ]; then
    echo "==> push  $IMG"
    docker push "$IMG"
    if [ "$LATEST" = "1" ]; then
        docker push "$LATEST_IMG"
    fi
fi

echo "完成。"
