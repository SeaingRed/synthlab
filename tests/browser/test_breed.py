"""Journey 7: Breed — a litter, a card's ▶, "in your mix", a star, the
next generation.

Proves: the board draws eight cards, a card's ▶ fetches its audition WAV
(200, audio/wav) and the page plays it (the master analyser carries
signal), "in your mix" starts the loop, a star reaches the server and the
next generation replaces the board and grows the history strip. Does not
prove the eight sound different, or good.
"""

from __future__ import annotations

NEXT = "#view button.breed-go"  # the one primary (not a .ghost)


def _ids(page) -> list[str]:
    return page.evaluate(
        "[...document.querySelectorAll('#breed-board .tile')]"
        ".map((t) => t.dataset.tileId)"
    )


def test_audition_star_and_breed_again(studio, server):
    page = studio.open("breed")  # an empty board breeds its first litter
    studio.settle()
    first = _ids(page)
    assert len(first) == 8 and len(set(first)) == 8

    # a card's ▶: its own audition render, played through the page's context
    with page.expect_response(
        lambda r: r.url.endswith(f"/breed/{first[0]}/audition.wav")
    ) as hit:
        page.locator("#breed-board .tile button.audition").first.click()
    assert hit.value.status == 200
    assert hit.value.headers["content-type"] == "audio/wav"
    studio.wait_sound(floor=0.01)
    assert studio.ctx_state() == "running"
    studio.wait_silence(timeout=20)  # a one-shot: it ends on its own

    # "in your mix": the loop Use would land, playing until stopped
    with page.expect_response(
        lambda r: r.url.endswith("/fragment/breed/audition")
    ) as hit:
        page.locator("#breed-board .tile .loop-btn").nth(1).click()
    assert hit.value.status == 200
    studio.wait_sound()
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.click("#global-stop")  # Breed has no transport of its own
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    studio.wait_silence()

    page.locator("#breed-board .tile input.star").nth(2).click()
    page.wait_for_selector("#breed-board .tile.starred")
    assert list(server.state.breed_starred) == [first[2]]
    assert page.locator("#breed-board .tile.starred").count() == 1

    page.click(NEXT)
    page.wait_for_function(
        "(old) => { const t = document.querySelector('#breed-board .tile');"
        " return !!t && !old.includes(t.dataset.tileId); }",
        arg=first,
    )
    second = _ids(page)
    assert len(second) == 8 and not set(second) & set(first)
    assert page.locator("#gen-strip .gen").count() == 2
    studio.settle()
