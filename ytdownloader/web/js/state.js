// Shared application state. Views are rebuilt when the language changes, so anything the user
// would be annoyed to lose (the pasted link, the loaded video, the playlist selection) lives here
// and not in the DOM.

export const app = {
  status: null, // GET /api/status
  settings: null, // GET /api/settings
  jobs: {}, // job id -> latest job object from the server
  historyCount: 0,
  queueCount: 0, // videos in the queue that are not finished

  // Download page
  download: {
    url: "", // text in the input
    fetchedUrl: "", // the link the loaded video belongs to
    phase: "empty", // empty | fetching | loaded
    video: null,
    source: null, // browser profile used for sign-in, if any
    notice: null, // {kind, reason, detail, message} shown as a red box
    fmt: "mp4",
    quality: "",
    subs: "none",
    jobId: null,
    status: "", // short status line ("Cancelled"...)
  },

  // Playlist page (null until a playlist link was fetched)
  playlist: null, // {title, channel, entries, checked[], fmt, quality, url}
};

const bus = new EventTarget();

/** emit("job", job) / on("job", (job) => ...). Returns an unsubscribe function. */
export function emit(name, detail) {
  bus.dispatchEvent(new CustomEvent(name, { detail }));
}
export function on(name, handler) {
  const wrapped = (event) => handler(event.detail);
  bus.addEventListener(name, wrapped);
  return () => bus.removeEventListener(name, wrapped);
}

const EXCLUSIVE = new Set(["video", "playlist", "ffmpeg", "update"]);

/** The download, playlist, FFmpeg or update job that is running right now, if any. */
export function activeJob() {
  return Object.values(app.jobs).find((job) => EXCLUSIVE.has(job.kind) && job.state === "running") || null;
}

export function toast(text) {
  emit("toast", text);
}

export async function saveSettings(patch) {
  const { api } = await import("./lib/api.js");
  app.settings = await api.saveSettings(patch);
  emit("settings", app.settings);
  return app.settings;
}
