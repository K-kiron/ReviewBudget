/* Rendering only. All allocation and tier decisions come from the Python planner. */
(() => {
  "use strict";
  const root = document.getElementById("report");
  const notice = document.getElementById("notice");
  const importStatus = document.getElementById("import-status");
  const input = document.getElementById("report-file");
  const dropZone = document.getElementById("drop-zone");
  const tiers = ["deterministic", "light", "comprehensive", "intensive"];
  const maxImportBytes = 2 * 1024 * 1024;
  const human = value => String(value).replace(/_/g, " ");
  const amount = value => new Intl.NumberFormat("en", {maximumFractionDigits: 2}).format(value);
  const priced = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const append = (parent, ...children) => { parent.append(...children); return parent; };
  const attr = (node, key, value) => { node.setAttribute(key, String(value)); return node; };
  const strings = value => Array.isArray(value) && value.every(item => typeof item === "string");
  const isSynthetic = report => (report.provenance?.synthetic ?? report.synthetic) === true;
  const isLocal = report => (report.provenance?.source || report.source) === "local_git";
  const identity = report => isLocal(report)
    ? `${report.repository} / local diff · base ${(report.base_sha || "unknown").slice(0, 8)} / head ${(report.head_sha || "unknown").slice(0, 8)}`
    : `${report.repository} / PR #${report.number}`;

  function validateBudget(budget) {
    if (!object(budget)) throw new Error("Invalid budget summary.");
    for (const key of ["limit_minutes", "mandatory_known_minutes", "selected_known_minutes",
      "estimated_minutes", "shortfall_minutes", "remaining_minutes", "full_baseline_minutes", "baseline_known_minutes", "modeled_savings_minutes", "queue_limit_minutes"]) {
      if (budget[key] !== undefined && budget[key] !== null && !priced(budget[key])) throw new Error(`Invalid budget ${human(key)}.`);
    }
    for (const key of ["unmapped_capabilities", "unpriced_checks", "unpriced_configured_checks"]) {
      if (budget[key] !== undefined && !strings(budget[key])) throw new Error(`Invalid ${human(key)}.`);
    }
    if (budget.within_budget !== undefined && budget.within_budget !== null
        && typeof budget.within_budget !== "boolean") throw new Error("Invalid budget fit state.");
    for (const key of ["estimate_basis", "baseline_basis", "status", "scope"]) {
      if (budget[key] !== undefined && typeof budget[key] !== "string") throw new Error(`Invalid budget ${human(key)}.`);
    }
  }

  function validateReport(report) {
    const fail = message => { throw new Error(message); };
    if (!object(report) || report.schema_version !== 1 || !Number.isInteger(report.tier)
        || !tiers[report.tier] || report.tier_name !== tiers[report.tier]
        || typeof report.repository !== "string" || report.repository.length > 512
        || !Number.isInteger(report.number) || report.number < 1) {
      fail("Open a schema version 1 ReviewBudget analysis report, not a PR snapshot or queue report.");
    }
    // Bound structure before rendering. JSON objects are never merged into application state.
    const pending = [[report, 0]];
    let nodes = 0;
    while (pending.length) {
      const [value, depth] = pending.pop();
      if (++nodes > 60000 || depth > 24) fail("Report structure exceeds the supported limit.");
      if (typeof value === "string" && value.length > 100000) fail("Report text exceeds the supported limit.");
      if (typeof value === "number" && !Number.isFinite(value)) fail("Report numbers must be finite.");
      if (value !== null && typeof value === "object") {
        const values = Object.values(value);
        if (values.length > 5000) fail("Report collection exceeds the supported limit.");
        values.forEach(child => pending.push([child, depth + 1]));
      }
    }
    for (const key of ["risk", "evidence_debt"]) {
      if (!object(report[key]) || !priced(report[key].score) || report[key].score > 100) fail(`Invalid ${human(key)} score.`);
    }
    for (const key of ["missing_evidence", "uncertainties", "limitations"]) {
      if (!strings(report[key])) fail(`Invalid ${human(key)} list.`);
    }
    if (!object(report.evidence) || Object.values(report.evidence).some(value => typeof value !== "boolean")) fail("Evidence entries must be booleans.");
    if (!object(report.plan) || !strings(report.plan.required)) fail("The report is missing its required verification plan.");
    if (!Array.isArray(report.drivers) || report.drivers.length > 3000
        || report.drivers.some(driver => !object(driver) || typeof driver.signal !== "string"
          || typeof driver.message !== "string" || !strings(driver.paths))) fail("Invalid change drivers.");
    if (report.title !== undefined && typeof report.title !== "string" && report.title !== null) fail("Report title must be text.");
    for (const key of ["demo_label", "head_sha", "base_sha", "merge_base_sha", "policy_hash", "report_fingerprint", "source"]) {
      if (report[key] !== undefined && report[key] !== null && typeof report[key] !== "string") fail(`Invalid ${human(key)}.`);
    }
    if (typeof report.input_complete !== "boolean") fail("Input completeness must be a boolean.");
    if (report.provenance !== undefined && !object(report.provenance)) fail("Invalid provenance.");
    if (report.provenance) {
      for (const key of ["source", "feature_timing", "captured_at"]) {
        if (report.provenance[key] !== undefined && report.provenance[key] !== null
            && typeof report.provenance[key] !== "string") fail(`Invalid provenance ${human(key)}.`);
      }
      if (report.provenance.synthetic !== undefined && report.provenance.synthetic !== null
          && typeof report.provenance.synthetic !== "boolean") fail("Synthetic provenance must be a boolean or null.");
    }
    if (report.files !== undefined && (!Array.isArray(report.files) || report.files.length > 3000
        || report.files.some(file => !object(file) || typeof file.filename !== "string"
          || !priced(file.additions) || !priced(file.deletions) || !strings(file.signals)
          || typeof file.classification !== "string"))) fail("Invalid per-file details.");
    if (report.checks !== undefined) {
      if (!Array.isArray(report.checks) || report.checks.length > 500) fail("Expected at most 500 check decisions.");
      const ids = new Set();
      for (const check of report.checks) {
        if (!object(check) || typeof check.id !== "string" || ids.has(check.id)
            || typeof check.name !== "string" || !["selected", "deferred", "not_applicable"].includes(check.status)
            || typeof check.selected !== "boolean" || typeof check.mandatory !== "boolean"
            || typeof check.required !== "boolean" || typeof check.applicable !== "boolean"
            || !strings(check.covers) || !strings(check.reasons)
            || (check.matched_paths !== undefined && !strings(check.matched_paths))
            || (check.estimated_minutes !== null && !priced(check.estimated_minutes))) fail("Invalid or duplicate check decision.");
        if ((check.status === "selected") !== check.selected) fail("Selected check status is inconsistent.");
        ids.add(check.id);
      }
    }
    if (report.budget !== undefined) validateBudget(report.budget);
    return report;
  }

  function renderQueue(queue, reports) {
    const budget = queue.budget;
    const panel = document.getElementById("queue-summary");
    const header = el("div", "queue-heading");
    const heading = append(el("div"), el("p", "eyebrow", "SHARED QUEUE ALLOCATION"), el("h2", "", `${reports.length} ${reports.length === 1 ? "change" : "changes"}. One verification budget.`));
    const over = budget.within_budget === false;
    const fit = budget.within_budget === true;
    append(header, heading, el("span", `tag${fit ? "" : " warning"}`, over ? "Over budget" : fit ? "Within declared budget" : "Budget fit unknown"));
    panel.append(header);
    const costs = el("dl", "queue-costs");
    for (const [label, value] of [["Global limit", budget.limit_minutes], ["Selected known", budget.selected_known_minutes], ["Required known", budget.mandatory_known_minutes], ["Known shortfall", budget.shortfall_minutes]]) {
      const item = el("div");
      append(item, el("dt", "eyebrow", label), append(el("dd", "", priced(value) ? amount(value) : "—"), el("span", "", priced(value) ? " min" : " unknown")));
      costs.append(item);
    }
    panel.append(costs);
    let state = priced(budget.estimated_minutes)
      ? `The complete selected estimate is ${amount(budget.estimated_minutes)} min across this queue.`
      : "Selected known costs are a lower bound. The complete queue cost is unknown.";
    if (over) state += ` Required checks remain selected${priced(budget.shortfall_minutes) ? ` despite a ${priced(budget.estimated_minutes) ? "" : "minimum "}${amount(budget.shortfall_minutes)} min shortfall` : ""}.`;
    else if (!fit) state += " Do not treat zero known shortfall as evidence that the queue fits.";
    else if (priced(budget.remaining_minutes)) state += ` ${amount(budget.remaining_minutes)} min of the shared budget remains.`;
    panel.append(el("p", `queue-state${fit ? "" : " warning"}`, state));
    for (const [label, values] of [["Unpriced selected checks", budget.unpriced_checks || []], ["Unmapped required capabilities", budget.unmapped_capabilities || []]]) {
      if (!values.length) continue;
      const details = el("details", "queue-gaps");
      details.append(el("summary", "", `${label} (${values.length})`));
      details.append(append(el("ul"), ...values.map(value => el("li", "", value))));
      panel.append(details);
    }
    if (queue.recommendations?.length) {
      const notes = el("details", "queue-notes");
      notes.append(el("summary", "", "Allocation recommendations"));
      notes.append(append(el("ul"), ...queue.recommendations.map(value => el("li", "", value))));
      panel.append(notes);
    }
    panel.append(el("p", "estimate-note", "Declared estimates, not measured runtimes. This aggregate applies to the reports below; each report shows its share, not an independent budget."));
  }

  function sectionHead(index, title, aside) {
    const heading = el("div", "section-head");
    append(heading, append(el("h3", "section-title"), el("span", "section-index", index), document.createTextNode(title)));
    if (aside) heading.append(el("span", "quiet", aside));
    return heading;
  }

  function decisionChecks(report) {
    if (report.checks && report.checks.length) return report.checks;
    // Legacy reports name capabilities but do not configure executable checks or prices.
    return report.plan.required.map(id => ({id, name: human(id), covers: [id], reasons: ["Required capability in the verification plan. No check cost is configured."],
      selected: true, mandatory: true, required: false, applicable: true, status: "selected", estimated_minutes: null, matched_paths: []}));
  }

  function renderBudget(report) {
    const budget = report.budget || {};
    const selected = budget.estimated_minutes;
    const baseline = budget.full_baseline_minutes;
    const panel = el("div", "report-summary");
    const allocation = el("section", "allocation");
    attr(allocation, "aria-label", "Modeled verification cost");
    allocation.append(el("p", "eyebrow", "SELECTED / FULL BASELINE · ESTIMATED MINUTES"));
    const numbers = el("div", "big-numbers");
    const primary = el("div", "number-primary", priced(selected) ? amount(selected) : "—");
    primary.append(el("span", "unit", priced(selected) ? "min" : "unpriced"));
    const reference = append(el("div", "number-baseline"), el("span", "versus", "vs"), document.createTextNode(priced(baseline) ? amount(baseline) : "—"), el("span", "unit", priced(baseline) ? "min" : "unpriced"));
    append(numbers, primary, reference);
    allocation.append(numbers);
    let note = "Declared check costs, not measured runtimes. The full baseline includes every configured check.";
    if (!priced(selected)) note = `Complete cost unavailable.${priced(budget.selected_known_minutes) ? ` Known selected costs total ${amount(budget.selected_known_minutes)} min.` : " Configure check estimates to compare allocation costs."}`;
    allocation.append(el("p", "estimate-note", note));
    if (priced(selected) && priced(baseline)) {
      const chart = el("div", "bar-chart");
      const selectedBar = el("div", "bar-track");
      const meter = el("meter");
      meter.min = 0; meter.max = Math.max(1, baseline, selected); meter.value = selected;
      attr(meter, "aria-label", `${amount(selected)} selected minutes out of ${amount(baseline)} full baseline minutes`);
      selectedBar.append(meter);
      append(chart,
        append(el("div", "bar-row"), el("span", "bar-label", "Selected"), selectedBar, el("span", "bar-value", `${amount(selected)} min`)),
        append(el("div", "bar-row"), el("span", "bar-label", "Full suite"), attr(el("div", "bar-track full"), "aria-hidden", "true"), el("span", "bar-value", `${amount(baseline)} min`)));
      allocation.append(chart);
    }
    let state = budget.scope === "queue_share"
      ? `This change is part of a shared${priced(budget.queue_limit_minutes) ? ` ${amount(budget.queue_limit_minutes)} min` : ""} queue budget. Overall fit belongs to the queue summary.`
      : "No budget limit set. Required verification stays in the plan.";
    let warning = false;
    if (budget.status === "not_configured" || !report.checks?.length) {
      state = "Check catalog not configured. Required capabilities are listed below.";
    } else if (budget.within_budget === false || budget.status === "over_budget") {
      state = `Over budget${priced(budget.shortfall_minutes) ? ` by ${priced(selected) ? "" : "at least "}${amount(budget.shortfall_minutes)} min` : ""}. Required checks remain selected.${priced(selected) ? "" : " The complete cost is still unknown."}`;
      warning = true;
    } else if ((budget.unmapped_capabilities || []).length) {
      state = "Coverage gap: required capabilities have no configured check. Budget fit is unknown.";
      warning = true;
    } else if (!priced(selected)) {
      state = "Price all selected checks before judging whether this plan fits the budget.";
      warning = true;
    } else if (priced(budget.limit_minutes)) {
      state = `${amount(selected)} min selected / ${amount(budget.limit_minutes)} min budget. ${priced(budget.remaining_minutes) ? `${amount(budget.remaining_minutes)} min remains.` : "Fit is not established."}`;
      warning = budget.within_budget !== true;
    }
    allocation.append(el("p", `budget-state${warning ? " warning" : ""}`, state));
    const decision = el("section", "decision");
    decision.append(el("p", "eyebrow", "VERIFICATION FLOOR"));
    append(decision, append(el("h3", "", report.tier_name), el("span", "tier-number", `TIER ${report.tier}`)));
    const descriptions = [
      "Deterministic checks are the planner's minimum for this change. Existing repository requirements still apply.",
      "Unit tests and a focused review are the minimum. Add optional checks when the remaining budget allows.",
      "Broader review and integration checks are required. A small budget does not lower this verification floor.",
      "Full verification, security review, and human review are required. Sensitive changes keep their safety floor."
    ];
    decision.append(el("p", "decision-description", descriptions[report.tier]));
    const foot = el("div", "decision-foot");
    foot.append(el("div", "", `STRUCTURAL RISK ${amount(report.risk.score)}/100 · EVIDENCE DEBT ${amount(report.evidence_debt.score)}/100`));
    foot.append(el("div", "quiet", "Uncalibrated signals, not probabilities."));
    decision.append(foot);
    return append(panel, allocation, decision);
  }

  function renderCheckRow(check) {
    const details = el("details", `check-row ${check.status}`);
    const summary = el("summary");
    const name = el("div");
    name.append(el("div", "check-name", check.name));
    const meta = el("div", "check-meta");
    const status = !check.selected ? human(check.status) : check.mandatory ? "required · selected" : "optional · selected";
    meta.append(el("span", `check-status${check.mandatory ? " mandatory" : ""}`, status));
    if (check.required) meta.append(el("span", "", "· repository policy"));
    name.append(meta);
    append(summary, name, el("span", "check-price", priced(check.estimated_minutes) ? `${amount(check.estimated_minutes)} min` : "unpriced"));
    const body = el("div", "check-detail");
    if (check.reasons.length) body.append(append(el("ul"), ...check.reasons.map(reason => el("li", "", reason))));
    body.append(append(el("p"), document.createTextNode("Check ID: "), el("code", "", check.id)));
    body.append(el("p", "", `Covers: ${check.covers.length ? check.covers.map(human).join(", ") : "No verification capability declared."}`));
    if (check.matched_paths?.length) {
      body.append(el("p", "", "Matching changed paths:"));
      for (const path of check.matched_paths) body.append(el("code", "signal-path", path));
    }
    return append(details, summary, body);
  }

  function renderChecks(report) {
    const checks = decisionChecks(report);
    const section = el("section");
    attr(section, "aria-label", "Check allocation");
    section.append(sectionHead("01", "Check allocation", `${checks.filter(check => check.selected).length} of ${checks.length} selected`));
    const filters = attr(el("div", "check-filters"), "role", "group");
    attr(filters, "aria-label", "Filter checks");
    const list = el("div", "check-list");
    attr(list, "aria-live", "polite");
    const options = [["all", "All checks"], ["selected", "Selected"], ["deferred", "Deferred"]];
    if (checks.some(check => check.status === "not_applicable")) options.push(["not_applicable", "Not applicable"]);
    for (const [value, label] of options) {
      const count = value === "all" ? checks.length : checks.filter(check => check.status === value).length;
      const button = attr(el("button", "filter", `${label} ${count}`), "type", "button");
      attr(button, "aria-pressed", value === "all");
      button.addEventListener("click", () => {
        filters.querySelectorAll("button").forEach(node => attr(node, "aria-pressed", node === button));
        const visible = checks.filter(check => value === "all" || check.status === value);
        list.replaceChildren(...visible.map(renderCheckRow));
        if (!visible.length) list.append(el("p", "empty", `No ${human(value)} checks in this plan.`));
      });
      filters.append(button);
    }
    append(section, filters, list);
    list.append(...checks.map(renderCheckRow));
    section.append(el("p", "required-note", "Required means the planner's safety floor or an explicit policy requirement. Deferred checks are optional in this model. Keep all branch-protection requirements."));
    const gaps = report.budget?.unmapped_capabilities || [];
    if (gaps.length) {
      section.append(el("p", "unmapped", `Unmapped required capabilities: ${gaps.map(human).join(", ")}. Configure checks that cover these before treating the plan as complete.`));
    }
    return section;
  }

  function renderEvidence(report) {
    const section = el("section");
    const missing = report.missing_evidence;
    section.append(sectionHead("02", "Evidence to close", missing.length ? `${missing.length} missing` : "None missing by rule"));
    const list = el("div", "evidence-block");
    const keys = [...new Set([...missing, ...Object.keys(report.evidence).filter(key => report.evidence[key])])];
    const labels = {scope: "Scope of the change", verification: "Verification notes", test_changes: "Test changes", linked_issue: "Linked issue", reproduction: "Bug reproduction", migration: "Compatibility / migration", dependency_rationale: "Dependency rationale", rollout: "Rollout / rollback"};
    for (const key of keys) {
      const absent = missing.includes(key);
      append(list, append(el("div", "evidence-row"), el("span", "evidence-label", Object.hasOwn(labels, key) ? labels[key] : human(key)), el("span", `evidence-status ${absent ? "missing" : "present"}`, absent ? "MISSING ↗" : "PRESENT ✓")));
    }
    if (!keys.length) list.append(el("p", "empty", "No evidence entries recorded."));
    append(section, list, el("p", "evidence-caption", "Present means detected in the input. Description evidence is self-reported; test changes do not establish coverage or passing results."));
    return section;
  }

  function renderDrivers(report) {
    const section = el("section", "signals-section");
    section.append(sectionHead("03", "Why this plan"));
    for (const driver of report.drivers) {
      const item = el("div", "signal");
      item.append(el("p", "", driver.message));
      if (driver.paths.length) {
        const paths = el("details", "driver-details");
        paths.append(el("summary", "", `${driver.paths.length} matching ${driver.paths.length === 1 ? "path" : "paths"}`));
        driver.paths.forEach(path => paths.append(el("code", "signal-path", path)));
        item.append(paths);
      }
      section.append(item);
    }
    if (report.files?.length) {
      const files = el("details", "driver-details file-details");
      files.append(el("summary", "", `Inspect ${report.files.length} changed ${report.files.length === 1 ? "file" : "files"}`));
      for (const file of report.files) {
        const item = el("div", "file-item");
        item.append(el("code", "signal-path", file.filename));
        item.append(el("p", "", `${file.classification} · +${amount(file.additions)} / −${amount(file.deletions)}`));
        item.append(el("p", "quiet", file.signals.length ? `Signals: ${file.signals.map(human).join(", ")}` : "No sensitive path signal detected."));
        files.append(item);
      }
      section.append(files);
    }
    return section;
  }

  function renderProvenance(report, synthetic, imported) {
    const section = el("section", "provenance");
    section.append(sectionHead("04", "Source & boundaries", "ADVISORY OUTPUT"));
    const provenance = report.provenance || {};
    const grid = el("dl", "provenance-grid");
    const entries = [
      ["Change", identity(report)],
      ["Input source", imported ? "Local import · unverified content" : synthetic ? "Synthetic example" : provenance.source || "Analysis report"],
      ["Input completeness", report.input_complete === true ? "Complete by planner input checks" : "Incomplete / uncertain input"],
      ["Head commit", report.head_sha || "Not captured"],
      ["Base commit", report.base_sha || "Not captured"],
      ["Policy fingerprint", report.policy_hash || "Not captured"]
    ];
    if (provenance.feature_timing) entries.push(["Feature timing", provenance.feature_timing]);
    if (provenance.captured_at) entries.push(["Captured at", provenance.captured_at]);
    if (imported && (provenance.source || report.source)) entries.push(["Declared source", provenance.source || report.source]);
    if (report.merge_base_sha) entries.push(["Diff merge base", report.merge_base_sha]);
    if (report.report_fingerprint) entries.push(["Report fingerprint", report.report_fingerprint]);
    for (const [label, value] of entries) grid.append(append(el("div", "provenance-item"), el("dt", "", label), el("dd", "", value)));
    section.append(grid);
    const limitations = el("details");
    limitations.append(el("summary", "", "Planner limitations and estimate basis"));
    const items = [...report.limitations];
    if (report.budget?.estimate_basis) items.push(report.budget.estimate_basis);
    if (report.budget?.baseline_basis) items.push(report.budget.baseline_basis);
    limitations.append(append(el("ul"), ...items.map(text => el("li", "", text))));
    section.append(limitations);
    return section;
  }

  function render(report, {synthetic = false, imported = false, queueMember = false} = {}) {
    document.getElementById("queue-summary").hidden = !queueMember;
    root.replaceChildren();
    const heading = el("header", "report-header");
    const text = el("div", "report-heading");
    text.append(el("p", "report-ref", identity(report)));
    text.append(el("h2", "report-title", report.title || report.demo_label || (isLocal(report) ? "Local diff verification plan" : "Pull request verification plan")));
    const hasGaps = report.missing_evidence.length > 0 || report.uncertainties.length > 0 || (report.budget?.unmapped_capabilities || []).length > 0;
    append(heading, text, el("span", `tag${hasGaps ? " warning" : ""}`, hasGaps ? "Evidence needs attention" : "Plan ready to inspect"));
    root.append(heading, renderBudget(report));
    if (report.uncertainties.length) {
      const box = el("section", "uncertainties");
      box.append(el("h3", "", "Incomplete inputs keep the verification floor high."));
      box.append(append(el("ul"), ...report.uncertainties.map(item => el("li", "", human(item)))));
      box.append(el("p", "", "Resolve these input gaps and run the planner again. The viewer does not infer missing data."));
      root.append(box);
    }
    const aside = append(el("aside"), renderEvidence(report), renderDrivers(report));
    root.append(append(el("div", "main-grid"), renderChecks(report), aside));
    root.append(renderProvenance(report, synthetic, imported));
    notice.className = `notice${imported ? " imported" : ""}`;
    notice.textContent = imported
      ? "LOCAL IMPORT · Values and provenance are supplied by this file and have not been independently verified. The report stays in this tab; no network requests or persistent storage."
      : synthetic
        ? "SYNTHETIC EXAMPLE · This is a worked scenario, not a customer result or a measured saving. Inspect the selected checks and their reasons."
        : "ADVISORY REPORT · Check estimates and source identity before acting. This report does not execute checks or change repository requirements.";
    document.title = `ReviewBudget — ${queueMember ? "Queue / " : ""}${report.title || identity(report)}`;
  }

  let payload;
  try {
    payload = JSON.parse(document.getElementById("report-data").textContent);
    if (!Array.isArray(payload.reports) || !payload.reports.length || payload.reports.length > 30) throw new Error("No supported reports are embedded.");
    payload.reports.forEach(validateReport);
    if (payload.queue) {
      if (!object(payload.queue) || payload.queue.schema_version !== 1) throw new Error("Invalid queue allocation.");
      validateBudget(payload.queue.budget);
      if (!priced(payload.queue.budget.limit_minutes) || !priced(payload.queue.budget.selected_known_minutes)
          || !priced(payload.queue.budget.mandatory_known_minutes)) throw new Error("The queue is missing its global budget accounting.");
      if (payload.queue.recommendations !== undefined && !strings(payload.queue.recommendations)) throw new Error("Invalid queue recommendations.");
      renderQueue(payload.queue, payload.reports);
    }
    if (payload.demo || payload.collection || payload.queue) {
      document.getElementById("scenario-section").hidden = false;
      document.getElementById("selector-title").textContent = payload.demo ? "EXPLORE A CHANGE" : "INSPECT A CHANGE";
      document.getElementById("selector-description").textContent = payload.demo
        ? "Synthetic examples · same deterministic planner"
        : payload.queue ? "Per-change allocations · one shared queue budget" : "Analysis reports · original source provenance";
      const tabs = document.getElementById("scenario-tabs");
      attr(tabs, "aria-label", payload.demo ? "Choose a synthetic example" : "Choose a report");
      payload.reports.forEach((report, index) => {
        const button = attr(el("button", "scenario-button"), "type", "button");
        attr(button, "aria-pressed", index === 0);
        append(button, el("span", "scenario-name", report.demo_label || report.title || identity(report)),
          el("span", "scenario-meta", `T${report.tier} · ${priced(report.budget?.estimated_minutes) ? `${amount(report.budget.estimated_minutes)} min selected` : "cost not configured"}`));
        button.addEventListener("click", () => {
          tabs.querySelectorAll("button").forEach(node => attr(node, "aria-pressed", node === button));
          render(report, {synthetic: Boolean(payload.demo || isSynthetic(report)), queueMember: Boolean(payload.queue)});
          importStatus.textContent = "";
        });
        tabs.append(button);
      });
    }
    render(payload.reports[0], {synthetic: Boolean(payload.demo || isSynthetic(payload.reports[0])), queueMember: Boolean(payload.queue)});
  } catch (error) {
    importStatus.textContent = `Cannot display the embedded report: ${error.message}`;
    importStatus.className = "error";
    root.append(el("p", "empty", "The embedded report is invalid. Open a valid local analysis report to continue."));
  }

  async function importFile(file) {
    if (!file) return;
    importStatus.className = "";
    try {
      if (file.size > maxImportBytes) throw new Error("The file exceeds the 2 MiB limit.");
      const text = await file.text();
      let report;
      try { report = JSON.parse(text.replace(/^\uFEFF/, "")); }
      catch { throw new Error("The file is not valid JSON."); }
      validateReport(report);
      render(report, {imported: true});
      document.querySelectorAll(".scenario-button").forEach(button => attr(button, "aria-pressed", "false"));
      importStatus.textContent = `Opened ${file.name}. Read locally; no upload.`;
      root.focus({preventScroll: true});
    } catch (error) {
      importStatus.className = "error";
      importStatus.textContent = `Could not open report: ${error.message} The current report is unchanged.`;
    } finally { input.value = ""; }
  }
  input.addEventListener("change", () => importFile(input.files[0]));
  for (const eventName of ["dragenter", "dragover"]) dropZone.addEventListener(eventName, event => {
    event.preventDefault(); dropZone.classList.add("dragging");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
  dropZone.addEventListener("drop", event => {
    event.preventDefault(); dropZone.classList.remove("dragging");
    if (event.dataTransfer.files.length !== 1) {
      importStatus.className = "error";
      importStatus.textContent = "Drop one ReviewBudget analysis report at a time.";
      return;
    }
    importFile(event.dataTransfer.files[0]);
  });
})();
