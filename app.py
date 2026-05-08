import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "app_data"
JOBS_DIR = DATA_DIR / "jobs"
SETTINGS_FILE = DATA_DIR / "settings.json"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_AGENT = "deep-research-preview-04-2026"
MAX_AGENT = "deep-research-max-preview-04-2026"
CONNECT_TIMEOUT_SECONDS = 120
POLL_BACKOFF_SECONDS = 5

PROVIDER_DEFAULTS = {
    "gemini": {
        "label": "Gemini Deep Research",
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/interactions",
        "model": DEFAULT_AGENT,
        "mode": "deep_research",
    },
    "deepseek": {
        "label": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "mode": "openai_chat",
    },
    "openai": {
        "label": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "mode": "openai_chat",
    },
    "openrouter": {
        "label": "OpenRouter",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "mode": "openai_chat",
    },
}

RUNNING = {}
RUNNING_LOCK = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs():
    STATIC_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        save_settings(default_settings())


def slugify(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return value[:42] or "research"


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def default_settings():
    return {
        "default_provider": "gemini",
        "providers": {
            provider: {
                "api_key": "",
                "base_url": defaults["base_url"],
                "model": defaults["model"],
            }
            for provider, defaults in PROVIDER_DEFAULTS.items()
        },
    }


def load_settings():
    settings = read_json(SETTINGS_FILE, None) or default_settings()
    defaults = default_settings()
    settings.setdefault("default_provider", defaults["default_provider"])
    settings.setdefault("providers", {})
    for provider, provider_defaults in defaults["providers"].items():
        settings["providers"].setdefault(provider, {})
        for key, value in provider_defaults.items():
            settings["providers"][provider].setdefault(key, value)
    return settings


def save_settings(settings):
    write_json(SETTINGS_FILE, settings)


def mask_secret(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return value[:4] + "..." + value[-4:]


def provider_config(provider):
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"unsupported provider: {provider}")

    settings = load_settings()
    saved = settings["providers"].get(provider, {})
    defaults = PROVIDER_DEFAULTS[provider]
    api_key = saved.get("api_key") or os.getenv(defaults["env_key"]) or ""
    return {
        "provider": provider,
        "label": defaults["label"],
        "mode": defaults["mode"],
        "env_key": defaults["env_key"],
        "api_key": api_key,
        "base_url": saved.get("base_url") or defaults["base_url"],
        "model": saved.get("model") or defaults["model"],
    }


def public_settings():
    settings = load_settings()
    providers = {}
    for provider, defaults in PROVIDER_DEFAULTS.items():
        saved = settings["providers"].get(provider, {})
        env_key = os.getenv(defaults["env_key"]) or ""
        local_key = saved.get("api_key") or ""
        providers[provider] = {
            "label": defaults["label"],
            "mode": defaults["mode"],
            "env_key": defaults["env_key"],
            "configured": bool(local_key or env_key),
            "key_source": "GUI" if local_key else ("env" if env_key else ""),
            "masked_key": mask_secret(local_key or env_key),
            "base_url": saved.get("base_url") or defaults["base_url"],
            "model": saved.get("model") or defaults["model"],
        }
    return {
        "default_provider": settings.get("default_provider", "gemini"),
        "providers": providers,
    }


def update_settings(payload):
    settings = load_settings()
    if payload.get("default_provider") in PROVIDER_DEFAULTS:
        settings["default_provider"] = payload["default_provider"]

    incoming = payload.get("providers") or {}
    for provider, values in incoming.items():
        if provider not in PROVIDER_DEFAULTS:
            continue
        target = settings["providers"].setdefault(provider, {})
        if "base_url" in values and values["base_url"].strip():
            target["base_url"] = values["base_url"].strip().rstrip("/")
        if "model" in values and values["model"].strip():
            target["model"] = values["model"].strip()
        if values.get("clear_key"):
            target["api_key"] = ""
        elif values.get("api_key"):
            target["api_key"] = values["api_key"].strip()

    save_settings(settings)
    return public_settings()


def job_dir(job_id):
    return JOBS_DIR / job_id


def state_path(job_id):
    return job_dir(job_id) / "state.json"


def load_state(job_id):
    return read_json(state_path(job_id), {})


def save_state(job_id, state):
    state["updated_at"] = utc_now()
    write_json(state_path(job_id), state)


def set_progress(state, stage, percent, message):
    state["stage"] = stage
    state["progress_percent"] = max(0, min(100, int(percent)))
    state["status_message"] = message


def bump_progress(state, stage, target_percent, message):
    current = int(state.get("progress_percent") or 0)
    set_progress(state, stage, max(current, target_percent), message)


def normalize_state(state):
    state.setdefault("stage", state.get("local_status") or "unknown")
    state.setdefault("progress_percent", 100 if state.get("local_status") == "completed" else 0)
    state.setdefault("status_message", "")
    if not state.get("status_message"):
        if state.get("local_status") == "completed":
            state["status_message"] = "最终报告和可导出文件已生成。"
        elif state.get("local_status") == "failed":
            state["status_message"] = "任务失败，请查看错误信息或重新运行。"
        elif state.get("local_status") == "stopped":
            state["status_message"] = "本地监听已停止，可恢复。"
    state.setdefault("provider_label", state.get("provider") or "")
    state.setdefault("pdf_ready", False)
    state.setdefault("report_bytes", 0)
    state.setdefault("progress_bytes", 0)
    state.setdefault("plan_bytes", 0)
    state.setdefault("image_count", 0)
    if state.get("id"):
        state["artifact_dir"] = str(job_dir(state["id"]))
    return state


def append_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def request_json(method, url, api_key):
    req = request.Request(
        url,
        method=method,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url, api_key, body, timeout=240):
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


def write_pdf_from_markdown(markdown_path, pdf_path):
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if not pandoc or not xelatex:
        return False, "未找到 pandoc 或 xelatex，跳过 PDF 生成。"

    link_style_path = markdown_path.parent / "pdf_link_style.tex"
    link_style_path.write_text(
        "\\usepackage[normalem]{ulem}\n"
        "\\let\\DeepResearchOldHref\\href\n"
        "\\renewcommand{\\href}[2]{\\DeepResearchOldHref{#1}{\\textcolor{blue}{\\uline{#2}}}}\n",
        encoding="utf-8",
    )

    command = [
        pandoc,
        str(markdown_path.name),
        "-o",
        str(pdf_path.name),
        "--pdf-engine=xelatex",
        "-V",
        "CJKmainfont=Songti SC",
        "-V",
        "geometry:margin=1in",
        "-V",
        "colorlinks=true",
        "-V",
        "linkcolor=blue",
        "-V",
        "urlcolor=blue",
        "-V",
        "citecolor=blue",
        f"--include-in-header={link_style_path.name}",
        "--resource-path=.:images",
    ]
    try:
        subprocess.run(
            command,
            cwd=markdown_path.parent,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr or str(exc)
    return True, ""


class CitationMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs if key and value}
        if tag.lower() == "title":
            self.in_title = True
            return
        if tag.lower() != "meta":
            return
        key = attrs.get("name") or attrs.get("property") or attrs.get("itemprop")
        content = attrs.get("content")
        if key and content:
            self.meta.setdefault(key.lower(), []).append(clean_meta_text(content))

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    @property
    def title(self):
        return clean_meta_text(" ".join(self.title_parts))


def clean_meta_text(value):
    value = unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def source_domain(url):
    try:
        host = parse.urlparse(url).netloc.lower()
    except Exception:
        host = ""
    return host.removeprefix("www.") or "Unknown source"


def first_meta(meta, *keys):
    for key in keys:
        values = meta.get(key.lower())
        if values:
            return values[0]
    return ""


def extract_year(value):
    match = re.search(r"\b(19|20)\d{2}\b", value or "")
    return match.group(0) if match else "n.d."


def clean_author(value, fallback):
    value = clean_meta_text(value)
    if not value:
        value = fallback
    value = re.split(r"\s+[|–—-]\s+", value)[0].strip()
    if len(value) > 90:
        value = value[:87].rstrip() + "..."
    return value or fallback


def fetch_source_metadata(url, label):
    fallback_site = clean_meta_text(label) or source_domain(url)
    fallback_author = fallback_site
    metadata = {
        "url": url,
        "final_url": url,
        "label": fallback_site,
        "title": fallback_site,
        "site": fallback_site,
        "author": fallback_author,
        "year": "n.d.",
    }

    try:
        req = request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 DeepResearchLocalApp/0.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with request.urlopen(req, timeout=5) as response:
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(240000)
    except Exception:
        return metadata

    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = CitationMetadataParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    site = first_meta(parser.meta, "og:site_name", "application-name", "twitter:site") or source_domain(final_url)
    title = (
        first_meta(parser.meta, "citation_title", "dc.title", "og:title", "twitter:title")
        or parser.title
        or fallback_site
    )
    author = first_meta(
        parser.meta,
        "citation_author",
        "author",
        "article:author",
        "dc.creator",
        "parsely-author",
        "sailthru.author",
    )
    date = first_meta(
        parser.meta,
        "citation_publication_date",
        "citation_date",
        "article:published_time",
        "datepublished",
        "date",
        "dc.date",
        "pubdate",
    )
    year = extract_year(date) if date else extract_year(title)

    metadata.update(
        {
            "final_url": final_url,
            "title": clean_meta_text(title),
            "site": clean_meta_text(site),
            "author": clean_author(author, clean_meta_text(site) or fallback_author),
            "year": year,
        }
    )
    return metadata


def parse_sources(markdown):
    match = re.search(r"(?ims)\n(?:\*\*Sources:\*\*|#+\s*Sources)\s*\n(.+)\s*$", markdown)
    if not match:
        return None, []

    sources = []
    for line in match.group(1).splitlines():
        item = re.match(r"\s*(\d+)\.\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if item:
            sources.append(
                {
                    "index": int(item.group(1)),
                    "label": clean_meta_text(item.group(2)),
                    "url": item.group(3).strip(),
                }
            )
    return match, sources


def citation_metadata_for_sources(job_id, sources):
    cache_path = job_dir(job_id) / "citation_metadata.json"
    cache = read_json(cache_path, {}) or {}
    metadata_by_index = {}
    for source in sources:
        cache_key = source["url"]
        metadata = cache.get(cache_key)
        if not metadata:
            metadata = fetch_source_metadata(source["url"], source["label"])
            cache[cache_key] = metadata
        metadata_by_index[source["index"]] = metadata
    write_json(cache_path, cache)
    return metadata_by_index


def citation_label(metadata):
    return f"{metadata.get('author') or metadata.get('site')}, {metadata.get('year') or 'n.d.'}"


def accessed_date():
    return datetime.now().strftime("%Y-%m-%d")


def numbered_reference(index, metadata):
    title = metadata.get("title") or metadata.get("label") or "Untitled"
    site = metadata.get("site") or source_domain(metadata.get("final_url") or metadata.get("url") or "")
    url = metadata.get("final_url") or metadata.get("url") or ""
    title = clean_meta_text(title).rstrip(".。；;")
    if not title or title == "Untitled":
        title = clean_meta_text(site) or source_domain(url)
    return f"{index}. {title}。<{url}>。访问时间：{accessed_date()}。"


def normalize_citations(markdown, metadata_by_index):
    sources_match, sources = parse_sources(markdown)
    if not sources:
        return markdown

    def replace_cite(match):
        labels = []
        for number in re.findall(r"\d+", match.group(1)):
            metadata = metadata_by_index.get(int(number))
            if metadata:
                url = metadata.get("final_url") or metadata.get("url") or ""
                labels.append(f"[{number}]({url})")
        if not labels:
            return match.group(0)
        return f"（{'；'.join(labels)}）"

    body = markdown[: sources_match.start()].rstrip()
    body = re.sub(r"\s*\[cite:\s*([0-9,\s]+)\]", replace_cite, body)
    references = "\n".join(
        numbered_reference(source["index"], metadata_by_index[source["index"]])
        for source in sources
        if source["index"] in metadata_by_index
    )
    return f"{body}\n\n## 参考文献\n\n{references}\n"


def normalize_job_citations(job_id):
    report_path = job_dir(job_id) / "research_report.md"
    if not report_path.exists():
        return {"changed": False, "reason": "report not found"}

    backup_path = job_dir(job_id) / "research_report.original.md"
    markdown = backup_path.read_text(encoding="utf-8") if backup_path.exists() else report_path.read_text(encoding="utf-8")
    sources_match, sources = parse_sources(markdown)
    if not sources:
        return {"changed": False, "reason": "sources not found"}

    metadata_by_index = citation_metadata_for_sources(job_id, sources)
    normalized = normalize_citations(markdown, metadata_by_index)
    if normalized == markdown:
        return {"changed": False, "reason": "no citation changes"}

    if not backup_path.exists():
        backup_path.write_text(markdown, encoding="utf-8")
    report_path.write_text(normalized, encoding="utf-8")

    state = load_state(job_id)
    state["report_bytes"] = report_path.stat().st_size
    state["citation_normalized"] = True
    state["citation_count"] = len(sources)
    ok, pdf_error = write_pdf_from_markdown(report_path, job_dir(job_id) / "research_report.pdf")
    state["pdf_ready"] = ok
    if not ok:
        state["pdf_error"] = pdf_error
    save_state(job_id, state)
    return {"changed": True, "source_count": len(sources), "pdf_ready": ok}


def make_artifact_zip(job_id):
    base = job_dir(job_id)
    archive = base / "research_artifacts.zip"
    include_names = [
        "state.json",
        "research_progress.md",
        "research_plan.md",
        "research_report.md",
        "research_report.original.md",
        "research_report.pdf",
        "citation_metadata.json",
        "interaction_plan.json",
        "interaction_final.json",
        "chat_completion.json",
    ]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in include_names:
            path = base / name
            if path.exists() and path.is_file():
                zf.write(path, arcname=name)
        images_dir = base / "images"
        if images_dir.exists():
            for image in sorted(images_dir.iterdir()):
                if image.is_file():
                    zf.write(image, arcname=f"images/{image.name}")
    return archive


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


def worker(job_id):
    state = load_state(job_id)
    state["local_status"] = "running"
    state["error"] = None
    bump_progress(state, "starting", 3, "任务已进入本地后台队列。")
    save_state(job_id, state)

    provider = state.get("provider", "gemini")
    config = provider_config(provider)
    if not config["api_key"]:
        state["local_status"] = "failed"
        state["error"] = f"{config['label']} API key is not configured"
        set_progress(state, "failed", 0, "缺少所选 Provider 的 API key。")
        save_state(job_id, state)
        with RUNNING_LOCK:
            RUNNING.pop(job_id, None)
        return

    if config["mode"] == "openai_chat":
        try:
            run_chat_job(job_id, state, config)
        except Exception as exc:
            state = load_state(job_id)
            state["local_status"] = "failed"
            state["error"] = repr(exc)
            save_state(job_id, state)
        finally:
            with RUNNING_LOCK:
                RUNNING.pop(job_id, None)
        return

    api_key = config["api_key"]
    state["base_url"] = config["base_url"]
    state["agent"] = state.get("agent") or config["model"]
    if state.get("workflow_phase") == "planning":
        bump_progress(state, "connecting", 5, "正在连接 Gemini Deep Research Agent 生成研究计划。")
    elif state.get("collaborative_planning"):
        bump_progress(state, "connecting", 40, "计划已批准，正在启动正式研究。")
    else:
        bump_progress(state, "connecting", 5, "正在连接 Gemini Deep Research Agent。")
    save_state(job_id, state)

    content_types = {}
    try:
        while True:
            state = load_state(job_id)
            if state.get("stop_requested"):
                state["local_status"] = "stopped"
                set_progress(state, "stopped", state.get("progress_percent", 0), "本地监听已停止，可稍后恢复。")
                save_state(job_id, state)
                break

            if state.get("local_status") == "awaiting_approval":
                save_state(job_id, state)
                break

            req = build_stream_request(state, api_key)
            try:
                with request.urlopen(req, timeout=CONNECT_TIMEOUT_SECONDS) as response:
                    for event_type, event_id, data in iter_sse(response):
                        state = load_state(job_id)
                        if state.get("stop_requested"):
                            state["local_status"] = "stopped"
                            set_progress(state, "stopped", state.get("progress_percent", 0), "本地监听已停止，可稍后恢复。")
                            save_state(job_id, state)
                            return
                        state["local_status"] = "running"
                        handle_event(job_id, state, content_types, event_type, event_id, data)
                        save_state(job_id, state)
            except (error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
                state = load_state(job_id)
                if not state.get("interaction_id"):
                    state["local_status"] = "failed"
                    state["error"] = (
                        "启动请求在收到 interaction id 之前断开。"
                        "为避免重复新建任务，后台不会自动重试。"
                    )
                    set_progress(state, "failed", state.get("progress_percent", 0), "启动阶段连接断开，未取得任务 ID。")
                    save_state(job_id, state)
                    break
                state["last_network_error"] = str(exc)
                state["local_status"] = "reconnecting"
                bump_progress(state, "reconnecting", state.get("progress_percent", 20), "网络连接中断，正在自动恢复监听。")
                save_state(job_id, state)
                time.sleep(POLL_BACKOFF_SECONDS)
                continue

            state = load_state(job_id)
            try:
                if fetch_final_report(job_id, state, api_key):
                    save_state(job_id, state)
                    break
            except Exception as exc:
                state["last_network_error"] = str(exc)
                state["local_status"] = "reconnecting"
                bump_progress(state, "reconnecting", state.get("progress_percent", 20), "查询最终报告时网络中断，正在重试。")
                save_state(job_id, state)
                time.sleep(POLL_BACKOFF_SECONDS)
                continue

            if state.get("remote_status") == "failed":
                state["local_status"] = "failed"
                set_progress(state, "failed", state.get("progress_percent", 0), "远端研究任务失败。")
                save_state(job_id, state)
                break

            state["local_status"] = "waiting"
            bump_progress(state, "waiting", state.get("progress_percent", 20), "流暂时结束，远端任务仍在运行，等待下一次恢复。")
            save_state(job_id, state)
            time.sleep(POLL_BACKOFF_SECONDS)
    except Exception as exc:
        state = load_state(job_id)
        state["local_status"] = "failed"
        state["error"] = repr(exc)
        set_progress(state, "failed", state.get("progress_percent", 0), "本地后台任务异常退出。")
        save_state(job_id, state)
    finally:
        with RUNNING_LOCK:
            RUNNING.pop(job_id, None)


def start_job_thread(job_id):
    with RUNNING_LOCK:
        thread = RUNNING.get(job_id)
        if thread and thread.is_alive():
            return False
        thread = threading.Thread(target=worker, args=(job_id,), daemon=True)
        RUNNING[job_id] = thread
        thread.start()
        return True


def make_prompt(topic, include_visuals):
    reference_instruction = (
        "\n\n引用和参考文献要求："
        "\n- 正文中凡引用事实、数据、观点或文献，请使用数字编号引用，并让编号可以点击打开网页来源，例如（[1](https://example.com)）。"
        "\n- 不要把文末参考资料标题写成 Sources。"
        "\n- 报告末尾必须列出“参考文献”章节。"
        "\n- 参考文献清单保持简洁，不要勉强套用 APA，也不要补写“年份不详”；每条参考文献只需包含标题或来源名、可点击网页链接或 DOI URL，并标注访问时间，方便读者核对。"
        "\n- 不要伪造来源；无法核实来源时，请明确标注为模型综合判断。"
    )
    if not include_visuals:
        return topic.strip() + reference_instruction
    visual_instruction = (
        "\n\n图表要求："
        "\n- 最终中文报告中包含至少 2 张相关图表，例如能力矩阵、成本/隐私风险对比图。"
        "\n- 图、表要插入在正文中最相关的位置，并紧跟对应分析，不要集中放在报告最后。"
        "\n- 每张图表要有简短标题，并在正文中解释其含义。"
    )
    return topic.strip() + visual_instruction + reference_instruction


def create_job(payload):
    raw_topic = (payload.get("topic") or "").strip()
    if not raw_topic:
        raise ValueError("topic is required")

    title = (payload.get("title") or raw_topic.splitlines()[0]).strip()[:120]
    include_visuals = bool(payload.get("include_visuals", True))
    provider = payload.get("provider") or load_settings().get("default_provider", "gemini")
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"unsupported provider: {provider}")
    config = provider_config(provider)
    if not config["api_key"]:
        raise ValueError(f"{config['label']} API key is not configured")
    collaborative_planning = bool(payload.get("collaborative_planning")) and provider == "gemini"

    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + slugify(title) + "-" + uuid.uuid4().hex[:6]
    path = job_dir(job_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "images").mkdir(exist_ok=True)

    state = {
        "id": job_id,
        "title": title,
        "topic": make_prompt(raw_topic, include_visuals),
        "provider": provider,
        "provider_label": config["label"],
        "provider_mode": config["mode"],
        "agent": payload.get("agent") or (config["model"] if provider == "gemini" else None),
        "model": payload.get("model") or config["model"],
        "base_url": config["base_url"] if provider == "gemini" else None,
        "include_visuals": include_visuals,
        "collaborative_planning": collaborative_planning,
        "workflow_phase": "planning" if collaborative_planning else "executing",
        "previous_interaction_id": None,
        "plan_interaction_id": None,
        "pending_input": None,
        "local_status": "queued",
        "remote_status": None,
        "interaction_id": None,
        "last_event_id": None,
        "stop_requested": False,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "stage": "queued",
        "progress_percent": 0,
        "status_message": "任务已创建，等待后台启动。",
        "progress_bytes": 0,
        "plan_bytes": 0,
        "report_bytes": 0,
        "image_count": 0,
        "pdf_ready": False,
        "error": None,
    }
    write_json(path / "state.json", state)
    (path / "research_progress.md").write_text("", encoding="utf-8")
    (path / "research_plan.md").write_text("", encoding="utf-8")
    (path / "research_report.md").write_text("", encoding="utf-8")
    start_job_thread(job_id)
    return state


def plan_action(job_id, action, payload=None):
    payload = payload or {}
    state = load_state(job_id)
    if not state:
        raise ValueError("job not found")
    if state.get("provider") != "gemini" or not state.get("collaborative_planning"):
        raise ValueError("this job is not a Gemini collaborative planning job")
    if state.get("local_status") != "awaiting_approval":
        raise ValueError("research plan is not ready for approval or refinement")

    previous_id = state.get("plan_interaction_id") or state.get("previous_interaction_id")
    if not previous_id:
        raise ValueError("missing previous interaction id")

    state["previous_interaction_id"] = previous_id
    state["interaction_id"] = None
    state["last_event_id"] = None
    state["event_count"] = 0
    state["stop_requested"] = False
    state["remote_status"] = None

    if action == "approve":
        instruction = (payload.get("input") or "").strip()
        state["pending_input"] = instruction or "Plan looks good. Please proceed with the research and produce the final report."
        state["workflow_phase"] = "executing"
        state["local_status"] = "queued"
        set_progress(state, "approved", 40, "研究计划已批准，等待启动正式研究。")
    elif action == "refine":
        instruction = (payload.get("input") or "").strip()
        if not instruction:
            raise ValueError("refinement input is required")
        state["pending_input"] = instruction
        state["workflow_phase"] = "planning"
        state["local_status"] = "queued"
        append_text(job_dir(job_id) / "research_progress.md", f"\n\n## 计划修改意见\n\n{instruction}\n")
        set_progress(state, "refining_plan", 36, "已提交修改意见，正在生成新版研究计划。")
    else:
        raise ValueError("unsupported plan action")

    save_state(job_id, state)
    started = start_job_thread(job_id)
    return {"started": started, "state": normalize_state(load_state(job_id))}


def list_jobs():
    jobs = []
    for path in JOBS_DIR.iterdir() if JOBS_DIR.exists() else []:
        if path.is_dir() and (path / "state.json").exists():
            state = normalize_state(read_json(path / "state.json", {}))
            with RUNNING_LOCK:
                state["thread_running"] = path.name in RUNNING and RUNNING[path.name].is_alive()
            jobs.append(state)
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return jobs


def health():
    settings = public_settings()
    return {
        "api_key_configured": any(item["configured"] for item in settings["providers"].values()),
        "providers": settings["providers"],
        "pandoc": shutil.which("pandoc"),
        "xelatex": shutil.which("xelatex"),
        "data_dir": str(DATA_DIR),
        "default_agent": DEFAULT_AGENT,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepResearchApp/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self.serve_file(STATIC_DIR / "index.html")
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/static/"):
            return self.serve_file(STATIC_DIR / path.removeprefix("/static/"))

        if path == "/api/health":
            return self.send_json(health())
        if path == "/api/settings":
            return self.send_json(public_settings())
        if path == "/api/jobs":
            return self.send_json({"jobs": list_jobs()})

        match = re.fullmatch(r"/api/jobs/([^/]+)", path)
        if match:
            job_id = parse.unquote(match.group(1))
            state = load_state(job_id)
            if not state:
                return self.send_json({"error": "job not found"}, 404)
            state = normalize_state(state)
            with RUNNING_LOCK:
                state["thread_running"] = job_id in RUNNING and RUNNING[job_id].is_alive()
            return self.send_json(state)

        match = re.fullmatch(r"/api/jobs/([^/]+)/(progress|plan|report)", path)
        if match:
            job_id, name = parse.unquote(match.group(1)), match.group(2)
            if name == "progress":
                file_name = "research_progress.md"
            elif name == "plan":
                file_name = "research_plan.md"
            else:
                file_name = "research_report.md"
            path = job_dir(job_id) / file_name
            if not path.exists():
                return self.send_text("")
            return self.send_text(path.read_text(encoding="utf-8"))

        match = re.fullmatch(r"/files/([^/]+)/(.*)", path)
        if match:
            job_id = parse.unquote(match.group(1))
            rel = parse.unquote(match.group(2))
            if rel == "research_artifacts.zip":
                make_artifact_zip(job_id)
            return self.serve_job_file(job_id, rel)

        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/jobs":
            try:
                state = create_job(self.read_body_json())
                return self.send_json(state, 201)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path == "/api/settings":
            try:
                return self.send_json(update_settings(self.read_body_json()))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 400)

        match = re.fullmatch(r"/api/jobs/([^/]+)/(resume|stop|approve|refine|reveal|normalize)", path)
        if match:
            job_id, action = parse.unquote(match.group(1)), match.group(2)
            state = load_state(job_id)
            if not state:
                return self.send_json({"error": "job not found"}, 404)
            if action == "normalize":
                try:
                    return self.send_json(normalize_job_citations(job_id))
                except Exception as exc:
                    return self.send_json({"error": str(exc)}, 500)
            if action == "reveal":
                target = job_dir(job_id)
                try:
                    subprocess.run(["open", str(target)], check=True)
                    return self.send_json({"revealed": True, "path": str(target)})
                except Exception as exc:
                    return self.send_json({"error": str(exc), "path": str(target)}, 500)
            if action in ("approve", "refine"):
                try:
                    return self.send_json(plan_action(job_id, action, self.read_body_json()))
                except Exception as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if action == "resume":
                if state.get("local_status") == "awaiting_approval":
                    return self.send_json({"error": "plan is waiting for approval or refinement"}, 400)
                state["stop_requested"] = False
                state["local_status"] = "queued"
                save_state(job_id, state)
                started = start_job_thread(job_id)
                return self.send_json({"started": started, "state": load_state(job_id)})
            state["stop_requested"] = True
            save_state(job_id, state)
            return self.send_json({"stopping": True, "state": state})

        return self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = parse.urlparse(self.path)
        match = re.fullmatch(r"/api/jobs/([^/]+)", parsed.path)
        if not match:
            return self.send_json({"error": "not found"}, 404)

        job_id = parse.unquote(match.group(1))
        with RUNNING_LOCK:
            running = job_id in RUNNING and RUNNING[job_id].is_alive()
        if running:
            state = load_state(job_id)
            state["stop_requested"] = True
            save_state(job_id, state)
            return self.send_json({"error": "job is running; stop it first"}, 409)

        path = job_dir(job_id)
        if path.exists():
            shutil.rmtree(path)
        return self.send_json({"deleted": True})

    def serve_file(self, path):
        path = path.resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists() or not path.is_file():
            return self.send_json({"error": "file not found"}, 404)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_job_file(self, job_id, rel):
        base = job_dir(job_id).resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)) or not target.exists() or not target.is_file():
            return self.send_json({"error": "file not found"}, 404)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if target.suffix in (".zip", ".pdf", ".md"):
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ensure_dirs()
    parser = argparse.ArgumentParser(description="Gemini Deep Research local app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Gemini Deep Research app: http://{args.host}:{args.port}")
    print(f"Data directory: {DATA_DIR}")
    if not any(item["configured"] for item in public_settings()["providers"].values()):
        print("Warning: no provider API key is configured.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
