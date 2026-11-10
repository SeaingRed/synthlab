"""The one filename sanitizer: clips.py and state.py
used to carry their own copies, and the clip one was missing the Windows
reserved-device-name guard the preset one had."""

import pytest

from synthlab.studio.files import RESERVED, safe_name


@pytest.mark.parametrize("raw", ["CON", "nul", "com1", "LPT9", "con.txt"])
def test_safe_name_blocks_reserved(raw):
    assert safe_name(raw).upper().split(".")[0] not in RESERVED


def test_safe_name_strips_leading_dots_and_falls_back():
    assert safe_name("...") == "untitled"


def test_safe_name_clamps_length():
    assert len(safe_name("x" * 300)) <= 80


def test_safe_name_passes_through_normal_name():
    assert safe_name("My Bass! (v2)") == "My Bass! (v2)"


def test_safe_name_custom_fallback():
    assert safe_name("...", fallback="clip") == "clip"
    assert safe_name("CON", fallback="clip") == "clip"


def test_clip_and_preset_share_sanitizer():
    """clips._safe and StudioState._safe are gone — both modules import
    the one safe_name() instead."""
    from synthlab.studio import clips, state

    assert not hasattr(clips, "_safe")
    assert not hasattr(state.StudioState, "_safe")
