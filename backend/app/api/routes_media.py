"""Authenticated reads of locally stored images.

Azure deployments hand the client a SAS URL from the finger endpoints, so
this route is the dev fallback. The key must sit under users/{caller id}/.
"""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.deps import CurrentStorage, CurrentUser
from app.storage import InvalidStorageKey, StorageNotFound, media_type_for_key, validate_blob_key

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{key:path}")
def read_media(key: str, user: CurrentUser, storage: CurrentStorage) -> Response:
    try:
        safe = validate_blob_key(key)
        content_type = media_type_for_key(safe)
    except InvalidStorageKey:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from None
    prefix = f"users/{user.id}/"
    if not safe.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    try:
        payload = storage.get_bytes(safe)
    except (StorageNotFound, InvalidStorageKey):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from None
    return Response(
        content=payload,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        },
    )
