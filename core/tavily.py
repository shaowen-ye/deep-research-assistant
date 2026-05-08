import json
from urllib import error, request


SEARCH_URL = "https://api.tavily.com/search"


def search(api_key, query, max_results=8, search_depth="advanced", timeout=60):
    if not api_key:
        raise RuntimeError(
            "Tavily API key is not configured. Add it via API 设置 → 搜索引擎 / Tavily."
        )
    body = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        SEARCH_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Tavily HTTP {exc.code}: {detail}") from exc

    results = []
    for item in payload.get("results", [])[:max_results]:
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": item.get("url") or "",
                "snippet": (item.get("content") or "").strip(),
                "score": item.get("score"),
            }
        )
    return {"query": query, "results": results}


def format_results_for_llm(query, results):
    if not results:
        return f"# Search results for: {query}\n\n(No results returned by Tavily.)"
    lines = [f"# Search results for: {query}", ""]
    for idx, item in enumerate(results, start=1):
        title = item["title"] or item["url"] or f"Result {idx}"
        lines.append(f"## [{idx}] {title}")
        if item["url"]:
            lines.append(f"URL: {item['url']}")
        if item.get("snippet"):
            lines.append("")
            lines.append(item["snippet"])
        lines.append("")
    return "\n".join(lines)
