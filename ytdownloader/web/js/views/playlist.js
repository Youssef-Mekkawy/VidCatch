// Playlist page: choose videos and a format, then send them to the queue (which downloads them one
// by one, can pause and resume, and survives closing the app).

import { api } from "../lib/api.js";
import { h, icon } from "../lib/dom.js";
import { t } from "../lib/i18n.js";
import { app, toast } from "../state.js";

const QUALITIES = ["best", "1080p", "720p", "480p", "360p"];

export function mountPlaylist(outlet) {
  const p = app.playlist;
  if (!p) {
    location.hash = "#/";
    return () => {};
  }

  const countText = h("span", { class: "list__count" });
  const addBtn = h("button", { class: "btn btn--primary", type: "button", onclick: addToQueue }, icon("download", 20), t("playlist_add_queue"));
  const checks = [];

  async function addToQueue() {
    const urls = p.entries.filter((_, i) => p.checked[i]).map((e) => e.url);
    if (!urls.length) return toast(t("playlist_none_sel"));
    addBtn.disabled = true;
    try {
      const result = await api.queueAdd(urls, { fmt: p.fmt, quality: p.fmt === "mp4" ? p.quality : null, start: true, partial: true });
      const parts = [t("queue_added", { n: result.added.length }),
        result.duplicates ? t("queue_duplicates", { n: result.duplicates }) : "",
        result.left_out ? t("playlist_left_out", { n: result.left_out }) : ""].filter(Boolean);
      toast(parts.join(" · "));
      location.hash = "#/queue";
    } catch (error) {
      toast(error.message);
      addBtn.disabled = false;
    }
  }

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
    optionsBox.replaceChildren(...[
      h("div", { class: "field" }, h("span", { class: "label" }, t("format_label")),
        seg([["mp4", t("mp4_label")], ["mp3", t("mp3_label")]], p.fmt, (fmt) => { p.fmt = fmt; buildOptions(); })),
      p.fmt === "mp4" && h("div", { class: "field" }, h("span", { class: "label" }, t("quality_label")),
        h("div", { class: "chips", role: "group" },
          QUALITIES.map((q) => h("button", { class: "chip", type: "button", "aria-pressed": String(q === p.quality), onclick: () => { p.quality = q; buildOptions(); } },
            q === "best" ? t("s_quality_best") : q)))),
    ].filter(Boolean));
  }

  outlet.replaceChildren(h(
    "section", { class: "page" },
    h("a", { class: "btn btn--ghost btn--small page__back", href: "#/" }, icon("back", 18), t("playlist_back")),
    h("div", {}, h("span", { class: "kicker" }, `${p.entries.length} ${t("playlist_videos")}`),
      h("h1", { class: "page__title" }, p.title || "—"),
      p.channel && h("p", { class: "muted" }, p.channel)),
    h("div", { class: "card card--hard tool" }, optionsBox, addBtn, h("div", { class: "hint" }, t("playlist_queue_hint"))),
    h("div", { class: "card list" },
      h("div", { class: "list__bar" },
        h("button", { class: "btn btn--small", type: "button", onclick: () => setAll(true) }, t("playlist_select_all")),
        h("button", { class: "btn btn--small", type: "button", onclick: () => setAll(false) }, t("playlist_deselect_all")),
        countText),
      rows),
  ));
  buildOptions();

  function refresh() {
    const n = p.checked.filter(Boolean).length;
    countText.textContent = `${n}/${p.entries.length}`;
    addBtn.disabled = n === 0;
    addBtn.lastChild.textContent = n ? `${t("playlist_add_queue")} (${n})` : t("playlist_add_queue");
  }
  refresh();
  return () => {};
}
