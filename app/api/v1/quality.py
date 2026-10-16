"""数据质量检查 API v2 — 五维评分 + 标准驱动。

端点:
  POST /quality/check             对已存储文件执行质量检查（新 v2 接口）
  POST /quality/check/upload      上传文件并执行质量检查（旧兼容接口，保留）
  GET  /quality                   查询质量报告列表
  GET  /quality/{report_id}       查询报告详情
  GET  /quality/{report_id}/html  HTML 报告
  GET  /quality/{report_id}/json  JSON 报告
  GET  /quality/{report_id}/export 导出违规 GeoJSON/Excel
"""

import json
import os
import tempfile
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

import logging

logger = logging.getLogger(__name__)

from app.config import settings
from app.core.deps import authenticated
from app.core.file_utils import validate_filename
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.quality_report import QualityReport
from app.models.stored_file import StoredFile
from app.schemas.quality import (
    QualityCheckRequest,
    QualityCheckResponse,
    QualityReportBrief,
    QualityReportDetail,
    CheckItemResult,
    TopoCheckItemResult,
    CheckSummary,
    DimensionScores,
    ViolationSample,
)
from app.services.quality_checker import run_quality_check, check_quality
from app.services.quality_checker import WEIGHTS  # 五维权重唯一权威来源
from app.services.quality_checker import generate_html_report, generate_json_report  # noqa: F401
from app.services.topology_checker import TopologyConfig
from app.services.quality_exporter import export_violations_geojson, export_violations_excel

router = APIRouter()


# ── POST /quality/check ──────────────────────────────────────────────────────

# ── 权重辅助（v2.20 收敛：前端/HTML 不再硬编码权重）──────────────────────────

def _overall_from_scores(
    completeness: float, accuracy: float,
    consistency: float, uniqueness: float, topology: float,
) -> float:
    """按 quality_checker.WEIGHTS（唯一权威）计算综合分。"""
    return round(
        completeness * WEIGHTS["completeness"]
        + accuracy * WEIGHTS["accuracy"]
        + consistency * WEIGHTS["consistency"]
        + uniqueness * WEIGHTS["uniqueness"]
        + topology * WEIGHTS["topology"],
        4,
    )


# ── 质检执行 ────────────────────────────────────────────────────────────────

@router.post(
    "/quality/check",
    response_model=QualityCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="对已存储文件执行质量检查（v2 五维评分）",
)
async def quality_check_stored_file(
    file_id: str = Query(..., description="已存储文件 ID"),
    body: QualityCheckRequest | None = None,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """
    对已入库文件执行完整的属性结构 + 拓扑检查，返回五维评分。

    支持两种传参方式：
      1. JSON body: `POST /quality/check?file_id=xxx` + `{"standard_table_id": "..."}`
      2. Form: 通过 `/quality/check/upload` 上传新文件
    """
    # 验证文件权限
    from sqlalchemy import select, func
    from app.models.spatial_feature import PointFeature, LineFeature, PolygonFeature
    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")

    # 立即缓存前端需要的文件字段：run_quality_check 运行期间 AsyncSession 会过期
    # sf 的属性，延后访问 sf.original_name 会触发同步懒加载 → MissingGreenlet（异步会话禁止同步 IO）
    file_name = sf.original_name

    # 前置校验：无图形要素的文件直接拒绝，避免生成空报告并显示满分
    feature_count = sf.feature_count or 0
    if feature_count <= 0:
        # 【S4】区分「真无要素 / 计数不一致」：先查 spatial 分表实际要素数
        actual = 0
        for model in (PointFeature, LineFeature, PolygonFeature):
            cnt = (await db.execute(
                select(func.count()).select_from(model).where(
                    model.stored_file_id == file_id,
                    model.api_key_id == api_key.id,
                )
            )).scalar() or 0
            actual += cnt
        if actual > 0:
            # 分表确有要素但 feature_count=0（历史数据/计数异常）→ 放行，以实际要素质检
            logger.info("quality/check: feature_count=0 但分表有 %d 要素，放行 file_id=%s", actual, file_id)
            feature_count = actual
        else:
            reindex_hint = (
                " 可尝试重新索引（POST /api/v1/data/{id}/reindex）恢复要素，"
                "或重新上传数据。"
                if sf.file_data else " 原始文件数据已清理，请重新上传数据。"
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "该文件无图形要素（feature_count=0），不能执行质检。"
                    "若上传文件包含要素但显示为 0，说明解析失败；"
                    + reindex_hint
                ),
            )

    req = body or QualityCheckRequest()

    # 从文件记录继承标准信息（如果请求未指定）
    standard_id = req.standard_id or sf.standard_id
    standard_table_id = req.standard_table_id or sf.standard_table_id
    geometry_type = req.geometry_type

    # 解析拓扑配置
    topology_config = None
    if req.topology_config:
        topology_config = TopologyConfig(
            enabled_rules=req.topology_config.get("enabled_rules", TopologyConfig().enabled_rules),
            tolerances=req.topology_config.get("tolerances", TopologyConfig().tolerances),
        )

    if not standard_id:
        # 未关联标准：仅执行拓扑检查（属性/结构检查跳过）
        logger.info("quality/check: 未关联标准，仅执行拓扑检查 file_id=%s", file_id)

    # 立即缓存 api_key_id：run_quality_check 运行期间 topology_checker 可能
    # 触发 db.rollback()，导致 session 内所有 ORM 对象过期 → MissingGreenlet
    api_key_id = api_key.id

    # 执行质检
    check_result = await run_quality_check(
        db=db,
        standard_id=standard_id,
        stored_file_id=file_id,
        api_key_id=api_key_id,
        geometry_type=geometry_type,
        topology_config=topology_config,
        standard_table_id=standard_table_id,
    )

    # 生成报告文件
    report_id = str(uuid.uuid4())
    report_dir = Path(settings.quality_report_dir) / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = str(report_dir / "report.html")
    json_path = str(report_dir / "report.json")

    # 生成简要 JSON 报告（供前端下载）
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(check_result, f, ensure_ascii=False, indent=2, default=str)

    # 写入数据库
    scores = check_result["scores"]
    report = QualityReport(
        id=report_id,
        api_key_id=api_key_id,
        stored_file_id=file_id,
        file_name=file_name,
        detected_format="stored_file",
        total_features=feature_count,
        total_fields=0,
        completeness_score=scores["completeness"],
        # 旧字段保持兼容
        missing_fields=None,
        null_value_counts=None,
        critical_fields_status=None,
        issues=None,
        constraint_violations=check_result["attr_results"],
        topology_violations=check_result["topo_results"],
        data_standard_id=standard_id,
        standard_table_id=standard_table_id,
        # 新字段
        attribute_summary=check_result["attr_summary"],
        topology_summary=check_result["topo_summary"],
        score_completeness=scores["completeness"],
        score_accuracy=scores["accuracy"],
        score_consistency=scores["consistency"],
        score_uniqueness=scores["uniqueness"],
        score_topology=scores["topology"],
        score_overall=scores["overall"],
        html_report_path=html_path,
        json_report_path=json_path,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    # 构建响应
    return QualityCheckResponse(
        report_id=report.id,
        stored_file_id=file_id,
        file_name=file_name,
        standard_id=standard_id,
        standard_table_id=standard_table_id,
        attr_results=[CheckItemResult(**r) for r in check_result["attr_results"]],
        attr_summary=CheckSummary(**check_result["attr_summary"]),
        topo_results=[TopoCheckItemResult(**r) for r in check_result["topo_results"]],
        topo_summary=CheckSummary(**check_result["topo_summary"]),
        scores=DimensionScores(**scores),
        total_violations=check_result["total_violations"],
        violation_samples=[ViolationSample(**s) for s in check_result["violation_samples"]],
        checked_layers=report.checked_layers,
        created_at=report.created_at,
    )


# ── POST /quality/check/upload（旧兼容接口）─────────────────────────────────

@router.post(
    "/quality/check/upload",
    response_model=QualityReportDetail,
    status_code=status.HTTP_200_OK,
    summary="上传文件并执行质量检查（旧接口兼容）",
)
async def quality_check_upload(
    file: UploadFile = File(..., description="待检查的矢量文件"),
    data_standard_id: str | None = Form(None, description="数据标准 ID（可选）"),
    standard_table_id: str | None = Form(None, description="标准表 ID（可选）"),
    geometry_type: str | None = Form(None, description="几何类型（可选）"),
    topology_config: str | None = Form(None, description="拓扑检查配置 JSON（可选）"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """上传文件并执行质量检查，兼容旧的 5 参数签名。"""
    report_id = str(uuid.uuid4())
    upload_dir = Path(settings.upload_dir) / "quality" / report_id
    if not file.filename:  # pragma: no cover - multipart 无 filename 的 part 由 FastAPI 校验拦截（422）先行
        raise HTTPException(status_code=400, detail="文件名为空")
    validate_filename(file.filename)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(upload_dir / file.filename)

    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    # 立即缓存 api_key_id：run_quality_check 运行期间 topology_checker 可能
    # 触发 db.rollback()，导致 session 内所有 ORM 对象过期 → MissingGreenlet
    api_key_id = api_key.id

    # 优先使用新 orchestrator（如果指定了标准）
    if data_standard_id:
        # 先写一个临时 StoredFile 记录（给 run_quality_check 用）
        tmp_sf_id = str(uuid.uuid4())
        tmp_sf = StoredFile(
            id=tmp_sf_id,
            api_key_id=api_key.id,
            original_name=file.filename,
            layer_name=Path(file.filename).stem,
            content_type=file.content_type or "application/octet-stream",
            file_size=len(content),
            file_data=content,
            standard_id=data_standard_id,
            standard_table_id=standard_table_id,
        )
        db.add(tmp_sf)
        await db.flush()

        try:
            parsed_topology = None
            if topology_config:
                tc = json.loads(topology_config)
                parsed_topology = TopologyConfig(
                    enabled_rules=tc.get("enabled_rules", TopologyConfig().enabled_rules),
                    tolerances=tc.get("tolerances", TopologyConfig().tolerances),
                )

            check_result = await run_quality_check(
                db=db,
                standard_id=data_standard_id,
                stored_file_id=tmp_sf_id,
                api_key_id=api_key_id,
                geometry_type=geometry_type,
                topology_config=parsed_topology,
            )

            scores = check_result["scores"]
            report = QualityReport(
                id=report_id,
                api_key_id=api_key_id,
                file_name=file.filename,
                detected_format="upload",
                total_features=check_result["attr_summary"].get("total", 0),
                total_fields=0,
                completeness_score=scores["completeness"],
                constraint_violations=check_result["attr_results"],
                topology_violations=check_result["topo_results"],
                data_standard_id=data_standard_id,
                standard_table_id=standard_table_id,
                attribute_summary=check_result["attr_summary"],
                topology_summary=check_result["topo_summary"],
                score_completeness=scores["completeness"],
                score_accuracy=scores["accuracy"],
                score_consistency=scores["consistency"],
                score_uniqueness=scores["uniqueness"],
                score_topology=scores["topology"],
                score_overall=scores["overall"],
            )
            db.add(report)
            await db.commit()
            await db.refresh(report)

            return QualityReportDetail(
                id=report.id,
                file_name=report.file_name,
                detected_format=report.detected_format,
                total_features=report.total_features,
                total_fields=report.total_fields,
                completeness_score=report.completeness_score,
                constraint_violations=report.constraint_violations,
                topology_violations=report.topology_violations,
                data_standard_id=report.data_standard_id,
                standard_table_id=report.standard_table_id,
                attribute_summary=report.attribute_summary,
                topology_summary=report.topology_summary,
                score_completeness=report.score_completeness,
                score_accuracy=report.score_accuracy,
                score_consistency=report.score_consistency,
                score_uniqueness=report.score_uniqueness,
                score_topology=report.score_topology,
                score_overall=report.score_overall,
                checked_layers=report.checked_layers,
                created_at=report.created_at,
            )

        except Exception as e:
            # 回退到旧接口
            import logging
            logging.getLogger(__name__).warning("新质检接口失败，回退旧接口: %s", e)

    # 回退：旧 check_quality 同步接口
    from app.core.db_utils import load_standard_fields
    standard_fields = None
    if data_standard_id:
        standard_fields = await load_standard_fields(db, data_standard_id, api_key.id)
        if standard_fields is None:
            raise HTTPException(status_code=404, detail="数据标准不存在")

    parsed_topology = None
    if topology_config:
        try:
            parsed_topology = json.loads(topology_config)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="topology_config 不是有效的 JSON")

    result = check_quality(
        file_path,
        data_standard_id=data_standard_id,
        standard_fields=standard_fields,
        topology_config=parsed_topology,
    )

    report_dir = Path(settings.quality_report_dir) / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = str(report_dir / "report.html")
    json_path = str(report_dir / "report.json")
    generate_html_report(result, html_path)
    generate_json_report(result, json_path)

    report = QualityReport(
        id=report_id,
        api_key_id=api_key.id,
        file_name=file.filename,
        detected_format=result.detected_format,
        total_features=result.total_features,
        total_fields=result.total_fields,
        completeness_score=result.completeness_score,
        missing_fields=result.missing_fields,
        null_value_counts=result.null_value_counts,
        critical_fields_status=result.critical_fields_status,
        issues=[
            {"severity": i.severity, "field": i.field, "issue_type": i.issue_type,
             "message": i.message, "affected_count": i.affected_count}
            for i in (result.issues or [])
        ],
        constraint_violations=result.constraint_violations or None,
        topology_violations=result.topology_violations or None,
        data_standard_id=result.data_standard_id,
        html_report_path=html_path,
        json_report_path=json_path,
    )
    db.add(report)
    await db.commit()

    return QualityReportDetail(
        id=report.id,
        file_name=report.file_name,
        detected_format=report.detected_format,
        total_features=report.total_features,
        total_fields=report.total_fields,
        completeness_score=report.completeness_score,
        missing_fields=report.missing_fields,
        null_value_counts=report.null_value_counts,
        critical_fields_status=report.critical_fields_status,
        issues=report.issues,
        constraint_violations=report.constraint_violations,
        topology_violations=report.topology_violations,
        data_standard_id=report.data_standard_id,
        html_report_path=report.html_report_path,
        json_report_path=report.json_report_path,
        checked_layers=report.checked_layers,
        created_at=report.created_at,
        score_overall=report.score_overall or 0.0,  # 旧接口无五维分数列
    )


# ── GET /quality/{report_id} ─────────────────────────────────────────────────

@router.get("/quality/{report_id}", response_model=QualityReportDetail, summary="查询质量报告详情")
async def get_quality_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(QualityReport).where(
            QualityReport.id == report_id,
            api_key.scope_clause(QualityReport),
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在或无权限访问")

    # 【S5】从已存 JSONB 推导违规样本 / 严重度分布 / 违规总数（不落新列，兼容存量报告）
    attr_list = report.constraint_violations or []
    topo_list = report.topology_violations or []

    violation_samples: list[dict] = []
    for r in attr_list:
        if not isinstance(r, dict) or r.get("status") != "fail":
            continue
        samples = r.get("samples") or []
        for s in samples[:5]:
            violation_samples.append({
                "rule_code": r.get("rule_code", ""),
                "rule_name": r.get("rule_name", ""),
                "message": r.get("message", ""),
                "layer": r.get("layer"),
                "field": r.get("field") or r.get("field_name"),
                # 属性样本语义：int=feature_index，其它为样本值
                "feature_index": s if isinstance(s, int) else None,
                "sample": s if not isinstance(s, int) else None,
            })
    for r in topo_list:
        if not isinstance(r, dict) or r.get("status") != "fail":
            continue
        for idx in (r.get("feature_indices") or [])[:5]:
            violation_samples.append({
                "rule_code": r.get("rule_code", ""),
                "rule_name": r.get("rule_name", ""),
                "message": r.get("message", ""),
                "layer": None,
                "field": None,
                "feature_index": idx,
                "sample": None,
            })
    violation_samples = violation_samples[:100]

    total_violations = sum(
        r.get("failed_count", 0) for r in attr_list
        if isinstance(r, dict) and r.get("status") == "fail"
    ) + sum(
        r.get("failed_count", 0) for r in topo_list
        if isinstance(r, dict) and r.get("status") == "fail"
    )

    severity_dist: dict[str, int] = {"error": 0, "warning": 0}
    for r in list(attr_list) + list(topo_list):
        if isinstance(r, dict) and r.get("status") == "fail":
            sev = r.get("severity", "warning")
            severity_dist[sev] = severity_dist.get(sev, 0) + 1

    return QualityReportDetail(
        id=report.id,
        job_id=report.job_id,
        batch_job_id=report.batch_job_id,
        file_name=report.file_name,
        detected_format=report.detected_format,
        total_features=report.total_features,
        total_fields=report.total_fields,
        completeness_score=report.completeness_score,
        missing_fields=report.missing_fields,
        null_value_counts=report.null_value_counts,
        critical_fields_status=report.critical_fields_status,
        issues=report.issues,
        constraint_violations=report.constraint_violations,
        topology_violations=report.topology_violations,
        data_standard_id=report.data_standard_id,
        standard_table_id=report.standard_table_id,
        stored_file_id=report.stored_file_id,
        attribute_summary=report.attribute_summary,
        topology_summary=report.topology_summary,
        score_completeness=report.score_completeness or 0.0,
        score_accuracy=report.score_accuracy or 0.0,
        score_consistency=report.score_consistency or 0.0,
        score_uniqueness=report.score_uniqueness or 0.0,
        score_topology=report.score_topology or 0.0,
        html_report_path=report.html_report_path,
        json_report_path=report.json_report_path,
        checked_layers=report.checked_layers,
        created_at=report.created_at,
        attr_results=[CheckItemResult(**r) for r in attr_list if isinstance(r, dict)],
        topo_results=[TopoCheckItemResult(**r) for r in topo_list if isinstance(r, dict)],
        total_violations=total_violations,
        violation_samples=[ViolationSample(**s) for s in violation_samples],
        severity_dist=severity_dist,
        score_overall=_overall_from_scores(
            report.score_completeness or 0.0,
            report.score_accuracy or 0.0,
            report.score_consistency or 0.0,
            report.score_uniqueness or 0.0,
            report.score_topology or 0.0,
        ) if report.score_overall is None else round(report.score_overall, 4),
    )


@router.get("/quality/{report_id}/html", response_class=HTMLResponse, summary="查看 HTML 质量报告")
async def get_quality_report_html(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(QualityReport).where(
            QualityReport.id == report_id,
            api_key.scope_clause(QualityReport),
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    # 从数据库数据即时生成 HTML 报告（不依赖磁盘文件）
    overall = _overall_from_scores(
        report.score_completeness or 0,
        report.score_accuracy or 0,
        report.score_consistency or 0,
        report.score_uniqueness or 0,
        report.score_topology or 0,
    ) * 100

    def _sc(v):
        if v >= 85: return "#27ae60"
        if v >= 60: return "#f39c12"
        return "#e74c3c"

    # 【S5】SVG 环形仪表（内联，零 CDN）
    def _donut(pct: float, color: str, size: int = 84) -> str:
        r = (size - 12) / 2
        c = 2 * 3.14159265 * r
        filled = max(0, min(100, pct)) / 100 * c
        return (
            f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" style="transform:rotate(-90deg)">'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="#eef1f5" stroke-width="9"/>'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{color}" stroke-width="9" '
            f'stroke-linecap="round" stroke-dasharray="{filled:.1f} {c:.1f}"/>'
            f'<text x="{size/2}" y="{size/2+5}" fill="{color}" font-size="{size*0.24}" font-weight="700" '
            f'text-anchor="middle" transform="rotate(90 {size/2} {size/2})">{pct:.0f}%</text></svg>'
        )

    def _dim_card(key: str, label: str, val: float) -> str:
        pct = val * 100
        color = _sc(pct)
        return (
            f'<div class="dim"><div>{_donut(pct, color)}</div>'
            f'<div class="dim-lbl">{label}</div>'
            f'<div class="dim-val" style="color:{color}">{pct:.0f}%</div></div>'
        )

    # 【S5】严重度分布
    attr_list = report.constraint_violations or []
    topo_list = report.topology_violations or []
    n_error = sum(1 for r in list(attr_list) + list(topo_list)
                  if isinstance(r, dict) and r.get("status") == "fail" and r.get("severity") == "error")
    n_warning = sum(1 for r in list(attr_list) + list(topo_list)
                    if isinstance(r, dict) and r.get("status") == "fail" and r.get("severity") == "warning")
    total_failed = n_error + n_warning
    sev_html = ""
    if total_failed:
        pct_e = n_error / total_failed * 100
        pct_w = n_warning / total_failed * 100
        sev_html = f"""
        <div class="sev"><div style="color:#e74c3c;font-weight:700;">{n_error}</div><div class="sev-lbl">错误</div></div>
        <div class="sev"><div style="color:#f39c12;font-weight:700;">{n_warning}</div><div class="sev-lbl">警告</div></div>
        <div class="sev-bar"><div style="width:{pct_e:.1f}%;background:#e74c3c;" title="错误 {n_error}"></div><div style="width:{pct_w:.1f}%;background:#f39c12;" title="警告 {n_warning}"></div></div>"""
    else:
        sev_html = '<div class="sev-ok">✔ 全部检查通过，无违规</div>'

    # 【S5】违规样本明细（feature_index / 值）
    sample_rows = ""
    n_samples = 0
    for r in list(attr_list) + list(topo_list):
        if not isinstance(r, dict) or r.get("status") != "fail":
            continue
        samples = r.get("samples") or []
        indices = (r.get("feature_indices") or [])[:5] if r.get("rule_code", "").startswith("32") else []
        for s in samples[:5]:
            if isinstance(s, int):
                sample_cell = f'<span class="idx">要素 #{s}</span>'
            else:
                sample_cell = f'<span class="val">{str(s)[:60]}</span>'
            sample_rows += f"<tr><td>{r.get('rule_code','')}</td><td>{r.get('rule_name','')}</td><td>{r.get('layer','') or '—'}</td><td>{r.get('field') or r.get('field_name','') or '—'}</td><td>{sample_cell}</td></tr>"
            n_samples += 1
        for idx in indices:
            sample_rows += f"<tr><td>{r.get('rule_code','')}</td><td>{r.get('rule_name','')}</td><td>{r.get('layer','') or '—'}</td><td>—</td><td><span class='idx'>要素 #{idx}</span></td></tr>"
            n_samples += 1

    # 属性检查行
    attr_rows = ""
    for cv in (report.constraint_violations or []):
        if isinstance(cv, dict):
            status_cls = "#27ae60" if cv.get("status") == "pass" else "#e74c3c"
            status_txt = "通过" if cv.get("status") == "pass" else "不通过"
            attr_rows += f"""<tr>
                <td>{cv.get('rule_code','')}</td><td>{cv.get('rule_name','')}</td>
                <td style="color:{status_cls};font-weight:600;">{status_txt}</td>
                <td>{cv.get('total_checked',0)}</td><td>{cv.get('failed_count',0)}</td>
                <td>{cv.get('message','')}</td></tr>"""

    # 拓扑检查行
    topo_rows = ""
    for tv in (report.topology_violations or []):
        if isinstance(tv, dict):
            status_cls = "#27ae60" if tv.get("status") == "pass" else "#e74c3c"
            status_txt = "通过" if tv.get("status") == "pass" else "不通过"
            topo_rows += f"""<tr>
                <td>{tv.get('rule_code','')}</td><td>{tv.get('rule_name','')}</td>
                <td style="color:{status_cls};font-weight:600;">{status_txt}</td>
                <td>{tv.get('total_checked',0)}</td><td>{tv.get('failed_count',0)}</td>
                <td>{tv.get('message','')}</td></tr>"""

    # 属性摘要
    attr_sum = report.attribute_summary or {}
    topo_sum = report.topology_summary or {}

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>质量报告 - {report.file_name}</title>
<style>
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; margin: 0; background: #f0f2f5; color: #2c3e50; }}
.container {{ max-width: 980px; margin: 20px auto; background: #fff; border-radius: 10px; padding: 28px 32px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
h1 {{ font-size: 20px; border-bottom: 2px solid #eee; padding-bottom: 12px; margin-top: 0; }}
h3 {{ color: #34495e; margin: 24px 0 4px; font-size: 15px; }}
.meta {{ font-size: 13px; color: #666; margin-bottom: 18px; }}
.scores {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin: 16px 0 8px; }}
.dim {{ background: #f8f9fa; border-radius: 10px; padding: 12px 6px; text-align: center; }}
.dim-lbl {{ font-size: 11px; color: #7f8c8d; margin-top: 4px; }}
.dim-val {{ font-size: 15px; font-weight: 700; margin-top: 2px; }}
.dim:first-child {{ background: #f1f5ff; }}
.sev-box {{ display: flex; align-items: center; gap: 18px; background: #fdf6ec; border-radius: 8px; padding: 12px 16px; margin: 8px 0 4px; }}
.sev {{ text-align: center; min-width: 56px; }}
.sev-lbl {{ font-size: 11px; color: #7f8c8d; }}
.sev-bar {{ flex: 1; display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #eef1f5; }}
.sev-ok {{ color: #27ae60; font-size: 14px; font-weight: 600; background: #eefaf2; border-radius: 8px; padding: 12px 16px; margin: 8px 0 4px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
th, td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid #eee; font-size: 12.5px; }}
th {{ background: #f8f9fa; font-weight: 600; }}
.idx {{ background: #fdecea; color: #c0392b; border-radius: 4px; padding: 1px 6px; font-size: 11.5px; font-weight: 600; }}
.val {{ background: #fef9e7; color: #b7950b; border-radius: 4px; padding: 1px 6px; font-size: 11.5px; }}
@media print {{ body {{ background: #fff; }} .container {{ box-shadow: none; margin: 0; max-width: 100%; padding: 12px; }} }}
</style></head><body><div class="container">
<h1>📊 质量检查报告</h1>
<div class="meta">文件: {report.file_name} ｜ 要素数: {report.total_features} ｜ 违规总数: {total_failed} ｜ 生成时间: {str(report.created_at)[:19]}</div>
<div class="scores">
  <div class="dim"><div>{_donut(overall, _sc(overall))}</div><div class="dim-lbl">综合评分</div><div class="dim-val" style="color:{_sc(overall)}">{overall:.0f}</div></div>
  {_dim_card('completeness','完整性', report.score_completeness or 0)}
  {_dim_card('accuracy','准确性', report.score_accuracy or 0)}
  {_dim_card('consistency','一致性', report.score_consistency or 0)}
  {_dim_card('uniqueness','唯一性', report.score_uniqueness or 0)}
  {_dim_card('topology','拓扑', report.score_topology or 0)}
</div>
<h3>严重度分布</h3>
<div class="sev-box">{sev_html}</div>
<h3>属性检查 ({attr_sum.get('passed',0)}/{attr_sum.get('total',0)} 通过)</h3>
<table><thead><tr><th>规则</th><th>名称</th><th>状态</th><th>检查数</th><th>失败数</th><th>说明</th></tr></thead>
<tbody>{attr_rows or '<tr><td colspan="6" style="text-align:center;color:#999;">无属性检查数据</td></tr>'}</tbody></table>
<h3>拓扑检查 ({topo_sum.get('passed',0)}/{topo_sum.get('total',0)} 通过)</h3>
<table><thead><tr><th>规则</th><th>名称</th><th>状态</th><th>检查数</th><th>失败数</th><th>说明</th></tr></thead>
<tbody>{topo_rows or '<tr><td colspan="6" style="text-align:center;color:#999;">无拓扑检查数据</td></tr>'}</tbody></table>
<h3>违规样本明细（{n_samples} 条）</h3>
<table><thead><tr><th>规则</th><th>名称</th><th>图层</th><th>字段</th><th>样本（要素索引 / 值）</th></tr></thead>
<tbody>{sample_rows or '<tr><td colspan="5" style="text-align:center;color:#999;">无违规样本</td></tr>'}</tbody></table>
</div></body></html>"""

    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/quality/{report_id}/json", summary="下载 JSON 质量报告")
async def get_quality_report_json(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(QualityReport).where(
            QualityReport.id == report_id,
            api_key.scope_clause(QualityReport),
        )
    )
    report = result.scalar_one_or_none()
    if not report or not report.json_report_path:
        raise HTTPException(status_code=404, detail="JSON 报告不存在")
    return FileResponse(report.json_report_path, media_type="application/json", filename="quality_report.json")


# ── GET /quality（列表）─────────────────────────────────────────────────────

@router.get("/quality", response_model=list[QualityReportBrief], summary="查询质量报告列表")
async def list_quality_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),  # 【S4】放宽上限（原 50），质检报告列表常用大分页
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    query = (
        select(QualityReport)
        .where(api_key.scope_clause(QualityReport))
        .order_by(QualityReport.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    reports = result.scalars().all()
    return [
        QualityReportBrief(
            id=r.id,
            file_name=r.file_name,
            detected_format=r.detected_format,
            completeness_score=r.completeness_score,
            total_features=r.total_features,
            total_fields=r.total_fields,
            issues_count=len(r.constraint_violations or []) + len(r.topology_violations or []),
            created_at=r.created_at,
            # v2.5 五维评分
            score_completeness=r.score_completeness or 0.0,
            score_accuracy=r.score_accuracy or 0.0,
            score_consistency=r.score_consistency or 0.0,
            score_uniqueness=r.score_uniqueness or 0.0,
            score_topology=r.score_topology or 0.0,
            # 违规明细
            constraint_violations=r.constraint_violations,
            topology_violations=r.topology_violations,
            topology_violations_count=len(r.topology_violations or []),
            constraint_violations_count=len(r.constraint_violations or []),
            # 【v2.10.5】圈定图层
            checked_layers=r.checked_layers,
            # 关联
            stored_file_id=r.stored_file_id,
            data_standard_id=r.data_standard_id,
        )
        for r in reports
    ]


# ── GET /quality/{report_id}/export ──────────────────────────────────────────

@router.get("/quality/{report_id}/export", summary="导出违规要素（GeoJSON/Excel）")
async def export_quality_report(
    report_id: str,
    format: str = Query("geojson", pattern="^(geojson|excel)$"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """导出质检报告中的违规记录为 GeoJSON 或 Excel。"""
    result = await db.execute(
        select(QualityReport).where(
            QualityReport.id == report_id,
            api_key.scope_clause(QualityReport),
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    if format == "geojson":
        content = await export_violations_geojson(report, db=db)
        safe_name = report.file_name.replace(".", "_") + "_violations.geojson"
        # RFC 5987: filename* 使用 UTF-8 percent-encoding 避免 latin-1 编码失败
        from urllib.parse import quote
        encoded_name = quote(safe_name)
        return Response(
            content=content,
            media_type="application/geo+json",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        )
    else:
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        export_violations_excel(report, tmp_path)
        from urllib.parse import quote
        safe_xlsx = report.file_name + "_violations.xlsx"
        encoded_xlsx = quote(safe_xlsx)
        return FileResponse(
            tmp_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=encoded_xlsx,
        )


# ── DELETE /quality/{report_id} ───────────────────────────────────────────────

@router.delete("/quality/{report_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除单条质检报告")
async def delete_quality_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """删除指定质检报告。"""
    result = await db.execute(
        select(QualityReport).where(
            QualityReport.id == report_id,
            api_key.scope_clause(QualityReport),
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    await db.delete(report)
    await db.flush()


# ── POST /quality/batch-delete ────────────────────────────────────────────────

@router.post("/quality/batch-delete", status_code=status.HTTP_204_NO_CONTENT, summary="批量删除质检报告")
async def batch_delete_quality_reports(
    report_ids: list[str] = Form(...),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """批量删除质检报告。"""
    if not report_ids:  # pragma: no cover - FastAPI Form(list[str]) 必填，空列表无法通过解析
        return
    result = await db.execute(
        select(QualityReport).where(
            QualityReport.id.in_(report_ids),
            api_key.scope_clause(QualityReport),
        )
    )
    reports = result.scalars().all()
    for r in reports:
        await db.delete(r)
    await db.flush()


# ── POST /quality/delete-all ──────────────────────────────────────────────────

@router.post("/quality/delete-all", status_code=status.HTTP_204_NO_CONTENT, summary="删除全部质检报告")
async def delete_all_quality_reports(
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """删除当前 API Key 有权限的所有质检报告。"""
    await db.execute(
        sa_delete(QualityReport).where(api_key.scope_clause(QualityReport))
    )
    await db.flush()
