import json
import re
from urllib import error, request

from .common import read_json, utc_now, write_json
from .config import provider_config
from .http_client import post_json
from .state import job_dir


PATCH_BLOCK_RE = re.compile(r"```(?:json)?\s*edit-patches\s*\n(.*?)```", re.DOTALL)
MAX_REPORT_CHARS = 60000
MAX_HISTORY_TURNS = 12


SYSTEM_PROMPT = """你是一名严谨的中文学术报告编辑助手。用户已经有一份完成的研究报告，希望通过多轮对话逐步完善。

你的工作方式：
1. 仔细阅读用户提供的「当前报告全文」，再结合用户最新的修改诉求，给出建议。
2. 当你建议具体的文字修改时，必须以 find/replace 的方式输出补丁；不要直接重写整段，也不要给出"建议改成 …"这样的散文描述而不附补丁。
3. 补丁里的 `find` 必须是当前报告中**逐字出现的连续片段**（包括标点、空格、换行、引用编号），不能改写或润色，否则补丁会失败。
4. 补丁要小而聚焦：每条补丁尽量只解决一个具体问题（一个论点、一个句子、一个数据、一处引用、一个段落小结）。如果一次涉及多个修改，拆成多条补丁，方便用户逐条审阅。
5. 如果用户只是讨论或提问、没有让你改稿，那就正常用中文回答，不要硬塞补丁。
6. 不要伪造引用、不要捏造数据；引用编号 `[n](url)` 与现有报告保持一致。

补丁格式（用代码块包裹，语言标识为 `json edit-patches`，整体是合法 JSON）：

```json edit-patches
{
  "summary": "本轮主要修改的一句话总结",
  "patches": [
    {
      "id": "p1",
      "intent": "为什么改 / 改了什么（一句话）",
      "find": "<报告里逐字出现的原文>",
      "replace": "<替换后的内容；保留 Markdown 与引用格式>"
    }
  ]
}
```

你可以在代码块前后写自然语言解释，但补丁本身**只能放在一个代码块里**。如果本轮没有补丁，省略代码块即可。"""


def _editor_dir(job_id):
    return job_dir(job_id)


def _versions_dir(job_id):
    return _editor_dir(job_id) / "report_versions"


def _session_path(job_id):
    return _editor_dir(job_id) / "editor_session.json"


def _manifest_path(job_id):
    return _versions_dir(job_id) / "manifest.json"


def _report_path(job_id):
    return _editor_dir(job_id) / "research_report.md"


def _ensure_baseline(job_id):
    """Make sure v0001 exists; treat current report.md as v0001 if first run."""
    versions = _versions_dir(job_id)
    versions.mkdir(parents=True, exist_ok=True)
    manifest = read_json(_manifest_path(job_id), [])
    if manifest:
        return manifest
    report = _report_path(job_id)
    if not report.exists():
        raise RuntimeError("research_report.md 不存在，无法启动编辑会话")
    baseline = versions / "v0001.md"
    baseline.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = [{
        "version": "v0001",
        "parent": None,
        "timestamp": utc_now(),
        "summary": "初始报告（编辑前）",
        "applied_patches": [],
    }]
    write_json(_manifest_path(job_id), manifest)
    return manifest


def _next_version(manifest):
    if not manifest:
        return "v0001"
    last = manifest[-1]["version"]
    n = int(last.lstrip("v"))
    return f"v{n + 1:04d}"


def load_session(job_id):
    _ensure_baseline(job_id)
    session = read_json(_session_path(job_id), None)
    if not session:
        session = {"history": [], "pending": []}
        write_json(_session_path(job_id), session)
    return session


def list_versions(job_id):
    _ensure_baseline(job_id)
    return read_json(_manifest_path(job_id), [])


def read_version(job_id, version):
    path = _versions_dir(job_id) / f"{version}.md"
    if not path.exists():
        raise RuntimeError(f"版本不存在：{version}")
    return path.read_text(encoding="utf-8")


def get_state(job_id):
    return {
        "session": load_session(job_id),
        "versions": list_versions(job_id),
        "report": _report_path(job_id).read_text(encoding="utf-8")
        if _report_path(job_id).exists()
        else "",
    }


def parse_patches(text):
    """Extract patch JSON from the assistant message. Returns (patches, summary, parse_error)."""
    match = PATCH_BLOCK_RE.search(text)
    if not match:
        return [], "", None
    blob = match.group(1).strip()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return [], "", f"补丁 JSON 解析失败：{exc}"
    summary = (data.get("summary") or "").strip()
    raw_patches = data.get("patches") or []
    if not isinstance(raw_patches, list):
        return [], summary, "patches 字段必须是数组"
    cleaned = []
    for idx, p in enumerate(raw_patches):
        if not isinstance(p, dict):
            continue
        find = p.get("find")
        replace = p.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str):
            continue
        if not find:
            continue
        cleaned.append({
            "id": str(p.get("id") or f"p{idx + 1}"),
            "intent": (p.get("intent") or "").strip(),
            "find": find,
            "replace": replace,
            "status": "pending",
        })
    return cleaned, summary, None


def _validate_patch_against(report, patch):
    occurrences = report.count(patch["find"])
    if occurrences == 0:
        return "未在当前报告中找到匹配文本"
    if occurrences > 1:
        return f"原文出现 {occurrences} 次，无法唯一定位"
    return None


def annotate_patches(report, patches):
    """Add validation + preview info to each patch."""
    for p in patches:
        err = _validate_patch_against(report, p)
        p["match_error"] = err
        p["applicable"] = err is None
    return patches


def _trim_report_for_prompt(report):
    if len(report) <= MAX_REPORT_CHARS:
        return report, False
    head = MAX_REPORT_CHARS // 2
    tail = MAX_REPORT_CHARS - head
    return report[:head] + "\n\n[...报告中段已省略...]\n\n" + report[-tail:], True


def _trim_history(history):
    if len(history) <= MAX_HISTORY_TURNS:
        return history
    return history[-MAX_HISTORY_TURNS:]


def build_messages(report, history, user_message, truncated):
    user_intro = (
        "## 当前报告全文\n\n"
        + ("（报告过长，中段已省略，请基于上下文判断；必要时让用户提供具体段落。）\n\n" if truncated else "")
        + report
    )
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_intro},
        {"role": "assistant", "content": "已读取报告，请告诉我你想从哪里改起。"},
    ]
    msgs.extend(_trim_history(history))
    msgs.append({"role": "user", "content": user_message})
    return msgs


def call_chat(provider, messages, model_override=None):
    """Provider-agnostic chat call. Returns the assistant text."""
    config = provider_config(provider)
    if model_override:
        config = dict(config)
        config["model"] = model_override
    if not config.get("api_key"):
        raise RuntimeError(f"{config['label']} 未配置 API key")
    if provider == "anthropic":
        return _call_anthropic(config, messages)
    return _call_openai_chat(config, messages)


def _call_openai_chat(config, messages):
    url = config["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.4,
    }
    result = post_json(url, config["api_key"], body, timeout=180)
    return (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def _call_anthropic(config, messages):
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    convo = [m for m in messages if m["role"] != "system"]
    body = {
        "model": config["model"],
        "max_tokens": 8000,
        "system": "\n\n".join(system_parts),
        "messages": [{"role": m["role"], "content": m["content"]} for m in convo],
    }
    url = config["base_url"].rstrip("/") + "/v1/messages"
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": config["api_key"],
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with request.urlopen(req, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"Anthropic HTTP {exc.code}: {detail}") from exc
    parts = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts).strip()


def send_message(job_id, user_message, provider, model=None):
    """Send a chat turn. Returns the new session state."""
    user_message = (user_message or "").strip()
    if not user_message:
        raise RuntimeError("消息为空")
    session = load_session(job_id)
    report_path = _report_path(job_id)
    if not report_path.exists():
        raise RuntimeError("研究报告不存在，无法编辑")
    report = report_path.read_text(encoding="utf-8")
    trimmed_report, truncated = _trim_report_for_prompt(report)
    history = [
        {"role": h["role"], "content": h["content"]} for h in session.get("history", [])
    ]
    messages = build_messages(trimmed_report, history, user_message, truncated)
    assistant = call_chat(provider, messages, model_override=model)
    patches, summary, parse_error = parse_patches(assistant)
    annotate_patches(report, patches)

    turn_id = utc_now()
    user_turn = {
        "id": f"u-{turn_id}",
        "role": "user",
        "content": user_message,
        "timestamp": utc_now(),
    }
    assistant_turn = {
        "id": f"a-{turn_id}",
        "role": "assistant",
        "content": assistant,
        "timestamp": utc_now(),
        "provider": provider,
        "model": model or provider_config(provider)["model"],
        "summary": summary,
        "patches": patches,
        "parse_error": parse_error,
    }
    session.setdefault("history", []).extend([user_turn, assistant_turn])
    write_json(_session_path(job_id), session)
    return get_state(job_id)


def _find_patch(session, patch_id):
    for turn in reversed(session.get("history", [])):
        if turn.get("role") != "assistant":
            continue
        for patch in turn.get("patches") or []:
            if patch.get("id") == patch_id and patch.get("status") == "pending":
                return turn, patch
    return None, None


def apply_patches(job_id, patch_ids):
    """Apply selected patches in order, snapshot a new version."""
    if not patch_ids:
        raise RuntimeError("未选择补丁")
    session = load_session(job_id)
    report_path = _report_path(job_id)
    report = report_path.read_text(encoding="utf-8")

    targets = []
    for pid in patch_ids:
        turn, patch = _find_patch(session, pid)
        if not turn or not patch:
            raise RuntimeError(f"补丁未找到或已处理：{pid}")
        if patch.get("match_error"):
            raise RuntimeError(f"补丁无法应用（{pid}）：{patch['match_error']}")
        targets.append((turn, patch))

    new_report = report
    applied = []
    for turn, patch in targets:
        find = patch["find"]
        if new_report.count(find) != 1:
            raise RuntimeError(
                f"补丁应用时原文不再唯一匹配（{patch['id']}）；可能与之前的补丁冲突，请重新发送修改诉求。"
            )
        new_report = new_report.replace(find, patch["replace"], 1)
        applied.append({
            "id": patch["id"],
            "intent": patch.get("intent", ""),
            "find": find,
            "replace": patch["replace"],
        })

    manifest = list_versions(job_id)
    parent_version = manifest[-1]["version"] if manifest else None
    new_version = _next_version(manifest)
    (_versions_dir(job_id) / f"{new_version}.md").write_text(new_report, encoding="utf-8")
    report_path.write_text(new_report, encoding="utf-8")

    summaries = [t.get("summary") or "" for t, _ in targets]
    summary_line = next((s for s in summaries if s), "应用了 " + str(len(applied)) + " 条补丁")
    manifest.append({
        "version": new_version,
        "parent": parent_version,
        "timestamp": utc_now(),
        "summary": summary_line,
        "applied_patches": applied,
    })
    write_json(_manifest_path(job_id), manifest)

    for turn, patch in targets:
        patch["status"] = "applied"
        patch["applied_version"] = new_version
    write_json(_session_path(job_id), session)
    return get_state(job_id)


def reject_patches(job_id, patch_ids):
    if not patch_ids:
        raise RuntimeError("未选择补丁")
    session = load_session(job_id)
    rejected = 0
    for pid in patch_ids:
        turn, patch = _find_patch(session, pid)
        if patch:
            patch["status"] = "rejected"
            rejected += 1
    write_json(_session_path(job_id), session)
    return get_state(job_id)


def rollback(job_id, version):
    manifest = list_versions(job_id)
    if not any(v["version"] == version for v in manifest):
        raise RuntimeError(f"版本不存在：{version}")
    content = read_version(job_id, version)
    _report_path(job_id).write_text(content, encoding="utf-8")
    manifest.append({
        "version": _next_version(manifest),
        "parent": version,
        "timestamp": utc_now(),
        "summary": f"回滚到 {version}",
        "applied_patches": [],
        "rollback_to": version,
    })
    write_json(_manifest_path(job_id), manifest)
    new_path = _versions_dir(job_id) / f"{manifest[-1]['version']}.md"
    new_path.write_text(content, encoding="utf-8")
    return get_state(job_id)


def reset_session(job_id):
    write_json(_session_path(job_id), {"history": [], "pending": []})
    return get_state(job_id)
