# syntax=docker/dockerfile:1.7

# ── LibreDWG 构建阶段（Debian bookworm 无 libredwg-utils 包，须源码构建）──
FROM debian:bookworm-slim AS libredwg-builder
ARG LIBREDWG_VERSION=0.13.3
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl pkg-config libpcre2-dev python3 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/libredwg.tar.gz \
        "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.gz" \
    && mkdir -p /src && tar -xzf /tmp/libredwg.tar.gz -C /src --strip-components=1 \
    && cd /src && ./configure \
        --disable-bindings --disable-python --disable-docs --disable-dependency-tracking \
    && make -j"$(nproc)" \
    && make install DESTDIR=/out

# ── 运行时基础阶段 ────────────────────────────────────────────────────────────
FROM python:3.11.11-slim AS base

# GDAL 运行时 + ODBC/MDB + LibreDWG 运行依赖 + 构建工具（构建后移除）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin libgdal-dev libgeos-dev libproj-dev libpq-dev \
        unixodbc unixodbc-dev mdbtools odbc-mdbtools libpcre2-8-0 \
        gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

ENV CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal \
    PYTHONUNBUFFERED=1

# LibreDWG 运行时（dwg2dxf + libredwg.so）
COPY --from=libredwg-builder /out/usr/local/ /usr/local/
RUN ldconfig

# MDBTools ODBC 驱动注册（pyodbc 经 odbcinst.ini 解析 DRIVER={MDBTools}）
# 路径动态探测，兼容 amd64 / arm64（/usr/lib/<triplet>/odbc/libmdbodbc.so）
RUN MDB_LIB="$(dpkg -L odbc-mdbtools | grep -m1 '/libmdbodbc\.so$')" \
    && test -n "$MDB_LIB" \
    && printf '[MDBTools]\nDescription=MDB Tools ODBC\nDriver=%s\n' "$MDB_LIB" > /etc/odbcinst.ini \
    && (dwg2dxf --version 2>/dev/null || true)

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y gcc g++ libgdal-dev unixodbc-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p /data/uploads /data/outputs /data/quality_reports /data/logs \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app /data \
    && chmod +x /app/docker/entrypoint.sh \
    && cp /app/docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER appuser
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# ── API 服务入口 ───────────────────────────────────────────────────────────────
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]

# ── Celery Worker 入口 ────────────────────────────────────────────────────────
FROM base AS worker
CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=4", "-Q", "vector_conversion,celery"]

# ── Celery Beat 入口 ─────────────────────────────────────────────────────────
FROM base AS beat
CMD ["celery", "-A", "app.workers.celery_app.celery_app", "beat", "--loglevel=info"]

# ── Flower 监控（shell 形式以展开 $FLOWER_BASIC_AUTH）──────────────────────────
FROM base AS flower
EXPOSE 5555
CMD ["sh", "-c", "celery -A app.workers.celery_app.celery_app flower --port=5555 --broker_api=redis://redis:6379/0 --basic-auth=$FLOWER_BASIC_AUTH"]
