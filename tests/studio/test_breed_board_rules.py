"""Breeding on the board: one probe per candidate, gates that gate,
steering that steers, and a generation inside its time budget."""

import re
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from synthlab import PRESETS
from synthlab.analysis import extract
from synthlab.explore import SpaceStore
from synthlab.explore import probe as probe_mod
from synthlab.explore.breed import DIRECTIONS, generation
from synthlab.explore.probe import QUIET_DB, log_flatness, probe
from synthlab.studio import breedboard
from synthlab.studio.breedboard import (
    _NOVELTY_EPS,
    _POOL,
    _STEER_POOL,
    WILDNESS,
    _Candidate,
    new_generation,
    pick,
)
from synthlab.studio.genome import EXPLORERS
from synthlab.studio.server import create_app
from synthlab.synth import Patch


def _state(tmp_path: Path, patch: Patch) -> SimpleNamespace:
    return SimpleNamespace(breed_starred=[], patch=patch, data_dir=tmp_path, bpm=124.0)


def _run(tmp_path, patch, direction=None, seed=0, amount=WILDNESS["normal"]):
    store = SpaceStore(tmp_path / "space.jsonl")
    tiles = new_generation(
        _state(tmp_path, patch),
        direction,
        rng=np.random.default_rng(seed),
        amount=amount,
        store=store,
    )
    return [Patch.from_dict(store.get(t.id)["patch"]) for t in tiles], tiles


def _cands(patches):
    return [_Candidate(p, [], probe(p)) for p in patches]


# ---------------------------------------------------------------- gates


def test_novelty_gate_rejects_clones():
    parent = PRESETS["bass_reese"]
    pv = probe(parent).vector
    # renamed and one inaudible gene moved: the same sound, twice
    clones = _cands(
        [replace(parent, name="twin"), replace(parent, glide=0.2, name="twin2")]
    )
    for c in clones:
        assert np.linalg.norm(c.m.vector - pv) < _NOVELTY_EPS
    kids = [k for k, _ in generation([parent], n=12, rng=np.random.default_rng(3))]
    novel = [c for c in _cands(kids) if c.m.audible]
    novel = [c for c in novel if np.linalg.norm(c.m.vector - pv) >= _NOVELTY_EPS]
    assert len(novel) >= 8
    chosen = pick(clones + novel, [pv], None)
    assert len(chosen) == 8
    assert not {id(c) for c in clones} & {id(c) for c in chosen}


def test_no_quiet_child_is_picked_while_eight_audible_exist():
    parent = Patch()
    pv = probe(parent).vector
    # silent / sub-rumble candidates sit far from everything, so a plain
    # maximin pick would take them first
    hp = dict(osc1="sine", sub_level=0.0, filter_type="highpass")
    quiet = _cands(
        [
            Patch(**hp, osc1_level=0.1, cutoff=8000.0),
            Patch(**hp, osc1_level=0.3, cutoff=12000.0, release=1.5),
            Patch(**hp, osc1_level=0.2, cutoff=6000.0, attack=1.0, key_track=0.0),
        ]
    )
    assert all(not c.m.audible for c in quiet), [c.m.texture for c in quiet]
    kids = [k for k, _ in generation([parent], n=12, rng=np.random.default_rng(4))]
    loud = [c for c in _cands(kids) if c.m.audible]
    assert len(loud) >= 8
    # without the gate, maximin alone would put a silent card on the board
    blind = breedboard._pick_diverse(quiet + loud, [pv], 8)
    assert any(not c.m.audible for c in blind)
    chosen = pick(quiet + loud, [pv], None)
    assert len(chosen) == 8 and all(c.m.audible for c in chosen)
    # and only as a last resort, to keep the board full
    assert len(pick(quiet + loud[:6], [pv], None)) == 8


def test_generated_boards_have_no_quiet_cards(tmp_path):
    for i, parent in enumerate((Patch(), PRESETS["pad_warm"], PRESETS["bass_reese"])):
        kids, _ = _run(tmp_path, parent, seed=i)
        for k in kids:
            assert probe(k).texture.loudness_db >= QUIET_DB, k.name


# ------------------------------------------------------------- steering


def _mean(direction_score, kids):
    return float(np.mean([direction_score(probe(k)) for k in kids]))


def test_cleaner_lowers_flatness_and_grittier_adds_grit(tmp_path):
    """Whole-note flatness is ~0 for anything pitched, so "cleaner" had no
    signal. Per-frame flatness (200 Hz-8 kHz, in decades) does."""
    starts = (Patch(), PRESETS["lead_supersaw"], PRESETS["pad_warm"])
    flat, clean, grit, plain_grit = [], [], [], []
    for i, start in enumerate(starts):
        for seed in (0, 1):
            plain, _ = _run(tmp_path, start, None, seed=10 * i + seed)
            steered, _ = _run(tmp_path, start, "cleaner", seed=10 * i + seed)
            gritty, _ = _run(tmp_path, start, "grittier", seed=10 * i + seed)
            flat.append(_mean(lambda m: log_flatness(m.texture), plain))
            clean.append(_mean(lambda m: log_flatness(m.texture), steered))
            plain_grit.append(_mean(DIRECTIONS["grittier"], plain))
            grit.append(_mean(DIRECTIONS["grittier"], gritty))
    assert np.mean(clean) < np.mean(flat) - 0.5, (np.mean(clean), np.mean(flat))
    assert np.mean(grit) > np.mean(plain_grit) + 0.5


def test_more_sub_is_scored_at_the_listening_register(tmp_path):
    kids, _ = _run(tmp_path, PRESETS["lead_supersaw"], "more_sub", seed=2)
    kids = kids[:-EXPLORERS]  # the steered cards: the explorers come last
    plain, _ = _run(tmp_path, PRESETS["lead_supersaw"], None, seed=2)
    assert _mean(DIRECTIONS["more_sub"], kids) > _mean(DIRECTIONS["more_sub"], plain)


# ------------------------------------------------------------ one render


def _count_renders(monkeypatch):
    calls = []
    real = probe_mod.probe_audio

    def counted(patch, bpm=probe_mod.PROBE_BPM):
        calls.append(patch.name)
        return real(patch, bpm)

    monkeypatch.setattr(probe_mod, "probe_audio", counted)
    return calls


def test_one_render_per_candidate(tmp_path, monkeypatch):
    """Steering rendered 40 candidates at 0.5 s and then the 20 it kept all
    over again. Now every candidate is measured once and the parent is
    cached: renders == pool size."""
    parent = Patch()
    _run(tmp_path, parent)  # warms the parent cache
    calls = _count_renders(monkeypatch)
    _run(tmp_path, parent, seed=5)
    assert len(calls) == _POOL
    calls.clear()
    _run(tmp_path, parent, "darker", seed=5)
    assert len(calls) == _STEER_POOL


# ------------------------------------------------------------- variety


def _steered_run(tmp_path, direction, seed, gens):
    """`gens` steered generations, starring the three best-scoring cards
    each time (the listener who follows the direction hardest): per
    generation, (mean sibling distance, mean score of the three best)."""
    r = np.random.default_rng(seed)
    store = SpaceStore(tmp_path / "space.jsonl")
    state = _state(tmp_path, Patch())
    out = []
    for _ in range(gens):
        tiles = new_generation(state, direction, rng=r, store=store)
        probes = [probe(Patch.from_dict(store.get(t.id)["patch"])) for t in tiles]
        gaps = [
            float(np.linalg.norm(a.vector - b.vector))
            for i, a in enumerate(probes)
            for b in probes[i + 1 :]
        ]
        scores = [float(DIRECTIONS[direction](m)) for m in probes]
        order = sorted(range(len(tiles)), key=lambda i: -scores[i])
        state.breed_starred = [tiles[i].id for i in order[:3]]
        out.append((float(np.mean(gaps)), float(np.mean(sorted(scores)[-3:]))))
    return out


def test_a_steered_board_keeps_its_variety_over_generations(tmp_path):
    """Steering picked all eight cards from the best-scoring 30 %, which
    are each other's neighbours, so breeding from the best three walked
    the board into one sound: from the default patch toward "brighter"
    (seed 0), mean sibling distance by generation was 3.32 3.86 2.46 2.53
    2.98. With two explorer slots it is 4.46 5.46 5.83 6.05 5.67 — and the
    three best cards still climb (+9.76 -> +13.05 against +13.29 before).
    `tools/breed_spread.py` prints the same over more starts and seeds.
    Adding genes moves the seeded draws; with the current genes this run is
    5.04 5.54 5.48 3.85 4.58 — one generation dips, so the later ones are
    held as a mean — and its best three start higher and climb less
    (+11.76 -> +14.11). Over the tool's three starts x three directions x
    two seeds the board's mean spread rose (4.90 5.02 4.99 4.80 4.84
    before, 5.12 5.20 5.18 5.06 5.08 after) and "brighter"'s best climbed
    as far (+10.54 -> +13.62 before, +10.31 -> +13.89 after)."""
    run = _steered_run(tmp_path, "brighter", 0, 5)
    spreads = [s for s, _ in run]
    assert np.mean(spreads[2:]) > 4.5, spreads
    assert min(spreads) > 3.5, spreads
    best = [b for _, b in run]
    assert best[-1] > best[0] + 2.0, best


def test_explorer_slots_never_change_an_unsteered_board(tmp_path, monkeypatch):
    """The explorers come from the pool the maximin already picks from when
    nothing steers, so a plain generation is the one it always was."""
    kids, _ = _run(tmp_path, PRESETS["pad_warm"], seed=4)
    monkeypatch.setattr(breedboard.genome, "EXPLORERS", 0)
    same, _ = _run(tmp_path, PRESETS["pad_warm"], seed=4)
    assert kids == same
    steered, _ = _run(tmp_path, PRESETS["pad_warm"], "brighter", seed=4)
    monkeypatch.undo()
    explored, _ = _run(tmp_path, PRESETS["pad_warm"], "brighter", seed=4)
    assert explored != steered
    again, _ = _run(tmp_path, PRESETS["pad_warm"], "brighter", seed=4)
    assert explored == again  # seeded: the same litter, the same board


# ---------------------------------------------------------------- budget


def _best_of(n, fn):
    best = float("inf")
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_generation_time_budget(tmp_path):
    """Measured on both dev machines: plain ~150-250 ms, steered ~200-390
    ms (4 probe threads). Best of 5 with ~2x headroom: the full suite runs
    other CPU-heavy tests alongside, and this failed there at best-of-3 while
    passing alone. The regression it guards (rendering the pool twice) is
    pinned exactly by the render-count test; this only catches gross drift."""
    for parent in (Patch(), PRESETS["pad_warm"]):
        _run(tmp_path, parent)  # warm: imports, thread pool, parent cache
        plain = _best_of(5, lambda p=parent: _run(tmp_path, p, seed=1))
        steered = _best_of(5, lambda p=parent: _run(tmp_path, p, "darker", seed=1))
        assert plain < 0.8, f"{parent.name}: {plain * 1000:.0f} ms"
        assert steered < 1.2, f"{parent.name}: {steered * 1000:.0f} ms"


# ----------------------------------------------------------------- tiles


def test_tile_numbers_are_the_probe_and_say_how_it_moved(tmp_path):
    kids, tiles = _run(tmp_path, PRESETS["bass_reese"], seed=3)
    for k, t in zip(kids, tiles, strict=True):
        f = extract(probe_mod.probe_audio(k))
        assert t.centroid == round(f.centroid)
        assert t.vs_parent == "same brightness" or re.fullmatch(
            r"[+-]\d+\.\d oct", t.vs_parent
        )


def test_restore_marks_the_restored_generation(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/fragment/breed/generation", data={})
    client.post("/fragment/breed/generation", data={})
    breed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/breed"}
    html = client.post(
        "/fragment/breed/restore", data={"index": "0"}, headers=breed
    ).text
    dots = re.findall(
        r'<button class="dot gen ?(now)?"[^>]*aria-label="restore generation (\d)"',
        html,
    )
    assert dots == [("now", "1"), ("", "2")], dots
    html = client.get("/view/breed").text
    assert re.search(r'class="dot gen now"[^>]*aria-label="restore generation 1"', html)


def test_breedboard_uses_one_measure_per_candidate():
    # the board no longer calls the separate steer() render pass
    assert not hasattr(breedboard, "steer")


def test_overlapping_generations_share_the_parent_caches_safely(monkeypatch):
    """A generation runs outside the studio lock, so two can overlap. The
    parent caches were bare OrderedDicts: one request's eviction between
    another's `in` and its `move_to_end` was a KeyError. Every touch now
    happens under the caches' lock."""
    import threading
    from collections import OrderedDict

    class Guarded(OrderedDict):
        def _held(self):
            assert breedboard._CACHE_LOCK.locked(), "cache touched unlocked"

        def __contains__(self, key):
            self._held()
            return super().__contains__(key)

        def __getitem__(self, key):
            self._held()
            return super().__getitem__(key)

        def __setitem__(self, key, value):
            self._held()
            super().__setitem__(key, value)

        def move_to_end(self, key, last=True):
            self._held()
            super().move_to_end(key, last)

        def popitem(self, last=True):
            self._held()
            return super().popitem(last)

    monkeypatch.setattr(breedboard, "_PARENT_CACHE", Guarded())
    monkeypatch.setattr(breedboard, "_PARENT_CACHE_MAX", 2)
    monkeypatch.setattr(breedboard, "probe", lambda p, bpm: None)  # too quiet
    patches = [replace(Patch(), cutoff=300.0 + i) for i in range(6)]
    errors: list[BaseException] = []

    def hammer():
        try:
            for _ in range(200):
                for p in patches:
                    assert breedboard._parent_probe(p, 124.0) is None
        except BaseException as err:  # noqa: BLE001 - reported below
            errors.append(err)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors[0]
    assert len(breedboard._PARENT_CACHE) <= 2


def test_a_card_says_what_it_changed_from_its_parent():
    """`breedboard.changes`: the genes heard that moved, the
    biggest first — a choice outranks any number — with the two values as a
    card prints them and their places on the gene's range; a nudge under
    3 % of the range is no change, and two equal sounds have none."""
    p = Patch()
    other = "highpass" if p.filter_type != "highpass" else "lowpass"
    c = replace(p, cutoff=p.cutoff * 3, filter_type=other, release=p.release + 0.4)
    rows = breedboard.changes(p, c)
    assert [r["label"] for r in rows][:1] == ["Filter type"]
    assert rows[0]["a"] == rows[0]["b"] == -1.0
    cut = next(r for r in rows if r["label"] == "Cutoff")
    assert 0 <= cut["a"] < cut["b"] <= 1 and cut["to"].endswith("kHz")
    assert breedboard.changes(p, p) == []
    assert breedboard.changes(p, replace(p, cutoff=p.cutoff * 1.001)) == []
