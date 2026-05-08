import os
from pathlib import Path

from .common import read_json, write_json


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "app_data"
JOBS_DIR = DATA_DIR / "jobs"
SETTINGS_FILE = DATA_DIR / "settings.json"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_AGENT = "deep-research-preview-04-2026"
MAX_AGENT = "deep-research-max-preview-04-2026"
CONNECT_TIMEOUT_SECONDS = 120
POLL_BACKOFF_SECONDS = 5

TAVILY_ENV_KEY = "TAVILY_API_KEY"

PROVIDER_DEFAULTS = {
    "gemini": {
        "label": "Gemini Deep Research",
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/interactions",
        "model": DEFAULT_AGENT,
        "mode": "deep_research",
        "search": "native",
    },
    "anthropic": {
        "label": "Anthropic Claude (Research)",
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-4-7",
        "mode": "anthropic_research",
        "search": "native",
    },
    "deepseek": {
        "label": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "mode": "openai_chat",
        "search": "tavily",
    },
    "openai": {
        "label": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.5",
        "mode": "openai_chat",
        "search": "tavily",
    },
    "openrouter": {
        "label": "OpenRouter",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4.6",
        "mode": "openai_chat",
        "search": "tavily",
    },
}


def ensure_dirs():
    STATIC_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        save_settings(default_settings())


def default_settings():
    return {
        "default_provider": "gemini",
        "tavily_api_key": "",
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
    settings.setdefault("tavily_api_key", defaults["tavily_api_key"])
    settings.setdefault("providers", {})
    for provider, provider_defaults in defaults["providers"].items():
        settings["providers"].setdefault(provider, {})
        for key, value in provider_defaults.items():
            settings["providers"][provider].setdefault(key, value)
    return settings


def get_tavily_key():
    settings = load_settings()
    return (settings.get("tavily_api_key") or "").strip() or os.getenv(TAVILY_ENV_KEY) or ""


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
        "search": defaults.get("search", "none"),
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
            "search": defaults.get("search", "none"),
            "env_key": defaults["env_key"],
            "configured": bool(local_key or env_key),
            "key_source": "GUI" if local_key else ("env" if env_key else ""),
            "masked_key": mask_secret(local_key or env_key),
            "base_url": saved.get("base_url") or defaults["base_url"],
            "model": saved.get("model") or defaults["model"],
        }
    tavily_local = (settings.get("tavily_api_key") or "").strip()
    tavily_env = os.getenv(TAVILY_ENV_KEY) or ""
    return {
        "default_provider": settings.get("default_provider", "gemini"),
        "providers": providers,
        "tavily": {
            "env_key": TAVILY_ENV_KEY,
            "configured": bool(tavily_local or tavily_env),
            "key_source": "GUI" if tavily_local else ("env" if tavily_env else ""),
            "masked_key": mask_secret(tavily_local or tavily_env),
        },
    }


def update_settings(payload):
    settings = load_settings()
    if payload.get("default_provider") in PROVIDER_DEFAULTS:
        settings["default_provider"] = payload["default_provider"]

    if payload.get("clear_tavily_key"):
        settings["tavily_api_key"] = ""
    elif "tavily_api_key" in payload:
        value = payload.get("tavily_api_key")
        if isinstance(value, str) and value.strip():
            settings["tavily_api_key"] = value.strip()

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
