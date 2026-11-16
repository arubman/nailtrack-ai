import uuid

import pytest
from fastapi.testclient import TestClient

from app.constants import NOT_MEDICAL_ADVICE
from app.models import FingerBaseline, FingerCode, ScaleReferenceType, User
from app.storage import AzureBlobStorage, InvalidStorageKey, blob_key_for_user


def test_health_docs_and_session_contract(client: TestClient):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"

    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert "/api/v1/treatments" in paths
    assert "/api/v1/captures/analyze" not in paths
    assert "/api/v1/calibrate" not in paths

    session = client.get("/api/v1/me")
    assert session.status_code == 200
    body = session.json()
    assert body["forecast_horizons_days"] == [7, 14, 30, 60, 90]
    assert body["timeline_slider"] == {"min_days": -90, "max_days": 90, "step_days": 1}
    assert body["disclaimer"] == NOT_MEDICAL_ADVICE
    assert body["quality_thresholds"] == {
        "blur_laplacian_min": 110.0,
        "glare_ratio_max": 0.08,
        "tilt_deg_max": 10.0,
    }
    assert body["ridge_score_insight_threshold"] == 0.35
    assert body["default_velocity_mm_day"] == 0.11
    assert body["population_velocity_mm_day"] == 0.115
    assert body["storage"]["read_access"] == "authenticated_route"


def test_cors_allows_the_local_frontend(client):
    response = client.options(
        "/api/v1/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_fingers_are_listed_and_scoped(client, db):
    empty = client.get("/api/v1/fingers")
    assert empty.status_code == 200
    codes = [item["finger_code"] for item in empty.json()]
    assert codes[0] == "L_THUMB"
    assert codes[-1] == "R_PINKY"
    assert len(codes) == 10
    assert all(item["baseline_id"] is None for item in empty.json())

    me = uuid.UUID(client.get("/api/v1/me").json()["user"]["id"])
    other = User(external_subject="other-fingers", email="other@example.com")
    db.add(other)
    db.flush()
    db.add(
        FingerBaseline(
            user_id=other.id,
            finger_code=FingerCode.L_INDEX,
            calibrated_width_mm=9.0,
            reference_white_json={"source": "id1_card_white"},
        )
    )
    mine = FingerBaseline(
        user_id=me,
        finger_code=FingerCode.L_INDEX,
        calibrated_width_mm=12.5,
        scale_reference_type=ScaleReferenceType.ID1_CARD,
        scale_reference_width_mm=85.60,
        baseline_image_key=blob_key_for_user(me, "baselines", "L_INDEX", "day0.jpg"),
        reference_white_json={"source": "id1_card_white", "target": "D65"},
    )
    db.add(mine)
    db.commit()
    client.app.state.storage.put_bytes(mine.baseline_image_key, b"\xff\xd8\xff raw", "image/jpeg")

    listing = client.get("/api/v1/fingers").json()
    index = next(item for item in listing if item["finger_code"] == "L_INDEX")
    assert index["baseline_id"] == str(mine.id)
    assert index["calibrated_width_mm"] == 12.5
    assert index["has_reference_white"] is True
    assert index["scale_reference_width_mm"] == 85.6
    link = index["baseline_image"]
    assert link["access"] == "authenticated_route"
    assert link["url"] == f"/api/v1/media/{mine.baseline_image_key}"

    detail = client.get("/api/v1/fingers/L_INDEX")
    assert detail.status_code == 200
    assert detail.json()["baseline_id"] == str(mine.id)
    assert client.get("/api/v1/fingers/not-a-finger").status_code == 422


def test_local_media_route_is_authenticated_and_user_scoped(client):
    me = client.get("/api/v1/me").json()["user"]["id"]
    key = blob_key_for_user(me, "captures", "one", "raw.jpg")
    client.app.state.storage.put_bytes(key, b"\xff\xd8\xff jpeg", "image/jpeg")

    ok = client.get(f"/api/v1/media/{key}")
    assert ok.status_code == 200
    assert ok.content == b"\xff\xd8\xff jpeg"
    assert ok.headers["content-type"].startswith("image/jpeg")
    assert "private" in ok.headers["cache-control"]

    other = f"users/{uuid.uuid4()}/captures/one/raw.jpg"
    assert client.get(f"/api/v1/media/{other}").status_code == 404
    assert client.get(f"/api/v1/media/users/{me}/../../secret.jpg").status_code == 404
    assert client.get("/api/v1/media/users/not-me/raw.jpg").status_code == 404


def test_storage_keys_reject_traversal_and_non_images(tmp_path):
    from app.storage import LocalStorage

    storage = LocalStorage(tmp_path, "/api/v1/media")
    with pytest.raises(InvalidStorageKey):
        storage.put_bytes("../secret.jpg", b"nope", "image/jpeg")
    with pytest.raises(InvalidStorageKey):
        storage.put_bytes("notes.txt", b"nope", "text/plain")


def test_azure_read_url_is_a_short_lived_read_sas():
    storage = AzureBlobStorage(
        connection_string=(
            "DefaultEndpointsProtocol=https;AccountName=nailtrackdev;"
            "AccountKey=a2V5;EndpointSuffix=core.windows.net"
        ),
        container="nailtrack-private",
        ttl_seconds=300,
    )
    url = storage.read_url("users/abc/captures/raw.jpg", expires_in=120)
    assert url.startswith(
        "https://nailtrackdev.blob.core.windows.net/nailtrack-private/users/abc/captures/raw.jpg?"
    )
    query = url.split("?", 1)[1]
    assert "sp=r" in query
    assert "sp=rw" not in query
    assert "sp=racw" not in query
    assert "sr=b" in query
