// One place for every call to the local server. See docs/API.md for the routes.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

// Called when the session is gone (401) or the server cannot be reached at all.
const listeners = { unauthorized: () => {}, unreachable: () => {} };
export function onConnectionProblem(handlers) {
  Object.assign(listeners, handlers);
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    listeners.unreachable();
    throw new ApiError(0, "The app is not running.");
  }
  if (response.status === 401) {
    listeners.unauthorized();
    throw new ApiError(401, "Open the app from its launcher.");
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const detail = data?.detail;
    // FastAPI sends a list for validation errors; show the first message.
    const text = Array.isArray(detail) ? detail[0]?.msg : detail;
    throw new ApiError(response.status, text);
  }
  return data;
}

export const api = {
  status: () => request("GET", "/status"),
  settings: () => request("GET", "/settings"),
  saveSettings: (patch) => request("PUT", "/settings", patch),

  fetchInfo: (url, sources) => request("POST", "/fetch", sources ? { url, sources } : { url }),
  signinPrepare: (firstTime) => request("POST", "/signin/prepare", { first_time: firstTime }),
  signinAccounts: () => request("GET", "/signin/accounts"),
  signinTest: () => request("POST", "/signin/test"),

  startDownload: (body) => request("POST", "/downloads", body),
  startPlaylist: (body) => request("POST", "/playlists/download", body),
  cancelJob: (id) => request("POST", `/jobs/${id}/cancel`),

  history: () => request("GET", "/history"),
  removeHistory: (id, deleteFile = false) => request("DELETE", `/history/${id}${deleteFile ? "?delete_file=true" : ""}`),
  pruneHistory: () => request("POST", "/history/prune"),
  clearHistory: (deleteFiles = false) => request("DELETE", `/history${deleteFiles ? "?delete_files=true" : ""}`),
  revealHistory: (id) => request("POST", `/history/${id}/reveal`),

  openFolder: (jobId) => request("POST", jobId ? `/folder/open?job_id=${encodeURIComponent(jobId)}` : "/folder/open"),
  pickFolder: () => request("POST", "/folder/pick"),

  installFfmpeg: () => request("POST", "/ffmpeg/install"),
  checkUpdates: () => request("GET", "/updates"),
  installUpdate: () => request("POST", "/updates/install"),
  restart: () => request("POST", "/restart"),
};
