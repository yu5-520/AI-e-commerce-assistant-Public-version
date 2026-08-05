"""V16.25 UID utility.

Small active-runtime helper for task evidence, task lifecycle events and audit
records. This is a V16 support utility, not a restored legacy workflow module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

UID_SERVICE_VERSION = "16.25"


def make_id(prefix: str = "ID") -> str:
    """Return a compact stable-enough uppercase identifier for runtime records."""
    safe_prefix = str(prefix or "ID").strip().replace(" ", "_").upper()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{safe_prefix}_{timestamp}_{uuid4().hex[:8].upper()}"


def make_run_id(prefix: str = "RUN") -> str:
    return make_id(prefix)


def make_event_id(prefix: str = "EVENT") -> str:
    return make_id(prefix)


def make_record_id(prefix: str = "RECORD") -> str:
    return make_id(prefix)
