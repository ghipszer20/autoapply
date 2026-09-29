from pathlib import Path

import pytest

from autoapply.config import ConfigError, load_config

ROOT = Path(__file__).parent.parent


def test_repo_config_loads():
    cfg = load_config(ROOT / "config.yaml")
    assert "Software" in cfg.filter.categories
    assert cfg.boards["greenhouse"]["wehrtyou"] == "Hudson River Trading"


def test_unset_citizenship_is_readable_error(tmp_path):
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    import re
    bad = re.sub(r"us_citizen: \S+", "us_citizen: REPLACE_ME", text)
    (tmp_path / "c.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError, match="us_citizen"):
        load_config(tmp_path / "c.yaml")


def test_bad_regex_is_readable_error(tmp_path):
    (tmp_path / "c.yaml").write_text(
        "filter:\n  categories: []\n  allowed_terms: []\n  title_include: ['(']\n  title_exclude: []\n"
        "  require_bachelors: true\n  us_only: true\n  us_citizen: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="title_include"):
        load_config(tmp_path / "c.yaml")
