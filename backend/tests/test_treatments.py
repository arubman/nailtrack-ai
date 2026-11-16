from datetime import timedelta

from app.database import utcnow
from app.models import Treatment, TreatmentCategory


def _payload(**overrides):
    started = utcnow().replace(microsecond=0)
    body = {
        "started_at": started.isoformat(),
        "category": "TOPICAL_OIL",
        "target_fingers": ["L_INDEX", "l_thumb"],
        "notes": "  jojoba  ",
    }
    body.update(overrides)
    return body


def test_treatment_crud_is_scoped_to_the_dev_user(client, db):
    created = client.post("/api/v1/treatments", json=_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["target_fingers"] == ["L_INDEX", "L_THUMB"]
    assert body["notes"] == "jojoba"
    assert body["velocity_modifier"] == 1.0
    me = client.get("/api/v1/me").json()["user"]["id"]
    assert body["user_id"] == me

    other = Treatment(
        user_id=_other_user(db).id,
        started_at=utcnow(),
        category=TreatmentCategory.SUPPLEMENT_BIOTIN,
        target_fingers_json=["ALL"],
        notes="someone else",
    )
    db.add(other)
    db.commit()

    listing = client.get("/api/v1/treatments")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == body["id"]
    assert client.get(f"/api/v1/treatments/{other.id}").status_code == 404

    patched = client.patch(
        f"/api/v1/treatments/{body['id']}",
        json={"velocity_modifier": 1.25, "notes": None},
    )
    assert patched.status_code == 200
    assert patched.json()["velocity_modifier"] == 1.25
    assert patched.json()["notes"] is None
    assert patched.json()["category"] == "TOPICAL_OIL"

    finger = client.get("/api/v1/treatments", params={"finger_code": "L_THUMB"})
    assert finger.json()["total"] == 1
    other_finger = client.get("/api/v1/treatments", params={"finger_code": "R_PINKY"})
    assert other_finger.json()["total"] == 0

    assert client.delete(f"/api/v1/treatments/{body['id']}").status_code == 204
    assert client.get(f"/api/v1/treatments/{body['id']}").status_code == 404


def test_treatment_validation(client):
    started = utcnow()
    ended = started - timedelta(days=1)
    backwards = client.post(
        "/api/v1/treatments",
        json=_payload(ended_at=ended.isoformat()),
    )
    assert backwards.status_code == 422

    combined = client.post(
        "/api/v1/treatments",
        json=_payload(target_fingers=["ALL", "L_THUMB"]),
    )
    assert combined.status_code == 422

    zero = client.post("/api/v1/treatments", json=_payload(velocity_modifier=0))
    assert zero.status_code == 422

    unknown = client.post("/api/v1/treatments", json=_payload(category="PRESCRIPTION"))
    assert unknown.status_code == 422


def test_active_filter(client):
    past = utcnow() - timedelta(days=10)
    client.post(
        "/api/v1/treatments",
        json=_payload(
            started_at=past.isoformat(),
            ended_at=(past + timedelta(days=1)).isoformat(),
            category="GROOMING_TRIM",
            target_fingers=["ALL"],
        ),
    )
    client.post("/api/v1/treatments", json=_payload(category="TRAUMA_NOTE", target_fingers=["ALL"]))
    active = client.get("/api/v1/treatments", params={"active_only": "true"})
    assert active.status_code == 200
    assert active.json()["total"] == 1
    assert active.json()["items"][0]["category"] == "TRAUMA_NOTE"
    # ["ALL"] applies to every finger, including ones that were not named.
    scoped = client.get(
        "/api/v1/treatments",
        params={"finger_code": "R_RING", "active_only": "true"},
    )
    assert scoped.json()["total"] == 1


def _other_user(db):
    from app.models import User

    user = User(external_subject="someone-else", email="someone@example.com", display_name="Someone")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
