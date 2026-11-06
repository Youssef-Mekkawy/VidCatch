// Settings page. Every control saves immediately (PUT /api/settings); there is no Save button.

import { api } from "../lib/api.js";
import { h, icon } from "../lib/dom.js";
import { t, setLanguage } from "../lib/i18n.js";
import { app, emit, saveSettings, toast } from "../state.js";

const QUALITIES = ["best", "1080p", "720p", "480p", "360p"];
const BROWSER_NAMES = { firefox: "Firefox", edge: "Edge", chrome: "Chrome", brave: "Brave", vivaldi: "Vivaldi", opera: "Opera", chromium: "Chromium", safari: "Safari" };

function card(title, ...children) {
  return h("div", { class: "card card--hard" }, h("h2", { class: "card__title" }, title), ...children);
}

function switchRow(label, key, hint) {
  const sw = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(!!app.settings[key]), "aria-label": label });
  sw.addEventListener("click", async () => {
    const next = sw.getAttribute("aria-checked") !== "true";
    sw.setAttribute("aria-checked", String(next));
    await saveSettings({ [key]: next }).catch((e) => { sw.setAttribute("aria-checked", String(!next)); toast(e.message); });
  });
  return h("div", { class: "switch-row" }, h("div", {}, h("div", {}, label), hint && h("div", { class: "hint" }, hint)), sw);
}

function seg(options, value, onPick) {
  return h("div", { class: "seg", role: "group" },
    options.map(([key, text]) => h("button", { type: "button", "aria-pressed": String(key === value), onclick: () => onPick(key) }, text)));
}

export function mountSettings(outlet) {
  const s = app.settings;

  // Download folder
  const folder = h("input", { class: "input input--ltr input--path", type: "text", value: s.download_dir, "aria-label": t("s_folder") });
  folder.addEventListener("change", () =>
    saveSettings({ download_dir: folder.value.trim() }).catch((e) => { toast(e.message); folder.value = app.settings.download_dir; }));
  const browse = h("button", { class: "btn", type: "button", onclick: async () => {
    try {
      const r = await api.pickFolder();
      if (r.status === "ok") { folder.value = r.path; await saveSettings({ download_dir: r.path }); }
    } catch (e) { toast(e.message); }
  } }, icon("folder", 18), t("s_browse"));

  const downloadCard = card(t("section_download"),
    h("div", { class: "field" }, h("label", { class: "label" }, t("s_folder")), h("div", { class: "row" }, folder, browse)),
    h("button", { class: "btn btn--small", type: "button", onclick: () => api.openFolder().catch((e) => toast(e.message)) }, t("s_open_dl_folder")),
    h("div", { class: "field" }, h("span", { class: "label" }, t("s_default_format")),
      seg([["mp4", t("mp4_label")], ["mp3", t("mp3_label")]], s.default_format, async (v) => { await saveSettings({ default_format: v }); mountSettings(outlet); })),
    h("div", { class: "field" }, h("span", { class: "label" }, t("s_default_quality")),
      h("div", { class: "chips", role: "group" },
        QUALITIES.map((q) => h("button", { class: "chip", type: "button", "aria-pressed": String(q === s.default_quality),
          onclick: async () => { await saveSettings({ default_quality: q }); mountSettings(outlet); } }, q === "best" ? t("s_quality_best") : q)))));

  const behaviorCard = card(t("section_behavior"),
    switchRow(t("s_ask_overwrite"), "ask_overwrite"), switchRow(t("s_auto_download"), "auto_download"),
    switchRow(t("s_open_folder"), "open_folder"), switchRow(t("s_clear_url"), "clear_url"),
    switchRow(t("s_check_updates"), "check_updates"));

  // Sign-in
  const consent = s.signin_consent;
  const signinSwitch = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(consent === "allow"), "aria-label": t("s_signin_switch") });
  signinSwitch.addEventListener("click", async () => {
    await saveSettings({ signin_consent: signinSwitch.getAttribute("aria-checked") === "true" ? "never" : "allow" }).catch((e) => toast(e.message));
    mountSettings(outlet);
  });
  const testOut = h("ul", { class: "account-list", "aria-live": "polite" });
  // One list with every browser profile found on this computer.
  const accountSel = h("select", { class: "select", "aria-label": t("s_signin_account") });
  let accounts = [];
  const encode = (browser, profile) => `${browser}\u0000${profile}`;

  function fillAccounts() {
    accountSel.replaceChildren(
      h("option", { value: "" }, t("s_signin_account_auto")),
      ...accounts.map((b) =>
        h("optgroup", { label: BROWSER_NAMES[b.id] || b.id },
          b.profiles.map((p) => h("option", { value: encode(b.id, p.profile) },
            `${BROWSER_NAMES[b.id] || b.id}${p.label && p.label !== b.id ? ` — ${p.label}` : ""}`)))),
    );
    const { cookies_browser: browser, cookies_profile: profile } = app.settings;
    accountSel.value = browser ? encode(browser, profile) : "";
    if (browser && accountSel.value !== encode(browser, profile)) accountSel.value = "";
  }
  accountSel.addEventListener("change", async () => {
    const [browser = "", profile = ""] = accountSel.value.split("\u0000");
    await saveSettings({ cookies_browser: browser, cookies_profile: profile }).catch((e) => toast(e.message));
  });
  api.signinAccounts().then((r) => { accounts = r.browsers; fillAccounts(); }).catch(() => {});

  const testBtn = h("button", { class: "btn btn--small", type: "button" }, t("s_signin_test"));
  testBtn.addEventListener("click", async () => {
    testBtn.disabled = true; testBtn.textContent = t("s_signin_testing");
    try {
      const r = await api.signinTest();
      const keys = { "": "test_ok", locked: "test_locked", encrypted: "test_encrypted", not_signed_in: "test_not_signed_in", other: "test_other" };
      testOut.replaceChildren(...(r.installed ? r.results.map((x) =>
        h("li", {}, h("strong", {}, `${x.title}: `), x.signed_in ? t("test_ok") : t(keys[x.kind] || "test_other", { error: x.error || "" }))) : [h("li", {}, t("test_none"))]));
    } catch (e) { toast(e.message); }
    testBtn.disabled = false; testBtn.textContent = t("s_signin_test");
  });

  const signinCard = card(t("section_signin"),
    h("div", { class: "switch-row" }, h("div", {}, t("s_signin_switch")), signinSwitch),
    consent === "ask" && h("p", { class: "hint" }, t("s_signin_undecided")),
    h("div", { class: "field" }, h("label", { class: "label" }, t("s_signin_account")), accountSel),
    testBtn, testOut,
    h("p", { class: "hint" }, t("s_signin_privacy")));

  const languageCard = card(t("section_language"),
    seg([["en", "English"], ["ar", "العربية"]], s.language, async (lang) => {
      if (lang === app.settings.language) return;
      await saveSettings({ language: lang });
      await setLanguage(lang);
      emit("language");
    }));

  const st = app.status;
  const aboutCard = h("div", { class: "card card--ink about" },
    h("div", { class: "about__name" }, t("title"), h("span", { class: "tag" }, t("version_fmt", { version: st.version }))),
    h("p", {}, t("s_about_desc")),
    h("p", { class: "muted" }, t("s_about_powered")),
    h("dl", { class: "kv" },
      h("dt", {}, "yt-dlp"), h("dd", {}, st.ytdlp_version),
      h("dt", {}, "FFmpeg"), h("dd", {}, st.ffmpeg ? "✓" : "—"),
      h("dt", {}, "Data"), h("dd", {}, st.data_dir)));

  function render() {
    outlet.replaceChildren(h("section", { class: "page" },
      h("h1", { class: "page__title" }, t("settings_title")),
      h("div", { class: "settings" },
        h("div", { class: "settings__col" }, downloadCard, behaviorCard),
        h("div", { class: "settings__col" }, signinCard, languageCard, aboutCard))));
  }
  render();
  return () => {};
}
