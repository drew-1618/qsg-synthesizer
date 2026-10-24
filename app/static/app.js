const state = { busy: false, mode: "sales", slots: { a: emptySlot(), b: emptySlot() } };

// One slot holds either an uploaded file or pasted text; `mode` says which is used.
function emptySlot() {
  return { mode: "file", file: null, text: "", name: "" };
}

// What a slot is called depends on the comparison mode.
function slotLabel(id) {
  if (state.mode === "sales") return id === "a" ? "Our device" : "Competitor";
  return `Device ${id.toUpperCase()}`;
}

function slotHasContent(slot) {
  return slot.mode === "file" ? Boolean(slot.file) : slot.text.trim().length > 0;
}
// ---- Theme ----
// Follows the OS until the user picks one; the pick is remembered per browser.
const THEME_STORAGE_KEY = "qsg-theme";
const THEME_BAR_COLORS = { light: "#2b333b", dark: "#0c0f12" };
const themeToggle = document.getElementById("theme-toggle");

function storedTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return theme === "light" || theme === "dark" ? theme : null;
  } catch {
    return null;
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  // The button names the action, and its icon (swapped in CSS) shows the target mode.
  const label = `Switch to ${theme === "dark" ? "light" : "dark"} theme`;
  themeToggle.setAttribute("aria-label", label);
  themeToggle.title = label;
  document.querySelector('meta[name="theme-color"]').content = THEME_BAR_COLORS[theme];
}

themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    // Storage unavailable (e.g. private mode): the choice just won't persist.
  }
});

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  if (!storedTheme()) applyTheme(e.matches ? "dark" : "light");
});

applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

let currentHistoryId = null;
const HISTORY_PAGE_SIZE = 20;
let historyOffset = 0;
let historyItems = [];
let historySearch = "";

const EXPORT_FORMATS = {
  md: { buttonId: "export-md-btn", label: "Markdown" },
  pdf: { buttonId: "export-pdf-btn", label: "PDF" },
};

// Enables the export buttons only while a saved comparison is on screen.
function setExportTarget(historyId) {
  currentHistoryId = historyId;
  Object.values(EXPORT_FORMATS).forEach(({ buttonId }) => {
    document.getElementById(buttonId).disabled = !historyId;
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
  if (res.status === 422) return `${detail || "That document could not be read."} Use a document with selectable text.`;
  if (res.status === 404) return `${detail || fallback} It may have been deleted. Open History to pick another.`;
  if (res.status === 400 && /exceeded maximum size/.test(detail)) {
    return "That pasted text is too long. Paste a shorter excerpt or upload it as a file.";
  }
  if (res.status === 400) return `${detail || fallback} Choose a different file.`;
  return detail || fallback;
}

function describeException(err, fallback) {
  return err instanceof TypeError ? NETWORK_ERROR : err.message || fallback;
}

// ---- Device slots ----
// Each slot takes a file or pasted text. The drop surface is a native <label>
// for a visually hidden file input, so click, Enter/Space and focus all come
// from the browser; the Remove button sits outside the label to avoid nesting
// controls.
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".md", ".markdown", ".txt"];
// Servers cap a single form field at about 1 MB; this stays under it even at
// 3 bytes per character.
const MAX_PASTE_CHARS = 300000;

function renderDeviceSlot(letter) {
  const id = letter.toLowerCase();
  return `
    <div id="slot-${id}" class="group" data-state="empty">
      <div class="flex items-center justify-between gap-3 mb-2">
        <h3 class="flex items-center gap-2 font-heading font-semibold">
          <span id="led-${id}" class="led-dot led-dot--off mt-0" aria-hidden="true"></span>
          <span data-slot-label="${id}">${slotLabel(id)}</span>
        </h3>
        <button type="button" id="clear-${id}"
                class="hidden text-sm font-medium text-ink-muted hover:text-ink disabled:opacity-50 disabled:cursor-not-allowed underline underline-offset-2 rounded-control">
          Remove<span class="sr-only"> file for <span data-slot-label="${id}">${slotLabel(id)}</span></span>
        </button>
      </div>
      <fieldset class="mb-2 flex gap-5 text-sm">
        <legend class="sr-only">Source for <span data-slot-label="${id}">${slotLabel(id)}</span></legend>
        <label class="inline-flex items-center gap-2 cursor-pointer">
          <input type="radio" name="source-${id}" value="file" checked class="accent-action" /> Upload file
        </label>
        <label class="inline-flex items-center gap-2 cursor-pointer">
          <input type="radio" name="source-${id}" value="text" class="accent-action" /> Paste text
        </label>
      </fieldset>
      <div id="pane-file-${id}">
        <label id="dropzone-${id}" for="file-${id}"
               class="dropzone block cursor-pointer border-2 border-dashed border-line bg-plate rounded-control p-6 text-center transition-colors hover:border-action has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-action">
          <span class="sr-only"><span data-slot-label="${id}">${slotLabel(id)}</span> document (PDF, Word, Markdown or text)</span>
          <input type="file" id="file-${id}" accept="${ACCEPTED_EXTENSIONS.join(",")}" class="sr-only" />
          <svg class="w-8 h-8 mx-auto text-ink-muted mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5" />
          </svg>
          <span class="block text-ink-muted group-data-[state=filled]:hidden">
            Drop a file here or <span class="font-medium text-action underline underline-offset-2">choose one</span>
          </span>
          <span class="block text-sm text-ink-muted group-data-[state=filled]:hidden">PDF, Word, Markdown or text</span>
          <span id="filename-${id}" class="block font-mono text-sm break-all"></span>
        </label>
      </div>
      <div id="pane-text-${id}" class="hidden space-y-3">
        <div>
          <label for="text-${id}" class="sr-only"><span data-slot-label="${id}">${slotLabel(id)}</span> document text</label>
          <textarea id="text-${id}" name="text-${id}" rows="8" maxlength="${MAX_PASTE_CHARS}" spellcheck="false"
                    placeholder="Paste the guide or spec sheet text…"
                    class="w-full font-mono text-sm bg-plate border border-line rounded-control p-3 placeholder:text-ink-muted"></textarea>
        </div>
        <div>
          <label for="name-${id}" class="block text-sm font-medium mb-1">Document name <span class="font-normal text-ink-muted">(optional)</span></label>
          <input type="text" id="name-${id}" name="name-${id}" maxlength="100" autocomplete="off" placeholder="Pasted text"
                 class="w-full text-sm bg-plate border border-line rounded-control px-3 py-2 placeholder:text-ink-muted" />
        </div>
      </div>
    </div>`;
}

document.getElementById("device-slots").innerHTML = ["A", "B"].map(renderDeviceSlot).join("");

function wireSlot(letter) {
  const id = letter.toLowerCase();
  const slotState = state.slots[id];
  const root = document.getElementById(`slot-${id}`);
  const zone = document.getElementById(`dropzone-${id}`);
  const fileInput = document.getElementById(`file-${id}`);
  const filenameEl = document.getElementById(`filename-${id}`);
  const clearBtn = document.getElementById(`clear-${id}`);
  const led = document.getElementById(`led-${id}`);
  const textArea = document.getElementById(`text-${id}`);
  const nameInput = document.getElementById(`name-${id}`);
  const radios = root.querySelectorAll(`input[name="source-${id}"]`);

  // Syncs everything derived from the slot's state: LED, Remove button, panes.
  const refresh = () => {
    const filled = slotHasContent(slotState);
    led.classList.toggle("led-dot--green", filled);
    led.classList.toggle("led-dot--off", !filled);
    root.dataset.state = slotState.file ? "filled" : "empty";
    clearBtn.classList.toggle("hidden", slotState.mode !== "file" || !slotState.file);
    document.getElementById(`pane-file-${id}`).classList.toggle("hidden", slotState.mode !== "file");
    document.getElementById(`pane-text-${id}`).classList.toggle("hidden", slotState.mode !== "text");
    radios.forEach((radio) => {
      radio.checked = radio.value === slotState.mode;
    });
  };

  const setFile = (file) => {
    if (!file || state.busy) return;
    if (!ACCEPTED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
      showError(`"${file.name}" is not a supported file type. Use a PDF, Word (.docx), Markdown or text file.`);
      return;
    }
    clearError();
    slotState.file = file;
    slotState.mode = "file";
    filenameEl.textContent = file.name;
    refresh();
  };

  const clearFile = () => {
    slotState.file = null;
    filenameEl.textContent = "";
    fileInput.value = "";
    refresh();
  };

  fileInput.addEventListener("change", (e) => setFile(e.target.files[0]));
  clearBtn.addEventListener("click", clearFile);
  radios.forEach((radio) =>
    radio.addEventListener("change", () => {
      slotState.mode = radio.value;
      refresh();
    })
  );
  textArea.addEventListener("input", () => {
    slotState.text = textArea.value;
    refresh();
  });
  nameInput.addEventListener("input", () => {
    slotState.name = nameInput.value;
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

const setFileA = wireSlot("A");
const setFileB = wireSlot("B");

// ---- Comparison mode ----
// Sales: side A is the user's own device and B the competitor's, and the talk
// track is written from A's side. Neutral: two devices, no side.
const MODE_STORAGE_KEY = "qsg-mode";
const MODE_HINTS = {
  sales: "Compare your device against a competitor's. The talk track is written from your side.",
  neutral: "Compare two devices without picking a side.",
};

function storedMode() {
  try {
    const mode = localStorage.getItem(MODE_STORAGE_KEY);
    return mode === "sales" || mode === "neutral" ? mode : null;
  } catch {
    return null;
  }
}

function applyMode(mode, { persist = false } = {}) {
  state.mode = mode;
  document.querySelectorAll('#mode-control input[name="mode"]').forEach((radio) => {
    radio.checked = radio.value === mode;
  });
  document.getElementById("mode-hint").textContent = MODE_HINTS[mode];
  document.querySelectorAll("[data-slot-label]").forEach((el) => {
    el.textContent = slotLabel(el.dataset.slotLabel);
  });
  if (!persist) return;
  try {
    localStorage.setItem(MODE_STORAGE_KEY, mode);
  } catch {
    // Storage unavailable (e.g. private mode): the choice just won't persist.
  }
}

document.querySelectorAll('#mode-control input[name="mode"]').forEach((radio) =>
  radio.addEventListener("change", () => applyMode(radio.value, { persist: true }))
);
applyMode(storedMode() || "sales");

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

// ---- Export ----
const exportDialog = document.getElementById("export-dialog");
const exportForm = document.getElementById("export-form");
const exportFilenameInput = document.getElementById("export-filename");
const exportErrorEl = document.getElementById("export-error");
const exportDownloadBtn = document.getElementById("export-download-btn");
let exportFormat = "md";

async function downloadExport(format, requestedName) {
  const query = requestedName ? `?filename=${encodeURIComponent(requestedName)}` : "";
  const res = await fetch(`/api/v1/history/${currentHistoryId}/export.${format}${query}`);
  if (!res.ok) {
    throw new Error(describeFailure(res, await readJson(res), "Could not export that comparison."));
  }
  // The server sanitizes the requested name; use exactly what it settled on.
  const match = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? match[1] : `battlecard.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function openExportDialog(format) {
  exportFormat = format;
  document.getElementById("export-title").textContent = `Export as ${EXPORT_FORMATS[format].label}`;
  document.getElementById("export-ext").textContent = `.${format}`;
  exportFilenameInput.value = "";
  exportErrorEl.classList.add("hidden");
  exportDownloadBtn.disabled = false;
  exportDialog.showModal();
  exportFilenameInput.focus();
  try {
    const res = await fetch(`/api/v1/history/${currentHistoryId}/export-name`);
    if (!res.ok) return;
    const { basename } = await res.json();
    // Don't overwrite a name the user has already started typing.
    if (exportDialog.open && exportFilenameInput.value === "") {
      exportFilenameInput.value = basename;
      exportFilenameInput.select();
    }
  } catch {
    // No suggestion: leaving the field empty makes the server pick the default.
  }
}

Object.entries(EXPORT_FORMATS).forEach(([format, { buttonId }]) => {
  document.getElementById(buttonId).addEventListener("click", () => openExportDialog(format));
});

exportForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  exportDownloadBtn.disabled = true;
  exportErrorEl.classList.add("hidden");
  try {
    await downloadExport(exportFormat, exportFilenameInput.value.trim());
    exportDialog.close();
  } catch (err) {
    exportErrorEl.textContent = describeException(err, "Could not export that comparison.");
    exportErrorEl.classList.remove("hidden");
    exportDownloadBtn.disabled = false;
  }
});
document.getElementById("export-cancel-btn").addEventListener("click", () => exportDialog.close());
exportDialog.addEventListener("click", (e) => {
  if (e.target === exportDialog) exportDialog.close();
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
  document.querySelectorAll("#mode-control input, #device-slots input, #device-slots textarea, #device-slots button").forEach((el) => {
    el.disabled = isLoading;
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
  const missing = ["a", "b"]
    .filter((id) => !slotHasContent(state.slots[id]))
    .map(slotLabel);
  if (missing.length > 0) {
    showError(`Add a document or paste text for ${missing.join(" and ")}.`);
    return;
  }
  clearError();
  setLoading(true);
  document.getElementById("results").classList.add("hidden");
  try {
    const formData = new FormData();
    formData.append("mode", state.mode);
    for (const id of ["a", "b"]) {
      const slot = state.slots[id];
      if (slot.mode === "file") {
        formData.append(`device_${id}_file`, slot.file);
      } else {
        formData.append(`device_${id}_text`, slot.text);
        if (slot.name.trim()) formData.append(`device_${id}_name`, slot.name.trim());
      }
    }
    const res = await fetch("/api/v1/compare", { method: "POST", body: formData });
    const data = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, data, "The comparison failed."));
    setExportTarget(data.history_id);
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
  if (item.perspective === "sales") return `${a < b ? "Ours" : "Competitor"}: easier to install`;
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
                  class="shrink-0 w-9 h-9 inline-flex items-center justify-center rounded-control text-ink-muted hover:text-danger-text hover:bg-plate">
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
    setExportTarget(id);
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
      setExportTarget(null);
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
    setExportTarget(null);
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
    setExportTarget(null);
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

// In a sales comparison side A is ours and B is the competitor; the tag goes
// under each device name wherever the results show one.
function roleTag(perspective, id, colorClass = "text-ink-muted") {
  if (perspective !== "sales") return "";
  const role = id === "a" ? "Ours" : "Competitor";
  return `<span class="block text-xs font-medium ${colorClass}">${role}</span>`;
}

function renderVerdict(data, nameA, nameB) {
  const scoreA = data.installation_friction_score_device_a;
  const scoreB = data.installation_friction_score_device_b;
  const easier = scoreA < scoreB ? "a" : scoreB < scoreA ? "b" : null;
  const card = (key, name, score) => `
    <div class="bg-plate border border-line rounded-control p-5">
      <h4 class="font-heading font-semibold text-lg break-words">${escapeHtml(name)}${roleTag(data.perspective, key)}</h4>
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

function renderSpecTable(cm, nameA, nameB, perspective) {
  document.getElementById("col-header-a").innerHTML = escapeHtml(nameA) + roleTag(perspective, "a");
  document.getElementById("col-header-b").innerHTML = escapeHtml(nameB) + roleTag(perspective, "b");
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
  const panel = (key, name, advantages, risks) => `
    <div class="bg-plate border border-line rounded-control p-5 space-y-5">
      <h4 class="font-heading font-semibold text-lg break-words">${escapeHtml(name)}${roleTag(data.perspective, key)}</h4>
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
    panel("a", nameA, data.key_advantages_device_a, data.key_risks_device_a) +
    panel("b", nameB, data.key_advantages_device_b, data.key_risks_device_b);
}

function renderLedGuide(cm, nameA, nameB, perspective) {
  const column = (key, name, leds) => `
    <div>
      <h4 class="font-heading font-semibold text-lg break-words mb-3">${escapeHtml(name)}${roleTag(perspective, key, "text-bezel-ink/70")}</h4>
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
    column("a", nameA, cm.led_states.device_a) + column("b", nameB, cm.led_states.device_b);
}

function renderResults(data) {
  const cm = data.comparison_matrix;
  const nameA = cm.model_name.device_a;
  const nameB = cm.model_name.device_b;

  renderVerdict(data, nameA, nameB);
  document.getElementById("sales-talk-track").textContent = data.sales_talk_track;
  // The pitch only makes sense in a sales comparison; otherwise it is a summary.
  const isSales = data.perspective === "sales";
  document.getElementById("talk-track-heading").textContent = isSales ? "Sales talk track" : "Summary";
  document.getElementById("copy-btn").textContent = isSales ? "Copy talk track" : "Copy summary";
  renderSpecTable(cm, nameA, nameB, data.perspective);
  renderAssessment(data, nameA, nameB);
  renderLedGuide(cm, nameA, nameB, data.perspective);

  const results = document.getElementById("results");
  results.classList.remove("hidden");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  results.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  document.getElementById("results-heading").focus({ preventScroll: true });
}
