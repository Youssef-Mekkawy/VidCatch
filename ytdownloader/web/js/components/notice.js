// The red box: why something failed, what the user can do about it, and the error code.

import { h } from "../lib/dom.js";
import { t } from "../lib/i18n.js";
import { describeProblem, diagnostics } from "../lib/problems.js";
import { toast } from "../state.js";

export function reasonText(reason) {
  const keys = { bot: "reason_bot", age: "reason_age", private: "reason_private", members: "reason_members" };
  return t(keys[reason] || "reason_login");
}

// notice.kind (from the sign-in flow) -> error code. Plain errors carry their own `code`.
const KIND_CODES = {
  never: "E119", no_account: "E119", locked: "E115", encrypted: "E116", not_signed_in: "E117",
  none_installed: "E118", rejected: "E114", offline: "E100", other: "E900",
};
const SETTINGS_CODES = new Set(["E110", "E111", "E112", "E113", "E114", "E115", "E116", "E117", "E118", "E119"]);

/** notice: {kind, code?, reason?, detail?, message?} */
export function renderNotice(notice, { onDismiss } = {}) {
  const code = KIND_CODES[notice.kind] || notice.code || "E900";
  const problem = describeProblem(code, { reason: reasonText(notice.reason) });
  const detail = notice.detail || notice.message || "";
  const text = problem.code === "E900" && detail && notice.kind === "other" ? t("notice_other", { error: detail }) : problem.text;
  const showDetail = detail && detail !== problem.text;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(diagnostics(problem.code, detail));
      toast(t("err_copied"));
    } catch (error) {
      toast(diagnostics(problem.code, detail));
    }
  };

  return h(
    "div", { class: "notice", role: "alert" },
    h("div", { class: "notice__head" },
      h("div", { class: "notice__title" }, problem.title),
      h("span", { class: "notice__code", title: t("err_code") }, problem.code)),
    h("p", {}, text),
    problem.steps.length > 0 && h("div", { class: "notice__steps" },
      h("div", { class: "label" }, t("err_steps")),
      h("ol", {}, problem.steps.map((step) => h("li", {}, step)))),
    showDetail && h("details", { class: "notice__detail" }, h("summary", {}, t("err_details")), h("pre", {}, detail)),
    h("div", { class: "notice__actions" },
      SETTINGS_CODES.has(problem.code) && h("a", { class: "btn btn--ink btn--small", href: "#/settings" }, t("notice_open_settings")),
      h("button", { class: "btn btn--small", type: "button", onclick: copy }, t("err_copy")),
      onDismiss && h("button", { class: "btn btn--small", type: "button", onclick: onDismiss }, t("js_dismiss"))),
  );
}
