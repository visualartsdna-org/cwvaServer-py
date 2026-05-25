#!/usr/bin/env python3
"""
cwva_cmd.py — command-line tool for cwva server administration.
Standalone — no imports from the cwva server codebase.
Reads ~/.secrets.rson for the phrase and the server rson config for host/port.

Usage:
  python cwva_cmd.py <command>
  python cwva_cmd.py <command> -H http://192.168.1.71 -p 8081
  python cwva_cmd.py <command> --cfg ~/cwva/main/serverCwva.rson

Commands:
  refresh    Reload RDF stores from disk/GCP
  cestfini   Dump metrics and shut down the server
  status     Show OS-level process and disk status (token-authenticated, remote)

Options:
  -H, --host URL    server host URL (default: http://localhost)
  -p, --port PORT   server port (default: 80)
  --cfg PATH        rson config file — optional, used only to read host
                    (can also set CWVA_CFG env var)

Priority: --host/--port > --cfg file > CWVA_CFG env var > localhost:80
"""

import sys
import os
import json
import re
import time
import urllib.request
import argparse
from pathlib import Path


# ── RSON loader (inline — no dependency on util/rson.py) ──────────────────

def rson_load(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    lines = [re.sub(r'\s*#.*$', '', line) for line in raw.splitlines()]
    return json.loads('\n'.join(lines))


# ── Java String.hashCode() reimplementation ────────────────────────────────

def java_hashcode(s: str) -> int:
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return h


# ── Token generation ───────────────────────────────────────────────────────

def get_token() -> str:
    secrets = rson_load(str(Path.home() / ".secrets.rson"))
    phrase = secrets["secrets"]["phrase"]
    hc = java_hashcode(phrase)
    ms = int(time.time() * 1000)
    return str(ms * hc)


# ── Send command ───────────────────────────────────────────────────────────

def send_cmd(host: str, command: str) -> dict:
    token = get_token()
    url = f"{host}/cmd?token={token}&cmd={command}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


# ── OS status (remote) ────────────────────────────────────────────────────

def show_status(host: str):
    token = get_token()
    url = f"{host}/status/os?token={token}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read())
    for section in ("system", "python", "java", "disk", "logs", "errors"):
        print(f"=== {section.capitalize()} ===")
        print(data.get(section, ""))


# ── Host resolution ────────────────────────────────────────────────────────

def resolve_host(args) -> str:
    # explicit --host / --port win
    if args.host:
        host = args.host.rstrip("/")
        port = args.port or 80
        # only append port if not already present after the scheme
        if ":" not in host.split("//")[-1]:
            host = f"{host}:{port}"
        return host

    # fall back to --cfg / CWVA_CFG rson file
    if args.cfg:
        cfg = rson_load(args.cfg)
        return cfg.get("host", "http://localhost:80").rstrip("/")

    # final default
    port = args.port or 80
    return f"http://localhost:{port}"


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="cwva server admin tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cwva_cmd.py refresh -H https://visualartsdna.org
  python cwva_cmd.py refresh --cfg ~/cwva/main/config/serverCwva.rson
  python cwva_cmd.py status  -H http://192.168.1.71 -p 8081
  export CWVA_CFG=~/cwva/main/config/serverCwva.rson && python cwva_cmd.py status
""",
    )
    parser.add_argument("command", choices=["refresh", "cestfini", "status"])
    parser.add_argument("-H", "--host", help="server host URL (e.g. http://192.168.1.71)")
    parser.add_argument("-p", "--port", type=int, help="server port (default: 80)")
    parser.add_argument("--cfg", default=os.environ.get("CWVA_CFG"),
                        help="path to server rson config (optional; or set CWVA_CFG env var)")
    args = parser.parse_args()
    host = resolve_host(args)

    try:
        if args.command == "status":
            show_status(host)
        else:
            result = send_cmd(host, args.command)
            print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
