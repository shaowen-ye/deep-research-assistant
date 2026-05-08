let jobs = [];
let selectedJob = null;
let activeTab = "progress";
let settings = null;

const $ = (id) => document.getElementById(id);

const MODEL_SUGGESTIONS = {
  gemini: ["deep-research-preview-04-2026", "deep-research-max-preview-04-2026"],
  anthropic: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
  deepseek: ["deepseek-v4-pro", "deepseek-chat-v3.1"],
  openai: ["gpt-5.5", "gpt-5.5-pro", "gpt-5.5-mini", "gpt-5"],
  openrouter: [
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.7",
    "openai/gpt-5.5-pro",
    "google/gemini-3.1-flash-lite",
    "x-ai/grok-4.3",
    "deepseek/deepseek-v4-pro",
  ],
};

const FONT_KEY = "deepResearchUiFontSize";
const DEFAULT_FONT_SIZE = 15;
const MIN_FONT_SIZE = 7.5;
const MAX_FONT_SIZE = 22.5;

function applyFontSize(size) {
  const next = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Number(size) || DEFAULT_FONT_SIZE));
  document.documentElement.style.setProperty("--ui-font-size", `${next}px`);
  localStorage.setItem(FONT_KEY, String(next));
  if ($("fontResetBtn")) $("fontResetBtn").textContent = `${Math.round((next / DEFAULT_FONT_SIZE) * 100)}%`;
}

function changeFontSize(delta) {
  const current = Number(localStorage.getItem(FONT_KEY) || DEFAULT_FONT_SIZE);
  applyFontSize(current + delta);
}

function resetFontSize() {
  applyFontSize(DEFAULT_FONT_SIZE);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const contentType = response.headers.get("Content-Type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    throw new Error(data.error || data || response.statusText);
  }
  return data;
}

function badge(value) {
  const text = value || "unknown";
  return `<span class="badge ${text}">${text}</span>`;
}

function fileLink(job, file, label) {
  return `/files/${encodeURIComponent(job.id)}/${file}`;
}

async function loadHealth() {
  const data = await api("/api/health");
  const configured = Object.values(data.providers || {}).filter((item) => item.configured).length;
  const key = configured ? `${configured} 个 API 已配置` : "缺少 API key";
  const tav = data.tavily && data.tavily.configured ? "Tavily ✓" : "Tavily ✗";
  const pdf = data.pandoc && data.xelatex ? "PDF ✓" : "PDF ✗";
  $("health").textContent = `${key} · ${tav} · ${pdf}`;
}

async function loadSettings() {
  settings = await api("/api/settings");
  renderProviderSelect();
  renderTavily();
  renderSettings();
}

function renderTavily() {
  const t = (settings && settings.tavily) || {};
  const status = t.configured
    ? `<span class="badge completed">已配置 ${escapeHtml(t.key_source || "")}</span>`
    : `<span class="badge failed">未配置</span>`;
  const note = t.configured
    ? `Anthropic 用原生 web_search；OpenAI / DeepSeek / OpenRouter 通过 Tavily 联网研究。`
    : `未配置时，OpenAI / DeepSeek / OpenRouter 将退化为单次 Chat（无联网）；Anthropic 不受影响（用原生 web_search）。`;
  const section = $("tavilySection");
  if (!section) return;
  section.innerHTML = `
    <div class="provider-settings tavily">
      <div class="provider-head">
        <strong>搜索引擎 / Tavily API Key</strong>
        ${status}
      </div>
      <p class="note">${note} 申请：<a href="https://tavily.com" target="_blank" rel="noopener">tavily.com</a>（免费 1000 查询 / 月）。</p>
      <label>
        Tavily API Key ${t.configured ? "（已配置）" : ""}
        <input id="tavilyKey" type="password" placeholder="留空则保持现有 key" autocomplete="off" />
      </label>
      <label class="check">
        <input id="tavilyClear" type="checkbox" />
        清除已保存的 Tavily key
      </label>
    </div>
  `;
}

function renderProviderSelect() {
  const providers = settings.providers || {};
  const options = Object.entries(providers)
    .map(([id, provider]) => {
      const selected = id === settings.default_provider ? " selected" : "";
      const suffix = provider.configured ? "" : "（未配置）";
      return `<option value="${id}"${selected}>${provider.label}${suffix}</option>`;
    })
    .join("");
  $("provider").innerHTML = options;
  $("settingsProvider").innerHTML = options;
  $("settingsProvider").value = $("provider").value;
  updateAgentChoices();
}

let lastAgentProvider = null;

function updateAgentChoices() {
  const providerId = $("provider").value;
  const provider = settings && settings.providers ? settings.providers[providerId] : null;
  const agent = $("agent");
  const list = $("agentList");
  if (!provider) return;
  const isGemini = providerId === "gemini";
  $("collabLabel").style.display = isGemini ? "" : "none";
  if (!isGemini) $("collab").checked = false;
  $("includeVisualsText").textContent = isGemini ? "生成图表" : "用表格可视化";

  const suggestions = MODEL_SUGGESTIONS[providerId] || [];
  const merged =
    provider.model && !suggestions.includes(provider.model)
      ? [provider.model, ...suggestions]
      : suggestions.slice();
  if (list) {
    list.innerHTML = merged
      .map((m) => `<option value="${escapeHtml(m)}"></option>`)
      .join("");
  }

  const providerChanged = lastAgentProvider !== providerId;
  const currentValid = agent.value && (merged.includes(agent.value) || agent.value === provider.model);
  if (providerChanged || !currentValid) {
    agent.value = provider.model || merged[0] || "";
  }
  agent.placeholder = provider.model || merged[0] || "model id";
  lastAgentProvider = providerId;
}

function searchBadge(provider) {
  const search = provider.search;
  const tavConfigured = settings && settings.tavily && settings.tavily.configured;
  if (search === "native") {
    return `<span class="badge running">原生联网搜索</span>`;
  }
  if (search === "tavily") {
    return tavConfigured
      ? `<span class="badge completed">已联网（Tavily）</span>`
      : `<span class="badge failed">需 Tavily key 才能联网</span>`;
  }
  return "";
}

function renderSettings() {
  const providers = settings.providers || {};
  const id = $("settingsProvider").value || settings.default_provider || "gemini";
  const provider = providers[id];
  if (!provider) {
    $("settingsList").innerHTML = "";
    return;
  }
  $("settingsList").innerHTML = `
    <div class="provider-settings" data-provider="${id}">
      <div class="provider-head">
        <strong>${escapeHtml(provider.label)}</strong>
        <span class="badge ${provider.configured ? "completed" : "failed"}">
          ${provider.configured ? `已配置 ${provider.key_source}` : "未配置"}
        </span>
        ${searchBadge(provider)}
      </div>
      <label>
        API Key ${provider.configured ? "（已配置）" : ""}
        <input data-field="api_key" type="password" placeholder="留空则保持现有 key" autocomplete="off" />
      </label>
      <label>
        Base URL
        <input data-field="base_url" value="${escapeHtml(provider.base_url)}" />
      </label>
      <label>
        Model / Agent
        <input data-field="model" list="modelSuggestions-${id}" value="${escapeHtml(provider.model)}" />
        <datalist id="modelSuggestions-${id}">
          ${(MODEL_SUGGESTIONS[id] || [])
            .map((m) => `<option value="${escapeHtml(m)}"></option>`)
            .join("")}
        </datalist>
      </label>
      <label class="check">
        <input data-field="clear_key" type="checkbox" />
        清除 GUI 中保存的 key
      </label>
    </div>
  `;
}

async function saveSettings() {
  const providers = {};
  const editedProvider = $("settingsProvider").value;
  document.querySelectorAll(".provider-settings[data-provider]").forEach((block) => {
    const id = block.dataset.provider;
    providers[id] = {};
    block.querySelectorAll("[data-field]").forEach((input) => {
      const field = input.dataset.field;
      providers[id][field] = input.type === "checkbox" ? input.checked : input.value.trim();
    });
  });

  const selectedProvider = $("provider").value;
  const tavilyKey = ($("tavilyKey") && $("tavilyKey").value.trim()) || "";
  const tavilyClear = !!($("tavilyClear") && $("tavilyClear").checked);

  const payload = {
    default_provider: selectedProvider,
    providers,
  };
  if (tavilyClear) payload.clear_tavily_key = true;
  else if (tavilyKey) payload.tavily_api_key = tavilyKey;

  settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderProviderSelect();
  renderTavily();
  $("provider").value = selectedProvider;
  $("settingsProvider").value = editedProvider;
  updateAgentChoices();
  renderSettings();
  await loadHealth();
  $("settingsStatus").textContent = "已保存";
  setTimeout(() => {
    if ($("settingsStatus")) $("settingsStatus").textContent = "";
  }, 2200);
}

async function loadJobs() {
  const data = await api("/api/jobs");
  jobs = data.jobs;
  $("jobCount").textContent = `${jobs.length} 个任务`;
  renderJobs();
  if (selectedJob) {
    const latest = jobs.find((job) => job.id === selectedJob.id);
    if (latest) {
      selectedJob = latest;
      if (activeTab !== "edit") {
        await loadDetail();
      }
    }
  }
}

function isActiveJob(job) {
  return Boolean(job.thread_running) || ["queued", "running", "reconnecting"].includes(job.local_status);
}

function canStopJob(job) {
  return isActiveJob(job) && job.local_status !== "completed";
}

function canResumeJob(job) {
  return !job.thread_running && !["completed", "awaiting_approval", "queued", "running"].includes(job.local_status);
}

async function handleJobAction(id, action) {
  if (action === "delete") {
    if (!confirm("删除这个任务及其本地文件？")) return;
    await api(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (selectedJob && selectedJob.id === id) selectedJob = null;
    await loadJobs();
    return;
  }

  if (action === "normalize") {
    await runNormalizeCitations(id);
    return;
  }

  await api(`/api/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" });
  await loadJobs();
}

async function runNormalizeCitations(id) {
  const ok = confirm(
    "将扫描报告中的 ## 参考文献 / ## References 章节，回抓每条来源的标题、站点、年份与最终跳转链接，" +
      "并按「标题 + URL + 访问日期」重写后重新生成 PDF。\n\n" +
      "首次执行会把当前 research_report.md 备份为 research_report.original.md。\n\n要继续吗？",
  );
  if (!ok) return;

  const button = document.querySelector(`button[data-action="normalize"][data-id="${id}"]`);
  const originalLabel = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "抓取中…";
  }
  showNormalizeBanner({ pending: true });

  let result;
  try {
    result = await api(`/api/jobs/${encodeURIComponent(id)}/normalize`, { method: "POST" });
  } catch (error) {
    showNormalizeBanner({ error: error.message });
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel || "规范引用";
    }
    return;
  }

  await loadJobs();
  showNormalizeBanner(result);
}

function showNormalizeBanner(result) {
  const target = document.getElementById("actions");
  if (!target) return;
  target.querySelectorAll(".normalize-banner").forEach((node) => node.remove());

  const banner = document.createElement("div");
  banner.className = "normalize-banner";

  if (result.pending) {
    banner.classList.add("muted");
    banner.textContent = "正在回抓来源元数据…首次执行可能需要十几秒到一分钟。";
  } else if (result.error) {
    banner.classList.add("warn");
    banner.textContent = `规范引用失败：${result.error}`;
  } else if (result.changed) {
    const pdfNote = result.pdf_ready ? "PDF 已重新生成" : "PDF 重新生成失败（检查 Pandoc / XeLaTeX）";
    banner.classList.add(result.pdf_ready ? "ok" : "warn");
    banner.innerHTML =
      `<strong>已规范 ${result.source_count || 0} 条引用</strong>` +
      `<span> · ${escapeHtml(pdfNote)} · 原文备份已写入 <code>research_report.original.md</code></span>`;
  } else {
    banner.classList.add("muted");
    banner.textContent = `未做修改：${describeNormalizeReason(result.reason)}`;
  }
  target.appendChild(banner);
}

function describeNormalizeReason(reason) {
  if (reason === "report not found") return "尚未生成报告";
  if (reason === "sources not found") return "报告中未发现 ## 参考文献 / ## References 章节";
  if (reason === "no citation changes") return "引用已是规范格式，无需变更";
  return reason || "未知原因";
}

function attachJobActionControls(root = document) {
  root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        await handleJobAction(button.dataset.id, button.dataset.action);
      } catch (error) {
        alert(error.message);
      }
    });
  });
}

function renderJobs() {
  if (!jobs.length) {
    $("jobsList").innerHTML = `<p class="note">还没有研究任务。</p>`;
    return;
  }

  $("jobsList").innerHTML = jobs
    .map((job) => {
      const active = selectedJob && selectedJob.id === job.id ? " active" : "";
      const stopButton = canStopJob(job)
        ? `<button data-action="stop" data-id="${job.id}">停止</button>`
        : "";
      return `
        <article class="job${active}" data-id="${job.id}">
          <div class="job-title">${escapeHtml(job.title)}</div>
          <div class="meta">
            <span class="badge">${escapeHtml(job.provider_label || job.provider || "provider")}</span>
            ${badge(job.local_status)}
            ${job.remote_status && job.remote_status !== job.local_status ? badge(job.remote_status) : ""}
            <span class="badge">${job.image_count || 0} 图</span>
          </div>
          <div class="progress-row">
            <div class="progress-track">
              <div class="progress-fill" style="width:${Number(job.progress_percent || 0)}%"></div>
            </div>
            <span>${Number(job.progress_percent || 0)}%</span>
          </div>
          ${
            job.local_status !== "completed" && (job.status_message || job.stage)
              ? `<div class="status-line">${escapeHtml(job.status_message || job.stage)}</div>`
              : ""
          }
          ${stopButton ? `<div class="compact-actions">${stopButton}</div>` : ""}
        </article>`;
    })
    .join("");

  document.querySelectorAll(".job").forEach((item) => {
    item.addEventListener("click", (event) => {
      if (event.target.closest("button,a")) return;
      selectedJob = jobs.find((job) => job.id === item.dataset.id);
      renderJobs();
      loadDetail();
    });
  });

  attachJobActionControls($("jobsList"));
}

async function loadDetail() {
  if (!selectedJob) {
    $("detailTitle").textContent = "任务详情";
    $("actions").innerHTML = "";
    $("content").innerHTML = `
      <div class="content-empty">
        <div class="content-empty-title">选择左侧任意任务查看详情</div>
        <ul class="content-empty-tips">
          <li><strong>过程</strong>：查看模型思考、工具调用、检索源与生成图表的实时记录</li>
          <li><strong>计划</strong>：协作规划模式下生成的研究计划全文</li>
          <li><strong>报告</strong>：最终 Markdown 报告（含图表与编号引用）</li>
          <li><strong>编辑</strong>：用自然语言指挥 AI 进行多轮 find/replace 精修，支持版本回滚</li>
        </ul>
        <div class="content-empty-hint">还没有任务？在最左侧"新建研究"中填写要求并启动。</div>
      </div>
    `;
    return;
  }

  $("detailTitle").textContent = selectedJob.title;
  const percent = Number(selectedJob.progress_percent || 0);
  const taskControls = `
    <div class="job-actions">
      ${canResumeJob(selectedJob) ? `<button data-action="resume" data-id="${selectedJob.id}">恢复监听</button>` : ""}
      ${canStopJob(selectedJob) ? `<button data-action="stop" data-id="${selectedJob.id}">停止监听</button>` : ""}
      <button data-action="delete" data-id="${selectedJob.id}">删除任务</button>
    </div>
  `;
  const downloadLinks = `
    <div class="job-actions">
      <a class="button" href="${fileLink(selectedJob, "research_progress.md")}" download>过程 MD</a>
      ${
        selectedJob.collaborative_planning
          ? `<a class="button" href="${fileLink(selectedJob, "research_plan.md")}" download>计划 MD</a>`
          : ""
      }
      <a class="button" href="${fileLink(selectedJob, "research_report.md")}" download>报告 MD</a>
      ${
        selectedJob.pdf_ready
          ? `<a class="button" href="${fileLink(selectedJob, "research_report.pdf")}" download>报告 PDF</a>`
          : ""
      }
      <a class="button" href="${fileLink(selectedJob, "research_artifacts.zip")}" download>全部 ZIP</a>
      ${
        selectedJob.citation_normalized
          ? `<a class="button" href="${fileLink(selectedJob, "research_report.original.md")}" download title="规范引用前的报告原始 Markdown">原文备份</a>`
          : ""
      }
      <span class="action-sep" aria-hidden="true"></span>
      <button data-action="normalize" data-id="${selectedJob.id}" title="扫描 ## 参考文献 章节，回抓每条来源的标题/站点/年份/最终跳转链接，按「标题 + URL + 访问日期」重写并重新生成 PDF；原文备份为 research_report.original.md">规范引用</button>
      <button id="revealFolderBtn" title="在 Finder 中显示">打开目录</button>
    </div>
  `;
  const planControls = selectedJob.local_status === "awaiting_approval"
    ? `
      <div class="plan-controls">
        <label>
          修改计划的反馈
          <textarea id="planFeedback" placeholder="例如：减少背景综述，增加近三年文献、方法比较和生态模型案例。"></textarea>
        </label>
        <div class="job-actions">
          <button id="approvePlanBtn" class="primary small">批准计划并开始研究</button>
          <button id="refinePlanBtn">按反馈修改计划</button>
        </div>
      </div>
    `
    : "";
  $("actions").innerHTML = `
    <div class="detail-status">
      <div class="progress-row wide">
        <div class="progress-track">
          <div class="progress-fill" style="width:${percent}%"></div>
        </div>
        <span>${percent}%</span>
      </div>
      <div class="status-line">
        阶段：${escapeHtml(selectedJob.stage || selectedJob.local_status || "unknown")} ·
        ${escapeHtml(selectedJob.status_message || "")}
      </div>
      <div class="meta">
        <span class="badge">计划 ${formatBytes(selectedJob.plan_bytes || 0)}</span>
        <span class="badge">报告 ${formatBytes(selectedJob.report_bytes || 0)}</span>
        <span class="badge">${selectedJob.pdf_ready ? "PDF 已生成" : "PDF 未生成"}</span>
        <span class="badge">${selectedJob.image_count || 0} 图</span>
      </div>
      <div class="status-line">本地文件夹：${escapeHtml(selectedJob.artifact_dir || "")}</div>
      ${taskControls}
    </div>
    ${planControls}
    ${downloadLinks}
  `;
  attachPlanControls();
  attachArtifactControls();
  attachJobActionControls($("actions"));

  if (activeTab === "edit") {
    await renderEditor();
    return;
  }

  const endpoint = activeTab;
  const text = await api(`/api/jobs/${encodeURIComponent(selectedJob.id)}/${endpoint}`);
  if (activeTab === "progress") {
    $("content").classList.add("pre");
    $("content").textContent = text || "暂无过程内容。任务运行时会在这里显示研究计划、阶段摘要和连接恢复信息。";
  } else if (activeTab === "plan") {
    $("content").classList.remove("pre");
    $("content").innerHTML = text
      ? renderMarkdown(text, selectedJob)
      : "研究计划尚未生成。启用协作规划后，计划生成完成会显示在这里。";
  } else {
    $("content").classList.remove("pre");
    $("content").innerHTML = text
      ? renderMarkdown(text, selectedJob)
      : "最终研究报告尚未生成。请等待进度到 100%，完成后会生成 Markdown、PDF 和图片文件。";
  }
}

let editorBusy = false;
const editorDraft = { provider: "", model: "", message: "" };

async function renderEditor() {
  const content = $("content");
  content.classList.remove("pre");
  if (!selectedJob || (selectedJob.report_bytes || 0) === 0) {
    content.innerHTML = "报告尚未生成，无法进入编辑模式。";
    return;
  }
  content.innerHTML = `<div class="editor-loading status-line">载入编辑会话…</div>`;
  let state;
  try {
    state = await api(`/api/jobs/${encodeURIComponent(selectedJob.id)}/edit`);
  } catch (error) {
    content.innerHTML = `<div class="status-line">载入失败：${escapeHtml(error.message)}</div>`;
    return;
  }
  drawEditor(state);
}

function drawEditor(state) {
  const content = $("content");
  const session = state.session || { history: [] };
  const versions = state.versions || [];
  const reportLength = (state.report || "").length;
  const providers = settings && settings.providers ? Object.keys(settings.providers) : [];
  if (!editorDraft.provider) {
    editorDraft.provider = (selectedJob && selectedJob.provider) || $("provider").value || providers[0] || "";
  }
  if (!editorDraft.model) {
    const cfg = settings && settings.providers ? settings.providers[editorDraft.provider] : null;
    editorDraft.model = (cfg && cfg.model) || "";
  }

  const providerOpts = providers
    .map((p) => {
      const label = settings.providers[p].label || p;
      return `<option value="${escapeHtml(p)}" ${p === editorDraft.provider ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");

  const versionRows = versions
    .slice()
    .reverse()
    .map((v, idx) => {
      const isCurrent = idx === 0;
      const restoreBtn = isCurrent
        ? ""
        : `<button class="editor-rollback" data-version="${escapeHtml(v.version)}">回滚到此版本</button>`;
      const tag = isCurrent ? `<span class="badge completed">当前</span>` : "";
      return `
        <li>
          <div class="editor-version-head">
            <strong>${escapeHtml(v.version)}</strong>
            <span class="status-line">${escapeHtml(v.timestamp || "")}</span>
            ${tag}
          </div>
          <div class="editor-version-body">${escapeHtml(v.summary || "")}</div>
          <div class="editor-version-actions">
            <a class="button" href="/api/jobs/${encodeURIComponent(selectedJob.id)}/edit/version/${encodeURIComponent(v.version)}" target="_blank" rel="noopener">查看 MD</a>
            ${restoreBtn}
          </div>
        </li>
      `;
    })
    .join("");

  const chatRows = (session.history || [])
    .map((turn) => renderEditorTurn(turn))
    .join("");

  content.innerHTML = `
    <div class="editor-shell">
      <div class="editor-toolbar">
        <label class="editor-field">
          <span>使用模型</span>
          <select id="editorProvider">${providerOpts}</select>
        </label>
        <label class="editor-field">
          <span>Model</span>
          <input id="editorModel" value="${escapeHtml(editorDraft.model || "")}" placeholder="可选，留空使用默认" />
        </label>
        <button id="editorReset" class="editor-secondary" title="清空对话历史（不影响版本）">清空对话</button>
        <span class="status-line">报告 ${formatBytes(reportLength * 1)}</span>
      </div>
      <div class="editor-main">
        <div id="editorChat" class="editor-chat">${chatRows || '<div class="status-line editor-empty">还没有对话。说出你想改的内容，例如 "把摘要里的时间窗写得更具体" 或 "图表 3 的数据可信度怎么样？"</div>'}</div>
        <div class="editor-resizer editor-resizer-x" data-editor-resizer="x" title="拖动调整版本栏宽度"></div>
        <div class="editor-versions">
          <div class="editor-section-title">版本时间线</div>
          <ul class="editor-version-list">${versionRows}</ul>
        </div>
      </div>
      <div class="editor-resizer editor-resizer-y" data-editor-resizer="y" title="拖动调整输入区高度"></div>
      <form id="editorForm" class="editor-input">
        <textarea id="editorMessage" placeholder="告诉模型你想怎么改：可以是具体到一句话的修改，也可以是结构性建议。" rows="3">${escapeHtml(editorDraft.message || "")}</textarea>
        <div class="editor-input-row">
          <span class="status-line">补丁可以多轮叠加；每条补丁都需要你点一下「应用」才会写入报告。</span>
          <button id="editorSend" class="primary small" type="submit" ${editorBusy ? "disabled" : ""}>${editorBusy ? "对话中…" : "发送"}</button>
        </div>
      </form>
    </div>
  `;

  attachEditorHandlers();
  const chatEl = document.getElementById("editorChat");
  if (chatEl) chatEl.scrollTop = chatEl.scrollHeight;
}

function renderEditorTurn(turn) {
  if (turn.role === "user") {
    return `
      <div class="editor-turn user">
        <div class="editor-turn-head">你</div>
        <div class="editor-turn-body">${escapeHtml(turn.content || "")}</div>
      </div>
    `;
  }
  const provider = turn.provider ? escapeHtml(turn.provider) : "";
  const model = turn.model ? escapeHtml(turn.model) : "";
  const tag = provider ? `${provider}${model ? " · " + model : ""}` : "";
  const proseHtml = renderEditorAssistantText(turn.content || "");
  const patches = (turn.patches || []).map((p) => renderPatchCard(p)).join("");
  const parseError = turn.parse_error
    ? `<div class="editor-parse-error">补丁解析失败：${escapeHtml(turn.parse_error)}</div>`
    : "";
  return `
    <div class="editor-turn assistant">
      <div class="editor-turn-head">模型 ${tag}</div>
      <div class="editor-turn-body">${proseHtml}</div>
      ${patches ? `<div class="editor-patches">${patches}</div>` : ""}
      ${parseError}
    </div>
  `;
}

function renderEditorAssistantText(text) {
  const stripped = text.replace(/```(?:json)?\s*edit-patches[\s\S]*?```/g, "").trim();
  if (!stripped) return '<span class="status-line">（仅返回了补丁，无额外解释）</span>';
  return renderMarkdown(stripped, selectedJob);
}

function renderPatchCard(patch) {
  const id = escapeHtml(patch.id || "");
  const intent = escapeHtml(patch.intent || "");
  const find = escapeHtml((patch.find || "").slice(0, 320));
  const replace = escapeHtml((patch.replace || "").slice(0, 320));
  const longFind = (patch.find || "").length > 320;
  const longReplace = (patch.replace || "").length > 320;
  let actions;
  let badge;
  if (patch.status === "applied") {
    actions = `<span class="status-line">已应用 → ${escapeHtml(patch.applied_version || "")}</span>`;
    badge = `<span class="badge completed">已应用</span>`;
  } else if (patch.status === "rejected") {
    actions = `<span class="status-line">已拒绝</span>`;
    badge = `<span class="badge failed">已拒绝</span>`;
  } else if (patch.match_error) {
    actions = `<span class="status-line">${escapeHtml(patch.match_error)}</span>`;
    badge = `<span class="badge failed">无法应用</span>`;
  } else {
    actions = `
      <button class="primary small editor-apply-btn" data-id="${id}">应用</button>
      <button class="editor-reject-btn" data-id="${id}">拒绝</button>
    `;
    badge = `<span class="badge waiting">待审</span>`;
  }
  return `
    <div class="editor-patch" data-id="${id}">
      <div class="editor-patch-head">
        <span class="editor-patch-id">${id}</span>
        ${badge}
        <span class="editor-patch-intent">${intent}</span>
      </div>
      <div class="editor-patch-diff">
        <div class="editor-patch-side editor-patch-find"><span class="editor-side-label">原文</span><pre>${find}${longFind ? "…" : ""}</pre></div>
        <div class="editor-patch-side editor-patch-replace"><span class="editor-side-label">替换</span><pre>${replace}${longReplace ? "…" : ""}</pre></div>
      </div>
      <div class="editor-patch-actions">${actions}</div>
    </div>
  `;
}

function attachEditorHandlers() {
  const providerSel = document.getElementById("editorProvider");
  const modelInput = document.getElementById("editorModel");
  const messageEl = document.getElementById("editorMessage");
  const form = document.getElementById("editorForm");
  const reset = document.getElementById("editorReset");

  if (providerSel) {
    providerSel.addEventListener("change", () => {
      editorDraft.provider = providerSel.value;
      const cfg = settings && settings.providers ? settings.providers[editorDraft.provider] : null;
      editorDraft.model = (cfg && cfg.model) || "";
      modelInput.value = editorDraft.model;
    });
  }
  if (modelInput) {
    modelInput.addEventListener("input", () => {
      editorDraft.model = modelInput.value.trim();
    });
  }
  if (messageEl) {
    messageEl.addEventListener("input", () => {
      editorDraft.message = messageEl.value;
    });
  }
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      sendEditorMessage();
    });
  }
  if (reset) {
    reset.addEventListener("click", async () => {
      if (!confirm("清空对话历史？版本时间线和已写入报告不受影响。")) return;
      try {
        const state = await api(
          `/api/jobs/${encodeURIComponent(selectedJob.id)}/edit/reset`,
          { method: "POST", body: "{}" },
        );
        editorDraft.message = "";
        drawEditor(state);
      } catch (error) {
        alert(error.message);
      }
    });
  }

  document.querySelectorAll(".editor-apply-btn").forEach((btn) => {
    btn.addEventListener("click", () => editorPatchAction("apply", btn.dataset.id));
  });
  document.querySelectorAll(".editor-reject-btn").forEach((btn) => {
    btn.addEventListener("click", () => editorPatchAction("reject", btn.dataset.id));
  });
  document.querySelectorAll(".editor-rollback").forEach((btn) => {
    btn.addEventListener("click", () => editorRollback(btn.dataset.version));
  });

  setupEditorResizers();
}

const EDITOR_KEYS = { x: "deepResearchEditorSideW", y: "deepResearchEditorInputH" };
const EDITOR_MIN = { x: 180, y: 80 };
const EDITOR_DEFAULT = { x: 240, y: 132 };

function setupEditorResizers() {
  for (const axis of ["x", "y"]) {
    const saved = Number(localStorage.getItem(EDITOR_KEYS[axis]));
    if (saved >= EDITOR_MIN[axis]) {
      document.documentElement.style.setProperty(
        axis === "x" ? "--editor-side-w" : "--editor-input-h",
        `${saved}px`,
      );
    }
  }
  document.querySelectorAll("[data-editor-resizer]").forEach((handle) => {
    handle.addEventListener("mousedown", (event) => beginEditorDrag(event, handle));
  });
}

function beginEditorDrag(event, handle) {
  event.preventDefault();
  const axis = handle.dataset.editorResizer;
  const root = document.documentElement;
  const cssVar = axis === "x" ? "--editor-side-w" : "--editor-input-h";
  const current = parseFloat(getComputedStyle(root).getPropertyValue(cssVar)) || EDITOR_DEFAULT[axis];
  const startPos = axis === "x" ? event.clientX : event.clientY;
  const shell = document.querySelector(".editor-shell");
  const main = document.querySelector(".editor-main");
  let maxSize;
  if (axis === "x") {
    const mainWidth = main.getBoundingClientRect().width;
    maxSize = Math.max(EDITOR_MIN.x, mainWidth - EDITOR_MIN.x - 20);
  } else {
    const shellHeight = shell.getBoundingClientRect().height;
    maxSize = Math.max(EDITOR_MIN.y, shellHeight - 220);
  }

  handle.classList.add("dragging");
  document.body.style.cursor = axis === "x" ? "col-resize" : "row-resize";

  function onMove(ev) {
    const pos = axis === "x" ? ev.clientX : ev.clientY;
    const delta = pos - startPos;
    const next = axis === "x"
      ? Math.max(EDITOR_MIN.x, Math.min(maxSize, current - delta))
      : Math.max(EDITOR_MIN.y, Math.min(maxSize, current - delta));
    root.style.setProperty(cssVar, `${next}px`);
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    handle.classList.remove("dragging");
    document.body.style.cursor = "";
    const finalSize = parseFloat(getComputedStyle(root).getPropertyValue(cssVar));
    if (finalSize) localStorage.setItem(EDITOR_KEYS[axis], String(Math.round(finalSize)));
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

async function sendEditorMessage() {
  if (editorBusy || !selectedJob) return;
  const messageEl = document.getElementById("editorMessage");
  const message = (messageEl ? messageEl.value : editorDraft.message).trim();
  if (!message) {
    alert("请填写修改诉求。");
    return;
  }
  if (!editorDraft.provider) {
    alert("请选择模型。");
    return;
  }
  editorBusy = true;
  const sendBtn = document.getElementById("editorSend");
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.textContent = "对话中…";
  }
  try {
    const state = await api(
      `/api/jobs/${encodeURIComponent(selectedJob.id)}/edit/message`,
      {
        method: "POST",
        body: JSON.stringify({
          message,
          provider: editorDraft.provider,
          model: editorDraft.model || undefined,
        }),
      },
    );
    editorDraft.message = "";
    drawEditor(state);
  } catch (error) {
    alert(error.message);
  } finally {
    editorBusy = false;
    const send = document.getElementById("editorSend");
    if (send) {
      send.disabled = false;
      send.textContent = "发送";
    }
  }
}

async function editorPatchAction(action, patchId) {
  if (!selectedJob || !patchId) return;
  try {
    const state = await api(
      `/api/jobs/${encodeURIComponent(selectedJob.id)}/edit/${action}`,
      {
        method: "POST",
        body: JSON.stringify({ patch_ids: [patchId] }),
      },
    );
    if (action === "apply") {
      // Refresh job stats since report.md changed
      await loadJobs();
    }
    drawEditor(state);
  } catch (error) {
    alert(error.message);
  }
}

async function editorRollback(version) {
  if (!selectedJob || !version) return;
  if (!confirm(`回滚到 ${version}？当前内容会另存为新的版本。`)) return;
  try {
    const state = await api(
      `/api/jobs/${encodeURIComponent(selectedJob.id)}/edit/rollback`,
      { method: "POST", body: JSON.stringify({ version }) },
    );
    await loadJobs();
    drawEditor(state);
  } catch (error) {
    alert(error.message);
  }
}

async function createJob() {
  const payload = {
    title: $("title").value.trim(),
    topic: $("topic").value.trim(),
    provider: $("provider").value,
    agent: $("agent").value,
    model: $("agent").value,
    include_visuals: $("includeVisuals").checked,
    collaborative_planning: $("collab").checked,
  };

  if (!payload.topic) {
    alert("请填写研究要求。");
    return;
  }

  $("createBtn").disabled = true;
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    selectedJob = job;
    await loadJobs();
  } catch (error) {
    alert(error.message);
  } finally {
    $("createBtn").disabled = false;
  }
}

function attachPlanControls() {
  const approve = document.getElementById("approvePlanBtn");
  const refine = document.getElementById("refinePlanBtn");
  if (approve) {
    approve.addEventListener("click", async () => {
      await submitPlanAction("approve");
    });
  }
  if (refine) {
    refine.addEventListener("click", async () => {
      await submitPlanAction("refine");
    });
  }
}

function attachArtifactControls() {
  const reveal = document.getElementById("revealFolderBtn");
  if (!reveal || !selectedJob) return;
  reveal.addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${encodeURIComponent(selectedJob.id)}/reveal`, { method: "POST" });
    } catch (error) {
      alert(error.message);
    }
  });
}

async function submitPlanAction(action) {
  if (!selectedJob) return;
  const feedback = document.getElementById("planFeedback");
  const input = feedback ? feedback.value.trim() : "";
  if (action === "refine" && !input) {
    alert("请先填写修改计划的反馈。");
    return;
  }

  try {
    await api(`/api/jobs/${encodeURIComponent(selectedJob.id)}/${action}`, {
      method: "POST",
      body: JSON.stringify({ input }),
    });
    activeTab = action === "approve" ? "progress" : "plan";
    document.querySelectorAll(".tab").forEach((item) => {
      item.classList.toggle("active", item.dataset.tab === activeTab);
    });
    await loadJobs();
  } catch (error) {
    alert(error.message);
  }
}

function openSettingsModal() {
  $("settingsModal").classList.remove("hidden");
  $("settingsStatus").textContent = "";
  $("settingsProvider").focus();
}

function closeSettingsModal() {
  $("settingsModal").classList.add("hidden");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function renderMarkdown(markdown, job) {
  const lines = markdown.split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let list = null;
  let table = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "), job)}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!list) return;
    html.push(`<${list.type}>${list.items.map((item) => `<li>${inlineMarkdown(item, job)}</li>`).join("")}</${list.type}>`);
    list = null;
  };

  const flushTable = () => {
    if (!table.length) return;
    const rows = table
      .map((row, index) => {
        const cells = row
          .split("|")
          .slice(1, -1)
          .map((cell) => cell.trim());
        if (index === 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))) return "";
        const tag = index === 0 ? "th" : "td";
        return `<tr>${cells.map((cell) => `<${tag}>${inlineMarkdown(cell, job)}</${tag}>`).join("")}</tr>`;
      })
      .filter(Boolean)
      .join("");
    html.push(`<table>${rows}</table>`);
    table = [];
  };

  for (const line of lines) {
    if (/^\s*$/.test(line)) {
      flushParagraph();
      flushList();
      flushTable();
      continue;
    }

    if (/^\|.*\|\s*$/.test(line)) {
      flushParagraph();
      flushList();
      table.push(line);
      continue;
    }

    flushTable();

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2], job)}</h${level}>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (!list || list.type !== "ul") list = { type: "ul", items: [] };
      list.items.push(bullet[1]);
      continue;
    }

    const numbered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (numbered) {
      flushParagraph();
      if (!list || list.type !== "ol") list = { type: "ol", items: [] };
      list.items.push(numbered[1]);
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  flushTable();
  return html.join("");
}

function inlineMarkdown(value, job) {
  let html = escapeHtml(value);
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_match, alt, src) => {
    const cleanSrc = String(src).replace(/^\.?\//, "");
    const url = cleanSrc.startsWith("http")
      ? cleanSrc
      : `/files/${encodeURIComponent(job.id)}/${cleanSrc}`;
    return `<img alt="${escapeHtml(alt)}" src="${escapeHtml(url)}" />`;
  });
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, (_match, label, href) => {
    const className = /^\d+$/.test(label.trim()) ? "citation-link" : "content-link";
    return `<a class="${className}" href="${href}" target="_blank" rel="noreferrer">${label}</a>`;
  });
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  return html;
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", async () => {
    activeTab = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    await loadDetail();
  });
});

$("createBtn").addEventListener("click", createJob);
$("refreshBtn").addEventListener("click", loadJobs);
$("saveSettingsBtn").addEventListener("click", saveSettings);
$("openSettingsBtn").addEventListener("click", openSettingsModal);
$("closeSettingsBtn").addEventListener("click", closeSettingsModal);
$("settingsModal").addEventListener("click", (event) => {
  if (event.target.dataset.closeSettings !== undefined) closeSettingsModal();
});
$("fontDownBtn").addEventListener("click", () => changeFontSize(-1));
$("fontUpBtn").addEventListener("click", () => changeFontSize(1));
$("fontResetBtn").addEventListener("click", resetFontSize);
$("provider").addEventListener("change", () => {
  $("settingsProvider").value = $("provider").value;
  updateAgentChoices();
  renderSettings();
});
$("settingsProvider").addEventListener("change", renderSettings);
document.addEventListener("keydown", (event) => {
  const mod = event.metaKey || event.ctrlKey;
  if (!mod) {
    if (event.key === "Escape" && !$("settingsModal").classList.contains("hidden")) closeSettingsModal();
    return;
  }
  if (event.key === "=" || event.key === "+") {
    event.preventDefault();
    changeFontSize(1);
  } else if (event.key === "-") {
    event.preventDefault();
    changeFontSize(-1);
  } else if (event.key === "0") {
    event.preventDefault();
    resetFontSize();
  }
});

const COL_KEYS = { 1: "deepResearchCol1", 2: "deepResearchCol2" };
const COL_MIN = 240;

applyFontSize(localStorage.getItem(FONT_KEY) || DEFAULT_FONT_SIZE);
setupColumnResizers();
loadHealth();
loadSettings();
loadJobs();
setInterval(loadJobs, 10000);

function setupColumnResizers() {
  for (const [which, key] of Object.entries(COL_KEYS)) {
    const saved = Number(localStorage.getItem(key));
    if (saved >= COL_MIN) {
      document.documentElement.style.setProperty(`--col-${which}`, `${saved}px`);
    }
  }
  document.querySelectorAll(".col-resizer").forEach((handle) => {
    handle.addEventListener("mousedown", (event) => beginColumnDrag(event, handle));
  });
}

function beginColumnDrag(event, handle) {
  event.preventDefault();
  const which = handle.dataset.resizer;
  const layout = document.querySelector(".layout");
  const cols = getComputedStyle(layout).gridTemplateColumns.split(/\s+/).map(parseFloat);
  const startX = event.clientX;
  const startWidth = which === "1" ? cols[0] : cols[2];
  const layoutWidth = layout.getBoundingClientRect().width;
  const otherFixed = which === "1" ? cols[2] : cols[0];
  const handlesWidth = 6 * 2 + 10 * 2;
  const maxWidth = Math.max(COL_MIN, layoutWidth - otherFixed - handlesWidth - COL_MIN);

  handle.classList.add("dragging");
  document.body.style.cursor = "col-resize";

  function onMove(ev) {
    const dx = ev.clientX - startX;
    const next = Math.max(COL_MIN, Math.min(maxWidth, startWidth + dx));
    document.documentElement.style.setProperty(`--col-${which}`, `${next}px`);
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    handle.classList.remove("dragging");
    document.body.style.cursor = "";
    const finalWidth = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(`--col-${which}`));
    if (finalWidth) localStorage.setItem(COL_KEYS[which], String(Math.round(finalWidth)));
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}
