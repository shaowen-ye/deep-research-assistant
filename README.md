# Gemini Deep Research Local App

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Stdlib only](https://img.shields.io/badge/runtime-stdlib%20only-informational)](#架构)

一个本地 Python Web GUI，用来调用 [Gemini Deep Research][gdr] 与 OpenAI
兼容的 Chat Completions（DeepSeek / OpenAI / OpenRouter）完成研究任务，
并把过程、报告、图表、原始 API 返回归档到本机。

> [English version](#english) is available below.

---

## 亮点

- **Gemini Deep Research 端到端**：长任务、SSE 流式、协作规划、引用、
  正文嵌入图表、计划批准与修订循环。
- **三个 OpenAI 兼容 fallback**：DeepSeek / OpenAI / OpenRouter 走 Chat
  Completions，适合低成本草稿和后期整理。
- **本地优先**：服务器只监听 `127.0.0.1`；设置、任务状态、产物都保存
  在 `app_data/`，默认已加入 `.gitignore`。
- **每任务可归档**：每次运行产出 Markdown + PDF + 单个 ZIP，包含报告、
  图表、原始 API 返回与过程日志。
- **零构建、零外部运行时依赖**：后端用 Python 标准库 `http.server`，
  前端用原生 HTML / CSS / JS。

## 快速开始

需要 **Python ≥ 3.8**，macOS 或 Linux。PDF 导出额外依赖
[`pandoc`](https://pandoc.org) 与 XeLaTeX 引擎（如
[TeX Live](https://tug.org/texlive/) 或
[MacTeX](https://www.tug.org/mactex/)）；缺少时仍可生成 Markdown。

```bash
git clone https://github.com/shaowen-ye/gemini-deep-research-local-app.git
cd gemini-deep-research-local-app
./run_app.sh                    # 或者：python3 app.py
```

浏览器打开 <http://127.0.0.1:8765>，点击 **API 设置** 填入至少一个
provider 的 key。macOS 也可以双击 `Gemini Deep Research.command`。

自定义 host / port：

```bash
python3 app.py --host 127.0.0.1 --port 8765
```

## 配置

可以在 GUI 弹窗中配置 API key、Base URL 与默认模型，保存在
`app_data/settings.json`；也可以使用环境变量。两者并存时 GUI 中的值
优先。

```bash
export GEMINI_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export OPENAI_API_KEY="..."
export OPENROUTER_API_KEY="..."
```

`app_data/` 已在 `.gitignore` 内，所以 key 与任务产物都不会被推送到
仓库。

### Providers

| Provider | 模式 | 默认模型 | 说明 |
| --- | --- | --- | --- |
| **Gemini Deep Research** | [Interactions API][gdr] | `deep-research-preview-04-2026` | 完整 Deep Research Agent：协作规划、联网搜索、图表、SSE 流式。 |
| DeepSeek | OpenAI 兼容 Chat Completions | `deepseek-v4-pro` | 低成本一次性报告生成。 |
| OpenAI | Chat Completions | `gpt-4.1` | 通用一次性报告生成。 |
| OpenRouter | OpenAI 兼容聚合接口 | `deepseek/deepseek-chat` | 一个端点访问多种模型。 |

只有 Gemini 路径会真正运行 research agent。DeepSeek / OpenAI /
OpenRouter 是单次 Chat Completions 调用，不会自行联网搜索。

## 输出

每次任务保存到 `app_data/jobs/<slug>-<id>/`：

```
state.json                  序列化的任务状态
research_progress.md        流式过程记录、工具调用、引用
research_plan.md            仅协作规划模式下生成
research_report.md          含正文嵌入图表的最终 Markdown 报告
research_report.pdf         pandoc + xelatex 可用时生成
images/                     报告引用的图表
interaction_final.json      API 原始最终响应
```

详情面板提供 Markdown / PDF / ZIP 下载，**规范引用** 用于重排引用编号，
**在 Finder 中显示** 直接打开任务目录。ZIP 把上述所有内容打包，便于
归档或共享。

## 架构

后端是标准库 `ThreadingHTTPServer`，没有 Web 框架，没有构建步骤。
逻辑分散在 `core/`：

| 模块 | 职责 |
| --- | --- |
| `app.py` | CLI 入口，串联 `core.config` 与 `core.server`。 |
| `core/server.py` | HTTP 路由：任务、设置、静态文件、SSE。 |
| `core/config.py` | 数据目录、provider 默认值、settings 读写、密钥掩码。 |
| `core/state.py` | 任务状态磁盘持久化与内存锁。 |
| `core/worker.py` | 任务生命周期、线程、计划批准流程。 |
| `core/gemini.py` | Deep Research Interactions API 与 SSE 事件循环。 |
| `core/chat.py` | DeepSeek / OpenAI / OpenRouter 的 Chat Completions。 |
| `core/citations.py` | 来源元数据抓取与数字编号引用。 |
| `core/exporters.py` | Markdown → PDF（pandoc）与 ZIP 打包。 |
| `core/http_client.py` | 基于 stdlib `urllib` 的最小 JSON 客户端。 |
| `core/common.py` | `utc_now`、`slugify` 与 JSON 文件辅助函数。 |

模块间 import 形成 DAG：
`common → config → state, http_client → citations, exporters → gemini, chat → worker → server`。

前端是 `static/index.html` + `static/app.js` + `static/styles.css`，
原生 JS，无打包工具。

## 贡献

欢迎 Issue 与 Pull Request。

- 保持依赖最少：后端维持 stdlib-only，前端维持无构建。
- 不要破坏 `core/` 的 import DAG，避免循环依赖。
- 不要把 `app_data/` 中任何文件提交到仓库；API key 在
  `app_data/settings.json` 已被 gitignore。
- UI / UX 改动请在 PR 中附上 before / after 截图。
- 沿用现有代码风格，没有强制的 linter。

Bug 报告建议附上：Python 版本、provider、`state.json` 摘要，以及
`research_progress.md` 中的相关片段。

## 许可

MIT，见 [LICENSE](LICENSE)。

[gdr]: https://ai.google.dev/gemini-api/docs/interactions/deep-research

---

<a id="english"></a>

## English

A local Python web GUI for running and archiving deep-research jobs against
[Gemini Deep Research][gdr] and OpenAI-compatible providers
(DeepSeek, OpenAI, OpenRouter). Jobs, settings, and outputs stay on your
machine.

### Highlights

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

### Quickstart

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

### Configuration

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

#### Providers

| Provider | Mode | Default model | Notes |
| --- | --- | --- | --- |
| **Gemini Deep Research** | [Interactions API][gdr] | `deep-research-preview-04-2026` | Full Deep Research Agent: collaborative planning, web search, figures, SSE streaming. |
| DeepSeek | OpenAI-compatible Chat Completions | `deepseek-v4-pro` | Low-cost single-shot report generation. |
| OpenAI | Chat Completions | `gpt-4.1` | Single-shot report generation. |
| OpenRouter | OpenAI-compatible aggregator | `deepseek/deepseek-chat` | Access many models via one endpoint. |

Only the Gemini path runs an actual research agent. DeepSeek / OpenAI /
OpenRouter make a single Chat Completions call against the topic — they do
not search the web on their own.

### Output

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

### Architecture

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

### Contributing

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

### License

MIT — see [LICENSE](LICENSE).
