// test/render.jsx
import { renderToString } from "react-dom/server";

// src/components/chat/AnswerCard.jsx
import { useState as useState3 } from "react";
import { AnimatePresence as AnimatePresence2, motion as motion2 } from "framer-motion";

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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { jsx as jsx2 } from "react/jsx-runtime";
function Markdown({ children, className = "" }) {
  if (!children) return null;
  return /* @__PURE__ */ jsx2("div", { className: `md ${className}`, children: /* @__PURE__ */ jsx2(
    ReactMarkdown,
    {
      remarkPlugins: [remarkGfm],
      components: {
        table: ({ node, ...props }) => /* @__PURE__ */ jsx2("div", { className: "md__table-wrap", children: /* @__PURE__ */ jsx2("table", { ...props }) }),
        a: ({ node, ...props }) => /* @__PURE__ */ jsx2("a", { ...props, target: "_blank", rel: "noreferrer noopener" })
      },
      children
    }
  ) });
}

// src/components/common/CopyButton.jsx
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { jsx as jsx3, jsxs } from "react/jsx-runtime";
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
  return /* @__PURE__ */ jsx3(
    "button",
    {
      type: "button",
      className: `btn btn--ghost btn--sm ${className}`,
      onClick: copy,
      disabled: !text,
      "aria-label": copied ? "Copied" : label,
      children: /* @__PURE__ */ jsx3(AnimatePresence, { mode: "wait", initial: false, children: /* @__PURE__ */ jsxs(
        motion.span,
        {
          initial: { opacity: 0, scale: 0.8 },
          animate: { opacity: 1, scale: 1 },
          exit: { opacity: 0, scale: 0.8 },
          transition: { duration: 0.14 },
          style: { display: "flex", alignItems: "center", gap: 6 },
          children: [
            /* @__PURE__ */ jsx3(Icon, { name: copied ? "check" : "copy", size: 14 }),
            copied ? "Copied" : label
          ]
        },
        copied ? "done" : "idle"
      ) })
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
    throw new Error(
      "Could not reach the backend. Start the stack with `docker compose up -d`, or run the gateway directly: uvicorn app.main:app --port $ARTHA_BACKEND_PORT"
    );
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
import { jsx as jsx4, jsxs as jsxs2 } from "react/jsx-runtime";
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
  return /* @__PURE__ */ jsx4(
    "div",
    {
      className: "cite__overlay",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "Source scan",
      onClick: onClose,
      children: /* @__PURE__ */ jsxs2("div", { className: "cite__panel", onClick: (e) => e.stopPropagation(), children: [
        /* @__PURE__ */ jsxs2("header", { className: "cite__head", children: [
          /* @__PURE__ */ jsx4(Icon, { name: "doc", size: 15 }),
          /* @__PURE__ */ jsx4("span", { className: "cite__caption", children: caption || "Source" }),
          /* @__PURE__ */ jsx4("button", { type: "button", className: "cite__close", onClick: onClose, "aria-label": "Close", children: "\xD7" })
        ] }),
        /* @__PURE__ */ jsxs2("div", { className: "cite__body", children: [
          error && /* @__PURE__ */ jsx4("p", { className: "cite__error", children: error }),
          !error && !url && /* @__PURE__ */ jsx4("p", { className: "cite__loading", children: "Loading the scan\u2026" }),
          url && /* @__PURE__ */ jsx4("img", { className: "cite__img", src: url, alt: caption || "Scanned source" })
        ] }),
        /* @__PURE__ */ jsx4("footer", { className: "cite__foot", children: "This is the region of the original scan the figures were read from. Check the printed values against it before relying on them." })
      ] })
    }
  );
}

// src/components/chat/AnswerCard.jsx
import { jsx as jsx5, jsxs as jsxs3 } from "react/jsx-runtime";
function Collapsible({ title, count, icon, children, defaultOpen = false }) {
  const [open, setOpen] = useState3(defaultOpen);
  return /* @__PURE__ */ jsxs3("div", { className: `collapse ${open ? "is-open" : ""}`, children: [
    /* @__PURE__ */ jsxs3("button", { type: "button", className: "collapse__head", onClick: () => setOpen((v) => !v), children: [
      /* @__PURE__ */ jsx5(Icon, { name: icon, size: 15, className: "collapse__icon" }),
      /* @__PURE__ */ jsx5("span", { className: "collapse__title", children: title }),
      count != null && /* @__PURE__ */ jsx5("span", { className: "pill pill--mute", children: count }),
      /* @__PURE__ */ jsx5(
        motion2.span,
        {
          className: "collapse__caret",
          animate: { rotate: open ? 180 : 0 },
          transition: { duration: 0.2, ease: [0.22, 1, 0.36, 1] },
          children: /* @__PURE__ */ jsx5(Icon, { name: "chevron", size: 15 })
        }
      )
    ] }),
    /* @__PURE__ */ jsx5(AnimatePresence2, { initial: false, children: open && /* @__PURE__ */ jsx5(
      motion2.div,
      {
        className: "collapse__body",
        initial: { height: 0, opacity: 0 },
        animate: { height: "auto", opacity: 1 },
        exit: { height: 0, opacity: 0 },
        transition: { duration: 0.26, ease: [0.22, 1, 0.36, 1] },
        children: /* @__PURE__ */ jsx5("div", { className: "collapse__inner", children })
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
  return /* @__PURE__ */ jsxs3("div", { className: "chunk", children: [
    /* @__PURE__ */ jsxs3("div", { className: "chunk__head", children: [
      /* @__PURE__ */ jsx5("span", { className: "chunk__index", children: chunk.index }),
      /* @__PURE__ */ jsx5("span", { className: "chunk__source", children: chunk.source }),
      chunk.hint_matched && /* @__PURE__ */ jsx5("span", { className: "pill pill--navy", children: "hint" }),
      chunk.rerank_score != null && /* @__PURE__ */ jsx5("span", { className: "chunk__score", title: "Reranker score", children: chunk.rerank_score.toFixed(3) }),
      onShowScan && /* @__PURE__ */ jsxs3(
        "button",
        {
          type: "button",
          className: "chunk__scan",
          onClick: onShowScan,
          title: "Show the scanned region this was read from",
          children: [
            /* @__PURE__ */ jsx5(Icon, { name: "search", size: 12 }),
            " scan"
          ]
        }
      )
    ] }),
    /* @__PURE__ */ jsx5("p", { className: "chunk__text", children: shown }),
    isLong && /* @__PURE__ */ jsx5("button", { type: "button", className: "chunk__more", onClick: () => setExpanded((v) => !v), children: expanded ? "Show less" : "Show full extract" })
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
    uploaded_documents: uploaded = []
  } = result || {};
  const [citation, setCitation] = useState3(null);
  const displayAnswer = hideCaveats(answer);
  const copyText = [summary, displayAnswer, evidence].filter(Boolean).join("\n\n");
  return /* @__PURE__ */ jsxs3("article", { className: "answer card", children: [
    /* @__PURE__ */ jsxs3("header", { className: "answer__head", children: [
      /* @__PURE__ */ jsx5("span", { className: "answer__mark", children: /* @__PURE__ */ jsx5(Icon, { name: "sparkle", size: 15 }) }),
      /* @__PURE__ */ jsx5("span", { className: "answer__label", children: "Answer" }),
      /* @__PURE__ */ jsxs3("div", { className: "answer__meta", children: [
        tables > 0 && /* @__PURE__ */ jsxs3("span", { className: "answer__stat", children: [
          tables,
          " tables searched"
        ] }),
        retrieved > 0 && /* @__PURE__ */ jsxs3("span", { className: "answer__stat", children: [
          retrieved,
          " chunks retrieved"
        ] }),
        elapsed > 0 && /* @__PURE__ */ jsxs3("span", { className: "answer__stat", children: [
          elapsed.toFixed(1),
          "s"
        ] })
      ] }),
      /* @__PURE__ */ jsx5(CopyButton, { text: copyText })
    ] }),
    summary && /* @__PURE__ */ jsx5("div", { className: "answer__summary", children: /* @__PURE__ */ jsx5(Markdown, { children: summary }) }),
    displayAnswer && /* @__PURE__ */ jsx5("div", { className: "answer__body", children: /* @__PURE__ */ jsx5(Markdown, { children: displayAnswer }) }),
    !summary && !answer && /* @__PURE__ */ jsx5("p", { className: "answer__blank", children: "The pipeline returned an empty answer." }),
    uploaded.length > 0 && /* @__PURE__ */ jsxs3("div", { className: "answer__uploads", children: [
      /* @__PURE__ */ jsx5(Icon, { name: "doc", size: 13 }),
      /* @__PURE__ */ jsxs3("span", { children: [
        "Answered against ",
        uploaded.length,
        " uploaded document",
        uploaded.length === 1 ? "" : "s",
        ":",
        " ",
        uploaded.map((d) => d.financial_year || d.filename).join(", ")
      ] }),
      uploaded.some((d) => d.unreadable_cells > 0) && /* @__PURE__ */ jsxs3("span", { className: "pill pill--warn", children: [
        uploaded.reduce((n, d) => n + (d.unreadable_cells || 0), 0),
        " figure(s) withheld"
      ] })
    ] }),
    (evidence || chunks.length > 0) && /* @__PURE__ */ jsxs3("div", { className: "answer__extras", children: [
      evidence && /* @__PURE__ */ jsx5(Collapsible, { title: "Evidence", icon: "doc", children: /* @__PURE__ */ jsx5(Markdown, { children: evidence }) }),
      chunks.length > 0 && /* @__PURE__ */ jsx5(Collapsible, { title: "Retrieved sources", icon: "search", count: chunks.length, children: /* @__PURE__ */ jsx5("div", { className: "answer__chunks", children: chunks.map((c) => /* @__PURE__ */ jsx5(
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
    citation && /* @__PURE__ */ jsx5(
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
import { jsx as jsx6, jsxs as jsxs4 } from "react/jsx-runtime";
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
  return /* @__PURE__ */ jsxs4("div", { className: "dropzone-wrap", children: [
    /* @__PURE__ */ jsxs4(
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
          /* @__PURE__ */ jsx6(Icon, { name: "upload", size: 20, className: "dropzone__icon" }),
          /* @__PURE__ */ jsx6("span", { className: "dropzone__label", children: busy ? "Reading\u2026" : label }),
          /* @__PURE__ */ jsx6("span", { className: "dropzone__hint", children: busy ? "One document at a time" : `${hint} \xB7 click or drop` }),
          /* @__PURE__ */ jsx6(
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
    error && /* @__PURE__ */ jsx6("p", { className: "dropzone__error", children: error })
  ] });
}

// src/components/common/ErrorBoundary.jsx
import { Component } from "react";

// src/components/common/Notice.jsx
import { motion as motion3 } from "framer-motion";
import { jsx as jsx7, jsxs as jsxs5 } from "react/jsx-runtime";
var ICONS = { error: "alert", warn: "alert", info: "info" };
function Notice({ tone = "info", title, children, action }) {
  return /* @__PURE__ */ jsxs5(
    motion3.div,
    {
      className: `notice notice--${tone}`,
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] },
      role: tone === "error" ? "alert" : "status",
      children: [
        /* @__PURE__ */ jsx7(Icon, { name: ICONS[tone] || "info", size: 18, className: "notice__icon" }),
        /* @__PURE__ */ jsxs5("div", { className: "notice__body", children: [
          title && /* @__PURE__ */ jsx7("p", { className: "notice__title", children: title }),
          children && /* @__PURE__ */ jsx7("div", { className: "notice__text", children }),
          action && /* @__PURE__ */ jsx7("div", { className: "notice__action", children: action })
        ] })
      ]
    }
  );
}

// src/components/common/ErrorBoundary.jsx
import { jsx as jsx8, jsxs as jsxs6 } from "react/jsx-runtime";
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
    return /* @__PURE__ */ jsxs6(Notice, { tone: "error", title: this.props.label || "Something failed to render", children: [
      /* @__PURE__ */ jsx8("p", { style: { margin: "0 0 6px" }, children: String(error.message || error) }),
      /* @__PURE__ */ jsx8("p", { style: { margin: 0, fontSize: "11.5px", opacity: 0.8 }, children: "The rest of the app is still usable. The full stack is in the browser console." }),
      info?.componentStack && /* @__PURE__ */ jsxs6("details", { style: { marginTop: 8 }, children: [
        /* @__PURE__ */ jsx8("summary", { style: { cursor: "pointer", fontSize: "11.5px" }, children: "Component stack" }),
        /* @__PURE__ */ jsx8("pre", { style: {
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
import { motion as motion4 } from "framer-motion";
import { jsx as jsx9, jsxs as jsxs7 } from "react/jsx-runtime";
var STAGES = [
  { key: "render", label: "Reading the PDF" },
  { key: "precheck", label: "Checking scan quality" },
  { key: "preprocess", label: "Straightening pages" },
  { key: "convert", label: "Detecting layout and tables" },
  { key: "vlm", label: "Second read" },
  { key: "verify", label: "Checking the arithmetic" },
  { key: "identify", label: "Reading year and framework" }
];
function IngestProgress({ filename, stage, message, fraction = 0, error }) {
  const index = STAGES.findIndex((s) => s.key === stage);
  const done = stage === "done";
  return /* @__PURE__ */ jsxs7("div", { className: `ingest ${error ? "is-error" : ""}`, children: [
    /* @__PURE__ */ jsxs7("div", { className: "ingest__head", children: [
      /* @__PURE__ */ jsx9("span", { className: "ingest__file", title: filename, children: filename }),
      /* @__PURE__ */ jsx9("span", { className: "ingest__pct", children: error ? "failed" : done ? "done" : `${Math.round((fraction || 0) * 100)}%` })
    ] }),
    /* @__PURE__ */ jsx9(
      "div",
      {
        className: "ingest__track",
        role: "progressbar",
        "aria-valuenow": Math.round((fraction || 0) * 100),
        "aria-valuemin": 0,
        "aria-valuemax": 100,
        children: /* @__PURE__ */ jsx9(
          motion4.div,
          {
            className: "ingest__bar",
            initial: false,
            animate: { width: `${Math.round((done ? 1 : fraction || 0) * 100)}%` },
            transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] }
          }
        )
      }
    ),
    /* @__PURE__ */ jsx9("p", { className: "ingest__msg", children: error || message || "Queued\u2026" }),
    /* @__PURE__ */ jsx9("ol", { className: "ingest__steps", children: STAGES.map((s, i) => {
      const state = done || index > -1 && i < index ? "is-done" : i === index ? "is-active" : "is-todo";
      return /* @__PURE__ */ jsxs7("li", { className: `ingest__step ${state}`, children: [
        /* @__PURE__ */ jsx9("span", { className: "ingest__dot", "aria-hidden": "true" }),
        /* @__PURE__ */ jsx9("span", { className: "ingest__step-label", children: s.label })
      ] }, s.key);
    }) })
  ] });
}

// src/components/ingestion/QualityReport.jsx
import { useState as useState5 } from "react";
import { Fragment, jsx as jsx10, jsxs as jsxs8 } from "react/jsx-runtime";
var COVERAGE_PILL = {
  viable: "pill--ok",
  degraded: "pill--warn",
  unavailable: "pill--err"
};
var GRADE_PILL = {
  excellent: "pill--ok",
  good: "pill--ok",
  fair: "pill--warn",
  poor: "pill--err",
  unspecified: "pill--mute"
};
function Grade({ value }) {
  return /* @__PURE__ */ jsx10("span", { className: `pill ${GRADE_PILL[value] || "pill--mute"}`, children: value || "unknown" });
}
function Row({ label, children }) {
  return /* @__PURE__ */ jsxs8("div", { className: "qr__row", children: [
    /* @__PURE__ */ jsx10("dt", { className: "qr__key", children: label }),
    /* @__PURE__ */ jsx10("dd", { className: "qr__val", children })
  ] });
}
function QualityReport({ doc, onDelete }) {
  const [open, setOpen] = useState5(false);
  if (!doc) return null;
  const ident = doc.identification || {};
  const quality = doc.quality || {};
  const coverage = doc.coverage || {};
  const paths = coverage.paths || [];
  const blocked = paths.filter((p) => p.state !== "viable");
  const withheld = quality.unreadable_cells || [];
  const failed2 = quality.failed_footings || [];
  const pages = quality.pages || [];
  const problems = pages.filter((p) => p.grade === "poor" || p.grade === "fair");
  return /* @__PURE__ */ jsxs8("section", { className: "qr card", children: [
    /* @__PURE__ */ jsxs8("header", { className: "qr__head", children: [
      /* @__PURE__ */ jsx10(Icon, { name: "doc", size: 15, className: "qr__icon" }),
      /* @__PURE__ */ jsx10("span", { className: "qr__file", title: doc.filename, children: doc.filename }),
      /* @__PURE__ */ jsx10(Grade, { value: doc.grade }),
      onDelete && /* @__PURE__ */ jsx10(
        "button",
        {
          type: "button",
          className: "qr__del",
          onClick: () => onDelete(doc.doc_id),
          title: "Remove this document from the conversation",
          children: /* @__PURE__ */ jsx10(Icon, { name: "trash", size: 14 })
        }
      )
    ] }),
    /* @__PURE__ */ jsxs8("dl", { className: "qr__grid", children: [
      /* @__PURE__ */ jsxs8(Row, { label: "Financial year", children: [
        ident.financial_year || /* @__PURE__ */ jsx10("em", { children: "not readable" }),
        ident.financial_year && /* @__PURE__ */ jsxs8("span", { className: "qr__note", children: [
          " \xB7 inferred from the document, confidence ",
          ident.fy_confidence
        ] })
      ] }),
      /* @__PURE__ */ jsx10(Row, { label: "Entity", children: doc.company || /* @__PURE__ */ jsx10("em", { children: "not readable" }) }),
      /* @__PURE__ */ jsx10(Row, { label: "Framework", children: ident.framework ? `${ident.framework}${ident.framework_division ? ` \xB7 Division ${ident.framework_division}` : ""}` : /* @__PURE__ */ jsx10("em", { children: "not determined" }) }),
      /* @__PURE__ */ jsxs8(Row, { label: "Extent", children: [
        doc.pages,
        " page",
        doc.pages === 1 ? "" : "s",
        " \xB7 ",
        doc.tables,
        " table",
        doc.tables === 1 ? "" : "s"
      ] })
    ] }),
    /* @__PURE__ */ jsxs8("div", { className: "qr__stats", children: [
      /* @__PURE__ */ jsxs8("span", { className: `qr__stat ${withheld.length ? "is-bad" : "is-ok"}`, children: [
        /* @__PURE__ */ jsx10("strong", { children: withheld.length }),
        " figure",
        withheld.length === 1 ? "" : "s",
        " withheld"
      ] }),
      /* @__PURE__ */ jsxs8("span", { className: `qr__stat ${failed2.length ? "is-bad" : "is-ok"}`, children: [
        /* @__PURE__ */ jsx10("strong", { children: failed2.length }),
        " total",
        failed2.length === 1 ? "" : "s",
        " did not foot"
      ] }),
      /* @__PURE__ */ jsxs8("span", { className: "qr__stat", children: [
        "weakest page ",
        /* @__PURE__ */ jsx10(Grade, { value: doc.low_grade })
      ] })
    ] }),
    !quality.vlm_used && /* @__PURE__ */ jsx10(Notice, { tone: "warn", children: "No second independent read was available, so each figure rests on one reader plus its own arithmetic." }),
    (quality.notes || []).map((note, i) => /* @__PURE__ */ jsx10(Notice, { tone: "info", children: note }, i)),
    blocked.length > 0 && /* @__PURE__ */ jsxs8("div", { className: "qr__coverage", children: [
      /* @__PURE__ */ jsx10("h4", { className: "qr__h", children: "What cannot be asked of this document" }),
      /* @__PURE__ */ jsx10("ul", { className: "qr__paths", children: blocked.map((path) => /* @__PURE__ */ jsxs8("li", { children: [
        /* @__PURE__ */ jsx10("span", { className: `pill ${COVERAGE_PILL[path.state] || "pill--mute"}`, children: path.state === "unavailable" ? "not available" : path.state }),
        /* @__PURE__ */ jsx10("span", { className: "qr__path-name", children: path.name }),
        /* @__PURE__ */ jsx10("span", { className: "qr__path-detail", children: path.detail }),
        path.workaround && /* @__PURE__ */ jsx10("span", { className: "qr__path-fix", children: path.workaround })
      ] }, path.name)) }),
      (coverage.unavailable || []).length > 0 && /* @__PURE__ */ jsx10("p", { className: "qr__lead", children: "For these, a \u201Cnot found\u201D result says nothing about whether the filing contains the disclosure \u2014 the scan did not yield what the lookup needs." })
    ] }),
    (ident.unresolved_conflicts || []).map((c, i) => /* @__PURE__ */ jsx10(Notice, { tone: "warn", children: c }, `c${i}`)),
    (withheld.length > 0 || failed2.length > 0 || problems.length > 0) && /* @__PURE__ */ jsxs8("button", { type: "button", className: "qr__toggle", onClick: () => setOpen((v) => !v), children: [
      open ? "Hide detail" : "What could not be read",
      /* @__PURE__ */ jsx10(Icon, { name: "chevron", size: 13, className: open ? "is-open" : "" })
    ] }),
    open && /* @__PURE__ */ jsxs8("div", { className: "qr__detail", children: [
      withheld.length > 0 && /* @__PURE__ */ jsxs8(Fragment, { children: [
        /* @__PURE__ */ jsx10("h4", { className: "qr__h", children: "Figures withheld" }),
        /* @__PURE__ */ jsx10("p", { className: "qr__lead", children: "These could not be read reliably and were removed from the tables the model sees, so no answer can quote them. They are an extraction limitation, not a disclosure the entity failed to make." }),
        /* @__PURE__ */ jsx10("ul", { className: "qr__list", children: withheld.map((c, i) => /* @__PURE__ */ jsxs8("li", { children: [
          /* @__PURE__ */ jsx10("code", { children: c.raw }),
          " \u2014 page ",
          c.page_no,
          ", row \u201C",
          c.row_label,
          "\u201D, column \u201C",
          c.column,
          "\u201D ",
          /* @__PURE__ */ jsxs8("span", { className: "qr__why", children: [
            "(",
            (c.reasons || []).join(", "),
            ")"
          ] })
        ] }, i)) })
      ] }),
      failed2.length > 0 && /* @__PURE__ */ jsxs8(Fragment, { children: [
        /* @__PURE__ */ jsx10("h4", { className: "qr__h", children: "Totals that did not add up" }),
        /* @__PURE__ */ jsx10("p", { className: "qr__lead", children: "Each is either a misread or a genuine error in the filing; the two cannot be told apart from a scan. Check against the original." }),
        /* @__PURE__ */ jsx10("ul", { className: "qr__list", children: failed2.map((f, i) => /* @__PURE__ */ jsxs8("li", { children: [
          "page ",
          f.page_no,
          ", \u201C",
          f.subtotal_label,
          "\u201D \u2014 printed",
          " ",
          f.printed != null ? f.printed.toLocaleString() : "\u2014",
          f.recomputed != null ? `, components sum to ${f.recomputed.toLocaleString()}` : ", components could not be summed"
        ] }, i)) })
      ] }),
      problems.length > 0 && /* @__PURE__ */ jsxs8(Fragment, { children: [
        /* @__PURE__ */ jsx10("h4", { className: "qr__h", children: "Pages needing attention" }),
        /* @__PURE__ */ jsx10("ul", { className: "qr__list", children: problems.map((p) => /* @__PURE__ */ jsxs8("li", { children: [
          "page ",
          p.page_no,
          " ",
          /* @__PURE__ */ jsx10(Grade, { value: p.grade }),
          /* @__PURE__ */ jsx10("ul", { className: "qr__sub", children: (p.defects || []).filter((d) => d.code !== "preprocessed").map((d, i) => /* @__PURE__ */ jsx10("li", { children: d.detail }, i)) })
        ] }, p.page_no)) })
      ] })
    ] })
  ] });
}

// src/components/ingestion/UploadPanel.jsx
import { useCallback as useCallback2, useEffect as useEffect3, useRef as useRef2, useState as useState7 } from "react";

// src/components/ingestion/DocumentChips.jsx
import { useState as useState6 } from "react";
import { jsx as jsx11, jsxs as jsxs9 } from "react/jsx-runtime";
var GRADE_TONE = {
  excellent: "is-ok",
  good: "is-ok",
  fair: "is-warn",
  poor: "is-bad"
};
function DocumentChips({ docs, onDelete, onView }) {
  const [openId, setOpenId] = useState6(null);
  if (!docs || docs.length === 0) return null;
  const open = docs.find((d) => d.doc_id === openId) || null;
  return /* @__PURE__ */ jsxs9("div", { className: "chips", children: [
    /* @__PURE__ */ jsxs9("div", { className: "chips__row", children: [
      docs.map((doc) => {
        const withheld = (doc.quality?.unreadable_cells || []).length;
        const unavailable = (doc.coverage?.unavailable || []).length;
        const label = doc.financial_year || doc.filename;
        const isOpen = doc.doc_id === openId;
        return /* @__PURE__ */ jsxs9(
          "span",
          {
            className: `chip ${GRADE_TONE[doc.grade] || ""} ${isOpen ? "is-open" : ""}`,
            children: [
              /* @__PURE__ */ jsxs9(
                "button",
                {
                  type: "button",
                  className: "chip__main",
                  onClick: () => setOpenId(isOpen ? null : doc.doc_id),
                  title: `${doc.filename}${doc.company ? ` \u2014 ${doc.company}` : ""}`,
                  "aria-expanded": isOpen,
                  children: [
                    /* @__PURE__ */ jsx11(Icon, { name: "doc", size: 13 }),
                    /* @__PURE__ */ jsx11("span", { className: "chip__label", children: label }),
                    withheld > 0 && /* @__PURE__ */ jsx11("span", { className: "chip__badge", title: `${withheld} figure(s) withheld as unreadable`, children: withheld }),
                    unavailable > 0 && /* @__PURE__ */ jsx11(
                      "span",
                      {
                        className: "chip__badge is-muted",
                        title: `${unavailable} lookup(s) cannot work on this document`,
                        children: "!"
                      }
                    )
                  ]
                }
              ),
              onView && /* @__PURE__ */ jsx11(
                "button",
                {
                  type: "button",
                  className: "chip__view",
                  onClick: () => onView(doc),
                  title: "View the processed pages",
                  "aria-label": `View ${doc.filename}`,
                  children: /* @__PURE__ */ jsx11(Icon, { name: "eye", size: 12 })
                }
              ),
              onDelete && /* @__PURE__ */ jsx11(
                "button",
                {
                  type: "button",
                  className: "chip__del",
                  onClick: () => {
                    if (isOpen) setOpenId(null);
                    onDelete(doc.doc_id);
                  },
                  title: "Remove this document from the conversation",
                  "aria-label": `Remove ${doc.filename}`,
                  children: /* @__PURE__ */ jsx11(Icon, { name: "trash", size: 12 })
                }
              )
            ]
          },
          doc.doc_id
        );
      }),
      /* @__PURE__ */ jsx11("span", { className: "chips__note", children: "held in memory for this conversation only" })
    ] }),
    open && /* @__PURE__ */ jsx11("div", { className: "chips__detail", children: /* @__PURE__ */ jsx11(QualityReport, { doc: open }) })
  ] });
}

// src/components/ingestion/UploadPanel.jsx
import { jsx as jsx12, jsxs as jsxs10 } from "react/jsx-runtime";
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
  const [waiting, setWaiting] = useState7(0);
  const [active, setActive] = useState7(null);
  const [error, setError] = useState7(null);
  const convoRef = useRef2(conversationId || null);
  useEffect3(() => {
    const next = conversationId || null;
    if (convoRef.current === next) return;
    convoRef.current = next;
    setDocs([]);
    setError(null);
    onDocumentsChange?.([]);
  }, [conversationId, onDocumentsChange]);
  const mountedRef = useRef2(true);
  useEffect3(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  useEffect3(() => {
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
  useEffect3(() => {
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
          setWaiting(pendingRef.current.length);
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
          if (mountedRef.current) setError(`${file.name}: ${e.message}`);
        } finally {
          if (mountedRef.current) setActive(null);
        }
      }
    } finally {
      drainingRef.current = false;
      if (mountedRef.current) setWaiting(0);
    }
  }, [mode, refresh, onConversationChange]);
  const accept = useCallback2((files) => {
    const all = Array.from(files || []);
    const pdfs = all.filter((f) => (f.name || "").toLowerCase().endsWith(".pdf"));
    const rejected = all.length - pdfs.length;
    setError(rejected > 0 ? `${rejected} file(s) skipped \u2014 only PDF is supported.` : null);
    if (pdfs.length === 0) return;
    pendingRef.current.push(...pdfs);
    setWaiting(pendingRef.current.length);
    drain();
  }, [drain]);
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
  useEffect3(() => {
    onReady?.({ accept, busy, available });
  }, [onReady, accept, busy, available]);
  if (health && !health.available) {
    return /* @__PURE__ */ jsx12("div", { className: "uploadpanel", children: /* @__PURE__ */ jsx12(Notice, { tone: "warn", title: "Document upload is unavailable", children: health.reason }) });
  }
  if (docs.length === 0 && !active && !error && waiting === 0) return null;
  return /* @__PURE__ */ jsxs10("div", { className: "uploadpanel", children: [
    active && /* @__PURE__ */ jsx12(IngestProgress, { ...active }),
    waiting > 0 && /* @__PURE__ */ jsxs10("p", { className: "uploadpanel__queued", children: [
      waiting,
      " more waiting \u2014 documents are converted one at a time."
    ] }),
    error && /* @__PURE__ */ jsx12(Notice, { tone: "error", title: "Upload failed", children: error }),
    /* @__PURE__ */ jsx12(DocumentChips, { docs, onDelete: remove, onView })
  ] });
}

// src/components/chat/Composer.jsx
import { useEffect as useEffect4, useRef as useRef3, useState as useState8 } from "react";
import { motion as motion5 } from "framer-motion";
import { Fragment as Fragment2, jsx as jsx13, jsxs as jsxs11 } from "react/jsx-runtime";
var MAX_HEIGHT = 190;
function Composer({
  onSubmit,
  disabled,
  placeholder,
  onFiles,
  attachAccept = ".pdf",
  attachBusy = false,
  attachTitle
}) {
  const [value, setValue] = useState8("");
  const ref = useRef3(null);
  const fileRef = useRef3(null);
  const canAttach = typeof onFiles === "function";
  useEffect4(() => {
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
  return /* @__PURE__ */ jsxs11("div", { className: "composer", children: [
    /* @__PURE__ */ jsxs11("div", { className: `composer__box ${disabled ? "is-disabled" : ""}`, children: [
      canAttach && /* @__PURE__ */ jsxs11(Fragment2, { children: [
        /* @__PURE__ */ jsx13(
          "button",
          {
            type: "button",
            className: "composer__attach",
            onClick: () => fileRef.current?.click(),
            disabled: attachBusy,
            title: attachTitle || "Attach a financial statement (PDF)",
            "aria-label": attachTitle || "Attach a financial statement",
            children: /* @__PURE__ */ jsx13(Icon, { name: attachBusy ? "refresh" : "plus", size: 17 })
          }
        ),
        /* @__PURE__ */ jsx13(
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
      /* @__PURE__ */ jsx13(
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
      /* @__PURE__ */ jsx13(
        motion5.button,
        {
          type: "button",
          className: "composer__send",
          onClick: send,
          disabled: !canSend,
          whileHover: canSend ? { scale: 1.05 } : {},
          whileTap: canSend ? { scale: 0.94 } : {},
          transition: { type: "spring", stiffness: 500, damping: 26 },
          "aria-label": "Send",
          children: /* @__PURE__ */ jsx13(Icon, { name: "send", size: 17 })
        }
      )
    ] }),
    /* @__PURE__ */ jsxs11("p", { className: "composer__hint", children: [
      /* @__PURE__ */ jsx13("kbd", { children: "Enter" }),
      " to send \xB7 ",
      /* @__PURE__ */ jsx13("kbd", { children: "Shift" }),
      "+",
      /* @__PURE__ */ jsx13("kbd", { children: "Enter" }),
      " for a new line"
    ] })
  ] });
}

// src/components/chat/ChatView.jsx
import { useCallback as useCallback3, useEffect as useEffect6, useRef as useRef4, useState as useState10 } from "react";
import { AnimatePresence as AnimatePresence4, motion as motion7 } from "framer-motion";

// src/components/common/ProgressSteps.jsx
import { useEffect as useEffect5, useState as useState9 } from "react";
import { AnimatePresence as AnimatePresence3, motion as motion6 } from "framer-motion";
import { jsx as jsx14, jsxs as jsxs12 } from "react/jsx-runtime";
function ProgressSteps({ steps, intervalMs = 4200 }) {
  const [current, setCurrent] = useState9(0);
  const [elapsed, setElapsed] = useState9(0);
  useEffect5(() => {
    const tick = setInterval(() => setElapsed((s) => s + 1), 1e3);
    return () => clearInterval(tick);
  }, []);
  useEffect5(() => {
    if (current >= steps.length - 1) return;
    const t = setTimeout(() => setCurrent((i) => i + 1), intervalMs);
    return () => clearTimeout(t);
  }, [current, steps.length, intervalMs]);
  return /* @__PURE__ */ jsxs12("div", { className: "thinking", role: "status", "aria-live": "polite", children: [
    /* @__PURE__ */ jsx14("span", { className: "thinking__glyph", "aria-hidden": "true", children: /* @__PURE__ */ jsx14("span", { className: "thinking__glyph-dot" }) }),
    /* @__PURE__ */ jsx14(AnimatePresence3, { mode: "wait", children: /* @__PURE__ */ jsx14(
      motion6.span,
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
    /* @__PURE__ */ jsxs12("span", { className: "thinking__timer", children: [
      elapsed,
      "s"
    ] })
  ] });
}

// src/components/chat/ChatView.jsx
import { jsx as jsx15, jsxs as jsxs13 } from "react/jsx-runtime";
var PIPELINE_STEPS = [
  "Interpreting the question",
  "Searching the knowledge base",
  "Reranking retrieved evidence",
  "Reasoning over the evidence",
  "Validating citations"
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
  const scrollRef = useRef4(null);
  const endRef = useRef4(null);
  const uploadRef = useRef4(null);
  const [uploadState, setUploadState] = useState10({ busy: false, available: true });
  const onUploadReady = useCallback3((api) => {
    uploadRef.current = api;
    setUploadState((prev) => prev.busy === api.busy && prev.available === api.available ? prev : { busy: api.busy, available: api.available });
  }, []);
  const [dragDepth, setDragDepth] = useState10(0);
  const canUpload = mode.id === "financial-statement" && uploadState.available;
  const dropFiles = useCallback3((files) => {
    setDragDepth(0);
    if (canUpload) uploadRef.current?.accept(files);
  }, [canUpload]);
  const blocked = health && health.available === false;
  useEffect6(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, pending]);
  async function submit(text) {
    const query = text.trim();
    if (!query || pending) return;
    setError(null);
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
    } finally {
      setPending(false);
    }
  }
  const isEmpty = thread.length === 0;
  return /* @__PURE__ */ jsxs13(
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
        dragDepth > 0 && canUpload && /* @__PURE__ */ jsxs13("div", { className: "chat__dropveil", "aria-hidden": "true", children: [
          /* @__PURE__ */ jsx15(Icon, { name: "upload", size: 22 }),
          /* @__PURE__ */ jsx15("span", { children: "Drop financial statements to attach them to this conversation" }),
          /* @__PURE__ */ jsx15("small", { children: "PDF only" })
        ] }),
        /* @__PURE__ */ jsx15("div", { className: "chat__scroll", ref: scrollRef, children: /* @__PURE__ */ jsxs13("div", { className: "chat__inner", children: [
          /* @__PURE__ */ jsxs13(AnimatePresence4, { mode: "popLayout", initial: false, children: [
            isEmpty && !pending && /* @__PURE__ */ jsxs13(
              motion7.div,
              {
                className: "chat__empty",
                initial: { opacity: 0, y: 14 },
                animate: { opacity: 1, y: 0 },
                exit: { opacity: 0, y: -10 },
                transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] },
                children: [
                  /* @__PURE__ */ jsx15(
                    motion7.div,
                    {
                      className: "chat__empty-mark",
                      initial: { scale: 0.86, opacity: 0 },
                      animate: { scale: 1, opacity: 1 },
                      transition: { delay: 0.06, duration: 0.45, ease: [0.22, 1, 0.36, 1] },
                      children: /* @__PURE__ */ jsx15(Icon, { name: "sparkle", size: 26 })
                    }
                  ),
                  /* @__PURE__ */ jsx15("h2", { className: "chat__empty-title", children: mode.label }),
                  /* @__PURE__ */ jsx15("p", { className: "chat__empty-sub", children: mode.description }),
                  !blocked && /* @__PURE__ */ jsx15("div", { className: "chat__suggestions", children: (SUGGESTIONS[mode.id] || []).map((s, i) => /* @__PURE__ */ jsxs13(
                    motion7.button,
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
                        /* @__PURE__ */ jsx15(Icon, { name: "search", size: 15, className: "chat__suggestion-icon" }),
                        /* @__PURE__ */ jsx15("span", { children: s })
                      ]
                    },
                    s
                  )) })
                ]
              },
              "empty"
            ),
            thread.map(
              (msg) => msg.role === "user" ? /* @__PURE__ */ jsx15(
                motion7.div,
                {
                  className: "chat__user",
                  layout: true,
                  initial: { opacity: 0, y: 12 },
                  animate: { opacity: 1, y: 0 },
                  transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] },
                  children: /* @__PURE__ */ jsx15("div", { className: "chat__user-bubble", children: msg.text })
                },
                msg.id
              ) : /* @__PURE__ */ jsx15(
                motion7.div,
                {
                  layout: true,
                  initial: { opacity: 0, y: 14 },
                  animate: { opacity: 1, y: 0 },
                  transition: { duration: 0.42, ease: [0.22, 1, 0.36, 1] },
                  children: /* @__PURE__ */ jsx15(
                    ErrorBoundary,
                    {
                      label: "This answer could not be displayed",
                      resetKey: msg.id,
                      children: /* @__PURE__ */ jsx15(
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
            pending && /* @__PURE__ */ jsx15(
              motion7.div,
              {
                layout: true,
                initial: { opacity: 0, y: 12 },
                animate: { opacity: 1, y: 0 },
                exit: { opacity: 0, y: -8 },
                transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] },
                children: /* @__PURE__ */ jsx15(ProgressSteps, { steps: PIPELINE_STEPS })
              },
              "pending"
            ),
            error && /* @__PURE__ */ jsx15(motion7.div, { layout: true, children: /* @__PURE__ */ jsx15(Notice, { tone: "error", title: "The request could not be completed", children: error }) }, "err")
          ] }),
          /* @__PURE__ */ jsx15("div", { ref: endRef })
        ] }) }),
        mode.id === "financial-statement" && /* @__PURE__ */ jsx15(ErrorBoundary, { label: "The upload panel could not be displayed", resetKey: conversationId, children: /* @__PURE__ */ jsx15(
          UploadPanel,
          {
            mode,
            conversationId,
            onConversationChange,
            onReady: onUploadReady,
            onView: onViewDocument
          }
        ) }),
        /* @__PURE__ */ jsx15(
          Composer,
          {
            onSubmit: submit,
            disabled: pending || blocked,
            placeholder: blocked ? "This mode is unavailable \u2014 see the notice above." : `Ask about ${mode.short_label.toLowerCase()}\u2026`,
            onFiles: canUpload ? dropFiles : void 0,
            attachBusy: uploadState.busy,
            attachTitle: uploadState.busy ? "Converting a document\u2026" : "Attach financial statements (PDF)"
          }
        )
      ]
    }
  );
}

// src/components/ingestion/DocumentPane.jsx
import { useCallback as useCallback4, useEffect as useEffect7, useRef as useRef5, useState as useState11 } from "react";
import { jsx as jsx16, jsxs as jsxs14 } from "react/jsx-runtime";
function DocumentPane({ mode, conversationId, doc, onClose }) {
  const [pageNos, setPageNos] = useState11([]);
  const [pageNo, setPageNo] = useState11(null);
  const [manifestError, setManifestError] = useState11(null);
  const [viewMode, setViewMode] = useState11("image");
  const docId = doc?.doc_id;
  useEffect7(() => {
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
  const index = pageNos.indexOf(pageNo);
  const goPrev = useCallback4(() => {
    if (index > 0) setPageNo(pageNos[index - 1]);
  }, [index, pageNos]);
  const goNext = useCallback4(() => {
    if (index >= 0 && index < pageNos.length - 1) setPageNo(pageNos[index + 1]);
  }, [index, pageNos]);
  return /* @__PURE__ */ jsxs14("div", { className: "docpane", children: [
    /* @__PURE__ */ jsxs14("header", { className: "docpane__head", children: [
      /* @__PURE__ */ jsx16(Icon, { name: "doc", size: 15 }),
      /* @__PURE__ */ jsx16("span", { className: "docpane__title", title: doc?.filename, children: doc?.filename || "Document" }),
      /* @__PURE__ */ jsxs14("div", { className: "docpane__toggle", role: "tablist", "aria-label": "View", children: [
        /* @__PURE__ */ jsx16(
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
        /* @__PURE__ */ jsx16(
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
      /* @__PURE__ */ jsx16("button", { type: "button", className: "docpane__close", onClick: onClose, "aria-label": "Close", children: "\xD7" })
    ] }),
    /* @__PURE__ */ jsxs14("div", { className: "docpane__body", children: [
      manifestError && /* @__PURE__ */ jsx16("p", { className: "docpane__error", children: manifestError }),
      !manifestError && pageNos.length === 0 && /* @__PURE__ */ jsx16("p", { className: "docpane__empty", children: "No processed pages were kept for this document." }),
      !manifestError && pageNo != null && (viewMode === "image" ? /* @__PURE__ */ jsx16(PageImage, { mode, conversationId, docId, pageNo }) : /* @__PURE__ */ jsx16(PageText, { mode, conversationId, docId, pageNo }))
    ] }),
    pageNos.length > 0 && /* @__PURE__ */ jsxs14("footer", { className: "docpane__nav", children: [
      /* @__PURE__ */ jsx16("button", { type: "button", onClick: goPrev, disabled: index <= 0, "aria-label": "Previous page", children: /* @__PURE__ */ jsx16(Icon, { name: "chevron", size: 14, className: "docpane__nav-prev" }) }),
      /* @__PURE__ */ jsxs14("span", { className: "docpane__nav-label", children: [
        "Page ",
        pageNo,
        " \xB7 ",
        index + 1,
        " of ",
        pageNos.length
      ] }),
      /* @__PURE__ */ jsx16(
        "button",
        {
          type: "button",
          onClick: goNext,
          disabled: index < 0 || index >= pageNos.length - 1,
          "aria-label": "Next page",
          children: /* @__PURE__ */ jsx16(Icon, { name: "chevron", size: 14, className: "docpane__nav-next" })
        }
      )
    ] })
  ] });
}
function PageImage({ mode, conversationId, docId, pageNo }) {
  const [url, setUrl] = useState11(null);
  const [error, setError] = useState11(null);
  useEffect7(() => {
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
  if (error) return /* @__PURE__ */ jsx16("p", { className: "docpane__error", children: error });
  if (!url) return /* @__PURE__ */ jsx16("p", { className: "docpane__loading", children: "Loading the page\u2026" });
  return /* @__PURE__ */ jsx16("img", { className: "docpane__img", src: url, alt: `Page ${pageNo}` });
}
function PageText({ mode, conversationId, docId, pageNo }) {
  const [data, setData] = useState11(null);
  const [error, setError] = useState11(null);
  const [refreshKey, setRefreshKey] = useState11(0);
  useEffect7(() => {
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
  if (error) return /* @__PURE__ */ jsx16("p", { className: "docpane__error", children: error });
  if (!data) return /* @__PURE__ */ jsx16("p", { className: "docpane__loading", children: "Loading\u2026" });
  const { narrative = [], tables = [] } = data;
  if (narrative.length === 0 && tables.length === 0) {
    return /* @__PURE__ */ jsx16("p", { className: "docpane__empty", children: "Nothing was extracted from this page." });
  }
  return /* @__PURE__ */ jsxs14("div", { className: "docpane__text", children: [
    narrative.map((chunk) => /* @__PURE__ */ jsx16("p", { className: `docpane__chunk docpane__chunk--${chunk.chunk_type || "text"}`, children: chunk.content }, chunk.chunk_id)),
    tables.map((table) => Array.isArray(table.cells) && table.cells.length > 0 ? /* @__PURE__ */ jsx16(
      EditableTable,
      {
        mode,
        conversationId,
        docId,
        pageNo,
        table,
        onSaved: () => setRefreshKey((k) => k + 1)
      },
      table.table_id
    ) : /* @__PURE__ */ jsx16(Markdown, { children: table.table_md }, table.table_id))
  ] });
}
function splitRow(line) {
  let s = (line || "").trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}
function EditableTable({ mode, conversationId, docId, pageNo, table, onSaved }) {
  const lines = (table.table_md || "").split("\n");
  const header = splitRow(lines[0] || "");
  const rows = lines.slice(2).map(splitRow);
  const cellMap = new Map((table.cells || []).map((c) => [`${c.row_index}:${c.col_index}`, c]));
  const [editingKey, setEditingKey] = useState11(null);
  const [showScan, setShowScan] = useState11(false);
  const triggerRefs = useRef5({});
  const closeEditor = useCallback4((key) => {
    setEditingKey(null);
    setShowScan(false);
    triggerRefs.current[key]?.focus();
  }, []);
  const editing = editingKey != null ? cellMap.get(editingKey) : null;
  const [rowIdx, colIdx] = editingKey ? editingKey.split(":").map(Number) : [null, null];
  const currentCellText = rowIdx != null ? rows[rowIdx]?.[colIdx] ?? "" : "";
  return /* @__PURE__ */ jsxs14("div", { className: "md", children: [
    /* @__PURE__ */ jsx16("div", { className: "md__table-wrap", children: /* @__PURE__ */ jsxs14("table", { children: [
      /* @__PURE__ */ jsx16("thead", { children: /* @__PURE__ */ jsx16("tr", { children: header.map((h, i) => /* @__PURE__ */ jsx16("th", { children: h }, i)) }) }),
      /* @__PURE__ */ jsx16("tbody", { children: rows.map((row, r) => /* @__PURE__ */ jsx16("tr", { children: row.map((text, c) => {
        const key = `${r}:${c}`;
        const cell = cellMap.get(key);
        if (!cell) return /* @__PURE__ */ jsx16("td", { children: text }, c);
        const label = `${cell.state === "unreadable" ? "Unreadable" : cell.state === "recovered" ? "Recovered, unconfirmed" : "User-entered"} figure, row ${cell.row_label || r + 1}, column ${cell.column || c + 1}. Press to ${cell.state === "user_entered" ? "edit or revert" : "enter"}.`;
        return /* @__PURE__ */ jsx16("td", { children: /* @__PURE__ */ jsx16(
          "button",
          {
            type: "button",
            ref: (el) => {
              triggerRefs.current[key] = el;
            },
            className: `docpane__cellbtn docpane__cellbtn--${cell.state}`,
            "aria-haspopup": "dialog",
            "aria-expanded": editingKey === key,
            "aria-label": label,
            onClick: () => {
              setEditingKey(key);
              setShowScan(false);
            },
            children: text
          }
        ) }, c);
      }) }, r)) })
    ] }) }),
    editing && /* @__PURE__ */ jsx16(
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
          onSaved();
          closeEditor(editingKey);
        }
      }
    )
  ] });
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
  useEffect7(() => {
    dialogRef.current?.focus();
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
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }, [mode, conversationId, docId, tableId, rowIndex, colIndex, currentCellText, onSaved]);
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    /* @__PURE__ */ jsxs14(
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
          /* @__PURE__ */ jsxs14("div", { className: "docpane__editor-head", children: [
            /* @__PURE__ */ jsx16("strong", { children: cell.row_label || "Row" }),
            /* @__PURE__ */ jsx16("span", { className: "docpane__editor-col", children: cell.column }),
            /* @__PURE__ */ jsx16("button", { type: "button", className: "docpane__editor-close", onClick: onClose, "aria-label": "Close editor", children: "\xD7" })
          ] }),
          cell.state === "recovered" && /* @__PURE__ */ jsxs14("p", { className: "docpane__editor-hint", children: [
            "A second read of the scan shows ",
            /* @__PURE__ */ jsx16("strong", { children: cell.recovered_text }),
            cell.confidence ? ` (confidence: ${cell.confidence})` : "",
            ", not confirmed by this column's own arithmetic."
          ] }),
          cell.state === "unreadable" && /* @__PURE__ */ jsx16("p", { className: "docpane__editor-hint", children: "The extraction could not read this figure from the scan." }),
          cell.state === "user_entered" && /* @__PURE__ */ jsxs14("p", { className: "docpane__editor-hint", children: [
            "Entered by ",
            cell.edit?.by || "a user",
            ". Originally ",
            cell.edit?.original_marker ? "unreadable or recovered" : "unreadable",
            " before that."
          ] }),
          cell.edit?.footing?.verdict === "does_not_tie" && /* @__PURE__ */ jsxs14("p", { className: "docpane__editor-warn", children: [
            "Not confirmed by this column's arithmetic (simple check): printed total",
            " ",
            cell.edit.footing.printed,
            ", components sum to ",
            cell.edit.footing.computed,
            "."
          ] }),
          cell.edit?.footing?.verdict === "ties" && /* @__PURE__ */ jsx16("p", { className: "docpane__editor-ok", children: "This column's total ties with this figure included." }),
          /* @__PURE__ */ jsxs14("label", { className: "docpane__editor-field", children: [
            /* @__PURE__ */ jsx16("span", { children: "Figure from the scan" }),
            /* @__PURE__ */ jsx16(
              "input",
              {
                type: "text",
                inputMode: "decimal",
                value,
                onChange: (e) => setValue(e.target.value),
                disabled: saving,
                placeholder: "e.g. 12,859 or (5,000)"
              }
            )
          ] }),
          error && /* @__PURE__ */ jsx16("p", { className: "docpane__editor-error", role: "alert", children: error }),
          /* @__PURE__ */ jsxs14("div", { className: "docpane__editor-actions", children: [
            /* @__PURE__ */ jsx16("button", { type: "button", disabled: saving || !value.trim(), onClick: () => run("set", value), children: cell.state === "user_entered" ? "Save change" : "Save" }),
            cell.state === "recovered" && /* @__PURE__ */ jsx16("button", { type: "button", disabled: saving, onClick: () => run("confirm"), children: "Confirm recovered value" }),
            cell.state === "user_entered" && /* @__PURE__ */ jsx16("button", { type: "button", className: "docpane__editor-danger", disabled: saving, onClick: () => run("revert"), children: "Revert" }),
            /* @__PURE__ */ jsx16("button", { type: "button", className: "docpane__editor-ghost", onClick: onToggleScan, children: showScan ? "Hide scan for this page" : "Show scan for this page" })
          ] }),
          showScan && /* @__PURE__ */ jsx16("div", { className: "docpane__editor-scan", children: /* @__PURE__ */ jsx16(PageImage, { mode, conversationId, docId, pageNo }) })
        ]
      }
    )
  );
}

// test/render.jsx
import { jsx as jsx17 } from "react/jsx-runtime";
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
  ["AnswerCard (legacy payload)", /* @__PURE__ */ jsx17(AnswerCard, { result: LEGACY_RESULT, mode: MODE, conversationId: "c1" })],
  ["AnswerCard (upload payload)", /* @__PURE__ */ jsx17(AnswerCard, { result: UPLOAD_RESULT, mode: MODE, conversationId: "c1" })],
  ["AnswerCard (no mode/convo)", /* @__PURE__ */ jsx17(AnswerCard, { result: UPLOAD_RESULT })],
  ["AnswerCard (empty result)", /* @__PURE__ */ jsx17(AnswerCard, { result: {} })],
  ["AnswerCard (null result)", /* @__PURE__ */ jsx17(AnswerCard, { result: null })],
  ["QualityReport (full)", /* @__PURE__ */ jsx17(QualityReport, { doc: DOC, onDelete: () => {
  } })],
  ["QualityReport (no coverage)", /* @__PURE__ */ jsx17(QualityReport, { doc: { ...DOC, coverage: void 0 } })],
  ["QualityReport (bare doc)", /* @__PURE__ */ jsx17(QualityReport, { doc: { doc_id: "x", filename: "x.pdf" } })],
  ["QualityReport (null doc)", /* @__PURE__ */ jsx17(QualityReport, { doc: null })],
  ["IngestProgress (queued)", /* @__PURE__ */ jsx17(IngestProgress, { filename: "a.pdf", stage: "queued", message: "Queued\u2026", fraction: 0 })],
  ["IngestProgress (convert)", /* @__PURE__ */ jsx17(IngestProgress, { filename: "a.pdf", stage: "convert", message: "Detecting", fraction: 0.4 })],
  ["IngestProgress (done)", /* @__PURE__ */ jsx17(IngestProgress, { filename: "a.pdf", stage: "done", message: "Finished", fraction: 1 })],
  ["IngestProgress (error)", /* @__PURE__ */ jsx17(IngestProgress, { filename: "a.pdf", error: "it failed" })],
  ["IngestProgress (no props)", /* @__PURE__ */ jsx17(IngestProgress, {})],
  ["Dropzone", /* @__PURE__ */ jsx17(Dropzone, { onFiles: () => {
  } })],
  ["Dropzone (busy)", /* @__PURE__ */ jsx17(Dropzone, { onFiles: () => {
  }, busy: true })],
  ["CitationViewer", /* @__PURE__ */ jsx17(CitationViewer, { mode: MODE, conversationId: "c1", docId: "up_a", tableId: "t1", caption: "Note 1", onClose: () => {
  } })],
  ["UploadPanel", /* @__PURE__ */ jsx17(UploadPanel, { mode: MODE, conversationId: "c1" })],
  ["UploadPanel (no conversation)", /* @__PURE__ */ jsx17(UploadPanel, { mode: MODE, conversationId: null })],
  ["ErrorBoundary (passthrough)", /* @__PURE__ */ jsx17(ErrorBoundary, { label: "x", children: /* @__PURE__ */ jsx17("span", { children: "ok" }) })],
  ["DocumentChips", /* @__PURE__ */ jsx17(DocumentChips, { docs: [DOC], onDelete: () => {
  } })],
  ["DocumentChips (empty)", /* @__PURE__ */ jsx17(DocumentChips, { docs: [] })],
  ["DocumentChips (null)", /* @__PURE__ */ jsx17(DocumentChips, { docs: null })],
  ["Composer (no attach)", /* @__PURE__ */ jsx17(Composer, { onSubmit: () => {
  }, placeholder: "Ask\u2026" })],
  ["Composer (with attach)", /* @__PURE__ */ jsx17(Composer, { onSubmit: () => {
  }, placeholder: "Ask\u2026", onFiles: () => {
  } })],
  ["Composer (attach busy)", /* @__PURE__ */ jsx17(Composer, { onSubmit: () => {
  }, placeholder: "Ask\u2026", onFiles: () => {
  }, attachBusy: true })],
  // The whole FS chat surface, which is what actually went blank.
  [
    "ChatView (empty thread)",
    /* @__PURE__ */ jsx17(
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
    /* @__PURE__ */ jsx17(
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
  ["DocumentPane", /* @__PURE__ */ jsx17(DocumentPane, { mode: MODE, conversationId: "c1", doc: DOC, onClose: () => {
  } })],
  ["DocumentPane (no conversation)", /* @__PURE__ */ jsx17(DocumentPane, { mode: MODE, conversationId: null, doc: DOC, onClose: () => {
  } })],
  ["DocumentPane (null doc)", /* @__PURE__ */ jsx17(DocumentPane, { mode: MODE, conversationId: "c1", doc: null, onClose: () => {
  } })],
  // A table with one flagged cell -- the exact shape `page_text` returns
  // when ARTHA_FS_UPLOAD_USER_EDITS is on (see edits.cells_for_table). Only
  // this shape switches a table off the plain <Markdown> path, so this is
  // the one render case that actually exercises the button/badge/ARIA label,
  // not just the "Loading…" placeholder every other DocumentPane case stops
  // at (renderToString runs no effects, so PageText's own fetch never fires).
  ["EditableTable (one flagged cell)", /* @__PURE__ */ jsx17(
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
    if (!html.includes("docpane__cellbtn--unreadable")) throw new Error("no state badge class");
    if (!html.includes("<button")) throw new Error("the flagged cell did not render as a button");
    if (!html.includes("Unreadable figure, row Revenue, column Amount")) {
      throw new Error("the ARIA label did not name the row/column");
    }
  }]
];
var failed = 0;
for (const [name, element, check] of CASES) {
  try {
    const html = renderToString(element);
    if (typeof html !== "string") throw new Error("did not produce markup");
    check?.(html);
    console.log(`  ok    ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`  FAIL  ${name}: ${e.message}`);
  }
}
console.log("");
console.log(failed === 0 ? `all ${CASES.length} render cases passed` : `${failed} of ${CASES.length} render cases FAILED`);
process.exit(failed === 0 ? 0 : 1);
