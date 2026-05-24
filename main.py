"""Entry point — load config, initialize server, start uvicorn."""

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import uvicorn

from util.rson import load as rson_load
from server import Server
from servlet import build_app


def _ensure_dirs(cfg: dict):
    """Create required local directories if they don't exist."""
    for key in ("data", "model", "vocab", "tags", "images", "thumbnails", "documents"):
        d = cfg.get(key, "")
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "-cfg":
        print("Usage: python main.py -cfg cwvaServer.rson")
        sys.exit(1)

    cfg_path = sys.argv[2]
    cfg = rson_load(cfg_path)

    # Validate required environment variables
    if not os.environ.get("GCP_BUCKET") and cfg.get("cloud"):
        Server.log_out("WARNING: GCP_BUCKET not set — TTL sync and on-demand image/thumbnail fetch will be disabled")
    if not os.environ.get("ANTHROP_KEY") and cfg.get("agentUrl"):
        Server.log_out("WARNING: ANTHROP_KEY not set — Ask/AI page will fail")

    srv = Server(cfg)
    srv.verbose_log(f"Config loaded from {cfg_path}")

    _ensure_dirs(cfg)

    # Evict stale cached images at startup (TTL sync runs inside DBMgr.__init__)
    if cfg.get("cloud") and os.environ.get("GCP_BUCKET") and cfg.get("images"):
        from util.gcp import evict_stale_images
        Server.log_out("Checking local image freshness against GCP bucket...")
        deleted = evict_stale_images(cfg["images"], thumbnails_dir=cfg.get("thumbnails"))
        if deleted:
            Server.log_out(f"Evicted {deleted} stale local image(s)")

    from rdf.db_mgr import DBMgr
    srv.dbm = DBMgr(cfg)

    app = build_app(srv)

    port = cfg.get("port", 80)
    Server.log_out(f"Starting CWVA server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
