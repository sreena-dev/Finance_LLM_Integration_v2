"""The Gemma client: per-kind concurrency, truncation, retries and the audit log."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm_client  # noqa: E402
from app.llm_client import KIND_STRUCTURE, KIND_VISION, LLMClient, parse_json  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_client.Config, "VLM_ENABLED", True)
    monkeypatch.setattr(llm_client.Config, "VLM_BASE_URL", "http://model.test")
    monkeypatch.setattr(llm_client.Config, "VLM_MODEL", "gemma-test")
    monkeypatch.setattr(llm_client.Config, "LLM_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(llm_client.Config, "LLM_STRUCTURE_CONCURRENCY", 1)
    monkeypatch.setattr(llm_client.Config, "LLM_VISION_CONCURRENCY", 3)
    return LLMClient()


def reply(text, finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


def test_parse_json_handles_fences_prose_and_garbage():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here it is: {"a": {"b": [1, 2]}} hope that helps') == {"a": {"b": [1, 2]}}
    assert parse_json('{"s": "brace } inside"}') == {"s": "brace } inside"}
    assert parse_json("[1, 2]") == [1, 2]
    assert parse_json("no json") is None
    assert parse_json('{"a": ') is None
    assert parse_json(None) is None


def test_a_normal_call_sends_the_image_and_returns_the_text(client):
    seen = {}

    def post(url, payload, headers, timeout):
        seen.update(url=url, payload=payload)
        return reply("hello")

    client.post = post
    out = client.chat(KIND_VISION, "read it", [np.zeros((10, 10), np.uint8)])
    assert out.ok and out.content == "hello"
    assert seen["url"] == "http://model.test/v1/chat/completions"
    content = seen["payload"]["messages"][1]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["text"] == "read it" and seen["payload"]["temperature"] == 0.0


def test_a_reply_cut_off_by_the_token_limit_is_a_failure_not_a_prefix(client):
    client.post = lambda *a: reply('{"rows": [', finish="length")
    out = client.chat(KIND_STRUCTURE, "x")
    assert not out.ok and out.content is None and "token limit" in out.error


def test_one_retry_on_a_transport_error_then_success(client):
    calls = []

    def post(*a):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("reset")
        return reply("ok")

    client.post = post
    assert client.chat(KIND_VISION, "x").content == "ok" and len(calls) == 2


def test_two_transport_errors_give_a_failed_result_not_an_exception(client):
    def post(*a):
        raise TimeoutError("slow")

    client.post = post
    out = client.chat(KIND_VISION, "x")
    assert not out.ok and "TimeoutError" in out.error


def test_unconfigured_client_does_not_call_out(monkeypatch):
    monkeypatch.setattr(llm_client.Config, "VLM_BASE_URL", "")
    c = LLMClient()
    c.post = lambda *a: pytest.fail("must not be called")
    assert not c.chat(KIND_VISION, "x").ok


def _peak_concurrency(client, kind, n):
    active, peak, lock = [0], [0], threading.Lock()

    def post(*a):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return reply("ok")

    client.post = post
    threads = [threading.Thread(target=client.chat, args=(kind, "x")) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return peak[0]


def test_structure_and_vision_calls_have_separate_caps(client):
    assert _peak_concurrency(client, KIND_STRUCTURE, 6) == 1
    assert _peak_concurrency(client, KIND_VISION, 8) == 3


def test_a_slow_structure_call_does_not_block_vision_calls(client):
    release = threading.Event()
    order = []

    def post(url, payload, headers, timeout):
        text = payload["messages"][1]["content"][-1]["text"]
        if text == "slow":
            release.wait(2)
        order.append(text)
        return reply("ok")

    client.post = post
    slow = threading.Thread(target=client.chat, args=(KIND_STRUCTURE, "slow"))
    slow.start()
    time.sleep(0.05)
    client.chat(KIND_VISION, "fast")
    assert order == ["fast"]                      # finished while the structure call was still held
    release.set()
    slow.join()


def test_every_call_is_logged_under_the_documents_folder(client, tmp_path):
    client.post = lambda *a: reply("hello there")
    client.chat(KIND_VISION, "read row", [np.zeros((5, 5), np.uint8)], doc_id="up_x", label="t1_r3")
    client.chat(KIND_STRUCTURE, "structure", doc_id="up_x", label="t1")
    files = sorted((tmp_path / "up_x" / "llm_logs").glob("*.json"))
    assert [f.name for f in files] == ["0001_vision_t1_r3.json", "0002_structure_t1.json"]
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["prompt"] == "read row" and data["response"] == "hello there" and data["images"] == 1


def test_a_log_write_failure_never_fails_the_call(client, monkeypatch):
    client.post = lambda *a: reply("ok")
    monkeypatch.setattr(llm_client.Config, "LLM_LOG_DIR", "\0invalid")
    assert client.chat(KIND_VISION, "x", doc_id="up_y").ok


def test_perceives_requires_the_number_that_was_drawn(client, monkeypatch):
    monkeypatch.setattr(llm_client.random, "choice", lambda seq: "7")
    client.post = lambda *a: reply("Answer: 777777")
    assert client.perceives()
    client.post = lambda *a: reply("I don't see an image")
    assert not client.perceives()
    client.post = lambda *a: reply("123456")
    assert not client.perceives()


def test_oversized_images_are_downscaled_before_sending(monkeypatch):
    import base64

    import cv2

    monkeypatch.setattr(llm_client.Config, "LLM_MAX_IMAGE_SIDE", 1000)
    url = llm_client.encode_image(np.full((3000, 1500), 255, np.uint8))
    raw = base64.b64decode(url.split(",", 1)[1])
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert max(decoded.shape) == 1000
