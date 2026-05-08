import base64
import json
from urllib import parse, request

from .citations import (
    citation_metadata_for_sources,
    normalize_citations,
    parse_sources,
)
from .common import append_text, write_json
from .config import BASE_URL, DEFAULT_AGENT
from .exporters import write_pdf_from_markdown
from .http_client import request_json
from .state import bump_progress, job_dir, set_progress


def build_stream_request(state, api_key):
    interaction_id = state.get("interaction_id")
    last_event_id = state.get("last_event_id")

    if interaction_id:
        query = {"stream": "true"}
        if last_event_id:
            query["last_event_id"] = last_event_id
        base_url = state.get("base_url") or BASE_URL
        url = f"{base_url}/{interaction_id}?{parse.urlencode(query)}"
        data = None
        method = "GET"
    else:
        workflow_phase = state.get("workflow_phase") or "executing"
        body = {
            "input": state.get("pending_input") or state["topic"],
            "agent": state.get("agent") or DEFAULT_AGENT,
            "background": True,
            "store": True,
            "stream": True,
            "agent_config": {
                "type": "deep-research",
                "thinking_summaries": "auto",
                "visualization": "auto" if state.get("include_visuals") else "none",
                "collaborative_planning": workflow_phase == "planning",
            },
        }
        if state.get("previous_interaction_id"):
            body["previous_interaction_id"] = state["previous_interaction_id"]
        url = state.get("base_url") or BASE_URL
        data = json.dumps(body).encode("utf-8")
        method = "POST"

    return request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )


def iter_sse(response):
    event_type = None
    event_id = None
    data_lines = []

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
        if line.endswith("\r"):
            line = line[:-1]

        if not line:
            if event_type or data_lines:
                yield event_type, event_id, "\n".join(data_lines)
            event_type = None
            event_id = None
            data_lines = []
            continue

        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("id:"):
            event_id = line[len("id:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())


def image_extension(mime_type):
    if mime_type == "image/jpeg":
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    return ".png"


def save_image(job_id, content, prefix):
    data = content.get("data") or content.get("image", {}).get("data")
    if not data:
        return None

    mime_type = (
        content.get("mime_type")
        or content.get("mimeType")
        or content.get("image", {}).get("mime_type")
        or content.get("image", {}).get("mimeType")
        or "image/png"
    )
    images_dir = job_dir(job_id) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    count = len(list(images_dir.glob(prefix + "_*"))) + 1
    path = images_dir / f"{prefix}_{count}{image_extension(mime_type)}"
    path.write_bytes(base64.b64decode(data))
    return path


def markdown_image_for(job_id, image_path):
    return f"![图表](images/{image_path.name})"


def handle_event(job_id, state, content_types, event_type, event_id, data):
    progress_path = job_dir(job_id) / "research_progress.md"
    plan_path = job_dir(job_id) / "research_plan.md"
    report_path = job_dir(job_id) / "research_report.md"
    workflow_phase = state.get("workflow_phase") or "executing"

    if event_id:
        state["last_event_id"] = event_id

    if not data:
        return

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        append_text(progress_path, data + "\n")
        return

    payload_event_id = payload.get("event_id")
    if payload_event_id:
        state["last_event_id"] = payload_event_id

    if event_type == "interaction.start":
        interaction = payload.get("interaction", {})
        interaction_id = interaction.get("id")
        if interaction_id:
            state["interaction_id"] = interaction_id
            if workflow_phase == "planning":
                bump_progress(state, "planning", 8, "Google 已接受协作规划任务。")
            else:
                bump_progress(state, "accepted", 45 if state.get("collaborative_planning") else 10, "Google 已接受后台研究任务。")
        if interaction.get("status"):
            state["remote_status"] = interaction["status"]
        return

    if event_type == "interaction.status_update":
        if payload.get("status"):
            state["remote_status"] = payload["status"]
            if payload["status"] == "in_progress":
                if workflow_phase == "planning":
                    bump_progress(state, "planning", 15, "正在生成可审阅的研究计划。")
                else:
                    bump_progress(state, "researching", 50 if state.get("collaborative_planning") else 20, "正在搜索、阅读和综合资料。")
        return

    if event_type == "content.start":
        index = payload.get("index")
        content_type = payload.get("content", {}).get("type")
        if index is not None and content_type:
            content_types[index] = content_type
        if content_type == "thought":
            if workflow_phase == "planning":
                bump_progress(state, "planning", 20, "正在拟定研究计划和研究路径。")
            else:
                bump_progress(state, "planning", 15, "正在生成研究计划和研究路径。")
        else:
            if workflow_phase == "planning":
                bump_progress(state, "plan_output", 28, "正在输出研究计划。")
            else:
                bump_progress(state, "writing", 70, "正在生成最终报告内容。")
        return

    if event_type == "content.delta":
        index = payload.get("index")
        delta = payload.get("delta", {})
        content = delta.get("content", {})
        text = content.get("text")

        if text:
            delta_type = delta.get("type")
            parent_type = content_types.get(index)
            is_thought = delta_type in ("thought", "thought_summary") or parent_type == "thought"
            if workflow_phase == "planning" and not is_thought:
                output_path = plan_path
            else:
                output_path = progress_path if is_thought else report_path
            append_text(output_path, text)
            state["progress_bytes"] = progress_path.stat().st_size if progress_path.exists() else 0
            state["report_bytes"] = report_path.stat().st_size if report_path.exists() else 0
            state["plan_bytes"] = plan_path.stat().st_size if plan_path.exists() else 0
            state["event_count"] = int(state.get("event_count") or 0) + 1
            if workflow_phase == "planning":
                estimated = min(34, 18 + state["event_count"])
                bump_progress(state, "planning", estimated, "正在生成研究计划，完成后可批准或修改。")
            elif is_thought:
                estimated = min(82, 20 + state["event_count"] * 2)
                bump_progress(state, "researching", estimated, "正在研究中，已收到阶段性过程摘要。")
            else:
                bump_progress(state, "writing", 85, "正在接收最终报告正文。")

        if content.get("type") == "image" or content.get("data"):
            image_path = save_image(job_id, content, "stream")
            if image_path:
                append_text(report_path, "\n\n" + markdown_image_for(job_id, image_path) + "\n\n")
                state["image_count"] = state.get("image_count", 0) + 1
                bump_progress(state, "visualizing", 88, "正在接收并保存报告图表。")
        return

    if event_type == "interaction.failed":
        state["local_status"] = "failed"
        state["remote_status"] = "failed"
        state["error"] = payload
        set_progress(state, "failed", state.get("progress_percent", 0), "远端研究任务失败。")
        return


def extract_final_parts(job_id, interaction):
    parts = []

    for item in interaction.get("outputs", []):
        item_type = item.get("type")
        if item_type == "text" and item.get("text"):
            parts.append(item["text"].strip())
        elif item_type == "image":
            image_path = save_image(job_id, item, "final")
            if image_path:
                parts.append(markdown_image_for(job_id, image_path))

    for step in interaction.get("steps", []) or []:
        for item in step.get("content", []) or []:
            item_type = item.get("type")
            if item_type == "text" and item.get("text"):
                parts.append(item["text"].strip())
            elif item_type == "image":
                image_path = save_image(job_id, item, "final")
                if image_path:
                    parts.append(markdown_image_for(job_id, image_path))

    text = "\n\n".join(part for part in parts if part).strip()
    image_count = sum(1 for part in parts if part.startswith("!["))
    return text, image_count


def fetch_final_report(job_id, state, api_key):
    if not state.get("interaction_id"):
        return False

    base_url = state.get("base_url") or BASE_URL
    interaction = request_json("GET", f"{base_url}/{state['interaction_id']}", api_key)
    workflow_phase = state.get("workflow_phase") or "executing"
    json_name = "interaction_plan.json" if workflow_phase == "planning" else "interaction_final.json"
    write_json(job_dir(job_id) / json_name, interaction)
    state["remote_status"] = interaction.get("status")
    state["usage"] = interaction.get("usage")

    if interaction.get("status") != "completed":
        if workflow_phase == "planning":
            bump_progress(state, "planning", 34, "规划仍在进行，继续等待研究计划。")
        else:
            bump_progress(state, "researching", 85, "后台研究仍在进行，继续等待最终报告。")
        return False

    if workflow_phase == "planning":
        text, _ = extract_final_parts(job_id, interaction)
        plan_path = job_dir(job_id) / "research_plan.md"
        if text:
            plan_path.write_text(text + "\n", encoding="utf-8")
            state["plan_bytes"] = plan_path.stat().st_size
        state["plan_interaction_id"] = state["interaction_id"]
        state["previous_interaction_id"] = state["interaction_id"]
        state["interaction_id"] = None
        state["last_event_id"] = None
        state["pending_input"] = None
        state["local_status"] = "awaiting_approval"
        state["remote_status"] = "plan_ready"
        set_progress(state, "awaiting_approval", 35, "研究计划已生成，等待批准或修改。")
        return True

    set_progress(state, "finalizing", 90, "研究已完成，正在整理最终报告。")
    text, image_count = extract_final_parts(job_id, interaction)
    if not text:
        state["local_status"] = "completed"
        state["warning"] = "任务已完成，但 API 返回里没有找到文本报告。"
        set_progress(state, "completed", 100, "任务完成，但没有找到最终文本报告。")
        return True

    report_path = job_dir(job_id) / "research_report.md"
    sources_match, sources = parse_sources(text)
    if sources:
        set_progress(state, "citations", 92, "正在规范数字引用和简洁参考文献清单。")
        metadata_by_index = citation_metadata_for_sources(job_id, sources)
        backup_path = job_dir(job_id) / "research_report.original.md"
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8")
        text = normalize_citations(text, metadata_by_index)
        state["citation_normalized"] = True
        state["citation_count"] = len(sources)
    report_path.write_text(text, encoding="utf-8")
    state["report_bytes"] = report_path.stat().st_size
    state["image_count"] = max(state.get("image_count", 0), image_count)

    set_progress(state, "exporting", 95, "正在导出 PDF 文件。")
    ok, pdf_error = write_pdf_from_markdown(report_path, job_dir(job_id) / "research_report.pdf")
    state["pdf_ready"] = ok
    if not ok:
        state["pdf_error"] = pdf_error

    state["local_status"] = "completed"
    set_progress(state, "completed", 100, "最终报告和可导出文件已生成。")
    return True
