"""Render the dashboard: template.html + data/listings.json → docs/index.html.
Runnable standalone (python -m site_builder.build_site) or from the pipeline.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(db: dict, cfg: dict) -> Path:
    listings = [{**l, "key": k} for k, l in db["listings"].items()]
    payload = {"meta": db["meta"], "listings": listings}

    html = (ROOT / "site_builder" / "template.html").read_text()
    bm = cfg["benchmarks"]["comp_median_usd"]
    html = (
        html.replace("__DATA__", json.dumps(payload))
        .replace("__BENCHMARK__", str(bm))
        .replace("__BENCHMARK_K__", str(bm // 1000))
        .replace("__NEW_DAYS__", str(cfg["store"]["new_badge_days"]))
        .replace("__TODAY__", date.today().isoformat())
        .replace("__GENERATED__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    )
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    return out


if __name__ == "__main__":
    import sys
    import yaml
    sys.path.insert(0, str(ROOT))
    from pipeline import store
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    print("Wrote", build(store.load(), cfg))
