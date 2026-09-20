"""Nightly orchestrator: fetch → extract → merge → digest → rebuild site.

Run locally:  ANTHROPIC_API_KEY=... SERPAPI_KEY=... python -m pipeline.run
In CI this is invoked by .github/workflows/nightly.yml
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import digest, extract, sources, store  # noqa: E402
from site_builder.build_site import build  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger("run")


def main() -> None:
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))

    raw = sources.fetch_all(cfg)
    extracted = extract.extract_batch(raw, cfg)

    db = store.load()

    # Google hands Bright Data a relative /goto redirect that is dead off a
    # SERP. Resolve it here rather than at fetch time: only listings we have
    # no working link for need it, which is ~15 a night instead of ~180.
    pending = [l for l in extracted if not store.usable_url(l.get("url")) and store.needs_link(db, l)]
    for l in pending:
        l["url"] = sources.resolve_job_url(l.get("url"))
    if pending:
        st = sources.resolve_stats()
        log.info("Apply links: %d needed resolving -> %d resolved, %d failed",
                 len(pending), st["resolved"], st["failed"])
        if st["failed"] and st["failed"] >= st["resolved"]:
            log.warning("Most apply-link resolutions failed; Google is probably "
                        "rate limiting this runner. Those listings will show "
                        "'No link captured' and retry tomorrow.")

    new_today = store.merge(db, extracted, cfg)
    store.save(db)
    log.info("Store: %d active, %d new today", len(store.active(db)), len(new_today))

    digest.send_digest(new_today, cfg, dashboard_url=os.environ.get("DASHBOARD_URL", ""))
    build(db, cfg)
    log.info("Done.")


if __name__ == "__main__":
    main()
