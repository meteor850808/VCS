# VCS 生产部署 Makefile（Linux 目标机）
COMPOSE      = docker compose
COMPOSE_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml

# Docker 推送参数（可通过环境变量覆盖）
REGISTRY  ?= docker.io
NAMESPACE ?=
TAG       ?= 2.24.0
PLATFORM  ?=
LATEST    ?= 0
SKIP_PUSH ?= 0

.PHONY: help secrets certs build up down logs ps prod-up prod-down migrate restart clean push push-latest
help:
	@echo "secrets certs build up down logs ps prod-up prod-down migrate restart clean push push-latest"

secrets:      ; sh docker/scripts/gen_secrets.sh
certs:        ; sh docker/scripts/gen_certs.sh
build:        ; $(COMPOSE) build
up:           ; $(COMPOSE) up -d
down:         ; $(COMPOSE) down
logs:         ; $(COMPOSE) logs -f --tail=200
ps:           ; $(COMPOSE) ps
prod-up:      ; $(COMPOSE_PROD) up -d --build
prod-down:    ; $(COMPOSE_PROD) down
migrate:      ; $(COMPOSE) run --rm migrate
restart:      ; $(COMPOSE) restart api worker beat
clean:        ; $(COMPOSE_PROD) down -v

# 推送镜像到仓库（必需：NAMESPACE=xxx）
push:         ; REGISTRY="$(REGISTRY)" NAMESPACE="$(NAMESPACE)" TAG="$(TAG)" PLATFORM="$(PLATFORM)" SKIP_PUSH="$(SKIP_PUSH)" LATEST="0" sh docker/scripts/build_push.sh

# 推送镜像并额外打推 latest
push-latest:  ; REGISTRY="$(REGISTRY)" NAMESPACE="$(NAMESPACE)" TAG="$(TAG)" PLATFORM="$(PLATFORM)" SKIP_PUSH="$(SKIP_PUSH)" LATEST="1" sh docker/scripts/build_push.sh
