import pytest

from app.reference_white import (
    von_kries_gains_neutral_gray,
    von_kries_gains_white_to_d65,
)
from app.schemas import reference_white_from_patch


def test_white_patch_maps_to_d65():
    rgb = (0.5, 0.25, 0.8)
    gains = von_kries_gains_white_to_d65(rgb)
    adapted = tuple(channel * gain for channel, gain in zip(rgb, gains))
    assert adapted == pytest.approx((1.0, 1.0, 1.0))


def test_gray_patch_is_neutral_without_being_blown_out_to_white():
    rgb = (0.2, 0.4, 0.3)
    gains = von_kries_gains_neutral_gray(rgb)
    adapted = tuple(channel * gain for channel, gain in zip(rgb, gains))
    assert adapted[0] == pytest.approx(adapted[1])
    assert adapted[1] == pytest.approx(adapted[2])
    assert sum(adapted) / 3 == pytest.approx(sum(rgb) / 3)
    assert adapted[0] != pytest.approx(1.0)


def test_reference_record_uses_the_card_white_not_a_gray_target():
    record = reference_white_from_patch(
        mean_rgb=(0.8, 0.75, 0.7),
        source="id1_card_white",
        patch_norm_bbox=(0.1, 0.6, 0.3, 0.2),
    )
    assert record.target == "D65"
    assert record.source == "id1_card_white"
    stored = record.model_dump(mode="json")
    assert stored["space"] == "linear_srgb"
    assert stored["von_kries_gains"][0] == pytest.approx(1 / 0.8)


def test_non_positive_channel_is_rejected():
    with pytest.raises(ValueError):
        von_kries_gains_white_to_d65((0.0, 1.0, 1.0))
