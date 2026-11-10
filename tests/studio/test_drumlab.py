import inspect

import numpy as np

from synthlab import drums
from synthlab.analysis.features import extract
from synthlab.studio.drumlab import DRUM_PARAMS, VOICE_PRESETS, preset_of
from synthlab.studio.render import RenderInputs
from synthlab.studio.state import StudioState
from synthlab.synth import preset

VOICE_FN = {
    "kick": drums.kick,
    "snare": drums.snare,
    "hat": drums.hat,
    "ohat": drums.hat,  # open hat = hat(open_hat=True, ...)
    "clap": drums.clap,
    "tom": drums.tom,
    "rim": drums.rim,
    "crash": drums.crash,
}


def test_drum_params_names_are_subset_of_engine_signature():
    for voice, specs in DRUM_PARAMS:
        sig_names = set(inspect.signature(VOICE_FN[voice]).parameters)
        for spec in specs:
            assert spec["name"] in sig_names, (voice, spec["name"])


def test_every_preset_renders_audibly():
    for voice, presets in VOICE_PRESETS.items():
        for p in presets:
            params = {k: v for k, v in p.items() if k != "name"}
            audio = drums.hit(voice, **params)
            peak = float(np.max(np.abs(audio)))
            assert peak > 0.1, (voice, p["name"], peak)


def test_808_sub_kick_has_more_sub_than_punch():
    punch = preset_of("kick", "Punch")
    sub808 = preset_of("kick", "808 Sub")
    assert punch is not None and sub808 is not None
    f_punch = extract(drums.hit("kick", **punch))
    f_sub = extract(drums.hit("kick", **sub808))
    sub_punch = f_punch.sub_20_60 + f_punch.sub_60_120
    sub_808 = f_sub.sub_20_60 + f_sub.sub_60_120
    assert sub_808 > sub_punch


def test_preset_of_unknown_returns_none():
    assert preset_of("kick", "nope") is None
    assert preset_of("nope", "Punch") is None


def test_arrangement_round_trips_drum_params(tmp_path):
    s = StudioState(tmp_path)
    s.arrangement.drum_params = {"kick": {"tune": 40.0}, "hat": {"open_hat": True}}
    s.save()
    s2 = StudioState(tmp_path)
    assert s2.arrangement.drum_params == {
        "kick": {"tune": 40.0},
        "hat": {"open_hat": True},
    }


def test_loop_render_reflects_drum_params():
    from synthlab.studio.arrange import build_song
    from synthlab.studio.state import Arrangement

    p = preset("bass_reese")
    base = Arrangement(drums=[["kick", 0]])
    tweaked = Arrangement(drums=[["kick", 0]], drum_params={"kick": {"tune": 40.0}})
    audio_base = build_song(p, base).render()
    audio_tweaked = build_song(p, tweaked).render()
    assert not np.array_equal(np.asarray(audio_base), np.asarray(audio_tweaked))


def test_render_key_incorporates_drum_params():
    p = preset("bass_reese")
    base = __import__("synthlab.studio.state", fromlist=["Arrangement"]).Arrangement(
        drums=[["kick", 0]]
    )
    tweaked = base.__class__(drums=[["kick", 0]], drum_params={"kick": {"tune": 40.0}})
    assert (
        RenderInputs(patch=p, arr=base).key()
        != RenderInputs(patch=p, arr=tweaked).key()
    )


def test_invalid_drum_param_key_does_not_crash():
    from synthlab.studio.arrange import build_song
    from synthlab.studio.state import Arrangement

    p = preset("bass_reese")
    arr = Arrangement(drums=[["kick", 0]], drum_params={"kick": {"bogus": 1.0}})
    build_song(p, arr).render()  # must not raise
