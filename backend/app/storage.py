"""Private image storage.

The database stores blob keys. Reads go out as a short-lived Azure Blob SAS
URL, or, in local development, as a path on an authenticated API route.
The container is never given anonymous access. StaticFiles is intentionally
not mounted, because that would publish the dev folder.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Request

from app.config import Settings
from app.database import utcnow

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_TYPE_TO_SUFFIXES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}


class InvalidStorageKey(ValueError):
    """The blob key is empty, escapes the user prefix, or is not an allowed image."""


class StorageNotFound(FileNotFoundError):
    """No object exists at this key."""


def validate_blob_key(key: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 512:
        raise InvalidStorageKey("key length is invalid")
    segments = key.split("/")
    if len(segments) > 12 or any(_SEGMENT_RE.fullmatch(segment) is None for segment in segments):
        raise InvalidStorageKey("key contains an invalid segment")
    return key


def blob_key_for_user(user_id: object, *segments: str) -> str:
    """Build a key under users/{user_id}/. Phase 2 uploads should use this."""
    return validate_blob_key("/".join(["users", str(user_id), *segments]))


def media_type_for_key(key: str) -> str:
    suffix = Path(key).suffix.lower()
    try:
        return _CONTENT_TYPES[suffix]
    except KeyError as exc:
        raise InvalidStorageKey("unsupported media type") from exc


class Storage:
    kind: str

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def read_url(self, key: str, *, expires_in: int | None = None) -> str:
        raise NotImplementedError

    def ensure_ready(self) -> None:
        return None


class LocalStorage(Storage):
    """Filesystem fallback. Bytes are only served through the authenticated media route."""

    kind = "local"

    def __init__(self, root: Path, media_url_prefix: str) -> None:
        self.root = root
        self.media_url_prefix = media_url_prefix.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key, content_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageNotFound(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def read_url(self, key: str, *, expires_in: int | None = None) -> str:
        # expires_in is unused: each request is authorized on its own. There is
        # no signed query string, and the path is not a public file URL.
        del expires_in
        safe = validate_blob_key(key)
        media_type_for_key(safe)
        return f"{self.media_url_prefix}/{safe}"

    def _path(self, key: str, content_type: str | None = None) -> Path:
        safe = validate_blob_key(key)
        if content_type is not None:
            _require_matching_type(safe, content_type)
        else:
            media_type_for_key(safe)
        root = self.root.resolve()
        path = (root / safe).resolve()
        if path != root and root not in path.parents:
            raise InvalidStorageKey("key escapes the storage root")
        return path


class AzureBlobStorage(Storage):
    """Private container. Read URLs are short-lived SAS tokens.

    When a credential is supplied, SAS tokens are user-delegation signatures
    from that identity (managed identity on App Service). Otherwise the
    account key in the connection string signs them. That key path is the
    local and dev fallback.
    """

    kind = "azure"

    def __init__(
        self,
        connection_string: str | None = None,
        container: str = "nailtrack-private",
        ttl_seconds: int = 300,
        *,
        credential: object | None = None,
        account_name: str | None = None,
        endpoint_suffix: str = "core.windows.net",
    ) -> None:
        self.connection_string = connection_string
        self.container_name = container
        self.ttl_seconds = ttl_seconds
        self._credential = credential
        self._parts = _parse_connection_string(connection_string or "")
        self._account_name = account_name or self._parts.get("AccountName")
        self._endpoint_suffix = self._parts.get("EndpointSuffix", endpoint_suffix)
        self._account_key = self._parts.get("AccountKey")
        if credential is None and (not self._account_name or not self._account_key):
            raise ValueError(
                "Azure storage needs a connection string with AccountName and AccountKey, "
                "or a managed-identity credential plus AZURE_STORAGE_ACCOUNT_NAME"
            )
        if not self._account_name:
            raise ValueError("Azure storage account name is missing")
        self._service = None
        self._delegation_key = None
        self._delegation_expires: object | None = None

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        safe = validate_blob_key(key)
        _require_matching_type(safe, content_type)
        blob = self._container().get_blob_client(safe)
        blob.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(
                content_type=content_type,
                cache_control="private, max-age=60",
            ),
        )

    def get_bytes(self, key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        safe = validate_blob_key(key)
        media_type_for_key(safe)
        blob = self._container().get_blob_client(safe)
        try:
            return blob.download_blob().readall()
        except ResourceNotFoundError as exc:
            raise StorageNotFound(safe) from exc

    def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        safe = validate_blob_key(key)
        blob = self._container().get_blob_client(safe)
        try:
            blob.delete_blob(delete_snapshots="include")
        except ResourceNotFoundError:
            return

    def read_url(self, key: str, *, expires_in: int | None = None) -> str:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        safe = validate_blob_key(key)
        media_type_for_key(safe)
        ttl = self.ttl_seconds if expires_in is None else expires_in
        if ttl <= 0 or ttl > 3600:
            raise ValueError("SAS lifetime must be between 1 and 3600 seconds")
        expiry = utcnow() + timedelta(seconds=ttl)
        sas_kwargs: dict[str, object] = {
            "account_name": self._account_name,
            "container_name": self.container_name,
            "blob_name": safe,
            "permission": BlobSasPermissions(read=True),
            "expiry": expiry,
            "protocol": "https",
        }
        if self._credential is not None:
            sas_kwargs["user_delegation_key"] = self._user_delegation_key()
        else:
            sas_kwargs["account_key"] = self._account_key
        sas = generate_blob_sas(**sas_kwargs)
        quoted = quote(safe, safe="/")
        return (
            f"https://{self._account_name}.blob.{self._endpoint_suffix}/"
            f"{self.container_name}/{quoted}?{sas}"
        )

    def _user_delegation_key(self):
        now = utcnow()
        cached_expiry = self._delegation_expires
        if self._delegation_key is not None and isinstance(cached_expiry, datetime) and cached_expiry > now + timedelta(minutes=5):
            return self._delegation_key
        start = now - timedelta(minutes=1)
        key_expiry = now + timedelta(hours=1)
        key = self._service_client().get_user_delegation_key(start, key_expiry)
        self._delegation_key = key
        self._delegation_expires = key_expiry
        return key

    def ensure_ready(self) -> None:
        """Create the container if needed and refuse to run against a public one."""
        import logging

        from azure.core.exceptions import HttpResponseError, ResourceExistsError

        log = logging.getLogger(__name__)
        container = self._container()
        try:
            container.create_container()
            log.info("Created private blob container %s", self.container_name)
            return
        except ResourceExistsError:
            log.debug("Blob container %s already exists", self.container_name)
        try:
            props = container.get_container_properties()
        except HttpResponseError as exc:
            log.warning(
                "Could not verify that blob container %s is private (%s). The app will not enable public access.",
                self.container_name,
                getattr(exc, "status_code", exc.__class__.__name__),
            )
            return
        public_access = getattr(props, "public_access", None)
        if public_access not in (None, "off", ""):
            raise RuntimeError(
                f"Blob container {self.container_name} allows public access ({public_access}). "
                "NailTrack requires a private container so images are only readable via short-lived SAS URLs."
            )

    def _service_client(self):
        if self._service is None:
            from azure.storage.blob import BlobServiceClient

            if self._credential is not None:
                account_url = f"https://{self._account_name}.blob.{self._endpoint_suffix}"
                self._service = BlobServiceClient(account_url=account_url, credential=self._credential)
            else:
                self._service = BlobServiceClient.from_connection_string(self.connection_string or "")
        return self._service

    def _container(self):
        return self._service_client().get_container_client(self.container_name)


def build_storage(settings: Settings) -> Storage:
    if settings.resolved_storage_backend == "azure":
        if settings.azure_storage_use_managed_identity:
            from azure.identity import DefaultAzureCredential

            account = settings.azure_storage_account_name
            if not account and settings.azure_storage_connection_string:
                account = _parse_connection_string(settings.azure_storage_connection_string).get("AccountName")
            if not account:
                raise RuntimeError("Managed identity storage requires AZURE_STORAGE_ACCOUNT_NAME")
            return AzureBlobStorage(
                connection_string=settings.azure_storage_connection_string,
                container=settings.azure_storage_container,
                ttl_seconds=settings.sas_url_ttl_seconds,
                credential=DefaultAzureCredential(),
                account_name=account,
            )
        if not settings.azure_storage_connection_string:
            raise RuntimeError("Azure storage is not configured")
        return AzureBlobStorage(
            connection_string=settings.azure_storage_connection_string,
            container=settings.azure_storage_container,
            ttl_seconds=settings.sas_url_ttl_seconds,
        )
    return LocalStorage(root=Path(settings.local_storage_path), media_url_prefix=settings.media_url_prefix)


def get_storage(request: Request) -> Storage:
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise RuntimeError("Storage is not initialized")
    return storage


def _parse_connection_string(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in value.split(";"):
        if not segment or "=" not in segment:
            continue
        key, item = segment.split("=", 1)
        parts[key] = item
    return parts


def _require_matching_type(key: str, content_type: str) -> None:
    base = content_type.split(";", 1)[0].strip().lower()
    suffix = Path(key).suffix.lower()
    allowed = _TYPE_TO_SUFFIXES.get(base)
    if allowed is None or suffix not in allowed:
        raise InvalidStorageKey("content type does not match the image key")
