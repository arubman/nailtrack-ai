"""Short-lived read links for private images."""

from __future__ import annotations

from app.schemas import ImageLink
from app.storage import Storage


def image_link(storage: Storage, key: str | None) -> ImageLink | None:
    if not key:
        return None
    if storage.kind == "azure":
        ttl = getattr(storage, "ttl_seconds", None)
        return ImageLink(url=storage.read_url(key), access="sas", expires_in_seconds=ttl)
    return ImageLink(url=storage.read_url(key), access="authenticated_route", expires_in_seconds=None)
