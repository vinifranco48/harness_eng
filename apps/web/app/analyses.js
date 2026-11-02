"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const types = {quantitative: "Número", nominal: "Texto", temporal: "Data"};
  const date = (value) => new Date(value).toLocaleDateString("pt-BR");
  const number = new Intl.NumberFormat("pt-BR", {maximumFractionDigits: 2});
  const analysisId = location.pathname.split("/")[2] || null;
  let csrf = "";
  let datasets = [];
  let charts = [];
  let analysis = null;
  let offset = 0;
  let renderVersion = 0;
  let resizeTimer;
  let toastTimer;
  const views = new Map();

  async function api(path, {method = "GET", data, form} = {}) {
    const headers = {"X-Harness-CSRF": csrf};
    if (data !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {method, headers, body: form || (data === undefined ? undefined : JSON.stringify(data))});
    let payload;
    try { payload = await response.json(); } catch { throw new Error("O servidor não respondeu. Tente novamente."); }
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Confira os campos informados.");
    return payload;
  }
  function error(message = "", id = "page-error") { $(id).textContent = message; $(id).hidden = !message; }
  function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500); }
  function node(tag, className, text) { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; }
  function chatLink(prompt) { return `/?new=1&provider=codex&prompt=${encodeURIComponent(prompt)}`; }
  async function busy(button, action, errorId = "page-error") {
    button.disabled = true; error("", errorId);
    try { await action(); } catch (exception) { error(exception.message, errorId); }
    finally { button.disabled = false; }
  }
  function empty(title, text) { const box = node("div", "empty-state"); box.append(node("h3", "", title), node("p", "", text)); return box; }

  async function loadAnalyses(append = false) {
    const data = await api(`/api/analyses?limit=20&offset=${append ? offset : 0}`);
    if (!append) { $("analysis-list").replaceChildren(); offset = 0; }
    for (const item of data.analyses) {
      const link = node("a", "analysis-card"); link.href = `/analyses/${item.id}`;
      link.append(node("strong", "", item.title), node("span", "", `Criada em ${date(item.created_at)} · Abrir análise →`));
      $("analysis-list").append(link);
    }
    offset += data.analyses.length;
    if (!offset) $("analysis-list").append(empty("Seu primeiro ponto de vista", "Crie uma análise para reunir gráficos ou peça ao Codex para começar com a base de exemplo."));
    $("more-analyses").hidden = data.analyses.length < 20;
  }

  async function loadDatasets() {
    datasets = (await api("/api/datasets")).datasets;
    $("dataset-list").replaceChildren();
    for (const dataset of datasets) {
      const card = node("article", "dataset-card");
      card.append(node("strong", "", dataset.name), node("p", "", `${number.format(dataset.row_count)} linhas · ${dataset.columns.length} colunas`));
      const actions = node("div", "dataset-actions");
      const preview = node("button", "text-button", "Ver dados"); preview.type = "button";
      preview.addEventListener("click", () => busy(preview, () => previewDataset(dataset.id)));
      const link = node("a", "", "Analisar com Codex ↗");
      link.href = chatLink(`Use a base de dados "${dataset.name}" (ID ${dataset.id}) e crie uma análise com gráficos úteis. Consulte as colunas antes de escolher os gráficos e retorne o link da análise salva.`);
      actions.append(preview, link); card.append(actions); $("dataset-list").append(card);
    }
    if (!datasets.length) $("dataset-list").append(empty("Adicione seus dados", "Importe uma planilha ou use os dados de exemplo para experimentar."));
  }

  async function previewDataset(id) {
    const dataset = (await api(`/api/datasets/${id}`)).dataset;
    $("preview-title").textContent = dataset.name;
    $("preview-caption").textContent = `Prévia de ${dataset.preview.length} das ${number.format(dataset.row_count)} linhas. ${dataset.filename}`;
    const head = node("thead"); const header = node("tr");
    for (const column of dataset.columns) { const cell = node("th", "", column.name); cell.scope = "col"; cell.append(node("small", "", types[column.type])); header.append(cell); }
    head.append(header);
    const body = node("tbody");
    for (const row of dataset.preview) {
      const tr = node("tr");
      for (const column of dataset.columns) {
        const value = row[column.name];
        tr.append(node("td", column.type === "quantitative" ? "numeric" : "", value === null ? "—" : typeof value === "number" ? number.format(value) : String(value)));
      }
      body.append(tr);
    }
    $("data-preview").replaceChildren(head, body); $("preview-dialog").showModal();
  }

  function tooltip(_handler, event, _item, value) {
    const tip = $("chart-tooltip");
    if (!value) { tip.hidden = true; return; }
    tip.textContent = typeof value === "object" ? Object.entries(value).map(([key, val]) => `${key}: ${typeof val === "number" ? number.format(val) : val}`).join("\n") : String(value);
    tip.hidden = false;
    tip.style.left = `${Math.max(8, Math.min(event.clientX + 12, innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${Math.max(8, Math.min(event.clientY + 12, innerHeight - tip.offsetHeight - 8))}px`;
  }

  async function renderCharts() {
    const version = ++renderVersion;
    for (const view of views.values()) view.finalize();
    views.clear(); $("chart-grid").replaceChildren(); $("chart-tooltip").hidden = true;
    $("charts-empty").hidden = charts.length > 0;
    const styles = getComputedStyle(document.documentElement);
    const color = (name) => styles.getPropertyValue(name).trim();
    for (const chart of charts) {
      if (version !== renderVersion) return;
      const card = node("article", "chart-card"); card.dataset.chartId = chart.id;
      const header = node("header"); header.append(node("h3", "", chart.title)); card.append(header);
      const dataset = datasets.find((item) => item.id === chart.dataset_id);
      card.append(node("p", "chart-description", `${dataset?.name || "Base importada"} · ${number.format(chart.spec.data.values.length)} registros`));
      const host = node("div", "chart-view"); host.setAttribute("aria-label", chart.title);
      const status = node("p", "chart-status", "Desenhando gráfico…"); status.setAttribute("role", "status");
      const footer = node("footer", "chart-footer");
      const exportButton = node("button", "text-button", "Baixar SVG"); exportButton.type = "button"; exportButton.disabled = true;
      footer.append(node("span", "", "Vega-Lite · Salvo"), exportButton);
      card.append(host, status, footer); $("chart-grid").append(card);
      try {
        const spec = structuredClone(chart.spec); delete spec.title;
        spec.width = "container"; spec.height = 260; spec.background = "transparent";
        const result = await vegaEmbed(host, spec, {
          renderer: "svg", ast: true, actions: false, defaultStyle: false, tooltip,
          formatLocale: {decimal: ",", thousands: ".", grouping: [3], currency: ["R$ ", ""]},
          timeFormatLocale: {dateTime: "%A, %e de %B de %Y, %X", date: "%d/%m/%Y", time: "%H:%M:%S", periods: ["AM", "PM"],
            days: ["domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado"],
            shortDays: ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"],
            months: ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
            shortMonths: ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]},
          config: {font: "Segoe UI, sans-serif", view: {stroke: null},
            axis: {labelColor: color("--text-muted"), titleColor: color("--text-color"), gridColor: color("--border-color"), domainColor: color("--border-color"), tickColor: color("--border-color"), labelFontSize: 11, titleFontSize: 11, titlePadding: 12},
            legend: {labelColor: color("--text-muted"), titleColor: color("--text-color"), orient: "bottom"},
            mark: {color: color("--primary-green")}, range: {category: ["#5b8368", "#6b88a8", "#bd8a55", "#9980a4", "#6b9d9b", "#bf7971"]}}
        });
        if (version !== renderVersion) { result.view.finalize(); return; }
        views.set(chart.id, result.view); status.hidden = true; exportButton.disabled = false;
        exportButton.addEventListener("click", () => busy(exportButton, async () => {
          const svg = await result.view.toSVG();
          const url = URL.createObjectURL(new Blob([svg], {type: "image/svg+xml"}));
          const link = node("a"); link.href = url; link.download = `${chart.title.replace(/[^\p{L}\p{N}_-]/gu, "_")}.svg`; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }));
      } catch { status.textContent = "Não foi possível desenhar este gráfico. Recarregue a página para tentar novamente."; status.classList.add("failed"); }
    }
  }

  async function loadDetail() {
    const [details, items] = await Promise.all([api(`/api/analyses/${analysisId}`), api(`/api/analyses/${analysisId}/charts`)]);
    analysis = details.analysis; charts = items.charts;
    $("analysis-title").textContent = analysis.title; document.title = `${analysis.title} · Insolo`;
    $("analysis-meta").textContent = `Criada em ${date(analysis.created_at)} · ${charts.length} ${charts.length === 1 ? "gráfico" : "gráficos"}`;
    $("analysis-chat").href = chatLink(`Na análise "${analysis.title}" (ID ${analysis.id}), consulte os gráficos existentes e as bases disponíveis. Sugira o próximo gráfico e me ajude a criá-lo.`);
    $("analysis-detail").hidden = false;
    await renderCharts();
  }

  function populateFields() {
    const dataset = datasets.find((item) => item.id === $("chart-dataset").value);
    for (const id of ["chart-x", "chart-y", "chart-color"]) $(id).replaceChildren();
    $("chart-color").add(new Option("Sem agrupamento adicional", ""));
    for (const column of dataset?.columns || []) {
      $("chart-x").add(new Option(column.name, column.name));
      if (column.type === "quantitative" || $("chart-aggregate").value === "count") $("chart-y").add(new Option(column.name, column.name));
      if (column.type === "nominal") $("chart-color").add(new Option(column.name, column.name));
    }
    if (dataset?.columns.some((column) => column.name === "receita")) $("chart-y").value = "receita";
  }

  $("new-analysis").addEventListener("click", () => { error("", "analysis-form-error"); $("analysis-form").reset(); $("analysis-dialog").showModal(); });
  $("analysis-form").addEventListener("submit", (event) => {
    event.preventDefault(); busy(event.submitter, async () => {
      const data = await api("/api/analyses", {method: "POST", data: {title: $("analysis-name").value}});
      location.assign(`/analyses/${data.analysis.id}`);
    }, "analysis-form-error");
  });
  $("refresh-analyses").addEventListener("click", (event) => busy(event.currentTarget, () => loadAnalyses()));
  $("more-analyses").addEventListener("click", (event) => busy(event.currentTarget, () => loadAnalyses(true)));
  $("upload-trigger").addEventListener("click", () => $("dataset-file").click());
  $("dataset-file").addEventListener("change", () => {
    const file = $("dataset-file").files[0]; if (!file) return;
    busy($("upload-trigger"), async () => {
      if (file.size > 5 * 1024 * 1024) throw new Error("Envie um arquivo de até 5 MB.");
      const form = new FormData(); form.append("file", file);
      const data = await api("/api/datasets", {method: "POST", form});
      await loadDatasets(); toast(`Base disponível: ${data.dataset.name}.`);
    }).finally(() => { $("dataset-file").value = ""; });
  });
  $("import-demo").addEventListener("click", (event) => busy(event.currentTarget, async () => { await api("/api/datasets/demo", {method: "POST"}); await loadDatasets(); toast("A base fictícia de vendas está pronta para usar."); }));
  $("new-chart").addEventListener("click", () => {
    error("", "chart-form-error");
    if (!datasets.length) { error("Importe uma base de dados na página Análises antes de adicionar um gráfico."); return; }
    $("chart-form").reset(); $("chart-dataset").replaceChildren();
    for (const dataset of datasets) $("chart-dataset").add(new Option(dataset.name, dataset.id));
    populateFields(); $("chart-dialog").showModal();
  });
  $("chart-dataset").addEventListener("change", populateFields);
  $("chart-aggregate").addEventListener("change", populateFields);
  $("chart-form").addEventListener("submit", (event) => {
    event.preventDefault(); busy(event.submitter, async () => {
      const data = {dataset_id: $("chart-dataset").value, title: $("chart-title").value, mark: $("chart-mark").value, x: $("chart-x").value, y: $("chart-y").value, aggregate: $("chart-aggregate").value, color: $("chart-color").value || null};
      await api(`/api/analyses/${analysisId}/charts`, {method: "POST", data});
      $("chart-dialog").close(); await loadDetail(); toast("Gráfico salvo.");
    }, "chart-form-error");
  });
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
  const rerender = () => { if (charts.length) { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderCharts, 120); } };
  new MutationObserver(rerender).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme", "data-sidebar-collapsed"]});
  let lastWidth = 0;
  new ResizeObserver(([entry]) => { const width = Math.round(entry.contentRect.width); if (width !== lastWidth) { lastWidth = width; rerender(); } }).observe($("chart-grid"));
  window.addEventListener("pagehide", () => { renderVersion++; for (const view of views.values()) view.finalize(); });

  (async () => {
    try {
      csrf = (await api("/api/bootstrap")).csrf_token;
      await loadDatasets();
      if (analysisId) await loadDetail();
      else { await loadAnalyses(); $("library").hidden = false; }
    } catch (exception) { error(exception.message); }
    finally { $("loading-status").hidden = true; }
  })();
})();
