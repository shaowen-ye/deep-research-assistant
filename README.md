# Gemini Deep Research Local App

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Stdlib only](https://img.shields.io/badge/runtime-stdlib%20only-informational)](#architecture)

A local Python web GUI for running and archiving deep-research jobs against
[Gemini Deep Research][gdr] and OpenAI-compatible providers
(DeepSeek, OpenAI, OpenRouter). Jobs, settings, and outputs stay on your
machine.

> [简体中文说明](#中文说明) is at the bottom of this file.

---

## Highlights

- **Gemini Deep Research, end to end** — long-running interactions, SSE
  streaming, collaborative planning, citations, inline figures, plan
  approval/refinement loop.
- **Three OpenAI-compatible fallbacks** — DeepSeek, OpenAI, OpenRouter via
  Chat Completions, useful for cheaper drafts and offline-friendly post-edit.
- **Local-first** — server binds to `127.0.0.1`; settings, job state, and
  generated artifacts live under `app_data/` and are gitignored by default.
- **Per-job archive** — every run produces Markdown + PDF + a single ZIP
  containing reports, figures, raw API responses, and progress logs.
- **No build step, no external runtime deps** — Python stdlib backend
  (`http.server`), vanilla HTML/CSS/JS frontend.

## Quickstart

Requires **Python ≥ 3.8** on macOS or Linux. PDF export additionally needs
[`pandoc`](https://pandoc.org) and an XeLaTeX engine
(e.g. [TeX Live](https://tug.org/texlive/) or
[MacTeX](https://www.tug.org/mactex/)). Markdown still works without them.

```bash
git clone https://github.com/shaowen-ye/gemini-deep-research-local-app.git
cd gemini-deep-research-local-app
./run_app.sh                    # or: python3 app.py
```

Open <http://127.0.0.1:8765>, click **API 设置**, and add at least one
provider key. On macOS you can also double-click
`Gemini Deep Research.command`.

A custom host/port:

```bash
python3 app.py --host 127.0.0.1 --port 8765
```

## Configuration

Provider keys can be configured either in the GUI (stored in
`app_data/settings.json`) or via environment variables. The GUI value wins
when both are set.

```bash
export GEMINI_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export OPENAI_API_KEY="..."
export OPENROUTER_API_KEY="..."
```

`app_data/` is in `.gitignore`, so neither keys nor job outputs are pushed.

### Providers

| Provider | Mode | Default model | Notes |
| --- | --- | --- | --- |
| **Gemini Deep Research** | [Interactions API][gdr] | `deep-research-preview-04-2026` | Full Deep Research Agent: collaborative planning, web search, figures, SSE streaming. |
| DeepSeek | OpenAI-compatible Chat Completions | `deepseek-v4-pro` | Low-cost single-shot report generation. |
| OpenAI | Chat Completions | `gpt-4.1` | Single-shot report generation. |
| OpenRouter | OpenAI-compatible aggregator | `deepseek/deepseek-chat` | Access many models via one endpoint. |

Only the Gemini path runs an actual research agent. DeepSeek / OpenAI /
OpenRouter make a single Chat Completions call against the topic — they do
not search the web on their own.

## Output

Each job is saved to `app_data/jobs/<slug>-<id>/`:

```
state.json                  serialized job state
research_progress.md        streamed thoughts, tool calls, citations
research_plan.md            collaborative-planning mode only
research_report.md          final markdown with inline figures
research_report.pdf         if pandoc + xelatex are available
images/                     figures referenced from the report
interaction_final.json      raw final response from the API
```

The detail pane offers Markdown / PDF / ZIP downloads, a **规范引用**
button to renumber citations, and **在 Finder 中显示** to open the folder.
The ZIP packages everything above for archival or sharing.

## Architecture

The backend is a stdlib `ThreadingHTTPServer`. No web framework, no build
step. Logic is split across `core/`:

| Module | Role |
| --- | --- |
| `app.py` | CLI entrypoint. Wires `core.config` and `core.server`. |
| `core/server.py` | HTTP routing for jobs, settings, static files, SSE. |
| `core/config.py` | Data dirs, provider defaults, settings load/save, secret masking. |
| `core/state.py` | Per-job state on disk + in-memory locks. |
| `core/worker.py` | Job lifecycle, threading, plan approval flow. |
| `core/gemini.py` | Deep Research Interactions API + SSE event loop. |
| `core/chat.py` | OpenAI-compatible Chat Completions for DeepSeek/OpenAI/OpenRouter. |
| `core/citations.py` | Source metadata fetch, numbered references. |
| `core/exporters.py` | Markdown → PDF via pandoc, ZIP packaging. |
| `core/http_client.py` | Minimal stdlib `urllib` JSON wrapper. |
| `core/common.py` | `utc_now`, `slugify`, JSON file helpers. |

Imports form a DAG:
`common → config → state, http_client → citations, exporters → gemini, chat → worker → server`.

The frontend is `static/index.html` + `static/app.js` + `static/styles.css`.
Vanilla JS, no bundler.

## Contributing

Issues and pull requests are welcome.

- Keep dependencies minimal: backend should remain stdlib-only; frontend
  should remain build-less.
- Don't introduce cycles in the `core/` import DAG above.
- Never commit anything from `app_data/`. API keys live in
  `app_data/settings.json` and are gitignored.
- For UI/UX changes, please attach a short before/after screenshot in the
  PR description.
- Match the existing code style; there is no enforced linter.

Bug reports should ideally include: Python version, provider, an
abbreviated `state.json`, and the relevant lines from
`research_progress.md`.

## Acknowledgements

This project was developed with the assistance of AI coding agents:

- **OpenAI Codex** — initial scaffolding of the GUI, backend, and
  Gemini Deep Research integration.
- **Anthropic Claude Code** — subsequent module refactor (`app.py` →
  `core/`), GUI polish, and documentation.

All code has been reviewed and is maintained by [@shaowen-ye](https://github.com/shaowen-ye).

## License

MIT — see [LICENSE](LICENSE).

[gdr]: https://ai.google.dev/gemini-api/docs/interactions/deep-research

---

## 中文说明

一个本地 Python Web GUI，用来调用 [Gemini Deep Research][gdr] 与 OpenAI
兼容的 Chat Completions（DeepSeek / OpenAI / OpenRouter）完成研究任务，
并把过程、报告、图表、原始 API 返回归档到本机。默认只监听
`127.0.0.1`，无外部运行时依赖。

### 快速开始

需要 **Python ≥ 3.8**；PDF 导出额外依赖 `pandoc` 与 `xelatex`，缺失时
仍可生成 Markdown。

```bash
git clone https://github.com/shaowen-ye/gemini-deep-research-local-app.git
cd gemini-deep-research-local-app
./run_app.sh                    # 或者：python3 app.py
```

浏览器打开 <http://127.0.0.1:8765>，在 **API 设置** 中填入任一 provider
的 key。macOS 也可以双击 `Gemini Deep Research.command`。

### Provider

- **Gemini Deep Research**：完整 Deep Research Agent，支持后台长任务、
  协作规划、流式进度、图表与最终报告。
- **DeepSeek / OpenAI / OpenRouter**：OpenAI 兼容 Chat Completions，
  适合低成本生成或后期整理；它们不会主动联网搜索。

### 输出

每次任务的 Markdown / PDF / 图表 / 原始 API 返回都保存在
`app_data/jobs/<slug-id>/`，详情面板提供 Markdown / PDF / ZIP 下载和
"在 Finder 中显示"。`app_data/` 已在 `.gitignore` 内，API key 与产物
不会进入仓库。

### 贡献

欢迎 Issue 与 PR。请保持后端 stdlib-only、前端无构建工具，UI 改动请
在 PR 中附 before/after 截图，不要把 `app_data/` 内任何文件提交进来。
更详细的架构说明见上方 [Architecture](#architecture)。

### 致谢

本仓库代码由 AI 编程助手协作生成：初始版本由 OpenAI Codex 协助搭建，
后续 `core/` 模块拆分与 GUI 优化由 Anthropic Claude Code 协助完成；
所有代码经过 [@shaowen-ye](https://github.com/shaowen-ye) 审阅与维护。

### 许可

MIT，见 [LICENSE](LICENSE)。
