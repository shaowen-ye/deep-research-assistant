import json

from .common import append_text, utc_now, write_json
from .exporters import write_pdf_from_markdown
from .http_client import post_json
from .state import job_dir, save_state, set_progress
from .tavily import search as tavily_search


MAX_ITERATIONS = 8
RESULTS_PER_QUERY = 6
TIMEOUT = 240


def make_system_prompt(include_visuals):
    base = (
        "你是一名严谨的中文研究分析师，可以调用 web_search(query) 工具检索网页。"
        "工作流程："
        "(1) 拆解题目并规划 4–8 个互不重复的检索词，必要时混用中英文；"
        "(2) 每次搜索的结果会以编号 [N] 返回，N 在整段对话内持续累计，跨轮不重置；"
        "(3) 信息足够后输出最终中文 Markdown 研究报告。"
        " 报告结构：摘要 / 关键发现 / 对比表 / 风险与限制 / 建议 / 结论。"
        "正文中所有事实、数据、观点必须使用数字编号引用并带可点击链接，"
        "例如（[1](https://example.com)）；编号必须与 Tavily 搜索结果中的 [N] 对应。"
        "末尾必须有「参考文献」章节，列出所有引用过的 [N] → 标题、URL、访问时间，"
        "不要补「年份不详」，不要伪造来源，不要把章节命名为 Sources。"
    )
    if include_visuals:
        base += (
            " 报告至少包含 2 张以 Markdown 表格形式呈现的可视化"
            "（评分矩阵 / 成本对比 / 风险矩阵等），插入到正文最相关段落附近，"
            "不要把表格堆在末尾，不要声称生成了图片。"
        )
    return base


def web_search_tool_schema():
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via Tavily for current information. Returns numbered snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, in any language. Be specific.",
                    }
                },
                "required": ["query"],
            },
        },
    }


def run_openai_research_job(job_id, state, config, tavily_key):
    progress_path = job_dir(job_id) / "research_progress.md"
    report_path = job_dir(job_id) / "research_report.md"

    state["local_status"] = "running"
    state["remote_status"] = "openai_research"
    state["base_url"] = config["base_url"]
    set_progress(state, "starting", 5, f"准备调用 {config['label']} + Tavily 搜索。")
    append_text(
        progress_path,
        f"[{utc_now()}] 使用 {config['label']} / {config['model']} + Tavily web_search "
        f"启动 agentic 研究（max_iter={MAX_ITERATIONS}）。\n",
    )
    save_state(job_id, state)

    system_prompt = make_system_prompt(state.get("include_visuals"))
    tools = [web_search_tool_schema()]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": state["topic"]},
    ]
    url = config["base_url"].rstrip("/") + "/chat/completions"

    global_sources = []
    url_to_index = {}
    search_queries = []
    final_content = None

    for iteration in range(MAX_ITERATIONS):
        body = {
            "model": config["model"],
            "messages": messages,
            "temperature": 0.3,
            "tools": tools,
            "tool_choice": "auto",
        }
        set_progress(
            state,
            "researching",
            min(15 + 8 * iteration, 80),
            f"模型规划与搜索（轮 {iteration + 1}/{MAX_ITERATIONS}）。",
        )
        save_state(job_id, state)

        result = post_json(url, config["api_key"], body, timeout=TIMEOUT)
        choice = (result.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []

        assistant_msg = {"role": "assistant", "content": message.get("content")}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            final_content = (message.get("content") or "").strip()
            if not final_content:
                raise RuntimeError("Provider returned no content and no tool calls.")
            break

        for tc in tool_calls:
            tcid = tc.get("id")
            fn = tc.get("function") or {}
            fn_name = fn.get("name")
            args_str = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}

            if fn_name != "web_search":
                tool_result_text = f"Unknown tool: {fn_name}. Only web_search is available."
            else:
                query = (args.get("query") or "").strip()
                if not query:
                    tool_result_text = "Tool web_search requires a non-empty 'query' argument."
                else:
                    search_queries.append(query)
                    set_progress(
                        state,
                        "searching",
                        min(15 + 8 * iteration, 80),
                        f"🔍 第 {len(search_queries)} 次搜索：{query}",
                    )
                    save_state(job_id, state)
                    append_text(
                        progress_path,
                        f"\n[{utc_now()}] 🔍 搜索 #{len(search_queries)}: {query}\n",
                    )
                    try:
                        res = tavily_search(tavily_key, query, max_results=RESULTS_PER_QUERY)
                    except Exception as exc:
                        tool_result_text = f"Tavily error: {exc}"
                        append_text(progress_path, f"  ⚠ Tavily 错误：{exc}\n")
                    else:
                        formatted = [f"# Search results for: {query}", ""]
                        for item in res["results"]:
                            url_ = item.get("url") or ""
                            if url_ in url_to_index:
                                idx = url_to_index[url_]
                            else:
                                idx = len(global_sources) + 1
                                url_to_index[url_] = idx
                                global_sources.append(
                                    {
                                        "url": url_,
                                        "title": item.get("title") or "",
                                        "snippet": item.get("snippet") or "",
                                    }
                                )
                            title = item.get("title") or url_ or f"Result {idx}"
                            formatted.append(f"## [{idx}] {title}")
                            if url_:
                                formatted.append(f"URL: {url_}")
                            if item.get("snippet"):
                                formatted.append("")
                                formatted.append(item["snippet"])
                            formatted.append("")
                            append_text(progress_path, f"  ↳ [{idx}] {title}\n    {url_}\n")
                        tool_result_text = "\n".join(formatted)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tcid,
                    "content": tool_result_text,
                }
            )

    if final_content is None:
        append_text(
            progress_path,
            f"\n[{utc_now()}] ⚠ 达到最大检索轮数 {MAX_ITERATIONS}，请求模型直接出报告。\n",
        )
        finalize_messages = messages + [
            {
                "role": "user",
                "content": (
                    "现在请基于以上所有 [N] 编号的检索结果，直接输出最终中文 Markdown 研究报告，"
                    "不要再调用工具，并按 system 提示中的格式给出「参考文献」章节。"
                ),
            }
        ]
        result = post_json(
            url,
            config["api_key"],
            {
                "model": config["model"],
                "messages": finalize_messages,
                "temperature": 0.3,
            },
            timeout=TIMEOUT,
        )
        choice = (result.get("choices") or [{}])[0]
        final_content = ((choice.get("message") or {}).get("content") or "").strip()
        if not final_content:
            raise RuntimeError("Provider returned no content after max iterations.")

    write_json(job_dir(job_id) / "openai_research_messages.json", messages)

    if "参考文献" not in final_content and "## References" not in final_content:
        bullets = []
        for idx, src in enumerate(global_sources, start=1):
            title = src.get("title") or src["url"] or f"Source {idx}"
            bullets.append(f"{idx}. [{title}]({src['url']})（访问时间 {utc_now()}）")
        if bullets:
            final_content += "\n\n## 参考文献\n\n" + "\n".join(bullets) + "\n"

    set_progress(state, "writing", 90, "正在写入 Markdown 报告。")
    save_state(job_id, state)
    report_path.write_text(final_content + "\n", encoding="utf-8")

    set_progress(state, "exporting", 95, "正在导出 PDF 文件。")
    save_state(job_id, state)
    ok, pdf_error = write_pdf_from_markdown(
        report_path, job_dir(job_id) / "research_report.pdf"
    )

    state["local_status"] = "completed"
    state["remote_status"] = "completed"
    state["report_bytes"] = report_path.stat().st_size
    state["progress_bytes"] = progress_path.stat().st_size if progress_path.exists() else 0
    state["pdf_ready"] = ok
    if not ok:
        state["pdf_error"] = pdf_error
    state["search_query_count"] = len(search_queries)
    state["citation_count"] = len(global_sources)
    state["usage"] = result.get("usage") if isinstance(result, dict) else None
    set_progress(
        state,
        "completed",
        100,
        f"研究完成（{len(search_queries)} 次搜索 / {len(global_sources)} 个来源）。",
    )
    save_state(job_id, state)
