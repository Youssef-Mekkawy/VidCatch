// Playlist page: choose videos, one format for all, download them one after another.

import { api } from "../lib/api.js";
import { h, icon } from "../lib/dom.js";
import { t } from "../lib/i18n.js";
import { app, activeJob, emit, on, toast } from "../state.js";

const QUALITIES = ["best", "1080p", "720p", "480p", "360p"];
const changed = () => emit("playlist");

async function startPlaylist() {
  const p = app.playlist;
  const entries = p.entries.filter((_, i) => p.checked[i]).map((e) => ({ title: e.title, url: e.url }));
  if (!entries.length) return toast(t("playlist_none_sel"));
  try {
    const result = await api.startPlaylist({
      title: p.title, entries, fmt: p.fmt, quality: p.fmt === "mp4" ? p.quality : null,
    });
    p.jobId = result.job.id;
    app.jobs[result.job.id] = result.job;
    changed();
  } catch (error) {
    toast(error.status === 409 ? t("task_running") : error.message);
  }
}

function statusText(job) {
  if (job.state === "running") return `${t("playlist_progress")} ${job.current}/${job.total} · ${Math.round(job.percent)}%`;
  if (job.state === "cancelled") return t("cancelled");
  if (job.state === "failed") return t("failed");
  const s = job.summary || {};
  const text = t("playlist_summary", { done: s.done ?? 0, skipped: s.skipped ?? 0, failed: s.failed ?? 0 });
  return s.signin ? `${text} · ${t("playlist_need_signin", { n: s.signin })}` : text;
}

export function mountPlaylist(outlet) {
  const p = app.playlist;
  if (!p) {
    location.hash = "#/";
    return () => {};
  }
  const job = () => (p.jobId ? app.jobs[p.jobId] : null);
  const isRunning = () => job()?.state === "running";

  const status = h("div", { class: "hint", role: "status" });
  const bar = h("div", { class: "progress__bar" });
  const progress = h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 }, bar);
  const countText = h("span", { class: "list__count" });
  const startBtn = h("button", { class: "btn btn--primary", type: "button", onclick: startPlaylist }, icon("download", 20), t("playlist_download_sel"));
  const cancelBtn = h("button", { class: "btn", type: "button", onclick: () => api.cancelJob(p.jobId).catch(() => {}) }, t("cancel_btn"));
  const checks = [];

  const setAll = (value) => {
    p.checked = p.checked.map(() => value);
    checks.forEach((c) => (c.checked = value));
    refresh();
  };

  const rows = p.entries.map((entry, i) => {
    const box = h("input", { type: "checkbox", checked: p.checked[i], "aria-label": entry.title });
    box.addEventListener("change", () => { p.checked[i] = box.checked; refresh(); });
    checks.push(box);
    const thumb = h("div", { class: "thumb thumb--small" });
    if (entry.thumbnail) {
      const img = h("img", { src: entry.thumbnail, alt: "", loading: "lazy", width: 128, height: 72 });
      img.addEventListener("error", () => img.remove());
      thumb.append(img);
    }
    return h(
      "div", { class: "item" },
      h("label", { class: "item__check" }, box),
      thumb,
      h("div", { class: "item__text" },
        h("div", { class: "item__title" }, entry.title || "—"),
        entry.duration_text && h("div", { class: "item__sub" }, entry.duration_text)),
    );
  });

  const seg = (options, value, onPick) =>
    h("div", { class: "seg", role: "group" },
      options.map(([key, text]) => h("button", { type: "button", "aria-pressed": String(key === value), onclick: () => onPick(key) }, text)));

  const optionsBox = h("div", { class: "options" });
  function buildOptions() {
    const lock = isRunning();
    optionsBox.replaceChildren(...[
      h("div", { class: "field" }, h("span", { class: "label" }, t("format_label")),
        seg([["mp4", t("mp4_label")], ["mp3", t("mp3_label")]], p.fmt, (fmt) => { if (!lock) { p.fmt = fmt; buildOptions(); } })),
      p.fmt === "mp4" && h("div", { class: "field" }, h("span", { class: "label" }, t("quality_label")),
        h("div", { class: "chips", role: "group" },
          QUALITIES.map((q) => h("button", { class: "chip", type: "button", disabled: lock, "aria-pressed": String(q === p.quality), onclick: () => { p.quality = q; buildOptions(); } },
            q === "best" ? t("s_quality_best") : q)))),
    ].filter(Boolean));
  }

  const page = h(
    "section", { class: "page" },
    h("a", { class: "btn btn--ghost btn--small page__back", href: "#/" }, icon("back", 18), t("playlist_back")),
    h("div", {}, h("span", { class: "kicker" }, `${p.entries.length} ${t("playlist_videos")}`),
      h("h1", { class: "page__title" }, p.title || "—"),
      p.channel && h("p", { class: "muted" }, p.channel)),
    h("div", { class: "card card--hard tool" }, optionsBox, startBtn, status, progress, cancelBtn),
    h("div", { class: "card list" },
      h("div", { class: "list__bar" },
        h("button", { class: "btn btn--small", type: "button", onclick: () => setAll(true) }, t("playlist_select_all")),
        h("button", { class: "btn btn--small", type: "button", onclick: () => setAll(false) }, t("playlist_deselect_all")),
        countText),
      rows),
  );
  outlet.replaceChildren(page);
  buildOptions();

  function refresh() {
    const j = job();
    const running = isRunning();
    const n = p.checked.filter(Boolean).length;
    countText.textContent = `${n}/${p.entries.length}`;
    startBtn.hidden = running;
    startBtn.disabled = !!activeJob() || n === 0;
    startBtn.lastChild.textContent = n ? `${t("playlist_download_sel")} (${n})` : t("playlist_download_sel");
    cancelBtn.hidden = !running;
    cancelBtn.disabled = !!j?.cancelling;
    progress.hidden = !j;
    status.hidden = !j;
    if (j) {
      status.textContent = `${statusText(j)}${running && j.title ? ` — ${j.title}` : ""}`;
      bar.style.width = `${j.percent}%`;
      bar.toggleAttribute("data-empty", j.percent <= 0);
      progress.setAttribute("aria-valuenow", String(Math.round(j.percent)));
    }
  }
  let wasRunning = isRunning();
  const onJob = () => {
    refresh();
    if (wasRunning !== isRunning()) { wasRunning = isRunning(); buildOptions(); }
  };
  refresh();
  const stops = [on("job", onJob), on("playlist", refresh)];
  return () => stops.forEach((s) => s());
}
