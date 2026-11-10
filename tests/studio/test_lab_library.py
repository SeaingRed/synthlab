"""The Lab's chops and the clip library drawer, together.

Each half is tested with fixtures for the other; this is the seams with
both halves' real code:

- the meta a real Keep writes (`labchops.clip_info`) is what the drawer
  groups by and links from (`cliplib.kind_of` / `lab_ref`), and the link
  lands on the Lab with that song open and that chop selected;
- the Stems group's links are the stem routes', for a song holding one set
  and one holding two — one listing behind both (`sources.stem_sets`);
- Keep and Update on the Lab page re-send the drawer; Update, with the clip
  placed in the song, re-renders the song and names where it is used in
  the words delete uses (`StudioState.clip_usage`, the undo ring included);
- a clip deleted from the drawer: its chop offers Keep again; renamed: the
  chop follows it; the song deleted in the Lab: "Open in Lab" is off and
  the Stems group drops it.

Separation is the fake of `lab_helpers`. Not proven here: how any of it
sounds.
"""

from __future__ import annotations

import html as html_lib
import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from synthlab import stems
from synthlab.studio import cliplib, labchops, sources
from synthlab.studio.clips import clip_hash, list_clips, save_clip
from synthlab.studio.server import create_app
from synthlab.studio.service import StudioService
from tests.studio.lab_helpers import (
    LAB,
    SixBands,
    fake_separation,
    separate,
    song_wav,
    source_id,
    upload,
)
from tests.studio.test_song_routes import use_song

FOUR, SIX = stems.HTDEMUCS, stems.HTDEMUCS_6S


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture(scope="module")
def wav() -> bytes:
    return song_wav()  # 8 bars at 120 bpm


@pytest.fixture
def client(tmp_path, wav, monkeypatch):
    """A studio with the routes' fixture song, and "my song" in the Lab on
    a 120 bpm grid, separated (the fake), its vocals stem ticked."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        assert upload(c, wav).status_code == 200
        sid = source_id(c)
        sources.update_meta(
            _dir(c), sid, lambda m: m["user"].update(bpm=120.0, offset=0.0)
        )
        separate(c, monkeypatch)
        _lab(c, "/lab/region", source=sid, stems="1", **{"stem.vocals": "on"})
        yield c


def _dir(client):
    return client.app.state.svc.data_dir


def _state(client):
    return client.app.state.studio


def _lab(client, path: str, **data):
    r = client.post(path, data=data, headers=LAB)
    assert r.status_code == 200, r.text[:300]
    return r


def _chops(client) -> list[dict]:
    return sources.read_meta(_dir(client), source_id(client))["chops"]


def _kept(client, at: str = "4", **more) -> tuple[dict, str]:
    """Cut a chop at `at` seconds and keep it: (the chop, its clip's name)."""
    sid = source_id(client)
    before = {c["id"] for c in _chops(client)}
    _lab(client, "/lab/chop", source=sid, op="split", at=at)
    (chop,) = [c for c in _chops(client) if c["id"] not in before]
    r = _lab(client, "/lab/chop/keep", source=sid, chop=chop["id"], **more)
    return chop, r.headers["X-Clip-Name"]


def _rename(client, old: str, new: str):
    """The drawer's ✎, from the Lab page."""
    return client.post(
        "/fragment/clip/rename", data={"name": old, "new": new}, headers=LAB
    )


def _quieter(client, cid: str, db: str) -> None:
    """Change what the chop bakes to: its cleaning's gain."""
    _lab(
        client,
        "/lab/chop",
        source=source_id(client),
        op="clean",
        chop=cid,
        level="gain",
        gain_db=db,
    )


def _drawer(client, view: str = "editor", **query) -> str:
    r = client.get("/fragment/clips", params=query, headers=_hx(view))
    assert r.status_code == 200, r.text[:300]
    return r.text


def _row(html: str, cid: str) -> str:
    """One chop's row of a Lab page or body."""
    return html.split(f'id="chop-{cid}"')[1].split("</li>")[0]


def _clip_row(html: str, name: str) -> str:
    return html.split(f'<li class="cd-clip" data-name="{name}"')[1].split("</li>")[0]


# --- Keep -> the drawer ---------------------------------------------------------------


def test_a_kept_chop_is_listed_under_its_song_and_stems_and_links_to_its_chop(client):
    sid = source_id(client)
    chop, name = _kept(client)
    (clip,) = list_clips(_dir(client))
    # what a real Keep wrote is what the drawer reads
    assert cliplib.lab_ref(clip) == (sid, chop["id"])
    assert cliplib.kind_of(clip)[1] == "my song · vocals"
    html = _drawer(client, "voice")
    # the first clip group (the Loops are above every group)
    group = html.split('<section class="cd-group" data-group="lab"')[1]
    assert 'aria-label="my song · vocals"' in group.split("</section>")[0]
    assert f'data-name="{name}"' in group
    assert "s · 120 bpm" in _clip_row(html, name)
    href = f"/view/lab?source={sid}&amp;chop={chop['id']}"
    assert f'href="{href}"' in _clip_row(html, name)
    # a chop of the recording itself (no stem ticked) is its own group
    _lab(client, "/lab/region", source=sid, stems="1")
    _mix, mix_name = _kept(client, "10")
    labels = re.findall(r'data-group="lab" aria-label="([^"]*)"', _drawer(client))
    assert labels == ["my song · mix", "my song · vocals"]
    # the link lands on the Lab with that song open and that chop the region:
    # another song was opened since, and another chop selected
    upload(client, song_wav(bars=4), name="other.wav")
    assert source_id(client) != sid
    page = client.get(html_lib.unescape(href))
    assert page.status_code == 200
    assert 'aria-current="true"' in _row(page.text, chop["id"]).split(">")[0]
    meta = sources.read_meta(_dir(client), sid)
    assert meta["chop"] == chop["id"] and meta["picked"] == ["vocals"]
    assert sources.list_sources(_dir(client))[0]["id"] == sid
    assert 'id="lab-notice"' not in page.text


def test_keep_and_update_on_the_lab_page_re_send_the_drawer(client):
    """The Lab has no region of its own, but the drawer is on its page: a
    clip made or rewritten there moves the drawer's watch (the store's
    listing), and it comes back out of band, morphed."""
    sid = source_id(client)
    _lab(client, "/lab/chop", source=sid, op="split", at="4")
    (chop,) = _chops(client)
    assert 'clip-count">0<' in client.get("/view/lab").text
    r = _lab(client, "/lab/chop/keep", source=sid, chop=chop["id"])
    assert 'id="clip-library" hx-swap-oob="outerMorph"' in r.text
    assert 'clip-count">1<' in r.text and 'data-name="chop 1"' in r.text
    assert "cd-take" not in r.text and "Open in Lab" in r.text  # the Lab's drawer
    # Update rewrites the file: the listing moves again
    _quieter(client, chop["id"], "-6")
    r = _lab(client, "/lab/chop/keep", source=sid, chop=chop["id"], update="1")
    assert r.headers["X-Clip-Name"] == "chop 1"
    assert 'id="clip-library" hx-swap-oob="outerMorph"' in r.text
    assert 'clip-count">1<' in r.text


# --- the Stems group and the stem routes ----------------------------------------------


def _stem_links(html: str, sid: str) -> list[str]:
    group = html.split('data-group="stems"')[1]
    row = group.split(f'data-source="{sid}"')[1].split("</li>")[0]
    return [html_lib.unescape(h) for h in re.findall(r'href="(/lab/[^"]*)"', row)]


def test_every_stem_link_of_the_drawer_downloads_for_one_set_and_for_two(
    client, monkeypatch
):
    sid = source_id(client)
    links = _stem_links(_drawer(client), sid)
    assert links == [
        *(f"/lab/{sid}/stem/{s}.flac?model={FOUR.id}" for s in FOUR.sources),
        f"/lab/{sid}/stems.zip?model={FOUR.id}",
    ]
    # a second set: the six-stem one is the set shown now, and is first
    fake_separation(client, monkeypatch, SixBands())
    _lab(client, "/lab/separate", source=sid, model=SIX.id)
    client.app.state.svc.lab.join()
    links = _stem_links(_drawer(client), sid)
    assert links == [
        *(f"/lab/{sid}/stem/{s}.flac?model={SIX.id}" for s in SIX.sources),
        f"/lab/{sid}/stems.zip?model={SIX.id}",
        *(f"/lab/{sid}/stem/{s}.flac?model={FOUR.id}" for s in FOUR.sources),
        f"/lab/{sid}/stems.zip?model={FOUR.id}",
    ]
    d = sources.source_dir(_dir(client), sid)
    for link in links:
        r = client.get(link)
        assert r.status_code == 200, link
        assert r.headers["content-disposition"].startswith('attachment; filename="my')
        model = link.split("model=")[1]
        if "stems.zip" in link:
            names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
            assert names == [
                f"my song - {s}.flac" for s in sources.stem_names(model)
            ], link
        else:
            stem = link.split("/stem/")[1].split(".flac")[0]
            assert r.content == (d / f"stems-{model}" / f"{stem}.flac").read_bytes()


def test_the_drawers_stems_are_the_one_listing_the_routes_serve(client, monkeypatch):
    """`cliplib.stem_sets` is a view of `sources.stem_sets`: the same
    songs and sets, and the watch value moves whenever that listing does."""
    data_dir, sid = _dir(client), source_id(client)
    svc = client.app.state.svc

    def watch():
        with svc.state.lock:
            return svc.watch_page("editor")["clip-library"]

    def same() -> None:
        listed = sources.stem_sets(data_dir)
        drawn = cliplib.stem_sets(data_dir)
        assert sorted(s["id"] for s in listed) == sorted(s["id"] for s in drawn)
        for row in drawn:
            src = next(s for s in listed if s["id"] == row["id"])
            assert {s["model"]: tuple(s["stems"]) for s in row["sets"]} == src["sets"]
            assert row["sets"][0]["model"] == src["model"] and row["name"] == "my song"

    same()
    seen = [watch()]
    fake_separation(client, monkeypatch, SixBands())
    _lab(client, "/lab/separate", source=sid, model=SIX.id)
    svc.lab.join()
    same()
    seen.append(watch())
    # a set made by another build of the model: the meta names it, so the
    # routes serve it — listed with links that answer; a stray folder that
    # no route would serve is not listed
    d = sources.source_dir(data_dir, sid)
    (d / f"stems-{FOUR.id}").rename(d / "stems-htdemucs-0ld")
    (d / "stems-somebody-elses").mkdir()
    sources.update_meta(
        data_dir, sid, lambda m: m["stems"].update(model="htdemucs-0ld")
    )
    same()
    seen.append(watch())
    (row,) = cliplib.stem_sets(data_dir)
    assert [s["model"] for s in row["sets"]] == ["htdemucs-0ld", SIX.id]
    links = _stem_links(_drawer(client), sid)
    assert len(links) == 5 + 7 and "somebody" not in "".join(links)
    assert [client.get(link).status_code for link in links] == [200] * 12
    sources.delete(data_dir, sid)
    assert cliplib.stem_sets(data_dir) == sources.stem_sets(data_dir) == []
    seen.append(watch())
    assert len(set(seen)) == len(seen)


# --- Update clip ---------------------------------------------------------------------


def test_update_with_the_clip_placed_re_renders_the_song_and_says_where(
    client, monkeypatch
):
    """Update's confirm reads `StudioState.clip_usage`, as the drawer's
    delete does: the live session and the song's undo / redo steps, in the
    same words. After it the section plays the new audio."""
    sid, s = source_id(client), _state(client)
    chop, name = _kept(client)
    assert "It changes everywhere it is used: nowhere in this session yet." in _row(
        client.get("/view/lab").text, chop["id"]
    )
    for section in ("s1", "s2"):
        r = client.post(
            f"/song/section/{section}/clip", data={"clip": name}, headers=_hx("song")
        )
        assert r.status_code == 200
    usage = s.clip_usage()[name]
    assert usage == {"song": 2, "undo": 1}
    row = _row(client.get("/view/lab").text, chop["id"])
    where = "2 takes in the song, 1 undo step of the song"
    confirm = re.search(r'hx-confirm="([^"]*)"', row).group(1)
    assert f"Write over the clip “{name}”" in confirm
    assert f"everywhere it is used: {where} (Ctrl+Z brings the take back" in confirm
    assert f"used: {where}" in row
    # the drawer's delete says the same places about the same clip
    asks = re.search(r'hx-confirm="([^"]*)"', _clip_row(_drawer(client), name)).group(1)
    assert where in asks
    assert cliplib.where_used(usage).split(" (")[0] == where
    # Update: the section that holds the take is rendered again, differently
    before = client.get("/song/section/s1.wav")
    assert before.status_code == 200
    was = clip_hash(_dir(client), name)
    resets: list = []
    real = StudioService.reset_caches
    monkeypatch.setattr(
        StudioService, "reset_caches", lambda self: resets.append(1) or real(self)
    )
    _quieter(client, chop["id"], "-12")
    r = _lab(client, "/lab/chop/keep", source=sid, chop=chop["id"], update="1")
    assert r.headers["X-Clip-Name"] == name and resets == [1]
    assert clip_hash(_dir(client), name) != was
    assert [c["name"] for c in list_clips(_dir(client))] == [name]
    after = client.get("/song/section/s1.wav")
    assert after.status_code == 200 and after.content != before.content
    # the song still holds it, by name, and the drawer (re-sent) still says so
    assert s.clip_usage()[name] == usage
    assert "2 takes in the song" in r.text


def test_a_clip_only_an_undo_step_holds_is_named_by_update_too(client):
    """The count the Lab half took from the live session alone missed this
    one: placed, then undone — Ctrl+Shift+Z brings the take back."""
    chop, name = _kept(client)
    client.post("/song/section/s1/clip", data={"clip": name}, headers=_hx("song"))
    client.post("/song/undo", headers=_hx("song"))
    assert _state(client).clip_usage() == {name: {"undo": 1}}
    row = _row(client.get("/view/lab").text, chop["id"])
    assert "everywhere it is used: 1 undo step of the song" in row
    assert "nowhere in this session" not in row


# --- delete and rename from the drawer -----------------------------------------------


def test_a_clip_deleted_from_the_drawer_leaves_its_chop_offering_keep(client):
    sid = source_id(client)
    chop, name = _kept(client)
    assert f'id="chop-update-{chop["id"]}"' in client.get("/view/lab").text
    # from the drawer on the Lab page itself: the chop list rides along
    r = client.post("/fragment/clip/delete", data={"name": name}, headers=LAB)
    assert r.status_code == 200 and list_clips(_dir(client)) == []
    assert 'id="lab-chops"' in r.text and 'hx-swap-oob="outerMorph"' in r.text
    for html in (r.text, client.get("/view/lab").text):
        row = _row(html, chop["id"])
        assert "Update clip" not in row and "Keep as new" not in row
        assert f'id="chop-keep-{chop["id"]}"' in row and ">Keep</button>" in row
    # the chop still names the clip; nothing reads that as a clip to write over
    assert _chops(client)[0]["clip"] == name
    assert labchops.own_clip(_dir(client), sid, _chops(client)[0]) == ""
    # a page that had not redrawn yet posts Update: a new clip, not an error
    r = _lab(client, "/lab/chop/keep", source=sid, chop=chop["id"], update="1")
    assert (
        r.headers["X-Clip-Name"] == name and "Kept as clip" in r.headers["HX-Trigger"]
    )
    # on any other page the drawer answers alone
    r = client.post("/fragment/clip/delete", data={"name": name}, headers=_hx("song"))
    assert r.status_code == 200 and 'id="lab-chops"' not in r.text
    for view in ("lab", "song", "voice", "editor"):
        assert client.get(f"/view/{view}").status_code == 200


@pytest.mark.parametrize("route", ["drawer", "take"])
def test_a_renamed_clip_is_still_its_chops_clip(client, route):
    """The chop records the clip's name: a rename takes the chop with it,
    so Update goes on writing over that clip (never a clip that later
    takes the old name) and Keep does not quietly make a second one."""
    sid = source_id(client)
    chop, name = _kept(client)
    if route == "drawer":
        r = _rename(client, name, "verse hit")
        # on the Lab page the row follows at once
        assert "Write over the clip “verse hit”" in _row(r.text, chop["id"])
    else:  # the Song page's inline take rename moves the same file
        r = client.post(
            f"/song/take/{name}/rename", data={"name": "verse hit"}, headers=_hx("song")
        )
    assert r.status_code == 200, r.text[:300]
    assert [c["name"] for c in list_clips(_dir(client))] == ["verse hit"]
    assert _chops(client)[0]["clip"] == "verse hit"
    assert labchops.own_clip(_dir(client), sid, _chops(client)[0]) == "verse hit"
    row = _row(client.get("/view/lab").text, chop["id"])
    assert "Write over the clip “verse hit”" in row and "Update clip" in row
    # somebody else's clip takes the old name: Update leaves it alone
    save_clip(_dir(client), name, [[0.25, 0.25]] * 4410, meta={"source": "jam"})
    other = clip_hash(_dir(client), name)
    was = clip_hash(_dir(client), "verse hit")
    _quieter(client, chop["id"], "-6")
    r = _lab(client, "/lab/chop/keep", source=sid, chop=chop["id"], update="1")
    assert r.headers["X-Clip-Name"] == "verse hit"
    assert clip_hash(_dir(client), "verse hit") != was
    assert clip_hash(_dir(client), name) == other
    assert len(list_clips(_dir(client))) == 2


def test_a_rename_moves_no_chop_that_is_not_the_clips_own(client):
    """Only the chop the clip's own meta names, and only while it still
    points at the old name: a chop whose `clip` was typed into the meta,
    another chop of the same name, a clip that names a song that is gone."""
    sid = source_id(client)
    first, name = _kept(client, "4")
    _lab(client, "/lab/chop", source=sid, op="split", at="10")
    second = next(c for c in _chops(client) if c["id"] != first["id"])

    def point(m: dict) -> None:  # a hand-edited meta: both chops name the clip
        for c in m["chops"]:
            c["clip"] = name

    sources.update_meta(_dir(client), sid, point)
    _rename(client, name, "a")
    by_id = {c["id"]: c["clip"] for c in _chops(client)}
    assert by_id == {first["id"]: "a", second["id"]: name}
    # a jam, a clip whose chop is gone, a clip whose song is gone: nothing moves
    save_clip(_dir(client), "jam", [[0.2, 0.2]] * 4410, meta={"source": "jam"})
    _rename(client, "jam", "j2")
    _lab(client, "/lab/chop", source=sid, op="remove", chop=first["id"])
    r = _rename(client, "a", "b")
    assert r.status_code == 200
    assert [c["clip"] for c in _chops(client)] == [name]
    sources.delete(_dir(client), sid)
    r = _rename(client, "b", "c")
    assert r.status_code == 200 and 'id="lab-chops"' not in r.text
    assert sorted(c["name"] for c in list_clips(_dir(client))) == ["c", "j2"]


def test_a_song_deleted_in_the_lab_turns_its_clips_link_off_and_leaves_stems(client):
    sid = source_id(client)
    _chop, name = _kept(client)
    html = _drawer(client)
    assert 'data-group="stems"' in html and f"/view/lab?source={sid}&amp;chop=" in html
    # deleted from the Lab page: the drawer comes back with the answer
    r = _lab(client, "/lab/delete", source=sid)
    assert 'id="clip-library" hx-swap-oob="outerMorph"' in r.text
    for html in (r.text, _drawer(client, "song")):
        row = _clip_row(html, name)
        assert 'aria-disabled="true"' in row and "href=" not in row
        assert "no longer in the Lab" in row
        assert 'data-group="stems"' not in html and "/stem/" not in html
    # the clip is a copy: it stays, still under its song's name, still plays
    assert [c["name"] for c in list_clips(_dir(client))] == [name]
    assert 'aria-label="my song · vocals"' in _drawer(client)
    assert client.get(f"/clip/{name}.wav").status_code == 200
    # a link someone still has open lands on the Lab, with a line saying so
    page = client.get(f"/view/lab?source={sid}&chop=abcd")
    assert page.status_code == 200 and "no longer in the Lab" in page.text
