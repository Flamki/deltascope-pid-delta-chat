const state = {
  files: { a: null, b: null },
  session: null,
  activeTab: "PID-A",
  viewPage: 1,
  zoom: 1,
  selectionMode: false,
  selection: null,
  pendingSelection: null,
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
  const overlayAvailable = !(
    (a.format === "dwg" && !a.metadata.geometry_available)
    || (b.format === "dwg" && !b.metadata.geometry_available)
  );
  $(".overlay-tab").disabled = !overlayAvailable;
  $(".overlay-tab").title = overlayAvailable ? "Overlay both documents" : "Overlay requires renderable PDF or DWG geometry";
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

function documentPageCount(pid) {
  const document = state.session.documents[pid];
  return Number(document.metadata.page_count || document.pages.length || 1);
}

function drawingUrl(pid, page, highlightBlockId = "") {
  const document = state.session.documents[pid];
  if (document.format === "dwg") {
    const query = new URLSearchParams({ page: String(page) });
    if (highlightBlockId) query.set("block_id", highlightBlockId);
    return `/api/sessions/${state.session.id}/documents/${pid}/view.svg?${query}`;
  }
  const query = new URLSearchParams({ page: String(page), scale: "1" });
  if (highlightBlockId) query.set("block_id", highlightBlockId);
  return `/api/sessions/${state.session.id}/documents/${pid}/render.png?${query}`;
}

function selectionPreviewUrl(selection) {
  const document = state.session.documents[selection.source];
  if (document.format === "dwg") {
    return drawingUrl(selection.source, selection.page);
  }
  const query = new URLSearchParams({
    page: String(selection.page),
    scale: "3",
    x0: String(selection.region.x0),
    y0: String(selection.region.y0),
    x1: String(selection.region.x1),
    y1: String(selection.region.y1)
  });
  return `/api/sessions/${state.session.id}/documents/${selection.source}/render.png?${query}`;
}

function clearCanvasSelection() {
  state.selection = null;
  $("#selection-box").classList.add("hidden");
  $("#ask-selection").classList.add("hidden");
}

function setSelectionMode(enabled) {
  state.selectionMode = enabled;
  $("#select-region").classList.toggle("active", enabled);
  $("#selection-layer").classList.toggle("active", enabled);
  $("#select-region span").textContent = enabled ? "Drag on drawing" : "Select area";
  if (!enabled) clearCanvasSelection();
}

function renderDrawing(highlightBlockId = "") {
  const tab = state.activeTab;
  const isOverlay = tab === "OVERLAY";
  const primaryPid = isOverlay ? "PID-A" : tab;
  const primaryDocument = state.session.documents[primaryPid];
  const pageCount = isOverlay
    ? Math.min(documentPageCount("PID-A"), documentPageCount("PID-B"))
    : documentPageCount(primaryPid);
  state.viewPage = Math.min(Math.max(1, state.viewPage), pageCount);
  $("#page-label").textContent = `Page ${state.viewPage} of ${pageCount}`;
  $("#previous-page").disabled = state.viewPage <= 1;
  $("#next-page").disabled = state.viewPage >= pageCount;
  $("#overlay-control").classList.toggle("hidden", !isOverlay);
  $("#selection-source-wrap").classList.toggle("hidden", !isOverlay);
  $("#viewer-image-overlay").classList.toggle("hidden", !isOverlay);
  $("#document-page").style.width = `${state.zoom * 100}%`;
  $("#zoom-label").textContent = state.zoom === 1 ? "Fit" : `${Math.round(state.zoom * 100)}%`;
  clearCanvasSelection();

  const cannotRender = primaryDocument.format === "dwg" && !primaryDocument.metadata.geometry_available;
  $("#drawing-viewer").classList.toggle("hidden", cannotRender);
  $("#dwg-viewer").classList.toggle("hidden", !cannotRender);
  if (cannotRender) return;

  const primary = $("#viewer-image-primary");
  const overlay = $("#viewer-image-overlay");
  $("#viewer-loading span").textContent = "Rendering drawing";
  $("#viewer-loading").classList.remove("hidden");
  primary.onload = () => $("#viewer-loading").classList.add("hidden");
  primary.onerror = () => {
    $("#viewer-loading span").textContent = "Drawing could not be rendered";
  };
  primary.src = drawingUrl(primaryPid, state.viewPage, highlightBlockId);
  if (isOverlay) {
    overlay.src = drawingUrl("PID-B", state.viewPage);
    overlay.style.opacity = String(Number($("#overlay-opacity").value) / 100);
  } else {
    overlay.removeAttribute("src");
  }
}

function switchDocument(tab, page = 1, highlightBlockId = "") {
  state.activeTab = tab;
  state.viewPage = Number(page || 1);
  setSelectionMode(false);
  $$(".document-tab").forEach(button => button.classList.toggle("active", button.dataset.tab === tab));
  $("#delta-viewer").classList.toggle("hidden", tab !== "DELTA");
  $("#drawing-viewer").classList.toggle("hidden", tab === "DELTA");
  $("#dwg-viewer").classList.add("hidden");
  if (tab === "DELTA") return;
  renderDrawing(highlightBlockId);
}

$$(".document-tab").forEach(button => button.addEventListener("click", () => switchDocument(button.dataset.tab)));
$("#previous-page").addEventListener("click", () => {
  if (state.viewPage > 1) {
    state.viewPage -= 1;
    renderDrawing();
  }
});
$("#next-page").addEventListener("click", () => {
  state.viewPage += 1;
  renderDrawing();
});
$("#zoom-out").addEventListener("click", () => {
  state.zoom = Math.max(1, Math.round((state.zoom - 0.25) * 100) / 100);
  renderDrawing();
});
$("#zoom-in").addEventListener("click", () => {
  state.zoom = Math.min(2.5, Math.round((state.zoom + 0.25) * 100) / 100);
  renderDrawing();
});
$("#overlay-opacity").addEventListener("input", event => {
  $("#viewer-image-overlay").style.opacity = String(Number(event.target.value) / 100);
});
$("#select-region").addEventListener("click", () => setSelectionMode(!state.selectionMode));

let selectionDragStart = null;
const selectionLayer = $("#selection-layer");

function normalizedSelectionPoint(event) {
  const bounds = selectionLayer.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
    y: Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height))
  };
}

function drawSelectionBox(start, end) {
  const x0 = Math.min(start.x, end.x), y0 = Math.min(start.y, end.y);
  const x1 = Math.max(start.x, end.x), y1 = Math.max(start.y, end.y);
  const box = $("#selection-box");
  box.classList.remove("hidden");
  box.style.left = `${x0 * 100}%`;
  box.style.top = `${y0 * 100}%`;
  box.style.width = `${(x1 - x0) * 100}%`;
  box.style.height = `${(y1 - y0) * 100}%`;
  return { x0, y0, x1, y1 };
}

selectionLayer.addEventListener("pointerdown", event => {
  if (!state.selectionMode || event.target.closest("#ask-selection")) return;
  event.preventDefault();
  selectionDragStart = normalizedSelectionPoint(event);
  selectionLayer.setPointerCapture(event.pointerId);
  clearCanvasSelection();
});

selectionLayer.addEventListener("pointermove", event => {
  if (!selectionDragStart || !state.selectionMode) return;
  drawSelectionBox(selectionDragStart, normalizedSelectionPoint(event));
});

selectionLayer.addEventListener("pointerup", event => {
  if (!selectionDragStart || !state.selectionMode) return;
  const region = drawSelectionBox(selectionDragStart, normalizedSelectionPoint(event));
  selectionDragStart = null;
  if (region.x1 - region.x0 < 0.015 || region.y1 - region.y0 < 0.015) {
    clearCanvasSelection();
    return;
  }
  const source = state.activeTab === "OVERLAY" ? $("#selection-source").value : state.activeTab;
  state.selection = { source, page: state.viewPage, region };
  const action = $("#ask-selection");
  action.style.left = `${Math.min(82, region.x1 * 100)}%`;
  action.style.top = `${Math.min(90, region.y1 * 100 + 2)}%`;
  action.classList.remove("hidden");
});

function clearPendingSelection() {
  state.pendingSelection = null;
  $("#selection-context").classList.add("hidden");
  $("#selection-context").classList.remove("preview-failed");
  $("#selection-context-preview").removeAttribute("src");
}

$("#selection-context-preview").addEventListener("load", () => {
  $("#selection-context").classList.remove("preview-failed");
  $("#selection-context-status").textContent = "Visual reference attached";
});
$("#selection-context-preview").addEventListener("error", () => {
  $("#selection-context").classList.add("preview-failed");
  $("#selection-context-status").textContent = "Coordinates attached · preview unavailable";
});

$("#ask-selection").addEventListener("click", event => {
  event.stopPropagation();
  if (!state.selection) return;
  state.pendingSelection = structuredClone(state.selection);
  state.pendingSelection.previewUrl = selectionPreviewUrl(state.pendingSelection);
  const sourceLabel = state.pendingSelection.source === "PID-A" ? "File A" : "File B";
  $("#selection-context-label").textContent = `${sourceLabel} · Page ${state.pendingSelection.page} · selected area`;
  $("#selection-context-preview").src = state.pendingSelection.previewUrl;
  $("#selection-context").classList.remove("hidden");
  if (!questionField.value.trim()) {
    questionField.value = "What is shown in this selected area, and does it change between the documents?";
  }
  resizeQuestionField();
  questionField.focus();
});

$("#clear-selection-context").addEventListener("click", clearPendingSelection);
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
  const source = state.activeTab === "OVERLAY" ? $("#selection-source").value : state.activeTab;
  const link = source === "PID-A" ? state.session.links.base : state.session.links.revised;
  window.open(link, "_blank", "noopener");
});

$("#export-button").addEventListener("click", () => $(".export-popover").classList.toggle("open"));
document.addEventListener("click", event => {
  if (!event.target.closest(".export-menu")) $(".export-popover").classList.remove("open");
});

function addMessage(role, content, citations = [], attachment = null) {
  const thread = $("#chat-thread");
  const shouldFollow = role === "user" || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 100;
  const element = document.createElement("div");
  element.className = `message ${role}`;
  if (role === "user") {
    const sourceLabel = attachment?.source === "PID-A" ? "File A" : "File B";
    const attachmentMarkup = attachment
      ? `<figure class="message-attachment">
          <img src="${escapeHtml(attachment.previewUrl)}" alt="Selected area from ${sourceLabel}, page ${attachment.page}">
          <figcaption><span>Selected drawing area</span><b>${sourceLabel} · Page ${attachment.page}</b></figcaption>
        </figure>`
      : "";
    element.innerHTML = `<div class="bubble${attachment ? " with-attachment" : ""}">${attachmentMarkup}<p>${escapeHtml(content)}</p></div>`;
    const preview = $(".message-attachment img", element);
    if (preview) {
      preview.addEventListener("error", () => {
        preview.remove();
        $(".message-attachment span", element).textContent = "Visual preview unavailable";
      });
    }
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
  const attachment = state.pendingSelection ? structuredClone(state.pendingSelection) : null;
  const selection = attachment
    ? { source: attachment.source, page: attachment.page, region: attachment.region }
    : null;
  addMessage("user", question, [], attachment); field.value = ""; resizeQuestionField();
  clearPendingSelection();
  const pending = addMessage("assistant", "Searching both documents and the delta report...");
  try {
    const response = await fetch(`/api/sessions/${state.session.id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, selection })
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
  state.session = null;
  state.files = { a: null, b: null };
  state.activeTab = "PID-A";
  state.viewPage = 1;
  state.zoom = 1;
  state.selectionMode = false;
  state.selection = null;
  clearPendingSelection();
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
