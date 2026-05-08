from .common import append_text, utc_now, write_json
from .exporters import write_pdf_from_markdown
from .http_client import post_json
from .state import job_dir, save_state, set_progress


def run_chat_job(job_id, state, config):
    progress_path = job_dir(job_id) / "research_progress.md"
    report_path = job_dir(job_id) / "research_report.md"
    state["local_status"] = "running"
    state["remote_status"] = "chat_completion"
    set_progress(state, "starting", 5, f"准备调用 {config['label']}。")
    append_text(
        progress_path,
        f"[{utc_now()}] 使用 {config['label']} / {config['model']} 生成报告。\n",
    )
    save_state(job_id, state)

    system_prompt = (
        "你是一个严谨的中文研究报告助手。请输出结构化 Markdown 报告，"
        "包含摘要、关键发现、对比表、风险/限制、建议和结论。"
        "如果用户没有提供资料来源，请明确说明报告主要基于模型已有知识和用户输入，"
        "不能伪造网页引用。正文引用使用数字编号并带链接，例如（[1](https://example.com)）。"
        "报告末尾必须有“参考文献”章节，清单格式保持简洁；每条参考文献只需包含标题或来源名、可点击 URL 或 DOI URL、访问时间；不要补写“年份不详”。"
        "不要把参考资料章节命名为 Sources；"
        "无法核实的来源不要列入参考文献。"
    )
    if state.get("include_visuals"):
        system_prompt += (
            " 请使用 Markdown 表格形成可视化矩阵或评分表，并把图表放在最相关的正文段落附近，不要集中放到最后。"
            "如果当前模型不能生成图片，不要声称已生成图片。"
        )

    url = config["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["topic"]},
        ],
        "temperature": 0.4,
    }
    set_progress(state, "generating", 35, "模型正在生成研究报告。")
    save_state(job_id, state)
    result = post_json(url, config["api_key"], body)
    write_json(job_dir(job_id) / "chat_completion.json", result)
    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        raise RuntimeError("provider returned no message content")

    set_progress(state, "writing", 85, "正在写入 Markdown 报告。")
    report_path.write_text(content + "\n", encoding="utf-8")
    set_progress(state, "exporting", 95, "正在导出 PDF 文件。")
    ok, pdf_error = write_pdf_from_markdown(report_path, job_dir(job_id) / "research_report.pdf")
    state["local_status"] = "completed"
    state["remote_status"] = "completed"
    state["report_bytes"] = report_path.stat().st_size
    state["progress_bytes"] = progress_path.stat().st_size if progress_path.exists() else 0
    state["pdf_ready"] = ok
    if not ok:
        state["pdf_error"] = pdf_error
    state["usage"] = result.get("usage")
    set_progress(state, "completed", 100, "最终报告和可导出文件已生成。")
    save_state(job_id, state)
