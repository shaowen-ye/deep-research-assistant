import shutil
import threading
import time
import uuid
from datetime import datetime
from urllib import error, request

from .chat import run_chat_job
from .citations import citation_metadata_for_sources, normalize_citations, parse_sources
from .common import append_text, read_json, slugify, utc_now, write_json
from .config import (
    CONNECT_TIMEOUT_SECONDS,
    DATA_DIR,
    DEFAULT_AGENT,
    POLL_BACKOFF_SECONDS,
    PROVIDER_DEFAULTS,
    load_settings,
    provider_config,
    public_settings,
)
from .exporters import write_pdf_from_markdown
from .gemini import build_stream_request, fetch_final_report, handle_event, iter_sse
from .state import (
    RUNNING,
    RUNNING_LOCK,
    bump_progress,
    job_dir,
    load_state,
    normalize_state,
    save_state,
    set_progress,
)


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
    jobs_dir = DATA_DIR / "jobs"
    for path in jobs_dir.iterdir() if jobs_dir.exists() else []:
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
