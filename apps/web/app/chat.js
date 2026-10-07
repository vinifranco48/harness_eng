"use strict";

const $ = (id) => document.getElementById(id);
const providerNames = { claude: "Claude", codex: "Codex" };
const effortNames = { none: "Desligado", minimal: "Mínimo", low: "Baixo", medium: "Médio", high: "Alto", xhigh: "Muito alto", max: "Máximo", ultra: "Ultra" };
let csrf = "";
let catalogs = [];
let currentChat = null;
let currentRun = null;
let streamController = null;
let sending = false;
let loading = true;
let viewVersion = 0;
let toastTimer;
const messageNodes = new Map();

async function api(path, method = "GET", body) {
  const options = { method, headers: { "X-Harness-CSRF": csrf } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { throw new Error("O servidor não respondeu. Atualize a página e tente novamente."); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Confira os dados e tente novamente.");
  return data;
}

function showError(message = "") { $("chat-error").textContent = message; $("chat-error").hidden = !message; }
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3000); }
function remember(key, value) { try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch {} }
function remembered(key) { try { return localStorage.getItem(key); } catch { return null; } }
function selectedCatalog() { return catalogs.find((item) => item.provider === $("provider-select").value); }
function selectedModel() { return selectedCatalog()?.models.find((item) => item.id === $("model-select").value); }

function updateControls() {
  const model = selectedModel();
  const connected = selectedCatalog()?.connected;
  const busy = sending || loading;
  $("provider-select").disabled = busy;
  $("model-select").disabled = busy || !!currentRun || !model;
  $("effort-select").disabled = busy || !!currentRun;
  $("message-input").disabled = busy || !model || !connected;
  $("send-message").disabled = busy || !!currentRun || !model || !connected || !$("message-input").value.trim();
  $("send-message").hidden = !!currentRun;
  $("stop-response").hidden = !currentRun;
  $("new-chat").disabled = sending;
  $("delete-chat").hidden = !currentChat || !!currentRun;
  $("delete-chat").disabled = busy;
  $("refresh-models").disabled = busy || !!currentRun;
  $("composer-provider").textContent = `${providerNames[$("provider-select").value]}${model ? " / " + model.name : ""}`;
  $("conversation-state").textContent = currentRun ? "Recebendo a resposta…" : sending ? "Enviando mensagem…" : loading ? "Carregando…" : currentChat ? currentChat.title : connected && model ? "Pronto para conversar" : "Conecte uma conta em Contas para começar";
  $("conversation-title").textContent = currentChat?.title || "Nova conversa";
}

function populateModels(preferred, effort) {
  const catalog = selectedCatalog();
  const options = catalog?.models || [];
  const select = $("model-select");
  select.replaceChildren();
  for (const model of options) select.add(new Option(model.name, model.id));
  if (!options.length) select.add(new Option(catalog?.connected ? "Modelos indisponíveis" : "Conta desconectada", ""));
  const choice = options.find((model) => model.id === preferred) || options.find((model) => model.default) || options[0];
  select.value = choice?.id || "";
  if (preferred && choice && preferred !== choice.id) toast("O modelo anterior não está na lista atual. Selecione um modelo para continuar.");
  populateEfforts(effort);
}

function populateEfforts(preferred) {
  const model = selectedModel();
  $("effort-select").replaceChildren();
  for (const effort of model?.efforts || []) $("effort-select").add(new Option(effortNames[effort] || effort, effort));
  $("effort-select").value = model?.efforts.includes(preferred) ? preferred : model?.default_effort || model?.efforts[0] || "";
  $("effort-field").hidden = !model?.efforts.length;
  $("model-caption").textContent = model?.description || selectedCatalog()?.error || "Escolha um modelo para começar a conversa.";
  updateControls();
}

async function loadModels(force = false) {
  $("refresh-models").disabled = true;
  const oldModel = $("model-select").value;
  const oldEffort = $("effort-select").value;
  try {
    const data = await api(`/api/models${force ? "?refresh=true" : ""}`);
    catalogs = data.providers;
    if (!currentChat && !selectedCatalog()?.connected) $("provider-select").value = catalogs.find((item) => item.connected && item.models.length)?.provider || "claude";
    for (const option of $("provider-select").options) option.disabled = !catalogs.find((item) => item.provider === option.value)?.connected;
    const count = catalogs.filter((item) => item.connected).length;
  $("account-status").textContent = count === 2 ? "2 contas conectadas" : count === 1 ? "1 conta conectada" : "Conectar contas";
    $("account-dot").classList.toggle("connected", count > 0);
    populateModels(currentChat?.model || oldModel, currentChat?.effort || oldEffort);
    if (catalogs.every((item) => !item.models.length)) showError(selectedCatalog()?.error || "Abra Contas para conectar Claude ou Codex.");
  } catch (error) { showError(error.message); }
  finally { updateControls(); }
}

async function loadHistory() {
  const data = await api("/api/chats");
  $("chat-history").replaceChildren();
  $("history-count").textContent = data.chats.length;
  if (!data.chats.length) {
    const empty = document.createElement("p"); empty.className = "history-empty"; empty.textContent = "Suas conversas aparecerão aqui."; $("chat-history").append(empty);
  }
  for (const chat of data.chats) {
    const button = document.createElement("button"); button.type = "button"; button.className = "history-item";
    button.classList.toggle("active", chat.id === currentChat?.id);
    if (chat.id === currentChat?.id) button.setAttribute("aria-current", "page");
    const title = document.createElement("strong"); title.textContent = chat.title;
    const caption = document.createElement("span"); caption.textContent = `${providerNames[chat.provider]} · ${new Date(chat.updated_at * 1000).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}`;
    button.title = `${chat.title} · ${caption.textContent}`;
    button.setAttribute("aria-label", `${chat.title}, ${providerNames[chat.provider]}`);
    button.append(title, caption); button.addEventListener("click", () => { if (!sending) openChat(chat.id); });
    $("chat-history").append(button);
  }
}

function closeSidebar() { document.dispatchEvent(new Event("harness:close-menu")); }
function detachStream() { streamController?.abort(); streamController = null; currentRun = null; }

async function openChat(chatId) {
  const version = ++viewVersion;
  detachStream(); loading = true; showError(); updateControls(); closeSidebar();
  try {
    const data = await api(`/api/chats/${chatId}`);
    if (version !== viewVersion) return;
    currentChat = data.chat;
    $("provider-select").value = currentChat.provider;
    remember("harness.chat", chatId);
    populateModels(currentChat.model, currentChat.effort);
    renderChat();
    if (data.chat.active_run) watchRun(data.chat.active_run, version);
    await loadHistory();
  } catch (error) { if (version === viewVersion) showError(error.message); }
  finally { if (version === viewVersion) { loading = false; updateControls(); } }
}

function newChat() {
  if (sending) return;
  viewVersion += 1; detachStream(); currentChat = null; loading = false;
  remember("harness.chat", null); showError(); $("usage-info").textContent = "";
  $("messages").replaceChildren(); messageNodes.clear(); $("messages").hidden = true; $("welcome").hidden = false;
  closeSidebar(); updateControls(); loadHistory().catch((error) => showError(error.message)); $("message-input").focus();
}

async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copiado."); } catch { toast("Selecione o texto e copie manualmente."); } }

function inline(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
  let previous = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(previous, match.index)));
    const token = match[0];
    if (token.startsWith("`")) { const code = document.createElement("code"); code.textContent = token.slice(1, -1); parent.append(code); }
    else if (token.startsWith("**")) { const bold = document.createElement("strong"); bold.textContent = token.slice(2, -2); parent.append(bold); }
    else {
      const parts = token.match(/^\[([^\]]+)\]\((.+)\)$/);
      try {
        const url = new URL(parts[2]);
        if (!["http:", "https:"].includes(url.protocol)) throw new Error();
        const link = document.createElement("a"); link.textContent = parts[1]; link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; parent.append(link);
      } catch { parent.append(document.createTextNode(token)); }
    }
    previous = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(previous)));
}

function markdown(target, text) {
  const fragment = document.createDocumentFragment();
  const lines = text.split("\n");
  let paragraph = []; let list = null;
  function flush() { if (paragraph.length) { const p = document.createElement("p"); inline(p, paragraph.join("\n")); fragment.append(p); paragraph = []; } list = null; }
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith("```")) {
      flush(); const language = line.slice(3).trim(); const codeLines = [];
      while (++index < lines.length && !lines[index].startsWith("```")) codeLines.push(lines[index]);
      const block = document.createElement("div"); block.className = "code-block";
      const bar = document.createElement("div"); bar.className = "code-bar"; const label = document.createElement("span"); label.textContent = language || "código";
      const button = document.createElement("button"); button.type = "button"; button.textContent = "Copiar"; button.addEventListener("click", () => copy(codeLines.join("\n")));
      const pre = document.createElement("pre"); const code = document.createElement("code"); code.textContent = codeLines.join("\n"); pre.append(code); bar.append(label, button); block.append(bar, pre); fragment.append(block);
    } else if (/^#{1,6}\s/.test(line)) {
      flush(); const match = line.match(/^(#{1,6})\s+(.*)$/); const heading = document.createElement(`h${Math.min(4, match[1].length + 1)}`); inline(heading, match[2]); fragment.append(heading);
    } else if (/^\s*(?:[-*]|\d+\.)\s/.test(line)) {
      if (paragraph.length) flush(); const ordered = /^\s*\d+\./.test(line);
      if (!list || list.tagName !== (ordered ? "OL" : "UL")) { list = document.createElement(ordered ? "ol" : "ul"); fragment.append(list); }
      const item = document.createElement("li"); inline(item, line.replace(/^\s*(?:[-*]|\d+\.)\s+/, "")); list.append(item);
    } else if (line.startsWith("> ")) {
      flush(); const quote = document.createElement("blockquote"); inline(quote, line.slice(2)); fragment.append(quote);
    } else if (!line.trim()) flush();
    else { list = null; paragraph.push(line); }
  }
  flush(); target.replaceChildren(fragment);
}

function messageNode(message) {
  if (messageNodes.has(message.id)) return messageNodes.get(message.id);
  const article = document.createElement("article"); article.className = `chat-message ${message.role} ${currentChat.provider}`;
  const avatar = document.createElement("div"); avatar.className = "message-avatar"; avatar.setAttribute("aria-hidden", "true"); avatar.textContent = message.role === "user" ? "V" : currentChat.provider === "claude" ? "✳" : ">_";
  const body = document.createElement("div"); const heading = document.createElement("div"); heading.className = "message-heading";
  const name = document.createElement("strong"); name.textContent = message.role === "user" ? "Você" : providerNames[currentChat.provider]; const model = document.createElement("span"); heading.append(name, model);
  const content = document.createElement("div"); content.className = "message-content";
  const thinking = document.createElement("div"); thinking.className = "thinking"; const spinner = document.createElement("span"); spinner.className = "spinner"; const wait = document.createElement("span"); wait.textContent = "Preparando a resposta…"; thinking.append(spinner, wait);
  const error = document.createElement("p"); error.className = "message-error"; error.hidden = true;
  const actions = document.createElement("div"); actions.className = "message-actions";
  const button = document.createElement("button"); button.type = "button"; button.textContent = message.role === "assistant" ? "Copiar resposta" : "Usar mensagem";
  button.addEventListener("click", () => { const latest = currentChat?.messages.find((item) => item.id === message.id); if (!latest) return; if (latest.role === "assistant") copy(latest.content); else { $("message-input").value = latest.content; resizeInput(); updateControls(); $("message-input").focus(); } });
  actions.append(button); body.append(heading, content, thinking, error, actions); article.append(avatar, body); $("messages").append(article);
  const nodes = { article, content, model, thinking, error, actions, lastContent: null }; messageNodes.set(message.id, nodes); return nodes;
}

function updateMessage(message) {
  const node = messageNode(message);
  node.model.textContent = message.role === "assistant" ? message.model : "";
  if (node.lastContent !== message.content) {
    if (message.role === "user") node.content.textContent = message.content;
    else markdown(node.content, message.content);
    node.lastContent = message.content;
  }
  node.thinking.hidden = message.state !== "running" || !!message.content;
  node.error.hidden = !message.error; node.error.textContent = message.error || "";
  node.actions.hidden = message.state === "running" || !message.content;
}

function renderChat() {
  $("messages").replaceChildren(); messageNodes.clear();
  const hasMessages = currentChat.messages.length > 0;
  $("welcome").hidden = hasMessages; $("messages").hidden = !hasMessages;
  currentChat.messages.forEach(updateMessage);
  const last = [...currentChat.messages].reverse().find((message) => message.usage);
  showUsage(last?.usage); $("conversation-area").scrollTop = $("conversation-area").scrollHeight;
}

function showUsage(usage) { $("usage-info").textContent = usage ? `${Number(usage.input_tokens || 0).toLocaleString("pt-BR")} entrada · ${Number(usage.output_tokens || 0).toLocaleString("pt-BR")} saída` : ""; }

function applyRun(run, version) {
  if (version !== viewVersion || currentChat?.id !== run.chat_id) return;
  currentRun = run.state === "running" ? run : null;
  const message = currentChat.messages.find((item) => item.id === run.message_id);
  if (!message) return;
  const area = $("conversation-area"); const nearBottom = area.scrollHeight - area.scrollTop - area.clientHeight < 130;
  Object.assign(message, { content: run.content, state: run.state, error: run.error, model: run.model, usage: run.usage });
  updateMessage(message); showUsage(run.usage); updateControls();
  if (nearBottom) area.scrollTop = area.scrollHeight;
}

async function watchRun(run, version) {
  detachStream(); currentRun = run; updateControls();
  const controller = new AbortController(); streamController = controller;
  let finished = false;
  try {
    const response = await fetch(`/api/runs/${run.id}/events`, { headers: { "X-Harness-CSRF": csrf }, signal: controller.signal });
    if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "Não foi possível acompanhar a resposta."); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const event = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const data = event.split("\n").find((line) => line.startsWith("data: "));
        if (data) {
          const snapshot = JSON.parse(data.slice(6)); applyRun(snapshot, version);
          if (snapshot.state !== "running") finished = true;
        }
      }
    }
    if (!finished && !controller.signal.aborted) throw new Error("A conexão foi interrompida. Reabrindo a conversa para acompanhar a resposta…");
    if (version === viewVersion && finished) { await loadHistory(); $("message-input").focus(); }
  } catch (error) {
    if (error.name !== "AbortError" && version === viewVersion) {
      showError(error.message);
      setTimeout(() => { if (version === viewVersion && currentChat) openChat(currentChat.id); }, 2000);
    }
  } finally { if (streamController === controller) streamController = null; }
}

function resizeInput() {
  const input = $("message-input");
  const styles = getComputedStyle(input);
  const minimum = parseFloat(styles.minHeight) || 56;
  const maximum = parseFloat(styles.maxHeight) || 190;
  input.style.height = "auto";
  input.style.height = `${Math.min(maximum, Math.max(minimum, input.scrollHeight))}px`;
}

$("message-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const text = $("message-input").value.trim(); const model = selectedModel();
  if (!text || !model || sending || currentRun || loading) return;
  const version = viewVersion; const provider = $("provider-select").value; const effort = $("effort-select").value || null;
  sending = true; showError(); updateControls();
  try {
    if (!currentChat) { const data = await api("/api/chats", "POST", { provider, model: model.id, effort }); currentChat = data.chat; remember("harness.chat", currentChat.id); }
    const data = await api(`/api/chats/${currentChat.id}/messages`, "POST", { text, model: model.id, effort });
    currentChat = data.chat; $("message-input").value = ""; resizeInput(); renderChat();
    watchRun(data.run, version); await loadHistory();
  } catch (error) { showError(error.message); }
  finally { sending = false; updateControls(); }
});

$("message-input").addEventListener("input", () => { resizeInput(); updateControls(); });
$("message-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$("send-message").disabled) $("message-form").requestSubmit(); } });
$("new-chat").addEventListener("click", newChat);
$("provider-select").addEventListener("change", () => {
  const provider = $("provider-select").value;
  const previousProvider = currentChat?.provider;
  if (previousProvider && previousProvider !== provider) newChat();
  showError();
  populateModels();
  if (previousProvider && previousProvider !== provider) toast(`Nova conversa com ${providerNames[provider]}. A conversa anterior continua no histórico.`);
  $("message-input").focus();
});
$("model-select").addEventListener("change", () => { showError(); populateEfforts(); });
$("refresh-models").addEventListener("click", async () => { loading = true; showError(); updateControls(); await loadModels(true); loading = false; updateControls(); });
$("stop-response").addEventListener("click", async () => {
  if (!currentRun) return; const run = currentRun; $("stop-response").disabled = true;
  try { await api(`/api/runs/${run.id}/cancel`, "POST"); } catch (error) { showError(error.message); }
  finally { $("stop-response").disabled = false; }
});
$("conversation-area").addEventListener("click", closeSidebar);
document.querySelectorAll("[data-prompt]").forEach((button) => { button.addEventListener("click", () => { $("message-input").value = button.dataset.prompt; resizeInput(); updateControls(); $("message-input").focus(); }); });
$("delete-chat").addEventListener("click", () => $("delete-dialog").showModal());
$("delete-cancel").addEventListener("click", () => $("delete-dialog").close());
$("delete-confirm").addEventListener("click", async () => {
  if (!currentChat || currentRun) return; $("delete-confirm").disabled = true;
  try { await api(`/api/chats/${currentChat.id}`, "DELETE"); $("delete-dialog").close(); newChat(); toast("Conversa excluída."); }
  catch (error) { $("delete-dialog").close(); showError(error.message); }
  finally { $("delete-confirm").disabled = false; }
});

(async () => {
  try {
    const data = await api("/api/bootstrap"); csrf = data.csrf_token;
    await Promise.all([loadModels(), loadHistory()]);
    const url = new URL(window.location.href);
    const startNew = url.searchParams.get("new") === "1";
    if (startNew) {
      remember("harness.chat", null);
      url.searchParams.delete("new");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
    const saved = startNew ? null : remembered("harness.chat");
    if (saved) await openChat(saved);
  } catch (error) { showError(error.message); }
  finally { loading = false; updateControls(); }
})();
