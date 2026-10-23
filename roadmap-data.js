/* =============================================================================
   roadmap-data.js — SINGLE SOURCE OF TRUTH for the roadmap video, the roadmap
   section, and the user report. Edit numbers here; everything else updates.

   Values marked  // FROM SITE  were copied from antydata.com on 2026-10-23.
   Values marked  // ASSUMPTION  are planning inputs the team must confirm.
   Values set to  null  are shown qualitatively until you fill them in.
============================================================================= */
window.ROADMAP_DATA = {
  team: "ETA Reliability Lab",
  event: "W&M AI Club Case-A-Thon 2026",
  dataset: "LaDe last-mile deliveries · 31,415 orders · 24 matched runs", // FROM SITE

  results: {
    referenceMin: 62.1,      // FROM SITE  average ETA miss, trusted data only
    noisyMin: 64.2,          // FROM SITE  average ETA miss with the noisy feed
    safeguardMin: 63.7,      // FROM SITE  average ETA miss with our safeguard
    sentToDispatcherPct: 10.7, // FROM SITE share of ETAs sent for human review
    handledWithoutReviewPct: 89, // FROM SITE
    noisyFeedSharePct: 55,   // FROM SITE  share of the evidence from the noisy feed
    sourceTrustPct: 35,      // FROM SITE  trust score assigned to the noisy feed
    repeatRunSpread: 0.3,    // FROM SITE  ± min across repeat runs
    // Old-data ("stale") and combined tests: the site says the safeguard made
    // these WORSE. Fill in the minutes to show exact numbers (else qualitative).
    staleOffMin: null, staleOnMin: null,
    combinedOffMin: null, combinedOnMin: null,
  },

  assumptions: {
    dailyDeliveries: 1000,   // ASSUMPTION  deliveries per day in one pilot region
    reviewSeconds: 30,       // ASSUMPTION  replace with measured review time from the site
    pilotRegion: "one service region (TBD)", // ASSUMPTION
  },

  gates: {                   // proposed pass/fail gates — team decision
    recoverPctOfLoss: 50,    // safeguard must recover >= this % of the noisy-feed loss
    maxSentToDispatcherPct: 15,
    maxRepeatRunSpread: 0.5,
    noConditionWorse: true,  // must not be worse than "no safeguard" on ANY condition
  },

  phases: [
    { tag: "Now", title: "Hold deployment",
      body: "Trusted feed stays the only input to customer ETAs. The noisy feed is logged and monitored, not used.",
      owner: "Operations manager" },
    { tag: "Weeks 1–4", title: "Fix the safeguard",
      body: "Re-test all 8 matched conditions. Must recover at least half of the loss and never make the old-data or combined tests worse. Also attack the 62-minute baseline miss.",
      owner: "Model owner + data-quality owner" },
    { tag: "Weeks 5–8", title: "Shadow pilot, one region",
      body: "New ETAs shown beside current ETAs, not used for routing. Dispatchers review flagged deliveries; we measure review time and correction rates.",
      owner: "Pilot region dispatch lead" },
    { tag: "Week 9+", title: "Gated rollout",
      body: "Region by region, only if every gate passes. The 8-condition test re-runs weekly; any failure pauses rollout.",
      owner: "Operations manager (go / no-go)" },
  ],

  stopConditions: [
    "The safeguard makes any test condition worse than no safeguard",
    "More than 15% of deliveries sent to a dispatcher for 2 days running",
    "Spot checks show the trusted feed itself drifting",
    "A pilot region's area history is more than 7 days old",
    "Severe weather or road closures: switch to manual ETAs",
  ],

  roles: [
    { role: "Dispatchers", does: "See flagged ETAs; accept, correct, or escalate. Never blamed for flagging." },
    { role: "Dispatch lead", does: "Owns the review queue and reports review time weekly." },
    { role: "Data-quality owner", does: "Audits the noisy feed weekly; receives every escalation." },
    { role: "Model owner", does: "Re-runs the 8-condition test weekly; owns the gates." },
    { role: "Operations manager", does: "Go / no-go at each checkpoint." },
  ],

  nextCheckpoint: "End of week 4 (date TBD)",
  videoFile: "/api/downloads/impact_roadmap.mp4", // pre-rendered fallback (optional)
  // Live experiment data from the site. Every scenario video is generated from this file.
  timelineUrl: "/api/downloads/timeline.csv",
  reportPage: "user-report.html",
};

/* Derived numbers — computed, never typed. */
window.deriveRoadmap = function (D) {
  const r = D.results, a = D.assumptions, g = D.gates;
  const loss = +(r.noisyMin - r.referenceMin).toFixed(1);
  const recovered = +(r.noisyMin - r.safeguardMin).toFixed(1);
  const recoveredPct = loss > 0 ? Math.round((100 * recovered) / loss) : 0;
  const gateRecoverMin = +((loss * g.recoverPctOfLoss) / 100).toFixed(2);
  const reviewsPerDay = Math.round((a.dailyDeliveries * r.sentToDispatcherPct) / 100);
  const reviewMinPerDay = Math.round((reviewsPerDay * a.reviewSeconds) / 60);
  const fmt = (n) => Number(n).toLocaleString("en-US");
  return {
    loss, recovered, recoveredPct, gateRecoverMin, reviewsPerDay, reviewMinPerDay,
    // "ETA error" = total minutes by which ETAs miss, summed over a day's deliveries
    errorAvoidedTodayMin: Math.round(recovered * a.dailyDeliveries),
    errorAvoidedAtGateMin: Math.round(gateRecoverMin * a.dailyDeliveries),
    errorAddedByFeedMin: Math.round(loss * a.dailyDeliveries),
    hasStale: r.staleOffMin != null && r.staleOnMin != null,
    hasCombined: r.combinedOffMin != null && r.combinedOnMin != null,
    fmt,
  };
};
window.ROADMAP_DERIVED = window.deriveRoadmap(window.ROADMAP_DATA);

/* =============================================================================
   LIVE DATA: read the site's timeline.csv and aggregate every scenario.
   Expected columns (as served by /api/downloads/timeline.csv):
   service_date, condition, mitigated, seed, data_age_days, low_quality_share,
   source_reliability, mae_minutes, rmse_minutes, over_15_minutes_rate,
   baseline_mae_minutes, deferred_rate, n_test
============================================================================= */
window.ROADMAP_CONDITIONS = {
  reference:   { name: "Trusted data", tag: "Reference",
                 what: "Only verified, up-to-date records reach the model. Every other scenario is measured against this." },
  stale:       { name: "Old data", tag: "Stale",
                 what: "Area history stops refreshing, so the model leans on patterns that no longer match operations." },
  low_quality: { name: "Noisy feed", tag: "Low quality",
                 what: "A fast but error-prone feed floods in and becomes a large share of the evidence." },
  combined:    { name: "Both problems", tag: "Combined",
                 what: "Old area history and the noisy feed at the same time." },
};

window.parseTimelineCSV = function (text) {
  const lines = text.trim().split(/\r?\n/);
  const head = lines.shift().split(",").map((h) => h.trim());
  const rows = [];
  for (const ln of lines) {
    const cells = ln.split(",");
    if (cells.length !== head.length) continue;           // skip truncated rows
    const o = {};
    head.forEach((h, i) => (o[h] = cells[i].trim()));
    o.mitigated = /^(true|1|yes)$/i.test(o.mitigated);
    ["data_age_days", "low_quality_share", "source_reliability", "mae_minutes", "rmse_minutes",
     "over_15_minutes_rate", "baseline_mae_minutes", "deferred_rate", "n_test"].forEach((k) => (o[k] = +o[k]));
    if (Number.isFinite(o.mae_minutes) && o.n_test > 0) rows.push(o);
  }
  return rows;
};

window.aggregateTimeline = function (rows) {
  const groups = {};
  for (const r of rows) {
    const k = r.condition + "|" + r.mitigated;
    (groups[k] = groups[k] || []).push(r);
  }
  const wmean = (rs, f) => rs.reduce((s, r) => s + r[f] * r.n_test, 0) / rs.reduce((s, r) => s + r.n_test, 0);
  const out = {};
  for (const [k, rs] of Object.entries(groups)) {
    const bySeed = {}, byDate = {};
    rs.forEach((r) => { (bySeed[r.seed] = bySeed[r.seed] || []).push(r); (byDate[r.service_date] = byDate[r.service_date] || []).push(r); });
    const seedMae = Object.values(bySeed).map((x) => wmean(x, "mae_minutes"));
    const m = seedMae.reduce((a, b) => a + b, 0) / seedMae.length;
    const sd = Math.sqrt(seedMae.reduce((a, b) => a + (b - m) ** 2, 0) / seedMae.length);
    const dates = Object.keys(byDate).sort();
    out[k] = {
      condition: rs[0].condition, mitigated: rs[0].mitigated,
      mae: wmean(rs, "mae_minutes"), maeSd: sd, seeds: seedMae.length,
      over15: wmean(rs, "over_15_minutes_rate"), deferred: wmean(rs, "deferred_rate"),
      lqShare: wmean(rs, "low_quality_share"), reliability: wmean(rs, "source_reliability"),
      dataAge: wmean(rs, "data_age_days"), baselineMae: wmean(rs, "baseline_mae_minutes"),
      nTest: rs.reduce((s, r) => s + r.n_test, 0) / seedMae.length,
      dates, daily: dates.map((d) => wmean(byDate[d], "mae_minutes")),
      dailyAge: dates.map((d) => wmean(byDate[d], "data_age_days")),
      dailyLq: dates.map((d) => wmean(byDate[d], "low_quality_share")),
    };
  }
  const order = ["reference", "stale", "low_quality", "combined"];
  const conds = [...new Set(rows.map((r) => r.condition))]
    .sort((a, b) => (order.indexOf(a) + 99 * (order.indexOf(a) < 0)) - (order.indexOf(b) + 99 * (order.indexOf(b) < 0)));
  return { groups: out, conditions: conds, get: (c, m) => out[c + "|" + m] || null, rows: rows.length };
};

/* Loads timeline.csv once; overwrites headline results with live numbers. Resolves to aggregates or null. */
window.ROADMAP_READY = (function () {
  const D = window.ROADMAP_DATA;
  const apply = (agg) => {
    const g = (c, m) => agg.get(c, m), R = D.results;
    if (g("reference", false)) R.referenceMin = +g("reference", false).mae.toFixed(1);
    if (g("low_quality", false)) R.noisyMin = +g("low_quality", false).mae.toFixed(1);
    if (g("low_quality", true)) R.safeguardMin = +g("low_quality", true).mae.toFixed(1);
    if (g("stale", false) && g("stale", true)) { R.staleOffMin = g("stale", false).mae; R.staleOnMin = g("stale", true).mae; }
    if (g("combined", false) && g("combined", true)) { R.combinedOffMin = g("combined", false).mae; R.combinedOnMin = g("combined", true).mae; }
    window.ROADMAP_DERIVED = window.deriveRoadmap(D);
    window.ROADMAP_TIMELINE = agg;
    return agg;
  };
  window.applyRoadmapTimeline = apply;
  if (!D.timelineUrl || typeof fetch !== "function") return Promise.resolve(null);
  return fetch(D.timelineUrl, { cache: "no-store" })
    .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
    .then((t) => { const rows = window.parseTimelineCSV(t); return rows.length ? apply(window.aggregateTimeline(rows)) : null; })
    .catch((e) => { console.warn("timeline.csv not loaded:", e); return null; });
})();
