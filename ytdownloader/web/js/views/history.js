// History page: past downloads, newest first.

import { api } from "../lib/api.js";
import { formatBytes } from "../lib/format.js";
import { h, icon } from "../lib/dom.js";
import { t } from "../lib/i18n.js";
import { app, on, toast, emit } from "../state.js";
import { choiceDialog } from "../components/dialog.js";

export async function refreshHistoryCount() {
  try {
    const data = await api.history();
    app.historyCount = data.entries.length;
    emit("history_count");
    return data;
  } catch (error) {
    return { entries: [], total_bytes: 0, existing: 0 };
  }
}

function dateText(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value || "") : d.toLocaleString(document.documentElement.lang);
}

export function mountHistory(outlet) {
  const clearBtn = h("button", { class: "btn btn--danger btn--small", type: "button", onclick: clearAll }, icon("trash", 18), t("history_clear"));
  const pruneBtn = h("button", { class: "btn btn--small", type: "button", hidden: true, onclick: prune }, icon("trash", 18), t("history_prune"));
  const summary = h("p", { class: "muted history__summary", hidden: true });
  const body = h("div", {});
  outlet.replaceChildren(
    h("section", { class: "page" },
      h("div", { class: "page__head" }, h("h1", { class: "page__title" }, t("history_title")), h("div", { class: "row" }, pruneBtn, clearBtn)),
      summary, body),
  );

  async function clearAll() {
    const choice = await choiceDialog({
      title: t("history_title"),
      text: t("history_confirm_clear"),
      cancelText: t("dlg_cancel"),
      choices: [
        { value: "list", text: t("history_clear_list") },
        { value: "file", text: t("history_clear_files"), danger: true },
      ],
    });
    if (choice === "cancel") return;
    await api.clearHistory(choice === "file").catch((e) => toast(e.message));
    load();
  }

  async function remove(entry) {
    let deleteFile = false;
    if (entry.exists) {
      const choice = await choiceDialog({
        title: t("history_delete"),
        text: t("history_remove_text", { title: entry.title || entry.filename }),
        cancelText: t("dlg_cancel"),
        choices: [
          { value: "list", text: t("history_remove_list") },
          { value: "file", text: t("history_remove_file"), danger: true },
        ],
      });
      if (choice === "cancel") return;
      deleteFile = choice === "file";
    }
    await api.removeHistory(entry.id, deleteFile).catch((e) => toast(e.message));
    load();
  }

  async function prune() {
    const { removed } = await api.pruneHistory().catch((e) => { toast(e.message); return { removed: 0 }; });
    toast(t("history_pruned", { count: removed }));
    load();
  }

  function item(entry) {
    const thumb = h("div", { class: "thumb thumb--small" });
    if (entry.thumbnail) {
      const img = h("img", { src: entry.thumbnail, alt: "", loading: "lazy", width: 128, height: 72 });
      img.addEventListener("error", () => img.remove());
      thumb.append(img);
    }
    return h("div", { class: "item" }, thumb,
      h("div", { class: "item__text" },
        h("div", { class: "item__title" }, entry.title || entry.filename),
        h("div", { class: "item__sub" },
          [entry.channel, dateText(entry.date)].filter(Boolean).join(" · ")),
        h("div", {}, h("span", { class: "format" }, [String(entry.format || "").toUpperCase(), entry.quality, formatBytes(entry.size)].filter(Boolean).join(" · ")),
          !entry.exists && h("span", { class: "hint" }, ` ${t("history_file_gone")}`))),
      h("div", { class: "item__actions" },
        h("button", { class: "btn btn--small", type: "button", disabled: !entry.exists, onclick: () => api.revealHistory(entry.id).catch((e) => toast(e.message)) }, icon("folder", 18), t("history_open_file")),
        h("button", { class: "btn btn--ghost btn--small", type: "button", onclick: () => remove(entry) }, icon("close", 18), t("history_delete"))));
  }

  async function load() {
    const { entries, total_bytes: total, existing } = await refreshHistoryCount();
    summary.textContent = existing ? t("history_summary", { n: existing, size: formatBytes(total) }) : "";
    summary.hidden = !existing;
    clearBtn.hidden = !entries.length;
    const missing = entries.filter((e) => !e.exists).length;
    pruneBtn.hidden = !missing;
    body.replaceChildren(entries.length
      ? h("div", { class: "card list" }, entries.map(item))
      : h("div", { class: "empty" }, h("strong", {}, t("history_empty")), h("span", { class: "muted" }, t("history_empty_hint"))));
  }
  load();
  // The user may delete files in the file explorer: re-check when they come back to this tab.
  const onFocus = () => { if (!document.hidden) load(); };
  window.addEventListener("focus", onFocus);
  document.addEventListener("visibilitychange", onFocus);
  const timer = setInterval(onFocus, 10000);
  const stop = on("history_changed", load);
  return () => {
    stop();
    clearInterval(timer);
    window.removeEventListener("focus", onFocus);
    document.removeEventListener("visibilitychange", onFocus);
  };
}
