"""Fetch model and vocabulary TTL from a reference CWVA server.

Used to bootstrap a local deployment: if the local model or vocab folder
contains no TTL files, the canonical content is fetched from referenceModel
and saved locally. Existing TTL files are never overwritten.
"""

from pathlib import Path

import httpx

from util.logging import log_out, log_err


def _fetch_if_empty(url: str, local_dir: str, filename: str):
    """Download url as Turtle into local_dir/filename if that file does not exist."""
    path = Path(local_dir)
    path.mkdir(parents=True, exist_ok=True)
    dest = path / filename
    if dest.exists():
        log_out(f"[reference] {dest} already exists — skipping")
        return
    log_out(f"[reference] fetching {url} → {local_dir}/{filename}")
    try:
        resp = httpx.get(url, headers={"Accept": "text/turtle"}, timeout=30,
                         follow_redirects=True)
        if resp.status_code == 200:
            dest = path / filename
            dest.write_bytes(resp.content)
            log_out(f"[reference] saved {dest} ({len(resp.content)} bytes)")
        else:
            log_err(f"[reference] HTTP {resp.status_code} fetching {url}")
    except Exception as e:
        log_err(f"[reference] fetch failed for {url}: {e}")


def fetch_model(base_url: str, model_dir: str, vocab_dir: str = None):
    """Fetch model (and optionally vocab) TTL from a reference CWVA server.

    Only downloads if the local directory is empty. Safe to call on every
    startup — once local files exist the fetch is skipped.
    """
    base = base_url.rstrip("/")
    _fetch_if_empty(f"{base}/schema?format=ttl", model_dir, "cwva-reference.ttl")
    if vocab_dir:
        _fetch_if_empty(f"{base}/vocab", vocab_dir, "vocab-reference.ttl")
