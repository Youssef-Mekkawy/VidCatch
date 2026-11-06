// Queue page: add many links, choose quality and subtitles once (or per video), download one by one.
// All data comes from GET /api/queue; the server keeps it saved, so closing the page loses nothing.

import { api } from "../lib/api.js";
import { h, icon } from "../lib/dom.js";
import { formatBytes } from "../lib/format.js";
import { t } from "../lib/i18n.js";
import { languageName } from "../lib/languages.js";
import { app, emit, on, toast } from "../state.js";
import { choiceDialog } from "../components/dialog.js";
import { renderNotice } from "../components/notice.js";
import { runSigninFlow } from "../signin.js";

const QUALITIES = ["best", "1080p", "720p", "480p", "360p"];
const UNFINISHED = ["fetching", "ready", "queued", "downloading", "paused", "exists"];

export async function refreshQueueCount() {
  try {
    const snap = await api.queue();
    app.queueCount = snap.items.filter((i) => UNFINISHED.includes(i.status)).length;
    emit("queue_count");
    return snap;
  } catch (error) {
    return null;
  }
}

// Written out (not built from the status name) so every text is visible to the translation checks.
const STATUS_TEXT = {
  fetching: () => t("q_status_fetching"), ready: () => t("q_status_ready"), queued: () => t("q_status_queued"),
  downloading: () => t("q_status_downloading"), paused: () => t("q_status_paused"), exists: () => t("q_status_exists"),
  done: () => t("q_status_done"), skipped: () => t("q_status_skipped"), cancelled: () => t("q_status_cancelled"),
  failed: () => t("q_status_failed"),
};
const STATE_TEXT = {
  idle: () => t("queue_state_idle"), running: () => t("queue_state_running"), paused: () => t("queue_state_paused"),
};

const baseCode = (code) => code.replace(/-orig$/, "");

function seg(options, value, onPick) {
  return h("div", { class: "seg", role: "group" },
    options.map(([key, text]) => h("button", { type: "button", "aria-pressed": String(key === value), onclick: () => onPick(key) }, text)));
}

export function mountQueue(outlet) {
  let snap = null;
  const notices = {}; // item id -> notice shown in a red box (sign-in problems)
  const live = {}; // item id -> {bar, line} elements updated by progress events

  // ── Add links ─────────────────────────────────────────────────────────────
  const box = h("textarea", {
    class: "input input--ltr textarea", rows: 4, spellcheck: "false", "aria-label": t("queue_add_label"),
    placeholder: t("queue_add_placeholder"),
  });
  const counter = h("span", { class: "hint" });
  const addBtn = h("button", { class: "btn btn--primary", type: "button", onclick: addLinks }, icon("download", 20), t("queue_add_btn"));

  async function addLinks() {
    const lines = box.value.split(/\s+/).map((x) => x.trim()).filter(Boolean);
    const links = lines.filter((x) => /^https?:\/\/\S+$/i.test(x));
    const bad = lines.length - links.length;
    if (!links.length) return toast(t("queue_no_links"));
    addBtn.disabled = true;
    try {
      const result = await api.queueAdd(links);
      box.value = "";
      const parts = [result.added.length ? t("queue_added", { n: result.added.length }) : "",
        result.duplicates ? t("queue_duplicates", { n: result.duplicates }) : "",
        bad ? t("queue_invalid", { n: bad }) : ""].filter(Boolean);
      if (parts.length) toast(parts.join(" · "));
    } catch (error) {
      toast(error.status === 422 ? t("queue_full", { max: snap?.max_items ?? 50 }) : error.message);
    }
    addBtn.disabled = false;
    load();
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  const run = (promise) => promise.catch((e) => toast(e.message)).finally(load);

  async function cancelItem(it) {
    let discard = false;
    if (it.partial_bytes > 0 || it.status === "downloading") {
      const choice = await choiceDialog({
        title: t("queue_cancel_title"), text: t("queue_cancel_text"), cancelText: t("dlg_cancel"),
        choices: [{ value: "keep", text: t("queue_keep") }, { value: "discard", text: t("queue_discard"), danger: true }],
      });
      if (choice === "cancel") return;
      discard = choice === "discard";
    }
    run(api.queueCancel(it.id, discard));
  }

  async function removeItem(it) {
    run(api.queueRemove(it.id, it.partial_bytes > 0 && it.status === "paused"));
  }

  async function signinRetry(it) {
    try {
      const result = await runSigninFlow(it.error?.reason);
      if (result.notice) {
        notices[it.id] = result.notice;
        return render();
      }
      delete notices[it.id];
      await api.queueRetry({ ids: [it.id], source: result.sources[0] });
    } catch (error) {
      toast(error.message);
    }
    load();
  }

  // ── Rendering ─────────────────────────────────────────────────────────────
  const dynamic = h("div", { class: "stack" });

  function statusBadge(it) {
    return h("span", { class: "item__status", "data-state": it.status }, STATUS_TEXT[it.status]());
  }

  function itemBody(it) {
    const locked = ["downloading", "done", "skipped", "fetching"].includes(it.status);
    const subs = [...it.sub_manual, ...it.sub_auto];
    const lines = [];
    if (it.fmt === "mp3") lines.push(h("span", { class: "format" }, "MP3"));
    else if (it.qualities?.length) {
      lines.push(h("select", { class: "select select--small", disabled: locked, "aria-label": t("quality_label"),
        onchange: (e) => run(api.queuePatchItem(it.id, { quality: e.target.value })) },
      it.qualities.map((q) => h("option", { value: q, selected: q === it.quality }, q))));
    }
    if (it.est_bytes) lines.push(h("span", { class: "item__sub" }, t("queue_about", { size: formatBytes(it.est_bytes) })));
    if (it.fmt === "mp4" && snap.settings.sub_langs.length && it.status !== "fetching") {
      lines.push(h("span", { class: "item__sub" }, subs.length
        ? t("queue_subs_found", { langs: subs.map(languageName).join(", ") }) : t("queue_no_subs")));
    }
    return h("div", { class: "item__meta" }, lines);
  }

  function progressBlock(it) {
    const bar = h("div", { class: "progress__bar" });
    const line = h("span", { class: "run__stats" });
    const track = h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 }, bar);
    live[it.id] = { bar, line, track };
    paint(it.id, it.percent, it.speed, it.eta, it.phase);
    return h("div", { class: "item__progress" }, track, line);
  }

  function paint(id, percent, speed, eta, phase) {
    const el = live[id];
    if (!el) return;
    el.bar.style.width = `${percent || 0}%`;
    el.bar.toggleAttribute("data-empty", !percent);
    el.track.setAttribute("aria-valuenow", String(Math.round(percent || 0)));
    el.line.textContent = phase === "converting" ? t("converting")
      : [`${Math.round(percent || 0)}%`, speed, eta && `${t("eta")} ${eta}`].filter(Boolean).join(" · ");
  }

  function itemRow(it) {
    const thumb = h("div", { class: "thumb thumb--small" });
    if (it.thumbnail) {
      const img = h("img", { src: it.thumbnail, alt: "", loading: "lazy", width: 128, height: 72 });
      img.addEventListener("error", () => img.remove());
      thumb.append(img);
    }
    const waiting = ["queued", "ready", "paused", "downloading", "exists", "fetching"].includes(it.status);
    const actions = [];
    if (it.status === "exists") {
      actions.push(h("button", { class: "btn btn--small", type: "button", onclick: () => run(api.queueExists(it.id, true)) }, t("queue_replace")),
        h("button", { class: "btn btn--small", type: "button", onclick: () => run(api.queueExists(it.id, false)) }, t("queue_skip")));
    }
    if (["failed", "cancelled", "skipped"].includes(it.status) && it.error?.code !== "playlist") {
      actions.push(h("button", { class: "btn btn--small", type: "button", onclick: () => run(api.queueRetry({ ids: [it.id] })) }, t("queue_retry_one")));
    }
    if (it.error?.code === "needs_login") {
      actions.push(h("button", { class: "btn btn--ink btn--small", type: "button", onclick: () => signinRetry(it) }, t("queue_signin_retry")));
    }
    if (["downloading", "paused", "queued"].includes(it.status)) {
      actions.push(h("button", { class: "btn btn--small", type: "button", onclick: () => cancelItem(it) }, t("cancel_btn")));
    }
    actions.push(h("button", { class: "btn btn--ghost btn--small", type: "button", "aria-label": t("queue_remove"), onclick: () => removeItem(it) }, icon("close", 18), t("queue_remove")));

    const saved = it.status === "paused" && it.partial_bytes > 0
      ? h("div", { class: "hint" }, t("queue_saved_part", { size: formatBytes(it.partial_bytes) })) : null;
    const problem = it.status === "failed" && it.error
      ? (it.error.code === "playlist"
        ? h("div", { class: "hint hint--warn" }, t("queue_is_playlist"))
        : renderNotice(notices[it.id] || { code: it.error.problem, reason: it.error.reason, message: it.error.message }))
      : null;
    const skipped = it.status === "skipped" && it.message === "exists" ? h("div", { class: "hint" }, t("queue_skipped_exists")) : null;
    const subsSkipped = it.status === "done" && it.message === "subtitles_skipped" ? h("div", { class: "hint hint--warn" }, t("sub_skipped")) : null;

    return h("div", { class: "item item--queue" },
      thumb,
      h("div", { class: "item__text" },
        h("div", { class: "item__title" }, it.title || it.url),
        h("div", { class: "item__sub" }, [it.channel, it.duration_text].filter(Boolean).join(" · ")),
        waiting && itemBody(it), saved, skipped, subsSkipped,
        it.status === "downloading" && progressBlock(it)),
      h("div", { class: "item__actions" }, statusBadge(it), ...actions),
      problem && h("div", { class: "item__problem" }, problem));
  }

  function batchCard() {
    const s = snap.settings;
    const fmt = s.fmt || app.settings.default_format;
    const tracks = new Map();
    for (const it of snap.items.filter((i) => UNFINISHED.includes(i.status))) {
      for (const code of [...(it.tracks?.manual || []), ...(it.tracks?.auto || [])]) {
        const base = baseCode(code);
        tracks.set(base, (tracks.get(base) || 0) + 1);
      }
    }
    const langs = [...new Set([...tracks.keys(), ...s.sub_langs])]
      .sort((a, b) => (a === "en" ? -1 : b === "en" ? 1 : a === "ar" ? -1 : b === "ar" ? 1 : a.localeCompare(b)));
    const toggle = (code) => {
      const next = s.sub_langs.includes(code) ? s.sub_langs.filter((c) => c !== code) : [...s.sub_langs, code];
      run(api.queueSettings({ sub_langs: next }));
    };
    return h("div", { class: "card card--hard tool" },
      h("h2", { class: "card__title" }, t("queue_batch_title")),
      h("div", { class: "options" },
        h("div", { class: "field" }, h("span", { class: "label" }, t("format_label")),
          seg([["mp4", t("mp4_label")], ["mp3", t("mp3_label")]], fmt, (v) => run(api.queueSettings({ fmt: v })))),
        fmt === "mp4" && h("div", { class: "field" }, h("span", { class: "label" }, t("queue_quality_all")),
          h("div", { class: "chips", role: "group" },
            QUALITIES.map((q) => h("button", { class: "chip", type: "button", "aria-pressed": String(q === s.quality),
              onclick: () => run(api.queueSettings({ quality: q })) }, q === "best" ? t("s_quality_best") : q)))),
        h("div", { class: "field" }, h("span", { class: "label" }, t("queue_exists_label")),
          seg([["ask", t("queue_exists_ask")], ["skip", t("queue_exists_skip")], ["replace", t("queue_exists_replace")]], s.on_exists,
            (v) => run(api.queueSettings({ on_exists: v }))))),
      fmt === "mp4" && h("div", { class: "field subtitles" },
        h("span", { class: "label" }, `${t("queue_subs_label")}${s.sub_langs.length ? ` (${s.sub_langs.length})` : ""}`),
        langs.length
          ? h("div", { class: "chips", role: "group", "aria-label": t("queue_subs_label") },
            langs.map((code) => h("button", { class: "chip", type: "button", title: code, "aria-pressed": String(s.sub_langs.includes(code)), onclick: () => toggle(code) },
              languageName(code), h("span", { class: "chip__code" }, code))))
          : h("span", { class: "hint" }, t("queue_subs_none")),
        s.sub_langs.length > 0 && h("span", { class: "hint" }, t("queue_subs_hint"))));
  }

  function controls() {
    const c = snap.counts;
    const ready = (c.ready || 0) + (c.queued || 0) + (c.paused || 0);
    const running = snap.state === "running";
    const buttons = [];
    if (running) {
      buttons.push(h("button", { class: "btn btn--ink", type: "button", onclick: () => run(api.queuePause()) }, icon("pause", 20), t("queue_pause")));
    } else {
      const resume = (c.paused || 0) > 0 || snap.state === "paused";
      buttons.push(h("button", { class: "btn btn--primary", type: "button", disabled: ready === 0, onclick: () => run(api.queueStart()) },
        icon("download", 20), resume ? t("queue_resume") : t("queue_start_n", { n: ready })));
    }
    if (c.failed) buttons.push(h("button", { class: "btn", type: "button", onclick: () => run(api.queueRetry({})) }, t("queue_retry")));
    if ((c.done || 0) + (c.skipped || 0) + (c.cancelled || 0)) {
      buttons.push(h("button", { class: "btn btn--ghost", type: "button", onclick: () => run(api.queueClearFinished()) }, t("queue_clear_finished")));
    }
    const total = snap.items.filter((i) => UNFINISHED.includes(i.status)).length;
    const low = snap.estimate_bytes > 0 && snap.free_bytes > 0 && snap.estimate_bytes > snap.free_bytes;
    return h("div", { class: "card tool" },
      h("div", { class: "row queue__bar" }, ...buttons),
      h("div", { class: "queue__stats" },
        h("span", { class: "run__stats" }, [
          t("queue_total", { n: total, size: snap.estimate_bytes ? formatBytes(snap.estimate_bytes) : "—" }),
          snap.free_bytes ? t("queue_free", { size: formatBytes(snap.free_bytes) }) : "",
        ].filter(Boolean).join(" · ")),
        h("span", { class: "hint" }, STATE_TEXT[snap.state]())),
      low && h("div", { class: "hint hint--warn", role: "alert" }, t("queue_low_space")),
      snap.halted === "disk_full" && renderNotice({ code: "E140", message: "" }),
      snap.partial_bytes > 0 && h("div", { class: "row queue__partial" },
        h("span", { class: snap.partial_days_left <= 2 ? "hint hint--warn" : "hint" },
          snap.partial_days_left <= 2
            ? t("queue_partial_expiring", { size: formatBytes(snap.partial_bytes), days: snap.partial_days_left })
            : t("queue_partial", { size: formatBytes(snap.partial_bytes), days: snap.partial_days_left })),
        snap.state !== "running" && h("button", { class: "btn btn--ghost btn--small", type: "button",
          onclick: () => run(api.queueClearPartials().then(() => toast(t("queue_partial_cleared")))) }, t("queue_partial_clear"))));
  }

  function render() {
    if (!snap) return;
    for (const key of Object.keys(live)) delete live[key];
    counter.textContent = t("queue_counter", { n: snap.items.length, max: snap.max_items });
    dynamic.replaceChildren(...(snap.items.length ? [
      batchCard(), controls(),
      h("div", { class: "card list" }, snap.items.map(itemRow)),
    ] : [h("div", { class: "empty" }, h("strong", {}, t("queue_empty")), h("span", { class: "muted" }, t("queue_empty_hint")))]));
  }

  async function load() {
    const fresh = await refreshQueueCount();
    if (fresh) {
      snap = fresh;
      render();
    }
  }

  outlet.replaceChildren(h("section", { class: "page" },
    h("div", { class: "page__head" }, h("h1", { class: "page__title" }, t("queue_title"))),
    h("div", { class: "card card--hard tool" },
      h("label", { class: "label", for: "queue-links" }, t("queue_add_label")), box,
      h("div", { class: "row queue__bar" }, addBtn, counter)),
    dynamic));
  box.id = "queue-links";
  load();

  const stops = [
    on("queue_changed", load),
    on("queue_progress", (e) => paint(e.id, e.percent, e.speed, e.eta, e.phase)),
  ];
  return () => stops.forEach((stop) => stop());
}
