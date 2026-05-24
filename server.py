"""Server singleton — holds config, DBMgr reference, and shared utilities."""

from datetime import datetime
from util.logging import log_out as _log_out, log_err as _log_err

VERSION = "5.0.0"

DOMAIN = "http://visualartsdna.org"


class Server:
    _instance: "Server" = None

    def __init__(self, cfg: dict):
        Server._instance = self
        self.cfg = cfg
        self.dbm = None  # set after DBMgr loads
        self.started_at = datetime.now().isoformat()

    @classmethod
    def get_instance(cls) -> "Server":
        return cls._instance

    @staticmethod
    def rehost(value):
        """Replace production domain with local host in URIs."""
        host = Server.get_instance().cfg["host"]
        if isinstance(value, list):
            return [v.replace(DOMAIN, host) for v in value]
        return value.replace(DOMAIN, host)

    @staticmethod
    def log_out(msg: str):
        _log_out(msg)

    @staticmethod
    def log_err(msg: str):
        _log_err(msg)

    def verbose_log(self, msg: str):
        if self.cfg.get("verbose"):
            Server.log_out(msg)
