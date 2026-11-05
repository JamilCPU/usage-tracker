import pytest

from usage_sync.config import Config, ConfigError
from usage_sync.redact import PRIVATE_TITLE_MARKERS

BASE = {"ingest_url": "https://i.example", "device": "pc"}


@pytest.mark.parametrize(
    "over",
    [
        {"interval_seconds": 0},
        {"interval_seconds": -5},
        {"batch_size": 0},
        {"batch_size": 5001},
        {"settle_seconds": -1},
        {"overlap_seconds": -1},
        {"batch_size": "10"},
        {"interval_seconds": True},
    ],
)
def test_out_of_range_values_rejected(over, tmp_path):
    with pytest.raises(ConfigError):
        Config(**BASE, token_file=tmp_path / "t", **over)


def test_boundary_values_accepted(tmp_path):
    cfg = Config(
        **BASE,
        token_file=tmp_path / "t",
        interval_seconds=1,
        batch_size=5000,
        settle_seconds=0,
        overlap_seconds=0,
    )
    assert cfg.batch_size == 5000


def test_title_markers_default_and_override(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('ingest_url = "https://i.example"\ndevice = "pc"\n', encoding="utf-8")
    assert Config.load(f).redact_title_markers == list(PRIVATE_TITLE_MARKERS)
    f.write_text(f.read_text() + 'redact_title_markers = ["x"]\n', encoding="utf-8")
    assert Config.load(f).redact_title_markers == ["x"]
