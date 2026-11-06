// Tiny DOM helpers. Everything the user or YouTube controls (titles, channel names, file names)
// goes in through text nodes, never as HTML markup, so a hostile video title cannot inject markup.

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * h("button", {class: "btn", onclick: fn, "aria-label": "x"}, "Text", otherNode)
 * Props: class, text, on* handlers, boolean props (disabled, hidden, checked...), data-*, aria-*.
 * Children: strings, numbers, nodes, arrays of those; null/false/undefined are skipped.
 */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "value" || key === "checked" || key === "disabled" || key === "hidden") {
      el[key] = value;
    } else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

export function append(parent, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

// ── Icons: simple stroked shapes, no icon font ─────────────────────────────
const ICONS = {
  download: { d: ["M12 3v12", "M7 11l5 5 5-5", "M4 20h16"], stroke: 2.4 },
  check: { d: ["M5 12.5l4.5 4.5L19 7"], stroke: 2.6 },
  close: { d: ["M6 6l12 12", "M18 6L6 18"], stroke: 2.4 },
  folder: { d: ["M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"], stroke: 2 },
  back: { d: ["M15 5l-7 7 7 7"], stroke: 2.4, flip: true },
  clock: { d: ["M12 7v5l3 2", "M12 3a9 9 0 100 18 9 9 0 000-18z"], stroke: 2 },
  pause: { d: ["M8 5v14", "M16 5v14"], stroke: 2.8 },
  trash: { d: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"], stroke: 2 },
};

export function icon(name, size = 20) {
  const spec = ICONS[name];
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", spec.stroke);
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  if (spec.flip) svg.setAttribute("class", "icon--flip");
  for (const d of spec.d) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

/** The logo mark: a rounded square with a download arrow. */
export function logo(size = 34) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 34 34");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS(SVG_NS, "rect");
  for (const [k, v] of Object.entries({ x: 1, y: 1, width: 32, height: 32, rx: 9, stroke: "#15171a", "stroke-width": 2 })) {
    rect.setAttribute(k, v);
  }
  rect.setAttribute("fill", "var(--accent)");
  const arrow = document.createElementNS(SVG_NS, "path");
  arrow.setAttribute("d", "M17 8v13M11 16l6 6 6-6M10 26h14");
  for (const [k, v] of Object.entries({ fill: "none", stroke: "#fff", "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round" })) {
    arrow.setAttribute(k, v);
  }
  svg.append(rect, arrow);
  return svg;
}

export function playMark() {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "thumb__play");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", "M7 4l13 8-13 8z");
  svg.append(path);
  return svg;
}
