// Turns an error code (E120...) into the texts shown to the user, and into a block of plain text
// the user can paste into a bug report. The texts live in locales/*.json as err_<code>_title,
// err_<code>_text and err_<code>_steps ("- " lines).

import { t } from "./i18n.js";
import { app } from "../state.js";

export function describeProblem(code, vars = {}) {
  const known = `err_${code}_title`;
  const c = t(known) === known ? "E900" : code;
  const steps = t(`err_${c}_steps`, vars);
  return {
    code: c,
    title: t(`err_${c}_title`, vars),
    text: t(`err_${c}_text`, vars),
    steps: steps.startsWith("err_") ? [] : steps.split("\n").map((line) => line.replace(/^- /, "")),
  };
}

/** Plain text for a bug report. It contains no link, file name or account name. */
export function diagnostics(code, detail) {
  const st = app.status || {};
  return [
    `Code: ${code}`,
    `Message: ${detail || "-"}`,
    `App: ${st.version || "?"} | yt-dlp: ${st.ytdlp_version || "?"} | ${st.platform || "?"}${st.frozen ? " (packaged)" : ""}`,
    `FFmpeg: ${st.ffmpeg ? "yes" : "no"} | JS runtime: ${(st.js_runtimes || []).join(",") || "none"}`,
  ].join("\n");
}
