"""Adapter contract.

Add a platform by subclassing Adapter and implementing fetch(). Nothing else in
the codebase changes -- run.py dispatches purely on the `platform` key in
targets.yaml.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import requests

from ..models import Posting

log = logging.getLogger(__name__)

USER_AGENT = (
    "uni-job-collector/0.1 (personal job search; contact chengjoshua22@gmail.com)"
)


class Adapter(ABC):
    """One instance per target institution/portal."""

    platform: str = "base"

    def __init__(self, target: dict[str, Any], *, delay: float = 1.0,
                 timeout: int = 30, session: requests.Session | None = None):
        self.target = target
        self.name = target["name"]
        self.delay = delay
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    @abstractmethod
    def fetch(self) -> list[Posting]:
        """Return every current posting for this target."""

    # -- helpers shared by all adapters -------------------------------------

    def _sleep(self) -> None:
        """Be a good citizen. These are .edu servers run by small IT teams."""
        time.sleep(self.delay)

    def _get(self, url: str, **kw) -> requests.Response:
        r = self.session.get(url, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def _post(self, url: str, json: dict, **kw) -> requests.Response:
        r = self.session.post(url, json=json, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def _base_fields(self) -> dict[str, Any]:
        return {
            "institution": self.name,
            "platform": self.platform,
            "system": self.target.get("system"),
            "state": self.target.get("state"),
        }
