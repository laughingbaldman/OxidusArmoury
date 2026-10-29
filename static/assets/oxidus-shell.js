/* Shared Oxidus layout: header + grouped left nav on every page. No CDN. */
(function () {
  "use strict";
  const GROUPS = [
    ["Talk", [["\u{1F4AC}", "Chat", "/?tab=conversationTab"], ["\u{1F4AD}", "Thoughts", "/?tab=thoughtsTab"]]],
    ["Brain", [["\u{1F578}", "Brain map", "/brain"]]],
    ["Library", [["\u{1F4DA}", "Knowledge", "/?tab=knowledgeTab"], ["\u{1F5C2}", "Shelf", "/console#Shelf"], ["\u{1F4D6}", "Bible", "/console#Bible"]]],
    ["Questions", [["\u2753", "Questions", "/console#Questions"]]],
    ["Play", [["\u{1F3AE}", "Games", "/games"], ["\u{1F916}", "Agent", "/agent"]]],
    ["Settings", [["\u2699", "Settings", "/settings"]]],
    ["Admin", [["\u{1F6E0}", "Workshop", "/console"], ["\u{1F512}", "Admin", "/admin"]]],
  ];
  const PAGE = (function () {
    const p = location.pathname.replace(/\/+$/, "") || "/";
    return ({ "/": "home", "/brain": "brain", "/console": "console", "/games": "games", "/agent": "agent", "/admin": "admin", "/settings": "settings" })[p] || "other";
  })();
  const TITLES = { home: "Chat", brain: "Brain map", console: "Workshop", games: "Games", agent: "Agent", admin: "Admin", settings: "Settings", other: document.title };
  const NAV_KEY = "ox-nav-open" + (PAGE === "brain" ? "-brain" : "");

  function navDefault() {
    const saved = localStorage.getItem(NAV_KEY);
    if (saved !== null) return saved === "1";
    return PAGE !== "brain" && window.innerWidth > 860;
  }
  function isCurrent(href) {
    const u = new URL(href, location.origin);
    if (u.pathname !== location.pathname) return false;
    if (u.pathname === "/") return (new URLSearchParams(location.search).get("tab") || "conversationTab") === u.searchParams.get("tab");
    if (u.pathname === "/console") return u.hash ? decodeURIComponent(location.hash) === u.hash : !location.hash || location.hash === "#Status";
    return true;
  }
  function build() {
    if (document.querySelector(".ox-nav")) return;
    const body = document.body;
    body.classList.add("ox", "ox-page-" + PAGE);
    if (localStorage.getItem("ox-video") === "1") body.classList.add("ox-video");

    const header = document.createElement("div");
    header.className = "ox-header";
    header.innerHTML = '<button type="button" class="ox-burger" aria-label="Menu" title="Menu">\u2630</button>' +
      '<a class="ox-logo" href="/">OXI<span>D</span>US</a><span class="ox-title"></span><span class="ox-spacer"></span>' +
      '<a class="ox-pill" href="/settings" id="oxLm" title="LM Studio connection"><span class="ox-dot" id="oxLmDot"></span><span id="oxLmText">LM Studio\u2026</span></a>';
    header.querySelector(".ox-title").textContent = TITLES[PAGE] || "";

    const nav = document.createElement("nav");
    nav.className = "ox-nav";
    nav.setAttribute("aria-label", "Main");
    let html = "";
    GROUPS.forEach(([group, links]) => {
      html += `<div class="ox-group">${group}</div>`;
      links.forEach(([ico, label, href]) => { html += `<a href="${href}"><span class="ox-ico" aria-hidden="true">${ico}</span>${label}</a>`; });
    });
    nav.innerHTML = html;
    body.prepend(nav);
    body.prepend(header);

    function setOpen(open, save) {
      body.classList.toggle("ox-nav-open", open);
      if (save) localStorage.setItem(NAV_KEY, open ? "1" : "0");
      setTimeout(() => window.dispatchEvent(new Event("resize")), 200);
    }
    header.querySelector(".ox-burger").onclick = () => setOpen(!body.classList.contains("ox-nav-open"), true);
    setOpen(navDefault(), false);

    function mark() { nav.querySelectorAll("a[href]").forEach(a => a.classList.toggle("on", isCurrent(a.getAttribute("href")))); }
    nav.addEventListener("click", (e) => {
      const a = e.target.closest("a[href]");
      if (!a) return;
      const u = new URL(a.getAttribute("href"), location.origin);
      if (u.pathname === "/" && location.pathname === "/" && typeof window.showTab === "function") {
        e.preventDefault(); window.showTab(u.searchParams.get("tab")); history.replaceState(null, "", u.pathname + u.search); mark();
      } else if (u.pathname === location.pathname && u.hash) {
        e.preventDefault(); location.hash = u.hash; mark();
      }
      if (window.innerWidth <= 860) setOpen(false, false);
    });
    window.addEventListener("hashchange", mark);
    mark();
    // Old "back to chat" links are redundant with the sidebar.
    document.querySelectorAll("body > header a[href='/'], .bar a.btn[href='/']").forEach(a => { a.classList.add("ox-old-link"); a.style.display = "none"; });
    checkLm();
    setInterval(checkLm, 30000);
  }
  async function checkLm() {
    const dot = document.getElementById("oxLmDot"), text = document.getElementById("oxLmText"), pill = document.getElementById("oxLm");
    if (!dot) return;
    try {
      const d = await (await fetch("/api/settings/lm-studio")).json();
      dot.className = "ox-dot " + (d.ok ? "ok" : d.reachable ? "warn" : "bad");
      text.textContent = d.ok ? d.model : d.reachable ? (d.auth === "ok" ? "LM Studio: no model" : "LM Studio: needs token") : "LM Studio server off";
      pill.title = d.message || "";
      document.querySelectorAll("#heartModel, .ox-model-name").forEach(el => { el.textContent = d.ok ? d.model : "LM Studio not connected"; });
      window.oxLmStatus = d;
      window.dispatchEvent(new CustomEvent("ox-lm-status", { detail: d }));
    } catch (e) { dot.className = "ox-dot bad"; text.textContent = "LM Studio: unknown"; }
  }
  window.oxCheckLm = checkLm;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build); else build();
})();
