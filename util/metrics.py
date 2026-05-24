"""In-memory request metrics — port of ServletBase metrics logic."""

import re
import threading
import datetime
from urllib.parse import urlparse

_lock = threading.Lock()
_metrics: dict = {}  # {date: {ip: {path: {count: N, ua: str, referer: str}}}}

# Rate limiting for /agent/query: 10 requests per IP per 10-minute window
_rate_lock = threading.Lock()
_rate_limits: dict = {}  # {ip: (count, window_start)}

RATE_LIMIT = 10
RATE_WINDOW = 600  # seconds


def _today() -> str:
    return datetime.date.today().isoformat()


def get_ip(request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def classify(ua: str) -> str:
    """Classify User-Agent string into a named category."""
    if not ua:
        return "other-browser"
    u = ua.lower()

    if "googlebot" in u:            return "googlebot"
    if "bingbot" in u:              return "bingbot"
    if "applebot" in u:             return "applebot"
    if "yandexbot" in u:            return "yandexbot"
    if "baiduspider" in u:          return "baidubot"
    if "duckduckbot" in u:          return "duckduckbot"
    if "semrushbot" in u:           return "semrush"
    if "ahrefsbot" in u:            return "ahrefs"
    if "mj12bot" in u:              return "majestic"
    if "dotbot" in u or "moz.com" in u: return "moz"
    if "huaweisymantecspider" in u: return "huawei"
    if "bytespider" in u:           return "bytedance"
    if "facebookexternalhit" in u:  return "facebook"
    if "twitterbot" in u:           return "twitter"
    if "linkedinbot" in u:          return "linkedin"
    if "gptbot" in u or "openai" in u:  return "openai"
    if "anthropic" in u or "claude" in u: return "anthropic"
    if "ccbot" in u or "commoncrawl" in u: return "commoncrawl"
    if "edg/" in u:                 return "edge"
    if "firefox/" in u:             return "firefox"
    if re.search(r"android.+mobile|android.+chrome", u): return "chrome-mobile"
    if re.search(r"iphone|ipad|ipod", u) and "safari" in u: return "safari-mobile"
    if "chrome/" in u:              return "chrome"
    if "safari/" in u:              return "safari"
    if "curl/" in u:                return "curl"
    if "wget/" in u:                return "wget"
    if "python-" in u:              return "python-script"
    if "java/" in u or "okhttp" in u: return "java-client"
    if "go-http-client" in u:       return "go-client"
    if re.search(r"bot|spider|crawler|scan|check|monitor", u): return "other-bot"
    return "other-browser"


def record(request, path: str):
    """Record a request in the metrics store.

    Structure mirrors the Groovy server exactly:
      {date: {"count": N, ip: {"count": N, "/path": {"count": N},
                                "u=chrome": {"count": N}, "r=same": {"count": N}}}}

    UA class and referer are recorded at the IP level (not nested inside each path).
    Date-level and IP-level count totals are maintained.
    """
    ip = get_ip(request)
    ua_key = f"u={classify(request.headers.get('user-agent', '') or 'unknown')}"

    raw_ref = request.headers.get("referer", "")
    if not raw_ref:
        ref_key = "r=direct"
    else:
        # Compare referer's netloc against the Host header — works regardless of
        # which IP/hostname the browser used, no dependency on server config.
        req_host = request.headers.get("host", "")
        try:
            ref_host = urlparse(raw_ref).netloc
        except Exception:
            ref_host = ""
        if (req_host and ref_host == req_host) or ip in raw_ref:
            ref_key = "r=same"
        else:
            ref_key = f"r={raw_ref}"

    day = _today()
    with _lock:
        day_data = _metrics.setdefault(day, {"count": 0})
        day_data["count"] += 1

        ip_data = day_data.setdefault(ip, {"count": 0})
        ip_data["count"] += 1

        ip_data.setdefault(path, {"count": 0})["count"] += 1
        ip_data.setdefault(ua_key, {"count": 0})["count"] += 1
        ip_data.setdefault(ref_key, {"count": 0})["count"] += 1


def get_metrics() -> dict:
    with _lock:
        import copy
        return copy.deepcopy(_metrics)


def get_all() -> dict:
    """Alias for get_metrics() — used by /cestfini and /cmd?cmd=stats."""
    return get_metrics()


# Paths excluded from metrics recording
METRICS_EXCLUDE = {"/images", "/thumbnails", "/favicon"}


def should_record(path: str) -> bool:
    return not any(path.startswith(p) for p in METRICS_EXCLUDE)


def check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate limit exceeded."""
    now = datetime.datetime.now().timestamp()
    with _rate_lock:
        entry = _rate_limits.get(ip)
        if entry is None or (now - entry[1]) > RATE_WINDOW:
            _rate_limits[ip] = (1, now)
            return True
        count, window_start = entry
        if count >= RATE_LIMIT:
            return False
        _rate_limits[ip] = (count + 1, window_start)
        return True
