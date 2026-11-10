"""Test helper: open the song's i-th section in the working loop. There is
no index-based `StudioState.load_scene` wrapper; tests that walk sections
by position use this."""


def load_section(s, index) -> bool:
    sections = s.song["sections"]
    if not isinstance(index, int) or not 0 <= index < len(sections):
        return False
    return s.section_to_loop(sections[index]["id"])
