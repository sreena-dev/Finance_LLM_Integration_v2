// test/render.jsx
import { renderToString } from "react-dom/server";

// src/components/chat/AnswerCard.jsx
import { useState as useState3 } from "react";
import { AnimatePresence as AnimatePresence2, motion as motion3 } from "framer-motion";

// src/components/common/Icon.jsx
import { jsx } from "react/jsx-runtime";
var PATHS = {
  ledger: "M4 4.5A1.5 1.5 0 0 1 5.5 3H16l4 4v12.5A1.5 1.5 0 0 1 18.5 21h-13A1.5 1.5 0 0 1 4 19.5zM15 3v5h5M8 12h8M8 16h5",
  seal: "M12 3l2.2 1.6 2.7-.3 1 2.5 2.3 1.4-.7 2.6.7 2.6-2.3 1.4-1 2.5-2.7-.3L12 18.6 9.8 17l-2.7.3-1-2.5L3.8 13.4l.7-2.6-.7-2.6 2.3-1.4 1-2.5 2.7.3zM9.5 11.2l1.8 1.8 3.4-3.4",
  scales: "M12 4v16M7 20h10M5 8h14M5 8l-2.5 6a3 3 0 0 0 5 0zM19 8l2.5 6a3 3 0 0 1-5 0zM12 4.5a1 1 0 1 0 0-.1",
  pulse: "M3 12h3.5L9 5.5l3.5 13L15.5 12H21",
  send: "M4.5 12l15.5-7.5-4 15.5-3.9-5.4zM12.1 14.6L20 4.5",
  copy: "M9 9V5.5A1.5 1.5 0 0 1 10.5 4h8A1.5 1.5 0 0 1 20 5.5v8a1.5 1.5 0 0 1-1.5 1.5H15M4 10.5A1.5 1.5 0 0 1 5.5 9h8a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 18.5z",
  check: "M4.5 12.5l5 5 10-11",
  chevron: "M6 9l6 6 6-6",
  alert: "M12 8.5v5M12 17h.01M10.3 3.9 2.6 17.4a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  info: "M12 16v-5M12 8h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
  refresh: "M20 11a8 8 0 1 0-.6 4M20 4v7h-7",
  doc: "M6 3h8l5 5v13H6zM14 3v5h5M9 13h7M9 17h7",
  search: "M20 20l-4-4M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0z",
  sparkle: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  download: "M12 3v12M7.5 10.5L12 15l4.5-4.5M4 20h16",
  upload: "M12 21V9M7.5 13.5L12 9l4.5 4.5M4 4h16",
  trash: "M5 7h14M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7M7 7l1 13a1.5 1.5 0 0 0 1.5 1.4h5a1.5 1.5 0 0 0 1.5-1.4l1-13",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  scale: "M12 4v16M7 20h10M5 8h14M5 8l-2.5 6a3 3 0 0 0 5 0zM19 8l2.5 6a3 3 0 0 1-5 0z",
  shield: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z",
  // Added for the sign-in / chat-history UI. Additive only — every
  // existing key and path is untouched.
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5a7.5 7.5 0 0 1 15 0",
  logout: "M15 17l5-5-5-5M20 12H9M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h6",
  plus: "M12 5v14M5 12h14",
  // Added for the document pane's "view the processed pages" action on a
  // document chip. Additive only — every existing key and path is untouched.
  eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"
};
function Icon({ name, size = 20, className = "", strokeWidth = 1.6 }) {
  const d = PATHS[name];
  if (!d) return null;
  return /* @__PURE__ */ jsx(
    "svg",
    {
      className,
      width: size,
      height: size,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth,
      strokeLinecap: "round",
      strokeLinejoin: "round",
      "aria-hidden": "true",
      children: /* @__PURE__ */ jsx("path", { d })
    }
  );
}

// src/components/common/Markdown.jsx
import { Children, isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// src/lib/figures.js
var UNREADABLE_RE = /^\s*\[\s*unreadable\b[^\]]*\]\s*$/i;
var RECOVERED_RE = /^\s*\[\s*recovered\s+([^;\]]*?)\s*;[^\]]*\]\s*$/i;
var USER_RE = /^\s*(.*?)\s*\[user-entered\]\s*$/i;
function parseFigureCell(text) {
  const raw = typeof text === "string" ? text : "";
  if (UNREADABLE_RE.test(raw)) return { kind: "unreadable", value: "" };
  const rec = RECOVERED_RE.exec(raw);
  if (rec) return { kind: "recovered", value: rec[1].trim() };
  const user = USER_RE.exec(raw);
  if (user) return { kind: "you", value: user[1].trim() };
  return { kind: "plain", value: raw };
}
var FIGURE_LABELS = {
  unreadable: "unreadable",
  recovered: "recovered ?",
  you: "you"
};
function looksNumeric(text) {
  const t = (text || "").trim();
  return /^[(\-]?\s*(?:₹|rs\.?)?\s*\d[\d,]*(?:\.\d+)?\s*\)?%?$/i.test(t);
}
var TAGS = {
  FINDING: "finding",
  "RISK FLAG": "risk",
  "AUDIT POINTER": "pointer",
  "COVERAGE NOTE": "coverage"
};

// src/components/common/FigureCell.jsx
import { Fragment, jsx as jsx2, jsxs } from "react/jsx-runtime";
function FigureCell({ text }) {
  const { kind, value } = parseFigureCell(text);
  if (kind === "plain") return /* @__PURE__ */ jsx2(Fragment, { children: text });
  return /* @__PURE__ */ jsxs("span", { className: `fig fig--${kind}`, title: kind === "unreadable" ? "Withheld: the scan could not be read" : kind === "recovered" ? "A second read of the scan; not confirmed by the arithmetic" : "Entered by a person from the scan", children: [
    /* @__PURE__ */ jsx2("span", { className: "fig__label", children: FIGURE_LABELS[kind] }),
    value && /* @__PURE__ */ jsx2("span", { children: value })
  ] });
}

// src/components/common/Markdown.jsx
import { jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
function plainText(children) {
  const parts = Children.toArray(children);
  if (parts.length === 0) return "";
  return parts.every((p) => typeof p === "string" || typeof p === "number") ? parts.join("") : null;
}
function Markdown({ children, className = "" }) {
  if (!children) return null;
  return /* @__PURE__ */ jsx3("div", { className: `md ${className}`, children: /* @__PURE__ */ jsx3(
    ReactMarkdown,
    {
      remarkPlugins: [remarkGfm],
      components: {
        table: ({ node, ...props }) => /* @__PURE__ */ jsx3("div", { className: "md__table-wrap", children: /* @__PURE__ */ jsx3("table", { ...props }) }),
        tr: ({ node, children: rowChildren, ...props }) => {
          const first = Children.toArray(rowChildren).find((c) => isValidElement(c));
          const label = first ? plainText(first.props.children) : null;
          const total = label && /^\s*(total|totai)\b/i.test(label);
          return /* @__PURE__ */ jsx3("tr", { ...props, className: total ? "md__total" : void 0, children: rowChildren });
        },
        td: ({ node, children: cellChildren, ...props }) => {
          const text = plainText(cellChildren);
          if (text === null) return /* @__PURE__ */ jsx3("td", { ...props, children: cellChildren });
          const state = parseFigureCell(text).kind;
          const numeric = state !== "plain" || looksNumeric(text);
          return /* @__PURE__ */ jsx3("td", { ...props, className: numeric ? "md__num" : void 0, children: /* @__PURE__ */ jsx3(FigureCell, { text }) });
        },
        p: ({ node, children: pChildren, ...props }) => {
          const [first, ...rest] = Children.toArray(pChildren);
          if (isValidElement(first) && first.type === "strong") {
            const tag = plainText(first.props.children)?.trim().replace(/:$/, "").toUpperCase();
            if (tag && TAGS[tag]) {
              return /* @__PURE__ */ jsxs2("div", { className: `tagblock tagblock--${TAGS[tag]}`, children: [
                /* @__PURE__ */ jsx3("span", { className: "tagblock__tag", children: tag }),
                /* @__PURE__ */ jsx3("span", { className: "tagblock__text", children: rest })
              ] });
            }
          }
          return /* @__PURE__ */ jsx3("p", { ...props, children: pChildren });
        },
        a: ({ node, ...props }) => /* @__PURE__ */ jsx3("a", { ...props, target: "_blank", rel: "noreferrer noopener" })
      },
      children
    }
  ) });
}

// src/components/common/CopyButton.jsx
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
function CopyButton({ text, label = "Copy", className = "" }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(t);
  }, [copied]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text || "");
      setCopied(true);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text || "";
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
      } catch {
      }
      document.body.removeChild(ta);
    }
  }
  return /* @__PURE__ */ jsx4(
    "button",
    {
      type: "button",
      className: `btn btn--ghost btn--sm ${className}`,
      onClick: copy,
      disabled: !text,
      "aria-label": copied ? "Copied" : label,
      children: /* @__PURE__ */ jsx4(AnimatePresence, { mode: "wait", initial: false, children: /* @__PURE__ */ jsxs3(
        motion.span,
        {
          initial: { opacity: 0, scale: 0.8 },
          animate: { opacity: 1, scale: 1 },
          exit: { opacity: 0, scale: 0.8 },
          transition: { duration: 0.14 },
          style: { display: "flex", alignItems: "center", gap: 6 },
          children: [
            /* @__PURE__ */ jsx4(Icon, { name: copied ? "check" : "copy", size: 14 }),
            copied ? "Copied" : label
          ]
        },
        copied ? "done" : "idle"
      ) })
    }
  );
}

// src/components/common/Notice.jsx
import { motion as motion2 } from "framer-motion";
import { jsx as jsx5, jsxs as jsxs4 } from "react/jsx-runtime";
var ICONS = { error: "alert", warn: "alert", info: "info", ok: "check" };
function Notice({ tone = "info", title, children, action }) {
  return /* @__PURE__ */ jsxs4(
    motion2.div,
    {
      className: `notice notice--${tone}`,
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0.28, ease: [0.2, 0.7, 0.2, 1] },
      role: tone === "error" ? "alert" : "status",
      children: [
        /* @__PURE__ */ jsx5(Icon, { name: ICONS[tone] || "info", size: 18, className: "notice__icon" }),
        /* @__PURE__ */ jsxs4("div", { className: "notice__body", children: [
          title && /* @__PURE__ */ jsx5("p", { className: "notice__title", children: title }),
          children && /* @__PURE__ */ jsx5("div", { className: "notice__text", children }),
          action && /* @__PURE__ */ jsx5("div", { className: "notice__action", children: action })
        ] })
      ]
    }
  );
}

// src/components/chat/CitationViewer.jsx
import { useEffect as useEffect2, useState as useState2 } from "react";

// src/auth/token.js
var TOKEN_KEY = "artha.token";
var USER_KEY = "artha.user";
var token = null;
var loaded = false;
function read(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key, value) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
  }
}
function getToken() {
  if (!loaded) {
    token = read(TOKEN_KEY);
    loaded = true;
  }
  return token;
}
function setToken(value) {
  token = value || null;
  loaded = true;
  write(TOKEN_KEY, token);
}
function getStoredUser() {
  const raw = read(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
function setStoredUser(user) {
  write(USER_KEY, user ? JSON.stringify(user) : null);
}
function clearAuth() {
  setToken(null);
  setStoredUser(null);
}
function authHeaders() {
  const value = getToken();
  return value ? { Authorization: `Bearer ${value}` } : {};
}
var onUnauthorized = null;
function setUnauthorizedHandler(fn) {
  onUnauthorized = typeof fn === "function" ? fn : null;
}
function notifyUnauthorized(reason) {
  clearAuth();
  if (onUnauthorized) onUnauthorized(reason);
}

// src/api/client.js
async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { ...options.headers || {}, ...authHeaders() }
    });
  } catch {
    console.warn(
      "Could not reach the backend. Start the stack with `docker compose up -d`, or run the gateway directly: uvicorn app.main:app --port $ARTHA_BACKEND_PORT"
    );
    throw new Error("Can\u2019t reach Artha.AI. Check your connection and try again.");
  }
  if (!res.ok) {
    if (res.status === 401) {
      const body2 = await res.json().catch(() => ({}));
      notifyUnauthorized(body2.detail || "Your session ended. Please sign in again.");
      const err2 = new Error(body2.detail || "Your session ended. Please sign in again.");
      err2.status = 401;
      throw err2;
    }
    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    if (!isJson && (res.status === 404 || res.status === 403)) {
      throw new Error(
        `Port conflict: something other than the Artha.AI gateway answered on the API port (HTTP ${res.status}). Another service is likely holding it. Start the backend on a free port and point the dev server at it with VITE_API_TARGET=http://127.0.0.1:<port>.`
      );
    }
    if (!isJson && res.status >= 500) {
      throw new Error(
        `The backend gateway is not responding (HTTP ${res.status}). Check it with \`docker compose ps\`, or start it from the backend/ directory with: uvicorn app.main:app --reload --port $ARTHA_BACKEND_PORT`
      );
    }
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}
function post(path, payload) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}
function runQuery(mode, query, conversationId = null) {
  return post(`${mode.base_path}/query`, {
    query,
    conversation_id: conversationId || null
  });
}
async function uploadFile(path, file, fields = {}) {
  const form = new FormData();
  form.append("file", file);
  for (const [k, v] of Object.entries(fields)) {
    if (v !== null && v !== void 0) form.append(k, v);
  }
  let res;
  try {
    res = await fetch(path, { method: "POST", body: form, headers: { ...authHeaders() } });
  } catch {
    throw new Error("Could not reach the backend to upload the file.");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 422 && body?.detail?.needs_mapping) {
      return { needsMapping: true, ...body.detail };
    }
    const err = new Error(body.detail?.message || body.detail || `Upload failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}
async function readSSE(res, handlers = {}) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (; ; ) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let name = "message";
      let data = null;
      for (const line of frame.split("\n")) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) {
          try {
            data = JSON.parse(line.slice(5).trim());
          } catch {
            data = null;
          }
        }
      }
      if (data !== null) handlers[name]?.(data);
    }
  }
}
async function fsUploadHealth(mode) {
  try {
    return await request(`${mode.base_path}/upload/health`);
  } catch (e) {
    return { available: false, reason: e.message };
  }
}
function fsUpload(mode, file, conversationId) {
  return uploadFile(`${mode.base_path}/upload`, file, { conversation_id: conversationId });
}
async function fsWatchUpload(mode, jobId, onProgress) {
  const path = `${mode.base_path}/upload/${encodeURIComponent(jobId)}/events`;
  let res;
  try {
    res = await fetch(path, {
      headers: { Accept: "text/event-stream", ...authHeaders() }
    });
  } catch {
    throw new Error("Could not reach the backend to follow the upload.");
  }
  if (res.status === 401) {
    notifyUnauthorized();
    const err = new Error("Your session has expired. Sign in again.");
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Could not follow the upload (HTTP ${res.status})`);
  }
  let result = null;
  let failure = null;
  await readSSE(res, {
    progress: (p) => onProgress?.(p),
    result: (r) => {
      result = r;
    },
    error: (e) => {
      failure = e;
    }
  });
  if (failure) throw new Error(failure.error || "The document could not be processed.");
  if (!result) throw new Error("The upload ended without producing a document.");
  return result;
}
function fsDocuments(mode, conversationId) {
  return request(`${mode.base_path}/documents?conversation_id=${encodeURIComponent(conversationId)}`);
}
function fsDeleteDocument(mode, conversationId, docId) {
  return request(
    `${mode.base_path}/documents/${encodeURIComponent(docId)}?conversation_id=${encodeURIComponent(conversationId)}`,
    { method: "DELETE" }
  );
}
async function fsTableSnippet(mode, conversationId, docId, tableId) {
  const path = `${mode.base_path}/documents/${encodeURIComponent(docId)}/tables/${encodeURIComponent(tableId)}/snippet.jpg?conversation_id=${encodeURIComponent(conversationId)}`;
  const res = await fetch(path, { headers: { ...authHeaders() } });
  if (res.status === 401) {
    notifyUnauthorized();
    throw new Error("Your session has expired. Sign in again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "No scan is available for that citation.");
  }
  return URL.createObjectURL(await res.blob());
}
function fsDocumentPages(mode, conversationId, docId) {
  return request(
    `${mode.base_path}/documents/${encodeURIComponent(docId)}/pages?conversation_id=${encodeURIComponent(conversationId)}`
  );
}
async function fsPageImage(mode, conversationId, docId, pageNo) {
  const path = `${mode.base_path}/documents/${encodeURIComponent(docId)}/pages/${encodeURIComponent(pageNo)}.jpg?conversation_id=${encodeURIComponent(conversationId)}`;
  const res = await fetch(path, { headers: { ...authHeaders() } });
  if (res.status === 401) {
    notifyUnauthorized();
    throw new Error("Your session has expired. Sign in again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "No image is available for that page.");
  }
  return URL.createObjectURL(await res.blob());
}
function fsPageText(mode, conversationId, docId, pageNo) {
  return request(
    `${mode.base_path}/documents/${encodeURIComponent(docId)}/pages/${encodeURIComponent(pageNo)}/text?conversation_id=${encodeURIComponent(conversationId)}`
  );
}
async function fsEditCell(mode, conversationId, docId, tableId, body) {
  const path = `${mode.base_path}/documents/${encodeURIComponent(docId)}/tables/${encodeURIComponent(tableId)}/cells?conversation_id=${encodeURIComponent(conversationId)}`;
  let res;
  try {
    res = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        row_index: body.rowIndex,
        col_index: body.colIndex,
        expected_cell: body.expectedCell,
        action: body.action,
        ...body.value !== void 0 ? { value: body.value } : {}
      })
    });
  } catch {
    throw new Error("Could not reach the backend to save this figure.");
  }
  if (res.status === 401) {
    notifyUnauthorized();
    const err = new Error("Your session has expired. Sign in again.");
    err.status = 401;
    throw err;
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload.detail;
    const structured = detail && typeof detail === "object";
    const err = new Error(
      (structured ? detail.message : detail) || `Could not save this figure (HTTP ${res.status}).`
    );
    err.status = res.status;
    if (structured) {
      err.code = detail.code;
      Object.assign(err, detail);
    }
    throw err;
  }
  return payload;
}

// src/components/chat/CitationViewer.jsx
import { jsx as jsx6, jsxs as jsxs5 } from "react/jsx-runtime";
function CitationViewer({ mode, conversationId, docId, tableId, caption, onClose }) {
  const [url, setUrl] = useState2(null);
  const [error, setError] = useState2(null);
  useEffect2(() => {
    let objectUrl = null;
    let cancelled = false;
    fsTableSnippet(mode, conversationId, docId, tableId).then((u) => {
      if (cancelled) {
        URL.revokeObjectURL(u);
        return;
      }
      objectUrl = u;
      setUrl(u);
    }).catch((e) => {
      if (!cancelled) setError(e.message);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mode, conversationId, docId, tableId]);
  useEffect2(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return /* @__PURE__ */ jsx6(
    "div",
    {
      className: "cite__overlay",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "Source scan",
      onClick: onClose,
      children: /* @__PURE__ */ jsxs5("div", { className: "cite__panel", onClick: (e) => e.stopPropagation(), children: [
        /* @__PURE__ */ jsxs5("header", { className: "cite__head", children: [
          /* @__PURE__ */ jsx6(Icon, { name: "doc", size: 15 }),
          /* @__PURE__ */ jsx6("span", { className: "cite__caption", children: caption || "Source" }),
          /* @__PURE__ */ jsx6("button", { type: "button", className: "cite__close", onClick: onClose, "aria-label": "Close", children: "\xD7" })
        ] }),
        /* @__PURE__ */ jsxs5("div", { className: "cite__body", children: [
          error && /* @__PURE__ */ jsx6("p", { className: "cite__error", children: error }),
          !error && !url && /* @__PURE__ */ jsx6("p", { className: "cite__loading", children: "Loading the scan\u2026" }),
          url && /* @__PURE__ */ jsx6("img", { className: "cite__img", src: url, alt: caption || "Scanned source" })
        ] }),
        /* @__PURE__ */ jsx6("footer", { className: "cite__foot", children: "This is the region of the original scan the figures were read from. Check the printed values against it before relying on them." })
      ] })
    }
  );
}

// src/components/chat/AnswerCard.jsx
import { jsx as jsx7, jsxs as jsxs6 } from "react/jsx-runtime";
function Collapsible({ title, count, icon, children, defaultOpen = false }) {
  const [open, setOpen] = useState3(defaultOpen);
  return /* @__PURE__ */ jsxs6("div", { className: `collapse ${open ? "is-open" : ""}`, children: [
    /* @__PURE__ */ jsxs6("button", { type: "button", className: "collapse__head", onClick: () => setOpen((v) => !v), children: [
      /* @__PURE__ */ jsx7(Icon, { name: icon, size: 15, className: "collapse__icon" }),
      /* @__PURE__ */ jsx7("span", { className: "collapse__title", children: title }),
      count != null && /* @__PURE__ */ jsx7("span", { className: "pill pill--mute", children: count }),
      /* @__PURE__ */ jsx7(
        motion3.span,
        {
          className: "collapse__caret",
          animate: { rotate: open ? 180 : 0 },
          transition: { duration: 0.2, ease: [0.22, 1, 0.36, 1] },
          children: /* @__PURE__ */ jsx7(Icon, { name: "chevron", size: 15 })
        }
      )
    ] }),
    /* @__PURE__ */ jsx7(AnimatePresence2, { initial: false, children: open && /* @__PURE__ */ jsx7(
      motion3.div,
      {
        className: "collapse__body",
        initial: { height: 0, opacity: 0 },
        animate: { height: "auto", opacity: 1 },
        exit: { height: 0, opacity: 0 },
        transition: { duration: 0.26, ease: [0.22, 1, 0.36, 1] },
        children: /* @__PURE__ */ jsx7("div", { className: "collapse__inner", children })
      }
    ) })
  ] });
}
function hideCaveats(md) {
  if (!md) return md;
  return md.replace(/^>\s*\*\*No tool was called for this answer\*\*.*$/gm, "").replace(/\n{3,}/g, "\n\n").trim();
}
function SourceChunk({ chunk, onShowScan }) {
  const [expanded, setExpanded] = useState3(false);
  const content = chunk.content || "";
  const isLong = content.length > 520;
  const shown = expanded || !isLong ? content : `${content.slice(0, 520)}\u2026`;
  return /* @__PURE__ */ jsxs6("div", { className: "chunk", children: [
    /* @__PURE__ */ jsxs6("div", { className: "chunk__head", children: [
      /* @__PURE__ */ jsx7("span", { className: "chunk__index", children: chunk.index }),
      /* @__PURE__ */ jsx7("span", { className: "chunk__source", children: chunk.source }),
      chunk.hint_matched && /* @__PURE__ */ jsx7("span", { className: "pill pill--navy", children: "hint" }),
      chunk.rerank_score != null && /* @__PURE__ */ jsx7("span", { className: "chunk__score", title: "Reranker score", children: chunk.rerank_score.toFixed(3) }),
      onShowScan && /* @__PURE__ */ jsxs6(
        "button",
        {
          type: "button",
          className: "chunk__scan",
          onClick: onShowScan,
          title: "Show the scanned region this was read from",
          children: [
            /* @__PURE__ */ jsx7(Icon, { name: "search", size: 12 }),
            " scan"
          ]
        }
      )
    ] }),
    /* @__PURE__ */ jsx7("p", { className: "chunk__text", children: shown }),
    isLong && /* @__PURE__ */ jsx7("button", { type: "button", className: "chunk__more", onClick: () => setExpanded((v) => !v), children: expanded ? "Show less" : "Show full extract" })
  ] });
}
var hasChecks = (c) => Boolean(c && (c.confidence || c.unsourced || (c.tools_used || []).length > 0));
function HowChecked({ checks }) {
  const tools = checks.tools_used || [];
  return /* @__PURE__ */ jsxs6("div", { className: "answer__checks", children: [
    checks.confidence && /* @__PURE__ */ jsxs6("p", { children: [
      /* @__PURE__ */ jsx7("strong", { children: "Confidence:" }),
      " ",
      /* @__PURE__ */ jsx7("span", { className: `pill ${checks.reduced_from ? "pill--warn" : "pill--ok"}`, children: checks.confidence }),
      checks.reduced_from && /* @__PURE__ */ jsxs6("span", { className: "answer__checks-why", children: [
        " ",
        "Lowered from ",
        checks.reduced_from,
        checks.reduced_reason ? `: ${checks.reduced_reason}` : "."
      ] })
    ] }),
    checks.unsourced && /* @__PURE__ */ jsxs6("p", { className: "answer__checks-unsourced", children: [
      /* @__PURE__ */ jsx7(Icon, { name: "alert", size: 14 }),
      /* @__PURE__ */ jsxs6("span", { children: [
        /* @__PURE__ */ jsx7("strong", { children: "Unsourced answer." }),
        " No tool was called, so nothing here is checked against a document. Treat it as general knowledge."
      ] })
    ] }),
    tools.length > 0 && /* @__PURE__ */ jsxs6("p", { children: [
      /* @__PURE__ */ jsx7("strong", { children: "Tools used:" }),
      " ",
      /* @__PURE__ */ jsx7("span", { className: "answer__tools", children: tools.join(" \xB7 ") })
    ] })
  ] });
}
function AnswerCard({ result, mode, conversationId }) {
  const {
    summary,
    final_answer: answer,
    evidences_md: evidence,
    chunks = [],
    num_tables_searched: tables,
    num_chunks_retrieved: retrieved,
    elapsed_seconds: elapsed,
    // Upload-only field. Defaulted rather than optional-chained at every use,
    // because a conversation saved before this feature existed rehydrates from
    // a stored payload that has neither key.
    uploaded_documents: uploaded = [],
    rewritten_query: rewritten = "",
    materiality_legend: legend = null,
    upload_store_notice: storeNotice = null,
    // How the answer was checked. Hidden from the answer text on purpose (the
    // server strips it); offered here, collapsed, so it is available without
    // being the first thing a reader sees. Older stored turns have no `checks`.
    checks = null
  } = result || {};
  const [citation, setCitation] = useState3(null);
  const displayAnswer = hideCaveats(answer);
  const copyText = [summary, displayAnswer, evidence].filter(Boolean).join("\n\n");
  return /* @__PURE__ */ jsxs6("article", { className: "answer card", children: [
    /* @__PURE__ */ jsxs6("header", { className: "answer__head", children: [
      /* @__PURE__ */ jsx7("span", { className: "answer__mark", children: /* @__PURE__ */ jsx7(Icon, { name: "sparkle", size: 15 }) }),
      /* @__PURE__ */ jsx7("span", { className: "answer__label", children: "Answer" }),
      /* @__PURE__ */ jsxs6("div", { className: "answer__meta", children: [
        tables > 0 && /* @__PURE__ */ jsxs6("span", { className: "answer__stat", children: [
          tables,
          " tables searched"
        ] }),
        retrieved > 0 && /* @__PURE__ */ jsxs6("span", { className: "answer__stat", children: [
          retrieved,
          " chunks retrieved"
        ] }),
        elapsed > 0 && /* @__PURE__ */ jsxs6("span", { className: "answer__stat", children: [
          elapsed.toFixed(1),
          "s"
        ] })
      ] }),
      /* @__PURE__ */ jsx7(CopyButton, { text: copyText })
    ] }),
    rewritten && /* @__PURE__ */ jsxs6("p", { className: "answer__readas", children: [
      /* @__PURE__ */ jsx7("span", { children: "Read as:" }),
      " ",
      /* @__PURE__ */ jsxs6("em", { children: [
        "\u201C",
        rewritten,
        "\u201D"
      ] })
    ] }),
    storeNotice && /* @__PURE__ */ jsx7(Notice, { tone: "warn", children: storeNotice }),
    summary && /* @__PURE__ */ jsx7("div", { className: "answer__summary", children: /* @__PURE__ */ jsx7(Markdown, { children: summary }) }),
    displayAnswer && /* @__PURE__ */ jsx7("div", { className: "answer__body", children: /* @__PURE__ */ jsx7(Markdown, { children: displayAnswer }) }),
    !summary && !answer && /* @__PURE__ */ jsx7("p", { className: "answer__blank", children: "The pipeline returned an empty answer." }),
    legend?.markdown && !/materiality legend/i.test(`${summary || ""} ${answer || ""}`) && /* @__PURE__ */ jsx7("div", { className: "answer__legend", children: /* @__PURE__ */ jsx7(Markdown, { children: legend.markdown }) }),
    uploaded.length > 0 && /* @__PURE__ */ jsxs6("div", { className: "answer__uploads", children: [
      /* @__PURE__ */ jsx7(Icon, { name: "doc", size: 13 }),
      /* @__PURE__ */ jsxs6("span", { children: [
        "Answered against ",
        uploaded.length,
        " uploaded document",
        uploaded.length === 1 ? "" : "s",
        ":",
        " ",
        uploaded.map((d) => d.financial_year || d.filename).join(", ")
      ] }),
      uploaded.some((d) => d.unreadable_cells > 0) && /* @__PURE__ */ jsxs6("span", { className: "pill pill--warn", children: [
        uploaded.reduce((n, d) => n + (d.unreadable_cells || 0), 0),
        " figure(s) withheld"
      ] })
    ] }),
    (evidence || chunks.length > 0 || hasChecks(checks)) && /* @__PURE__ */ jsxs6("div", { className: "answer__extras", children: [
      evidence && /* @__PURE__ */ jsx7(Collapsible, { title: "Evidence", icon: "doc", children: /* @__PURE__ */ jsx7(Markdown, { children: evidence }) }),
      hasChecks(checks) && /* @__PURE__ */ jsx7(Collapsible, { title: "How this was checked", icon: "shield", children: /* @__PURE__ */ jsx7(HowChecked, { checks }) }),
      chunks.length > 0 && /* @__PURE__ */ jsx7(Collapsible, { title: "Retrieved sources", icon: "search", count: chunks.length, children: /* @__PURE__ */ jsx7("div", { className: "answer__chunks", children: chunks.map((c) => /* @__PURE__ */ jsx7(
        SourceChunk,
        {
          chunk: c,
          onShowScan: (
            // Only uploaded documents carry a scan to show. Corpus
            // chunks have no page image, so no affordance is offered
            // for them rather than one that fails when clicked.
            c.doc_id && c.table_id && conversationId && mode ? () => setCitation({ docId: c.doc_id, tableId: c.table_id, caption: c.source }) : null
          )
        },
        c.index
      )) }) })
    ] }),
    citation && /* @__PURE__ */ jsx7(
      CitationViewer,
      {
        mode,
        conversationId,
        docId: citation.docId,
        tableId: citation.tableId,
        caption: citation.caption,
        onClose: () => setCitation(null)
      }
    )
  ] });
}

// src/components/common/Dropzone.jsx
import { useCallback, useRef, useState as useState4 } from "react";
import { jsx as jsx8, jsxs as jsxs7 } from "react/jsx-runtime";
function Dropzone({
  onFiles,
  accept = ".pdf",
  multiple = true,
  disabled = false,
  busy = false,
  hint = "PDF only",
  label = "Drop financial statements here"
}) {
  const inputRef = useRef(null);
  const [dragDepth, setDragDepth] = useState4(0);
  const [error, setError] = useState4(null);
  const accepts = useCallback(
    (file) => {
      const patterns = accept.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
      if (patterns.length === 0) return true;
      const name = (file.name || "").toLowerCase();
      return patterns.some((p) => p.startsWith(".") ? name.endsWith(p) : file.type === p);
    },
    [accept]
  );
  const hand = useCallback(
    (fileList) => {
      const files = Array.from(fileList || []);
      if (files.length === 0) return;
      const ok = files.filter(accepts);
      const rejected = files.filter((f) => !accepts(f));
      setError(
        rejected.length ? `Not accepted: ${rejected.map((f) => f.name).join(", ")}. ${hint}.` : null
      );
      if (ok.length) onFiles?.(multiple ? ok : [ok[0]]);
    },
    [accepts, hint, multiple, onFiles]
  );
  const inert = disabled || busy;
  return /* @__PURE__ */ jsxs7("div", { className: "dropzone-wrap", children: [
    /* @__PURE__ */ jsxs7(
      "div",
      {
        className: `dropzone ${dragDepth > 0 ? "is-over" : ""} ${inert ? "is-disabled" : ""}`,
        role: "button",
        tabIndex: inert ? -1 : 0,
        "aria-disabled": inert,
        onClick: () => !inert && inputRef.current?.click(),
        onKeyDown: (e) => {
          if (inert) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        },
        onDragEnter: (e) => {
          e.preventDefault();
          if (!inert) setDragDepth((d) => d + 1);
        },
        onDragOver: (e) => {
          e.preventDefault();
        },
        onDragLeave: (e) => {
          e.preventDefault();
          setDragDepth((d) => Math.max(0, d - 1));
        },
        onDrop: (e) => {
          e.preventDefault();
          setDragDepth(0);
          if (!inert) hand(e.dataTransfer?.files);
        },
        children: [
          /* @__PURE__ */ jsx8(Icon, { name: "upload", size: 20, className: "dropzone__icon" }),
          /* @__PURE__ */ jsx8("span", { className: "dropzone__label", children: busy ? "Reading\u2026" : label }),
          /* @__PURE__ */ jsx8("span", { className: "dropzone__hint", children: busy ? "One document at a time" : `${hint} \xB7 click or drop` }),
          /* @__PURE__ */ jsx8(
            "input",
            {
              ref: inputRef,
              type: "file",
              className: "dropzone__input",
              accept,
              multiple,
              disabled: inert,
              onChange: (e) => {
                hand(e.target.files);
                e.target.value = "";
              }
            }
          )
        ]
      }
    ),
    error && /* @__PURE__ */ jsx8("p", { className: "dropzone__error", children: error })
  ] });
}

// src/components/common/ErrorBoundary.jsx
import { Component } from "react";
import { jsx as jsx9, jsxs as jsxs8 } from "react/jsx-runtime";
var ErrorBoundary = class extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("[ErrorBoundary]", this.props.label || "render error", error, info);
    this.setState({ info });
  }
  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null, info: null });
    }
  }
  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;
    return /* @__PURE__ */ jsxs8(Notice, { tone: "error", title: this.props.label || "Something failed to render", children: [
      /* @__PURE__ */ jsx9("p", { style: { margin: "0 0 6px" }, children: String(error.message || error) }),
      /* @__PURE__ */ jsx9("p", { style: { margin: 0, fontSize: "11.5px", opacity: 0.8 }, children: "The rest of the app is still usable. The full stack is in the browser console." }),
      info?.componentStack && /* @__PURE__ */ jsxs8("details", { style: { marginTop: 8 }, children: [
        /* @__PURE__ */ jsx9("summary", { style: { cursor: "pointer", fontSize: "11.5px" }, children: "Component stack" }),
        /* @__PURE__ */ jsx9("pre", { style: {
          margin: "6px 0 0",
          maxHeight: 180,
          overflow: "auto",
          fontSize: "10.5px",
          whiteSpace: "pre-wrap"
        }, children: info.componentStack.trim() })
      ] })
    ] });
  }
};

// src/components/ingestion/IngestProgress.jsx
import { useEffect as useEffect3, useState as useState5 } from "react";
import { motion as motion4 } from "framer-motion";

// src/config/brand.js
var brandMarks = {
  emblem: null,
  // url | null
  cag: null
  // url | null
};
var DISCLAIMER = "Independent audit-assistance tool. Not an official government service.";

// src/components/common/BrandMark.jsx
import { jsx as jsx10, jsxs as jsxs9 } from "react/jsx-runtime";
function BrandMark({ height = 36 }) {
  const marks = [brandMarks.emblem, brandMarks.cag].filter(Boolean);
  if (marks.length === 0) return null;
  return /* @__PURE__ */ jsx10("span", { className: "brandmarks", style: { display: "inline-flex", alignItems: "center", gap: 14 }, children: marks.map((src) => /* @__PURE__ */ jsx10("img", { src, alt: "", style: { height, width: "auto", display: "block" } }, src)) });
}

// src/components/ingestion/LedgerLoop.jsx
import { jsx as jsx11, jsxs as jsxs10 } from "react/jsx-runtime";
function LedgerLoop({ size = 200 }) {
  return /* @__PURE__ */ jsxs10(
    "svg",
    {
      className: "loop",
      width: size,
      height: size * 0.62,
      viewBox: "0 0 200 124",
      role: "img",
      "aria-label": "Checking the arithmetic of your statement",
      fill: "none",
      children: [
        /* @__PURE__ */ jsx11("ellipse", { cx: "100", cy: "112", rx: "70", ry: "5", className: "loop__shadow" }),
        /* @__PURE__ */ jsxs10("g", { className: "loop__scene loop__s1", children: [
          /* @__PURE__ */ jsx11("rect", { x: "34", y: "18", width: "132", height: "84", rx: "4", className: "loop__paper" }),
          /* @__PURE__ */ jsx11("line", { x1: "100", y1: "18", x2: "100", y2: "102", className: "loop__ink" }),
          [32, 44, 56, 68, 80].map((y) => /* @__PURE__ */ jsx11("line", { x1: "42", y1: y, x2: "92", y2: y, className: "loop__rule" }, y)),
          [32, 56, 80].map((y) => /* @__PURE__ */ jsx11("line", { x1: "146", y1: y, x2: "158", y2: y, className: "loop__figure" }, y)),
          /* @__PURE__ */ jsx11("g", { className: "loop__flap", children: /* @__PURE__ */ jsx11("rect", { x: "100", y: "18", width: "66", height: "84", rx: "2", className: "loop__flappaper" }) })
        ] }),
        /* @__PURE__ */ jsxs10("g", { className: "loop__scene loop__s2", children: [
          /* @__PURE__ */ jsx11("rect", { x: "34", y: "18", width: "132", height: "84", rx: "4", className: "loop__paper" }),
          /* @__PURE__ */ jsx11("line", { x1: "100", y1: "18", x2: "100", y2: "102", className: "loop__ink" }),
          [32, 44, 56, 68, 80].map((y) => /* @__PURE__ */ jsx11("line", { x1: "42", y1: y, x2: "92", y2: y, className: "loop__rule" }, y)),
          /* @__PURE__ */ jsx11("line", { x1: "150", y1: "30", x2: "150", y2: "86", className: "loop__gold" }),
          /* @__PURE__ */ jsxs10("g", { className: "loop__coin", children: [
            /* @__PURE__ */ jsx11("circle", { cx: "132", cy: "58", r: "10", className: "loop__coinface" }),
            /* @__PURE__ */ jsx11("text", { x: "132", y: "63", textAnchor: "middle", className: "loop__rupee", children: "\u20B9" })
          ] })
        ] }),
        /* @__PURE__ */ jsxs10("g", { className: "loop__scene loop__s3", children: [
          /* @__PURE__ */ jsx11("line", { x1: "100", y1: "34", x2: "100", y2: "100", className: "loop__ink" }),
          /* @__PURE__ */ jsx11("line", { x1: "78", y1: "102", x2: "122", y2: "102", className: "loop__ink" }),
          /* @__PURE__ */ jsxs10("g", { className: "loop__beam", children: [
            /* @__PURE__ */ jsx11("line", { x1: "56", y1: "40", x2: "144", y2: "40", className: "loop__ink" }),
            /* @__PURE__ */ jsx11("path", { d: "M56 40l-12 26h24z", className: "loop__pan" }),
            /* @__PURE__ */ jsx11("path", { d: "M144 40l-12 26h24z", className: "loop__pan" }),
            /* @__PURE__ */ jsx11("circle", { cx: "56", cy: "62", r: "6", className: "loop__coinface" }),
            /* @__PURE__ */ jsx11("text", { x: "56", y: "65", textAnchor: "middle", className: "loop__rupee loop__rupee--s", children: "\u20B9" }),
            /* @__PURE__ */ jsx11("line", { x1: "138", y1: "60", x2: "150", y2: "60", className: "loop__figure" })
          ] })
        ] }),
        /* @__PURE__ */ jsxs10("g", { className: "loop__scene loop__s4", children: [
          /* @__PURE__ */ jsx11("rect", { x: "52", y: "20", width: "96", height: "70", rx: "4", className: "loop__paper" }),
          [34, 46].map((y) => /* @__PURE__ */ jsxs10("g", { children: [
            /* @__PURE__ */ jsx11("line", { x1: "62", y1: y, x2: "96", y2: y, className: "loop__rule" }),
            /* @__PURE__ */ jsx11("line", { x1: "118", y1: y, x2: "138", y2: y, className: "loop__figure" })
          ] }, y)),
          /* @__PURE__ */ jsx11("line", { x1: "62", y1: "62", x2: "138", y2: "62", className: "loop__ink" }),
          /* @__PURE__ */ jsx11("line", { x1: "62", y1: "66", x2: "138", y2: "66", className: "loop__ink" }),
          /* @__PURE__ */ jsx11("line", { x1: "62", y1: "78", x2: "92", y2: "78", className: "loop__ink loop__bold" }),
          /* @__PURE__ */ jsx11("line", { x1: "112", y1: "78", x2: "138", y2: "78", className: "loop__ink loop__bold" }),
          /* @__PURE__ */ jsxs10("g", { className: "loop__tick", children: [
            /* @__PURE__ */ jsx11("circle", { cx: "138", cy: "86", r: "14", className: "loop__ok" }),
            /* @__PURE__ */ jsx11("path", { d: "M131 86l5 5 9-11", className: "loop__okmark" })
          ] })
        ] })
      ]
    }
  );
}

// src/components/ingestion/IngestProgress.jsx
import { jsx as jsx12, jsxs as jsxs11 } from "react/jsx-runtime";
var STAGES = [
  { key: "render", label: "Reading the PDF", say: "Reading the PDF\u2026" },
  { key: "precheck", label: "Checking page quality", say: "Checking the quality of each page\u2026" },
  { key: "preprocess", label: "Straightening pages", say: "Straightening and cleaning the pages\u2026" },
  { key: "convert", label: "Detecting layout and reading tables", say: "Detecting layout and reading tables\u2026" },
  { key: "vlm", label: "Second read by the vision model", say: "Second read by the vision model\u2026" },
  { key: "verify", label: "Verifying figures", say: "Checking that every subtotal adds up\u2026" },
  { key: "identify", label: "Identifying the document", say: "Working out the entity, year and framework\u2026" },
  { key: "finish", label: "Finishing", say: "Finishing\u2026" }
];
var TIPS = [
  "We never guess a figure. If it can\u2019t be read, we tell you.",
  "A filing usually carries last year\u2019s column too, so one upload answers a two-year question.",
  "Click any red or amber figure to enter it from the scan yourself.",
  "Every subtotal is checked by arithmetic before we rely on it."
];
var mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
function stageIndex(stage, fraction = 0) {
  if (stage === "done") return STAGES.length;
  const i = STAGES.findIndex((s) => s.key === stage);
  if (i === STAGES.length - 2 && fraction >= 0.95) return STAGES.length - 1;
  return i;
}
function friendlyError(error) {
  const text = String(error || "");
  if (/password|encrypt/i.test(text)) {
    return "This file is password-protected. Remove the password and attach it again.";
  }
  return text.replace(/^[^:]+\.pdf:\s*/i, "") || "This file could not be converted.";
}
function IngestProgress({
  filename,
  stage,
  message,
  fraction = 0,
  error,
  status,
  ahead = 0,
  onRetry,
  onDismiss
}) {
  const [open, setOpen] = useState5(false);
  const [elapsed, setElapsed] = useState5(0);
  const [tip, setTip] = useState5(0);
  const queued = status === "queued" || !stage || stage === "queued";
  const done = stage === "done";
  const failed2 = Boolean(error);
  useEffect3(() => {
    if (done || failed2 || queued) return void 0;
    const t = setInterval(() => setElapsed((s) => s + 1), 1e3);
    return () => clearInterval(t);
  }, [done, failed2, queued]);
  useEffect3(() => {
    if (!open) return void 0;
    const t = setInterval(() => setTip((i) => (i + 1) % TIPS.length), 6e3);
    return () => clearInterval(t);
  }, [open]);
  const index = stageIndex(stage, fraction);
  const current = STAGES[Math.max(0, Math.min(index, STAGES.length - 1))];
  const pct = Math.round((done ? 1 : fraction || 0) * 100);
  if (failed2) {
    return /* @__PURE__ */ jsxs11("div", { className: "ingest is-error", role: "alert", children: [
      /* @__PURE__ */ jsx12(Icon, { name: "alert", size: 16, className: "ingest__erricon" }),
      /* @__PURE__ */ jsxs11("div", { className: "ingest__errbody", children: [
        /* @__PURE__ */ jsx12("span", { className: "ingest__file", title: filename, children: filename }),
        /* @__PURE__ */ jsx12("p", { className: "ingest__msg", children: friendlyError(error) })
      ] }),
      onRetry && /* @__PURE__ */ jsxs11("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: onRetry, children: [
        /* @__PURE__ */ jsx12(Icon, { name: "refresh", size: 13 }),
        " Retry"
      ] }),
      onDismiss && /* @__PURE__ */ jsx12("button", { type: "button", className: "ingest__x", onClick: onDismiss, "aria-label": "Dismiss", children: "\xD7" })
    ] });
  }
  if (queued) {
    return /* @__PURE__ */ jsxs11("div", { className: "ingest ingest--queued", children: [
      /* @__PURE__ */ jsx12(Icon, { name: "doc", size: 14 }),
      /* @__PURE__ */ jsx12("span", { className: "ingest__file", title: filename, children: filename }),
      /* @__PURE__ */ jsxs11("span", { className: "pill pill--mute", children: [
        "Queued",
        ahead > 0 ? ` \xB7 ${ahead} ahead` : ""
      ] })
    ] });
  }
  return /* @__PURE__ */ jsxs11("div", { className: `ingest ${open ? "is-open" : ""}`, children: [
    /* @__PURE__ */ jsxs11(
      "button",
      {
        type: "button",
        className: "ingest__bar",
        onClick: () => setOpen((v) => !v),
        "aria-expanded": open,
        children: [
          /* @__PURE__ */ jsx12("span", { className: "ingest__rupee", "aria-hidden": "true", children: "\u20B9" }),
          /* @__PURE__ */ jsxs11("span", { className: "ingest__now", children: [
            /* @__PURE__ */ jsx12("strong", { children: done ? "Done" : current.label }),
            /* @__PURE__ */ jsxs11("span", { className: "ingest__file", title: filename, children: [
              " \xB7 ",
              filename
            ] })
          ] }),
          /* @__PURE__ */ jsx12("span", { className: "ingest__time", children: mmss(elapsed) }),
          /* @__PURE__ */ jsx12(
            "span",
            {
              className: "ingest__track",
              role: "progressbar",
              "aria-valuenow": pct,
              "aria-valuemin": 0,
              "aria-valuemax": 100,
              children: /* @__PURE__ */ jsx12(
                motion4.span,
                {
                  className: "ingest__fill",
                  initial: false,
                  animate: { width: `${pct}%` },
                  transition: { duration: 0.4, ease: "easeOut" }
                }
              )
            }
          ),
          /* @__PURE__ */ jsx12("span", { className: "ingest__expand", children: open ? "Collapse" : "Expand" })
        ]
      }
    ),
    open && /* @__PURE__ */ jsxs11("div", { className: "ingest__panel", children: [
      /* @__PURE__ */ jsx12("div", { className: "ingest__marks", children: /* @__PURE__ */ jsx12(BrandMark, {}) }),
      /* @__PURE__ */ jsx12(LedgerLoop, {}),
      /* @__PURE__ */ jsxs11("p", { className: "ingest__step", children: [
        /* @__PURE__ */ jsxs11("span", { className: "pill pill--navy", children: [
          "Step ",
          Math.min(index + 1, STAGES.length),
          " of ",
          STAGES.length
        ] }),
        /* @__PURE__ */ jsxs11("span", { className: "ingest__elapsed", children: [
          mmss(elapsed),
          " elapsed"
        ] })
      ] }),
      /* @__PURE__ */ jsx12("h3", { className: "ingest__title", children: done ? "Ready" : current.say }),
      /* @__PURE__ */ jsxs11("p", { className: "ingest__msg", children: [
        message ? `${message.replace(/[.…]+$/, "")}. ` : "This can take a few minutes for a long filing. ",
        "You can close this and keep working; we will tell you when it is ready."
      ] }),
      /* @__PURE__ */ jsxs11("p", { className: "ingest__tip", children: [
        "Tip: ",
        TIPS[tip]
      ] }),
      /* @__PURE__ */ jsx12("ol", { className: "ingest__steps", children: STAGES.map((s, i) => {
        const state = done || i < index ? "is-done" : i === index ? "is-active" : "is-todo";
        return /* @__PURE__ */ jsxs11("li", { className: `ingest__stepitem ${state}`, children: [
          /* @__PURE__ */ jsx12("span", { className: "ingest__dot", "aria-hidden": "true", children: state === "is-done" ? /* @__PURE__ */ jsx12(Icon, { name: "check", size: 11, strokeWidth: 2.6 }) : i + 1 }),
          s.label
        ] }, s.key);
      }) })
    ] })
  ] });
}

// src/components/ingestion/QualityReport.jsx
import { useEffect as useEffect4 } from "react";
import { motion as motion5 } from "framer-motion";
import { Fragment as Fragment2, jsx as jsx13, jsxs as jsxs12 } from "react/jsx-runtime";
var GRADE_TONE = { excellent: "is-ok", good: "is-ok", fair: "is-warn", poor: "is-err" };
var GRADE_TEXT = {
  excellent: "This filing read very cleanly.",
  good: "This filing read cleanly.",
  fair: "Most of this filing can be relied on.",
  poor: "Several pages read badly. Check figures against the original."
};
function reasonText(code) {
  const c = String(code || "");
  if (c === "figures_not_extracted") return "The page shows a figure the table left empty";
  if (c === "unreadable_text") return "Smudged or unreadable digits";
  if (c === "readers_disagree") return "Read differently by the two readers";
  if (c === "unsupported_by_ocr") return "Not backed by the page text";
  if (c === "no_second_read_for_row") return "No second read of this row";
  if (c === "vlm_only_row") return "Only the second reader saw this row";
  if (c.startsWith("low_ocr_confidence")) return "Low OCR confidence";
  return c ? c.replace(/_/g, " ") : "Could not be established";
}
var plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
function keptUntil(uploadedAt, retentionDays) {
  if (!uploadedAt || !retentionDays) return null;
  const d = new Date((uploadedAt + retentionDays * 86400) * 1e3);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
function tablesWithProblems(quality) {
  const ids = /* @__PURE__ */ new Set();
  [...quality.unreadable_cells || [], ...quality.recovered_cells || []].forEach((c) => {
    if (c.table_id) ids.add(c.table_id);
  });
  return ids.size;
}
function Section({ title, children }) {
  return /* @__PURE__ */ jsxs12("section", { className: "qd__section", children: [
    /* @__PURE__ */ jsx13("h3", { className: "section-label", children: title }),
    children
  ] });
}
function QualityReport({
  doc,
  retentionDays = 30,
  onClose,
  onOpenPages,
  onEnter
}) {
  useEffect4(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!doc) return null;
  const ident = doc.identification || {};
  const quality = doc.quality || {};
  const coverage = doc.coverage || {};
  const withheld = quality.unreadable_cells || [];
  const unreadable = withheld.filter((c) => c.recovered_text == null);
  const recovered = withheld.filter((c) => c.recovered_text != null);
  const failed2 = quality.failed_footings || [];
  const paths = coverage.paths || [];
  const canAnswer = paths.filter((p) => p.state === "viable");
  const cannotAnswer = paths.filter((p) => p.state !== "viable");
  const periods = doc.periods || [];
  const tables = doc.tables || 0;
  const problemTables = Math.min(tables, tablesWithProblems(quality));
  const needYou = unreadable.length;
  const grade = doc.grade || "unknown";
  const until = keptUntil(doc.uploaded_at, retentionDays);
  const weakest = doc.low_grade && doc.low_grade !== "good" ? doc.low_grade : null;
  const meta = [
    doc.company,
    ident.financial_year && `FY ${ident.financial_year}`,
    doc.pages && plural(doc.pages, "page", "pages"),
    doc.tables != null && plural(doc.tables, "table", "tables"),
    until && `kept until ${until}, or until you delete this conversation`
  ].filter(Boolean).join(" \xB7 ");
  return /* @__PURE__ */ jsxs12("div", { className: "qd", role: "presentation", children: [
    /* @__PURE__ */ jsx13("div", { className: "qd__scrim", onClick: onClose }),
    /* @__PURE__ */ jsxs12(
      motion5.aside,
      {
        className: "qd__drawer",
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "Quality report",
        initial: { x: 40, opacity: 0 },
        animate: { x: 0, opacity: 1 },
        transition: { duration: 0.28, ease: [0.3, 0.7, 0.2, 1] },
        children: [
          /* @__PURE__ */ jsxs12("header", { className: "qd__head", children: [
            /* @__PURE__ */ jsxs12("div", { children: [
              /* @__PURE__ */ jsx13("p", { className: "qd__eyebrow", children: "Quality report" }),
              /* @__PURE__ */ jsx13("h2", { className: "qd__file", title: doc.filename, children: doc.filename }),
              /* @__PURE__ */ jsx13("p", { className: "qd__meta", children: meta })
            ] }),
            /* @__PURE__ */ jsx13("button", { type: "button", className: "qd__close", onClick: onClose, "aria-label": "Close quality report", children: "\xD7" })
          ] }),
          /* @__PURE__ */ jsxs12("div", { className: "qd__body", children: [
            /* @__PURE__ */ jsxs12("div", { className: `qd__grade ${GRADE_TONE[grade] || ""}`, children: [
              /* @__PURE__ */ jsx13("span", { className: "qd__badge", children: grade[0].toUpperCase() + grade.slice(1) }),
              /* @__PURE__ */ jsxs12("div", { children: [
                /* @__PURE__ */ jsxs12("p", { className: "qd__lead", children: [
                  GRADE_TEXT[grade] || "Scan quality could not be graded.",
                  needYou > 0 && ` ${plural(needYou, "figure needs", "figures need")} you.`
                ] }),
                /* @__PURE__ */ jsxs12("p", { className: "qd__sub", children: [
                  tables > 0 && `${tables - problemTables} of ${tables} tables were read cleanly. `,
                  weakest && `The weakest page is graded ${weakest}. `,
                  "We never guess a figure: anything we could not establish is withheld or marked."
                ] })
              ] })
            ] }),
            unreadable.length > 0 && /* @__PURE__ */ jsx13(Section, { title: `${plural(unreadable.length, "figure", "figures")} could not be read from the scan`, children: /* @__PURE__ */ jsxs12("table", { className: "qd__table", children: [
              /* @__PURE__ */ jsx13("thead", { children: /* @__PURE__ */ jsxs12("tr", { children: [
                /* @__PURE__ */ jsx13("th", { children: "Where" }),
                /* @__PURE__ */ jsx13("th", { children: "Item" }),
                /* @__PURE__ */ jsx13("th", { children: "Why" }),
                /* @__PURE__ */ jsx13("th", {})
              ] }) }),
              /* @__PURE__ */ jsx13("tbody", { children: unreadable.map((c, i) => /* @__PURE__ */ jsxs12("tr", { children: [
                /* @__PURE__ */ jsxs12("td", { className: "num", children: [
                  "p.",
                  c.page_no,
                  c.table_id ? ` \xB7 ${String(c.table_id).split("_").slice(-2).join("_")}` : ""
                ] }),
                /* @__PURE__ */ jsxs12("td", { children: [
                  c.row_label,
                  /* @__PURE__ */ jsx13("span", { className: "qd__col", children: c.column })
                ] }),
                /* @__PURE__ */ jsx13("td", { children: (c.reasons || []).map(reasonText)[0] }),
                /* @__PURE__ */ jsx13("td", { className: "qd__act", children: onEnter && /* @__PURE__ */ jsx13("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: () => onEnter(c), children: "Enter" }) })
              ] }, `u${i}`)) })
            ] }) }),
            recovered.length > 0 && /* @__PURE__ */ jsx13(Section, { title: "Recovered, not yet confirmed", children: recovered.map((c, i) => /* @__PURE__ */ jsxs12("div", { className: "qd__rec", children: [
              /* @__PURE__ */ jsx13("span", { className: "fig fig--recovered", children: /* @__PURE__ */ jsx13("span", { className: "fig__label", children: "recovered ?" }) }),
              /* @__PURE__ */ jsxs12("span", { className: "qd__recmain", children: [
                /* @__PURE__ */ jsx13("strong", { children: c.row_label }),
                ", ",
                c.column,
                ", p.",
                c.page_no,
                " \xB7",
                " ",
                /* @__PURE__ */ jsx13("span", { className: "num", children: c.recovered_text })
              ] }),
              c.confidence && /* @__PURE__ */ jsxs12("span", { className: "pill pill--warn", children: [
                "Confidence: ",
                c.confidence
              ] }),
              onEnter && /* @__PURE__ */ jsx13("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: () => onEnter(c), children: "Review" })
            ] }, `r${i}`)) }),
            failed2.length > 0 && /* @__PURE__ */ jsx13(Section, { title: "Printed totals that did not add up", children: failed2.map((f, i) => /* @__PURE__ */ jsxs12("p", { className: "qd__warn", children: [
              /* @__PURE__ */ jsx13(Icon, { name: "alert", size: 14 }),
              /* @__PURE__ */ jsxs12("span", { children: [
                /* @__PURE__ */ jsxs12("strong", { children: [
                  "Page ",
                  f.page_no,
                  ": ",
                  f.subtotal_label,
                  "."
                ] }),
                " ",
                f.recomputed != null ? /* @__PURE__ */ jsxs12(Fragment2, { children: [
                  "The rows add to ",
                  /* @__PURE__ */ jsx13("span", { className: "num", children: f.recomputed.toLocaleString("en-IN") }),
                  " but the printed total is ",
                  /* @__PURE__ */ jsx13("span", { className: "num", children: f.printed != null ? f.printed.toLocaleString("en-IN") : "\u2014" }),
                  ". Shown as printed; answers will say so."
                ] }) : /* @__PURE__ */ jsx13(Fragment2, { children: "The components could not be summed. Shown as printed." })
              ] })
            ] }, i)) }),
            paths.length > 0 && /* @__PURE__ */ jsx13(Section, { title: "What this document can answer", children: /* @__PURE__ */ jsxs12("div", { className: "qd__cols", children: [
              /* @__PURE__ */ jsxs12("div", { className: "qd__box qd__box--ok", children: [
                /* @__PURE__ */ jsxs12("p", { className: "qd__boxh", children: [
                  /* @__PURE__ */ jsx13(Icon, { name: "check", size: 13 }),
                  " Can answer"
                ] }),
                /* @__PURE__ */ jsx13("ul", { children: canAnswer.map((p) => /* @__PURE__ */ jsx13("li", { children: p.name }, p.name)) })
              ] }),
              /* @__PURE__ */ jsxs12("div", { className: "qd__box qd__box--no", children: [
                /* @__PURE__ */ jsxs12("p", { className: "qd__boxh", children: [
                  /* @__PURE__ */ jsx13(Icon, { name: "alert", size: 13 }),
                  " Cannot answer"
                ] }),
                cannotAnswer.length === 0 ? /* @__PURE__ */ jsx13("p", { className: "qd__none", children: "Nothing is ruled out." }) : /* @__PURE__ */ jsx13("ul", { children: cannotAnswer.map((p) => /* @__PURE__ */ jsxs12("li", { children: [
                  p.name,
                  p.detail ? ` \u2014 ${p.detail}` : ""
                ] }, p.name)) })
              ] })
            ] }) }),
            periods.length > 0 && /* @__PURE__ */ jsxs12(Section, { title: "Periods in the data", children: [
              /* @__PURE__ */ jsx13("div", { className: "qd__periods", children: periods.map((p) => /* @__PURE__ */ jsxs12("span", { className: "pill pill--navy", children: [
                p.label,
                " \xB7 ",
                p.is_comparative ? "prior-year column" : "current"
              ] }, p.label)) }),
              periods.length > 1 && /* @__PURE__ */ jsx13("p", { className: "qd__hint", children: "A single filing carries its prior-year column, so this one upload answers a two-year question." })
            ] }),
            ((quality.notes || []).length > 0 || !quality.vlm_used) && /* @__PURE__ */ jsx13(Section, { title: "Notes from the reader", children: /* @__PURE__ */ jsxs12("ul", { className: "qd__notes", children: [
              !quality.vlm_used && /* @__PURE__ */ jsx13("li", { children: "No second independent read was available, so each figure rests on one reader plus its own arithmetic." }),
              (quality.notes || []).map((n, i) => /* @__PURE__ */ jsx13("li", { children: n }, i)),
              (ident.unresolved_conflicts || []).map((n, i) => /* @__PURE__ */ jsx13("li", { children: n }, `c${i}`))
            ] }) })
          ] }),
          /* @__PURE__ */ jsxs12("footer", { className: "qd__foot", children: [
            /* @__PURE__ */ jsxs12("span", { children: [
              "Uploaded documents last ",
              retentionDays,
              " days and are removed with their conversation."
            ] }),
            onOpenPages && /* @__PURE__ */ jsx13("button", { type: "button", className: "btn btn--primary btn--sm", onClick: onOpenPages, children: "Open pages" })
          ] })
        ]
      }
    )
  ] });
}

// src/components/ingestion/UploadPanel.jsx
import { useCallback as useCallback2, useEffect as useEffect5, useRef as useRef2, useState as useState7 } from "react";

// src/components/ingestion/DocumentChips.jsx
import { useState as useState6 } from "react";
import { Fragment as Fragment3, jsx as jsx14, jsxs as jsxs13 } from "react/jsx-runtime";
var GRADE_TONE2 = {
  excellent: "pill--ok",
  good: "pill--ok",
  fair: "pill--warn",
  poor: "pill--err"
};
var plural2 = (n, one, many) => `${n} ${n === 1 ? one : many}`;
function DocumentChips({ docs, onDelete, onView, retentionDays = 30 }) {
  const [openId, setOpenId] = useState6(null);
  if (!docs || docs.length === 0) return null;
  const open = docs.find((d) => d.doc_id === openId) || null;
  return /* @__PURE__ */ jsxs13("div", { className: "chips", children: [
    docs.map((doc) => {
      const quality = doc.quality || {};
      const withheld = (quality.unreadable_cells || []).length;
      const recovered = doc.recovered_cells || 0;
      const footing = (quality.failed_footings || []).length;
      const entered = doc.user_entered_cells || 0;
      const grade = doc.grade;
      return /* @__PURE__ */ jsxs13("article", { className: "docchip", children: [
        /* @__PURE__ */ jsxs13("header", { className: "docchip__head", children: [
          /* @__PURE__ */ jsx14(Icon, { name: "doc", size: 15, className: "docchip__icon" }),
          /* @__PURE__ */ jsx14("span", { className: "docchip__file", title: doc.filename, children: doc.filename }),
          /* @__PURE__ */ jsx14("span", { className: "docchip__meta", children: [
            doc.company,
            doc.financial_year && `FY ${doc.financial_year}`,
            doc.pages && plural2(doc.pages, "page", "pages"),
            doc.tables != null && plural2(doc.tables, "table", "tables")
          ].filter(Boolean).join(" \xB7 ") }),
          onDelete && /* @__PURE__ */ jsx14(
            "button",
            {
              type: "button",
              className: "docchip__del",
              "aria-label": `Remove ${doc.filename}`,
              title: "Remove this document from the conversation",
              onClick: () => {
                if (openId === doc.doc_id) setOpenId(null);
                onDelete(doc.doc_id);
              },
              children: /* @__PURE__ */ jsx14(Icon, { name: "trash", size: 13 })
            }
          )
        ] }),
        /* @__PURE__ */ jsxs13("div", { className: "docchip__pills", children: [
          /* @__PURE__ */ jsxs13("span", { className: `pill ${GRADE_TONE2[grade] || "pill--mute"}`, children: [
            "Scan quality: ",
            grade || "unknown",
            doc.low_grade && doc.low_grade !== grade && /* @__PURE__ */ jsxs13(Fragment3, { children: [
              " \xB7 weakest ",
              doc.low_grade
            ] })
          ] }),
          doc.tables === 0 && /* @__PURE__ */ jsx14("span", { className: "pill pill--mute", children: "0 tables \u2014 text only" }),
          withheld > 0 && /* @__PURE__ */ jsxs13("span", { className: "pill pill--err", children: [
            /* @__PURE__ */ jsx14(Icon, { name: "alert", size: 12 }),
            " ",
            plural2(withheld, "figure", "figures"),
            " withheld"
          ] }),
          recovered > 0 && /* @__PURE__ */ jsxs13("span", { className: "pill pill--warn", children: [
            recovered,
            " recovered"
          ] }),
          footing > 0 && /* @__PURE__ */ jsxs13("span", { className: "pill pill--warn", children: [
            plural2(footing, "total", "totals"),
            " did not foot"
          ] }),
          entered > 0 && /* @__PURE__ */ jsxs13("span", { className: "pill pill--gold", children: [
            entered,
            " entered by you"
          ] })
        ] }),
        /* @__PURE__ */ jsxs13("div", { className: "docchip__actions", children: [
          onView && /* @__PURE__ */ jsxs13("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: () => onView(doc), children: [
            /* @__PURE__ */ jsx14(Icon, { name: "eye", size: 13 }),
            " Open pages"
          ] }),
          /* @__PURE__ */ jsx14(
            "button",
            {
              type: "button",
              className: "btn btn--ghost btn--sm",
              "aria-expanded": openId === doc.doc_id,
              onClick: () => setOpenId(openId === doc.doc_id ? null : doc.doc_id),
              children: "Quality report"
            }
          )
        ] })
      ] }, doc.doc_id);
    }),
    open && /* @__PURE__ */ jsx14(
      QualityReport,
      {
        doc: open,
        retentionDays,
        onClose: () => setOpenId(null),
        onOpenPages: onView ? () => {
          setOpenId(null);
          onView(open);
        } : void 0,
        onEnter: onView ? (cell) => {
          setOpenId(null);
          onView(open, cell);
        } : void 0
      }
    )
  ] });
}

// src/components/ingestion/UploadPanel.jsx
import { jsx as jsx15, jsxs as jsxs14 } from "react/jsx-runtime";
function UploadPanel({
  mode,
  conversationId,
  onConversationChange,
  onDocumentsChange,
  onReady,
  onView
}) {
  const [health, setHealth] = useState7(null);
  const [docs, setDocs] = useState7([]);
  const [waitingFiles, setWaitingFiles] = useState7([]);
  const [active, setActive] = useState7(null);
  const [error, setError] = useState7(null);
  const [failed2, setFailed] = useState7([]);
  const waiting = waitingFiles.length;
  const convoRef = useRef2(conversationId || null);
  useEffect5(() => {
    const next = conversationId || null;
    if (convoRef.current === next) return;
    convoRef.current = next;
    setDocs([]);
    setError(null);
    onDocumentsChange?.([]);
  }, [conversationId, onDocumentsChange]);
  const mountedRef = useRef2(true);
  useEffect5(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  useEffect5(() => {
    let alive = true;
    fsUploadHealth(mode).then((h) => {
      if (alive) setHealth(h);
    });
    return () => {
      alive = false;
    };
  }, [mode]);
  const refresh = useCallback2(async () => {
    const convo = convoRef.current;
    if (!convo) return;
    try {
      const { documents } = await fsDocuments(mode, convo);
      if (!mountedRef.current) return;
      setDocs(documents || []);
      onDocumentsChange?.(documents || []);
    } catch {
    }
  }, [mode, conversationId, onDocumentsChange]);
  useEffect5(() => {
    refresh();
  }, [refresh]);
  const pendingRef = useRef2([]);
  const drainingRef = useRef2(false);
  const drain = useCallback2(async () => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    try {
      while (pendingRef.current.length > 0) {
        const file = pendingRef.current.shift();
        if (mountedRef.current) {
          setWaitingFiles([...pendingRef.current]);
          setActive({ filename: file.name, stage: "queued", message: "Queued\u2026", fraction: 0 });
        }
        try {
          const { job_id: jobId, conversation_id: minted } = await fsUpload(mode, file, convoRef.current || "");
          if (minted && minted !== convoRef.current) {
            convoRef.current = minted;
            onConversationChange?.(minted);
          }
          await fsWatchUpload(mode, jobId, (p) => {
            if (mountedRef.current) {
              setActive({
                filename: file.name,
                stage: p.stage,
                message: p.message,
                fraction: p.fraction
              });
            }
          });
          await refresh();
        } catch (e) {
          if (mountedRef.current) {
            setFailed((prev) => [...prev, { id: `${file.name}-${Date.now()}`, file, error: e.message }]);
          }
        } finally {
          if (mountedRef.current) setActive(null);
        }
      }
    } finally {
      drainingRef.current = false;
      if (mountedRef.current) setWaitingFiles([]);
    }
  }, [mode, refresh, onConversationChange]);
  const accept = useCallback2((files) => {
    const all = Array.from(files || []);
    const pdfs = all.filter((f) => (f.name || "").toLowerCase().endsWith(".pdf"));
    const rejected = all.length - pdfs.length;
    setError(rejected > 0 ? `${rejected} file(s) skipped \u2014 only PDF is supported.` : null);
    if (pdfs.length === 0) return;
    pendingRef.current.push(...pdfs);
    setWaitingFiles([...pendingRef.current]);
    drain();
  }, [drain]);
  const retry = useCallback2((id) => {
    setFailed((prev) => {
      const item = prev.find((f) => f.id === id);
      if (item) {
        pendingRef.current.push(item.file);
        setWaitingFiles([...pendingRef.current]);
        drain();
      }
      return prev.filter((f) => f.id !== id);
    });
  }, [drain]);
  const dismiss = useCallback2((id) => setFailed((prev) => prev.filter((f) => f.id !== id)), []);
  const remove = useCallback2(async (docId) => {
    try {
      await fsDeleteDocument(mode, convoRef.current, docId);
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }, [mode, refresh]);
  const busy = Boolean(active);
  const available = health ? Boolean(health.available) : true;
  useEffect5(() => {
    onReady?.({ accept, busy, available, reason: health?.reason || null });
  }, [onReady, accept, busy, available, health?.reason]);
  if (health && !health.available) {
    return /* @__PURE__ */ jsx15("div", { className: "uploadpanel", children: /* @__PURE__ */ jsx15(Notice, { tone: "warn", title: "Document upload is unavailable", children: health.reason }) });
  }
  if (docs.length === 0 && !active && !error && waiting === 0 && failed2.length === 0) return null;
  return /* @__PURE__ */ jsxs14("div", { className: "uploadpanel", children: [
    active && /* @__PURE__ */ jsx15(IngestProgress, { ...active, status: "converting" }),
    waitingFiles.map((file, i) => /* @__PURE__ */ jsx15(
      IngestProgress,
      {
        filename: file.name,
        status: "queued",
        ahead: i + (active ? 1 : 0)
      },
      `${file.name}-${i}`
    )),
    failed2.map((f) => /* @__PURE__ */ jsx15(
      IngestProgress,
      {
        filename: f.file.name,
        error: f.error,
        onRetry: () => retry(f.id),
        onDismiss: () => dismiss(f.id)
      },
      f.id
    )),
    failed2.length > 0 && docs.length > 0 && /* @__PURE__ */ jsxs14("p", { className: "uploadpanel__queued", children: [
      docs.length,
      " ready \u2014 you can ask about ",
      docs.length === 1 ? "it" : "them",
      " now."
    ] }),
    error && /* @__PURE__ */ jsx15(Notice, { tone: "error", title: "Upload failed", children: error }),
    /* @__PURE__ */ jsx15(
      DocumentChips,
      {
        docs,
        onDelete: remove,
        onView,
        retentionDays: health?.retention_days || 30
      }
    )
  ] });
}

// src/components/chat/Composer.jsx
import { useEffect as useEffect6, useRef as useRef3, useState as useState8 } from "react";
import { motion as motion6 } from "framer-motion";
import { Fragment as Fragment4, jsx as jsx16, jsxs as jsxs15 } from "react/jsx-runtime";
var MAX_HEIGHT = 190;
function Composer({
  onSubmit,
  disabled,
  placeholder,
  onFiles,
  attachAccept = ".pdf",
  attachBusy = false,
  attachTitle,
  attachUnavailable,
  draft
}) {
  const [value, setValue] = useState8("");
  const ref = useRef3(null);
  const fileRef = useRef3(null);
  const canAttach = typeof onFiles === "function";
  useEffect6(() => {
    if (draft) setValue(draft);
  }, [draft]);
  useEffect6(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value]);
  function send() {
    if (!value.trim() || disabled) return;
    onSubmit(value);
    setValue("");
  }
  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
  const canSend = value.trim().length > 0 && !disabled;
  return /* @__PURE__ */ jsxs15("div", { className: "composer", children: [
    /* @__PURE__ */ jsxs15("div", { className: `composer__box ${disabled ? "is-disabled" : ""}`, children: [
      !canAttach && attachUnavailable && /* @__PURE__ */ jsx16(
        "button",
        {
          type: "button",
          className: "composer__attach",
          disabled: true,
          title: attachUnavailable,
          "aria-label": `Attach is unavailable. ${attachUnavailable}`,
          children: /* @__PURE__ */ jsx16(Icon, { name: "plus", size: 17 })
        }
      ),
      canAttach && /* @__PURE__ */ jsxs15(Fragment4, { children: [
        /* @__PURE__ */ jsx16(
          "button",
          {
            type: "button",
            className: "composer__attach",
            onClick: () => fileRef.current?.click(),
            disabled: attachBusy,
            title: attachTitle || "Attach a financial statement (PDF)",
            "aria-label": attachTitle || "Attach a financial statement",
            children: /* @__PURE__ */ jsx16(Icon, { name: attachBusy ? "refresh" : "plus", size: 17 })
          }
        ),
        /* @__PURE__ */ jsx16(
          "input",
          {
            ref: fileRef,
            type: "file",
            className: "composer__file",
            accept: attachAccept,
            multiple: true,
            onChange: (e) => {
              const files = Array.from(e.target.files || []);
              if (files.length) onFiles(files);
              e.target.value = "";
            }
          }
        )
      ] }),
      /* @__PURE__ */ jsx16(
        "textarea",
        {
          ref,
          className: "composer__input",
          rows: 1,
          value,
          onChange: (e) => setValue(e.target.value),
          onKeyDown,
          placeholder,
          disabled,
          "aria-label": "Your question"
        }
      ),
      /* @__PURE__ */ jsx16(
        motion6.button,
        {
          type: "button",
          className: "composer__send",
          onClick: send,
          disabled: !canSend,
          whileHover: canSend ? { scale: 1.05 } : {},
          whileTap: canSend ? { scale: 0.94 } : {},
          transition: { type: "spring", stiffness: 500, damping: 26 },
          "aria-label": "Send",
          children: /* @__PURE__ */ jsx16(Icon, { name: "send", size: 17 })
        }
      )
    ] }),
    /* @__PURE__ */ jsxs15("p", { className: "composer__hint", children: [
      /* @__PURE__ */ jsx16("kbd", { children: "Enter" }),
      " to send \xB7 ",
      /* @__PURE__ */ jsx16("kbd", { children: "Shift" }),
      "+",
      /* @__PURE__ */ jsx16("kbd", { children: "Enter" }),
      " for a new line",
      canAttach && " \xB7 Drop PDFs anywhere in the conversation"
    ] }),
    !canAttach && attachUnavailable && /* @__PURE__ */ jsxs15("p", { className: "composer__hint composer__hint--warn", children: [
      "Uploads are switched off: ",
      attachUnavailable,
      " You can still ask about the general corpus."
    ] })
  ] });
}

// src/components/chat/ChatView.jsx
import { useCallback as useCallback3, useEffect as useEffect8, useRef as useRef4, useState as useState10 } from "react";
import { AnimatePresence as AnimatePresence4, motion as motion8 } from "framer-motion";

// src/components/common/ProgressSteps.jsx
import { useEffect as useEffect7, useState as useState9 } from "react";
import { AnimatePresence as AnimatePresence3, motion as motion7 } from "framer-motion";
import { jsx as jsx17, jsxs as jsxs16 } from "react/jsx-runtime";
function ProgressSteps({ steps, intervalMs = 4200 }) {
  const [current, setCurrent] = useState9(0);
  const [elapsed, setElapsed] = useState9(0);
  useEffect7(() => {
    const tick = setInterval(() => setElapsed((s) => s + 1), 1e3);
    return () => clearInterval(tick);
  }, []);
  useEffect7(() => {
    if (current >= steps.length - 1) return;
    const t = setTimeout(() => setCurrent((i) => i + 1), intervalMs);
    return () => clearTimeout(t);
  }, [current, steps.length, intervalMs]);
  return /* @__PURE__ */ jsxs16("div", { className: "thinking", role: "status", "aria-live": "polite", children: [
    /* @__PURE__ */ jsx17("span", { className: "thinking__glyph", "aria-hidden": "true", children: /* @__PURE__ */ jsx17("span", { className: "thinking__glyph-dot" }) }),
    /* @__PURE__ */ jsx17(AnimatePresence3, { mode: "wait", children: /* @__PURE__ */ jsx17(
      motion7.span,
      {
        className: "thinking__label",
        initial: { opacity: 0, y: 5 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -5 },
        transition: { duration: 0.26, ease: [0.22, 1, 0.36, 1] },
        children: steps[current]
      },
      current
    ) }),
    /* @__PURE__ */ jsxs16("span", { className: "thinking__timer", children: [
      elapsed,
      "s"
    ] })
  ] });
}

// src/components/chat/ChatView.jsx
import { jsx as jsx18, jsxs as jsxs17 } from "react/jsx-runtime";
var PIPELINE_STEPS = [
  "Reading the question\u2026",
  "Searching the documents and the corpus\u2026",
  "Reading the statements and checking the arithmetic\u2026",
  "Reasoning over the evidence\u2026",
  "Validating citations\u2026"
];
var SUGGESTIONS = {
  "financial-statement": [
    "What disclosures does Ind AS 115 require for revenue from contracts with customers?",
    "Summarise the going-concern assessment requirements under SA 570.",
    "How should leases be presented in the balance sheet under Ind AS 116?"
  ],
  "financial-diagnostic-report": [
    "Assess liquidity and solvency trends over the last three years.",
    "Which ratios indicate elevated audit risk this year?",
    "Summarise working-capital movement and its drivers."
  ]
};
function ChatView({
  mode,
  health,
  thread,
  setThread,
  // Optional: only Financial Statements persists a thread. Absent, this
  // component behaves exactly as it did before conversations existed.
  conversationId = null,
  onConversationChange,
  // Opens the document pane for one document. Only wired for FS — see the
  // `mode.id === 'financial-statement'` guard below, same one `UploadPanel`
  // itself is already gated behind.
  onViewDocument
}) {
  const [pending, setPending] = useState10(false);
  const [error, setError] = useState10(null);
  const [failedDraft, setFailedDraft] = useState10(null);
  const scrollRef = useRef4(null);
  const endRef = useRef4(null);
  const uploadRef = useRef4(null);
  const [uploadState, setUploadState] = useState10({ busy: false, available: true, reason: null });
  const onUploadReady = useCallback3((api) => {
    uploadRef.current = api;
    setUploadState((prev) => prev.busy === api.busy && prev.available === api.available && prev.reason === (api.reason || null) ? prev : { busy: api.busy, available: api.available, reason: api.reason || null });
  }, []);
  const [dragDepth, setDragDepth] = useState10(0);
  const canUpload = mode.id === "financial-statement" && uploadState.available;
  const dropFiles = useCallback3((files) => {
    setDragDepth(0);
    if (canUpload) uploadRef.current?.accept(files);
  }, [canUpload]);
  const blocked = health && health.available === false;
  useEffect8(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, pending]);
  async function submit(text) {
    const query = text.trim();
    if (!query || pending) return;
    setError(null);
    setFailedDraft(null);
    setPending(true);
    setThread((prev) => [...prev, { role: "user", text: query, id: `u-${Date.now()}` }]);
    try {
      const result = await runQuery(mode, query, conversationId);
      setThread((prev) => [
        ...prev,
        { role: "assistant", result, id: `a-${Date.now()}` }
      ]);
      if (result?.conversation_id && result.conversation_id !== conversationId) {
        onConversationChange?.(result.conversation_id);
      }
    } catch (err) {
      setError(err.message);
      setFailedDraft(query);
    } finally {
      setPending(false);
    }
  }
  const isEmpty = thread.length === 0;
  return /* @__PURE__ */ jsxs17(
    "div",
    {
      className: `chat ${dragDepth > 0 ? "is-dropping" : ""}`,
      onDragEnter: (e) => {
        if (canUpload && e.dataTransfer?.types?.includes("Files")) {
          e.preventDefault();
          setDragDepth((d) => d + 1);
        }
      },
      onDragOver: (e) => {
        if (canUpload && dragDepth > 0) e.preventDefault();
      },
      onDragLeave: (e) => {
        if (canUpload && dragDepth > 0) {
          e.preventDefault();
          setDragDepth((d) => Math.max(0, d - 1));
        }
      },
      onDrop: (e) => {
        if (!canUpload) return;
        e.preventDefault();
        dropFiles(e.dataTransfer?.files);
      },
      children: [
        dragDepth > 0 && canUpload && /* @__PURE__ */ jsxs17("div", { className: "chat__dropveil", "aria-hidden": "true", children: [
          /* @__PURE__ */ jsx18(Icon, { name: "upload", size: 22 }),
          /* @__PURE__ */ jsx18("span", { children: "Drop financial statements to attach them to this conversation" }),
          /* @__PURE__ */ jsx18("small", { children: "PDF only" })
        ] }),
        /* @__PURE__ */ jsx18("div", { className: "chat__scroll", ref: scrollRef, children: /* @__PURE__ */ jsxs17("div", { className: "chat__inner", children: [
          /* @__PURE__ */ jsxs17(AnimatePresence4, { mode: "popLayout", initial: false, children: [
            isEmpty && !pending && /* @__PURE__ */ jsxs17(
              motion8.div,
              {
                className: "chat__empty",
                initial: { opacity: 0, y: 14 },
                animate: { opacity: 1, y: 0 },
                exit: { opacity: 0, y: -10 },
                transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] },
                children: [
                  /* @__PURE__ */ jsx18(
                    motion8.div,
                    {
                      className: "chat__empty-mark",
                      initial: { scale: 0.86, opacity: 0 },
                      animate: { scale: 1, opacity: 1 },
                      transition: { delay: 0.06, duration: 0.45, ease: [0.22, 1, 0.36, 1] },
                      children: /* @__PURE__ */ jsx18(Icon, { name: "sparkle", size: 26 })
                    }
                  ),
                  /* @__PURE__ */ jsx18("h2", { className: "chat__empty-title", children: mode.label }),
                  /* @__PURE__ */ jsx18("p", { className: "chat__empty-sub", children: mode.description }),
                  !blocked && /* @__PURE__ */ jsx18("div", { className: "chat__suggestions", children: (SUGGESTIONS[mode.id] || []).map((s, i) => /* @__PURE__ */ jsxs17(
                    motion8.button,
                    {
                      type: "button",
                      className: "chat__suggestion",
                      onClick: () => submit(s),
                      initial: { opacity: 0, y: 10 },
                      animate: { opacity: 1, y: 0 },
                      transition: {
                        delay: 0.16 + i * 0.07,
                        duration: 0.38,
                        ease: [0.22, 1, 0.36, 1]
                      },
                      whileHover: { y: -2 },
                      children: [
                        /* @__PURE__ */ jsx18(Icon, { name: "search", size: 15, className: "chat__suggestion-icon" }),
                        /* @__PURE__ */ jsx18("span", { children: s })
                      ]
                    },
                    s
                  )) })
                ]
              },
              "empty"
            ),
            thread.map(
              (msg) => msg.role === "user" ? /* @__PURE__ */ jsx18(
                motion8.div,
                {
                  className: "chat__user",
                  layout: true,
                  initial: { opacity: 0, y: 12 },
                  animate: { opacity: 1, y: 0 },
                  transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] },
                  children: /* @__PURE__ */ jsx18("div", { className: "chat__user-bubble", children: msg.text })
                },
                msg.id
              ) : /* @__PURE__ */ jsx18(
                motion8.div,
                {
                  layout: true,
                  initial: { opacity: 0, y: 14 },
                  animate: { opacity: 1, y: 0 },
                  transition: { duration: 0.42, ease: [0.22, 1, 0.36, 1] },
                  children: /* @__PURE__ */ jsx18(
                    ErrorBoundary,
                    {
                      label: "This answer could not be displayed",
                      resetKey: msg.id,
                      children: /* @__PURE__ */ jsx18(
                        AnswerCard,
                        {
                          result: msg.result,
                          mode,
                          conversationId
                        }
                      )
                    }
                  )
                },
                msg.id
              )
            ),
            pending && /* @__PURE__ */ jsx18(
              motion8.div,
              {
                layout: true,
                initial: { opacity: 0, y: 12 },
                animate: { opacity: 1, y: 0 },
                exit: { opacity: 0, y: -8 },
                transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] },
                children: /* @__PURE__ */ jsx18(ProgressSteps, { steps: PIPELINE_STEPS })
              },
              "pending"
            ),
            error && /* @__PURE__ */ jsx18(motion8.div, { layout: true, children: /* @__PURE__ */ jsxs17(
              Notice,
              {
                tone: "error",
                title: "Something went wrong while preparing this answer.",
                action: failedDraft ? /* @__PURE__ */ jsxs17(
                  "button",
                  {
                    type: "button",
                    className: "btn btn--ghost btn--sm",
                    onClick: () => submit(failedDraft),
                    children: [
                      /* @__PURE__ */ jsx18(Icon, { name: "refresh", size: 13 }),
                      " Try again"
                    ]
                  }
                ) : null,
                children: [
                  error,
                  " Nothing was lost",
                  failedDraft ? " \u2014 your question is still in the box." : "."
                ]
              }
            ) }, "err")
          ] }),
          /* @__PURE__ */ jsx18("div", { ref: endRef })
        ] }) }),
        mode.id === "financial-statement" && /* @__PURE__ */ jsx18(ErrorBoundary, { label: "The upload panel could not be displayed", resetKey: conversationId, children: /* @__PURE__ */ jsx18(
          UploadPanel,
          {
            mode,
            conversationId,
            onConversationChange,
            onReady: onUploadReady,
            onView: onViewDocument
          }
        ) }),
        /* @__PURE__ */ jsx18(
          Composer,
          {
            onSubmit: submit,
            draft: failedDraft,
            disabled: pending || blocked,
            placeholder: blocked ? "This mode is unavailable \u2014 see the notice above." : `Ask about ${mode.short_label.toLowerCase()}\u2026`,
            onFiles: canUpload ? dropFiles : void 0,
            attachUnavailable: mode.id === "financial-statement" && !uploadState.available ? uploadState.reason || "Document upload is not available right now." : void 0,
            attachBusy: uploadState.busy,
            attachTitle: uploadState.busy ? "Converting a document\u2026" : "Attach financial statements (PDF)"
          }
        )
      ]
    }
  );
}

// src/components/ingestion/DocumentPane.jsx
import { useCallback as useCallback4, useEffect as useEffect9, useRef as useRef5, useState as useState11 } from "react";

// src/lib/indian.js
function groupIndian(input) {
  const s = String(input ?? "").trim();
  const m = /^(\(?-?)\s*(?:₹)?\s*(\d[\d,]*)(\.\d*)?(\)?)$/.exec(s);
  if (!m) return s;
  const [, open, whole, dec = "", close] = m;
  const digits = whole.replace(/,/g, "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${open}${grouped}${dec}${close}`;
}
function inWords(amount) {
  const n = Math.abs(Number(amount));
  if (!Number.isFinite(n)) return "";
  if (n >= 1e7) return `\u20B9${trim(n / 1e7)} crore`;
  if (n >= 1e5) return `\u20B9${trim(n / 1e5)} lakh`;
  return `\u20B9${groupIndian(String(n))}`;
}
var trim = (x) => String(Math.round(x * 100) / 100);

// src/components/ingestion/DocumentPane.jsx
import { jsx as jsx19, jsxs as jsxs18 } from "react/jsx-runtime";
function DocumentPane({ mode, conversationId, doc, focus = null, onClose }) {
  const [pageNos, setPageNos] = useState11([]);
  const [pageNo, setPageNo] = useState11(null);
  const [manifestError, setManifestError] = useState11(null);
  const [viewMode, setViewMode] = useState11("image");
  const docId = doc?.doc_id;
  useEffect9(() => {
    let cancelled = false;
    setPageNos([]);
    setPageNo(null);
    setManifestError(null);
    if (!mode || !conversationId || !docId) return void 0;
    fsDocumentPages(mode, conversationId, docId).then(({ pages }) => {
      if (cancelled) return;
      const nos = (pages || []).map((p) => p.page_no).filter((n) => n != null);
      setPageNos(nos);
      setPageNo(nos[0] ?? null);
    }).catch((e) => {
      if (!cancelled) setManifestError(e.message);
    });
    return () => {
      cancelled = true;
    };
  }, [mode, conversationId, docId]);
  useEffect9(() => {
    if (!focus || pageNos.length === 0) return;
    const target = pageNos.includes(focus.page_no) ? focus.page_no : null;
    if (target != null) {
      setPageNo(target);
      setViewMode("text");
    }
  }, [focus, pageNos]);
  const index = pageNos.indexOf(pageNo);
  const goPrev = useCallback4(() => {
    if (index > 0) setPageNo(pageNos[index - 1]);
  }, [index, pageNos]);
  const goNext = useCallback4(() => {
    if (index >= 0 && index < pageNos.length - 1) setPageNo(pageNos[index + 1]);
  }, [index, pageNos]);
  const next = index >= 0 ? pageNos[index + 1] : null;
  const skipped = next != null && pageNo != null ? next - pageNo - 1 : 0;
  const withheld = (doc?.quality?.unreadable_cells || []).filter((c) => c.recovered_text == null).length;
  return /* @__PURE__ */ jsxs18("div", { className: "docpane", children: [
    /* @__PURE__ */ jsxs18("header", { className: "docpane__head", children: [
      /* @__PURE__ */ jsxs18("div", { className: "docpane__headtop", children: [
        /* @__PURE__ */ jsx19(Icon, { name: "doc", size: 15 }),
        /* @__PURE__ */ jsxs18("div", { className: "docpane__titles", children: [
          /* @__PURE__ */ jsx19("span", { className: "docpane__title", title: doc?.filename, children: doc?.filename || "Document" }),
          /* @__PURE__ */ jsx19("span", { className: "docpane__sub", children: [doc?.company, doc?.financial_year && `FY ${doc.financial_year}`].filter(Boolean).join(" \xB7 ") })
        ] }),
        /* @__PURE__ */ jsx19("button", { type: "button", className: "docpane__close", onClick: onClose, "aria-label": "Close", children: "\xD7" })
      ] }),
      /* @__PURE__ */ jsxs18("div", { className: "docpane__headrow", children: [
        /* @__PURE__ */ jsxs18("div", { className: "docpane__toggle", role: "tablist", "aria-label": "View", children: [
          /* @__PURE__ */ jsx19(
            "button",
            {
              type: "button",
              role: "tab",
              "aria-selected": viewMode === "image",
              className: viewMode === "image" ? "is-active" : "",
              onClick: () => setViewMode("image"),
              children: "Image"
            }
          ),
          /* @__PURE__ */ jsx19(
            "button",
            {
              type: "button",
              role: "tab",
              "aria-selected": viewMode === "text",
              className: viewMode === "text" ? "is-active" : "",
              onClick: () => setViewMode("text"),
              children: "Text"
            }
          )
        ] }),
        /* @__PURE__ */ jsxs18("div", { className: "docpane__pills", children: [
          withheld > 0 && /* @__PURE__ */ jsxs18("span", { className: "pill pill--err", children: [
            withheld,
            " unreadable"
          ] }),
          (doc?.recovered_cells || 0) > 0 && /* @__PURE__ */ jsxs18("span", { className: "pill pill--warn", children: [
            doc.recovered_cells,
            " recovered"
          ] }),
          /* @__PURE__ */ jsxs18("span", { className: "pill pill--gold", children: [
            doc?.user_entered_cells || 0,
            " entered by you"
          ] })
        ] })
      ] })
    ] }),
    /* @__PURE__ */ jsxs18("div", { className: "docpane__body", children: [
      manifestError && /* @__PURE__ */ jsx19("p", { className: "docpane__error", children: manifestError }),
      !manifestError && pageNos.length === 0 && /* @__PURE__ */ jsx19("p", { className: "docpane__empty", children: "No processed pages were kept for this document." }),
      !manifestError && pageNo != null && (viewMode === "image" ? /* @__PURE__ */ jsx19(PageImage, { mode, conversationId, docId, pageNo }) : /* @__PURE__ */ jsx19(
        PageText,
        {
          mode,
          conversationId,
          docId,
          pageNo,
          focus: focus && focus.page_no === pageNo ? focus : null,
          onSwitchToImage: () => setViewMode("image")
        }
      ))
    ] }),
    pageNos.length > 0 && /* @__PURE__ */ jsxs18("footer", { className: "docpane__nav", children: [
      /* @__PURE__ */ jsxs18(
        "button",
        {
          type: "button",
          className: "btn btn--ghost btn--sm",
          onClick: goPrev,
          disabled: index <= 0,
          "aria-label": "Previous page",
          children: [
            /* @__PURE__ */ jsx19(Icon, { name: "chevron", size: 14, className: "docpane__nav-prev" }),
            " Previous"
          ]
        }
      ),
      /* @__PURE__ */ jsxs18("span", { className: "docpane__nav-label", children: [
        /* @__PURE__ */ jsxs18("strong", { children: [
          "Page ",
          pageNo,
          ", ",
          index + 1,
          " of ",
          pageNos.length
        ] }),
        next != null && /* @__PURE__ */ jsxs18("small", { children: [
          "Next is page ",
          next,
          skipped > 0 && ` (${skipped === 1 ? `${pageNo + 1} was` : `${skipped} pages were`} blank and skipped)`
        ] })
      ] }),
      /* @__PURE__ */ jsxs18(
        "button",
        {
          type: "button",
          className: "btn btn--ghost btn--sm",
          onClick: goNext,
          disabled: index < 0 || index >= pageNos.length - 1,
          "aria-label": "Next page",
          children: [
            "Next ",
            /* @__PURE__ */ jsx19(Icon, { name: "chevron", size: 14, className: "docpane__nav-next" })
          ]
        }
      )
    ] })
  ] });
}
function PageImage({ mode, conversationId, docId, pageNo }) {
  const [url, setUrl] = useState11(null);
  const [error, setError] = useState11(null);
  useEffect9(() => {
    let objectUrl = null;
    let cancelled = false;
    setUrl(null);
    setError(null);
    fsPageImage(mode, conversationId, docId, pageNo).then((u) => {
      if (cancelled) {
        URL.revokeObjectURL(u);
        return;
      }
      objectUrl = u;
      setUrl(u);
    }).catch((e) => {
      if (!cancelled) setError(e.message);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mode, conversationId, docId, pageNo]);
  if (error) return /* @__PURE__ */ jsx19("p", { className: "docpane__error", children: error });
  if (!url) return /* @__PURE__ */ jsx19("p", { className: "docpane__loading", children: "Loading the page\u2026" });
  return /* @__PURE__ */ jsx19("img", { className: "docpane__img", src: url, alt: `Page ${pageNo}` });
}
function PageText({ mode, conversationId, docId, pageNo, focus, onSwitchToImage }) {
  const [data, setData] = useState11(null);
  const [error, setError] = useState11(null);
  const [refreshKey, setRefreshKey] = useState11(0);
  useEffect9(() => {
    let cancelled = false;
    if (refreshKey === 0) {
      setData(null);
      setError(null);
    }
    fsPageText(mode, conversationId, docId, pageNo).then((d) => {
      if (!cancelled) setData(d);
    }).catch((e) => {
      if (!cancelled) setError(e.message);
    });
    return () => {
      cancelled = true;
    };
  }, [mode, conversationId, docId, pageNo, refreshKey]);
  if (error) return /* @__PURE__ */ jsx19("p", { className: "docpane__error", children: error });
  if (!data) return /* @__PURE__ */ jsx19("p", { className: "docpane__loading", children: "Loading\u2026" });
  const { narrative = [], tables = [] } = data;
  if (narrative.length === 0 && tables.length === 0) {
    return /* @__PURE__ */ jsxs18("div", { className: "docpane__note", role: "status", children: [
      /* @__PURE__ */ jsx19(Icon, { name: "info", size: 16 }),
      /* @__PURE__ */ jsxs18("span", { children: [
        "No text could be read on this page.",
        " ",
        /* @__PURE__ */ jsx19("button", { type: "button", className: "docpane__link", onClick: onSwitchToImage, children: "Switch to Image to view the scan." })
      ] })
    ] });
  }
  return /* @__PURE__ */ jsxs18("div", { className: "docpane__text", children: [
    narrative.map((chunk) => /* @__PURE__ */ jsx19("p", { className: `docpane__chunk docpane__chunk--${chunk.chunk_type || "text"}`, children: chunk.content }, chunk.chunk_id)),
    tables.map((table) => Array.isArray(table.cells) && table.cells.length > 0 ? /* @__PURE__ */ jsx19(
      EditableTable,
      {
        mode,
        conversationId,
        docId,
        pageNo,
        table,
        focus,
        onSaved: () => setRefreshKey((k) => k + 1)
      },
      table.table_id
    ) : /* @__PURE__ */ jsx19(Markdown, { children: table.table_md }, table.table_id))
  ] });
}
function splitRow(line) {
  let s = (line || "").trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}
var sameTable = (stored, wanted) => Boolean(wanted) && (stored === wanted || String(stored).endsWith(`_${wanted}`));
function EditableTable({ mode, conversationId, docId, pageNo, table, focus = null, onSaved }) {
  const lines = (table.table_md || "").split("\n");
  const header = splitRow(lines[0] || "");
  const rows = lines.slice(2).map(splitRow);
  const cellMap = new Map((table.cells || []).map((c) => [`${c.row_index}:${c.col_index}`, c]));
  const initialKey = focus && sameTable(table.table_id, focus.table_id) && cellMap.has(`${focus.row_index}:${focus.col_index}`) ? `${focus.row_index}:${focus.col_index}` : null;
  const [editingKey, setEditingKey] = useState11(initialKey);
  const [showScan, setShowScan] = useState11(false);
  const [savedKey, setSavedKey] = useState11(null);
  const triggerRefs = useRef5({});
  const closeEditor = useCallback4((key) => {
    setEditingKey(null);
    setShowScan(false);
    triggerRefs.current[key]?.focus();
  }, []);
  const editing = editingKey != null ? cellMap.get(editingKey) : null;
  const [rowIdx, colIdx] = editingKey ? editingKey.split(":").map(Number) : [null, null];
  const currentCellText = rowIdx != null ? rows[rowIdx]?.[colIdx] ?? "" : "";
  return /* @__PURE__ */ jsxs18("div", { className: "md", children: [
    /* @__PURE__ */ jsx19("div", { className: "md__table-wrap", children: /* @__PURE__ */ jsxs18("table", { children: [
      /* @__PURE__ */ jsx19("thead", { children: /* @__PURE__ */ jsx19("tr", { children: header.map((h, i) => /* @__PURE__ */ jsx19("th", { children: h }, i)) }) }),
      /* @__PURE__ */ jsx19("tbody", { children: rows.map((row, r) => /* @__PURE__ */ jsx19("tr", { className: /^\s*(total|totai)\b/i.test(row[0] || "") ? "md__total" : void 0, children: row.map((text, c) => {
        const key = `${r}:${c}`;
        const cell = cellMap.get(key);
        if (!cell) return /* @__PURE__ */ jsx19("td", { className: c > 0 && /^[(\-]?[\d,.]+\)?$/.test(text) ? "md__num" : void 0, children: text }, c);
        const { value } = parseFigureCell(text);
        const label = `${cell.state === "unreadable" ? "Unreadable" : cell.state === "recovered" ? "Recovered, unconfirmed" : "User-entered"} figure, row ${cell.row_label || r + 1}, column ${cell.column || c + 1}. Press to ${cell.state === "user_entered" ? "edit or revert" : "enter"}.`;
        const kind = cell.state === "user_entered" ? "you" : cell.state;
        return /* @__PURE__ */ jsx19("td", { className: "md__num", children: /* @__PURE__ */ jsxs18(
          "button",
          {
            type: "button",
            ref: (el) => {
              triggerRefs.current[key] = el;
            },
            className: `fig fig--${kind} docpane__cellbtn ${savedKey === key ? "is-saved" : ""}`,
            "aria-haspopup": "dialog",
            "aria-expanded": editingKey === key,
            "aria-label": label,
            onClick: () => {
              setEditingKey(key);
              setShowScan(false);
            },
            children: [
              /* @__PURE__ */ jsx19("span", { className: "fig__label", children: FIGURE_LABELS[kind] }),
              value && /* @__PURE__ */ jsx19("span", { children: kind === "you" ? groupIndian(value) : value })
            ]
          }
        ) }, c);
      }) }, r)) })
    ] }) }),
    editing && /* @__PURE__ */ jsx19(
      CellEditor,
      {
        mode,
        conversationId,
        docId,
        pageNo,
        tableId: table.table_id,
        rowIndex: rowIdx,
        colIndex: colIdx,
        cell: editing,
        currentCellText,
        showScan,
        onToggleScan: () => setShowScan((v) => !v),
        onClose: () => closeEditor(editingKey),
        onSaved: () => {
          setSavedKey(editingKey);
          setTimeout(() => setSavedKey(null), 900);
          onSaved();
        }
      },
      `${editingKey}:${editing.state}`
    )
  ] });
}
function refusalText(err) {
  switch (err.code) {
    case "not_editable":
      return {
        title: "Not editable.",
        body: "This figure was read and verified from the scan, so it can\u2019t be changed here. If it looks wrong, say so in the chat."
      };
    case "cell_changed":
      return {
        title: "Changed by someone else.",
        body: "This figure was changed after you opened the page. Reload to see the latest value, then try again.",
        reload: true
      };
    case "bad_value":
      return {
        title: "Not an amount.",
        body: "Try 12,859 or 1,50,000.50, or (5,000) for a negative. A blank means \u201Center a figure\u201D."
      };
    case "bad_coordinates":
      return { title: "That cell can\u2019t be found.", body: "The table changed. Reload the page and try again.", reload: true };
    default:
      return { title: "Could not save.", body: err.message };
  }
}
function CellEditor({
  mode,
  conversationId,
  docId,
  pageNo,
  tableId,
  rowIndex,
  colIndex,
  cell,
  currentCellText,
  showScan,
  onToggleScan,
  onClose,
  onSaved
}) {
  const [value, setValue] = useState11(
    cell.state === "user_entered" ? cell.edit?.value ?? "" : cell.recovered_text ?? ""
  );
  const [saving, setSaving] = useState11(false);
  const [error, setError] = useState11(null);
  const dialogRef = useRef5(null);
  const inputRef = useRef5(null);
  useEffect9(() => {
    (inputRef.current || dialogRef.current)?.focus();
  }, []);
  const run = useCallback4(async (action, actionValue) => {
    setSaving(true);
    setError(null);
    try {
      await fsEditCell(mode, conversationId, docId, tableId, {
        rowIndex,
        colIndex,
        expectedCell: currentCellText,
        action,
        ...actionValue !== void 0 ? { value: actionValue } : {}
      });
      onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }, [mode, conversationId, docId, tableId, rowIndex, colIndex, currentCellText, onSaved]);
  const refusal = error ? refusalText(error) : null;
  const footing = cell.edit?.footing;
  return /* @__PURE__ */ jsxs18(
    "div",
    {
      className: "docpane__editor",
      role: "dialog",
      "aria-modal": "false",
      "aria-label": `Edit figure: row ${cell.row_label || ""}, column ${cell.column || ""}`,
      tabIndex: -1,
      ref: dialogRef,
      onKeyDown: (e) => {
        if (e.key === "Escape") onClose();
      },
      children: [
        /* @__PURE__ */ jsxs18("div", { className: "docpane__editor-head", children: [
          /* @__PURE__ */ jsxs18("div", { children: [
            /* @__PURE__ */ jsx19("strong", { children: cell.row_label || "Row" }),
            /* @__PURE__ */ jsxs18("span", { className: "docpane__editor-col", children: [
              "Column: ",
              cell.column
            ] })
          ] }),
          /* @__PURE__ */ jsx19("button", { type: "button", className: "docpane__editor-close", onClick: onClose, "aria-label": "Close editor", children: "\xD7" })
        ] }),
        cell.state === "recovered" && /* @__PURE__ */ jsxs18("p", { className: "docpane__editor-hint is-warn", children: [
          "A second read of the scan shows ",
          /* @__PURE__ */ jsx19("strong", { className: "num", children: cell.recovered_text }),
          cell.confidence ? ` (confidence: ${cell.confidence})` : "",
          ", not confirmed by this column\u2019s arithmetic."
        ] }),
        cell.state === "unreadable" && /* @__PURE__ */ jsx19("p", { className: "docpane__editor-hint", children: "The extraction could not read this figure from the scan." }),
        cell.state === "user_entered" && /* @__PURE__ */ jsxs18("p", { className: "docpane__editor-hint", children: [
          /* @__PURE__ */ jsx19("span", { className: "pill pill--gold", children: "Entered by you" }),
          " ",
          /* @__PURE__ */ jsxs18("span", { className: "docpane__editor-orig", children: [
            "Original marker: ",
            cell.edit?.original_marker?.startsWith("[recovered") ? "recovered" : "unreadable"
          ] })
        ] }),
        /* @__PURE__ */ jsxs18("label", { className: "docpane__editor-field", children: [
          /* @__PURE__ */ jsx19("span", { children: "Amount in \u20B9" }),
          /* @__PURE__ */ jsxs18("span", { className: "docpane__editor-input", children: [
            /* @__PURE__ */ jsx19("span", { "aria-hidden": "true", children: "\u20B9" }),
            /* @__PURE__ */ jsx19(
              "input",
              {
                ref: inputRef,
                type: "text",
                inputMode: "decimal",
                value,
                onChange: (e) => setValue(e.target.value),
                disabled: saving,
                placeholder: "e.g. 1,50,000.50 or (5,000)",
                "aria-invalid": Boolean(refusal)
              }
            )
          ] }),
          /* @__PURE__ */ jsx19("small", { children: "Leave blank if you can\u2019t read it. A nil balance is entered as 0." })
        ] }),
        footing?.verdict === "ties" && /* @__PURE__ */ jsxs18("p", { className: "docpane__editor-ok", role: "status", children: [
          /* @__PURE__ */ jsx19(Icon, { name: "check", size: 14 }),
          " This column\u2019s total ties with your figure included."
        ] }),
        footing?.verdict === "does_not_tie" && /* @__PURE__ */ jsxs18("p", { className: "docpane__editor-warn", role: "status", children: [
          /* @__PURE__ */ jsx19(Icon, { name: "alert", size: 14 }),
          /* @__PURE__ */ jsx19("span", { children: "Not confirmed by this column\u2019s arithmetic (simple check). Your figure is kept and labelled as entered by you." })
        ] }),
        refusal && /* @__PURE__ */ jsxs18("div", { className: "docpane__editor-error", role: "alert", children: [
          /* @__PURE__ */ jsxs18("p", { children: [
            /* @__PURE__ */ jsx19("strong", { children: refusal.title }),
            " ",
            refusal.body
          ] }),
          refusal.reload && /* @__PURE__ */ jsxs18("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: onSaved, children: [
            /* @__PURE__ */ jsx19(Icon, { name: "refresh", size: 13 }),
            " Reload page"
          ] })
        ] }),
        /* @__PURE__ */ jsxs18("div", { className: "docpane__editor-actions", children: [
          /* @__PURE__ */ jsx19(
            "button",
            {
              type: "button",
              className: "btn btn--primary btn--sm",
              disabled: saving || !value.trim(),
              onClick: () => run("set", value),
              children: "Save"
            }
          ),
          cell.state === "recovered" && /* @__PURE__ */ jsx19("button", { type: "button", className: "btn btn--ghost btn--sm", disabled: saving, onClick: () => run("confirm"), children: "Confirm recovered value" }),
          cell.state === "user_entered" && /* @__PURE__ */ jsx19("button", { type: "button", className: "btn btn--ghost btn--sm", disabled: saving, onClick: () => run("revert"), children: "Revert" }),
          /* @__PURE__ */ jsxs18("button", { type: "button", className: "docpane__editor-ghost", onClick: onToggleScan, children: [
            /* @__PURE__ */ jsx19(Icon, { name: "search", size: 13 }),
            " ",
            showScan ? "Hide scan for this page" : "Show scan for this page"
          ] })
        ] }),
        showScan && /* @__PURE__ */ jsx19("div", { className: "docpane__editor-scan", children: /* @__PURE__ */ jsx19(PageImage, { mode, conversationId, docId, pageNo }) })
      ]
    }
  );
}

// src/auth/AuthScreen.jsx
import { useState as useState13 } from "react";
import { motion as motion9 } from "framer-motion";

// src/auth/AuthContext.jsx
import { createContext, useCallback as useCallback5, useContext, useEffect as useEffect10, useMemo, useState as useState12 } from "react";

// src/auth/api.js
async function call(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: {
        ...body ? { "Content-Type": "application/json" } : {},
        ...authHeaders()
      },
      ...body ? { body: JSON.stringify(body) } : {}
    });
  } catch {
    throw new Error(
      "Could not reach the server. Check that the backend is running, then try again."
    );
  }
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const payload = isJson ? await res.json().catch(() => ({})) : {};
  if (!res.ok) {
    let detail = payload.detail;
    if (Array.isArray(detail)) detail = detail[0]?.msg || "That input is not valid.";
    if (typeof detail !== "string") detail = `Request failed (HTTP ${res.status}).`;
    const err = new Error(detail.replace(/^Value error,\s*/i, ""));
    err.status = res.status;
    throw err;
  }
  return payload;
}
function signUp({ username, email, password, displayName }) {
  return call("/api/auth/signup", {
    method: "POST",
    body: { username, email, password, display_name: displayName || null }
  });
}
function signIn({ login, password }) {
  return call("/api/auth/login", { method: "POST", body: { login, password } });
}
function fetchMe() {
  return call("/api/auth/me");
}

// src/auth/AuthContext.jsx
import { jsx as jsx20 } from "react/jsx-runtime";
var AuthContext = createContext(null);
function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>.");
  return ctx;
}
function AuthProvider({ children }) {
  const [user, setUser] = useState12(() => getToken() ? getStoredUser() : null);
  const [status, setStatus] = useState12(() => getToken() ? "checking" : "signed-out");
  const [notice, setNotice] = useState12(null);
  const signOut = useCallback5((reason) => {
    clearAuth();
    setUser(null);
    setStatus("signed-out");
    setNotice(reason || null);
  }, []);
  useEffect10(() => {
    setUnauthorizedHandler((reason) => signOut(reason || "Your session ended. Please sign in again."));
    return () => setUnauthorizedHandler(null);
  }, [signOut]);
  useEffect10(() => {
    if (!getToken()) return void 0;
    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (cancelled) return;
        setStoredUser(me);
        setUser(me);
        setStatus("signed-in");
      } catch (err) {
        if (cancelled) return;
        if (err.status === 401) {
          clearAuth();
          setUser(null);
          setStatus("signed-out");
        } else {
          setStatus("error");
          setNotice(err.message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  const adopt = useCallback5((payload) => {
    setToken(payload.token);
    setStoredUser(payload.user);
    setUser(payload.user);
    setStatus("signed-in");
    setNotice(null);
    return payload.user;
  }, []);
  const signIn2 = useCallback5(async (creds) => adopt(await signIn(creds)), [adopt]);
  const signUp2 = useCallback5(async (details) => adopt(await signUp(details)), [adopt]);
  const value = useMemo(
    () => ({ user, status, notice, signIn: signIn2, signUp: signUp2, signOut, retry: () => window.location.reload() }),
    [user, status, notice, signIn2, signUp2, signOut]
  );
  return /* @__PURE__ */ jsx20(AuthContext.Provider, { value, children });
}

// src/auth/AuthScreen.jsx
import { Fragment as Fragment5, jsx as jsx21, jsxs as jsxs19 } from "react/jsx-runtime";
function Field({ id, label, hint, ...input }) {
  return /* @__PURE__ */ jsxs19("div", { className: "auth__field", children: [
    /* @__PURE__ */ jsx21("label", { className: "auth__label", htmlFor: id, children: label }),
    /* @__PURE__ */ jsx21("input", { id, className: "auth__input", ...input }),
    hint ? /* @__PURE__ */ jsx21("span", { className: "auth__hint", children: hint }) : null
  ] });
}
function LiveCheck() {
  return /* @__PURE__ */ jsxs19("div", { className: "auth__art", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs19("span", { className: "pill pill--ok auth__art-float auth__art-float--top", children: [
      /* @__PURE__ */ jsx21(Icon, { name: "check", size: 12 }),
      " 0 figures guessed"
    ] }),
    /* @__PURE__ */ jsxs19("div", { className: "auth__extract", children: [
      /* @__PURE__ */ jsxs19("header", { className: "auth__extract-head", children: [
        /* @__PURE__ */ jsxs19("div", { children: [
          /* @__PURE__ */ jsx21("strong", { children: "Statement of Profit and Loss" }),
          /* @__PURE__ */ jsx21("small", { children: "Illustrative extract \xB7 amounts in lakh" })
        ] }),
        /* @__PURE__ */ jsxs19("span", { className: "pill pill--ok", children: [
          /* @__PURE__ */ jsx21("span", { className: "dot" }),
          " Live check"
        ] })
      ] }),
      /* @__PURE__ */ jsxs19("ul", { children: [
        /* @__PURE__ */ jsxs19("li", { children: [
          /* @__PURE__ */ jsx21("span", { children: "Employee benefits expense" }),
          /* @__PURE__ */ jsxs19("span", { className: "num", children: [
            "12,859.00 ",
            /* @__PURE__ */ jsx21(Icon, { name: "check", size: 12 })
          ] })
        ] }),
        /* @__PURE__ */ jsxs19("li", { children: [
          /* @__PURE__ */ jsx21("span", { children: "Finance costs" }),
          /* @__PURE__ */ jsx21("span", { className: "fig fig--unreadable", children: /* @__PURE__ */ jsx21("span", { className: "fig__label", children: "unreadable" }) })
        ] }),
        /* @__PURE__ */ jsxs19("li", { children: [
          /* @__PURE__ */ jsx21("span", { children: "Impairment losses" }),
          /* @__PURE__ */ jsxs19("span", { className: "fig fig--recovered", children: [
            /* @__PURE__ */ jsx21("span", { className: "fig__label", children: "recovered ?" }),
            /* @__PURE__ */ jsx21("span", { children: "1,757.00" })
          ] })
        ] }),
        /* @__PURE__ */ jsxs19("li", { children: [
          /* @__PURE__ */ jsx21("span", { children: "Other expenses" }),
          /* @__PURE__ */ jsxs19("span", { className: "num", children: [
            "21,946.85 ",
            /* @__PURE__ */ jsx21(Icon, { name: "check", size: 12 })
          ] })
        ] }),
        /* @__PURE__ */ jsxs19("li", { className: "is-total", children: [
          /* @__PURE__ */ jsx21("span", { children: "Total expenses" }),
          /* @__PURE__ */ jsx21("span", { className: "num", children: "1,17,559.45" })
        ] })
      ] }),
      /* @__PURE__ */ jsxs19("p", { className: "auth__extract-foot", children: [
        /* @__PURE__ */ jsx21(Icon, { name: "check", size: 13 }),
        " Total held: one figure needs you. Nothing is estimated."
      ] })
    ] }),
    /* @__PURE__ */ jsxs19("div", { className: "auth__scanrow", children: [
      /* @__PURE__ */ jsxs19("div", { className: "auth__scan", children: [
        /* @__PURE__ */ jsxs19("header", { children: [
          /* @__PURE__ */ jsx21("span", { children: "Source scan \xB7 page 61" }),
          /* @__PURE__ */ jsx21("span", { className: "pill pill--mute", children: "straightened" })
        ] }),
        /* @__PURE__ */ jsxs19("div", { className: "auth__scan-body", children: [
          /* @__PURE__ */ jsx21("i", {}),
          /* @__PURE__ */ jsx21("i", { className: "short" }),
          /* @__PURE__ */ jsxs19("p", { children: [
            /* @__PURE__ */ jsx21("span", { children: "Finance costs" }),
            /* @__PURE__ */ jsx21("span", { className: "num", children: "26" }),
            /* @__PURE__ */ jsx21("b", {})
          ] }),
          /* @__PURE__ */ jsx21("i", {}),
          /* @__PURE__ */ jsx21("i", { className: "short" })
        ] })
      ] }),
      /* @__PURE__ */ jsxs19("span", { className: "pill auth__cite", children: [
        /* @__PURE__ */ jsx21(Icon, { name: "search", size: 12 }),
        " Every answer cites its page"
      ] })
    ] }),
    /* @__PURE__ */ jsxs19("div", { className: "auth__headline", children: [
      /* @__PURE__ */ jsx21("h2", { children: "Every figure, traced to its page." }),
      /* @__PURE__ */ jsx21("p", { children: "If a number can\u2019t be read, we say so and leave it for you. Nothing is guessed, and anything you enter is always labelled as yours." })
    ] })
  ] });
}
function AuthScreen() {
  const { signIn: signIn2, signUp: signUp2, notice } = useAuth();
  const [mode, setMode] = useState13("signin");
  const [values, setValues] = useState13({
    login: "",
    username: "",
    email: "",
    password: "",
    displayName: ""
  });
  const [error, setError] = useState13(null);
  const [busy, setBusy] = useState13(false);
  const isSignUp = mode === "signup";
  const set = (key) => (e) => setValues((v) => ({ ...v, [key]: e.target.value }));
  function switchTo(next) {
    setMode(next);
    setError(null);
  }
  async function onSubmit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (isSignUp) {
        await signUp2({
          username: values.username.trim(),
          email: values.email.trim(),
          password: values.password,
          displayName: values.displayName.trim()
        });
      } else {
        await signIn2({ login: values.login.trim(), password: values.password });
      }
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs19("div", { className: "auth", children: [
    /* @__PURE__ */ jsxs19(
      motion9.section,
      {
        className: "auth__form-pane",
        initial: { opacity: 0, y: 12 },
        animate: { opacity: 1, y: 0 },
        transition: { duration: 0.32, ease: [0.2, 0.7, 0.2, 1] },
        children: [
          /* @__PURE__ */ jsxs19("div", { className: "auth__brand", children: [
            /* @__PURE__ */ jsxs19("svg", { viewBox: "0 0 32 32", width: "36", height: "36", "aria-hidden": "true", children: [
              /* @__PURE__ */ jsx21("rect", { width: "32", height: "32", rx: "8", fill: "var(--navy-700)" }),
              /* @__PURE__ */ jsx21("path", { d: "M9 11h9M9 16h6M9 21h5", stroke: "#fff", strokeWidth: "2", strokeLinecap: "round" }),
              /* @__PURE__ */ jsx21(
                "path",
                {
                  d: "M17 20.5l3 3 5.5-7",
                  stroke: "var(--gold-600)",
                  strokeWidth: "2.4",
                  strokeLinecap: "round",
                  strokeLinejoin: "round",
                  fill: "none"
                }
              )
            ] }),
            /* @__PURE__ */ jsxs19("p", { className: "auth__name", children: [
              "Artha",
              /* @__PURE__ */ jsx21("span", { children: ".AI" })
            ] }),
            /* @__PURE__ */ jsx21(BrandMark, { height: 28 })
          ] }),
          /* @__PURE__ */ jsxs19("div", { className: "auth__main", children: [
            /* @__PURE__ */ jsx21("p", { className: "auth__eyebrow", children: "Audit workspace" }),
            /* @__PURE__ */ jsx21("h1", { className: "auth__title", children: isSignUp ? "Create your account." : "Welcome back." }),
            /* @__PURE__ */ jsx21("p", { className: "auth__sub", children: isSignUp ? "Your Financial Statements conversations are saved to your account." : "Sign in to ask questions of financial statements, draft auditor\u2019s reports and check trial balances." }),
            notice && !error ? /* @__PURE__ */ jsx21("div", { className: "auth__notice", children: /* @__PURE__ */ jsxs19(Notice, { tone: "info", children: [
              notice,
              " Your conversations are saved."
            ] }) }) : null,
            error ? /* @__PURE__ */ jsx21("div", { className: "auth__notice", children: /* @__PURE__ */ jsx21(Notice, { tone: "error", title: isSignUp ? "Could not create the account" : "Could not sign in", children: error }) }) : null,
            /* @__PURE__ */ jsxs19("div", { className: "auth__tabs", role: "tablist", "aria-label": "Sign in or create an account", children: [
              /* @__PURE__ */ jsx21(
                "button",
                {
                  type: "button",
                  role: "tab",
                  "aria-selected": !isSignUp,
                  className: !isSignUp ? "is-active" : "",
                  onClick: () => switchTo("signin"),
                  children: "Sign in"
                }
              ),
              /* @__PURE__ */ jsx21(
                "button",
                {
                  type: "button",
                  role: "tab",
                  "aria-selected": isSignUp,
                  className: isSignUp ? "is-active" : "",
                  onClick: () => switchTo("signup"),
                  children: "Create account"
                }
              )
            ] }),
            /* @__PURE__ */ jsxs19("form", { className: "auth__form", onSubmit, noValidate: true, children: [
              isSignUp ? /* @__PURE__ */ jsxs19(Fragment5, { children: [
                /* @__PURE__ */ jsx21(
                  Field,
                  {
                    id: "username",
                    label: "Username",
                    value: values.username,
                    onChange: set("username"),
                    autoComplete: "username",
                    required: true,
                    hint: "3\u201340 characters: letters, digits, dot, underscore or hyphen."
                  }
                ),
                /* @__PURE__ */ jsx21(
                  Field,
                  {
                    id: "email",
                    label: "Work email",
                    type: "email",
                    placeholder: "you@firm.in",
                    value: values.email,
                    onChange: set("email"),
                    autoComplete: "email",
                    required: true
                  }
                ),
                /* @__PURE__ */ jsx21(
                  Field,
                  {
                    id: "displayName",
                    label: "Display name",
                    value: values.displayName,
                    onChange: set("displayName"),
                    autoComplete: "name",
                    hint: "Optional \u2014 shown in the sidebar."
                  }
                ),
                /* @__PURE__ */ jsx21(
                  Field,
                  {
                    id: "password",
                    label: "Password",
                    type: "password",
                    value: values.password,
                    onChange: set("password"),
                    autoComplete: "new-password",
                    required: true,
                    hint: "At least 8 characters."
                  }
                )
              ] }) : /* @__PURE__ */ jsxs19(Fragment5, { children: [
                /* @__PURE__ */ jsx21(
                  Field,
                  {
                    id: "login",
                    label: "Work email or username",
                    placeholder: "you@firm.in",
                    value: values.login,
                    onChange: set("login"),
                    autoComplete: "username",
                    required: true
                  }
                ),
                /* @__PURE__ */ jsx21(
                  Field,
                  {
                    id: "password",
                    label: "Password",
                    type: "password",
                    value: values.password,
                    onChange: set("password"),
                    autoComplete: "current-password",
                    required: true
                  }
                )
              ] }),
              /* @__PURE__ */ jsx21("button", { type: "submit", className: "btn btn--primary auth__submit", disabled: busy, children: busy ? "Working\u2026" : isSignUp ? "Create account" : "Sign in" })
            ] })
          ] }),
          /* @__PURE__ */ jsxs19("footer", { className: "auth__foot", children: [
            /* @__PURE__ */ jsxs19("p", { className: "auth__promise", children: [
              /* @__PURE__ */ jsxs19("span", { children: [
                /* @__PURE__ */ jsx21(Icon, { name: "check", size: 13 }),
                " Figures verified by arithmetic"
              ] }),
              /* @__PURE__ */ jsxs19("span", { children: [
                /* @__PURE__ */ jsx21(Icon, { name: "check", size: 13 }),
                " Sources on every answer"
              ] })
            ] }),
            /* @__PURE__ */ jsx21("p", { className: "auth__disclaimer", children: DISCLAIMER })
          ] })
        ]
      }
    ),
    /* @__PURE__ */ jsx21("aside", { className: "auth__art-pane", children: /* @__PURE__ */ jsx21(LiveCheck, {}) })
  ] });
}

// src/components/Sidebar.jsx
import { motion as motion10 } from "framer-motion";
import { jsx as jsx22, jsxs as jsxs20 } from "react/jsx-runtime";
var MODE_ICONS = {
  "statutory-auditor-report": "seal",
  "financial-statement": "ledger",
  "trial-balance": "scales",
  "financial-diagnostic-report": "pulse"
};
function StatusDot({ status }) {
  const title = {
    checking: "Checking availability\u2026",
    ready: "Pipeline ready",
    down: "Pipeline unavailable \u2014 check backend configuration",
    pending: "Branch not integrated yet"
  }[status];
  return /* @__PURE__ */ jsx22("span", { className: `side__status side__status--${status}`, title });
}
function Sidebar({ modes, activeId, onSelect, health, children, rail = false, retentionDays = 30 }) {
  const { user, signOut } = useAuth();
  return /* @__PURE__ */ jsxs20("aside", { className: `side ${rail ? "is-rail" : ""}`, children: [
    /* @__PURE__ */ jsxs20("div", { className: "side__brand", children: [
      /* @__PURE__ */ jsx22("div", { className: "side__mark", children: /* @__PURE__ */ jsxs20("svg", { viewBox: "0 0 32 32", width: "32", height: "32", "aria-hidden": "true", children: [
        /* @__PURE__ */ jsx22("rect", { width: "32", height: "32", rx: "8", fill: "var(--navy-700)" }),
        /* @__PURE__ */ jsx22("path", { d: "M9 11h9M9 16h6M9 21h5", stroke: "#fff", strokeWidth: "2", strokeLinecap: "round" }),
        /* @__PURE__ */ jsx22(
          "path",
          {
            d: "M17 20.5l3 3 5.5-7",
            stroke: "var(--gold-600)",
            strokeWidth: "2.4",
            strokeLinecap: "round",
            strokeLinejoin: "round",
            fill: "none"
          }
        )
      ] }) }),
      /* @__PURE__ */ jsxs20("div", { className: "side__brand-text", children: [
        /* @__PURE__ */ jsxs20("span", { className: "side__name", children: [
          "Artha",
          /* @__PURE__ */ jsx22("span", { className: "side__name-ai", children: ".AI" })
        ] }),
        /* @__PURE__ */ jsx22("span", { className: "side__tag", children: "Audit assistance" })
      ] })
    ] }),
    /* @__PURE__ */ jsxs20("div", { className: "side__scroll", children: [
      /* @__PURE__ */ jsx22("nav", { className: "side__nav", "aria-label": "Modes", children: modes.map((mode, i) => {
        const isActive = mode.id === activeId;
        const state = !mode.integrated ? "pending" : health[mode.id] === void 0 ? "checking" : health[mode.id]?.available ? "ready" : "down";
        return /* @__PURE__ */ jsxs20(
          motion10.button,
          {
            type: "button",
            className: `side__item ${isActive ? "is-active" : ""}`,
            onClick: () => onSelect(mode.id),
            initial: { opacity: 0, x: -10 },
            animate: { opacity: 1, x: 0 },
            transition: { delay: 0.05 + i * 0.05, duration: 0.34, ease: [0.22, 1, 0.36, 1] },
            "aria-current": isActive ? "page" : void 0,
            children: [
              isActive && /* @__PURE__ */ jsx22(
                motion10.span,
                {
                  className: "side__active-bg",
                  layoutId: "side-active",
                  transition: { type: "spring", stiffness: 420, damping: 34 }
                }
              ),
              /* @__PURE__ */ jsxs20("span", { className: "side__item-inner", children: [
                /* @__PURE__ */ jsx22(Icon, { name: MODE_ICONS[mode.id] || "doc", size: 18, className: "side__item-icon" }),
                /* @__PURE__ */ jsxs20("span", { className: "side__item-label", children: [
                  mode.short_label,
                  state === "down" && /* @__PURE__ */ jsx22("span", { className: "side__item-sub", children: "Unavailable" })
                ] }),
                /* @__PURE__ */ jsx22(StatusDot, { status: state })
              ] })
            ]
          },
          mode.id
        );
      }) }),
      children
    ] }),
    retentionDays ? /* @__PURE__ */ jsxs20("p", { className: "side__retention", children: [
      "Uploaded PDFs are kept for ",
      retentionDays,
      " days and removed with their conversation."
    ] }) : null,
    /* @__PURE__ */ jsxs20("div", { className: "side__footer", children: [
      /* @__PURE__ */ jsxs20("div", { className: "side__user", children: [
        /* @__PURE__ */ jsx22("span", { className: "side__avatar", "aria-hidden": "true", children: /* @__PURE__ */ jsx22(Icon, { name: "user", size: 14 }) }),
        /* @__PURE__ */ jsxs20("span", { className: "side__user-text", children: [
          /* @__PURE__ */ jsx22("span", { className: "side__user-name", title: user?.email || "", children: user?.display_name || user?.username || "Signed in" }),
          /* @__PURE__ */ jsx22("button", { type: "button", className: "side__user-out", onClick: () => signOut(), children: "Sign out" })
        ] })
      ] }),
      /* @__PURE__ */ jsx22(
        "button",
        {
          type: "button",
          className: "side__signout",
          onClick: () => signOut(),
          title: "Sign out",
          "aria-label": "Sign out",
          children: /* @__PURE__ */ jsx22(Icon, { name: "logout", size: 15 })
        }
      )
    ] })
  ] });
}

// test/render.jsx
import { jsx as jsx23 } from "react/jsx-runtime";
var MODE = { id: "financial-statement", base_path: "/api/financial-statement", short_label: "FS" };
var LEGACY_RESULT = {
  summary: "Legacy summary",
  final_answer: "Legacy answer",
  evidences_md: "- evidence",
  chunks: [{ index: 1, source: "Ind AS 36, para 12", content: "text", rerank_score: 0.9 }],
  num_tables_searched: 3,
  num_chunks_retrieved: 8,
  elapsed_seconds: 2.5
};
var UPLOAD_RESULT = {
  ...LEGACY_RESULT,
  materiality_legend: {
    markdown: "**Materiality legend**\n\n- Threshold applied: **22,800.20**",
    provisional: true,
    amount: 22800.2
  },
  uploaded_documents: [
    { doc_id: "up_a", filename: "SFS.pdf", financial_year: "2022-23", unreadable_cells: 6 },
    { doc_id: "up_b", filename: "IARSFS.pdf", financial_year: "2022-23", unreadable_cells: 0 }
  ],
  chunks: [{
    index: 1,
    source: "SFS.pdf, page 5",
    content: "table text",
    rerank_score: 0.8,
    doc_id: "up_a",
    table_id: "up_a_t1"
  }]
};
var DOC = {
  doc_id: "up_a",
  filename: "SFS.pdf",
  company: "IDBI Trusteeship Services Ltd",
  pages: 23,
  tables: 9,
  grade: "fair",
  low_grade: "poor",
  identification: {
    financial_year: "2022-23",
    fy_confidence: "high",
    framework: "Ind AS",
    framework_division: "II",
    unresolved_conflicts: ["a conflict"]
  },
  quality: {
    grade: "fair",
    low_grade: "poor",
    vlm_used: false,
    notes: ["a note"],
    unreadable_cells: [{ raw: "(1,757", page_no: 5, row_label: "Disposals", column: "Total", reasons: ["sign_uncertain"] }],
    failed_footings: [{ page_no: 5, subtotal_label: "Balance", printed: 64777, recomputed: null }],
    pages: [{ page_no: 5, grade: "fair", defects: [{ code: "skew_corrected", detail: "rotated 1.7 degrees" }] }]
  },
  coverage: {
    paths: [
      { name: "accounting-policy lookup", state: "unavailable", detail: "no headings", workaround: "ask by note number" },
      { name: "disclosure / notes search", state: "viable", detail: "12 passages" }
    ],
    unavailable: ["accounting-policy lookup"],
    degraded: []
  }
};
var CASES = [
  // The regression: a stored payload with none of the upload fields.
  ["AnswerCard (legacy payload)", /* @__PURE__ */ jsx23(AnswerCard, { result: LEGACY_RESULT, mode: MODE, conversationId: "c1" })],
  ["AnswerCard (upload payload)", /* @__PURE__ */ jsx23(AnswerCard, { result: UPLOAD_RESULT, mode: MODE, conversationId: "c1" })],
  ["AnswerCard (no mode/convo)", /* @__PURE__ */ jsx23(AnswerCard, { result: UPLOAD_RESULT })],
  ["AnswerCard (empty result)", /* @__PURE__ */ jsx23(AnswerCard, { result: {} })],
  ["AnswerCard (null result)", /* @__PURE__ */ jsx23(AnswerCard, { result: null })],
  ["QualityReport (full)", /* @__PURE__ */ jsx23(QualityReport, { doc: DOC, onDelete: () => {
  } })],
  ["QualityReport (no coverage)", /* @__PURE__ */ jsx23(QualityReport, { doc: { ...DOC, coverage: void 0 } })],
  ["QualityReport (bare doc)", /* @__PURE__ */ jsx23(QualityReport, { doc: { doc_id: "x", filename: "x.pdf" } })],
  ["QualityReport (null doc)", /* @__PURE__ */ jsx23(QualityReport, { doc: null })],
  ["IngestProgress (queued)", /* @__PURE__ */ jsx23(IngestProgress, { filename: "a.pdf", stage: "queued", message: "Queued\u2026", fraction: 0 })],
  ["IngestProgress (convert)", /* @__PURE__ */ jsx23(IngestProgress, { filename: "a.pdf", stage: "convert", message: "Detecting", fraction: 0.4 })],
  ["IngestProgress (done)", /* @__PURE__ */ jsx23(IngestProgress, { filename: "a.pdf", stage: "done", message: "Finished", fraction: 1 })],
  ["IngestProgress (error)", /* @__PURE__ */ jsx23(IngestProgress, { filename: "a.pdf", error: "it failed" })],
  ["IngestProgress (no props)", /* @__PURE__ */ jsx23(IngestProgress, {})],
  ["Dropzone", /* @__PURE__ */ jsx23(Dropzone, { onFiles: () => {
  } })],
  ["Dropzone (busy)", /* @__PURE__ */ jsx23(Dropzone, { onFiles: () => {
  }, busy: true })],
  ["CitationViewer", /* @__PURE__ */ jsx23(CitationViewer, { mode: MODE, conversationId: "c1", docId: "up_a", tableId: "t1", caption: "Note 1", onClose: () => {
  } })],
  ["UploadPanel", /* @__PURE__ */ jsx23(UploadPanel, { mode: MODE, conversationId: "c1" })],
  ["UploadPanel (no conversation)", /* @__PURE__ */ jsx23(UploadPanel, { mode: MODE, conversationId: null })],
  ["ErrorBoundary (passthrough)", /* @__PURE__ */ jsx23(ErrorBoundary, { label: "x", children: /* @__PURE__ */ jsx23("span", { children: "ok" }) })],
  ["DocumentChips", /* @__PURE__ */ jsx23(DocumentChips, { docs: [DOC], onDelete: () => {
  } })],
  ["DocumentChips (empty)", /* @__PURE__ */ jsx23(DocumentChips, { docs: [] })],
  ["DocumentChips (null)", /* @__PURE__ */ jsx23(DocumentChips, { docs: null })],
  ["Composer (no attach)", /* @__PURE__ */ jsx23(Composer, { onSubmit: () => {
  }, placeholder: "Ask\u2026" })],
  ["Composer (with attach)", /* @__PURE__ */ jsx23(Composer, { onSubmit: () => {
  }, placeholder: "Ask\u2026", onFiles: () => {
  } })],
  ["Composer (attach busy)", /* @__PURE__ */ jsx23(Composer, { onSubmit: () => {
  }, placeholder: "Ask\u2026", onFiles: () => {
  }, attachBusy: true })],
  // The whole FS chat surface, which is what actually went blank.
  [
    "ChatView (empty thread)",
    /* @__PURE__ */ jsx23(
      ChatView,
      {
        mode: MODE,
        health: { available: true },
        thread: [],
        setThread: () => {
        },
        conversationId: null,
        onConversationChange: () => {
        }
      }
    )
  ],
  [
    "ChatView (with answers)",
    /* @__PURE__ */ jsx23(
      ChatView,
      {
        mode: MODE,
        health: { available: true },
        setThread: () => {
        },
        conversationId: "c1",
        onConversationChange: () => {
        },
        thread: [
          { role: "user", text: "what are total assets?", id: "u1" },
          { role: "assistant", result: UPLOAD_RESULT, id: "a1" },
          { role: "assistant", result: LEGACY_RESULT, id: "a2" }
        ]
      }
    )
  ],
  ["DocumentPane", /* @__PURE__ */ jsx23(DocumentPane, { mode: MODE, conversationId: "c1", doc: DOC, onClose: () => {
  } })],
  ["DocumentPane (no conversation)", /* @__PURE__ */ jsx23(DocumentPane, { mode: MODE, conversationId: null, doc: DOC, onClose: () => {
  } })],
  ["DocumentPane (null doc)", /* @__PURE__ */ jsx23(DocumentPane, { mode: MODE, conversationId: "c1", doc: null, onClose: () => {
  } })],
  // A table with one flagged cell -- the exact shape `page_text` returns
  // when ARTHA_FS_UPLOAD_USER_EDITS is on (see edits.cells_for_table). Only
  // this shape switches a table off the plain <Markdown> path, so this is
  // the one render case that actually exercises the button/badge/ARIA label,
  // not just the "Loading…" placeholder every other DocumentPane case stops
  // at (renderToString runs no effects, so PageText's own fetch never fires).
  ["EditableTable (one flagged cell)", /* @__PURE__ */ jsx23(
    EditableTable,
    {
      mode: MODE,
      conversationId: "c1",
      docId: "up_a",
      pageNo: 5,
      onSaved: () => {
      },
      table: {
        table_id: "up_a_t1",
        table_md: '| Particulars | Amount |\n| --- | --- |\n| Revenue | [unreadable: page 5, table t1, row "Revenue", col "Amount"] |',
        cells: [{
          row_index: 0,
          col_index: 1,
          state: "unreadable",
          marker: '[unreadable: page 5, table t1, row "Revenue", col "Amount"]',
          recovered_text: null,
          confidence: null,
          row_label: "Revenue",
          column: "Amount"
        }]
      }
    }
  ), (html) => {
    if (!html.includes("fig--unreadable")) throw new Error("no state badge class");
    if (!html.includes("<button")) throw new Error("the flagged cell did not render as a button");
    if (!html.includes("Unreadable figure, row Revenue, column Amount")) {
      throw new Error("the ARIA label did not name the row/column");
    }
  }],
  // --- redesign: figure states, tag blocks, drawer, cards, wait bar --------
  ["Markdown (figure states + tags)", /* @__PURE__ */ jsx23(Markdown, { children: [
    "| Particulars | FY 2023-24 | FY 2022-23 |",
    "| --- | --- | --- |",
    "| Revenue | 1,42,318.40 | 1,28,904.10 |",
    '| Finance costs | [unreadable: page 61, table t3, row "Finance costs", col "FY 2023-24"] | 2,318.90 |',
    "| Impairment losses | [recovered 1,757.00; second read, confidence medium, caveat: page 61] | 1,204.50 |",
    "| Other expenses | 21946.85 [user-entered] | 19,880.20 |",
    "| Total expenses | 1,17,559.45 | 1,04,724.55 |",
    "",
    "**RISK FLAG** The impairment figure is a second-reader value."
  ].join(String.fromCharCode(10)) }), (html) => {
    for (const needle of ["fig--unreadable", "fig--recovered", "fig--you", "md__num", "tagblock--risk", "md__total"]) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
    if (!html.includes("1,42,318.40")) throw new Error("a printed figure was reformatted");
    if (html.includes("142,318")) throw new Error("a printed figure was regrouped");
    if (html.includes("21,946.85")) throw new Error("should show the user figure as typed with Indian grouping only");
  }],
  ["QualityReport drawer (Enter / Review / kept until)", /* @__PURE__ */ jsx23(QualityReport, { doc: {
    ...DOC,
    uploaded_at: 179e7,
    user_entered_cells: 1,
    recovered_cells: 1,
    quality: { ...DOC.quality, unreadable_cells: [
      { raw: "", page_no: 61, table_id: "up_a_t1_1", row_label: "Finance costs", column: "FY 2023-24", reasons: ["unreadable_text"], recovered_text: null },
      { raw: "", page_no: 61, table_id: "up_a_t1_1", row_label: "Impairment losses", column: "FY 2023-24", reasons: ["readers_disagree"], recovered_text: "1,757.00", confidence: "medium" }
    ] },
    periods: [{ label: "FY 2023-24", is_comparative: false }, { label: "FY 2022-23", is_comparative: true }]
  }, retentionDays: 30, onEnter: () => {
  }, onOpenPages: () => {
  }, onClose: () => {
  } }), (html) => {
    for (const needle of ["Enter", "Review", "Smudged or unreadable digits", "kept until", "prior-year column", "Quality report"]) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
  }],
  ["DocumentChips (counters)", /* @__PURE__ */ jsx23(DocumentChips, { docs: [{
    ...DOC,
    recovered_cells: 1,
    user_entered_cells: 2,
    quality: { ...DOC.quality }
  }], onView: () => {
  } }), (html) => {
    for (const needle of ["figure withheld", "1 recovered", "did not foot", "2 entered by you", "Open pages"]) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
  }],
  ["IngestProgress (queued ahead)", /* @__PURE__ */ jsx23(IngestProgress, { filename: "b.pdf", status: "queued", ahead: 1 }), (html) => {
    if (!html.includes("Queued \xB7 1 ahead")) throw new Error("queue position missing");
  }],
  ["IngestProgress (password failure)", /* @__PURE__ */ jsx23(IngestProgress, { filename: "c.pdf", error: "c.pdf: file is encrypted", onRetry: () => {
  } }), (html) => {
    if (!html.includes("password-protected") || !html.includes("Retry")) throw new Error("failure not actionable");
  }],
  ["AuthScreen (two panes, no dead link)", /* @__PURE__ */ jsx23(AuthProvider, { children: /* @__PURE__ */ jsx23(AuthScreen, {}) }), (html) => {
    for (const needle of ["Welcome back.", "Every figure, traced to its page.", "Not an official government service"]) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
    if (/forgot/i.test(html)) throw new Error("there is no password reset, so no Forgot link");
  }],
  ["Sidebar (rail + retention)", /* @__PURE__ */ jsx23(AuthProvider, { children: /* @__PURE__ */ jsx23(
    Sidebar,
    {
      rail: false,
      retentionDays: 45,
      activeId: "financial-statement",
      onSelect: () => {
      },
      health: { "trial-balance": { available: false } },
      modes: [
        { id: "financial-statement", short_label: "Financial Statements", integrated: true },
        { id: "trial-balance", short_label: "Trial Balance", integrated: true }
      ]
    }
  ) }), (html) => {
    if (!html.includes("kept for 45 days")) throw new Error("retention note missing");
    if (!html.includes("Unavailable")) throw new Error("unavailable label missing");
  }],
  ["AnswerCard (How this was checked, collapsed)", /* @__PURE__ */ jsx23(AnswerCard, { mode: MODE, conversationId: "c1", result: {
    ...UPLOAD_RESULT,
    rewritten_query: "Compare finance costs FY 2023-24 vs FY 2022-23",
    upload_store_notice: "Your uploaded documents could not be reached.",
    checks: { confidence: "Medium", reduced_from: "High", reduced_reason: "a figure is withheld", tools_used: ["search_corpus"], unsourced: false }
  } }), (html) => {
    for (const needle of ["How this was checked", "Read as:", "could not be reached", "answer"]) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
    if (html.includes("Lowered from")) throw new Error("checks detail should be collapsed");
  }],
  ["pure helpers", /* @__PURE__ */ jsx23("span", {}), () => {
    const eq = (a, b, m) => {
      if (a !== b) throw new Error(`${m}: got ${a}, want ${b}`);
    };
    eq(groupIndian("150000.5"), "1,50,000.5", "lakh grouping");
    eq(groupIndian("12859"), "12,859", "no decimals added");
    eq(groupIndian("(5000)"), "(5,000)", "negative");
    eq(groupIndian("10000000"), "1,00,00,000", "crore");
    eq(inWords(15e4), "\u20B91.5 lakh", "words");
    eq(parseFigureCell("12,859 [user-entered]").kind, "you", "user");
    eq(parseFigureCell("[recovered 1,757.00; second read, confidence low]").value, "1,757.00", "recovered");
    eq(parseFigureCell("[unreadable: page 1]").kind, "unreadable", "unreadable");
    eq(parseFigureCell("460").kind, "plain", "plain");
    eq(stageIndex("convert", 0.5), 3, "stage index");
    eq(stageIndex("identify", 0.97), 7, "finishing");
    eq(friendlyError("x.pdf: PDF is password protected"), "This file is password-protected. Remove the password and attach it again.", "password");
    eq(reasonText("readers_disagree"), "Read differently by the two readers", "reason");
    eq(tablesWithProblems({ unreadable_cells: [{ table_id: "a" }, { table_id: "a" }], recovered_cells: [{ table_id: "b" }] }), 2, "tables");
    if (!String(keptUntil(86400 * 365, 30)).includes("1971")) throw new Error("kept until");
  }]
];
var failed = 0;
for (const [name, element, check] of CASES) {
  try {
    const html = renderToString(element);
    if (typeof html !== "string") throw new Error("did not produce markup");
    check?.(html.replace(/<!-- -->/g, ""));
    console.log(`  ok    ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`  FAIL  ${name}: ${e.message}`);
  }
}
console.log("");
console.log(failed === 0 ? `all ${CASES.length} render cases passed` : `${failed} of ${CASES.length} render cases FAILED`);
process.exit(failed === 0 ? 0 : 1);
