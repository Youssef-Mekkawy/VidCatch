// The sign-in flow. It only starts after YouTube refused a video (needs_login), and it only ever
// uses the user's browser session after they said yes. The server decides what happens next from
// the saved consent (see docs/API.md, "Sign-in flow"); this file turns its answers into dialogs.

import { api } from "./lib/api.js";
import { t } from "./lib/i18n.js";
import { app, saveSettings } from "./state.js";
import { confirmDialog, pickAccount } from "./components/dialog.js";
import { reasonText } from "./components/notice.js";

/**
 * Resolves to {sources} when the app may retry with a browser profile, or {notice} describing
 * why it cannot (the caller shows it in the red box).
 */
export async function runSigninFlow(reason) {
  let prepared = await api.signinPrepare(false);

  if (prepared.status === "consent_needed") {
    const allowed = await confirmDialog({
      title: t("signin_title"),
      text: t("signin_prompt", { reason: reasonText(reason) }),
      okText: t("signin_allow"),
      cancelText: t("signin_no"),
    });
    await saveSettings({ signin_consent: allowed ? "allow" : "never" });
    if (!allowed) return { notice: { kind: "never", reason } };
    prepared = await api.signinPrepare(true);
  }

  switch (prepared.status) {
    case "signin_off":
      return { notice: { kind: "never", reason } };
    case "signin_failed":
      return { notice: { kind: prepared.kind, reason, detail: prepared.detail } };
    case "choose_account": {
      const chosen = await pickAccount(prepared.sources);
      if (!chosen) return { notice: { kind: "no_account", reason } };
      await saveSettings({ cookies_browser: chosen.browser, cookies_profile: chosen.profile });
      return { sources: [chosen] };
    }
    default:
      return { sources: prepared.sources };
  }
}

export function signedInAs(source) {
  return source ? t("signin_ok", { name: source.title }) : "";
}

export function signinEnabled() {
  return app.settings?.signin_consent === "allow";
}
