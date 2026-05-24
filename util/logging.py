"""Centralised logging — stdout for operational output, stderr for errors."""

import sys
import datetime


def log_out(msg: str):
    """Normal operational output → stdout."""
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"{ts} {msg}", file=sys.stdout, flush=True)


def log_err(msg: str):
    """Exceptional/error output → stderr."""
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"{ts} {msg}", file=sys.stderr, flush=True)
