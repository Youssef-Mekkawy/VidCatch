// Modal dialogs on top of the native <dialog> element (focus trap, Esc to close, backdrop for free).

import { append, h } from "../lib/dom.js";
import { t } from "../lib/i18n.js";

function open(dialog) {
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(dialog.returnValue);
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

/** Text with blank-line paragraphs and "- " bullet lines, as used by the translations. */
function renderText(text) {
  const box = h("div", { class: "dialog__text" });
  for (const block of text.split("\n\n")) {
    const lines = block.split("\n");
    const bullets = lines.filter((line) => line.startsWith("- "));
    if (bullets.length === lines.length) {
      append(box, h("ul", { class: "dialog__list" }, bullets.map((line) => h("li", {}, line.slice(2)))));
    } else {
      append(box, h("p", {}, block.replace(/\n/g, " ")));
    }
  }
  return box;
}

/** Ask a yes/no question. Resolves true for the OK button, false for Cancel / Esc. */
export async function confirmDialog({ title, text, okText, cancelText, danger = false, extra = null }) {
  const dialog = h("dialog", { class: "dialog", "aria-labelledby": "dlg-title" });
  const form = h(
    "form",
    { method: "dialog", class: "dialog__body" },
    h("h2", { class: "dialog__title", id: "dlg-title" }, title),
    renderText(text),
    h(
      "div",
      { class: "dialog__actions" },
      extra && h("button", { class: "btn btn--ghost", type: "button", onclick: extra.onClick }, extra.text),
      cancelText && h("button", { class: "btn", value: "cancel", type: "submit" }, cancelText),
      h("button", { class: `btn ${danger ? "btn--danger" : "btn--ink"}`, value: "ok", type: "submit", autofocus: true }, okText || t("dlg_ok")),
    ),
  );
  dialog.append(form);
  return (await open(dialog)) === "ok";
}

/** Offer several buttons. Resolves to the chosen button's `value`, or "cancel" for Esc / Cancel. */
export async function choiceDialog({ title, text, choices, cancelText }) {
  const dialog = h("dialog", { class: "dialog", "aria-labelledby": "dlg-title" });
  const buttons = choices.map((c) => h("button", { class: `btn ${c.danger ? "btn--danger" : ""}`, value: c.value, type: "submit" }, c.text));
  dialog.append(h("form", { method: "dialog", class: "dialog__body" },
    h("h2", { class: "dialog__title", id: "dlg-title" }, title),
    renderText(text),
    h("div", { class: "dialog__actions" }, h("button", { class: "btn btn--ghost", value: "cancel", type: "submit" }, cancelText), ...buttons)));
  return (await open(dialog)) || "cancel";
}

/** Let the user pick one of several signed-in browser profiles. Resolves to a source or null. */
export async function pickAccount(sources) {
  const dialog = h("dialog", { class: "dialog", "aria-labelledby": "dlg-title" });
  const items = sources.map((source, index) =>
    h(
      "label",
      { class: "radio-item" },
      h("input", { type: "radio", name: "account", value: String(index), checked: index === 0 }),
      h("span", {}, source.title),
    ),
  );
  const form = h(
    "form",
    { method: "dialog", class: "dialog__body" },
    h("h2", { class: "dialog__title", id: "dlg-title" }, t("signin_picker_title")),
    h("p", {}, t("signin_picker_text")),
    h("div", { class: "radio-list" }, items),
    h(
      "div",
      { class: "dialog__actions" },
      h("button", { class: "btn", value: "cancel", type: "submit" }, t("signin_picker_cancel")),
      h("button", { class: "btn btn--ink", value: "ok", type: "submit" }, t("signin_picker_ok")),
    ),
  );
  dialog.append(form);
  const result = await open(dialog);
  if (result !== "ok") return null;
  const checked = form.querySelector("input[name=account]:checked");
  return checked ? sources[Number(checked.value)] : null;
}
