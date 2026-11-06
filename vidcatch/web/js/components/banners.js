// Strips under the header: yt-dlp update, FFmpeg missing, no JavaScript runtime.

import { api } from "../lib/api.js";
import { h } from "../lib/dom.js";
import { t } from "../lib/i18n.js";
import { app, emit, on, toast } from "../state.js";

const b = { update: null, dismissedJs: false, ffmpegMsg: "", updateMsg: "", restarting: false };

export async function checkUpdates() {
  if (!app.settings.check_updates) return;
  try {
    const info = await api.checkUpdates();
    if (info.newer) b.update = info;
    emit("banners");
  } catch (error) {
    /* offline: no banner */
  }
}

export async function restartApp() {
  b.restarting = true;
  try { await api.restart(); } catch (error) { toast(error.message); }
}

function ffmpegText(job) {
  if (job.state === "running") return t("update_running");
  if (job.state === "done") return t("ffmpeg_ok");
  return job.message === "none" ? t("ffmpeg_no_manager") : t("ffmpeg_manual");
}

function banner(text, actions = [], danger = false) {
  return h("div", { class: `banner${danger ? " banner--danger" : ""}` },
    h("div", { class: "banner__inner" }, h("span", { class: "banner__text" }, text), h("div", { class: "banner__actions" }, actions)));
}

export function mountBanners(outlet) {
  function render() {
    const parts = [];
    const jobs = Object.values(app.jobs);
    const upd = jobs.filter((j) => j.kind === "update").pop();
    const ff = jobs.filter((j) => j.kind === "ffmpeg").pop();
    const busy = jobs.some((j) => j.state === "running");

    if (upd?.state === "running") parts.push(banner(t("update_running")));
    else if (upd?.state === "done") {
      parts.push(banner(t("update_done", { latest: b.update?.latest || "" }), [
        h("button", { class: "btn btn--ink btn--small", type: "button", disabled: b.restarting, onclick: restartApp }, t("update_restart"))]));
    } else if (upd?.state === "failed") {
      parts.push(banner(t("update_failed", { error: upd.message || "" }), [], true));
    } else if (b.update) {
      parts.push(app.status.frozen || !b.update.can_self_update
        ? banner(t("update_manual", { latest: b.update.latest }))
        : banner(t("update_available", { latest: b.update.latest, current: b.update.installed }), [
            h("button", { class: "btn btn--ink btn--small", type: "button", disabled: busy,
              onclick: () => api.installUpdate().catch((e) => toast(e.status === 409 ? t("task_running") : e.message)) }, t("update_btn"))]));
    }

    if (ff?.state === "running" || (ff && ff.state !== "done")) parts.push(banner(ffmpegText(ff), [], ff.state === "failed"));
    else if (ff?.state === "done") parts.push(banner(ffmpegText(ff)));
    else if (!app.status.ffmpeg) {
      const key = { windows: "ffmpeg_install_win", mac: "ffmpeg_install_mac" }[app.status.platform] || "ffmpeg_install_linux";
      parts.push(banner(t(key), [
        h("button", { class: "btn btn--ink btn--small", type: "button", disabled: busy,
          onclick: () => api.installFfmpeg().catch((e) => toast(e.status === 409 ? t("task_running") : e.message)) }, t("ffmpeg_title"))], true));
    }

    if (!app.status.js_runtimes?.length && !b.dismissedJs) {
      parts.push(banner(t("js_banner"), [
        h("button", { class: "btn btn--small", type: "button", onclick: () => { b.dismissedJs = true; render(); } }, t("js_dismiss"))]));
    }
    outlet.replaceChildren(...parts);
  }
  render();
  const stops = [on("job", render), on("banners", render)];
  return () => stops.forEach((s) => s());
}
