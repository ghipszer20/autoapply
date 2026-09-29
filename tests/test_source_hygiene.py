from pathlib import Path

SRC = Path(__file__).parent.parent / "src"


def test_no_control_characters_in_source():
    """Regex word boundaries have been mangled into literal backspaces by shell quoting before."""
    bad = [str(p) for p in SRC.rglob("*.py") if any(ch in p.read_text(encoding="utf-8") for ch in "\x08\x0c\x00")]
    assert bad == []
