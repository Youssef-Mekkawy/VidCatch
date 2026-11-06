// Live updates from the server (Server-Sent Events).
//
// Keeping this stream open also tells the server the page is still there: it exits on its own a
// little after the last page closes. If the stream drops and does not come back, the server is gone.

const GONE_AFTER_MS = 6000;

export function connectEvents({ onEvent, onGone, onBack }) {
  let source = null;
  let goneTimer = null;

  function open() {
    source = new EventSource("/api/events");
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data));
      } catch (error) {
        console.error("Bad event", error);
      }
    };
    source.onopen = () => {
      if (goneTimer) {
        clearTimeout(goneTimer);
        goneTimer = null;
        onBack?.();
      }
    };
    source.onerror = () => {
      if (!goneTimer) goneTimer = setTimeout(onGone, GONE_AFTER_MS);
    };
  }

  open();
  return () => source?.close();
}
