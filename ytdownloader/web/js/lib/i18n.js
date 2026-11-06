// Translations: web/locales/<lang>.json, loaded once per language.

const RTL = new Set(["ar"]);
const cache = {};
let current = "en";

export async function loadLocale(lang) {
  if (!cache[lang]) {
    const response = await fetch(`/locales/${lang}.json`);
    cache[lang] = await response.json();
  }
}

/** Switches the active language and mirrors the page (lang and dir on <html>). */
export async function setLanguage(lang) {
  await loadLocale(lang);
  current = lang;
  document.documentElement.lang = lang;
  document.documentElement.dir = RTL.has(lang) ? "rtl" : "ltr";
}

export function getLanguage() {
  return current;
}

/** t("playlist_summary", {done: 2}) - {name} placeholders are replaced; unknown keys show the key. */
export function t(key, vars = {}) {
  const text = cache[current]?.[key] ?? cache.en?.[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
}
