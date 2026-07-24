const state = {
  files: { a: null, b: null },
  session: null,
  activeTab: "PID-A",
  filter: "all",
  maxFileBytes: 75 * 1024 * 1024
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const screens = ["upload-screen", "analysis-screen", "workspace-screen"];

function showScreen(id) {
  screens.forEach(name => $("#" + name).classList.toggle("active", name === id));
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[character]);
}

function setFile(slot, file) {
  if (file && !/\.(pdf|dwg)$/i.test(file.name)) {
    $("#form-error").textContent = "Use a native PDF, scanned PDF, or DWG file.";
    return;
  }
  if (file && file.size > state.maxFileBytes) {
    const limitMb = Math.floor(state.maxFileBytes / (1024 * 1024));
    $("#form-error").textContent = `${file.name} exceeds the ${limitMb} MB deployment limit.`;
    return;
  }
  state.files[slot] = file || null;
  const zone = slot === "a" ? $("#drop-a") : $("#drop-b");
  zone.classList.toggle("ready", !!file);
  if (file) {
    $(".selected-file b", zone).textContent = file.name;
    $(".selected-file small", zone).textContent = `${formatBytes(file.size)} · ${file.name.split(".").pop().toUpperCase()}`;
    $(".file-badge", zone).textContent = file.name.split(".").pop().toUpperCase();
  }
  $("#compare-button").disabled = !(state.files.a && state.files.b);
  $("#form-error").textContent = "";
}

["a", "b"].forEach(slot => {
  const input = $(`#file-${slot}`);
  const zone = $(`#drop-${slot}`);
  input.addEventListener("change", () => setFile(slot, input.files[0]));
  ["dragenter", "dragover"].forEach(event => zone.addEventListener(event, e => {
    e.preventDefault(); zone.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach(event => zone.addEventListener(event, e => {
    e.preventDefault(); zone.classList.remove("dragover");
  }));
  zone.addEventListener("drop", event => setFile(slot, event.dataTransfer.files[0]));
  $(".selected-file button", zone).addEventListener("click", event => {
    event.preventDefault(); event.stopPropagation(); input.value = ""; setFile(slot, null);
  });
});

$("#swap-files").addEventListener("click", () => {
  const original = state.files.a;
  setFile("a", state.files.b);
  setFile("b", original);
});

function animateAnalysis() {
  const steps = [
    [12, "Reading both documents"],
    [38, "Aligning document content"],
    [66, "Finding changes"],
    [88, "Preparing your workspace"]
  ];
  let index = 1;
  $("#progress-bar").style.width = `${steps[0][0]}%`;
  $("#analysis-percent").textContent = `${steps[0][0]}%`;
  $(".analysis-progress").setAttribute("aria-valuenow", steps[0][0]);
  const timer = setInterval(() => {
    const step = steps[Math.min(index, steps.length - 1)];
    $("#progress-bar").style.width = `${step[0]}%`;
    $("#analysis-percent").textContent = `${step[0]}%`;
    $(".analysis-progress").setAttribute("aria-valuenow", step[0]);
    $("#analysis-heading").textContent = step[1];
    index += 1;
  }, 650);
  return () => {
    clearInterval(timer);
    $("#progress-bar").style.width = "100%";
    $("#analysis-percent").textContent = "100%";
    $(".analysis-progress").setAttribute("aria-valuenow", "100");
    $("#analysis-heading").textContent = "Comparison ready";
  };
}

$("#upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.files.a || !state.files.b) return;
  showScreen("analysis-screen");
  const stopAnimation = animateAnalysis();
  const data = new FormData();
  data.append("file_a", state.files.a);
  data.append("file_b", state.files.b);
  try {
    const response = await fetch("/api/compare", { method: "POST", body: data });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Comparison failed.");
    state.session = payload;
    localStorage.setItem("deltascope-session-id", payload.id);
    localStorage.removeItem("deltascope-skip-restore");
    stopAnimation();
    await new Promise(resolve => setTimeout(resolve, 420));
    renderWorkspace();
    showScreen("workspace-screen");
  } catch (error) {
    stopAnimation();
    showScreen("upload-screen");
    $("#form-error").textContent = error.message;
  }
});

function renderWorkspace() {
  const session = state.session;
  const a = session.documents["PID-A"], b = session.documents["PID-B"], report = session.report;
  $("#workspace-title").textContent = `${a.filename} → ${b.filename}`;
  $("#workspace-subtitle").textContent = `${a.format.replaceAll("_", " ")} · ${b.format.replaceAll("_", " ")}`;
  $("#sidebar-current-title").textContent = `${a.filename} → ${b.filename}`;
  $("#tab-a-name").textContent = a.filename;
  $("#tab-b-name").textContent = b.filename;
  const total = Object.values(report.counts).reduce((sum, value) => sum + value, 0);
  $("#sidebar-current-meta").textContent = `${total} findings · ${Math.round(report.alignment.score * 100)}% aligned`;
  $("#tab-delta-count").textContent = `${total} findings`;
  $("#count-total").textContent = total;
  $("#count-critical").textContent = report.findings.filter(finding => finding.severity === "critical").length;
  $("#alignment-score").textContent = `${Math.round(report.alignment.score * 100)}%`;
  $("#alignment-banner").textContent = report.alignment.message;
  $("#alignment-banner").classList.toggle("good", report.alignment.status === "aligned");
  $("#export-json").href = session.links.report_json;
  $("#export-md").href = session.links.report_markdown;
  $("#export-html").href = session.links.report_html;
  $("#export-markup-a").href = session.links.markup_base || "#";
  $("#export-markup-b").href = session.links.markup_revised || "#";
  $("#export-markup-a").classList.toggle("hidden", !session.links.markup_base);
  $("#export-markup-b").classList.toggle("hidden", !session.links.markup_revised);
  renderFindings();
  switchDocument("PID-A");
}

function renderFindings() {
  const findings = state.session.report.findings.filter(finding => state.filter === "all" || finding.change_type === state.filter);
  $("#review-count").textContent = `${findings.length} ${findings.length === 1 ? "finding" : "findings"}`;
  $("#finding-list").innerHTML = findings.map(finding => {
    const reference = finding.after || finding.before || {};
    const confidence = Math.round(finding.confidence * 100);
    return `<article class="finding-card ${finding.change_type}" data-finding="${escapeHtml(finding.id)}" data-source="${escapeHtml(reference.source || "")}" data-page="${reference.page || 1}" data-block="${escapeHtml(reference.block_id || "")}">
      <div class="finding-top"><div><span class="finding-id">${escapeHtml(finding.id)}</span><span class="change-pill ${finding.change_type}">${finding.change_type}</span></div><span class="severity-pill ${finding.severity}">${finding.severity}</span></div>
      <p>${escapeHtml(finding.description)}</p>
      <div class="finding-foot"><span>${escapeHtml(finding.item_type)} · ${escapeHtml(reference.source || "source")} · page ${reference.page || "—"}</span><span class="confidence"><i><b style="width:${confidence}%"></b></i>${confidence}%</span></div>
    </article>`;
  }).join("") || '<div class="alignment-banner good">No findings in this category.</div>';
  $$(".finding-card").forEach(card => card.addEventListener("click", () => openCitation(card.dataset.source, card.dataset.page, card.dataset.block)));
}

function switchDocument(tab, page = 1, highlightBlockId = "") {
  state.activeTab = tab;
  $$(".document-tab").forEach(button => button.classList.toggle("active", button.dataset.tab === tab));
  $("#delta-viewer").classList.toggle("hidden", tab !== "DELTA");
  $("#document-frame").classList.toggle("hidden", tab === "DELTA");
  $("#dwg-viewer").classList.add("hidden");
  if (tab === "DELTA") {
    $("#viewer-label").textContent = "Structured delta report";
    $("#page-label").textContent = `${state.session.report.findings.length} findings`;
    return;
  }
  const document = state.session.documents[tab];
  const link = tab === "PID-A" ? state.session.links.base : state.session.links.revised;
  $("#viewer-label").textContent = `${tab} · ${document.filename}`;
  $("#page-label").textContent = `Page ${page} of ${document.metadata.page_count || document.pages.length}`;
  if (document.format === "dwg") {
    if (document.metadata.geometry_available) {
      $("#document-frame").classList.remove("hidden");
      const query = new URLSearchParams({ page: String(page) });
      if (highlightBlockId) query.set("block_id", highlightBlockId);
      $("#document-frame").src = `/api/sessions/${state.session.id}/documents/${tab}/view.svg?${query}`;
    } else {
      $("#document-frame").classList.add("hidden");
      $("#dwg-viewer").classList.remove("hidden");
    }
  } else {
    $("#document-frame").classList.remove("hidden");
    const source = highlightBlockId
      ? `/api/sessions/${state.session.id}/documents/${tab}/highlight?block_id=${encodeURIComponent(highlightBlockId)}`
      : link;
    $("#document-frame").src = `${source}#page=${page}&view=FitH`;
  }
}

$$(".document-tab").forEach(button => button.addEventListener("click", () => switchDocument(button.dataset.tab)));
$$(".delta-filters button").forEach(button => button.addEventListener("click", () => {
  $$(".delta-filters button").forEach(item => item.classList.remove("active"));
  button.classList.add("active"); state.filter = button.dataset.filter; renderFindings();
}));

function openCitation(source, page, blockId = "", targetSource = "") {
  const visualSource = targetSource || source;
  if (visualSource === "PID-A" || visualSource === "PID-B") {
    switchDocument(visualSource, Number(page || 1), blockId);
  } else {
    switchDocument("DELTA");
  }
}

$("#open-document").addEventListener("click", () => {
  if (!state.session || state.activeTab === "DELTA") return;
  const link = state.activeTab === "PID-A" ? state.session.links.base : state.session.links.revised;
  window.open(link, "_blank", "noopener");
});

$("#export-button").addEventListener("click", () => $(".export-popover").classList.toggle("open"));
document.addEventListener("click", event => {
  if (!event.target.closest(".export-menu")) $(".export-popover").classList.remove("open");
});

function addMessage(role, content, citations = []) {
  const thread = $("#chat-thread");
  const shouldFollow = role === "user" || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 100;
  const element = document.createElement("div");
  element.className = `message ${role}`;
  if (role === "user") {
    element.innerHTML = `<div class="bubble"><p>${escapeHtml(content)}</p></div>`;
  } else {
    element.innerHTML = `<div class="avatar">✦</div><div class="bubble"><p>${escapeHtml(content)}</p>
      <div class="citation-list">${citations.map(citation => `<button class="citation-chip" data-source="${escapeHtml(citation.source)}" data-target-source="${escapeHtml(citation.target_source || "")}" data-block="${escapeHtml(citation.target_block_id || "")}" data-page="${citation.page || 1}">${escapeHtml(citation.id)} · ${escapeHtml(citation.target_source || citation.source)} · p.${citation.page || "—"}</button>`).join("")}</div></div>`;
    $$(".citation-chip", element).forEach(button => button.addEventListener("click", () => openCitation(button.dataset.source, button.dataset.page, button.dataset.block, button.dataset.targetSource)));
  }
  thread.append(element);
  if (shouldFollow) {
    requestAnimationFrame(() => thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" }));
  }
  requestAnimationFrame(updateChatJumpButton);
  return element;
}

const questionField = $("#question");
const chatThread = $("#chat-thread");

function updateChatJumpButton() {
  const distance = chatThread.scrollHeight - chatThread.scrollTop - chatThread.clientHeight;
  $("#scroll-to-bottom").classList.toggle("hidden", distance < 110);
}

chatThread.addEventListener("scroll", updateChatJumpButton, { passive: true });
$("#scroll-to-bottom").addEventListener("click", () => {
  chatThread.scrollTo({ top: chatThread.scrollHeight, behavior: "smooth" });
});

function resizeQuestionField() {
  questionField.style.height = "auto";
  const maximumHeight = Number.parseFloat(getComputedStyle(questionField).maxHeight) || 150;
  const nextHeight = Math.min(questionField.scrollHeight, maximumHeight);
  questionField.style.height = `${nextHeight}px`;
  questionField.style.overflowY = questionField.scrollHeight > maximumHeight ? "auto" : "hidden";
}

questionField.addEventListener("input", resizeQuestionField);
questionField.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (questionField.value.trim()) $("#chat-form").requestSubmit();
  }
});

$("#chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  const field = $("#question"), question = field.value.trim();
  if (!question || !state.session) return;
  addMessage("user", question); field.value = ""; resizeQuestionField();
  const pending = addMessage("assistant", "Searching both documents and the delta report...");
  try {
    const response = await fetch(`/api/sessions/${state.session.id}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error);
    $("#provider-label").textContent = payload.provider.startsWith("fireworks:")
      ? "Fireworks · grounded in both files + delta"
      : payload.provider_error
        ? "Grounded fallback · hosted model unavailable"
        : "Grounded fallback · both files + delta";
    pending.remove(); addMessage("assistant", payload.answer, payload.citations);
  } catch (error) {
    pending.remove(); addMessage("assistant", `I could not complete that request: ${error.message}`);
  }
});

$$(".starter-questions button").forEach(button => button.addEventListener("click", () => {
  $("#question").value = button.textContent; $("#chat-form").requestSubmit();
}));

async function loadObservability() {
  const [metricsResponse, tracesResponse] = await Promise.all([
    fetch(`/api/sessions/${state.session.id}/metrics`),
    fetch(`/api/sessions/${state.session.id}/traces`)
  ]);
  if (!metricsResponse.ok || !tracesResponse.ok) throw new Error("Telemetry is temporarily unavailable.");
  const [metrics, traceData] = await Promise.all([metricsResponse.json(), tracesResponse.json()]);
  const cards = [
    ["Requests", metrics.requests],
    ["Avg latency", `${metrics.avg_latency_ms} ms`],
    ["Retrieval hits", metrics.retrieval_hits],
    ["LLM tokens", metrics.input_tokens + metrics.output_tokens],
    ["Model cost", `$${Number(metrics.estimated_cost_usd).toFixed(4)}`]
  ];
  $("#metric-grid").innerHTML = cards.map(([label, value]) => `<div class="metric-card"><span>${label}</span><b>${value}</b></div>`).join("");
  $("#trace-list").innerHTML = traceData.traces.length
    ? traceData.traces.map(trace => `<div class="trace-row"><span>${escapeHtml(trace.request)} · ${trace.trace_id.slice(0, 8)}</span><span>${trace.spans.map(span => escapeHtml(span.name)).join(" → ")}</span><span>${trace.duration_ms} ms</span><span class="trace-status ${trace.status}">${trace.status}</span></div>`).join("")
    : '<div class="trace-row"><span>Telemetry unavailable</span><span>No persisted trace was found.</span><span>—</span><span class="trace-status error">missing</span></div>';
}

async function openObservability() {
  try {
    await loadObservability();
  } catch (error) {
    $("#metric-grid").innerHTML = '<div class="metric-card"><span>Status</span><b>Unavailable</b></div>';
    $("#trace-list").innerHTML = `<div class="trace-row"><span>Telemetry error</span><span>${escapeHtml(error.message)}</span><span>—</span><span class="trace-status error">error</span></div>`;
  }
  $("#observability-dialog").showModal();
}

$("#observability-button").addEventListener("click", openObservability);
$("#sidebar-observability").addEventListener("click", openObservability);
$("#close-observability").addEventListener("click", () => $("#observability-dialog").close());

function setSidebarCollapsed(collapsed) {
  const shell = $("#workspace-shell");
  const toggle = $("#sidebar-toggle");
  shell.classList.toggle("sidebar-collapsed", collapsed);
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  localStorage.setItem("deltascope-sidebar-collapsed", collapsed ? "1" : "0");
}

setSidebarCollapsed(localStorage.getItem("deltascope-sidebar-collapsed") === "1");
$("#sidebar-toggle").addEventListener("click", () => {
  setSidebarCollapsed(!$("#workspace-shell").classList.contains("sidebar-collapsed"));
});
$("#sidebar-workspace").addEventListener("click", () => $("#question").focus());
$("#sidebar-export").addEventListener("click", event => {
  event.stopPropagation();
  $(".export-popover").classList.add("open");
  $("#export-button").focus();
});

function resetComparison() {
  state.session = null; state.files = { a: null, b: null }; state.activeTab = "PID-A";
  localStorage.setItem("deltascope-skip-restore", "1");
  localStorage.removeItem("deltascope-session-id");
  $("#file-a").value = ""; $("#file-b").value = ""; setFile("a", null); setFile("b", null);
  $("#chat-thread").querySelectorAll(".message:not(:first-child)").forEach(message => message.remove());
  chatThread.scrollTop = 0;
  updateChatJumpButton();
  showScreen("upload-screen");
}
$("#new-comparison").addEventListener("click", resetComparison);

async function restoreLatestComparison() {
  if (localStorage.getItem("deltascope-skip-restore") === "1") return;
  try {
    const sessionId = localStorage.getItem("deltascope-session-id");
    const endpoint = sessionId ? `/api/sessions/${encodeURIComponent(sessionId)}` : "/api/sessions/latest";
    const response = await fetch(endpoint);
    if (!response.ok) return;
    state.session = await response.json();
    localStorage.setItem("deltascope-session-id", state.session.id);
    renderWorkspace();
    showScreen("workspace-screen");
  } catch {
    // A missing or expired local session should simply show the uploader.
  }
}

async function initializeRuntime() {
  try {
    const response = await fetch("/api/health");
    if (response.ok) {
      const runtime = await response.json();
      if (runtime.max_file_bytes) state.maxFileBytes = runtime.max_file_bytes;
    }
  } catch {
    // Built-in defaults remain usable if the capability check is unavailable.
  }
  await restoreLatestComparison();
}
initializeRuntime();
