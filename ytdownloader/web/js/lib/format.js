// Small formatters for values the server sends as numbers.

export function formatBytes(size) {
  if (!size || size <= 0) return "";
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${(size / 1024 ** 3).toFixed(2)} GB`;
}

/** True for youtube.com / youtu.be links (used to fetch automatically after a paste). */
export function isYoutubeLink(text) {
  return /(^|\/\/|\.)(youtube\.com|youtu\.be)\b/i.test(text.trim());
}
