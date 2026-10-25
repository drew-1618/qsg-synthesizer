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
// Follows the OS ("System") until the user picks one in Settings; the pick is
// remembered per browser. The inline script in index.html applies it before
// first paint.
const THEME_STORAGE_KEY = "qsg-theme";
const THEME_BAR_COLORS = { light: "#2b333b", dark: "#0c0f12" };
const darkSchemeQuery = window.matchMedia("(prefers-color-scheme: dark)");

function storedTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return theme === "light" || theme === "dark" ? theme : null;
  } catch {
    return null;
  }
}

function systemTheme() {
  return darkSchemeQuery.matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').content = THEME_BAR_COLORS[theme];
}

// "light", "dark" or "system".
function setThemePreference(preference) {
  try {
    if (preference === "system") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Storage unavailable (e.g. private mode): the choice just won't persist.
  }
  applyTheme(preference === "system" ? systemTheme() : preference);
}

darkSchemeQuery.addEventListener("change", () => {
  if (!storedTheme()) applyTheme(systemTheme());
});

applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

// ---- Settings: which AI reads documents, with the user's own keys ----
// The provider choice is remembered in localStorage. Keys go to localStorage
// only when "Remember keys" is on, otherwise sessionStorage, so they clear
// with the tab. They are sent as headers with compare requests and never
// stored on the server. The model picked per provider ("" = the server's
// default for it) is remembered with the provider choice.
const LLM_STORAGE_KEY = "qsg-llm";
const LLM_KEYS_STORAGE_KEY = "qsg-llm-keys";
const LLM_PROVIDERS = ["server", "anthropic", "gemini", "heuristic"];
const LLM_PROVIDER_NAMES = { anthropic: "Anthropic", gemini: "Gemini" };

function readStored(storage, key) {
  try {
    const value = JSON.parse(storage.getItem(key));
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function loadLlmSettings() {
  const saved = readStored(localStorage, LLM_STORAGE_KEY);
  const remember = saved.remember === true;
  const keys = readStored(remember ? localStorage : sessionStorage, LLM_KEYS_STORAGE_KEY);
  const asKey = (value) => (typeof value === "string" ? value : "");
  const models = saved.models && typeof saved.models === "object" ? saved.models : {};
  return {
    provider: LLM_PROVIDERS.includes(saved.provider) ? saved.provider : "server",
    remember,
    keys: { anthropic: asKey(keys.anthropic), gemini: asKey(keys.gemini) },
    models: { anthropic: asKey(models.anthropic), gemini: asKey(models.gemini) },
  };
}

const llmSettings = loadLlmSettings();

function saveLlmSettings() {
  try {
    localStorage.setItem(
      LLM_STORAGE_KEY,
      JSON.stringify({
        provider: llmSettings.provider,
        remember: llmSettings.remember,
        models: llmSettings.models,
      })
    );
    const [keep, drop] = llmSettings.remember ? [localStorage, sessionStorage] : [sessionStorage, localStorage];
    keep.setItem(LLM_KEYS_STORAGE_KEY, JSON.stringify(llmSettings.keys));
    drop.removeItem(LLM_KEYS_STORAGE_KEY);
  } catch {
    // Storage unavailable: settings last until the page reloads.
  }
}

// Headers for POST /api/v1/compare; none means the server's own configuration.
function llmHeaders() {
  const { provider } = llmSettings;
  if (provider === "server") return {};
  const headers = { "X-LLM-Provider": provider };
  const key = llmSettings.keys[provider];
  if (key) headers["X-LLM-API-Key"] = key;
  const model = llmSettings.models[provider];
  if (model) headers["X-LLM-Model"] = model;
  return headers;
}

const settingsDialog = document.getElementById("settings-dialog");
const settingsKeyInput = document.getElementById("settings-key");
const settingsKeyReveal = document.getElementById("settings-key-reveal");
const settingsModelSelect = document.getElementById("settings-model");
let serverSettingsLoaded = false;
// Per provider: { default, choices: [{ id, label }] }, from GET /api/v1/settings.
let serverModels = {};

// The picker offers the server's list; without it (not loaded yet, or the
// request failed) it stays hidden and the server's default model is used.
function syncModelSelect(provider) {
  const options = serverModels[provider];
  document.getElementById("settings-model-row").classList.toggle("hidden", !options);
  if (!options) return;
  const ids = options.choices.map((choice) => choice.id);
  // A saved model the server no longer offers falls back to its default.
  if (!ids.includes(llmSettings.models[provider])) llmSettings.models[provider] = "";
  settingsModelSelect.innerHTML = [
    `<option value="">Server default (${escapeHtml(options.default)})</option>`,
    ...options.choices.map(
      (choice) => `<option value="${escapeHtml(choice.id)}">${escapeHtml(choice.label)}</option>`
    ),
  ].join("");
  settingsModelSelect.value = llmSettings.models[provider];
}

function syncSettingsDialog() {
  const theme = storedTheme() || "system";
  settingsDialog.querySelectorAll('input[name="theme"]').forEach((radio) => {
    radio.checked = radio.value === theme;
  });
  settingsDialog.querySelectorAll('input[name="llm-provider"]').forEach((radio) => {
    radio.checked = radio.value === llmSettings.provider;
  });
  const name = LLM_PROVIDER_NAMES[llmSettings.provider];
  document.getElementById("settings-key-section").classList.toggle("hidden", !name);
  if (!name) return;
  syncModelSelect(llmSettings.provider);
  document.getElementById("settings-key-label").textContent = `${name} API key`;
  settingsKeyInput.value = llmSettings.keys[llmSettings.provider];
  document.getElementById("settings-key-hint").textContent =
    `Without a key, the server's own ${name} key is used if it has one. Otherwise documents are read with the built-in parser.`;
  document.getElementById("settings-remember").checked = llmSettings.remember;
}

async function loadServerSettings() {
  if (serverSettingsLoaded) return;
  try {
    const res = await fetch("/api/v1/settings");
    if (!res.ok) return;
    const { server_provider: provider, models } = await res.json();
    document.getElementById("settings-server-hint").textContent =
      `(${LLM_PROVIDER_NAMES[provider] || "built-in parser"})`;
    serverModels = models || {};
    serverSettingsLoaded = true;
    if (settingsDialog.open && LLM_PROVIDER_NAMES[llmSettings.provider]) {
      syncModelSelect(llmSettings.provider);
    }
  } catch {
    // Leave the hint empty and the model picker hidden; both options still work.
  }
}

function setKeyRevealed(revealed) {
  settingsKeyInput.type = revealed ? "text" : "password";
  settingsKeyReveal.setAttribute("aria-pressed", String(revealed));
  settingsKeyReveal.textContent = revealed ? "Hide" : "Show";
}

document.getElementById("settings-toggle").addEventListener("click", () => {
  setKeyRevealed(false);
  syncSettingsDialog();
  settingsDialog.showModal();
  loadServerSettings();
});
document.getElementById("settings-done").addEventListener("click", () => settingsDialog.close());
settingsDialog.addEventListener("click", (e) => {
  if (e.target === settingsDialog) settingsDialog.close();
});
settingsDialog.addEventListener("close", () => setKeyRevealed(false));

settingsDialog.addEventListener("change", (e) => {
  const input = e.target;
  if (input.name === "theme") {
    setThemePreference(input.value);
  } else if (input.name === "llm-provider") {
    llmSettings.provider = input.value;
    saveLlmSettings();
    setKeyRevealed(false);
    syncSettingsDialog();
  } else if (input === settingsModelSelect) {
    llmSettings.models[llmSettings.provider] = input.value;
    saveLlmSettings();
  } else if (input.id === "settings-remember") {
    llmSettings.remember = input.checked;
    saveLlmSettings();
  }
});

settingsKeyInput.addEventListener("input", () => {
  llmSettings.keys[llmSettings.provider] = settingsKeyInput.value.trim();
  saveLlmSettings();
});
settingsKeyReveal.addEventListener("click", () => setKeyRevealed(settingsKeyInput.type === "password"));

document.getElementById("settings-forget").addEventListener("click", () => {
  llmSettings.keys = { anthropic: "", gemini: "" };
  saveLlmSettings();
  settingsKeyInput.value = "";
  settingsKeyInput.focus();
});

// ---- Settings: whether comparisons keep their documents (for Rerun) ----
// On by default: the server is the user's own and Rerun needs the text. The
// choice is per browser and sent with each compare as `keep_documents`.
const KEEP_DOCUMENTS_STORAGE_KEY = "qsg-keep-documents";
const keepDocumentsInput = document.getElementById("settings-keep-documents");
const documentsStatus = document.getElementById("settings-documents-status");

function keepDocuments() {
  try {
    return localStorage.getItem(KEEP_DOCUMENTS_STORAGE_KEY) !== "false";
  } catch {
    return keepDocumentsInput.checked;
  }
}

document.getElementById("settings-toggle").addEventListener("click", () => {
  keepDocumentsInput.checked = keepDocuments();
  documentsStatus.textContent = "";
});
keepDocumentsInput.addEventListener("change", () => {
  try {
    localStorage.setItem(KEEP_DOCUMENTS_STORAGE_KEY, String(keepDocumentsInput.checked));
  } catch {
    // Storage unavailable: the checkbox still applies until the page reloads.
  }
});

document.getElementById("settings-forget-documents").addEventListener("click", async () => {
  const ok = await confirmDialog(
    "Saved comparisons keep their battlecards, but none of them can be rerun afterwards. This can’t be undone.",
    { title: "Remove saved documents?", confirmLabel: "Remove documents" }
  );
  if (!ok) return;
  documentsStatus.textContent = "";
  try {
    const res = await fetch("/api/v1/history/documents", { method: "DELETE" });
    const data = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, data, "Could not remove the saved documents."));
    const n = data.forgotten;
    documentsStatus.textContent =
      n === 0 ? "No comparisons had saved documents." : `Removed documents from ${n} comparison${n === 1 ? "" : "s"}.`;
    if (!document.getElementById("result-bar").classList.contains("hidden")) {
      document.getElementById("rerun-control").classList.add("hidden");
      document.getElementById("result-bar-note").classList.remove("hidden");
    }
  } catch (err) {
    documentsStatus.textContent = describeException(err, "Could not remove the saved documents.");
  }
});

let currentHistoryId = null;
const HISTORY_PAGE_SIZE = 20;
let historyOffset = 0;
// Stacks group comparisons of the same device pair and mode (see history.py).
let historyStacks = [];
const expandedStacks = new Set();
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
  showForm();
}

// While a result is on screen the form collapses into #result-bar, so an empty
// form never sits above a result it didn't produce. `fromForm` says whether
// the form still holds the documents behind the result (a fresh compare) or
// not (opened from history); only then can "Change documents" reopen it.
// `canRerun` is false for records whose documents weren't kept.
function showResultBar(data, { fromForm, createdAt, canRerun = true }) {
  const names = data.comparison_matrix.model_name;
  document.getElementById("result-bar-title").textContent = `${names.device_a} vs ${names.device_b}`;
  const mode = data.perspective === "sales" ? "Sales battlecard" : "Neutral comparison";
  document.getElementById("result-bar-meta").textContent = `${mode} \u00b7 ${formatTimestamp(createdAt)}`;
  document.getElementById("change-documents-btn").classList.toggle("hidden", !fromForm);
  document.getElementById("rerun-control").classList.toggle("hidden", !canRerun);
  document.getElementById("result-bar-note").classList.toggle("hidden", canRerun);
  document.getElementById("compare-section").classList.add("hidden");
  document.getElementById("result-bar").classList.remove("hidden");
}

function showForm() {
  document.getElementById("result-bar").classList.add("hidden");
  document.getElementById("compare-section").classList.remove("hidden");
}

function scrollToForm() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  document.getElementById("file-a").focus({ preventScroll: true });
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

  // Back to an empty upload slot, as on first load.
  const reset = () => {
    Object.assign(slotState, emptySlot());
    textArea.value = "";
    nameInput.value = "";
    clearFile();
  };

  return { setFile, reset };
}

const slotA = wireSlot("A");
const slotB = wireSlot("B");

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
    slotA.setFile(base64ToFile(data.device_a.content_base64, data.device_a.filename));
    slotB.setFile(base64ToFile(data.device_b.content_base64, data.device_b.filename));
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
      <span id="compare-status-text" class="flex-1">${
        state.mode === "sales" ? "Comparing your device with the competitor…" : "Comparing Device A and Device B…"
      }</span>
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
    const keep = keepDocuments();
    formData.append("keep_documents", String(keep));
    for (const id of ["a", "b"]) {
      const slot = state.slots[id];
      if (slot.mode === "file") {
        formData.append(`device_${id}_file`, slot.file);
      } else {
        formData.append(`device_${id}_text`, slot.text);
        if (slot.name.trim()) formData.append(`device_${id}_name`, slot.name.trim());
      }
    }
    const res = await fetch("/api/v1/compare", { method: "POST", body: formData, headers: llmHeaders() });
    const data = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, data, "The comparison failed."));
    setExportTarget(data.history_id);
    setUrlId(data.history_id);
    await loadHistory();
    showResultBar(data, { fromForm: true, createdAt: new Date().toISOString(), canRerun: keep });
    renderResults(data);
  } catch (err) {
    showError(describeException(err, "The comparison failed. Try again."));
  } finally {
    setLoading(false);
  }
}

document.getElementById("synthesize-btn").addEventListener("click", synthesize);

// The result is already saved in history, so starting over needs no confirmation.
document.getElementById("new-comparison-btn").addEventListener("click", () => {
  slotA.reset();
  slotB.reset();
  clearError();
  hideResults();
  setExportTarget(null);
  setUrlId(null);
  renderHistoryList();
  scrollToForm();
});

// Reads the open comparison's saved documents again with the AI chosen in
// Settings; the new result is saved and lands in the same history stack.
async function rerun() {
  if (state.busy || !currentHistoryId) return;
  const bar = document.getElementById("result-bar");
  const setRerunning = (on) => {
    state.busy = on;
    bar.querySelectorAll("button").forEach((btn) => (btn.disabled = on));
    document.getElementById("rerun-spinner").classList.toggle("hidden", !on);
    document.getElementById("rerun-label").textContent = on ? "Rerunning\u2026" : "Rerun";
  };
  clearError();
  setRerunning(true);
  try {
    const res = await fetch(`/api/v1/history/${currentHistoryId}/rerun`, { method: "POST", headers: llmHeaders() });
    const data = await readJson(res);
    if (!res.ok) throw new Error(describeFailure(res, data, "The rerun failed."));
    const fromForm = !document.getElementById("change-documents-btn").classList.contains("hidden");
    setExportTarget(data.history_id);
    setUrlId(data.history_id);
    await loadHistory();
    showResultBar(data, { fromForm, createdAt: new Date().toISOString() });
    renderResults(data);
  } catch (err) {
    showError(describeException(err, "The rerun failed. Try again."));
  } finally {
    setRerunning(false);
  }
}

document.getElementById("rerun-btn").addEventListener("click", rerun);

// Reopens the form with the documents that produced the result, so one side
// can be swapped; the result stays below until the next compare.
document.getElementById("change-documents-btn").addEventListener("click", () => {
  showForm();
  scrollToForm();
});

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
  if (a == null || b == null) return "Spec comparison, no install scores";
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

// A row that opens one comparison, plus its delete button.
function historyRow(item, { title, subtitle }) {
  const plainLabel = `${escapeHtml(item.device_a_name)} vs ${escapeHtml(item.device_b_name)}`;
  const isCurrent = item.id === currentHistoryId;
  return `
    <li class="flex items-center gap-2">
      <button type="button" data-history-id="${item.id}"${isCurrent ? ' aria-current="true"' : ""}
              class="flex-1 min-w-0 text-left py-3 px-2 rounded-control ring-1 ring-inset hover:bg-plate transition-colors ${isCurrent ? "bg-plate ring-action" : "ring-transparent hover:ring-ink-muted"}">
        ${title}
        ${subtitle}
      </button>
      <button type="button" data-delete-id="${item.id}" aria-label="Delete comparison: ${plainLabel}, ${escapeHtml(formatTimestamp(item.created_at))}"
              class="shrink-0 w-9 h-9 inline-flex items-center justify-center rounded-control text-lg leading-none text-ink-muted hover:text-on-danger hover:bg-danger transition-colors">
        &times;
      </button>
    </li>`;
}

function historyLabel(item) {
  const plainLabel = `${escapeHtml(item.device_a_name)} vs ${escapeHtml(item.device_b_name)}`;
  const highlighted = `${highlightMatch(item.device_a_name, historySearch)} vs ${highlightMatch(item.device_b_name, historySearch)}`;
  return `<span class="block font-medium truncate" title="${plainLabel}">${highlighted}</span>`;
}

// The card every history entry gets in the drawer. A stack adds the plate
// peeking out underneath; `trailing` is its runs toggle, or a delete button.
function historyCard(item, { timestamp, trailing, stacked = false }) {
  const isCurrent = item.id === currentHistoryId;
  const layer = stacked
    ? "relative isolate mb-1 before:absolute before:inset-x-2 before:-bottom-1 before:h-2 before:-z-10 before:rounded-b-control before:border before:border-t-0 before:border-line before:bg-plate"
    : "";
  return `
    <div class="${layer} flex items-center gap-1 rounded-control border ${isCurrent ? "border-action" : "border-line has-[[data-history-id]:hover]:border-ink-muted"} bg-plate px-2 transition-colors">
      <button type="button" data-history-id="${item.id}"${isCurrent ? ' aria-current="true"' : ""}
              class="flex-1 min-w-0 text-left py-3 rounded-control">
        ${historyLabel(item)}
        <span class="block text-ink-muted truncate">${escapeHtml(easierLabel(item))}</span>
        <span class="block text-ink-muted">${escapeHtml(timestamp)}</span>
      </button>
      ${trailing}
    </div>`;
}

function singleHistoryRow(item) {
  const plainLabel = `${escapeHtml(item.device_a_name)} vs ${escapeHtml(item.device_b_name)}`;
  const deleteBtn = `
    <button type="button" data-delete-id="${item.id}" aria-label="Delete comparison: ${plainLabel}, ${escapeHtml(formatTimestamp(item.created_at))}"
            class="shrink-0 w-9 h-9 inline-flex items-center justify-center rounded-control text-lg leading-none text-ink-muted hover:text-on-danger hover:bg-danger transition-colors">
      &times;
    </button>`;
  return `<li>${historyCard(item, { timestamp: formatTimestamp(item.created_at), trailing: deleteBtn })}</li>`;
}

// Several runs of the same comparison: the newest is shown, the rest fold away.
function stackedHistoryRows(stack, index) {
  const members = [stack.latest, ...stack.others];
  const redundant = new Set(stack.redundant_ids);
  const isExpanded =
    expandedStacks.has(stack.key) || stack.others.some((item) => item.id === currentHistoryId);
  const panelId = `history-stack-${index}`;
  const memberRows = members
    .map((item) =>
      historyRow(item, {
        title: `<span class="block font-medium">${escapeHtml(formatTimestamp(item.created_at))}</span>`,
        subtitle: `
          <span class="block text-ink-muted truncate">${escapeHtml(easierLabel(item))}</span>
          ${redundant.has(item.id) ? '<span class="block text-ink-muted">Same documents as a newer run</span>' : ""}`,
      })
    )
    .join("");
  const copies = stack.redundant_ids.length;
  const dedupe = copies
    ? `<li class="pt-1 pb-2 px-2">
         <button type="button" data-dedupe-id="${stack.latest.id}" data-dedupe-count="${copies}"
                 class="text-sm font-medium text-ink-muted hover:text-danger-text underline underline-offset-2 rounded-control transition-colors">
           Delete ${copies} older ${copies === 1 ? "copy" : "copies"}
         </button>
       </li>`
    : "";
  const toggle = `
    <button type="button" data-stack-toggle="${escapeHtml(stack.key)}" aria-expanded="${isExpanded}" aria-controls="${panelId}"
            class="shrink-0 inline-flex items-center gap-1 h-9 px-2 rounded-control text-sm text-ink-muted hover:text-on-action hover:bg-action transition-colors">
      <span class="font-mono tabular-nums">${members.length}</span> runs
      <svg class="w-4 h-4 transition-transform ${isExpanded ? "rotate-180" : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>`;
  return `
    <li>
      ${historyCard(stack.latest, {
        timestamp: `Latest ${formatTimestamp(stack.latest.created_at)}`,
        trailing: toggle,
        stacked: true,
      })}
      <ul id="${panelId}" class="${isExpanded ? "" : "hidden "}mt-2 ml-2 pl-3 border-l border-line">
        ${memberRows}
        ${dedupe}
      </ul>
    </li>`;
}

function renderHistoryList() {
  const list = document.getElementById("history-list");
  const empty = document.getElementById("history-empty");
  const clearAllBtn = document.getElementById("clear-history-btn");
  clearAllBtn.classList.toggle("hidden", historyStacks.length === 0);
  if (historyStacks.length === 0) {
    list.innerHTML = "";
    empty.textContent = historySearch
      ? "No comparisons match that search."
      : "No comparisons yet. Compare two guides and they will show up here.";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = historyStacks
    .map((stack, i) => (stack.others.length ? stackedHistoryRows(stack, i) : singleHistoryRow(stack.latest)))
    .join("");
}

async function loadHistory({ append = false } = {}) {
  const empty = document.getElementById("history-empty");
  const loadMoreBtn = document.getElementById("load-more-history-btn");
  const offset = append ? historyOffset : 0;
  const q = historySearch ? `&q=${encodeURIComponent(historySearch)}` : "";
  try {
    const res = await fetch(`/api/v1/history/stacks?limit=${HISTORY_PAGE_SIZE}&offset=${offset}${q}`);
    if (!res.ok) throw new Error();
    const page = await res.json();
    historyStacks = append ? historyStacks.concat(page.items) : page.items;
    historyOffset = offset + page.items.length;
    if (!historySearch) {
      historyTotalAll = page.record_total;
      updateHistoryCount();
    }
    renderHistoryList();
    loadMoreBtn.classList.toggle("hidden", historyOffset >= page.total);
    return historyStacks;
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
    showResultBar(record.battlecard, {
      fromForm: false,
      createdAt: record.created_at,
      canRerun: (record.sources || []).length === 2,
    });
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

// Deletes the older runs made from the same documents as a newer run in the
// stack; runs from different documents are kept.
async function deleteOlderCopies(id, count) {
  const noun = count === 1 ? "copy" : "copies";
  const ok = await confirmDialog(
    `This deletes ${count} older ${noun} made from the same documents and keeps the newest. It can\u2019t be undone.`,
    { title: `Delete ${count} older ${noun}?`, confirmLabel: `Delete ${noun}` }
  );
  if (!ok) return;
  const stack = historyStacks.find((s) => s.latest.id === id);
  const removed = new Set(stack ? stack.redundant_ids : []);
  try {
    const res = await fetch(`/api/v1/history/${id}/copies`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      throw new Error(describeFailure(res, await readJson(res), "Could not delete the older copies."));
    }
    if (removed.has(currentHistoryId)) {
      setExportTarget(null);
      hideResults();
      setUrlId(null, { replace: true });
    }
    await loadHistory();
  } catch (err) {
    showError(describeException(err, "Could not delete the older copies."));
  }
}

function toggleStack(button) {
  const key = button.dataset.stackToggle;
  const expand = button.getAttribute("aria-expanded") !== "true";
  if (expand) expandedStacks.add(key);
  else expandedStacks.delete(key);
  button.setAttribute("aria-expanded", String(expand));
  button.querySelector("svg").classList.toggle("rotate-180", expand);
  document.getElementById(button.getAttribute("aria-controls")).classList.toggle("hidden", !expand);
}

document.getElementById("history-list").addEventListener("click", (e) => {
  const toggleBtn = e.target.closest("[data-stack-toggle]");
  if (toggleBtn) {
    toggleStack(toggleBtn);
    return;
  }
  const dedupeBtn = e.target.closest("[data-dedupe-id]");
  if (dedupeBtn) {
    deleteOlderCopies(dedupeBtn.dataset.dedupeId, Number(dedupeBtn.dataset.dedupeCount));
    return;
  }
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

const DOCUMENT_TYPE_LABELS = {
  quick_start_guide: "Quick start guide",
  datasheet: "Datasheet",
  other: "Other document",
};
const DOCUMENT_TYPE_PHRASES = {
  quick_start_guide: "a quick start guide",
  datasheet: "a datasheet",
  other: "another kind of document",
};

function renderVerdict(data, nameA, nameB) {
  const scoreA = data.installation_friction_score_device_a;
  const scoreB = data.installation_friction_score_device_b;
  const subtitle = document.getElementById("friction-subtitle");
  // Friction is only scored when both documents are quick start guides.
  if (scoreA == null || scoreB == null) {
    subtitle.classList.add("hidden");
    const types = data.comparison_matrix.document_type || {};
    const describe = (name, type) =>
      `${escapeHtml(name)} is ${DOCUMENT_TYPE_PHRASES[type] || "not a quick start guide"}`;
    document.getElementById("results-verdict").innerHTML = `
      <p class="md:col-span-2 max-w-[65ch] text-ink-muted">
        Install effort is only compared when both documents are quick start guides.
        ${describe(nameA, types.device_a)}, and ${describe(nameB, types.device_b)}.
      </p>`;
    return;
  }
  subtitle.classList.remove("hidden");
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
  // An empty list means the document named no method; the primary is then only
  // the conservative Console fallback used for scoring, so don't show it.
  const listed = (cm.provisioning_methods || {})[device];
  if (Array.isArray(listed) && listed.length === 0) {
    return `<span class="text-ink-muted">Not specified</span>`;
  }
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

function renderSpecTable(data, nameA, nameB) {
  const cm = data.comparison_matrix;
  const perspective = data.perspective;
  document.getElementById("col-header-a").innerHTML = escapeHtml(nameA) + roleTag(perspective, "a");
  document.getElementById("col-header-b").innerHTML = escapeHtml(nameB) + roleTag(perspective, "b");
  const textOrDash = (value) =>
    value ? escapeHtml(value) : `<span class="text-ink-muted">Not specified</span>`;
  const ipCell = (device) =>
    cm.default_ip[device]
      ? `<span class="font-mono break-all">${escapeHtml(cm.default_ip[device])}</span>${ipLabel(cm.is_private_ip[device])}`
      : `<span class="text-ink-muted">Not specified</span>`;
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
  // Older records have no document types; show the row only when known.
  const types = cm.document_type || {};
  if (types.device_a || types.device_b) {
    const typeCell = (type) =>
      type ? DOCUMENT_TYPE_LABELS[type] || "Other document" : `<span class="text-ink-muted">Not specified</span>`;
    rows.unshift(["Document type", typeCell(types.device_a), typeCell(types.device_b)]);
  }
  // Which model (or the built-in parser) read each document; older records
  // only know whether it was an AI read.
  const readWith = (device) => {
    const named = (cm.extracted_with || {})[device];
    if (named) return escapeHtml(named);
    const source = data[`extraction_source_${device}`];
    if (source === "llm") return "AI model";
    if (source === "heuristic") return "Built-in parser";
    return `<span class="text-ink-muted">Not recorded</span>`;
  };
  rows.unshift(["Read with", readWith("device_a"), readWith("device_b")]);
  // Only an AI read fills these, so show each row only when either side has it.
  const has = (value) => value != null && value !== "" && !(Array.isArray(value) && value.length === 0);
  const present = (values) => values && (has(values.device_a) || has(values.device_b));
  const steps = cm.step_count;
  if (present(steps)) {
    const stepCell = (n) => (n != null ? `${n}` : `<span class="text-ink-muted">Not specified</span>`);
    rows.push(["Setup steps", stepCell(steps.device_a), stepCell(steps.device_b)]);
  }
  const difficulty = cm.setup_difficulty;
  if (present(difficulty)) {
    const difficultyCell = (value) =>
      value ? escapeHtml(value.charAt(0).toUpperCase() + value.slice(1)) : `<span class="text-ink-muted">Not specified</span>`;
    rows.push(["Setup difficulty", difficultyCell(difficulty.device_a), difficultyCell(difficulty.device_b)]);
  }
  [
    ["Setup strengths", cm.setup_strengths],
    ["Setup gaps", cm.setup_gaps],
  ].forEach(([label, values]) => {
    if (present(values)) rows.push([label, listOrDash(values.device_a), listOrDash(values.device_b)]);
  });
  // Open-ended specs (power, certifications...) come from the documents
  // themselves, so the names are escaped like any other extracted text.
  const specCell = (value) =>
    value ? escapeHtml(value) : `<span class="text-ink-muted">Not listed</span>`;
  (cm.extra_specs || []).forEach((row) => {
    rows.push([escapeHtml(row.name), specCell(row.device_a), specCell(row.device_b)]);
  });
  // Below md each spec stacks into a block and the device name is repeated
  // above its value, since a 3-column table is too narrow on a phone.
  const roles = perspective === "sales" ? { a: " (ours)", b: " (competitor)" } : { a: "", b: "" };
  const deviceLabel = (name, id) =>
    `<span class="md:hidden block text-xs font-medium text-ink-muted mb-0.5">${escapeHtml(name)}${roles[id]}</span>`;
  document.getElementById("comparison-table-body").innerHTML = rows
    .map(
      ([label, a, b]) => `
      <tr class="align-top max-md:block max-md:border-b max-md:border-line max-md:py-3">
        <th scope="row" class="md:border-b md:border-line md:py-3 pr-3 text-left font-medium text-ink-muted max-md:block max-md:pb-2">${label}</th>
        <td class="md:border-b md:border-line md:py-3 pr-3 break-words max-md:block max-md:py-1.5">${deviceLabel(nameA, "a")}${a}</td>
        <td class="md:border-b md:border-line md:py-3 pr-3 break-words max-md:block max-md:py-1.5">${deviceLabel(nameB, "b")}${b}</td>
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

  const warnings = data.warnings || [];
  const warningsEl = document.getElementById("results-warnings");
  warningsEl.innerHTML = warnings
    .map(
      (warning) => `
      <li class="flex items-start gap-2 text-ink-muted">
        <span class="led-dot led-dot--amber mt-1.5" aria-hidden="true"></span>
        <span>${escapeHtml(warning)}</span>
      </li>`
    )
    .join("");
  warningsEl.classList.toggle("hidden", warnings.length === 0);

  renderVerdict(data, nameA, nameB);
  document.getElementById("sales-talk-track").textContent = data.sales_talk_track;
  // The pitch only makes sense in a sales comparison; otherwise it is a summary.
  const isSales = data.perspective === "sales";
  document.getElementById("talk-track-heading").textContent = isSales ? "Sales talk track" : "Summary";
  document.getElementById("copy-btn").textContent = isSales ? "Copy talk track" : "Copy summary";
  renderSpecTable(data, nameA, nameB);
  renderAssessment(data, nameA, nameB);
  renderLedGuide(cm, nameA, nameB, data.perspective);

  const results = document.getElementById("results");
  results.classList.remove("hidden");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  results.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  document.getElementById("results-heading").focus({ preventScroll: true });
}
