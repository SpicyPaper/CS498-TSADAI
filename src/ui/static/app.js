let state = {
  nodes: [],
  conversations: [],
  currentConversationId: null,
  currentConversation: null,
  inspectorOpen: false,
  inspectorWidth: 390,
  selectedTraceId: null,
  selectedTrace: null,
  selectedTraceLabel: "",
};

const els = {
  appShell: document.querySelector(".app-shell"),
  nodeSelect: document.querySelector("#node-select"),
  conversationList: document.querySelector("#conversation-list"),
  messages: document.querySelector("#messages"),
  prompt: document.querySelector("#prompt"),
  form: document.querySelector("#chat-form"),
  status: document.querySelector("#status"),
  send: document.querySelector("#send"),
  refresh: document.querySelector("#refresh"),
  newChat: document.querySelector("#new-chat"),
  deleteChat: document.querySelector("#delete-chat"),
  inspectorResize: document.querySelector("#inspector-resize"),
  routingInspector: document.querySelector("#routing-inspector"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").split("\n");
  const blocks = [];
  let paragraph = [];
  let list = [];
  let code = [];
  let math = [];
  let inCode = false;
  let inMath = false;

  function flushParagraph() {
    if (paragraph.length) {
      blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  }

  function flushList() {
    if (list.length) {
      blocks.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      list = [];
    }
  }

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith("$$")) {
      if (inMath) {
        const remainder = trimmed.slice(2).trim();
        if (remainder) {
          math.push(remainder);
        }
        blocks.push(`<div class="math-block">$$${escapeHtml(math.join("\n"))}$$</div>`);
        math = [];
        inMath = false;
      } else if (trimmed.endsWith("$$") && trimmed.length > 4) {
        flushParagraph();
        flushList();
        blocks.push(
          `<div class="math-block">$$${escapeHtml(trimmed.slice(2, -2).trim())}$$</div>`
        );
      } else {
        flushParagraph();
        flushList();
        const remainder = trimmed.slice(2).trim();
        if (remainder) {
          math.push(remainder);
        }
        inMath = true;
      }
      continue;
    }

    if (inMath) {
      if (trimmed.endsWith("$$")) {
        math.push(trimmed.slice(0, -2).trim());
        blocks.push(`<div class="math-block">$$${escapeHtml(math.join("\n"))}$$</div>`);
        math = [];
        inMath = false;
      } else {
        math.push(line);
      }
      continue;
    }

    if (line.trim().startsWith("```")) {
      if (inCode) {
        blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = [];
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        inCode = true;
      }
      continue;
    }

    if (inCode) {
      code.push(line);
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  if (inCode) {
    blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  if (inMath) {
    blocks.push(`<div class="math-block">$$${escapeHtml(math.join("\n"))}$$</div>`);
  }
  flushParagraph();
  flushList();
  return blocks.join("");
}

function formatScore(value) {
  if (typeof value !== "number") {
    return "n/a";
  }
  return value.toFixed(2);
}

function formatCapabilities(scores = {}) {
  const entries = Object.entries(scores);
  if (!entries.length) {
    return "none";
  }
  return entries.map(([capability, score]) => `${capability} ${formatScore(score)}`).join(", ");
}

function contributionLabel(value) {
  if (typeof value !== "number") {
    return "n/a";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function addField(parent, label, value) {
  const row = document.createElement("div");
  row.className = "trace-field";

  const name = document.createElement("span");
  name.className = "trace-field-label";
  name.textContent = label;

  const text = document.createElement("span");
  text.className = "trace-field-value";
  text.textContent = value || "n/a";

  row.append(name, text);
  parent.appendChild(row);
}

function nodeDisplayName(peer = {}) {
  return peer.model_name || peer.peer_id || "unknown node";
}

function nodeIdentityText(peer = {}) {
  const name = nodeDisplayName(peer);
  return peer.peer_id && peer.peer_id !== name ? `${name} (${peer.peer_id})` : name;
}

function describeAction(action) {
  const descriptions = {
    forward: "This node selected another node, then forwarded the query to it.",
    forward_failed: "This node selected another node, but the forward attempt failed.",
    execute_local: "This node selected itself as the best candidate. It will generate the answer in the final step below.",
    execute_forwarded_request: "This is the final answer step: this node was selected earlier and now answers the query.",
    no_suitable_node: "Could not find a suitable node.",
    forward_error: "The selected peer returned an error.",
    forwarding_exhausted: "All forward attempts failed, so routing stops.",
    forwarded_generation_error: "The selected peer failed while generating an answer.",
    generation_error: "This node failed while generating an answer.",
  };
  return descriptions[action] || "Processed this query step.";
}

function selectionReasonText(hop) {
  if (hop.selection_reason === "direct_candidate_above_threshold") {
    return "A direct candidate reached the minimum routing score, so it was selected without asking for recommendations.";
  }
  if (hop.selection_reason === "recommended_candidate_above_threshold") {
    return "No direct candidate reached the minimum routing score, then a recommended candidate did, so it was selected.";
  }
  if (hop.selection_reason === "best_seen_below_threshold") {
    return "No candidate reached the minimum routing score, so the best evaluated candidate was used as a fallback.";
  }
  return "The selected node is the best candidate found for this routing step.";
}

function stageLabel(name) {
  if (name === "direct") {
    return "Direct candidates";
  }
  if (name === "recommended") {
    return "Recommended candidates";
  }
  return `${name || "unknown"} candidates`;
}

function stageDescription(name) {
  if (name === "direct") {
    return "Local node plus peers discovered through the DHT/registry are scored first.";
  }
  if (name === "recommended") {
    return "If no direct candidate passes the threshold, peers are asked to suggest nodes. Suggested nodes are then scored here.";
  }
  return "Candidates scored during this stage.";
}

function makeStepHeader(number, title, description) {
  const header = document.createElement("div");
  header.className = "trace-step-header";

  const badge = document.createElement("span");
  badge.className = "trace-step-badge";
  badge.textContent = String(number);

  const text = document.createElement("div");
  const label = document.createElement("div");
  label.className = "trace-step-title";
  label.textContent = title;
  const detail = document.createElement("div");
  detail.className = "trace-step-description";
  detail.textContent = description;

  text.append(label, detail);
  header.append(badge, text);
  return header;
}

function makeTimelineStep(number, title, description) {
  const section = document.createElement("section");
  section.className = "trace-step";
  section.appendChild(makeStepHeader(number, title, description));
  return section;
}

function appendPreviousAttemptNote(parent, hop) {
  if (!hop.previous_attempt) {
    return;
  }

  const note = document.createElement("div");
  note.className = "trace-note trace-request-note";
  note.textContent =
    `No candidate was found for the original needs ` +
    `(${formatCapabilities(hop.previous_attempt.required_capabilities || {})}). ` +
    `The same discovery and scoring process is retried with ` +
    `${formatCapabilities(hop.required_capabilities || {})}.`;
  parent.appendChild(note);
}

function isLocalAnswerAction(action) {
  return action === "execute_local" || action === "execute_local_after_forward_failure";
}

function makeNodeTitle(prefix, peer) {
  const wrapper = document.createElement("div");
  wrapper.className = "trace-node-title";

  const name = document.createElement("div");
  name.className = "trace-node-name";
  name.textContent = `${prefix}${nodeDisplayName(peer)}`;
  wrapper.appendChild(name);

  if (peer?.peer_id) {
    const id = document.createElement("div");
    id.className = "trace-node-id";
    id.textContent = peer.peer_id;
    wrapper.appendChild(id);
  }

  return wrapper;
}

function makeScoreBreakdown(breakdown) {
  if (!breakdown || !Object.keys(breakdown).length) {
    return null;
  }

  const labels = {
    capability_match: "Capability match",
    fresh_profile: "Fresh profile",
    recent_failures: "Recent failures",
    latency: "Latency",
  };

  const box = document.createElement("div");
  box.className = "trace-score-breakdown";

  const title = document.createElement("div");
  title.className = "trace-score-title";
  title.textContent = "Routing score components";
  box.appendChild(title);

  for (const key of ["capability_match", "fresh_profile", "recent_failures", "latency"]) {
    const item = breakdown[key];
    if (!item) {
      continue;
    }
    const row = document.createElement("div");
    row.className = "trace-score-row";
    row.textContent = `${labels[key]}: value ${formatScore(item.value)} x weight ${formatScore(item.weight)} = ${contributionLabel(item.contribution)}`;
    box.appendChild(row);
  }

  return box;
}

function makeTraceCandidate(candidate) {
  const row = document.createElement("div");
  row.className = `trace-candidate${candidate.selected ? " selected" : ""}${
    candidate.kind === "local" ? " local" : ""
  }`;

  const peer = candidate.peer || {};
  const title = document.createElement("div");
  title.className = "trace-candidate-title";
  if (candidate.selected && candidate.kind === "local") {
    title.appendChild(makeNodeTitle("Selected local node: ", peer));
  } else if (candidate.selected) {
    title.appendChild(makeNodeTitle("Selected node: ", peer));
  } else if (candidate.kind === "local") {
    title.appendChild(makeNodeTitle("Local node: ", peer));
  } else {
    title.appendChild(makeNodeTitle("", peer));
  }

  const fields = document.createElement("div");
  fields.className = "trace-fields";
  addField(fields, "Peer ID", peer.peer_id || "unknown");
  addField(fields, "Candidate type", candidate.kind === "local" ? "local node" : "remote peer");
  addField(fields, "Source", candidate.source || "unknown");
  addField(fields, "Routing score", formatScore(candidate.routing_score));
  addField(fields, "Weighted quality", formatScore(candidate.weighted_quality));
  addField(fields, "Scores", formatCapabilities(candidate.node_scores || {}));
  addField(fields, "Capabilities", (peer.capabilities || []).join(", ") || "none");
  if ((candidate.recommended_by || []).length) {
    addField(
      fields,
      "Recommended by",
      candidate.recommended_by
        .map((item) => `${nodeIdentityText(item.source_peer)} for ${item.capability}`)
        .join(", ")
    );
  }

  row.append(title, fields);
  const breakdown = makeScoreBreakdown(candidate.score_breakdown);
  if (breakdown) {
    row.appendChild(breakdown);
  }
  return row;
}

function makeSkippedCandidate(candidate) {
  const row = document.createElement("div");
  row.className = `trace-candidate skipped${
    candidate.kind === "local" ? " local" : ""
  }`;

  const peer = candidate.peer || {};
  const title = document.createElement("div");
  title.className = "trace-candidate-title";
  title.appendChild(
    makeNodeTitle(
      candidate.kind === "local" ? "Local node not evaluated: " : "Candidate not evaluated: ",
      peer
    )
  );

  const fields = document.createElement("div");
  fields.className = "trace-fields";
  addField(fields, "Peer ID", peer.peer_id || "unknown");
  addField(fields, "Candidate type", candidate.kind === "local" ? "local node" : "remote peer");
  addField(fields, "Reason", candidate.reason || "Not eligible for this stage.");

  row.append(title, fields);
  return row;
}

function makeDecisionReason(hop) {
  const reason = document.createElement("div");
  reason.className = "trace-reason";

  const title = document.createElement("div");
  title.className = "trace-reason-title";
  title.textContent = "Decision";
  reason.appendChild(title);

  const fields = document.createElement("div");
  fields.className = "trace-fields";

  const selected = hop.selected || {};
  const peer = selected.peer || hop.node || {};
  if (hop.action === "forward") {
    addField(fields, "Outcome", "Forwarded to selected peer");
    addField(fields, "Selected peer", nodeIdentityText(peer));
  } else if (hop.action === "execute_local") {
    addField(fields, "Outcome", "Selected local node");
    addField(fields, "Selected node", nodeDisplayName(peer));
  } else if (hop.action === "execute_forwarded_request") {
    addField(fields, "Outcome", "Executed after being selected by a previous hop");
    addField(fields, "Selected node", nodeDisplayName(peer));
  } else {
    addField(fields, "Outcome", describeAction(hop.action));
  }

  addField(fields, "Required scores", formatCapabilities(hop.required_capabilities || {}));
  addField(fields, "Why", selectionReasonText(hop));
  addField(fields, "Minimum routing score", formatScore(hop.routing_score_threshold));
  if (selected.node_scores) {
    addField(fields, "Node scores", formatCapabilities(selected.node_scores));
  }
  if (typeof selected.weighted_quality === "number") {
    addField(fields, "Weighted quality", formatScore(selected.weighted_quality));
  }
  if (typeof selected.routing_score === "number") {
    addField(fields, "Routing score", formatScore(selected.routing_score));
  }
  if (!selected.peer && hop.decision_reason) {
    addField(fields, "Raw reason", hop.decision_reason);
  }

  reason.appendChild(fields);
  return reason;
}

function appendHopFields(parent, hop, options = {}) {
  const fields = document.createElement("div");
  fields.className = "trace-fields";
  addField(fields, "Node", nodeDisplayName(hop.node));
  addField(fields, "Peer ID", hop.node?.peer_id || "unknown");
  if (hop.routed_by_peer_id) {
    addField(fields, "Selected by", hop.routed_by_peer_id);
  }
  if (options.showNeeds && hop.required_capabilities) {
    addField(fields, "Required capabilities", formatCapabilities(hop.required_capabilities || {}));
  }
  if (options.showDht && hop.discovery_capabilities) {
    addField(fields, "DHT lookups", (hop.discovery_capabilities || []).join(", ") || "none");
  }
  if (options.showThreshold && typeof hop.routing_score_threshold === "number") {
    addField(fields, "Minimum routing score", formatScore(hop.routing_score_threshold));
  }
  parent.appendChild(fields);
}

function appendStageDetails(parent, stage, hop) {
  if (stage.name === "recommended" && (hop.recommendation_requests || []).length) {
    const requestNote = document.createElement("div");
    requestNote.className = "trace-note trace-request-note";
    requestNote.textContent =
      "Recommendation requests are discovery only: the asked node returns candidate peer IDs, but does not answer the user query.";
    parent.appendChild(requestNote);

    const requestList = document.createElement("div");
    requestList.className = "trace-request-list";

    for (const request of hop.recommendation_requests) {
      const requestRow = document.createElement("div");
      requestRow.className = "trace-request";
      addField(requestRow, "Capability", request.capability);
      addField(
        requestRow,
        "Asked node",
        request.source_peer ? nodeIdentityText(request.source_peer) : request.source_peer_id
      );
      addField(
        requestRow,
        "Returned peers",
        (request.returned_peer_ids || []).join(", ") || "none"
      );
      requestList.appendChild(requestRow);
    }

    parent.appendChild(requestList);
  }

  const candidates = stage.candidates || [];
  if (!candidates.length && !(stage.skipped_candidates || []).length) {
    const empty = document.createElement("div");
    empty.className = "trace-note";
    empty.textContent = "No reachable candidates in this step.";
    parent.appendChild(empty);
  }

  for (const candidate of candidates) {
    parent.appendChild(makeTraceCandidate(candidate));
  }

  for (const candidate of stage.skipped_candidates || []) {
    parent.appendChild(makeSkippedCandidate(candidate));
  }
}

function stageStepText(stage) {
  if (stage.name === "direct") {
    return {
      title: "Check direct candidates",
      description: "The entry node scores itself and peers it already knows from the DHT/registry.",
    };
  }
  if (stage.name === "recommended") {
    return {
      title: "Ask for recommendations",
      description: "Because direct candidates were not good enough, peers are asked to suggest additional candidates.",
    };
  }
  return {
    title: stageLabel(stage.name),
    description: stageDescription(stage.name),
  };
}

function makeAnswerStep(stepNumber, hop) {
  const section = makeTimelineStep(
    stepNumber,
    "Generate answer",
    hop.routed_by_peer_id
      ? "The selected node receives the forwarded query and produces the final answer."
      : "The selected local node produces the final answer."
  );
  appendHopFields(section, hop);
  return section;
}

function makeForwardFailureStep(stepNumber, hop) {
  const section = makeTimelineStep(
    stepNumber,
    "Forward failed",
    "The selected node did not answer, so it is marked unreachable and excluded before routing is retried."
  );
  const fields = document.createElement("div");
  fields.className = "trace-fields";
  addField(fields, "Attempt", hop.attempt || "unknown");
  addField(fields, "Failed peer", hop.failed_peer_id || hop.selected?.peer?.peer_id || "unknown");
  addField(fields, "Error", hop.forward_error || "unknown error");
  section.appendChild(fields);
  return section;
}

function makeRoutingTrace(trace) {
  const details = document.createElement("div");
  details.className = "routing-trace";

  let stepNumber = 1;
  for (const [index, hop] of (trace?.hops || []).entries()) {
    if (hop.action === "execute_forwarded_request") {
      details.appendChild(makeAnswerStep(stepNumber, hop));
      stepNumber += 1;
      continue;
    }

    if (hop.action === "forwarding_exhausted") {
      const exhaustedStep = makeTimelineStep(
        stepNumber,
        "Stop routing",
        "All forward attempts failed. The node returns no suitable answer instead of waiting longer."
      );
      appendHopFields(exhaustedStep, hop);
      details.appendChild(exhaustedStep);
      stepNumber += 1;
      continue;
    }

    const receiveStep = makeTimelineStep(
      stepNumber,
      index === 0 ? "Send request to entry node" : "Continue routing",
      index === 0
        ? "The query is sent to the node selected in the UI."
        : "Routing continues after a previous forwarding attempt failed."
    );
    appendHopFields(receiveStep, hop, { showNeeds: true });
    appendPreviousAttemptNote(receiveStep, hop);
    details.appendChild(receiveStep);
    stepNumber += 1;

    for (const stage of hop.stages || []) {
      const text = stageStepText(stage);
      const stageStep = makeTimelineStep(
        stepNumber,
        `${text.title} (${stage.candidate_count || 0})`,
        text.description
      );
      if (stage.name === "direct") {
        appendHopFields(stageStep, hop, { showDht: true, showThreshold: true });
      }
      appendStageDetails(stageStep, stage, hop);
      details.appendChild(stageStep);
      stepNumber += 1;
    }

    if (hop.recommendation_reason) {
      const recommendationStep = makeTimelineStep(
        stepNumber,
        "Recommendation result",
        hop.recommendation_reason
      );
      details.appendChild(recommendationStep);
      stepNumber += 1;
    }

    if (hop.selected) {
      const decisionStep = makeTimelineStep(
        stepNumber,
        "Choose answering node",
        selectionReasonText(hop)
      );
      decisionStep.appendChild(makeDecisionReason(hop));
      details.appendChild(decisionStep);
      stepNumber += 1;
    }

    if (hop.action === "forward" && hop.selected?.peer) {
      const forwardStep = makeTimelineStep(
        stepNumber,
        "Forward query",
        "The query is sent to the selected node so it can generate the answer."
      );
      const fields = document.createElement("div");
      fields.className = "trace-fields";
      addField(fields, "From", nodeDisplayName(hop.node));
      addField(fields, "To", nodeIdentityText(hop.selected.peer));
      forwardStep.appendChild(fields);
      details.appendChild(forwardStep);
      stepNumber += 1;
    }

    if (hop.action === "forward_failed") {
      details.appendChild(makeForwardFailureStep(stepNumber, hop));
      stepNumber += 1;
    }

    if (isLocalAnswerAction(hop.action)) {
      details.appendChild(makeAnswerStep(stepNumber, hop));
      stepNumber += 1;
    }
  }

  return details;
}

function makeTraceButton(traceId, trace, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "routing-chip";
  button.dataset.traceId = traceId;
  if (state.inspectorOpen && state.selectedTraceId === traceId) {
    button.classList.add("active");
  }
  button.title = "Show routing details in the side panel";

  const answeredBy = trace?.answered_by;
  const name = answeredBy ? nodeDisplayName(answeredBy) : "unknown node";
  button.textContent = `Routing details - answered by ${name}`;
  button.addEventListener("click", () => selectRoutingTrace(traceId, trace, label));
  return button;
}

function selectRoutingTrace(traceId, trace, label) {
  state.inspectorOpen = true;
  state.selectedTraceId = traceId;
  state.selectedTrace = trace;
  state.selectedTraceLabel = label;
  renderTraceInspector();
  syncTraceButtons();
}

function closeRoutingInspector() {
  state.inspectorOpen = false;
  renderTraceInspector();
  syncTraceButtons();
}

function resetTraceSelection() {
  state.inspectorOpen = false;
  state.selectedTraceId = null;
  state.selectedTrace = null;
  state.selectedTraceLabel = "";
}

function setInspectorWidth(width) {
  const maxWidth = Math.max(320, window.innerWidth - 620);
  state.inspectorWidth = Math.max(300, Math.min(width, Math.min(720, maxWidth)));
  els.appShell.style.setProperty("--inspector-width", `${state.inspectorWidth}px`);
}

function resizeInspector(event) {
  setInspectorWidth(window.innerWidth - event.clientX);
}

function startInspectorResize(event) {
  event.preventDefault();
  els.appShell.classList.add("resizing-inspector");
  els.inspectorResize.setPointerCapture(event.pointerId);
  resizeInspector(event);
}

function stopInspectorResize(event) {
  els.appShell.classList.remove("resizing-inspector");
  if (els.inspectorResize.hasPointerCapture(event.pointerId)) {
    els.inspectorResize.releasePointerCapture(event.pointerId);
  }
}

function syncTraceButtons() {
  for (const button of document.querySelectorAll(".routing-chip")) {
    button.classList.toggle(
      "active",
      state.inspectorOpen && button.dataset.traceId === state.selectedTraceId
    );
  }
}

function renderTraceInspector() {
  els.appShell.classList.toggle("inspector-open", state.inspectorOpen);
  setInspectorWidth(state.inspectorWidth);
  els.routingInspector.innerHTML = "";

  if (!state.inspectorOpen || !state.selectedTrace) {
    const empty = document.createElement("div");
    empty.className = "inspector-empty";
    empty.textContent = "Select a response's routing details to inspect the network decision.";
    els.routingInspector.appendChild(empty);
    return;
  }

  const header = document.createElement("header");
  header.className = "inspector-header";

  const heading = document.createElement("div");
  heading.className = "inspector-heading";

  const title = document.createElement("h2");
  title.textContent = "Network details";

  const close = document.createElement("button");
  close.type = "button";
  close.className = "secondary inspector-close";
  close.textContent = "Close";
  close.addEventListener("click", closeRoutingInspector);

  heading.append(title, close);

  const context = document.createElement("div");
  context.className = "inspector-context";
  const answeredBy = state.selectedTrace?.answered_by;
  context.textContent = answeredBy
    ? `${state.selectedTraceLabel} - answered by ${nodeDisplayName(answeredBy)}`
    : state.selectedTraceLabel;

  header.append(heading, context);
  els.routingInspector.appendChild(header);
  els.routingInspector.appendChild(makeRoutingTrace(state.selectedTrace));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function renderNodes() {
  els.nodeSelect.innerHTML = "";
  for (const node of state.nodes) {
    const option = document.createElement("option");
    option.value = node.api_url || "";
    option.textContent = node.label;
    option.disabled = !node.api_url;
    els.nodeSelect.appendChild(option);
  }
}

function renderConversations() {
  els.conversationList.innerHTML = "";
  for (const conversation of state.conversations) {
    const button = document.createElement("button");
    button.className = "conversation-item";
    if (conversation.id === state.currentConversationId) {
      button.classList.add("active");
    }
    button.textContent = conversation.title;
    button.addEventListener("click", () => selectConversation(conversation.id));
    els.conversationList.appendChild(button);
  }
}

function renderMessages() {
  els.messages.innerHTML = "";
  const messages = state.currentConversation?.messages || [];
  if (!messages.length) {
    resetTraceSelection();
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Start a new chat or select a saved conversation.";
    els.messages.appendChild(empty);
    renderTraceInspector();
    return;
  }

  const traces = [];
  let assistantIndex = 0;

  for (const [index, message] of messages.entries()) {
    const wrapper = document.createElement("article");
    const role = message.role === "User" ? "user" : "assistant";
    wrapper.className = `message ${role}`;
    if (role === "assistant") {
      assistantIndex += 1;
    }
    if (message.pending) {
      wrapper.classList.add("pending");
      wrapper.innerHTML = `
        <div class="message-role">TSADAI</div>
        <div class="message-content pending-content" aria-label="Waiting for network response">
          <span></span><span></span><span></span>
        </div>
      `;
    } else {
      wrapper.innerHTML = `
        <div class="message-role">${role === "user" ? "You" : "TSADAI"}</div>
        <div class="message-content">${markdownToHtml(message.content)}</div>
      `;
      if (role === "assistant" && message.routing_trace) {
        const traceId = `${state.currentConversationId || "conversation"}-${message.created_at || index}`;
        const label = `Assistant message ${assistantIndex}`;
        traces.push({ id: traceId, trace: message.routing_trace, label });
        wrapper.appendChild(makeTraceButton(traceId, message.routing_trace, label));
      }
    }
    els.messages.appendChild(wrapper);
  }

  const selected = traces.find((item) => item.id === state.selectedTraceId);
  if (selected) {
    state.selectedTrace = selected.trace;
    state.selectedTraceLabel = selected.label;
  } else {
    resetTraceSelection();
  }

  els.messages.scrollTop = els.messages.scrollHeight;
  renderTraceInspector();
  syncTraceButtons();
  typesetMath();
}

function typesetMath() {
  if (window.MathJax?.typesetPromise) {
    window.MathJax.typesetPromise([els.messages]).catch(() => {});
  }
}

function makeTitle(text) {
  const compact = text.trim().replace(/\s+/g, " ");
  if (!compact) {
    return "Untitled chat";
  }
  return compact.length <= 34 ? compact : `${compact.slice(0, 31).trim()}...`;
}

function ensureOptimisticConversation(prompt) {
  if (state.currentConversation) {
    return {
      conversation: state.currentConversation,
      serverConversationId: state.currentConversationId?.startsWith("local-")
        ? null
        : state.currentConversationId,
    };
  }

  const conversation = {
    id: `local-${Date.now()}`,
    title: makeTitle(prompt),
    messages: [],
    updated_at: Date.now() / 1000,
  };
  state.currentConversation = conversation;
  state.currentConversationId = conversation.id;
  state.conversations = [conversation, ...state.conversations];
  return { conversation, serverConversationId: null };
}

async function refreshState() {
  const data = await api("/api/state");
  state.nodes = data.nodes;
  state.conversations = data.conversations;
  renderNodes();
  renderConversations();
}

async function selectConversation(conversationId) {
  const conversation = await api(`/api/conversations/${encodeURIComponent(conversationId)}`);
  state.currentConversationId = conversation.id;
  state.currentConversation = conversation;
  resetTraceSelection();
  renderConversations();
  renderMessages();
}

function newChat() {
  state.currentConversationId = null;
  state.currentConversation = null;
  resetTraceSelection();
  renderConversations();
  renderMessages();
  els.prompt.focus();
}

async function deleteChat() {
  if (!state.currentConversationId) {
    setStatus("Select a conversation to delete.");
    return;
  }
  const data = await api(`/api/conversations/${encodeURIComponent(state.currentConversationId)}`, {
    method: "DELETE",
  });
  state.conversations = data.conversations;
  state.currentConversationId = null;
  state.currentConversation = null;
  resetTraceSelection();
  renderConversations();
  renderMessages();
  setStatus("Conversation deleted.");
}

function setStatus(value) {
  els.status.textContent = value;
}

async function sendMessage(event) {
  event.preventDefault();
  const prompt = els.prompt.value.trim();
  const entryNode = els.nodeSelect.value;
  if (!prompt || !entryNode) {
    return;
  }

  const { conversation, serverConversationId } = ensureOptimisticConversation(prompt);
  conversation.messages.push({
    role: "User",
    content: prompt,
    created_at: Date.now() / 1000,
  });
  conversation.messages.push({
    role: "Assistant",
    content: "",
    pending: true,
    created_at: Date.now() / 1000,
  });
  conversation.updated_at = Date.now() / 1000;
  renderConversations();
  renderMessages();

  els.prompt.value = "";
  els.send.disabled = true;
  setStatus("Preparing request...");

  try {
    const start = await api("/api/chat/start", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: serverConversationId,
        entry_node: entryNode,
        prompt,
      }),
    });

    state.currentConversationId = start.conversation.id;
    conversation.id = start.conversation.id;
    state.currentConversation = conversation;
    state.conversations = start.conversations;
    renderConversations();

    while (true) {
      await sleep(1000);
      const status = await api(`/api/chat/status/${encodeURIComponent(start.query_id)}`);
      setStatus(status.message || "Waiting for network response...");

      if (!status.done) {
        continue;
      }

      const data = status.response;
      if (!data?.ok) {
        throw new Error(data?.error || data?.answer || "The query failed.");
      }

      state.currentConversation = data.conversation;
      state.currentConversationId = data.conversation.id;
      state.conversations = data.conversations;
      renderConversations();
      renderMessages();
      setStatus("Ready");
      break;
    }
  } catch (error) {
    const pending = conversation.messages.find((message) => message.pending);
    if (pending) {
      pending.pending = false;
      pending.content = error.message;
    }
    renderMessages();
    setStatus(error.message);
  } finally {
    els.send.disabled = false;
    els.prompt.focus();
  }
}

els.form.addEventListener("submit", sendMessage);
els.refresh.addEventListener("click", refreshState);
els.newChat.addEventListener("click", newChat);
els.deleteChat.addEventListener("click", deleteChat);
els.inspectorResize.addEventListener("pointerdown", startInspectorResize);
els.inspectorResize.addEventListener("pointermove", (event) => {
  if (els.inspectorResize.hasPointerCapture(event.pointerId)) {
    resizeInspector(event);
  }
});
els.inspectorResize.addEventListener("pointerup", stopInspectorResize);
els.inspectorResize.addEventListener("pointercancel", stopInspectorResize);
els.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

refreshState().then(renderMessages).catch((error) => setStatus(error.message));
