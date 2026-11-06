// Application shell: header, routing, live events, banners, overlays.

import { ApiError, api, onConnectionProblem } from "./lib/api.js";
import { h, logo } from "./lib/dom.js";
import { describeProblem } from "./lib/problems.js";
import { connectEvents } from "./lib/events.js";
import { loadLocale, setLanguage, t } from "./lib/i18n.js";
import { app, emit, on } from "./state.js";
import { mountBanners, checkUpdates } from "./components/banners.js";
import { mountDownload } from "./views/download.js";
import { mountHistory, refreshHistoryCount } from "./views/history.js";
import { mountPlaylist } from "./views/playlist.js";
import { mountQueue, refreshQueueCount } from "./views/queue.js";
import { mountSettings } from "./views/settings.js";

const ROUTES = { "#/": mountDownload, "#/playlist": mountPlaylist, "#/queue": mountQueue, "#/history": mountHistory, "#/settings": mountSettings };
const root = document.getElementById("app");
let unmount = null;
let bannersStop = null;
let overlay = null;
let badgeEl = null;
let queueBadgeEl = null;
on("queue_count", () => { if (queueBadgeEl) { queueBadgeEl.textContent = String(app.queueCount); queueBadgeEl.hidden = !app.queueCount; } });
on("history_count", () => { if (badgeEl) { badgeEl.textContent = String(app.historyCount); badgeEl.hidden = !app.historyCount; } });
on("language", () => { document.title = t("title"); buildShell(); });

function showOverlay(title, text, code = "E920") {
  overlay?.remove();
  const steps = describeProblem(code).steps;
  overlay = h("div", { class: "overlay", role: "alert" },
    h("div", { class: "overlay__box" }, h("div", { class: "overlay__title" }, title), h("p", {}, text),
      h("ol", { class: "overlay__steps" }, steps.map((step) => h("li", {}, step))),
      h("span", { class: "notice__code" }, code)));
  document.body.append(overlay);
}

function toastText(text) {
  const box = document.querySelector(".toasts") || document.body.appendChild(h("div", { class: "toasts", role: "status", "aria-live": "polite" }));
  const el = h("div", { class: "toast" }, text);
  box.append(el);
  setTimeout(() => el.remove(), 5000);
}
on("toast", toastText);

function currentRoute() {
  return ROUTES[location.hash] ? location.hash : "#/";
}

function buildShell() {
  const link = (hash, label, badge) => h("a", { class: "nav__link", href: hash, "data-route": hash }, label, badge);
  const badge = h("span", { class: "nav__badge", hidden: !app.historyCount }, String(app.historyCount));
  const lang = h("button", { class: "btn btn--ghost btn--small", type: "button", lang: app.settings.language === "ar" ? "en" : "ar",
    onclick: async () => {
      const next = app.settings.language === "ar" ? "en" : "ar";
      const { saveSettings } = await import("./state.js");
      await saveSettings({ language: next });
      await setLanguage(next);
      buildShell();
    } }, t("lang_switch"));
  const queueBadge = h("span", { class: "nav__badge", hidden: !app.queueCount }, String(app.queueCount));
  badgeEl = badge;
  queueBadgeEl = queueBadge;
  const banners = h("div", { class: "banners" });
  const outlet = h("main", { id: "view" });
  root.replaceChildren(
    h("header", { class: "header" }, h("div", { class: "header__inner" },
      h("a", { class: "brand", href: "#/" }, logo(34), t("title")),
      h("nav", { class: "nav" }, link("#/", t("tab_download")), link("#/queue", t("tab_queue"), queueBadge), link("#/history", t("tab_history"), badge), link("#/settings", t("settings_title"))),
      lang)),
    banners, outlet);
  bannersStop?.();
  bannersStop = mountBanners(banners);
  route();
}

function route() {
  const hash = currentRoute();
  unmount?.();
  const outlet = document.getElementById("view");
  if (!outlet) return;
  document.querySelectorAll("[data-route]").forEach((a) => {
    const active = a.dataset.route === hash || (hash === "#/playlist" && a.dataset.route === "#/");
    active ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  });
  unmount = ROUTES[hash](outlet);
  window.scrollTo(0, 0);
}

async function start() {
  onConnectionProblem({
    unauthorized: () => showOverlay(t("conn_unauthorized_title"), t("conn_unauthorized_text"), "E920"),
    unreachable: () => showOverlay(t("conn_lost_title"), t("conn_lost_text")),
  });
  try {
    [app.status, app.settings] = await Promise.all([api.status(), api.settings()]);
  } catch (error) {
    await loadLocale("en");
    await setLanguage("en");
    if (!overlay) showOverlay(t("conn_lost_title"), error instanceof ApiError ? error.message : t("conn_lost_text"));
    return;
  }
  await loadLocale("en");
  await setLanguage(app.settings.language);
  document.title = t("title");

  connectEvents({
    onEvent: (event) => {
      if (event.type === "hello") event.jobs.forEach((j) => (app.jobs[j.id] = j));
      else if (event.type === "job") {
        app.jobs[event.job.id] = event.job;
        emit("job", event.job);
      } else if (event.type === "queue_changed") {
        emit("queue_changed");
        refreshQueueCount();
      } else if (event.type === "queue_progress") {
        emit("queue_progress", event);
      } else if (event.type === "history_changed") {
        emit("history_changed");
        refreshHistoryCount();
      }
    },
    onGone: () => showOverlay(t("conn_lost_title"), t("conn_lost_text")),
    onBack: () => { overlay?.remove(); overlay = null; },
  });
  addEventListener("hashchange", route);
  buildShell();
  refreshHistoryCount();
  refreshQueueCount();
  checkUpdates();
}

start();
