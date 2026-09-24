"""One client for the local Gemma model, used for two different jobs.

* **structure** calls read a table image plus its OCR tokens and return a long
  JSON document describing rows and columns. They hold an endpoint slot for
  ~15-20 s.
* **vision** calls read one cropped row and return a single short line. They
  hold a slot for a second or two.

Each kind has its own concurrency cap. With a single shared limit a burst of
structure calls queues every cheap row read behind it, and the whole pipeline
slows to the pace of its slowest call. The endpoint is also the one the live
chat agent generates against, so both caps default small.

The model is never asked for a digit it will then be believed about: structure
calls return ids and labels, vision calls are compared against OCR before
anything is trusted. This module only moves bytes and keeps an audit trail --
every request and response is logged under ``<dir>/<doc_id>/llm_logs/``.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config

logger = logging.getLogger(__name__)

KIND_STRUCTURE = "structure"
KIND_VISION = "vision"

_SYSTEM = (
    "You are a careful document-layout assistant for scanned financial "
    "statements. You describe structure and transcribe only what is printed. "
    "You never estimate, infer or complete a number."
)


@dataclass
class LLMResult:
    content: str | None
    ok: bool
    error: str | None = None
    finish_reason: str | None = None
    seconds: float = 0.0


def encode_image(image) -> str:
    """A grayscale PNG as a base64 ``data:`` URL (numpy array in)."""
    import cv2

    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    longest = max(image.shape[:2])
    if Config.LLM_MAX_IMAGE_SIDE and longest > Config.LLM_MAX_IMAGE_SIDE:
        scale = Config.LLM_MAX_IMAGE_SIDE / longest
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("could not encode image")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json(content: str | None):
    """The first JSON object or array in `content`, or None.

    Tolerates a code fence and prose around the payload, because models add
    both; it does not tolerate a payload that is not valid JSON once found.
    """
    if not content:
        return None
    text = _strip_fences(content)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        return None
        return None
    return None


def _http_post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    import httpx

    response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


class LLMClient:
    def __init__(self) -> None:
        self._sem = {
            KIND_STRUCTURE: threading.Semaphore(max(1, Config.LLM_STRUCTURE_CONCURRENCY)),
            KIND_VISION: threading.Semaphore(max(1, Config.LLM_VISION_CONCURRENCY)),
        }
        self._log_lock = threading.Lock()
        self._log_counters: dict[str, int] = {}
        #: Swapped out by tests; takes (url, payload, headers, timeout) -> dict.
        self.post = _http_post

    # -- public -----------------------------------------------------------

    def configured(self) -> bool:
        return Config.vlm_configured()

    def chat(
        self,
        kind: str,
        prompt: str,
        images: list | None = None,
        *,
        max_tokens: int | None = None,
        doc_id: str | None = None,
        label: str = "",
    ) -> LLMResult:
        if kind not in self._sem:
            raise ValueError(f"unknown call kind {kind!r}")
        if not self.configured():
            return LLMResult(None, False, "the vision model is not configured")

        content: list[dict] = []
        for image in images or []:
            content.append({"type": "image_url", "image_url": {"url": encode_image(image)}})
        content.append({"type": "text", "text": prompt})
        payload = {
            "model": Config.VLM_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": content},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens or (
                Config.LLM_STRUCTURE_MAX_TOKENS if kind == KIND_STRUCTURE
                else Config.LLM_VISION_MAX_TOKENS
            ),
        }
        headers = {"Content-Type": "application/json"}
        if Config.VLM_API_KEY:
            headers["Authorization"] = f"Bearer {Config.VLM_API_KEY}"
        url = f"{Config.VLM_BASE_URL}/v1/chat/completions"

        result = self._send(kind, url, payload, headers)
        self._log(doc_id, kind, label, prompt, len(images or []), result)
        return result

    # -- internals --------------------------------------------------------

    def _send(self, kind: str, url: str, payload: dict, headers: dict) -> LLMResult:
        started = time.monotonic()
        last_error = "no attempt made"
        with self._sem[kind]:
            for attempt in range(2):
                try:
                    body = self.post(url, payload, headers, Config.VLM_TIMEOUT)
                    choice = (body.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason")
                    text = ((choice.get("message") or {}).get("content")) or ""
                    if finish == "length":
                        # A reply cut off by the token limit is a prefix of an
                        # answer, and a prefix of JSON or of a row of figures
                        # is worse than no answer -- it looks complete.
                        return LLMResult(
                            None, False, "reply hit the token limit", finish,
                            time.monotonic() - started,
                        )
                    return LLMResult(text, True, None, finish, time.monotonic() - started)
                except Exception as exc:  # transport, HTTP status, bad JSON body
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("LLM %s call failed (attempt %d): %s", kind, attempt + 1, last_error)
        return LLMResult(None, False, last_error, None, time.monotonic() - started)

    def _log(self, doc_id, kind, label, prompt, n_images, result: LLMResult) -> None:
        base = Config.LLM_LOG_DIR
        if not base or not doc_id:
            return
        try:
            with self._log_lock:
                n = self._log_counters.get(doc_id, 0) + 1
                self._log_counters[doc_id] = n
            folder = Path(base) / doc_id / "llm_logs"
            folder.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:40]
            (folder / f"{n:04d}_{kind}{'_' + safe if safe else ''}.json").write_text(
                json.dumps({
                    "kind": kind,
                    "label": label,
                    "images": n_images,
                    "prompt": prompt,
                    "ok": result.ok,
                    "error": result.error,
                    "finish_reason": result.finish_reason,
                    "seconds": round(result.seconds, 2),
                    "response": result.content,
                }, indent=2),
                encoding="utf-8",
            )
        except Exception:  # an audit trail must never be the reason a job fails
            logger.debug("could not write LLM log", exc_info=True)

    # -- capability probe ---------------------------------------------------

    def perceives(self, doc_id: str | None = None) -> bool:
        """Does the model actually see images, not merely accept them?

        Measured on the live deployment: the endpoint accepted an image
        printing "HELLO 12345 TOTAL", answered "no image was provided" for a
        real balance sheet, and invented a whole income statement when pushed.
        So the model is asked to read back a random six-digit number drawn
        into a fresh image; a model that cannot see cannot guess it.
        """
        if not self.configured():
            return False
        try:
            import cv2
            import numpy as np
        except ImportError:
            return False
        number = "".join(random.choice("0123456789") for _ in range(6))
        image = np.full((90, 360), 255, np.uint8)
        cv2.putText(image, number, (20, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 3)
        reply = self.chat(
            KIND_VISION,
            "Read the number printed in this image. Reply with the digits only.",
            [image], max_tokens=20, doc_id=doc_id, label="perceive",
        )
        digits = re.sub(r"\D", "", reply.content or "")
        return reply.ok and digits == number


CLIENT = LLMClient()
