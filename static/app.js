let jobs = [];
let selectedJob = null;
let activeTab = "progress";
let settings = null;

const $ = (id) => document.getElementById(id);
const FONT_KEY = "deepResearchUiFontSize";
const DEFAULT_FONT_SIZE = 15;
const MIN_FONT_SIZE = 13;
const MAX_FONT_SIZE = 20;

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
  const pdf = data.pandoc && data.xelatex ? "PDF 可用" : "PDF 工具缺失";
  $("health").textContent = `${key} · ${pdf}`;
}

async function loadSettings() {
  settings = await api("/api/settings");
  renderProviderSelect();
  renderSettings();
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

function updateAgentChoices() {
  const providerId = $("provider").value;
  const provider = settings && settings.providers ? settings.providers[providerId] : null;
  const agent = $("agent");
  if (!provider) return;
  $("collab").disabled = providerId !== "gemini";
  if (providerId !== "gemini") $("collab").checked = false;

  if (providerId === "gemini") {
    agent.innerHTML = `
      <option value="deep-research-preview-04-2026">Deep Research</option>
      <option value="deep-research-max-preview-04-2026">Deep Research Max</option>
    `;
  } else {
    agent.innerHTML = `<option value="${escapeHtml(provider.model)}">${escapeHtml(provider.model)}</option>`;
  }
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
        <input data-field="model" value="${escapeHtml(provider.model)}" />
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
  document.querySelectorAll(".provider-settings").forEach((block) => {
    const id = block.dataset.provider;
    providers[id] = {};
    block.querySelectorAll("[data-field]").forEach((input) => {
      const field = input.dataset.field;
      providers[id][field] = input.type === "checkbox" ? input.checked : input.value.trim();
    });
  });

  const selectedProvider = $("provider").value;
  settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      default_provider: selectedProvider,
      providers,
    }),
  });
  renderProviderSelect();
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
      await loadDetail();
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

  await api(`/api/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" });
  await loadJobs();
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
      const resumeButton = canResumeJob(job)
        ? `<button data-action="resume" data-id="${job.id}">恢复</button>`
        : "";
      const hint = job.local_status === "completed"
        ? "点击查看报告和下载"
        : job.local_status === "awaiting_approval"
          ? "点击审阅研究计划"
          : "点击查看详情";
      return `
        <article class="job${active}" data-id="${job.id}">
          <div class="job-title">${escapeHtml(job.title)}</div>
          <div class="meta">
            <span class="badge">${escapeHtml(job.provider_label || job.provider || "provider")}</span>
            ${badge(job.local_status)}
            ${job.remote_status ? badge(job.remote_status) : ""}
            <span class="badge">${job.image_count || 0} 图</span>
          </div>
          <div class="progress-row">
            <div class="progress-track">
              <div class="progress-fill" style="width:${Number(job.progress_percent || 0)}%"></div>
            </div>
            <span>${Number(job.progress_percent || 0)}%</span>
          </div>
          <div class="status-line">${escapeHtml(job.status_message || job.stage || "")}</div>
          <div class="job-footer">
            <span class="status-line">${hint}</span>
            <div class="compact-actions">${resumeButton}${stopButton}</div>
          </div>
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
    $("content").textContent = "选择一个任务查看内容。";
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
      <button data-action="normalize" data-id="${selectedJob.id}">规范引用</button>
      <button id="revealFolderBtn">在 Finder 中显示</button>
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

applyFontSize(localStorage.getItem(FONT_KEY) || DEFAULT_FONT_SIZE);
loadHealth();
loadSettings();
loadJobs();
setInterval(loadJobs, 10000);
