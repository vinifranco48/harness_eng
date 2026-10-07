"use strict";

const $ = (id) => document.getElementById(id);
const labels = { claude: "Claude", codex: "Codex" };
let csrf = "";
let currentJob = null;
let requestOptions = null;
let pollTimer = null;
let toastTimer = null;
let refreshing = false;
let closing = false;
let dialogTrigger = null;
let flowVersion = 0;
let pendingStarts = 0;

async function api(path, method = "GET", body) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-Harness-CSRF"] = csrf;
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { throw new Error("O servidor não respondeu. Atualize a página e tente novamente."); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Verifique os dados e tente novamente.");
  return data;
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
}

function updateProvider(status) {
  const row = $(`${status.provider}-row`);
  const statusElement = $(`${status.provider}-status`);
  const statusNames = { connected: "Conectado", disconnected: "Não conectado", missing_cli: "Aplicativo não encontrado", error: "Não foi possível consultar", timeout: "A consulta demorou demais" };
  statusElement.className = `provider-state ${status.state}`;
  statusElement.querySelector("span:last-child").textContent = statusNames[status.state] || "Não foi possível consultar";
  const busy = currentJob?.provider === status.provider && currentJob.state === "running";
  row.querySelector('[data-action="login"]').hidden = status.connected;
  row.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  row.querySelector('[data-action="switch"]').hidden = !status.connected;
  row.querySelector('[data-action="logout"]').hidden = !status.connected;
  const device = row.querySelector('[data-action="device"]');
  if (device) device.hidden = status.connected;
  $(`${status.provider}-wire`).classList.toggle("connected", status.connected);
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  $("refresh").disabled = true;
  try {
    const data = await api("/api/status");
    data.providers.forEach(updateProvider);
    const count = data.providers.filter((status) => status.connected).length;
    $("summary").textContent = `${count} de 2 contas conectadas`;
    $("summary-dot").classList.toggle("connected", count > 0);
    $("map-indicator").classList.toggle("connected", count > 0);
    $("last-check").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
    $("page-error").hidden = true;
    if (!currentJob && !closing && pendingStarts === 0 && !$("auth-dialog").open && data.active_jobs.length) {
      const active = data.active_jobs[0];
      requestOptions = { provider: active.provider, action: active.action, mode: active.mode, force: false };
      prepareDialog(active.provider, active.action);
      showJob(active);
      schedulePoll();
    }
  } catch (error) {
    $("page-error").textContent = error.message;
    $("page-error").hidden = false;
  } finally {
    refreshing = false;
    $("refresh").disabled = false;
  }
}

function prepareDialog(provider, action) {
  const isLogin = action === "login";
  $("dialog-title").textContent = `${isLogin ? "Conectar" : "Desconectar"} ${labels[provider]}`;
  $("dialog-label").textContent = isLogin ? "AUTORIZAR CONEXÃO" : "ENCERRAR CONEXÃO";
  $("dialog-provider").textContent = provider === "claude" ? "✳" : ">_";
  $("dialog-provider").className = `dialog-provider ${provider}`;
  $("dialog-message").textContent = isLogin ? "Preparando o login…" : "Desconectando sua conta…";
  $("dialog-error").hidden = true;
  $("retry").hidden = true;
  $("authorize").hidden = true;
  $("authorize").removeAttribute("href");
  $("device-code-section").hidden = true;
  $("code-form").hidden = true;
  $("confirmation-code").value = "";
  $("waiting").hidden = false;
  $("waiting-label").textContent = "Preparando a conexão";
  $("cancel").textContent = "Cancelar";
  $("dialog-hint").textContent = "Esta janela acompanha o login automaticamente.";
  if (!$("auth-dialog").open) $("auth-dialog").showModal();
}

function showJob(job) {
  currentJob = job;
  $("dialog-message").textContent = job.message;
  const running = job.state === "running";
  $("waiting").hidden = !running;
  $("waiting-label").textContent = job.auth_url ? "Aguardando sua autorização" : "Preparando a conexão";
  $("authorize").hidden = !running || !job.auth_url;
  if (job.auth_url) {
    $("authorize").href = job.auth_url;
    $("authorize").textContent = `Abrir ${job.provider === "claude" ? "Claude" : "ChatGPT"} ↗`;
  } else $("authorize").removeAttribute("href");
  $("device-code-section").hidden = !running || !job.user_code;
  $("device-code").textContent = job.user_code || "";
  $("code-form").hidden = !running || !job.needs_code;
  $("retry").hidden = running || job.state === "succeeded" || job.state === "cancelled";
  $("cancel").textContent = running ? "Cancelar" : "Fechar";
  if (!running) {
    $("dialog-hint").textContent = job.state === "succeeded" ? "Você pode fechar esta janela." : "Sua conexão foi atualizada.";
    clearTimeout(pollTimer);
    refresh();
    if (job.state === "succeeded") toast(job.message);
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollJob, 700);
}

async function pollJob() {
  if (!currentJob || currentJob.state !== "running" || closing) return;
  const jobId = currentJob.id;
  try {
    const data = await api(`/api/jobs/${jobId}`);
    if (currentJob?.id !== jobId || closing) return;
    showJob(data.job);
    if (data.job.state === "running") schedulePoll();
  } catch (error) {
    $("dialog-error").textContent = error.message;
    $("dialog-error").hidden = false;
    schedulePoll();
  }
}

async function start(provider, action = "login", mode = "browser", force = false) {
  if (currentJob?.state === "running" || closing) return;
  requestOptions = { provider, action, mode, force };
  const version = ++flowVersion;
  pendingStarts += 1;
  dialogTrigger = document.activeElement;
  prepareDialog(provider, action);
  $("retry").disabled = true;
  try {
    const body = action === "login" ? { mode, force } : undefined;
    const data = await api(`/api/auth/${provider}/${action}`, "POST", body);
    if (version !== flowVersion) {
      await api(`/api/jobs/${data.job.id}/cancel`, "POST");
      return;
    }
    showJob(data.job);
    if (data.job.state === "running") schedulePoll();
  } catch (error) {
    if (version !== flowVersion) return;
    currentJob = null;
    $("dialog-message").textContent = error.message;
    $("waiting").hidden = true;
    $("retry").hidden = false;
    $("cancel").textContent = "Fechar";
  } finally { pendingStarts -= 1; $("retry").disabled = false; }
}

async function closeDialog() {
  if (closing) return;
  closing = true;
  flowVersion += 1;
  clearTimeout(pollTimer);
  try {
    if (currentJob?.state === "running") await api(`/api/jobs/${currentJob.id}/cancel`, "POST");
    currentJob = null;
    $("confirmation-code").value = "";
    $("auth-dialog").close();
    if (dialogTrigger && document.contains(dialogTrigger)) dialogTrigger.focus();
    await refresh();
  } catch (error) {
    $("dialog-error").textContent = error.message;
    $("dialog-error").hidden = false;
    schedulePoll();
  } finally { closing = false; }
}

document.querySelectorAll("[data-provider]").forEach((button) => {
  button.addEventListener("click", () => {
    const { provider, action } = button.dataset;
    if (action === "logout") {
      prepareDialog(provider, action);
      $("dialog-message").textContent = "Isso também desconecta a conta usada nesta máquina. Deseja continuar?";
      $("waiting").hidden = true;
      $("retry").hidden = false;
      $("retry").textContent = "Desconectar conta";
      requestOptions = { provider, action, mode: "browser", force: false };
      currentJob = null;
      dialogTrigger = button;
      return;
    }
    $("retry").textContent = "Tentar novamente";
    start(provider, "login", action === "device" ? "device" : "browser", action === "switch");
  });
});
$("refresh").addEventListener("click", refresh);
$("dialog-close").addEventListener("click", closeDialog);
$("cancel").addEventListener("click", closeDialog);
$("auth-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(); });
$("retry").addEventListener("click", () => { if (requestOptions) { const options = requestOptions; start(options.provider, options.action, options.mode, options.force); } });
$("copy-code").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("device-code").textContent); toast("Código copiado."); }
  catch { toast("Selecione o código e copie manualmente."); }
});
$("code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentJob) return;
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  $("dialog-error").hidden = true;
  try {
    const data = await api(`/api/jobs/${currentJob.id}/code`, "POST", { code: $("confirmation-code").value });
    $("confirmation-code").value = "";
    showJob(data.job);
  } catch (error) {
    $("dialog-error").textContent = error.message;
    $("dialog-error").hidden = false;
  } finally { button.disabled = false; }
});

(async () => {
  try {
    const bootstrap = await api("/api/bootstrap");
    csrf = bootstrap.csrf_token;
    if (bootstrap.scope === "isolated") $("storage-description").textContent = "Suas contas ficam separadas para este projeto. A autorização acontece diretamente no Claude ou no ChatGPT.";
    await refresh();
    setInterval(() => { if (!document.hidden && !$("auth-dialog").open) refresh(); }, 15000);
  } catch (error) { $("page-error").textContent = error.message; $("page-error").hidden = false; }
})();
