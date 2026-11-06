// Download page: paste a link, fetch the video, choose options, download.
//
// The logic (fetch, sign-in retry, download, job events) lives at module level and works on
// app.download, so it keeps running when the language changes and the page is rebuilt. The view
// only draws app.download and re-draws when it changes ("download" event).

import { ApiError, api } from "../lib/api.js";
import { h, icon, playMark } from "../lib/dom.js";
import { formatBytes, isYoutubeLink } from "../lib/format.js";
import { t } from "../lib/i18n.js";
import { languageName } from "../lib/languages.js";
import { app, activeJob, emit, on, toast } from "../state.js";
import { confirmDialog } from "../components/dialog.js";
import { renderNotice } from "../components/notice.js";
import { runSigninFlow, signedInAs } from "../signin.js";

const d = app.download;
const changed = () => emit("download");

// ── Logic ───────────────────────────────────────────────────────────────────
function showError(error) {
  const status = error instanceof ApiError ? error.status : 0;
  const code = status === 409 ? "E930" : status === 422 || status === 400 ? "E161" : "E910";
  d.notice = { kind: "error", code, message: error instanceof ApiError ? error.message : String(error) };
}

export async function fetchVideo() {
  const url = d.url.trim();
  if (!url) return toast(t("no_url"));
  if (d.phase === "fetching") return;
  Object.assign(d, { phase: "fetching", notice: null, source: null, video: null, status: "", jobId: null, fetchedUrl: "" });
  changed();
  try {
    await handleFetched(await api.fetchInfo(url), url);
  } catch (error) {
    d.phase = "empty";
    showError(error);
    changed();
  }
}

async function handleFetched(result, url) {
  switch (result.status) {
    case "ok":
      return applyFetched(result, url);
    case "needs_login":
      return fetchWithSignin(url, result.reason);
    case "offline":
      d.notice = { kind: "offline" };
      break;
    case "signin_failed":
      d.notice = { kind: "rejected", reason: result.reason, detail: result.detail };
      break;
    default:
      d.notice = { kind: "error", code: result.problem, message: result.message };
  }
  d.phase = "empty";
  changed();
}

async function fetchWithSignin(url, reason) {
  d.status = t("signin_trying");
  changed();
  const flow = await runSigninFlow(reason);
  d.status = "";
  if (flow.notice) {
    d.phase = "empty";
    d.notice = flow.notice;
    return changed();
  }
  await handleFetched(await api.fetchInfo(url, flow.sources), url);
}

function applyFetched(result, url) {
  if (result.type === "playlist") {
    const s = app.settings;
    app.playlist = {
      ...result.playlist, url,
      checked: result.playlist.entries.map(() => true),
      fmt: s.default_format, quality: s.default_quality,
    };
    d.phase = "empty";
    changed();
    location.hash = "#/playlist";
    return;
  }
  Object.assign(d, {
    phase: "loaded", video: result.video, fetchedUrl: url, source: result.source,
    quality: result.video.quality, subManual: [], subAuto: [], status: signedInAs(result.source) || t("signin_not_used"), notice: null,
  });
  changed();
  if (app.settings.auto_download) setTimeout(() => startDownload(), 300);
}

let starting = false;
export async function startDownload(overwrite = false, subsOnly = false) {
  if (!d.video || starting) return;
  starting = true;
  try {
    await startDownloadNow(overwrite, subsOnly);
  } finally {
    starting = false;
  }
}

async function startDownloadNow(overwrite, subsOnly) {
  if (d.url.trim() !== d.fetchedUrl) return toast(t("no_quality"));
  const mp4 = d.fmt === "mp4";
  const body = {
    url: d.fetchedUrl, title: d.video.title, fmt: d.fmt, overwrite,
    quality: mp4 && d.video.qualities.length ? d.quality : null,
  };
  if (mp4 && (d.subManual.length || d.subAuto.length)) {
    body.sub_manual = d.subManual;
    body.sub_auto = d.subAuto;
    if (subsOnly) body.subtitles_only = true;
  }
  if (subsOnly && !body.subtitles_only) return toast(t("sub_none_at_all"));
  if (d.source) body.source = { browser: d.source.browser, profile: d.source.profile, label: d.source.label };

  let result;
  try {
    result = await api.startDownload(body);
  } catch (error) {
    return toast(error.status === 409 ? t("task_running") : error.message);
  }
  if (result.status === "exists") {
    const yes = await confirmDialog({
      title: t("dup_title"),
      text: t("dup_text", {
        filename: result.filename, size: formatBytes(result.size),
        date: new Date(result.modified * 1000).toLocaleDateString(document.documentElement.lang),
      }),
      okText: t("dup_again"), cancelText: t("dup_skip"),
      extra: { text: t("dup_open"), onClick: () => api.openFolder().catch(() => {}) },
    });
    if (yes) setTimeout(() => startDownload(true, subsOnly));
    else { d.status = t("dup_skip"); changed(); }
    return;
  }
  Object.assign(d, { jobId: result.job.id, notice: null, status: "", subsOnly });
  app.jobs[result.job.id] = result.job;
  changed();
  // A job that fails at once can finish before this response arrives; handle that result too.
  onJob(result.job);
}

export async function cancelDownload() {
  if (d.jobId) await api.cancelJob(d.jobId).catch(() => {});
}

const finished = new Set();

/** Reacts once to the end of the download this page started. */
async function onJob(job) {
  if (job.id !== d.jobId || job.state === "running" || finished.has(job.id)) return;
  finished.add(job.id);
  if (job.state === "done") {
    d.status = `${t("completed")} · ${d.source ? signedInAs(d.source) : t("signin_not_used")}`;
    if (app.settings.clear_url) d.url = "";
  } else if (job.state === "cancelled") {
    d.status = t("cancelled");
  } else if (job.error?.code === "needs_login") {
    d.status = t("signin_trying");
    changed();
    const flow = await runSigninFlow(job.error.reason);
    if (flow.sources) {
      d.source = flow.sources[0];
      d.status = signedInAs(d.source);
      changed();
      return startDownload(true, d.subsOnly);
    }
    d.status = t("failed");
    d.notice = flow.notice;
  } else if (job.error?.code === "signin_rejected") {
    d.status = t("failed");
    d.notice = { kind: "rejected", reason: job.error.reason, detail: job.error.message };
  } else {
    d.status = t("failed");
    d.notice = { kind: "error", code: job.error?.problem || "E900", message: job.error?.message || "" };
  }
  changed();
}
on("job", onJob);

// ── View ────────────────────────────────────────────────────────────────────
function sizeText() {
  const { video } = d;
  if (!video) return "";
  const size = d.fmt === "mp3" ? video.size_texts.audio : video.size_texts[d.quality];
  return size ? `${t("filesize_label")}: ${size}` : t("filesize_unknown");
}

function thumbnail(video) {
  const box = h("div", { class: "thumb" });
  if (video.thumbnail) {
    const img = h("img", { src: video.thumbnail, alt: "", loading: "lazy", width: 192, height: 108 });
    img.addEventListener("error", () => img.remove());
    box.append(img);
  }
  box.append(playMark());
  if (video.duration_text) box.append(h("span", { class: "thumb__time" }, video.duration_text));
  return box;
}

export function mountDownload(outlet) {
  const urlInput = h("input", {
    class: "input input--ltr", id: "url", type: "text", value: d.url, autocomplete: "off",
    spellcheck: "false", placeholder: t("url_placeholder"),
  });
  const fetchBtn = h("button", { class: "btn btn--primary btn--field", type: "button" });
  const infoBox = h("div", {});
  const noticeBox = h("div", {});
  const optionsBox = h("div", { class: "options" });
  const runBox = h("div", { class: "run" });

  urlInput.addEventListener("input", () => {
    d.url = urlInput.value;
    refreshRun();
  });
  urlInput.addEventListener("keydown", (event) => event.key === "Enter" && fetchVideo());
  urlInput.addEventListener("paste", () =>
    setTimeout(() => {
      d.url = urlInput.value;
      if (isYoutubeLink(d.url) && d.phase !== "fetching" && !activeJob()) fetchVideo();
    }, 50),
  );
  fetchBtn.addEventListener("click", fetchVideo);

  const card = h(
    "div",
    { class: "card card--hard tool" },
    h("div", { class: "field" }, h("label", { class: "label", for: "url" }, t("url_label")), h("div", { class: "row" }, urlInput, fetchBtn)),
    infoBox, noticeBox, optionsBox, runBox,
  );
  const page = h(
    "section",
    { class: "page" },
    h(
      "div",
      { class: "intro" },
      h("h1", { class: "intro__title" }, t("intro_a"), " ", h("mark", {}, t("intro_b"))),
      h("p", { class: "intro__text" }, t("intro_text")),
    ),
    card,
    h("p", { class: "legal" }, t("legal_note")),
  );
  outlet.replaceChildren(page);

  // Each part is rebuilt only when what it shows has changed, so the thumbnail does not
  // reload and focus is not lost while progress events arrive ten times a second.
  const keys = {};
  const rebuild = (name, el, key, build) => {
    if (keys[name] === key) return;
    keys[name] = key;
    el.replaceChildren(...[build()].flat().filter(Boolean));
  };

  function refreshInfo() {
    const key = [d.phase, d.video?.id, d.fmt, d.quality, d.status].join("|");
    rebuild("info", infoBox, key, () => {
      if (d.phase === "fetching") return emptyBox(t("fetching"), d.status);
      if (!d.video) return d.notice ? null : emptyBox(t("info_empty_title"), t("info_empty_hint"));
      const { video } = d;
      return h(
        "div",
        { class: "video" },
        thumbnail(video),
        h(
          "div",
          { class: "video__text" },
          h("h2", { class: "video__title" }, video.title || "—"),
          h("div", { class: "video__meta" },
            video.channel && h("span", {}, video.channel),
            video.views_text && h("span", {}, `${video.views_text} ${t("views")}`)),
          h("div", { class: "video__size" }, sizeText()),
          d.status && h("div", { class: "hint" }, d.status),
        ),
      );
    });
  }

  function emptyBox(title, text) {
    return h("div", { class: "tool__empty" }, h("strong", {}, title), text && h("span", { class: "muted" }, text));
  }

  function refreshNotice() {
    const key = d.notice ? JSON.stringify(d.notice) : "";
    rebuild("notice", noticeBox, key, () =>
      d.notice ? renderNotice(d.notice, { onDismiss: () => { d.notice = null; changed(); } }) : null);
  }

  function segmented(label, options, value, onPick) {
    return h(
      "div",
      { class: "field" },
      h("span", { class: "label" }, label),
      h("div", { class: "seg", role: "group", "aria-label": label },
        options.map(([key, text]) =>
          h("button", { type: "button", "aria-pressed": String(key === value), onclick: () => onPick(key) }, text))),
    );
  }

  function toggle(list, code) {
    return list.includes(code) ? list.filter((c) => c !== code) : [...list, code];
  }

  /** Every subtitle language the video has; tick one or more. */
  function subtitlePicker(running) {
    const tracks = d.video.subtitles || { manual: [], auto: [], translated: [] };
    const chip = (code, selected, onPick) =>
      h("button", { class: "chip", type: "button", disabled: running, "aria-pressed": String(selected), title: code, onclick: onPick },
        languageName(code), h("span", { class: "chip__code" }, code.replace(/-orig$/, "")));
    const group = (title, codes, selectedList, key, note) =>
      codes.length > 0 && h("div", { class: "subgroup" },
        h("span", { class: "hint" }, title),
        note && h("span", { class: "hint hint--warn" }, note),
        h("div", { class: "chips", role: "group", "aria-label": title },
          codes.map((code) => chip(code, selectedList.includes(code), () => { d[key] = toggle(d[key], code); changed(); }))));
    const any = tracks.manual.length || tracks.auto.length || tracks.translated.length;
    const picked = d.subManual.length + d.subAuto.length;
    return h("div", { class: "field subtitles" },
      h("span", { class: "label" }, `${t("sub_label")}${picked ? ` (${picked})` : ""}`),
      !any && h("span", { class: "hint" }, t("sub_none_at_all")),
      group(t("sub_group_manual"), tracks.manual, d.subManual, "subManual"),
      group(t("sub_group_auto"), tracks.auto, d.subAuto, "subAuto"),
      tracks.translated.length > 0 && h("details", { class: "subgroup", open: d.subOpen || null,
          ontoggle: (event) => { d.subOpen = event.target.open; } },
        h("summary", {}, `${t("sub_group_translated")} (${tracks.translated.length})`),
        h("span", { class: "hint hint--warn" }, t("sub_translated_warn")),
        h("div", { class: "chips", role: "group" },
          tracks.translated.map((code) => chip(code, d.subAuto.includes(code), () => { d.subAuto = toggle(d.subAuto, code); changed(); })))),
      picked > 0 && h("span", { class: "hint" }, t("sub_folder_hint")),
    );
  }

  function refreshOptions() {
    const job = d.jobId ? app.jobs[d.jobId] : null;
    const running = job?.state === "running";
    const key = [d.video?.id, d.fmt, d.quality, d.subManual, d.subAuto, running, d.subOpen].join("|");
    rebuild("options", optionsBox, key, () => {
      if (!d.video) return null;
      const set = (patch) => { if (!running) { Object.assign(d, patch); changed(); } };
      const mp4 = d.fmt === "mp4";
      return [
        segmented(t("format_label"), [["mp4", t("mp4_label")], ["mp3", t("mp3_label")]], d.fmt, (fmt) => set({ fmt })),
        mp4 && subtitlePicker(running),
        mp4 && h(
          "div",
          { class: "field options__quality" },
          h("span", { class: "label" }, t("quality_label")),
          d.video.qualities.length
            ? h("div", { class: "chips", role: "group", "aria-label": t("quality_label") },
                d.video.qualities.map((q) =>
                  h("button", { class: "chip", type: "button", "aria-pressed": String(q === d.quality), disabled: running, onclick: () => set({ quality: q }) }, q)))
            : h("span", { class: "hint" }, t("no_formats")),
        ),
      ];
    });
  }

  // The run area: Download button, or progress while running, or the result.
  const refs = {};
  function buildRun() {
    refs.button = h("button", { class: "btn btn--primary btn--big", type: "button", onclick: () => startDownload() }, icon("download", 22), t("download_btn"));
    refs.subsOnly = h("button", { class: "btn btn--small", type: "button", title: t("sub_only_hint"), onclick: () => startDownload(false, true) }, t("sub_only_btn"));
    refs.status = h("span", { class: "label" });
    refs.percent = h("span", { class: "run__percent" });
    refs.bar = h("div", { class: "progress__bar" });
    refs.stats = h("span", { class: "run__stats" });
    refs.cancel = h("button", { class: "btn", type: "button", onclick: cancelDownload }, t("cancel_btn"));
    refs.progress = h(
      "div", { class: "run" },
      h("div", { class: "run__line" }, refs.status, refs.percent),
      h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 }, refs.bar),
      h("div", { class: "run__bottom" }, refs.stats, refs.cancel),
    );
    refs.done = h("div", { class: "notice notice--ok", role: "status" });
    runBox.append(refs.button, refs.subsOnly, refs.progress, refs.done);
  }
  buildRun();

  function refreshRun() {
    const job = d.jobId ? app.jobs[d.jobId] : null;
    const running = job?.state === "running";
    const loaded = d.phase === "loaded" && d.video;
    runBox.hidden = !loaded;
    if (!loaded) return;

    refs.button.hidden = running;
    refs.subsOnly.hidden = running || d.fmt !== "mp4" || !(d.subManual.length || d.subAuto.length);
    refs.subsOnly.disabled = refs.button.disabled;
    refs.button.disabled = !!activeJob() || d.url.trim() !== d.fetchedUrl;
    refs.progress.hidden = !running;
    if (running) {
      const pct = Math.round(job.percent);
      refs.status.textContent = job.cancelling ? t("cancelled") : job.phase === "converting" ? t("converting") : t("downloading");
      refs.percent.textContent = `${pct}%`;
      refs.bar.style.width = `${job.percent}%`;
      refs.bar.toggleAttribute("data-empty", job.percent <= 0);
      refs.progress.querySelector(".progress").setAttribute("aria-valuenow", String(pct));
      refs.stats.textContent = [job.speed && `${t("speed")}: ${job.speed}`, job.eta && `${t("eta")}: ${job.eta}`].filter(Boolean).join("  ·  ");
      refs.cancel.disabled = job.cancelling;
    }
    const done = job?.state === "done";
    refs.done.hidden = !done;
    if (done) {
      refs.done.replaceChildren(...[
        h("div", { class: "notice__title" }, job.message === "subtitles_only" ? t("sub_only_done", { n: job.summary?.files?.length || 0 }) : t("completed")),
        h("p", { class: "muted" }, job.message === "subtitles_only" ? (job.summary?.files || []).join(", ") : job.filename),
        job.message === "subtitles_skipped" && h("p", {}, t("sub_skipped")),
        h("div", { class: "notice__actions" },
          h("button", { class: "btn btn--small", type: "button", onclick: () => api.openFolder(job.id).catch((e) => toast(e.message)) }, icon("folder", 18), t("s_open_dl_folder"))),
      ].filter(Boolean));
    }
  }

  function refresh() {
    const fetching = d.phase === "fetching";
    fetchBtn.disabled = fetching;
    fetchBtn.textContent = fetching ? t("fetching_btn") : t("fetch_btn");
    if (document.activeElement !== urlInput && urlInput.value !== d.url) urlInput.value = d.url;
    refreshInfo();
    refreshNotice();
    refreshOptions();
    refreshRun();
  }

  refresh();
  const stops = [on("download", refresh), on("job", refresh)];
  return () => stops.forEach((stop) => stop());
}
