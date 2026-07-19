"""Email digest of NEW listings via Resend. Sent only when there is news —
an empty inbox is a feature. For a surgeon with a full OR schedule this
digest, not the dashboard, will likely deliver most of the value.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("digest")


def _fmt_comp(l: dict) -> str:
    lo, hi = l.get("comp_min"), l.get("comp_max")
    if lo and hi:
        return f"${lo//1000}k–${hi//1000}k"
    if lo or hi:
        return f"~${(lo or hi)//1000}k"
    return "Comp undisclosed"


def send_digest(new_listings: list[dict], cfg: dict, dashboard_url: str = "") -> None:
    if not cfg["digest"]["enabled"] or not new_listings:
        log.info("Digest skipped (%d new listings)", len(new_listings))
        return
    key = os.environ.get("RESEND_KEY")
    if not key:
        log.warning("RESEND_KEY not set — skipping digest")
        return

    rows = []
    for l in sorted(new_listings, key=lambda x: x.get("state") or "zz"):
        loc = ", ".join(filter(None, [l.get("city"), l.get("state")])) or "Location TBD"
        tags = " · ".join(filter(None, [
            (l.get("employment_model") or "").replace("_", " "),
            "MBSAQIP" if l.get("mbsaqip_mentioned") else "",
            "robotics" if l.get("robotics_mentioned") else "",
        ]))
        link = (l.get("urls") or [""])[0]
        rows.append(
            f"<tr><td style='padding:10px 14px;border-bottom:1px solid #dce4e1'>"
            f"<div style='font-weight:600;color:#122b29'>{l.get('employer','')}</div>"
            f"<div style='color:#3f5a56;font-size:14px'>{l.get('title','')} — {loc}</div>"
            f"<div style='color:#0e7c66;font-size:13px;margin-top:2px'>{_fmt_comp(l)} · {tags}</div>"
            f"<div style='margin-top:4px'><a href='{link}' style='color:#0e7c66;font-size:13px'>View &amp; apply →</a></div>"
            f"</td></tr>"
        )
    footer = (
        f"<p style='color:#6b7f7b;font-size:12px'>"
        f"{f'<a href=\"{dashboard_url}\" style=\"color:#0e7c66\">Open the full dashboard</a>' if dashboard_url else ''}"
        f"</p>"
    )
    html = (
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;max-width:560px'>"
        f"<h2 style='color:#122b29'>{len(new_listings)} new bariatric surgery "
        f"position{'s' if len(new_listings) != 1 else ''}</h2>"
        f"<table style='border-collapse:collapse;width:100%'>{''.join(rows)}</table>"
        f"{footer}</div>"
    )
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "from": cfg["digest"]["from"],
                "to": cfg["digest"]["to"],
                "subject": f"{cfg['digest']['subject_prefix']} {len(new_listings)} new listing"
                           f"{'s' if len(new_listings) != 1 else ''}",
                "html": html,
            },
            timeout=30,
        )
        r.raise_for_status()
        log.info("Digest sent to %s", cfg["digest"]["to"])
    except Exception as e:  # noqa: BLE001
        log.error("Digest send failed: %s", e)
