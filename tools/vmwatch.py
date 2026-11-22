#!/usr/bin/env python3
"""
vmwatch.py — periodic health check for the CWVA server host, with email alerts.

Standalone — no imports from the cwva server codebase; standard library only.
Run from cron every 10 minutes (see "Setup" below).  Each run checks a fixed
set of rules, appends a one-line summary to stdout, and emails only when an
alert starts, is still active after REMIND_HOURS, or clears.

Checks
  process     exactly one server process (`main.py -cfg ...`); notes a restart
  memory      MemAvailable vs MemTotal (/proc/meminfo)
  disk        free space on the server dir's filesystem
  load        1-minute load average per CPU
  http        /status and / on localhost: status, latency, and that the
              gallery actually lists artworks ("/work/" in the page)
  data        triple count from the last "Data load complete" line in out.log:
              below a floor, or a large drop from the previous load
  errors      new err.log lines matching serious patterns since the last run
  traffic     new access-log lines: 5xx responses, and any single IP flooding

When the http check fails, a diagnostics snapshot (py-spy dump if available,
top, free, df, tail of err.log) is written next to the state file, so the
next investigation starts with the server's state at the moment it failed.

Log lines quoted in alert emails are attacker-influenced (request paths, user
agents) — they are truncated and stripped of control characters, and this
script never acts on their content.

Setup
  1. SMTP settings in ~/.secrets.rson (never committed), e.g. for Gmail with
     an app password (Google Account > Security > App passwords):
        "smtp": {"host": "smtp.gmail.com", "port": 587,
                 "user": "you@gmail.com", "password": "<app password>",
                 "to": "you@gmail.com"}
  2. Test:   python3 tools/vmwatch.py --dry-run     (report only, no email)
             python3 tools/vmwatch.py --test-email  (send one test email)
  3. crontab -e:
        */10 * * * * cd ~/cwva-py && python3 tools/vmwatch.py >> log/vmwatch.log 2>&1

Options
  --dir PATH       server directory holding out.log/err.log and config/
                   (default: the repo this script lives in)
  --cfg PATH       server rson config (default: <dir>/config/serverCwva.rson)
  --state PATH     state file (default: ~/.vmwatch-state.json)
  --dry-run        print the report; send nothing, save no state
  --test-email     send a test email and exit

Thresholds can be overridden with a "vmwatch" section in the server config,
using the keys of DEFAULTS below.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import smtplib
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from email.message import EmailMessage
from pathlib import Path


DEFAULTS = {
    "mem_used_pct":      85,     # alert above this % of RAM in use
    "disk_used_pct":     85,     # alert above this % of the filesystem in use
    "load_per_cpu":      2.0,    # alert above this 1-minute load per CPU
    "http_timeout":      10,     # seconds per request
    "http_slow_secs":    3.0,    # alert when a page takes longer than this
    "min_triples":       10000,  # alert when the rdfs store loads fewer
    "triple_drop_pct":   10,     # alert when a load drops this % below the last
    "ip_flood":          600,    # alert when one IP makes this many requests per run
    "errors_5xx":        5,      # alert at this many 5xx responses per run
    "remind_hours":      6,      # re-send an active alert after this long
}

# err.log lines worth an alert.  Routine noise (scanner 404s, "Invalid HTTP
# request", bucket misses for probe names like .env) is deliberately absent.
ERROR_PATTERNS = {
    "traceback":      re.compile(r"Traceback \(most recent call last\)"),
    "unhandled":      re.compile(r"Unhandled error on "),
    "sync_failed":    re.compile(r"\[sync\] .*(download failed|downloads failed)"),
    "query_stuck":    re.compile(r"\[sparql\] timed-out query thread did not stop"),
    "thumb_failed":   re.compile(r"\[thumbnail\] failed for "),
    "memory":         re.compile(r"MemoryError|Cannot allocate memory"),
    "metrics_push":   re.compile(r"Failed to write metrics snapshot"),
}

ACCESS_RE = re.compile(r'^INFO:\s+(\S+?):\d+ - "(\S+) (\S+) [^"]*" (\d{3})')
LOAD_RE = re.compile(r"Data load complete in [\d.]+s .* (\d+) triples in rdfs store")
USER_AGENT = "cwva-vmwatch"  # excluded from the server's metrics


# ── Output (stdout/stderr with ISO timestamps, like util/logging.py) ───────

def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def log_out(msg: str):
    print(f"{_ts()} {msg}", file=sys.stdout, flush=True)


def log_err(msg: str):
    print(f"{_ts()} {msg}", file=sys.stderr, flush=True)


# ── RSON loader (inline — no dependency on util/rson.py) ──────────────────

def rson_load(path: Path) -> dict:
    """JSON with '#' comments; '#' inside strings is left alone."""
    lines = []
    for line in path.read_text().splitlines():
        out, in_str, esc = [], False, False
        for ch in line:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "#":
                break
            out.append(ch)
        lines.append("".join(out))
    return json.loads("\n".join(lines))


def _clean(line: str, limit: int = 200) -> str:
    """Make an attacker-influenced log line safe to quote in an email."""
    line = "".join(c if c.isprintable() else "?" for c in line.strip())
    return line if len(line) <= limit else line[:limit] + "…"


# ── Incremental log reading ───────────────────────────────────────────────

def read_new_lines(path: Path, cursor: dict) -> list:
    """Lines appended to path since cursor {inode, offset}; updates cursor.

    start.s renames the logs at each restart, so a new inode — or a file
    shorter than the saved offset — means start again from the top.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return []
    if cursor.get("inode") != st.st_ino or st.st_size < cursor.get("offset", 0):
        cursor["inode"], cursor["offset"] = st.st_ino, 0
    with path.open("rb") as f:
        f.seek(cursor["offset"])
        data = f.read()
    # Keep a partial last line for next time.
    end = data.rfind(b"\n") + 1
    cursor["offset"] += end
    return data[:end].decode("utf-8", errors="replace").splitlines()


# ── Checks — each returns a list of (alert_key, message) ──────────────────

def server_processes() -> list:
    """[(pid, rss_mb, start_epoch)] for running `main.py -cfg` processes."""
    procs = []
    boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    hz = os.sysconf("SC_CLK_TCK")
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().split(b"\0")
            if not any(c.endswith(b"main.py") for c in cmd) or b"-cfg" not in cmd:
                continue
            rss = 0
            for line in (p / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) // 1024
            start = boot + int((p / "stat").read_text().rsplit(")", 1)[1].split()[19]) / hz
            procs.append((int(p.name), rss, start))
        except (OSError, IndexError, ValueError):
            continue  # process exited mid-scan
    return procs


def check_process(state: dict, info: dict) -> list:
    procs = server_processes()
    info["procs"] = len(procs)
    if not procs:
        return [("process", "No server process (main.py -cfg) is running.")]
    if len(procs) > 1:
        pids = ", ".join(str(p[0]) for p in procs)
        return [("process", f"{len(procs)} server processes are running (pids {pids}); "
                            "expected exactly one.")]
    pid, rss, start = procs[0]
    info.update(pid=pid, rss_mb=rss)
    alerts = []
    if state.get("pid") not in (None, pid):
        started = datetime.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M")
        alerts.append(("notice_restart", f"Server restarted: pid {state['pid']} -> {pid}, "
                                         f"started {started}."))
    state["pid"] = pid
    return alerts


def check_memory(lim: dict, info: dict) -> list:
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0])
    used = 100 * (1 - mem["MemAvailable"] / mem["MemTotal"])
    info["mem_pct"] = round(used)
    if used > lim["mem_used_pct"]:
        return [("memory", f"Memory {used:.0f}% used "
                           f"({mem['MemAvailable'] // 1024} MB available of "
                           f"{mem['MemTotal'] // 1024} MB).")]
    return []


def check_disk(server_dir: Path, lim: dict, info: dict) -> list:
    du = shutil.disk_usage(server_dir)
    used = 100 * du.used / du.total
    info["disk_pct"] = round(used)
    if used > lim["disk_used_pct"]:
        return [("disk", f"Disk {used:.0f}% used ({du.free // 2**30} GB free) "
                         f"on the filesystem holding {server_dir}.")]
    return []


def check_load(lim: dict, info: dict) -> list:
    load1 = os.getloadavg()[0]
    per_cpu = load1 / (os.cpu_count() or 1)
    info["load"] = round(load1, 2)
    if per_cpu > lim["load_per_cpu"]:
        return [("load", f"Load average {load1:.2f} on {os.cpu_count()} CPUs.")]
    return []


def _get(url: str, timeout: float):
    """(status, seconds, body) — status is an error string on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, time.monotonic() - t0, body
    except Exception as e:  # timeout, refused, HTTP error
        return getattr(e, "code", None) or type(e).__name__, time.monotonic() - t0, ""


def check_http(base: str, lim: dict, info: dict) -> list:
    alerts = []
    for path, key in (("/status", "http_status"), ("/", "http_gallery")):
        status, secs, body = _get(base + path, lim["http_timeout"])
        info[key] = f"{status}/{secs:.2f}s"
        if status != 200:
            alerts.append((key, f"GET {path} failed: {status} after {secs:.1f}s."))
        elif secs > lim["http_slow_secs"]:
            alerts.append((key, f"GET {path} slow: {secs:.1f}s."))
        elif path == "/" and "/work/" not in body:
            alerts.append((key, "GET / returned 200 but lists no artworks "
                                "(empty data store?)."))
    return alerts


def check_data(out_lines: list, state: dict, lim: dict, info: dict) -> list:
    """The load line is logged once per load, so a bad load's alert is kept in
    state and repeated every run until a healthy load replaces it."""
    for m in map(LOAD_RE.search, out_lines):
        if not m:
            continue
        n, prev = int(m.group(1)), state.get("triples")
        if n < lim["min_triples"]:
            state["data_alert"] = (f"Data load produced only {n} triples "
                                   f"(floor {lim['min_triples']}).")
        elif prev and n < prev * (1 - lim["triple_drop_pct"] / 100):
            state["data_alert"] = f"Data load produced {n} triples, down from {prev}."
        else:
            state["data_alert"] = None
        state["triples"] = n
    info["triples"] = state.get("triples")
    return [("data", state["data_alert"])] if state.get("data_alert") else []


def check_errors(err_lines: list) -> list:
    hits = {}
    for line in err_lines:
        for key, rx in ERROR_PATTERNS.items():
            if rx.search(line):
                hits.setdefault(key, []).append(line)
    alerts = []
    for key, lines in hits.items():
        sample = "\n    ".join(_clean(l) for l in lines[:3])
        alerts.append((f"errors_{key}", f"{len(lines)} new err.log line(s) matching "
                                        f"'{key}':\n    {sample}"))
    return alerts


def check_traffic(out_lines: list, lim: dict, info: dict) -> list:
    ips, errors = Counter(), []
    for line in out_lines:
        m = ACCESS_RE.match(line)
        if not m:
            continue
        ip, _method, _path, code = m.groups()
        ips[ip] += 1
        if code.startswith("5"):
            errors.append(line)
    info["requests"] = sum(ips.values())
    alerts = []
    if len(errors) >= lim["errors_5xx"]:
        sample = "\n    ".join(_clean(l) for l in errors[:3])
        alerts.append(("traffic_5xx", f"{len(errors)} responses with 5xx status since "
                                      f"the last run:\n    {sample}"))
    for ip, n in ips.most_common(3):
        if n >= lim["ip_flood"]:
            alerts.append((f"traffic_flood_{ip}", f"{ip} made {n} requests since the "
                                                  f"last run."))
    return alerts


# ── Diagnostics snapshot (taken when the http check fails) ─────────────────

def snapshot(server_dir: Path, pid, out_dir: Path) -> Path:
    path = out_dir / f"vmwatch-diag-{datetime.datetime.now():%Y%m%dT%H%M%S}.txt"
    cmds = [["free", "-m"], ["df", "-h"], ["top", "-b", "-n", "1"]]
    if pid:
        cmds.insert(0, ["py-spy", "dump", "--pid", str(pid)])
    with path.open("w") as f:
        for cmd in cmds:
            f.write(f"\n===== {' '.join(cmd)} =====\n")
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                f.write(r.stdout + r.stderr)
            except (OSError, subprocess.TimeoutExpired) as e:
                f.write(f"(unavailable: {e})\n")
        f.write("\n===== tail of err.log =====\n")
        try:
            f.writelines(server_dir.joinpath("err.log").read_text(
                errors="replace").splitlines(keepends=True)[-50:])
        except OSError as e:
            f.write(f"(unavailable: {e})\n")
    return path


# ── Email ─────────────────────────────────────────────────────────────────

def load_smtp() -> dict:
    path = Path.home() / ".secrets.rson"
    try:
        return rson_load(path).get("smtp") or {}
    except (OSError, ValueError):
        return {}


def send_email(smtp: dict, subject: str, body: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.get("from", smtp["user"])
    msg["To"] = smtp["to"]
    msg.set_content(body)
    with smtplib.SMTP(smtp["host"], int(smtp.get("port", 587)), timeout=30) as s:
        s.starttls()
        s.login(smtp["user"], smtp["password"])
        s.send_message(msg)


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="CWVA host health check with email alerts.")
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--cfg")
    ap.add_argument("--state", default=str(Path.home() / ".vmwatch-state.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-email", action="store_true")
    args = ap.parse_args()

    host = socket.gethostname()
    smtp = load_smtp()
    if args.test_email:
        if not smtp:
            log_err("no 'smtp' section in ~/.secrets.rson")
            return 1
        send_email(smtp, f"[cwva {host}] vmwatch test", "vmwatch can send email.")
        log_out(f"test email sent to {smtp['to']}")
        return 0

    server_dir = Path(args.dir).expanduser()
    cfg_path = Path(args.cfg).expanduser() if args.cfg else server_dir / "config/serverCwva.rson"
    cfg = rson_load(cfg_path)
    lim = {**DEFAULTS, **cfg.get("vmwatch", {})}
    base = f"http://127.0.0.1:{cfg.get('port', 80)}"

    state_path = Path(args.state).expanduser()
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    first_run = not state
    cursors = state.setdefault("cursors", {})
    out_lines = read_new_lines(server_dir / "out.log", cursors.setdefault("out", {}))
    err_lines = read_new_lines(server_dir / "err.log", cursors.setdefault("err", {}))
    if first_run:
        # Logs may hold days of history: take the triple count from it, but
        # don't report old errors or traffic as new.
        err_lines = []
        out_lines = [l for l in out_lines if LOAD_RE.search(l)]

    info = {}
    found = []
    found += check_process(state, info)
    found += check_memory(lim, info)
    found += check_disk(server_dir, lim, info)
    found += check_load(lim, info)
    http_alerts = check_http(base, lim, info)
    found += http_alerts
    found += check_data(out_lines, state, lim, info)
    found += check_errors(err_lines)
    found += check_traffic(out_lines, lim, info)

    summary = " ".join(f"{k}={v}" for k, v in info.items())
    log_out(f"vmwatch {summary} alerts={len(found)}")

    # Alert bookkeeping: email on new alerts, reminders, and clears.
    # Notices (restart) and per-run events (errors, traffic) fire once each.
    now = time.time()
    active = state.setdefault("active", {})
    was_active = set(active)
    current = dict(found)
    persistent = {k for k in current if not k.startswith(("notice_", "errors_", "traffic_"))}
    new, remind = [], []
    for key, msg in current.items():
        if key not in persistent or key not in active:
            new.append(msg)
        elif now - active[key]["since_email"] > lim["remind_hours"] * 3600:
            remind.append(msg)
    cleared = [k for k in active if k not in current]
    for key in cleared:
        del active[key]
    for key in persistent:
        prior = active.get(key, {})
        emailed = key not in prior or now - prior["since_email"] > lim["remind_hours"] * 3600
        active[key] = {"since": prior.get("since", now),
                       "since_email": now if emailed else prior["since_email"]}

    # One snapshot when the http failure starts, not one every run it lasts.
    diag = None
    if any(k not in was_active for k, _ in http_alerts) and not args.dry_run:
        diag = snapshot(server_dir, info.get("pid"), state_path.parent)
        log_out(f"diagnostics written to {diag}")

    if new or remind or cleared:
        parts = []
        if new:
            parts.append("NEW\n" + "\n".join(f"  - {m}" for m in new))
        if remind:
            parts.append("STILL ACTIVE\n" + "\n".join(f"  - {m}" for m in remind))
        if cleared:
            parts.append("CLEARED\n" + "\n".join(f"  - {k}" for k in cleared))
        if diag:
            parts.append(f"Diagnostics snapshot: {diag}")
        parts.append(f"Status: {summary}")
        body = "\n\n".join(parts)
        if new or remind:
            subject = f"[cwva {host}] ALERT: " + _clean((new or remind)[0], 80)
        else:
            subject = f"[cwva {host}] cleared: {', '.join(cleared)}"
        if args.dry_run:
            log_out(f"(dry run) would email:\n{subject}\n{body}")
        elif not smtp:
            log_err(f"no 'smtp' section in ~/.secrets.rson; alert not sent:\n{body}")
        else:
            try:
                send_email(smtp, subject, body)
            except Exception as e:  # keep state consistent; retry next run
                log_err(f"email failed: {e}")
                return 1

    if not args.dry_run:
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
