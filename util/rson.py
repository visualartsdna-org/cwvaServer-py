"""RSON loader — JSON with # comments (whole-line or inline)."""

import re
import json


def load(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    lines = []
    for line in raw.splitlines():
        stripped = re.sub(r'\s*#.*$', '', line)
        lines.append(stripped)
    return json.loads('\n'.join(lines))
