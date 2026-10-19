import json
import logging
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line; Cloud Logging picks up `severity` and `message`.

    Pass structured fields with `logger.info(..., extra={"fields": {...}})`.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "request_id": getattr(record, "request_id", None),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class _StdoutHandler(logging.StreamHandler):
    """Marks the handler setup_logging owns, so calling it again replaces only that one."""


def setup_logging(level: str, *, json_format: bool) -> None:
    handler = _StdoutHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _CloudLoggingFormatter()
        if json_format
        else logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, _StdoutHandler)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
