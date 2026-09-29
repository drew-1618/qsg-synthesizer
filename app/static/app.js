const state = { fileA: null, fileB: null };
let currentHistoryId = null;
const HISTORY_PAGE_SIZE = 20;
let historyOffset = 0;
let historyItems = [];
let historySearch = "";

function setExportLinks(historyId) {
  currentHistoryId = historyId;
  const mdBtn = document.getElementById("export-md-btn");
  const pdfBtn = document.getElementById("export-pdf-btn");
  const active = Boolean(historyId);
  mdBtn.href = active ? `/api/v1/history/${historyId}/export.md` : "#";
  pdfBtn.href = active ? `/api/v1/history/${historyId}/export.pdf` : "#";
  mdBtn.classList.toggle("pointer-events-none", !active);
  mdBtn.classList.toggle("opacity-40", !active);
  pdfBtn.classList.toggle("pointer-events-none", !active);
  pdfBtn.classList.toggle("opacity-40", !active);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// Wraps the first case-insensitive match of `query` in `text` with <mark>.
// Operates on already-escaped strings so highlighting can never inject
// markup beyond the <mark> tag itself.
function highlightMatch(text, query) {
  const escaped = escapeHtml(text);
  if (!query) return escaped;
  const escapedQuery = escapeHtml(query);
  const idx = escaped.toLowerCase().indexOf(escapedQuery.toLowerCase());
  if (idx === -1) return escaped;
  return (
    escaped.slice(0, idx) +
    `<mark class="bg-amber-200 text-slate-900 rounded-sm px-0.5">${escaped.slice(idx, idx + escapedQuery.length)}</mark>` +
    escaped.slice(idx + escapedQuery.length)
  );
}

// ---- Confirmation dialog (replaces window.confirm) ----
function confirmDialog(message, { title = "Are you sure?", confirmLabel = "Confirm" } = {}) {
  const dialog = document.getElementById("confirm-dialog");
  const backdrop = document.getElementById("confirm-backdrop");
  const titleEl = document.getElementById("confirm-title");
  const messageEl = document.getElementById("confirm-message");
  const okBtn = document.getElementById("confirm-ok-btn");
  const cancelBtn = document.getElementById("confirm-cancel-btn");

  titleEl.textContent = title;
  messageEl.textContent = message;
  okBtn.textContent = confirmLabel;

  dialog.classList.remove("hidden");
  cancelBtn.focus();

  return new Promise((resolve) => {
    const cleanup = (result) => {
      dialog.classList.add("hidden");
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      backdrop.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKeydown);
      resolve(result);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onKeydown = (e) => {
      if (e.key === "Escape") cleanup(false);
      if (e.key === "Enter") cleanup(true);
    };

    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    backdrop.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKeydown);
  });
}

// ---- Error banner ----
function showError(message) {
  const banner = document.getElementById("error-banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}
function clearError() {
  document.getElementById("error-banner").classList.add("hidden");
}

// ---- Drop zones ----
function wireDropzone(zoneId, inputId, filenameId, clearId, key) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  const filenameEl = document.getElementById(filenameId);
  const clearBtn = document.getElementById(clearId);

  const setFile = (file) => {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      showError(`"${file.name}" is not a PDF file.`);
      return;
    }
    clearError();
    state[key] = file;
    filenameEl.textContent = file.name;
    clearBtn.classList.remove("hidden");
  };

  const clearFile = () => {
    state[key] = null;
    filenameEl.textContent = "";
    input.value = "";
    clearBtn.classList.add("hidden");
  };

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", (e) => setFile(e.target.files[0]));

  clearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    clearFile();
  });

  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    })
  );
  zone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

  return setFile;
}

const setFileA = wireDropzone("dropzone-a", "file-a", "filename-a", "clear-a", "fileA");
const setFileB = wireDropzone("dropzone-b", "file-b", "filename-b", "clear-b", "fileB");

// ---- Load Sample QSGs ----
function base64ToFile(base64, filename) {
  const bytes = atob(base64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
  return new File([arr], filename, { type: "application/pdf" });
}

document.getElementById("load-samples-btn").addEventListener("click", async () => {
  clearError();
  try {
    const res = await fetch("/api/v1/samples");
    if (!res.ok) throw new Error("Could not load sample QSGs.");
    const data = await res.json();
    setFileA(base64ToFile(data.device_a.content_base64, data.device_a.filename));
    setFileB(base64ToFile(data.device_b.content_base64, data.device_b.filename));
    await synthesize();
  } catch (err) {
    showError(err.message || "Could not load sample QSGs.");
  }
});

// ---- Synthesize ----
function setLoading(isLoading) {
  document.getElementById("synthesize-spinner").classList.toggle("hidden", !isLoading);
  document.getElementById("synthesize-label").textContent = isLoading ? "Synthesizing..." : "Synthesize";
  document.getElementById("synthesize-btn").disabled = isLoading;
}

async function synthesize() {
  if (!state.fileA || !state.fileB) {
    showError("Please provide a QSG PDF for both Device A and Device B.");
    return;
  }
  clearError();
  setLoading(true);
  document.getElementById("results").classList.add("hidden");
  try {
    const formData = new FormData();
    formData.append("device_a_pdf", state.fileA);
    formData.append("device_b_pdf", state.fileB);
    const res = await fetch("/api/v1/compare-pdfs", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Synthesis failed.");
    }
    const items = await loadHistory();
    setExportLinks(items && items[0] ? items[0].id : null);
    renderResults(data);
  } catch (err) {
    showError(err.message || "Something went wrong while synthesizing the comparison.");
  } finally {
    setLoading(false);
  }
}

document.getElementById("synthesize-btn").addEventListener("click", synthesize);

// ---- History ----
function formatTimestamp(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function renderHistoryList() {
  const list = document.getElementById("history-list");
  const empty = document.getElementById("history-empty");
  if (historyItems.length === 0) {
    list.innerHTML = "";
    empty.textContent = historySearch
      ? "No comparisons match that search."
      : "No comparisons yet — synthesize one to see it here.";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = historyItems
    .map((item) => {
      const plainLabel = `${escapeHtml(item.device_a_name)} vs ${escapeHtml(item.device_b_name)}`;
      const highlightedLabel = `${highlightMatch(item.device_a_name, historySearch)} vs ${highlightMatch(item.device_b_name, historySearch)}`;
      return `
        <li class="flex items-center gap-1">
          <button type="button" data-history-id="${item.id}"
                  class="history-item flex-1 min-w-0 text-left py-2.5 px-2 -mx-2 rounded-md flex items-center justify-between gap-3 hover:bg-slate-50 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">
            <span class="min-w-0">
              <span class="block font-medium text-slate-700 truncate">${highlightedLabel}</span>
              <span class="block text-xs text-slate-400">${formatTimestamp(item.created_at)}</span>
            </span>
            <span class="flex items-center gap-1.5 shrink-0 text-xs font-semibold text-slate-500">
              ${item.installation_friction_score_device_a}/10
              <span class="text-slate-300">&middot;</span>
              ${item.installation_friction_score_device_b}/10
            </span>
          </button>
          <button type="button" data-delete-id="${item.id}" aria-label="Delete comparison: ${plainLabel}"
                  class="delete-history-item shrink-0 w-8 h-8 inline-flex items-center justify-center rounded-full text-slate-400 hover:text-red-600 hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400">
            &times;
          </button>
        </li>`;
    })
    .join("");
  document.querySelectorAll(".history-item").forEach((btn) =>
    btn.addEventListener("click", () => loadHistoryItem(btn.dataset.historyId))
  );
  document.querySelectorAll(".delete-history-item").forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteHistoryItem(btn.dataset.deleteId);
    })
  );
}

async function loadHistory({ append = false } = {}) {
  const empty = document.getElementById("history-empty");
  const loadMoreBtn = document.getElementById("load-more-history-btn");
  const offset = append ? historyOffset : 0;
  const q = historySearch ? `&q=${encodeURIComponent(historySearch)}` : "";
  try {
    const res = await fetch(`/api/v1/history?limit=${HISTORY_PAGE_SIZE}&offset=${offset}${q}`);
    if (!res.ok) throw new Error();
    const page = await res.json();
    historyItems = append ? historyItems.concat(page.items) : page.items;
    historyOffset = offset + page.items.length;
    renderHistoryList();
    loadMoreBtn.classList.toggle("hidden", historyOffset >= page.total);
    return historyItems;
  } catch {
    document.getElementById("history-list").innerHTML = "";
    empty.textContent = "Could not load comparison history.";
    empty.classList.remove("hidden");
    loadMoreBtn.classList.add("hidden");
    return null;
  }
}

async function loadHistoryItem(id) {
  clearError();
  try {
    const res = await fetch(`/api/v1/history/${id}`);
    const record = await res.json();
    if (!res.ok) throw new Error(record.detail || "Could not load that comparison.");
    setExportLinks(id);
    renderResults(record.battlecard);
  } catch (err) {
    showError(err.message || "Could not load that comparison.");
  }
}

async function deleteHistoryItem(id) {
  const ok = await confirmDialog("Delete this comparison from history?", {
    title: "Delete comparison?",
    confirmLabel: "Delete",
  });
  if (!ok) return;
  try {
    const res = await fetch(`/api/v1/history/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error("Could not delete that comparison.");
    if (id === currentHistoryId) setExportLinks(null);
    await loadHistory();
  } catch (err) {
    showError(err.message || "Could not delete that comparison.");
  }
}

document.getElementById("clear-history-btn").addEventListener("click", async () => {
  const ok = await confirmDialog("Clear all comparison history? This cannot be undone.", {
    title: "Clear all history?",
    confirmLabel: "Clear all",
  });
  if (!ok) return;
  try {
    const res = await fetch("/api/v1/history", { method: "DELETE" });
    if (!res.ok) throw new Error("Could not clear history.");
    setExportLinks(null);
    await loadHistory();
  } catch (err) {
    showError(err.message || "Could not clear history.");
  }
});

document.getElementById("refresh-history-btn").addEventListener("click", () => loadHistory());
document.getElementById("load-more-history-btn").addEventListener("click", () => loadHistory({ append: true }));

let historySearchDebounce;
document.getElementById("history-search").addEventListener("input", (e) => {
  clearTimeout(historySearchDebounce);
  historySearchDebounce = setTimeout(() => {
    historySearch = e.target.value.trim();
    loadHistory();
  }, 300);
});

loadHistory();

// ---- Copy to clipboard ----
document.getElementById("copy-btn").addEventListener("click", async () => {
  const text = document.getElementById("sales-talk-track").textContent;
  const btn = document.getElementById("copy-btn");
  try {
    await navigator.clipboard.writeText(text);
    const original = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => (btn.textContent = original), 1500);
  } catch {
    showError("Could not copy to clipboard.");
  }
});

// ---- Rendering ----
function frictionColor(score) {
  if (score <= 3) return "bg-green-500";
  if (score <= 6) return "bg-amber-500";
  return "bg-red-500";
}

const LED_STYLES = {
  BOOTING: "bg-amber-100 text-amber-800",
  ONLINE: "bg-green-100 text-green-800",
  FAULT: "bg-red-100 text-red-800",
  UNKNOWN: "bg-slate-100 text-slate-600",
};

function ledPill(state) {
  const cls = LED_STYLES[state] || LED_STYLES.UNKNOWN;
  return `<span class="inline-block text-xs font-semibold px-2 py-0.5 rounded-full ${cls}">${escapeHtml(state)}</span>`;
}

function ipBadge(isPrivate) {
  return isPrivate
    ? `<span class="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">Private (RFC1918)</span>`
    : `<span class="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-orange-100 text-orange-800">Public</span>`;
}

function provisioningBadge(method) {
  return `<span class="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800">${escapeHtml(method)}</span>`;
}

function listOrDash(items) {
  if (!items || items.length === 0) {
    return `<span class="text-slate-400">None</span>`;
  }
  return `<ul class="list-disc list-inside space-y-0.5">${items
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("")}</ul>`;
}

function renderResults(data) {
  const cm = data.comparison_matrix;
  const nameA = cm.model_name.device_a;
  const nameB = cm.model_name.device_b;

  // Friction scorecards
  const scoreA = data.installation_friction_score_device_a;
  const scoreB = data.installation_friction_score_device_b;
  document.getElementById("friction-name-a").textContent = nameA;
  document.getElementById("friction-name-b").textContent = nameB;
  document.getElementById("friction-score-a").textContent = scoreA;
  document.getElementById("friction-score-b").textContent = scoreB;
  const barA = document.getElementById("friction-bar-a");
  const barB = document.getElementById("friction-bar-b");
  barA.style.width = `${scoreA * 10}%`;
  barA.className = `h-2.5 rounded-full transition-all ${frictionColor(scoreA)}`;
  barB.style.width = `${scoreB * 10}%`;
  barB.className = `h-2.5 rounded-full transition-all ${frictionColor(scoreB)}`;

  // Sales talk track
  document.getElementById("sales-talk-track").textContent = data.sales_talk_track;

  // Comparison table
  document.getElementById("col-header-a").textContent = nameA;
  document.getElementById("col-header-b").textContent = nameB;
  const rows = [
    ["Default IP", `${escapeHtml(cm.default_ip.device_a || "Unknown")} ${ipBadge(cm.is_private_ip.device_a)}`,
                    `${escapeHtml(cm.default_ip.device_b || "Unknown")} ${ipBadge(cm.is_private_ip.device_b)}`],
    ["Provisioning Method", provisioningBadge(cm.provisioning_method.device_a), provisioningBadge(cm.provisioning_method.device_b)],
    ["Required Tools", listOrDash(cm.required_tools.device_a), listOrDash(cm.required_tools.device_b)],
    ["Physical Ports", listOrDash(cm.ports.device_a), listOrDash(cm.ports.device_b)],
  ];
  document.getElementById("comparison-table-body").innerHTML = rows
    .map(
      ([label, a, b]) => `
      <tr class="align-top">
        <td class="py-3 pr-4 font-medium text-slate-500">${label}</td>
        <td class="py-3 pr-4">${a}</td>
        <td class="py-3 pr-4">${b}</td>
      </tr>`
    )
    .join("");

  // LED status lists
  document.getElementById("led-name-a").textContent = nameA;
  document.getElementById("led-name-b").textContent = nameB;
  const renderLedList = (leds) =>
    leds
      .map(
        (led) => `
        <li class="flex items-start gap-2">
          ${ledPill(led.state)}
          <span class="text-slate-600">${escapeHtml(led.description)}</span>
        </li>`
      )
      .join("") || `<li class="text-slate-400">No LED data extracted.</li>`;
  document.getElementById("led-list-a").innerHTML = renderLedList(cm.led_states.device_a);
  document.getElementById("led-list-b").innerHTML = renderLedList(cm.led_states.device_b);

  // Risks
  document.getElementById("risk-name-a").textContent = nameA;
  document.getElementById("risk-name-b").textContent = nameB;
  const renderRisks = (risks) =>
    risks
      .map(
        (risk) => `
        <li class="flex items-start gap-2 text-amber-800 bg-amber-50 rounded-md px-3 py-2">
          <span aria-hidden="true">&#9888;</span>
          <span>${escapeHtml(risk)}</span>
        </li>`
      )
      .join("");
  document.getElementById("risk-list-a").innerHTML = renderRisks(data.key_risks_device_a);
  document.getElementById("risk-list-b").innerHTML = renderRisks(data.key_risks_device_b);

  document.getElementById("results").classList.remove("hidden");
  document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
}
