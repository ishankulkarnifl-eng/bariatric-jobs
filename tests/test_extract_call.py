"""The extraction call against Haiku 5.5's response shape.

Haiku 5.5 thinks by default, so a reply can open with thinking blocks, and it rejects a
non-default temperature. Reading block 0 as the answer would turn every extraction into a
silent failure and an empty digest.
"""

import json

from pipeline import extract


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _patch(monkeypatch, body, sent):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_post(url, headers, json, timeout):  # noqa: A002 - mirrors requests.post
        sent.append(json)
        return _Resp(body)

    monkeypatch.setattr(extract.requests, "post", fake_post)


def test_answer_is_read_from_the_text_block_after_thinking(monkeypatch):
    answer = {"relevant": True, "summary": "Bariatric surgeon, Tampa"}
    body = {
        "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "```json\n" + json.dumps(answer) + "\n```"},
        ],
    }
    sent = []
    _patch(monkeypatch, body, sent)
    assert extract._call_claude("claude-haiku-5-5", "posting") == answer


def test_thinking_only_reply_fails_cleanly(monkeypatch):
    body = {"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": "", "signature": "s"}]}
    _patch(monkeypatch, body, [])
    assert extract._call_claude("claude-haiku-5-5", "posting") is None


def test_request_sends_no_sampling_params_and_low_effort(monkeypatch):
    body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}]}
    sent = []
    _patch(monkeypatch, body, sent)
    extract._call_claude("claude-haiku-5-5", "posting")
    req = sent[0]
    assert "temperature" not in req and "top_p" not in req and "top_k" not in req
    assert req["output_config"] == {"effort": "low"}
    assert req["max_tokens"] >= 2048
