from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import clear_settings_cache
from app.database import dispose_engine, get_engine, utcnow
from app.models import FingerBaseline, FingerCode, MaskSource, NailCapture, Treatment, TreatmentCategory, User

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_round_trip(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    clear_settings_cache()
    dispose_engine()

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    names = set(inspect(get_engine()).get_table_names())
    assert {"users", "finger_baselines", "nail_captures", "treatments", "alembic_version"} <= names

    with Session(bind=get_engine(), expire_on_commit=False) as session:
        user = User(external_subject="migrated", email="migrated@example.com")
        baseline = FingerBaseline(
            user=user,
            finger_code=FingerCode.R_PINKY,
            calibrated_width_mm=8.5,
            scale_reference_width_mm=85.60,
            reference_white_json={"source": "id1_card_white", "target": "D65"},
        )
        capture = NailCapture(
            user=user,
            baseline=baseline,
            finger_code=FingerCode.R_PINKY,
            mask_source=MaskSource.MODEL,
            segmentation_mask_key="users/x/captures/mask.png",
        )
        treatment = Treatment(
            user=user,
            started_at=utcnow(),
            category=TreatmentCategory.BUILDER_GEL_OVERLAY,
            target_fingers_json=["R_PINKY"],
            velocity_modifier=1.1,
        )
        session.add_all([user, baseline, capture, treatment])
        session.commit()
        assert capture.baseline_id == baseline.id

    command.downgrade(config, "base")
    left = set(inspect(get_engine()).get_table_names())
    assert "users" not in left
    assert "nail_captures" not in left
    dispose_engine()
    clear_settings_cache()
