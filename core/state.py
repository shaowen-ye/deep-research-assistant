import threading

from .common import read_json, utc_now, write_json
from .config import JOBS_DIR


RUNNING = {}
RUNNING_LOCK = threading.Lock()


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
