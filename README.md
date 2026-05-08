# Gemini Deep Research Local App

## 中文说明

这是一个本地 Web GUI app，用于创建、管理和导出 AI 研究报告。它支持 Gemini Deep Research，也支持 DeepSeek、OpenAI、OpenRouter 等 OpenAI-compatible Chat Completions 接口。

### 主要功能

- 在浏览器 GUI 中创建、恢复、停止和查看研究任务。
- 支持 Gemini Deep Research 的后台长任务、协作规划、流式进度和最终报告。
- 支持 DeepSeek、OpenAI、OpenRouter 等普通报告生成模式。
- 在 GUI 弹窗中配置 API key、Base URL 和模型名。
- 支持任务阶段、进度条、百分比和状态提示。
- 支持导出 Markdown、PDF 和包含全部文件的 ZIP。
- 支持报告正文数字编号引用，编号可点击打开网页来源。
- 正文文献编号链接显示为蓝色加下划线，方便识别和点击。
- 参考文献清单保持简洁，每条保留标题或来源名、网页链接和访问时间。
- 支持报告中的图、表插入到正文相关位置。
- 支持字体大小调整：`A- / 100% / A+`，以及 `Cmd +`、`Cmd -`、`Cmd 0`。

### 启动

终端方式：

```bash
./run_app.sh
```

浏览器打开：

```text
http://127.0.0.1:8765
```

macOS 双击方式：

```text
Gemini Deep Research.command
```

### 配置 API

可以在 GUI 右上角的 `API 设置` 弹窗中配置 API key、Base URL 和模型名。设置只保存在本地：

```text
app_data/settings.json
```

`app_data/` 已加入 `.gitignore`，不会提交到 GitHub。

也可以使用 shell 环境变量：

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export DEEPSEEK_API_KEY="your-deepseek-api-key"
export OPENAI_API_KEY="your-openai-api-key"
export OPENROUTER_API_KEY="your-openrouter-api-key"
```

GUI 中保存的 key 优先级高于环境变量。

### Provider

```text
Gemini Deep Research: 完整 Deep Research Agent，支持后台长任务、协作规划、图表和最终报告。
DeepSeek: OpenAI-compatible Chat Completions，用于低成本报告生成/整理，默认模型为 deepseek-v4-pro。
OpenAI: OpenAI Chat Completions，用于普通报告生成/整理。
OpenRouter: OpenAI-compatible 聚合接口，用于接入多种模型。
```

注意：DeepSeek/OpenAI/OpenRouter 当前是通用报告生成模式，不是 Google 的 Deep Research Agent。它们不会自动拥有 Gemini Deep Research 的专用搜索和后台 Agent 能力，除非后续再给 app 加搜索抓取流水线。

### 输出文件

每个任务保存到：

```text
app_data/jobs/
```

任务目录里通常包含：

```text
state.json
research_progress.md
research_plan.md
research_report.md
research_report.pdf
images/
interaction_final.json
```

报告完成后，任务详情里会提供：

```text
下载过程 MD
下载计划 MD
下载报告 MD
下载报告 PDF
下载全部 ZIP
在 Finder 中显示
```

`下载全部 ZIP` 会把 Markdown、PDF、图片、过程记录和原始 API 返回一起打包。

### PDF 导出

PDF 导出依赖系统命令：

```text
pandoc
xelatex
```

如果缺少这些工具，报告仍会生成 Markdown，PDF 会跳过。

### 本地范围

- `app_data/`、`.venv/`、生成的报告与原始 API 返回都已在 `.gitignore` 内，留在本机。
- 默认只监听 `127.0.0.1`，没有鉴权层，适合个人本机使用；若改成对外监听，请自行加鉴权。

---

## English

This is a local Web GUI app for creating, managing, and exporting AI research reports. It supports Gemini Deep Research, as well as OpenAI-compatible Chat Completions providers such as DeepSeek, OpenAI, and OpenRouter.

### Features

- Create, resume, stop, and inspect research jobs from a browser GUI.
- Supports Gemini Deep Research background jobs, collaborative planning, streaming progress, and final reports.
- Supports DeepSeek, OpenAI, and OpenRouter for general report generation.
- Configure API keys, Base URLs, and model names in a GUI settings modal.
- Shows job stages, progress bars, percentages, and status messages.
- Exports Markdown, PDF, and a ZIP archive with all job artifacts.
- Uses numeric citations in report body text, with clickable source links.
- Citation numbers are shown as blue underlined links in the GUI.
- Keeps the reference list concise: title or source name, URL, and access date.
- Places figures and tables near the relevant body text instead of collecting them at the end.
- Supports font scaling via `A- / 100% / A+`, `Cmd +`, `Cmd -`, and `Cmd 0`.

### Start

From terminal:

```bash
./run_app.sh
```

Then open:

```text
http://127.0.0.1:8765
```

On macOS, you can also double-click:

```text
Gemini Deep Research.command
```

### API Configuration

Use the `API 设置` settings modal in the upper-right corner to configure API keys, Base URLs, and model names. Settings are stored locally:

```text
app_data/settings.json
```

`app_data/` is ignored by Git and should not be pushed to GitHub.

You can also use shell environment variables:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export DEEPSEEK_API_KEY="your-deepseek-api-key"
export OPENAI_API_KEY="your-openai-api-key"
export OPENROUTER_API_KEY="your-openrouter-api-key"
```

Keys saved in the GUI take priority over environment variables.

### Providers

```text
Gemini Deep Research: full Deep Research Agent with background jobs, collaborative planning, figures, and final reports.
DeepSeek: OpenAI-compatible Chat Completions for lower-cost report generation, default model deepseek-v4-pro.
OpenAI: OpenAI Chat Completions for general report generation.
OpenRouter: OpenAI-compatible aggregation endpoint for multiple models.
```

Note: DeepSeek, OpenAI, and OpenRouter currently run in general report generation mode. They do not automatically provide Gemini Deep Research's dedicated search and background-agent capabilities unless a separate search pipeline is added later.

### Output Files

Each job is saved under:

```text
app_data/jobs/
```

A job folder usually contains:

```text
state.json
research_progress.md
research_plan.md
research_report.md
research_report.pdf
images/
interaction_final.json
```

After a report completes, the job detail panel provides:

```text
Download progress Markdown
Download plan Markdown
Download report Markdown
Download report PDF
Download all as ZIP
Show in Finder
```

The ZIP export includes Markdown, PDF, images, progress logs, and raw API responses for that job.

### PDF Export

PDF export depends on these system commands:

```text
pandoc
xelatex
```

If they are missing, Markdown reports still work, and PDF generation is skipped.

### Local Scope

- `app_data/`, `.venv/`, generated reports, and raw API responses are all in `.gitignore` and stay on the local machine.
- The server listens on `127.0.0.1` by default and has no authentication layer; add one if you bind to a non-loopback interface.
