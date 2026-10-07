"use strict";

(() => {
  const root = document.documentElement;
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  let savedTheme;
  try { savedTheme = localStorage.getItem("theme"); } catch {}
  const initialTheme = ["light", "dark"].includes(savedTheme) ? savedTheme : preference.matches ? "dark" : "light";
  function applyTheme(theme) {
    root.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0f172a" : "#1e8449");
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const label = theme === "dark" ? "Ativar tema claro" : "Ativar tema escuro";
      button.setAttribute("aria-label", label);
      button.title = label;
      button.setAttribute("aria-pressed", String(theme === "dark"));
      const caption = button.querySelector(".theme-label");
      if (caption) caption.textContent = theme === "dark" ? "Tema claro" : "Tema escuro";
    });
  }
  applyTheme(initialTheme);
  try { root.dataset.sidebarCollapsed = localStorage.getItem("harness.sidebar.collapsed") === "true" ? "true" : "false"; } catch { root.dataset.sidebarCollapsed = "false"; }
  function bindControls() {
    applyTheme(root.dataset.theme);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const theme = root.dataset.theme === "dark" ? "light" : "dark";
        savedTheme = theme;
        try { localStorage.setItem("theme", theme); } catch {}
        applyTheme(theme);
      });
    });
    const sidebar = document.getElementById("sidebar");
    const toggle = document.getElementById("menu-toggle");
    const collapse = document.getElementById("sidebar-close");
    const workspace = document.querySelector(".chat-main, .accounts-workspace");
    const skipLink = document.querySelector(".skip-link");
    const mobile = window.matchMedia("(max-width: 900px)");
    function syncSidebar() {
      if (!sidebar) return;
      const open = sidebar.classList.contains("open");
      const hidden = mobile.matches && !open;
      const expanded = mobile.matches ? open : root.dataset.sidebarCollapsed !== "true";
      sidebar.inert = hidden;
      if (hidden) sidebar.setAttribute("aria-hidden", "true");
      else sidebar.removeAttribute("aria-hidden");
      if (workspace) workspace.inert = mobile.matches && open;
      if (skipLink) skipLink.inert = mobile.matches && open;
      toggle?.setAttribute("aria-expanded", String(expanded));
      const label = mobile.matches ? "Fechar menu" : expanded ? "Recolher menu" : "Expandir menu";
      collapse?.setAttribute("aria-label", label);
      collapse?.setAttribute("title", label);
      collapse?.setAttribute("aria-expanded", String(expanded));
      sidebar.querySelectorAll(".sidebar-item").forEach((item) => {
        const caption = item.querySelector(".sidebar-item-label")?.textContent;
        if (caption && !item.hasAttribute("data-theme-toggle")) item.setAttribute("aria-label", caption);
      });
    }
    function closeSidebar() {
      sidebar?.classList.remove("open");
      syncSidebar();
    }
    function toggleDesktopSidebar() {
      root.dataset.sidebarCollapsed = root.dataset.sidebarCollapsed === "true" ? "false" : "true";
      try { localStorage.setItem("harness.sidebar.collapsed", root.dataset.sidebarCollapsed); } catch {}
      syncSidebar();
    }
    toggle?.addEventListener("click", () => {
      if (!mobile.matches) { toggleDesktopSidebar(); return; }
      const open = sidebar?.classList.toggle("open");
      syncSidebar();
      if (open) collapse?.focus();
    });
    document.getElementById("sidebar-backdrop")?.addEventListener("click", () => { closeSidebar(); toggle?.focus(); });
    collapse?.addEventListener("click", () => {
      if (mobile.matches) { closeSidebar(); toggle?.focus(); }
      else toggleDesktopSidebar();
    });
    document.addEventListener("harness:close-menu", () => {
      if (!mobile.matches) return;
      const restoreFocus = sidebar?.contains(document.activeElement);
      closeSidebar();
      if (restoreFocus) toggle?.focus();
    });
    document.addEventListener("keydown", (event) => {
      if (!mobile.matches || !sidebar?.classList.contains("open")) return;
      if (event.key === "Escape") { event.preventDefault(); closeSidebar(); toggle?.focus(); }
      if (event.key === "Tab") {
        const controls = [...sidebar.querySelectorAll('a[href], button:not([disabled])')].filter((item) => item.getClientRects().length);
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    });
    mobile.addEventListener("change", () => { closeSidebar(); if (sidebar?.inert && sidebar.contains(document.activeElement)) toggle?.focus(); });
    if (sidebar) new MutationObserver(syncSidebar).observe(sidebar, { attributes: true, attributeFilter: ["class"] });
    syncSidebar();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindControls, { once: true });
  else bindControls();
  preference.addEventListener("change", (event) => { if (!savedTheme) applyTheme(event.matches ? "dark" : "light"); });
})();
