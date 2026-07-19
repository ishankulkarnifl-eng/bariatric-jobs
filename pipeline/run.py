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
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())

    raw = sources.fetch_all(cfg)
    extracted = extract.extract_batch(raw, cfg)

    db = store.load()
    new_today = store.merge(db, extracted, cfg)
    store.save(db)
    log.info("Store: %d active, %d new today", len(store.active(db)), len(new_today))

    digest.send_digest(new_today, cfg, dashboard_url=os.environ.get("DASHBOARD_URL", ""))
    build(db, cfg)
    log.info("Done.")


if __name__ == "__main__":
    main()
