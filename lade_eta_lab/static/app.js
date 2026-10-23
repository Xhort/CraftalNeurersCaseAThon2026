const state = {
  condition: "low_quality",
  mitigated: false,
  comparison: [],
  overview: null,
  cases: [],
  caseIndex: 0,
  caseCondition: "combined",
  caseMitigated: true,
  reviewStarted: Date.now(),
  timer: null,
  liveCases: [],
  liveCaseIndex: 0,
  liveResults: {},
  liveOutcome: null,
  liveSelectionNote: "",
  liveRunning: false,
  uploadRows: [],
  uploadFileName: "",
  uploadRunning: false,
};

let API_BASE = "";
const requestCache = new Map();

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fmt = (value, digits = 1) => Number(value).toFixed(digits);
const pct = (value, digits = 0) => `${(Number(value) * 100).toFixed(digits)}%`;
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const escapeHtml = text => String(text).replace(/[&<>'"]/g, character => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[character]));

async function api(path, options = {}) {
  const { headers = {}, ...fetchOptions } = options;
  const method = String(fetchOptions.method || "GET").toUpperCase();
  const requestHeaders = { ...headers };
  if (fetchOptions.body && !Object.keys(requestHeaders).some(name => name.toLowerCase() === "content-type")) {
    requestHeaders["Content-Type"] = "application/json";
  }
  const cacheable = method === "GET"
    && path !== "/api/health"
    && !path.startsWith("/api/decisions");
  const cacheKey = `${API_BASE}${path}`;
  if (cacheable && requestCache.has(cacheKey)) return requestCache.get(cacheKey);

  const pending = (async () => {
    const response = await fetch(cacheKey, { ...fetchOptions, method, headers: requestHeaders });
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  })();
  if (cacheable) requestCache.set(cacheKey, pending);
  try {
    return await pending;
  } catch (error) {
    if (cacheable) requestCache.delete(cacheKey);
    throw error;
  }
}

function resolveBackend() {
  const localHost = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  if (window.location.protocol === "file:" || (localHost && window.location.port !== "8000")) {
    API_BASE = "http://127.0.0.1:8000";
  } else {
    API_BASE = "";
  }
  $$('[data-api-path]').forEach(link => {
    link.href = `${API_BASE}${link.dataset.apiPath}`;
  });
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("show"), 2800);
}

function setupNavigation() {
  const menu = $("#mobile-menu");
  const nav = $(".desktop-nav");
  menu.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    menu.setAttribute("aria-expanded", String(open));
  });
  $$(".nav-link").forEach(link => link.addEventListener("click", () => nav.classList.remove("open")));
  const sections = ["overview", "live-demo", "lab", "oversight", "roadmap"].map(id => document.getElementById(id));
  const observer = new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach(entry => {
      $$(".nav-link").forEach(link => link.classList.toggle("active", link.getAttribute("href") === `#${entry.target.id}`));
    });
  }, { rootMargin: "-35% 0px -55%", threshold: 0 });
  sections.forEach(section => section && observer.observe(section));
  const updateScrollState = () => document.body.classList.toggle("has-scrolled", window.scrollY > 220);
  updateScrollState();
  window.addEventListener("scroll", updateScrollState, { passive: true });
}

function setupHeroProof() {
  const proof = $("#hero-proof");
  const track = $("#proof-track");
  if (!proof || !track) return;

  const tabs = $$(".proof-tab", proof);
  const panels = $$(".proof-panel", proof);
  const previous = $("#proof-prev");
  const next = $("#proof-next");
  const count = $("#proof-count");
  const progress = $("#proof-progress-fill");
  let activeIndex = 0;

  const showStep = (requestedIndex, focusTab = false) => {
    activeIndex = clamp(requestedIndex, 0, panels.length - 1);
    proof.dataset.activeStep = String(activeIndex);
    track.style.transform = `translateX(-${activeIndex * 100}%)`;
    progress.style.width = `${((activeIndex + 1) / panels.length) * 100}%`;
    count.textContent = `${String(activeIndex + 1).padStart(2, "0")} / ${String(panels.length).padStart(2, "0")}`;
    previous.disabled = activeIndex === 0;
    next.disabled = activeIndex === panels.length - 1;

    tabs.forEach((tab, index) => {
      const selected = index === activeIndex;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel, index) => {
      const selected = index === activeIndex;
      panel.classList.toggle("active", selected);
      panel.setAttribute("aria-hidden", String(!selected));
    });
    if (focusTab) tabs[activeIndex].focus();
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showStep(index));
    tab.addEventListener("keydown", event => {
      const keys = { ArrowLeft: activeIndex - 1, ArrowRight: activeIndex + 1, Home: 0, End: panels.length - 1 };
      if (!(event.key in keys)) return;
      event.preventDefault();
      showStep(keys[event.key], true);
    });
  });
  previous.addEventListener("click", () => showStep(activeIndex - 1));
  next.addEventListener("click", () => showStep(activeIndex + 1));
  showStep(0);
}

function setupReveals() {
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: .08 });
  $$(".reveal").forEach(node => observer.observe(node));
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("#api-status").textContent = health.results_ready ? "API + results ready" : "API needs results";
    $(".status-dot").classList.toggle("online", health.results_ready);
    $("#backend-banner").hidden = true;
  } catch (error) {
    $("#api-status").textContent = "Live backend temporarily offline";
    $("#backend-banner").hidden = false;
    toast("The design is loaded, but the online evidence service did not respond. Try refreshing.");
  }
}

async function loadOverview() {
  state.overview = await api("/api/overview");
  const { profile, finding, experiment } = state.overview;
  $("#evidence-label").textContent = state.overview.evidence_label;
  $("#evidence-detail").textContent = `${profile.source_rows.toLocaleString()} orders · ${experiment.runs} matched runs · seeds ${experiment.seeds.join(", ")}`;
  const values = await api("/api/comparison");
  const rows = values.records;
  const row = (condition, mitigated) => rows.find(item => item.condition === condition && item.mitigated === mitigated);
  $("#hero-reference").textContent = `${fmt(row("reference", false).mae_minutes_mean)} min`;
  $("#hero-noisy").textContent = `${fmt(row("low_quality", false).mae_minutes_mean)} min`;
  $("#hero-mitigated").textContent = `${fmt(row("low_quality", true).mae_minutes_mean)} min`;
  $("#hero-delta").textContent = `+${fmt(finding.low_quality_delta)} min worse`;
  $("#hero-recovery").textContent = `only ${fmt(finding.low_quality_recovery)} min recovered`;
}

function selectedLiveCase() {
  return state.liveCases[state.liveCaseIndex] || null;
}

function formatSignedMinutes(value) {
  const number = Number(value);
  const sign = number > 0 ? "+" : number < 0 ? "−" : "";
  return `${sign}${fmt(Math.abs(number))} min`;
}

function liveSignal(result, key) {
  return result?.input_signals?.find(signal => signal.key === key) || null;
}

function syncLiveControls() {
  const available = state.liveCases.length > 0;
  const clean = $("#run-clean-prediction");
  const poisoned = $("#run-poisoned-prediction");
  const safeguarded = $("#run-safeguarded-prediction");
  clean.disabled = state.liveRunning || !available;
  poisoned.disabled = state.liveRunning || !state.liveResults.clean;
  safeguarded.disabled = state.liveRunning || !state.liveResults.poisoned;
  $("#reset-live-demo").disabled = state.liveRunning || !Object.keys(state.liveResults).length;
  $("#live-case-select").disabled = state.liveRunning || !available;
  [clean, poisoned, safeguarded].forEach(button => button.classList.remove("primary"));
  if (!state.liveResults.clean) clean.classList.add("primary");
  else if (!state.liveResults.poisoned) poisoned.classList.add("primary");
  else if (!state.liveResults.safeguarded) safeguarded.classList.add("primary");
}

function resetLiveDemo() {
  state.liveResults = {};
  state.liveOutcome = null;
  [["clean", "live-clean"], ["poisoned", "live-poisoned"], ["safeguarded", "live-safeguarded"]].forEach(([scenario, prefix]) => {
    $(`#${prefix}-value`).textContent = "—";
    const card = $(`#${prefix}-card`);
    card.classList.remove("complete");
    card.classList.toggle("locked", scenario !== "clean");
  });
  $("#live-clean-copy").textContent = "Run the fitted AI on the untouched held-out evidence.";
  $("#live-clean-trust").textContent = "100%";
  $("#live-poisoned-copy").textContent = "Use the model retrained on the simulated unreliable feed and this delivery’s matching degraded inputs.";
  $("#live-poisoned-delta").textContent = "—";
  $("#live-safeguarded-copy").textContent = "Weight trust, back off suspicious inputs, and check review risk.";
  $("#live-review-status").textContent = "—";
  $("#live-result-title").textContent = "Start with the trusted prediction.";
  $("#live-result-copy").textContent = "Each step makes a fresh request to the online backend and evaluates the corresponding fitted model.";
  $("#live-actual-value").textContent = "Hidden until comparison";
  $("#live-algorithm-label").textContent = "REAL AI · PREDICTION CALCULATED NOW";
  $("#live-run-status").textContent = state.liveCases.length ? "READY TO RUN" : "LOADING CASES";
  syncLiveControls();
}

function renderLiveCase() {
  const item = selectedLiveCase();
  if (!item) return;
  $("#live-case-select").value = String(state.liveCaseIndex);
  $("#live-case-id").textContent = item.package_id;
  $("#live-case-region").textContent = `${item.city} · ${item.region_id}`;
  const accepted = new Date(item.accepted_at);
  $("#live-case-time").textContent = Number.isNaN(accepted.getTime())
    ? item.service_date
    : accepted.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  $("#live-selection-note").textContent = state.liveSelectionNote;
  resetLiveDemo();
}

async function loadLiveDemoCases() {
  try {
    const response = await api("/api/live-demo/cases");
    state.liveCases = response.cases || [];
    state.liveSelectionNote = response.selection_note || "These are disclosed teaching examples from the fixed held-out set.";
    state.liveCaseIndex = 0;
    const select = $("#live-case-select");
    select.innerHTML = state.liveCases.map((item, index) => `<option value="${index}">${escapeHtml(item.label)} · package ${escapeHtml(item.package_id)}</option>`).join("");
    if (!state.liveCases.length) throw new Error("No live-demo cases are available.");
    renderLiveCase();
  } catch (error) {
    state.liveCases = [];
    $("#live-case-select").innerHTML = "<option>Live cases are temporarily unavailable</option>";
    $("#live-case-select").disabled = true;
    $("#live-run-status").textContent = "BACKEND OFFLINE";
    $("#live-result-title").textContent = "The interface is ready; the online API did not respond.";
    $("#live-result-copy").textContent = error.message;
    syncLiveControls();
  }
}

function showPoisonedOutcome(result) {
  const clean = state.liveResults.clean;
  const outcome = state.liveOutcome;
  if (!clean || !outcome) return;
  const errors = outcome.absolute_error_minutes;
  const actual = Number(outcome.actual_duration_minutes_demo_only);
  const harm = Number(errors.poisoned) - Number(errors.clean);
  const shift = Number(result.prediction_minutes) - Number(clean.prediction_minutes);
  $("#live-actual-value").textContent = `${fmt(actual)} min`;
  if (harm > .05) {
    $("#live-result-title").textContent = `This delivery became ${fmt(harm)} min less accurate.`;
    $("#live-result-copy").textContent = `The prediction moved ${formatSignedMinutes(shift)}, from ${fmt(clean.prediction_minutes)} to ${fmt(result.prediction_minutes)} minutes. After revealing the ${fmt(actual)}-minute outcome, the miss grew from ${fmt(errors.clean)} to ${fmt(errors.poisoned)} minutes.`;
  } else if (harm < -.05) {
    $("#live-result-title").textContent = `Counterexample: this delivery improved by ${fmt(Math.abs(harm))} min.`;
    $("#live-result-copy").textContent = `The prediction moved ${formatSignedMinutes(shift)} and landed closer to the ${fmt(actual)}-minute outcome. One row can improve even though average error worsened across all 3,407 held-out cases.`;
  } else {
    $("#live-result-title").textContent = "This delivery’s accuracy was effectively unchanged.";
    $("#live-result-copy").textContent = `The prediction moved ${formatSignedMinutes(shift)}, but its miss stayed at about ${fmt(errors.poisoned)} minutes. Aggregate evidence—not one delivery—drives the conclusion.`;
  }
}

function showSafeguardedOutcome(result) {
  const outcome = state.liveOutcome;
  if (!outcome) return;
  const errors = outcome.absolute_error_minutes;
  const recovery = Number(errors.poisoned) - Number(errors.safeguarded);
  const cleanGap = Number(errors.safeguarded) - Number(errors.clean);
  const review = result.review?.flagged ? "It was sent to human review." : "It was not sent to human review.";
  if (recovery > .05) {
    $("#live-result-title").textContent = `The safeguard recovered ${fmt(recovery)} min on this delivery.`;
    const comparison = cleanGap > .05
      ? `Its ${fmt(errors.safeguarded)}-minute miss is still ${fmt(cleanGap)} minutes above the trusted result.`
      : `For this one delivery, it matched or beat the trusted result.`;
    $("#live-result-copy").textContent = `The miss fell from ${fmt(errors.poisoned)} to ${fmt(errors.safeguarded)} minutes. ${comparison} ${review}`;
  } else if (recovery < -.05) {
    $("#live-result-title").textContent = `The safeguard made this delivery ${fmt(Math.abs(recovery))} min worse.`;
    $("#live-result-copy").textContent = `The miss rose from ${fmt(errors.poisoned)} to ${fmt(errors.safeguarded)} minutes. ${review} This failure is why the site recommends no deployment yet.`;
  } else {
    $("#live-result-title").textContent = "The safeguard did not materially change this delivery.";
    $("#live-result-copy").textContent = `Its miss stayed near ${fmt(errors.safeguarded)} minutes. ${review} The full experiment determines whether the policy is useful.`;
  }
}

async function runLiveInference(scenario) {
  const item = selectedLiveCase();
  if (!item || state.liveRunning) return;
  state.liveRunning = true;
  $("#live-run-status").textContent = "CALCULATING LIVE…";
  syncLiveControls();
  try {
    const result = await api("/api/live-demo/predict", {
      method: "POST",
      body: JSON.stringify({ package_id: item.package_id, scenario }),
    });
    state.liveResults[scenario] = result;
    $("#live-algorithm-label").textContent = `${result.model.algorithm} · LIVE INFERENCE`;
    $("#live-algorithm-label").title = `Fitted artifact: ${result.model.artifact}`;
    if (scenario === "clean") {
      $("#live-clean-value").textContent = fmt(result.prediction_minutes);
      $("#live-clean-card").classList.add("complete");
      const trust = liveSignal(result, "source_reliability");
      $("#live-clean-trust").textContent = trust ? `${fmt(trust.value_used_by_selected_scenario, 0)}%` : "100%";
      $("#live-clean-copy").textContent = `Fresh AI result. Expected range: ${fmt(result.interval.low_minutes)}–${fmt(result.interval.high_minutes)} minutes.`;
      $("#live-poisoned-card").classList.remove("locked");
      $("#live-result-title").textContent = `Trusted prediction: ${fmt(result.prediction_minutes)} minutes.`;
      $("#live-result-copy").textContent = "The real delivery time is still hidden. Now run the full degraded pipeline on this same package.";
    }
    if (scenario === "poisoned") {
      $("#live-poisoned-value").textContent = fmt(result.prediction_minutes);
      $("#live-poisoned-card").classList.add("complete");
      const shift = Number(result.prediction_minutes) - Number(state.liveResults.clean.prediction_minutes);
      $("#live-poisoned-delta").textContent = formatSignedMinutes(shift);
      const changed = result.inputs_changed_by_poisoning || [];
      const changedText = changed.length ? changed.slice(0, 3).join(", ") : "the documented reliability context";
      $("#live-poisoned-copy").textContent = `Full pipeline condition: corrupted training evidence plus changed ${changedText.toLowerCase()} for this delivery.`;
      $("#live-safeguarded-card").classList.remove("locked");
      state.liveOutcome = await api(`/api/live-demo/outcome/${encodeURIComponent(item.package_id)}`);
      showPoisonedOutcome(result);
    }
    if (scenario === "safeguarded") {
      $("#live-safeguarded-value").textContent = fmt(result.prediction_minutes);
      $("#live-safeguarded-card").classList.add("complete");
      const flagged = Boolean(result.review?.flagged);
      $("#live-review-status").textContent = flagged ? "Sent to review" : "Not flagged";
      $("#live-safeguarded-copy").textContent = flagged
        ? `Weighted training and input backoff ran; the policy also deferred this ETA for a person to check. ${result.review.reason}`
        : "Weighted training and input backoff ran, but the policy did not defer this ETA for human review.";
      showSafeguardedOutcome(result);
    }
    $("#live-run-status").textContent = "FRESH RESULT RETURNED";
  } catch (error) {
    $("#live-run-status").textContent = "COULD NOT RUN";
    toast(error.message);
  } finally {
    state.liveRunning = false;
    syncLiveControls();
  }
}

function setupLiveDemo() {
  $("#live-case-select").addEventListener("change", event => {
    state.liveCaseIndex = Number(event.target.value);
    renderLiveCase();
  });
  $("#run-clean-prediction").addEventListener("click", () => runLiveInference("clean"));
  $("#run-poisoned-prediction").addEventListener("click", () => runLiveInference("poisoned"));
  $("#run-safeguarded-prediction").addEventListener("click", () => runLiveInference("safeguarded"));
  $("#reset-live-demo").addEventListener("click", resetLiveDemo);
  resetLiveDemo();
}

const UPLOAD_REQUIRED_FIELDS = [
  "region_code", "aoi_type_code", "accept_hour", "day_of_week", "distance_km",
  "courier_recent_accepts", "area_historical_eta", "courier_historical_eta",
];

function parseCsv(text) {
  const rows = [];
  let row = [], value = "", quoted = false;
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') { value += '"'; index++; }
      else if (character === '"') quoted = false;
      else value += character;
    } else if (character === '"') quoted = true;
    else if (character === ",") { row.push(value.trim()); value = ""; }
    else if (character === "\n") { row.push(value.trim()); if (row.some(cell => cell !== "")) rows.push(row); row = []; value = ""; }
    else if (character !== "\r") value += character;
  }
  row.push(value.trim());
  if (row.some(cell => cell !== "")) rows.push(row);
  if (quoted) throw new Error("The CSV contains an unclosed quoted value.");
  return rows;
}

function setUploadStatus(label, title, detail, error = false) {
  const status = $("#upload-status");
  status.classList.toggle("error", error);
  status.innerHTML = `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(title)}</strong></div><small>${detail}</small>`;
}

function clearUpload() {
  state.uploadRows = [];
  state.uploadFileName = "";
  state.uploadRunning = false;
  $("#upload-data-input").value = "";
  $("#run-upload-predictions").disabled = true;
  $("#clear-upload").disabled = true;
  $("#upload-results").innerHTML = '<div class="upload-empty">A preview and batch prediction results will appear here.</div>';
  setUploadStatus("NO FILE SELECTED", "Upload the sample or a prepared dataset export.", "The algorithm will not use <code>duration_minutes</code> as an input.");
}

function uploadRowLabel(row, index) {
  return row.package_id || `row-${index + 1}`;
}

function renderUploadPreview() {
  const rows = state.uploadRows;
  $("#upload-results").innerHTML = `<table class="upload-table"><thead><tr><th>Row</th><th>Hour</th><th>Distance</th><th>Area history</th><th>Courier history</th><th>Actual</th></tr></thead><tbody>${rows.map((row, index) => `<tr><td>${escapeHtml(uploadRowLabel(row, index))}${row.city ? `<small>${escapeHtml(row.city)}</small>` : ""}</td><td>${fmt(row.accept_hour, 2)}</td><td>${fmt(row.distance_km, 2)} km</td><td>${fmt(row.area_historical_eta)} min</td><td>${fmt(row.courier_historical_eta)} min</td><td>${row.duration_minutes == null ? "—" : `${fmt(row.duration_minutes)} min`}</td></tr>`).join("")}</tbody></table>`;
}

async function loadUploadFile(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    setUploadStatus("FILE REJECTED", "The CSV is larger than 2 MB.", "Choose a smaller prepared export with no more than 50 rows.", true);
    return;
  }
  try {
    const parsed = parseCsv(await file.text());
    if (parsed.length < 2) throw new Error("The CSV needs a header and at least one data row.");
    const headers = parsed[0].map(header => header.replace(/^\uFEFF/, "").trim().toLowerCase());
    const missing = UPLOAD_REQUIRED_FIELDS.filter(field => !headers.includes(field));
    if (missing.length) throw new Error(`Missing required columns: ${missing.join(", ")}.`);
    if (parsed.length - 1 > 50) throw new Error("This live demo accepts up to 50 rows per upload.");
    const numberFields = [...UPLOAD_REQUIRED_FIELDS, "duration_minutes"];
    const rows = parsed.slice(1).map((values, index) => {
      const row = Object.fromEntries(headers.map((header, column) => [header, values[column] ?? ""]));
      numberFields.forEach(field => {
        if (!(field in row) || row[field] === "") { if (UPLOAD_REQUIRED_FIELDS.includes(field)) throw new Error(`Row ${index + 2}: ${field} is required.`); row[field] = null; return; }
        row[field] = Number(row[field]);
        if (!Number.isFinite(row[field])) throw new Error(`Row ${index + 2}: ${field} must be a number.`);
      });
      row.package_id = String(row.package_id || `upload-${index + 1}`);
      return row;
    });
    state.uploadRows = rows;
    state.uploadFileName = file.name;
    $("#run-upload-predictions").disabled = false;
    $("#clear-upload").disabled = false;
    const withOutcomes = rows.filter(row => row.duration_minutes != null).length;
    setUploadStatus("READY TO PREDICT", `${file.name} · ${rows.length} row${rows.length === 1 ? "" : "s"}`, `${withOutcomes} row${withOutcomes === 1 ? " has" : "s have"} a retrospective outcome for error comparison.`);
    renderUploadPreview();
  } catch (error) {
    state.uploadRows = [];
    $("#run-upload-predictions").disabled = true;
    $("#clear-upload").disabled = false;
    $("#upload-results").innerHTML = `<div class="upload-empty">${escapeHtml(error.message)}</div>`;
    setUploadStatus("CHECK THE CSV", error.message, "Use the sample file to see the expected prepared-feature format.", true);
  }
}

function predictionCell(result, scenario) {
  const item = result.predictions[scenario];
  const error = item.absolute_error_minutes == null ? "" : `<small>${fmt(item.absolute_error_minutes)} min error</small>`;
  return `${fmt(item.prediction_minutes)} min${error}`;
}

function renderUploadResults(response) {
  const summary = response.summary;
  const metric = value => value == null ? "—" : `${fmt(value)} min`;
  $("#upload-results").innerHTML = `<div class="upload-summary">
    <div><span>ROWS SCORED</span><strong>${summary.rows_scored}</strong></div>
    <div><span>TRUSTED MAE</span><strong>${metric(summary.mae_minutes?.clean)}</strong></div>
    <div><span>UNRELIABLE-FEED MAE</span><strong>${metric(summary.mae_minutes?.poisoned)}</strong></div>
    <div><span>SAFEGUARDED MAE</span><strong>${metric(summary.mae_minutes?.safeguarded)}</strong></div>
  </div><table class="upload-table"><thead><tr><th>Row</th><th>Trusted</th><th>After bad data</th><th>With safeguard</th><th>Review</th></tr></thead><tbody>${response.results.map(result => `<tr><td>${escapeHtml(result.package_id)}</td><td>${predictionCell(result, "clean")}</td><td class="upload-poisoned">${predictionCell(result, "poisoned")}</td><td class="upload-safe">${predictionCell(result, "safeguarded")}</td><td>${result.predictions.safeguarded.review.flagged ? '<span class="upload-review">REVIEW</span>' : "Pass"}</td></tr>`).join("")}</tbody></table>`;
}

async function runUploadPredictions() {
  if (!state.uploadRows.length || state.uploadRunning) return;
  state.uploadRunning = true;
  $("#run-upload-predictions").disabled = true;
  $("#clear-upload").disabled = true;
  setUploadStatus("CALCULATING LIVE", `${state.uploadFileName} · ${state.uploadRows.length} rows`, "Running every row through all three fitted model pipelines now.");
  try {
    const response = await api("/api/live-demo/upload-predict", {
      method: "POST",
      body: JSON.stringify({ rows: state.uploadRows }),
    });
    renderUploadResults(response);
    const outcomeText = response.summary.rows_with_outcomes
      ? `${response.summary.rows_with_outcomes} rows included outcomes, so MAE is shown above.`
      : "No outcomes were included, so the table shows prediction-only results.";
    setUploadStatus("FRESH BATCH RESULT", `${state.uploadRows.length} rows scored across three pipelines`, outcomeText);
  } catch (error) {
    setUploadStatus("COULD NOT RUN", error.message, "Check the CSV values and try again.", true);
    toast(error.message);
  } finally {
    state.uploadRunning = false;
    $("#run-upload-predictions").disabled = !state.uploadRows.length;
    $("#clear-upload").disabled = !state.uploadRows.length;
  }
}

function setupUploadDemo() {
  const input = $("#upload-data-input");
  const dropzone = $("#upload-dropzone");
  input.addEventListener("change", event => loadUploadFile(event.target.files?.[0]));
  ["dragenter", "dragover"].forEach(type => dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(type => dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
  dropzone.addEventListener("drop", event => loadUploadFile(event.dataTransfer?.files?.[0]));
  $("#run-upload-predictions").addEventListener("click", runUploadPredictions);
  $("#clear-upload").addEventListener("click", clearUpload);
  clearUpload();
}

async function loadComparison() {
  const response = await api("/api/comparison");
  state.comparison = response.records;
  renderComparisonChart();
  updateScenario();
}

function summaryRow(condition = state.condition, mitigated = state.mitigated) {
  return state.comparison.find(row => row.condition === condition && row.mitigated === mitigated);
}

function scenarioMessage(condition, mitigated, row) {
  const ref = summaryRow("reference", false);
  const delta = row.mae_minutes_mean - ref.mae_minutes_mean;
  const titles = {
    reference: mitigated ? "The safeguard adds no useful gain here." : "This is the trusted-data comparison point.",
    stale: mitigated ? "The safeguard makes this result worse." : "Old data did not hurt this model in this test.",
    low_quality: mitigated ? "The safeguard helps a little—not enough." : "The unreliable feed makes ETAs worse.",
    combined: mitigated ? "The safeguard fails this combined test." : "Both problems raise error and overwhelm review.",
  };
  const descriptions = {
    reference: "This is the repeatable comparison point for the same future deliveries; it is not assumed to be perfect truth.",
    stale: "Area-history inputs were frozen after training, but this injected change did not raise average error on this evaluation period.",
    low_quality: "A low-reliability feed grew to 55% of the evidence and pushed predictions farther from the real delivery time.",
    combined: "Old area history and the noisy high-volume feed reached the model together on the same future deliveries.",
  };
  let detail = delta > .15 ? ` Average prediction miss is ${fmt(delta)} minutes above the trusted-data reference.` : delta < -.15 ? ` Average prediction miss is ${fmt(Math.abs(delta))} minutes below the reference, so this change did not produce the expected harm.` : " Average prediction miss is effectively unchanged from the reference.";
  if (mitigated) {
    const without = summaryRow(condition, false);
    const effect = without.mae_minutes_mean - row.mae_minutes_mean;
    detail += effect > .15 ? ` The safeguard recovers ${fmt(effect)} minutes.` : ` The safeguard makes the result ${fmt(Math.abs(effect))} minutes worse.`;
  }
  if (row.deferred_rate_mean >= .99) detail += " Every case is sent to a dispatcher, so this is not usable coverage.";
  return { title: titles[condition], detail: descriptions[condition] + detail };
}

async function updateScenario() {
  if (!state.comparison.length) return;
  const row = summaryRow();
  if (!row) return;
  $$(".condition-button").forEach(button => {
    const active = button.dataset.condition === state.condition;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $("#mitigation-toggle").checked = state.mitigated;
  $("#mitigation-label").textContent = state.mitigated ? "On" : "Off";
  const conditionLabels = { reference: "TRUSTED DATA", stale: "OLD DATA", low_quality: "NOISY FEED", combined: "BOTH PROBLEMS" };
  $("#scenario-label").textContent = `${conditionLabels[state.condition]} · SAFEGUARD ${state.mitigated ? "ON" : "OFF"}`;
  $("#scenario-mode").textContent = row.data_mode === "LaDe" ? "REAL EXPERIMENT RESULTS" : "SYNTHETIC PREVIEW";
  $("#metric-mae").textContent = `${fmt(row.mae_minutes_mean)} min`;
  $("#metric-late").textContent = pct(row.over_15_minutes_rate_mean);
  $("#metric-deferred").textContent = pct(row.deferred_rate_mean);
  $("#metric-spread").textContent = Number.isFinite(row.mae_minutes_std) ? `±${fmt(row.mae_minutes_std)}` : "1 seed";
  const message = scenarioMessage(state.condition, state.mitigated, row);
  $("#scenario-outcome").textContent = message.title;
  $("#interpretation p").textContent = message.detail;
  await Promise.all([loadTimeline(), loadCases()]);
}

async function loadTimeline() {
  const response = await api(`/api/timeline?condition=${state.condition}&mitigated=${state.mitigated}`);
  const rows = response.records;
  renderTimeline(rows);
  const last = rows[rows.length - 1] || {};
  $("#quality-age").textContent = `${fmt(last.data_age_days || 0, 0)} days`;
  $("#quality-share").textContent = pct(last.low_quality_share || 0);
  $("#quality-reliability").textContent = pct(last.source_reliability ?? 1);
  $("#quality-coverage").textContent = pct(1 - (last.deferred_rate || 0));
}

function renderTimeline(rows) {
  const target = $("#timeline-chart");
  if (!rows.length) { target.textContent = "No timeline records."; return; }
  const width = 760, height = 245, left = 46, right = 14, top = 14, bottom = 32;
  const values = rows.map(row => Number(row.mae_minutes));
  const min = Math.floor(Math.min(...values) - 4), max = Math.ceil(Math.max(...values) + 4);
  const x = index => left + index * (width - left - right) / Math.max(rows.length - 1, 1);
  const y = value => top + (max - value) * (height - top - bottom) / Math.max(max - min, 1);
  const points = rows.map((row, index) => `${x(index)},${y(row.mae_minutes)}`).join(" ");
  const area = `${left},${height-bottom} ${points} ${x(rows.length-1)},${height-bottom}`;
  const ticks = [min, min + (max-min)/2, max];
  const circles = rows.filter((_, index) => index % Math.max(1, Math.ceil(rows.length / 10)) === 0).map((row, index) => {
    const originalIndex = rows.indexOf(row);
    return `<circle class="chart-point" cx="${x(originalIndex)}" cy="${y(row.mae_minutes)}" r="3.5"><title>${row.service_date}: ${fmt(row.mae_minutes)} min</title></circle>`;
  }).join("");
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="area-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0891b2" stop-opacity=".23"/><stop offset="1" stop-color="#0891b2" stop-opacity="0"/></linearGradient></defs>
    ${ticks.map(tick => `<line class="grid-line" x1="${left}" x2="${width-right}" y1="${y(tick)}" y2="${y(tick)}"/><text class="chart-label" x="4" y="${y(tick)+3}">${fmt(tick,0)}</text>`).join("")}
    <polygon class="line-area" points="${area}"/><polyline class="line-path" points="${points}"/>${circles}
    <text class="chart-label" x="${left}" y="${height-7}">${rows[0].service_date.slice(5)}</text><text class="chart-label" x="${width-right-32}" y="${height-7}">${rows[rows.length-1].service_date.slice(5)}</text>
  </svg>`;
}

function renderComparisonChart() {
  const target = $("#comparison-chart");
  const labels = { reference: "Trusted data", stale: "Old data", low_quality: "Noisy feed", combined: "Both problems" };
  const summaries = {
    reference: "No ordinary-condition benefit",
    stale: "Safeguard makes error worse",
    low_quality: "Small recovery, still above reference",
    combined: "Safeguard makes error worse",
  };
  let html = '<div class="comparison-results">';
  Object.keys(labels).forEach(condition => {
    const without = summaryRow(condition, false);
    const withSafeguard = summaryRow(condition, true);
    const change = withSafeguard.mae_minutes_mean - without.mae_minutes_mean;
    const changeLabel = Math.abs(change) < .15 ? "No meaningful change" : change < 0 ? `${fmt(Math.abs(change))} min recovered` : `${fmt(change)} min worse`;
    const tone = change < -.15 ? "helps" : change > .15 ? "hurts" : "neutral";
    html += `<article class="comparison-result ${tone}">
      <div class="comparison-result-heading"><strong>${labels[condition]}</strong><span>${summaries[condition]}</span></div>
      <div class="comparison-values"><div><small>Safeguard off</small><strong>${fmt(without.mae_minutes_mean)} min</strong></div><i aria-hidden="true">→</i><div><small>Safeguard on</small><strong>${fmt(withSafeguard.mae_minutes_mean)} min</strong></div></div>
      <p>${changeLabel}${withSafeguard.deferred_rate_mean >= .99 ? " · 100% sent to review" : ""}</p>
    </article>`;
  });
  target.innerHTML = html + "</div>";
}

function setupLabControls() {
  const buttons = $$(".condition-button");
  buttons.forEach(button => button.addEventListener("click", () => {
    state.condition = button.dataset.condition;
    updateScenario();
  }));
  buttons.forEach((button, index) => button.addEventListener("keydown", event => {
    let next = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % buttons.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + buttons.length) % buttons.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = buttons.length - 1;
    if (next === null) return;
    event.preventDefault();
    buttons[next].click();
    buttons[next].focus();
  }));
  $("#mitigation-toggle").addEventListener("change", event => {
    state.mitigated = event.target.checked;
    updateScenario();
  });
  $("#auto-compare").addEventListener("click", async () => {
    state.mitigated = !state.mitigated;
    await updateScenario();
    toast(`Safeguard ${state.mitigated ? "on" : "off"} — results updated on the same deliveries.`);
  });
}

async function loadCases() {
  let selectedCondition = state.condition;
  let selectedMitigation = state.mitigated;
  let response = await api(`/api/cases?condition=${selectedCondition}&mitigated=${selectedMitigation}&flagged_only=true&limit=80`);
  if (!response.records.some(item => item.flagged_for_review)) {
    selectedCondition = "combined";
    selectedMitigation = true;
    response = await api("/api/cases?condition=combined&mitigated=true&flagged_only=true&limit=80");
  }
  state.caseCondition = selectedCondition;
  state.caseMitigated = selectedMitigation;
  state.cases = response.records;
  state.caseIndex = 0;
  const select = $("#case-select");
  select.innerHTML = state.cases.map((item, index) => `<option value="${index}">${escapeHtml(item.package_id)}</option>`).join("");
  resetReviewTimer();
  renderCase();
}

function renderCase() {
  const item = state.cases[state.caseIndex];
  if (!item) return;
  $("#case-select").value = String(state.caseIndex);
  $("#case-id").textContent = item.package_id;
  $(".case-status").textContent = item.flagged_for_review ? "NEEDS REVIEW" : "NORMAL OUTPUT";
  $("#case-city").textContent = item.city;
  $("#case-region").textContent = item.region_id;
  $("#case-date").textContent = String(item.service_date).slice(0, 10);
  $("#case-eta").textContent = fmt(item.prediction_minutes, 0);
  $("#case-range").textContent = `${fmt(item.interval_low, 0)}–${fmt(item.interval_high, 0)} min`;
  $("#case-reason").textContent = item.review_reason;
  $("#case-age").textContent = `${fmt(item.data_age_days, 0)} days`;
  $("#case-reliability").textContent = pct(item.source_reliability);
  $("#case-share").textContent = pct(item.low_quality_share);
  $("#case-age-meter").style.width = `${clamp(item.data_age_days / 45 * 100, 2, 100)}%`;
  $("#case-reliability-meter").style.width = `${clamp(item.source_reliability * 100, 2, 100)}%`;
  $("#case-share-meter").style.width = `${clamp(item.low_quality_share * 100, 2, 100)}%`;
  $("#case-reliability-meter").style.background = item.source_reliability < .5 ? "#d94f61" : "#0891b2";
  $("#override-value").value = Math.round(item.prediction_minutes);
}

function resetReviewTimer() {
  state.reviewStarted = Date.now();
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - state.reviewStarted) / 1000);
    $("#review-timer").textContent = `${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`;
  }, 1000);
}

function setupOversight() {
  $("#case-select").addEventListener("change", event => { state.caseIndex = Number(event.target.value); resetReviewTimer(); renderCase(); });
  $("#next-case").addEventListener("click", () => { if (!state.cases.length) return; state.caseIndex = (state.caseIndex + 1) % state.cases.length; resetReviewTimer(); renderCase(); });
  $$(".decision-button").forEach(button => button.addEventListener("click", () => {
    if (button.dataset.decision === "override") {
      openModal($("#override-modal"));
    } else saveDecision(button.dataset.decision);
  }));
  $("#save-override").addEventListener("click", () => saveDecision("override", Number($("#override-value").value), $("#override-note").value));
  $$('[data-close-modal]').forEach(button => button.addEventListener("click", () => closeModal($("#override-modal"))));
  $("#override-modal").addEventListener("click", event => { if (event.target.id === "override-modal") closeModal(event.currentTarget); });
}

async function saveDecision(action, overrideMinutes = null, note = "") {
  const item = state.cases[state.caseIndex];
  if (!item) return;
  const reviewSeconds = Math.round((Date.now() - state.reviewStarted) / 1000);
  try {
    await api("/api/decisions", { method: "POST", body: JSON.stringify({
      package_id: item.package_id,
      condition: state.caseCondition,
      mitigated: state.caseMitigated,
      action,
      override_minutes: overrideMinutes,
      reviewer: "Demo dispatcher",
      review_seconds: reviewSeconds,
      note,
    }) });
    closeModal($("#override-modal"));
    toast(`${action[0].toUpperCase() + action.slice(1)} saved to the audit log.`);
    state.caseIndex = (state.caseIndex + 1) % state.cases.length;
    resetReviewTimer();
    renderCase();
  } catch (error) { toast(error.message); }
}

function openModal(node) { node.classList.add("open"); node.setAttribute("aria-hidden", "false"); }
function closeModal(node) { node.classList.remove("open"); node.setAttribute("aria-hidden", "true"); }

function setupRoadmapFrame() {
  const frame = $("#roadmap-frame");
  if (!frame) return;
  const resize = () => {
    try {
      const documentHeight = frame.contentDocument?.documentElement?.scrollHeight || 0;
      if (documentHeight) frame.style.height = `${documentHeight}px`;
    } catch (_) {}
  };
  const connect = () => {
    resize();
    const body = frame.contentDocument?.body;
    if (body && "ResizeObserver" in window) new ResizeObserver(resize).observe(body);
  };
  frame.addEventListener("load", connect);
  if (frame.contentDocument?.readyState === "complete") connect();
  window.addEventListener("resize", resize, { passive: true });
}

async function init() {
  setupNavigation(); setupHeroProof(); setupReveals(); setupLiveDemo(); setupUploadDemo(); setupLabControls(); setupOversight(); setupRoadmapFrame();
  await resolveBackend();
  try {
    await Promise.all([loadHealth(), loadOverview(), loadComparison(), loadLiveDemoCases()]);
  } catch (error) {
    console.error(error);
    toast(error.message);
  }
}

init();
