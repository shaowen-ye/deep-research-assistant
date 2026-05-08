import json
from urllib import error, request

from .common import append_text, utc_now, write_json
from .exporters import write_pdf_from_markdown
from .gemini import iter_sse
from .state import job_dir, save_state, set_progress


ANTHROPIC_VERSION = "2023-06-01"
WEB_SEARCH_TOOL = "web_search_20250305"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_MAX_USES = 16
STREAM_TIMEOUT = 900


def build_request(config, system_prompt, topic):
    body = {
        "model": config["model"],
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": True,
        "system": system_prompt,
        "tools": [
            {
                "type": WEB_SEARCH_TOOL,
                "name": "web_search",
                "max_uses": DEFAULT_MAX_USES,
            }
        ],
        "messages": [{"role": "user", "content": topic}],
    }
    url = config["base_url"].rstrip("/") + "/v1/messages"
    return request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-api-key": config["api_key"],
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )


def make_system_prompt(include_visuals):
    base = (
        "你是一名严谨的中文研究分析师，使用 Anthropic 原生 web_search 工具检索权威来源。"
        "请输出结构化 Markdown 报告：摘要、关键发现、对比表、风险/限制、建议、结论；"
        "正文中每个事实/数据/观点都要带数字编号引用，例如（[1](https://example.com)），"
        "并在文末列出「参考文献」章节，每条只需标题或来源名、可点击 URL、访问时间，"
        "不要补「年份不详」，无法核实的来源不要列入；"
        "不要把参考资料章节命名为 Sources。"
    )
    if include_visuals:
        base += (
            " 报告至少包含 2 张以 Markdown 表格形式呈现的可视化（评分矩阵、对比表等），"
            "插入到正文中最相关的段落附近，不要堆在末尾。"
            "不要声称生成了图片。"
        )
    return base


def _percent_for_search(idx):
    return min(40 + 3 * idx, 80)


def _emit_progress_log(progress_path, kind, payload):
    timestamp = utc_now()
    if kind == "search_query":
        append_text(progress_path, f"\n[{timestamp}] 🔍 搜索: {payload}\n")
    elif kind == "search_result":
        append_text(
            progress_path,
            f"  ↳ {payload.get('title') or payload.get('url')}\n"
            f"    {payload.get('url')}\n",
        )
    elif kind == "text":
        append_text(progress_path, payload)
    elif kind == "section":
        append_text(progress_path, f"\n## {payload}\n")


def run_anthropic_job(job_id, state, config):
    progress_path = job_dir(job_id) / "research_progress.md"
    report_path = job_dir(job_id) / "research_report.md"
    raw_path = job_dir(job_id) / "anthropic_stream_raw.jsonl"

    state["local_status"] = "running"
    state["remote_status"] = "anthropic_research"
    state["base_url"] = config["base_url"]
    state["agent"] = config["model"]
    set_progress(state, "starting", 5, f"准备调用 {config['label']}（含原生网页搜索）。")
    append_text(
        progress_path,
        f"[{utc_now()}] 使用 {config['label']} / {config['model']} + Anthropic web_search "
        f"(max_uses={DEFAULT_MAX_USES}) 启动研究。\n",
    )
    save_state(job_id, state)

    system_prompt = make_system_prompt(state.get("include_visuals"))
    req = build_request(config, system_prompt, state["topic"])

    text_chunks = []
    block_kind = None
    block_buffer = []
    block_citations = []
    pending_search_query = None

    search_queries = []
    seen_urls = set()
    sources_in_order = []
    raw_events = []

    try:
        with request.urlopen(req, timeout=STREAM_TIMEOUT) as response:
            set_progress(state, "researching", 25, "Claude 正在规划检索路径。")
            save_state(job_id, state)
            for event_type, _event_id, data in iter_sse(response):
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                raw_events.append({"event": event_type, "data": event})
                etype = event.get("type") or event_type

                if etype == "content_block_start":
                    block = event.get("content_block") or {}
                    block_kind = block.get("type")
                    block_buffer = []
                    block_citations = []
                    if block_kind == "server_tool_use" and block.get("name") == "web_search":
                        pending_search_query = (block.get("input") or {}).get("query")
                    elif block_kind == "text":
                        pass

                elif etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        chunk = delta.get("text") or ""
                        block_buffer.append(chunk)
                    elif dtype == "input_json_delta":
                        partial = delta.get("partial_json") or ""
                        block_buffer.append(partial)
                    elif dtype == "citations_delta":
                        cit = delta.get("citation") or {}
                        if cit.get("type") == "web_search_result_location":
                            url = cit.get("url")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                sources_in_order.append(
                                    {
                                        "url": url,
                                        "title": (cit.get("title") or "").strip(),
                                        "cited_text": (cit.get("cited_text") or "").strip(),
                                    }
                                )
                            block_citations.append(cit)

                elif etype == "content_block_stop":
                    if block_kind == "text":
                        text_chunks.append("".join(block_buffer))
                        _emit_progress_log(progress_path, "text", "".join(block_buffer))
                    elif block_kind == "server_tool_use" and pending_search_query is None:
                        try:
                            blob = json.loads("".join(block_buffer) or "{}")
                            pending_search_query = blob.get("query")
                        except json.JSONDecodeError:
                            pending_search_query = None
                    if block_kind == "server_tool_use" and pending_search_query:
                        search_queries.append(pending_search_query)
                        _emit_progress_log(progress_path, "search_query", pending_search_query)
                        set_progress(
                            state,
                            "searching",
                            _percent_for_search(len(search_queries)),
                            f"🔍 第 {len(search_queries)} 次搜索：{pending_search_query}",
                        )
                        save_state(job_id, state)
                        pending_search_query = None
                    if block_kind == "web_search_tool_result":
                        items = block.get("content") if isinstance(block_buffer, list) else None
                        items = items or event.get("content_block", {}).get("content")
                        if isinstance(items, list):
                            for item in items[:5]:
                                if not isinstance(item, dict):
                                    continue
                                _emit_progress_log(
                                    progress_path,
                                    "search_result",
                                    {
                                        "title": item.get("title"),
                                        "url": item.get("url"),
                                    },
                                )
                                url = item.get("url")
                                if url and url not in seen_urls:
                                    seen_urls.add(url)
                                    sources_in_order.append(
                                        {
                                            "url": url,
                                            "title": (item.get("title") or "").strip(),
                                        }
                                    )
                    block_kind = None
                    block_buffer = []
                    block_citations = []

                elif etype == "message_delta":
                    delta = event.get("delta") or {}
                    if delta.get("stop_reason"):
                        state["anthropic_stop_reason"] = delta.get("stop_reason")
                    usage = event.get("usage") or {}
                    if usage:
                        state["usage"] = usage

                elif etype == "message_stop":
                    break

                elif etype == "error":
                    raise RuntimeError(
                        "Anthropic stream error: "
                        + json.dumps(event.get("error") or event)[:400]
                    )

    except error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            detail = ""
        raise RuntimeError(f"Anthropic HTTP {exc.code}: {detail}") from exc

    write_json(raw_path.with_suffix(".json"), raw_events)

    full_text = "".join(text_chunks).strip()
    if not full_text:
        raise RuntimeError("Anthropic returned no text content")

    if sources_in_order:
        bullets = []
        for idx, src in enumerate(sources_in_order, start=1):
            title = src.get("title") or src["url"]
            bullets.append(f"{idx}. [{title}]({src['url']})（访问时间 {utc_now()}）")
        if "## 参考文献" not in full_text and "参考文献\n" not in full_text:
            full_text += "\n\n## 参考文献\n\n" + "\n".join(bullets) + "\n"

    set_progress(state, "writing", 90, "正在写入 Markdown 报告。")
    save_state(job_id, state)
    report_path.write_text(full_text + "\n", encoding="utf-8")

    set_progress(state, "exporting", 95, "正在导出 PDF 文件。")
    save_state(job_id, state)
    ok, pdf_error = write_pdf_from_markdown(report_path, job_dir(job_id) / "research_report.pdf")

    state["local_status"] = "completed"
    state["remote_status"] = "completed"
    state["report_bytes"] = report_path.stat().st_size
    state["progress_bytes"] = progress_path.stat().st_size if progress_path.exists() else 0
    state["pdf_ready"] = ok
    if not ok:
        state["pdf_error"] = pdf_error
    state["search_query_count"] = len(search_queries)
    state["citation_count"] = len(sources_in_order)
    set_progress(
        state,
        "completed",
        100,
        f"研究完成（{len(search_queries)} 次搜索 / {len(sources_in_order)} 个来源）。",
    )
    save_state(job_id, state)
