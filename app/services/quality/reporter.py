"""质量报告生成器 — HTML / JSON 报告。"""

import json
import os
from datetime import datetime, timezone

from app.services.quality.models import QualityResult


def _score_color(score: float) -> str:
    if score >= 0.8:
        return "#27ae60"
    elif score >= 0.5:
        return "#f39c12"
    return "#e74c3c"


def generate_html_report(result: QualityResult, output_path: str) -> str:
    """生成 HTML 质量报告。"""
    score_color = _score_color(result.completeness_score)
    issues_html = ""
    for issue in (result.issues or []):
        sev_color = {"critical": "#e74c3c", "warning": "#f39c12", "info": "#3498db"}.get(issue.severity, "#666")
        issues_html += f"""
        <tr>
            <td><span style="color:{sev_color}">●</span> {issue.severity}</td>
            <td>{issue.field}</td>
            <td>{issue.issue_type}</td>
            <td>{issue.message}</td>
            <td>{issue.affected_count}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>质量报告 - {result.file_name}</title>
<style>
    body {{ font-family: 'Segoe UI', sans-serif; margin: 20px; background: #f5f6fa; }}
    .container {{ max-width: 960px; margin: auto; background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
    .score {{ font-size: 48px; font-weight: bold; color: {score_color}; text-align: center; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
    th {{ background: #f8f9fa; font-weight: 600; }}
    tr:hover {{ background: #f1f1f1; }}
    .meta {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin: 15px 0; }}
    .meta-item {{ background: #f8f9fa; padding: 12px; border-radius: 6px; text-align: center; }}
    .meta-value {{ font-size: 28px; font-weight: bold; color: #2c3e50; }}
    .meta-label {{ font-size: 12px; color: #7f8c8d; text-transform: uppercase; }}
</style></head>
<body><div class="container">
    <h1>质量报告</h1>
    <p>文件: {result.file_name} | 格式: {result.detected_format} | 生成: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    <div class="meta">
        <div class="meta-item"><div class="meta-value">{result.total_features}</div><div class="meta-label">要素数</div></div>
        <div class="meta-item"><div class="meta-value">{result.total_fields}</div><div class="meta-label">字段数</div></div>
        <div class="meta-item"><div class="score">{result.completeness_score:.2f}</div><div class="meta-label">完整性评分</div></div>
    </div>
    <h3>问题列表 ({len(result.issues)})</h3>
    <table><thead><tr><th>严重度</th><th>字段</th><th>问题类型</th><th>描述</th><th>影响数</th></tr></thead><tbody>
        {issues_html if issues_html else '<tr><td colspan="5" style="text-align:center;color:#999;">未发现质量问题</td></tr>'}
    </tbody></table>
</div></body></html>"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def generate_json_report(result: QualityResult, output_path: str) -> str:
    """生成 JSON 质量报告。"""
    report_data = {
        "file_name": result.file_name,
        "detected_format": result.detected_format,
        "total_features": result.total_features,
        "total_fields": result.total_fields,
        "completeness_score": result.completeness_score,
        "missing_fields": result.missing_fields,
        "null_value_counts": result.null_value_counts,
        "critical_fields_status": result.critical_fields_status,
        "issues": [
            {"severity": i.severity, "field": i.field, "issue_type": i.issue_type,
             "message": i.message, "affected_count": i.affected_count}
            for i in (result.issues or [])
        ],
        "constraint_violations": result.constraint_violations,
        "topology_violations": result.topology_violations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    return output_path
