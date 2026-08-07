"""targets.yaml loading and adapter dispatch."""
from __future__ import annotations

import logging
from typing import Any

import yaml

from .adapters.base import Adapter
from .adapters.custom.umn import UMNAdapter
from .adapters.pageup import PageUpAdapter
from .adapters.peopleadmin import PeopleAdminAdapter
from .adapters.workday import WorkdayAdapter

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[Adapter]] = {
    "workday": WorkdayAdapter,
    "peopleadmin": PeopleAdminAdapter,
    "pageup": PageUpAdapter,
    "umn": UMNAdapter,
}


def load_targets(path: str = "targets.yaml") -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return doc.get("targets", [])


def build_adapter(target: dict[str, Any], **kw) -> Adapter | None:
    platform = target.get("platform")
    cls = REGISTRY.get(platform)
    if cls is None:
        log.warning("%s: no adapter registered for platform %r",
                    target.get("name"), platform)
        return None
    return cls(target, **kw)
