"""Normalize raw listings into a strict schema using Claude Haiku.

This layer is what turns "a pile of links" into at-a-glance fields:
comp range, practice model, call burden, MBSAQIP / robotics mentions,
fellowship requirement — none of which generic job boards surface.

It also enforces the scope filters (relevance, full-time, no locums),
because classification from free text is exactly what an LLM is for.
"""
from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger("extract")

SCHEMA_PROMPT = """You are a strict data extraction engine for physician job postings.
The end user is a fellowship-trained MIS / bariatric / foregut surgeon seeking
FULL-TIME positions (any practice model EXCEPT locum tenens). General surgery
roles are in scope; bariatric / MIS / robotic focus is strongly preferred but
not required.

Given ONE job posting, respond with ONLY a JSON object (no markdown, no prose):

{
  "relevant": bool,            // true for GENERAL SURGERY surgeon roles, including
                               // bariatric / MIS / foregut and acute care general surgery
                               // (NP/PA, admin, and other specialties — orthopedic, oral/OMFS,
                               // vascular, cardiothoracic, plastics, ENT, etc. = false)
  "full_time": bool,           // false for part-time, per-diem, or locums
  "employment_model": "hospital_employed" | "academic" | "private_practice" | "locums" | "unknown",
  "employer": string,          // best-known employer name
  "title": string,             // cleaned title, e.g. "Bariatric & MIS Surgeon"
  "city": string | null,
  "state": string | null,      // 2-letter US state code, null if non-US/unknown
  "comp_min": int | null,      // annual USD, null if undisclosed
  "comp_max": int | null,
  "call_burden": string | null,   // e.g. "1:4 general surgery call", <= 60 chars
  "mbsaqip_mentioned": bool,
  "robotics_mentioned": bool,     // da Vinci / robotic platform mentioned
  "fellowship_required": bool,
  "visa_sponsorship": bool,
  "summary": string            // <= 160 chars, the one-line gist she'd want
}

Rules: convert hourly/monthly comp to annual only if clearly stated. Never invent
numbers. If posting text is too thin to judge relevance, set relevant=false."""


def _call_claude(model: str, posting_text: str) -> dict | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for extraction")
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 700,
                "system": SCHEMA_PROMPT,
                "messages": [{"role": "user", "content": posting_text[:6000]}],
                "temperature": 0,
            },
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"].strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        log.error("extraction failed: %s", e)
        return None


def extract_batch(raw_listings: list[dict], cfg: dict) -> list[dict]:
    model = cfg["extraction"]["model"]
    cap = cfg["extraction"]["max_listings_per_run"]
    excl = set(cfg["filters"]["exclude_models"])
    ft_only = cfg["filters"]["full_time_only"]

    kept: list[dict] = []
    for raw in raw_listings[:cap]:
        blob = "\n".join(filter(None, [
            f"TITLE: {raw['title']}",
            f"EMPLOYER: {raw['employer']}",
            f"LOCATION: {raw['location']}",
            f"POSTED: {raw['posted_hint']}",
            "DESCRIPTION:",
            raw["description"],
        ]))
        ex = _call_claude(model, blob)
        if not ex:
            continue
        if cfg["filters"]["drop_irrelevant"] and not ex.get("relevant"):
            continue
        if ft_only and not ex.get("full_time"):
            continue
        if ex.get("employment_model") in excl:
            continue
        kept.append({**ex, "source": raw["source"], "source_id": raw["source_id"], "url": raw["url"]})
    log.info("Extraction kept %d of %d raw listings", len(kept), min(len(raw_listings), cap))
    return kept
