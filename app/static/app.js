const state = { fileA: null, fileB: null, busy: false };
let currentHistoryId = null;
const HISTORY_PAGE_SIZE = 20;
let historyOffset = 0;
let historyItems = [];
let historySearch = "";

function setExportLinks(historyId) {
  currentHistoryId = historyId;
  const active = Boolean(historyId);
  [
    ["export-md-btn", "export.md"],
    ["export-pdf-btn", "export.pdf"],
  ].forEach(([id, file]) => {
    const link = document.getElementById(id);
    link.href = active ? `/api/v1/history/${historyId}/${file}` : "#";
    link.classList.toggle("pointer-events-none", !active);
    link.classList.toggle("opacity-40", !active);
    link.setAttribute("aria-disabled", String(!active));
    link.tabIndex = active ? 0 : -1;
  });
}

// The results panel always mirrors a saved history record; once that record
// is gone (deleted, or wiped by Clear All), hide it rather than leaving a
// now-unsaved battlecard on screen with only its export links disabled.
function hideResults() {
  document.getElementById("results").classList.add("hidden");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// Must match app/services/orchestrator.py's NO_RISKS_MESSAGE /
// NO_ADVANTAGES_MESSAGE exactly — used to render the "nothing found"
// fallback as a neutral line instead of a highlighted risk/advantage.
const NO_RISKS_MESSAGE =
  "No significant field-deployment risks identified from the extracted spec.";
const NO_ADVANTAGES_MESSAGE =
  "No differentiating capabilities identified from the extracted spec.";

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
    `<mark class="bg-led-amber/30 text-ink rounded-sm px-0.5">${escaped.slice(idx, idx + escapedQuery.length)}</mark>` +
    escaped.slice(idx + escapedQuery.length)
  );
}

// ---- Confirmation dialog (replaces window.confirm) ----
// A native modal <dialog>: it joins the top layer (so it can open over the
// history drawer), traps focus, and Esc cancels it.
function confirmDialog(message, { title = "Confirm action", confirmLabel = "Confirm" } = {}) {
  const dialog = document.getElementById("confirm-dialog");
  const okBtn = document.getElementById("confirm-ok-btn");
  const cancelBtn = document.getElementById("confirm-cancel-btn");

  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;
  okBtn.textContent = confirmLabel;

  return new Promise((resolve) => {
    // Enter is deliberately not handled here: the focused button (Cancel by
    // default) activates itself natively, so Enter never confirms by accident.
    okBtn.onclick = () => dialog.close("confirm");
    cancelBtn.onclick = () => dialog.close("cancel");
    dialog.onclick = (e) => {
      if (e.target === dialog) dialog.close("cancel");
    };
    dialog.onclose = () => resolve(dialog.returnValue === "confirm");
    dialog.returnValue = "";
    dialog.showModal();
    cancelBtn.focus();
  });
}

// ---- Error banner ----
function showError(message) {
  document.getElementById("error-message").textContent = message;
  document.getElementById("error-banner").classList.remove("hidden");
}
function clearError() {
  document.getElementById("error-banner").classList.add("hidden");
}
document.getElementById("error-dismiss").addEventListener("click", clearError);

// Every failure message says what went wrong and what to do next. The server's
// own detail is kept when it is specific; generic cases get a next step here.
const NETWORK_ERROR = "Could not reach the server. Check your connection and try again.";

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    return {};
  }
}

function describeFailure(res, data, fallback) {
  const detail = typeof data.detail === "string" ? data.detail : "";
  if (res.status === 429) {
    const wait = Number(res.headers.get("Retry-After")) || 60;
    return `Too many requests. Wait ${wait} seconds and try again.`;
  }
  if (res.status >= 500) return "The server hit an error. Try again in a moment.";
  if (res.status === 413) return `${detail || "That file is too large."} Choose a smaller file.`;
  if (res.status === 422) return `${detail || "That document could not be read."} Use a PDF with selectable text.`;
  if (res.status === 404) return `${detail || fallback} It may have been deleted. Open History to pick another.`;
  if (res.status === 400) return `${detail || fallback} Choose a different PDF.`;
  return detail || fallback;
}

function describeException(err, fallback) {
  return err instanceof TypeError ? NETWORK_ERROR : err.message || fallback;
}

// ---- Device slots ----
// One slot per device. The drop surface is a native <label> for a visually
// hidden file input, so click, Enter/Space and focus all come from the browser;
// the Remove button sits outside the label to avoid nesting controls.
function renderDeviceSlot(letter) {
  const id = letter.toLowerCase();
  return `
    <div id="slot-${id}" class="group" data-state="empty">
      <div class="flex items-center justify-between gap-3 mb-2">
        <h3 class="flex items-center gap-2 font-heading font-semibold">
          <span id="led-${id}" class="led-dot led-dot--off mt-0" aria-hidden="true"></span>
          Device ${letter}
        </h3>
        <button type="button" id="clear-${id}"
                class="hidden text-sm font-medium text-ink-muted hover:text-ink disabled:opacity-50 disabled:cursor-not-allowed underline underline-offset-2 rounded-control">
          Remove<span class="sr-only"> file for Device ${letter}</span>
        </button>
      </div>
      <label id="dropzone-${id}" for="file-${id}"
             class="dropzone block cursor-pointer border-2 border-dashed border-line bg-plate rounded-control p-6 text-center transition-colors hover:border-action has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-action">
        <span class="sr-only">Device ${letter} quick start guide (PDF)</span>
        <input type="file" id="file-${id}" accept="application/pdf" class="sr-only" />
        <svg class="w-8 h-8 mx-auto text-ink-muted mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5" />
        </svg>
        <span class="block text-ink-muted group-data-[state=filled]:hidden">
          Drop a PDF here or <span class="font-medium text-action underline underline-offset-2">choose a file</span>
        </span>
        <span id="filename-${id}" class="block font-mono text-sm break-all"></span>
      </label>
    </div>`;
}

document.getElementById("device-slots").innerHTML = ["A", "B"].map(renderDeviceSlot).join("");

function wireDropzone(zoneId, inputId, filenameId, clearId, key) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  const filenameEl = document.getElementById(filenameId);
  const clearBtn = document.getElementById(clearId);
  const slot = zone.closest("[data-state]");
  const led = slot.querySelector(".led-dot");

  const setState = (filled) => {
    slot.dataset.state = filled ? "filled" : "empty";
    led.classList.toggle("led-dot--green", filled);
    led.classList.toggle("led-dot--off", !filled);
    clearBtn.classList.toggle("hidden", !filled);
  };

  const setFile = (file) => {
    if (!file || state.busy) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      showError(`"${file.name}" is not a PDF. Choose a PDF quick start guide.`);
      return;
    }
    clearError();
    state[key] = file;
    filenameEl.textContent = file.name;
    setState(true);
  };

  const clearFile = () => {
    state[key] = null;
    filenameEl.textContent = "";
    input.value = "";
    setState(false);
  };

  input.addEventListener("change", (e) => setFile(e.target.files[0]));
  clearBtn.addEventListener("click", clearFile);

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
  const loadSamplesBtn = document.getElementById("load-samples-btn");
  loadSamplesBtn.disabled = true;
  clearError();
  try {
    const res = await fetch("/api/v1/samples");
    const data = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, data, "Could not load the sample guides."));
    setFileA(base64ToFile(data.device_a.content_base64, data.device_a.filename));
    setFileB(base64ToFile(data.device_b.content_base64, data.device_b.filename));
    await synthesize();
  } catch (err) {
    showError(describeException(err, "Could not load the sample guides."));
  } finally {
    loadSamplesBtn.disabled = false;
  }
});

// ---- Compare ----
// Compare is one request with no server-side progress, so the status line
// reports only what is known: that it is running, for how long, and that long
// guides take longer.
const SLOW_AFTER_SECONDS = 15;
let loadingTimer;

function setLoading(isLoading) {
  state.busy = isLoading;
  document.getElementById("synthesize-spinner").classList.toggle("hidden", !isLoading);
  document.getElementById("synthesize-label").textContent = isLoading ? "Comparing…" : "Compare devices";
  document.getElementById("synthesize-btn").disabled = isLoading;
  document.getElementById("load-samples-btn").disabled = isLoading;
  ["file-a", "file-b", "clear-a", "clear-b"].forEach((id) => {
    document.getElementById(id).disabled = isLoading;
  });

  const status = document.getElementById("compare-status");
  clearInterval(loadingTimer);
  if (!isLoading) {
    status.textContent = "";
    return;
  }
  status.innerHTML = `
    <span class="flex items-start gap-2">
      <span class="led-dot led-dot--amber led-dot--blinking mt-1.5" aria-hidden="true"></span>
      <span id="compare-status-text" class="flex-1">Comparing Device A and Device B…</span>
      <span id="compare-elapsed" class="shrink-0 whitespace-nowrap font-mono tabular-nums" aria-hidden="true">0&nbsp;s</span>
    </span>`;
  const started = Date.now();
  loadingTimer = setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    document.getElementById("compare-elapsed").textContent = `${seconds}\u00a0s`;
    if (seconds === SLOW_AFTER_SECONDS) {
      document.getElementById("compare-status-text").textContent =
        "Still comparing. Longer guides take more time.";
    }
  }, 1000);
}

async function synthesize() {
  if (!state.fileA || !state.fileB) {
    showError("Add a quick start guide PDF for both Device A and Device B.");
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
    const data = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, data, "The comparison failed."));
    setExportLinks(data.history_id);
    setUrlId(data.history_id);
    await loadHistory();
    renderResults(data);
  } catch (err) {
    showError(describeException(err, "The comparison failed. Try again."));
  } finally {
    setLoading(false);
  }
}

document.getElementById("synthesize-btn").addEventListener("click", synthesize);

// ---- History drawer ----
const HISTORY_ID_PATTERN = /^[0-9a-f]{32}$/;
const historyDrawer = document.getElementById("history-drawer");
let historyTotalAll = 0;

function formatTimestamp(iso) {
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function easierLabel(item) {
  const a = item.installation_friction_score_device_a;
  const b = item.installation_friction_score_device_b;
  if (a === b) return "Same installation friction";
  return `Easier to install: ${a < b ? item.device_a_name : item.device_b_name}`;
}

function updateHistoryCount() {
  const countEl = document.getElementById("history-count");
  countEl.textContent = String(historyTotalAll);
  countEl.classList.toggle("hidden", historyTotalAll === 0);
}

// The URL carries the open comparison (?id=<history id>) so a result can be
// reloaded, bookmarked or shared.
function urlHistoryId() {
  const id = new URLSearchParams(location.search).get("id");
  return id && HISTORY_ID_PATTERN.test(id) ? id : null;
}

function setUrlId(id, { replace = false } = {}) {
  if (urlHistoryId() === id) return;
  const url = id ? `${location.pathname}?id=${id}` : location.pathname;
  history[replace ? "replaceState" : "pushState"]({}, "", url);
}

function renderHistoryList() {
  const list = document.getElementById("history-list");
  const empty = document.getElementById("history-empty");
  const clearAllBtn = document.getElementById("clear-history-btn");
  clearAllBtn.classList.toggle("hidden", historyItems.length === 0);
  if (historyItems.length === 0) {
    list.innerHTML = "";
    empty.textContent = historySearch
      ? "No comparisons match that search."
      : "No comparisons yet. Compare two guides and they will show up here.";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = historyItems
    .map((item) => {
      const plainLabel = `${escapeHtml(item.device_a_name)} vs ${escapeHtml(item.device_b_name)}`;
      const highlightedLabel = `${highlightMatch(item.device_a_name, historySearch)} vs ${highlightMatch(item.device_b_name, historySearch)}`;
      const isCurrent = item.id === currentHistoryId;
      return `
        <li class="flex items-center gap-1">
          <button type="button" data-history-id="${item.id}"${isCurrent ? ' aria-current="true"' : ""}
                  class="flex-1 min-w-0 text-left py-3 px-2 -mx-2 rounded-control hover:bg-plate transition-colors ${isCurrent ? "bg-plate" : ""}">
            <span class="block font-medium truncate" title="${plainLabel}">${highlightedLabel}</span>
            <span class="block text-ink-muted truncate">${escapeHtml(easierLabel(item))}</span>
            <span class="block text-ink-muted">${escapeHtml(formatTimestamp(item.created_at))}</span>
          </button>
          <button type="button" data-delete-id="${item.id}" aria-label="Delete comparison: ${plainLabel}"
                  class="shrink-0 w-9 h-9 inline-flex items-center justify-center rounded-control text-ink-muted hover:text-danger hover:bg-plate">
            &times;
          </button>
        </li>`;
    })
    .join("");
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
    if (!historySearch) {
      historyTotalAll = page.total;
      updateHistoryCount();
    }
    renderHistoryList();
    loadMoreBtn.classList.toggle("hidden", historyOffset >= page.total);
    return historyItems;
  } catch {
    document.getElementById("history-list").innerHTML = "";
    document.getElementById("clear-history-btn").classList.add("hidden");
    empty.textContent = "Could not load history. Close this panel and open it again to retry.";
    empty.classList.remove("hidden");
    loadMoreBtn.classList.add("hidden");
    return null;
  }
}

async function loadHistoryItem(id) {
  clearError();
  try {
    const res = await fetch(`/api/v1/history/${id}`);
    const record = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, record, "Could not load that comparison."));
    setExportLinks(id);
    renderResults(record.battlecard);
    return true;
  } catch (err) {
    showError(describeException(err, "Could not load that comparison."));
    return false;
  }
}

async function openHistoryItem(id) {
  if (!(await loadHistoryItem(id))) return;
  setUrlId(id);
  historyDrawer.close();
  renderHistoryList();
}

async function deleteHistoryItem(id) {
  const ok = await confirmDialog("This removes it from history and can\u2019t be undone.", {
    title: "Delete this comparison?",
    confirmLabel: "Delete comparison",
  });
  if (!ok) return;
  try {
    const res = await fetch(`/api/v1/history/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      throw new Error(describeFailure(res, await readJson(res), "Could not delete that comparison."));
    }
    if (id === currentHistoryId) {
      setExportLinks(null);
      hideResults();
      setUrlId(null, { replace: true });
    }
    await loadHistory();
  } catch (err) {
    showError(describeException(err, "Could not delete that comparison."));
  }
}

document.getElementById("history-list").addEventListener("click", (e) => {
  const deleteBtn = e.target.closest("[data-delete-id]");
  if (deleteBtn) {
    deleteHistoryItem(deleteBtn.dataset.deleteId);
    return;
  }
  const openBtn = e.target.closest("[data-history-id]");
  if (openBtn) openHistoryItem(openBtn.dataset.historyId);
});

document.getElementById("clear-history-btn").addEventListener("click", async () => {
  const ok = await confirmDialog("This removes every saved comparison and can\u2019t be undone.", {
    title: "Clear history?",
    confirmLabel: "Clear history",
  });
  if (!ok) return;
  try {
    const res = await fetch("/api/v1/history", { method: "DELETE" });
    if (!res.ok) throw new Error(describeFailure(res, await readJson(res), "Could not clear history."));
    setExportLinks(null);
    hideResults();
    setUrlId(null, { replace: true });
    await loadHistory();
  } catch (err) {
    showError(describeException(err, "Could not clear history."));
  }
});

document.getElementById("history-toggle").addEventListener("click", () => {
  historyDrawer.showModal();
  loadHistory();
});
document.getElementById("history-close").addEventListener("click", () => historyDrawer.close());
historyDrawer.addEventListener("click", (e) => {
  if (e.target === historyDrawer) historyDrawer.close();
});
document.getElementById("load-more-history-btn").addEventListener("click", () => loadHistory({ append: true }));

let historySearchDebounce;
document.getElementById("history-search").addEventListener("input", (e) => {
  clearTimeout(historySearchDebounce);
  historySearchDebounce = setTimeout(() => {
    historySearch = e.target.value.trim();
    loadHistory();
  }, 300);
});

window.addEventListener("popstate", () => {
  const id = urlHistoryId();
  if (id) {
    loadHistoryItem(id);
  } else {
    setExportLinks(null);
    hideResults();
  }
});

loadHistory();
const initialHistoryId = urlHistoryId();
if (initialHistoryId) {
  loadHistoryItem(initialHistoryId).then((ok) => {
    if (!ok) setUrlId(null, { replace: true });
  });
}

// ---- Copy to clipboard ----
document.getElementById("copy-btn").addEventListener("click", async () => {
  const text = document.getElementById("sales-talk-track").textContent;
  const btn = document.getElementById("copy-btn");
  try {
    await navigator.clipboard.writeText(text);
    const original = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => (btn.textContent = original), 1500);
  } catch {
    showError("Could not copy. Select the talk track text and copy it manually.");
  }
});

// ---- Rendering ----
const FRICTION_SEGMENTS = 10;

function frictionColor(score) {
  if (score <= 3) return "bg-led-green";
  if (score <= 6) return "bg-led-amber";
  return "bg-led-red";
}

// Fixed allowlist only — never interpolates led.color/led.pattern text directly,
// so no untrusted string ever reaches the class or style attribute. Colors live
// in input.css as .led-dot--<color> token classes.
const LED_COLORS = new Set(["green", "amber", "red", "blue", "white", "off"]);
// Capped well below real-device rates (LEDs are commonly documented up to
// ~2-4Hz) so a fast blink stays readable at dot size instead of flickering.
const MIN_BLINK_SECONDS = 0.6;
const MAX_BLINK_SECONDS = 3;

function ledDot(led) {
  const colorClass = `led-dot--${LED_COLORS.has(led.color) ? led.color : "unknown"}`;
  const patternClass =
    led.pattern === "blinking" ? "led-dot--blinking" : led.pattern === "pulsing" ? "led-dot--pulsing" : "";
  let style = "";
  if (patternClass && led.frequency_hz > 0) {
    const seconds = Math.min(MAX_BLINK_SECONDS, Math.max(MIN_BLINK_SECONDS, 1 / led.frequency_hz));
    style = ` style="animation-duration:${seconds}s"`;
  }
  return `<span class="led-dot ${colorClass} ${patternClass}"${style} aria-hidden="true"></span>`;
}

function frictionMeter(score) {
  const lit = Math.max(0, Math.min(FRICTION_SEGMENTS, Math.round(score)));
  const color = frictionColor(score);
  const segments = Array.from(
    { length: FRICTION_SEGMENTS },
    (_, i) => `<span class="h-2.5 flex-1 rounded-sm ${i < lit ? color : "bg-line"}"></span>`
  ).join("");
  return `<div class="flex gap-1" role="img" aria-label="Friction ${score} out of ${FRICTION_SEGMENTS}">${segments}</div>`;
}

function renderVerdict(data, nameA, nameB) {
  const scoreA = data.installation_friction_score_device_a;
  const scoreB = data.installation_friction_score_device_b;
  const easier = scoreA < scoreB ? "a" : scoreB < scoreA ? "b" : null;
  const card = (key, name, score) => `
    <div class="bg-plate border border-line rounded-control p-5">
      <h4 class="font-heading font-semibold text-lg break-words">${escapeHtml(name)}</h4>
      <p class="mt-3 flex items-baseline gap-2">
        <span class="font-heading font-semibold text-2xl tabular-nums">${score}</span>
        <span class="text-ink-muted">out of ${FRICTION_SEGMENTS}</span>
      </p>
      <div class="mt-3">${frictionMeter(score)}</div>
      ${
        easier === key
          ? `<p class="mt-3 flex items-center gap-2 text-sm font-medium"><span class="led-dot led-dot--green mt-0" aria-hidden="true"></span>Easier to install</p>`
          : ""
      }
    </div>`;
  document.getElementById("results-verdict").innerHTML =
    card("a", nameA, scoreA) + card("b", nameB, scoreB);
}

function ipLabel(isPrivate) {
  return `<span class="block text-ink-muted">${isPrivate ? "Private (RFC 1918)" : "Public"}</span>`;
}

function provisioningChip(method) {
  return `<span class="inline-block text-xs font-medium px-2 py-0.5 mr-1 mb-1 rounded-control border border-line bg-plate">${escapeHtml(method)}</span>`;
}

// Primary method first, then any other methods the document lists. Records
// saved before provisioning_methods existed only have the primary one.
function provisioningCell(cm, device) {
  const primary = cm.provisioning_method[device];
  const others = ((cm.provisioning_methods || {})[device] || []).filter(
    (method) => method !== primary
  );
  return [primary, ...others].map(provisioningChip).join("");
}

function listOrDash(items, { mono = false } = {}) {
  if (!items || items.length === 0) {
    return `<span class="text-ink-muted">None</span>`;
  }
  return `<ul class="list-disc pl-4 space-y-0.5 marker:text-ink-muted${mono ? " font-mono" : ""}">${items
    .map((item) => `<li class="break-words">${escapeHtml(item)}</li>`)
    .join("")}</ul>`;
}

function renderSpecTable(cm, nameA, nameB) {
  document.getElementById("col-header-a").textContent = nameA;
  document.getElementById("col-header-b").textContent = nameB;
  const textOrDash = (value) =>
    value ? escapeHtml(value) : `<span class="text-ink-muted">Not specified</span>`;
  const ipCell = (device) =>
    `<span class="font-mono break-all">${escapeHtml(cm.default_ip[device] || "Unknown")}</span>${ipLabel(cm.is_private_ip[device])}`;
  const rows = [
    ["Default IP", ipCell("device_a"), ipCell("device_b")],
    ["Provisioning method", provisioningCell(cm, "device_a"), provisioningCell(cm, "device_b")],
    ["Required tools", listOrDash(cm.required_tools.device_a), listOrDash(cm.required_tools.device_b)],
    ["Physical ports", listOrDash(cm.ports.device_a, { mono: true }), listOrDash(cm.ports.device_b, { mono: true })],
    ["Wi-Fi standard", textOrDash(cm.wifi_standard.device_a), textOrDash(cm.wifi_standard.device_b)],
    ["WAN speed", textOrDash(cm.wan_speed.device_a), textOrDash(cm.wan_speed.device_b)],
    ["Form factor", textOrDash(cm.form_factor.device_a), textOrDash(cm.form_factor.device_b)],
    ["Capabilities", listOrDash(cm.capabilities.device_a), listOrDash(cm.capabilities.device_b)],
  ];
  // Below md each spec stacks into a block and the device name is repeated
  // above its value, since a 3-column table is too narrow on a phone.
  const deviceLabel = (name) =>
    `<span class="md:hidden block text-xs font-medium text-ink-muted mb-0.5">${escapeHtml(name)}</span>`;
  document.getElementById("comparison-table-body").innerHTML = rows
    .map(
      ([label, a, b]) => `
      <tr class="align-top max-md:block max-md:border-b max-md:border-line max-md:py-3">
        <th scope="row" class="md:border-b md:border-line md:py-3 pr-3 text-left font-medium text-ink-muted max-md:block max-md:pb-2">${label}</th>
        <td class="md:border-b md:border-line md:py-3 pr-3 break-words max-md:block max-md:py-1.5">${deviceLabel(nameA)}${a}</td>
        <td class="md:border-b md:border-line md:py-3 pr-3 break-words max-md:block max-md:py-1.5">${deviceLabel(nameB)}${b}</td>
      </tr>`
    )
    .join("");
}

// The "nothing found" fallbacks render as plain muted text; real findings get
// a status LED (green advantage, amber risk).
function assessmentList(items, fallback, ledClass) {
  return items
    .map((item) =>
      item === fallback
        ? `<li class="text-ink-muted">${escapeHtml(item)}</li>`
        : `<li class="flex items-start gap-2"><span class="led-dot ${ledClass} mt-2" aria-hidden="true"></span><span>${escapeHtml(item)}</span></li>`
    )
    .join("");
}

function renderAssessment(data, nameA, nameB) {
  const panel = (name, advantages, risks) => `
    <div class="bg-plate border border-line rounded-control p-5 space-y-5">
      <h4 class="font-heading font-semibold text-lg break-words">${escapeHtml(name)}</h4>
      <div>
        <h5 class="font-medium mb-2">Advantages</h5>
        <ul class="space-y-2 text-sm">${assessmentList(advantages, NO_ADVANTAGES_MESSAGE, "led-dot--green")}</ul>
      </div>
      <div>
        <h5 class="font-medium mb-2">Field-deployment risks</h5>
        <ul class="space-y-2 text-sm">${assessmentList(risks, NO_RISKS_MESSAGE, "led-dot--amber")}</ul>
      </div>
    </div>`;
  document.getElementById("results-assessment").innerHTML =
    panel(nameA, data.key_advantages_device_a, data.key_risks_device_a) +
    panel(nameB, data.key_advantages_device_b, data.key_risks_device_b);
}

function renderLedGuide(cm, nameA, nameB) {
  const column = (name, leds) => `
    <div>
      <h4 class="font-heading font-semibold text-lg break-words mb-3">${escapeHtml(name)}</h4>
      <ul class="space-y-3 text-sm">
        ${
          leds
            .map(
              (led) => `
          <li class="flex items-start gap-3">
            ${ledDot(led)}
            <span class="text-bezel-ink/85">${led.name ? `<span class="font-semibold text-bezel-ink">${escapeHtml(led.name)}:</span> ` : ""}${escapeHtml(led.description)}</span>
          </li>`
            )
            .join("") || `<li class="text-bezel-ink/70">No LED data extracted.</li>`
        }
      </ul>
    </div>`;
  document.getElementById("results-leds").innerHTML =
    column(nameA, cm.led_states.device_a) + column(nameB, cm.led_states.device_b);
}

function renderResults(data) {
  const cm = data.comparison_matrix;
  const nameA = cm.model_name.device_a;
  const nameB = cm.model_name.device_b;

  renderVerdict(data, nameA, nameB);
  document.getElementById("sales-talk-track").textContent = data.sales_talk_track;
  renderSpecTable(cm, nameA, nameB);
  renderAssessment(data, nameA, nameB);
  renderLedGuide(cm, nameA, nameB);

  const results = document.getElementById("results");
  results.classList.remove("hidden");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  results.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  document.getElementById("results-heading").focus({ preventScroll: true });
}
