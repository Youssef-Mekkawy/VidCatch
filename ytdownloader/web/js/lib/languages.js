// Display names for subtitle language codes ("ar" -> "Arabic" / "العربية").

export function languageName(code) {
  try {
    const base = code.replace(/-orig$/, "");
    return new Intl.DisplayNames([document.documentElement.lang || "en"], { type: "language" }).of(base) || base;
  } catch (error) {
    return code;
  }
}
