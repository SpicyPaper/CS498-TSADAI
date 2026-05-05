let state = {
  nodes: [],
  conversations: [],
  currentConversationId: null,
  currentConversation: null,
};

const els = {
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

function describeAction(action) {
  const descriptions = {
    forward: "Routed this query to the selected peer.",
    execute_local: "Answered locally on this node.",
    execute_forwarded_request: "Executed the query because a previous hop selected this node.",
    no_suitable_node: "Could not find a suitable node.",
    forward_error: "The selected peer returned an error.",
    forwarded_generation_error: "The selected peer failed while generating an answer.",
    generation_error: "This node failed while generating an answer.",
  };
  return descriptions[action] || "Processed this query step.";
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

function makeTraceCandidate(candidate) {
  const row = document.createElement("div");
  row.className = `trace-candidate${candidate.selected ? " selected" : ""}`;

  const peer = candidate.peer || {};
  const title = document.createElement("div");
  title.className = "trace-candidate-title";
  title.textContent = candidate.selected
    ? `Selected node: ${nodeDisplayName(peer)}`
    : nodeDisplayName(peer);

  const fields = document.createElement("div");
  fields.className = "trace-fields";
  addField(fields, "Peer ID", peer.peer_id || "unknown");
  addField(fields, "Source", candidate.source || "unknown");
  addField(fields, "Utility", formatScore(candidate.utility));
  addField(fields, "Weighted quality", formatScore(candidate.weighted_quality));
  addField(fields, "Scores", formatCapabilities(candidate.node_scores || {}));
  addField(fields, "Capabilities", (peer.capabilities || []).join(", ") || "none");

  row.append(title, fields);
  return row;
}

function makeTraceSummary(answeredBy, hopCount) {
  const summary = document.createElement("summary");
  summary.className = "routing-summary";

  const label = document.createElement("span");
  label.className = "routing-summary-label";
  label.textContent = "Show routing details";

  const meta = document.createElement("span");
  meta.className = "routing-summary-meta";
  meta.textContent = answeredBy
    ? `Answered by ${nodeDisplayName(answeredBy)} - ${hopCount} hop${hopCount === 1 ? "" : "s"}`
    : `${hopCount} hop${hopCount === 1 ? "" : "s"}`;

  summary.append(label, meta);
  return summary;
}

function makeRoutingTrace(trace) {
  const details = document.createElement("details");
  details.className = "routing-trace";

  const answeredBy = trace?.answered_by;
  const hopCount = Array.isArray(trace?.hops) ? trace.hops.length : 0;
  details.appendChild(makeTraceSummary(answeredBy, hopCount));

  for (const [index, hop] of (trace?.hops || []).entries()) {
    const section = document.createElement("section");
    section.className = "trace-hop";

    const title = document.createElement("h3");
    title.textContent = `Hop ${index + 1}: ${nodeDisplayName(hop.node)}`;
    section.appendChild(title);

    const intro = document.createElement("p");
    intro.className = "trace-hop-intro";
    intro.textContent = describeAction(hop.action);
    section.appendChild(intro);

    const overview = document.createElement("div");
    overview.className = "trace-fields";
    addField(overview, "Peer ID", hop.node?.peer_id || "unknown");
    addField(overview, "Action", hop.action || "route");
    if (hop.routed_by_peer_id) {
      addField(overview, "Routed by", hop.routed_by_peer_id);
    }
    addField(overview, "Required capabilities", formatCapabilities(hop.required_capabilities || {}));
    addField(overview, "DHT lookups", (hop.discovery_capabilities || []).join(", ") || "none");
    addField(overview, "Utility threshold", formatScore(hop.utility_threshold));
    section.appendChild(overview);

    if (hop.previous_attempt) {
      const previous = document.createElement("div");
      previous.className = "trace-note";
      previous.textContent = `This hop is a fallback. The previous attempt found no candidate for ${formatCapabilities(hop.previous_attempt.required_capabilities || {})}.`;
      section.appendChild(previous);
    }

    let stepNumber = 1;

    for (const stage of hop.stages || []) {
      section.appendChild(
        makeStepHeader(
          stepNumber,
          `${stageLabel(stage.name)} (${stage.candidate_count || 0})`,
          stageDescription(stage.name)
        )
      );
      stepNumber += 1;

      if (stage.name === "recommended" && (hop.recommendation_requests || []).length) {
        const requestNote = document.createElement("div");
        requestNote.className = "trace-note trace-request-note";
        requestNote.textContent =
          "Recommendation requests are discovery only: the asked peer returns candidate peer IDs, but does not answer the user query.";
        section.appendChild(requestNote);

        const requestList = document.createElement("div");
        requestList.className = "trace-request-list";

        for (const request of hop.recommendation_requests) {
          const requestRow = document.createElement("div");
          requestRow.className = "trace-request";
          addField(requestRow, "Capability", request.capability);
          addField(requestRow, "Asked peer", request.source_peer_id);
          addField(
            requestRow,
            "Returned peers",
            (request.returned_peer_ids || []).join(", ") || "none"
          );
          requestList.appendChild(requestRow);
        }

        section.appendChild(requestList);
      }

      const candidates = stage.candidates || [];
      if (!candidates.length) {
        const empty = document.createElement("div");
        empty.className = "trace-note";
        empty.textContent = "No reachable candidates in this stage.";
        section.appendChild(empty);
      }

      for (const candidate of candidates) {
        section.appendChild(makeTraceCandidate(candidate));
      }
    }

    if (hop.selected) {
      section.appendChild(
        makeStepHeader(
          stepNumber,
          "Final decision",
          "The node with the best acceptable utility is selected for this hop."
        )
      );
      section.appendChild(makeTraceCandidate(hop.selected));
    }

    if (hop.decision_reason) {
      const reason = document.createElement("div");
      reason.className = "trace-reason";
      reason.textContent = `Reason: ${hop.decision_reason}`;
      section.appendChild(reason);
    }

    details.appendChild(section);
  }

  return details;
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
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Start a new chat or select a saved conversation.";
    els.messages.appendChild(empty);
    return;
  }

  for (const message of messages) {
    const wrapper = document.createElement("article");
    const role = message.role === "User" ? "user" : "assistant";
    wrapper.className = `message ${role}`;
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
        wrapper.appendChild(makeRoutingTrace(message.routing_trace));
      }
    }
    els.messages.appendChild(wrapper);
  }
  els.messages.scrollTop = els.messages.scrollHeight;
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
  renderConversations();
  renderMessages();
}

function newChat() {
  state.currentConversationId = null;
  state.currentConversation = null;
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
  setStatus("Waiting for network response...");

  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: serverConversationId,
        entry_node: entryNode,
        prompt,
      }),
    });
    state.currentConversation = data.conversation;
    state.currentConversationId = data.conversation.id;
    state.conversations = data.conversations;
    renderConversations();
    renderMessages();
    setStatus("Ready");
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
els.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

refreshState().then(renderMessages).catch((error) => setStatus(error.message));
