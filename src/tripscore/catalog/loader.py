from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from tripscore.domain.models import Destination


_DESTINATIONS_ADAPTER = TypeAdapter(list[Destination])


def load_destinations(path: str | Path) -> list[Destination]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _DESTINATIONS_ADAPTER.validate_python(payload)
