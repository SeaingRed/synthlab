"""The Synths page's UX-review contracts (Sep 2026): the signal-chain summary,
the identity line, full-word labels, and both riding OOB after every edit."""

from dataclasses import replace

from synthlab.studio.chain import chain_summary, motion_lines
from synthlab.studio.paramui import PARAM_GROUPS
from synthlab.synth import Patch
from tests.studio.test_routes import make_client


def test_chain_follows_engine_order_and_reads_the_patch():
    p = replace(Patch(), osc1="saw", osc2=None, cutoff=3650.0, formant_amount=0.0)
    stages = chain_summary(p, [{"kind": "reverb"}], [])
    assert [s["title"] for s in stages] == [
        "Source",
        "Filter",
        "Vowel",
        "Amp",
        "Character",
        "Layer FX",
        "Master",
    ]
    text = {s["title"]: s for s in stages}
    assert text["Source"]["text"] == "saw · osc 2 off"
    assert "3.65 kHz" in text["Filter"]["text"]
    assert text["Vowel"]["off"] and text["Master"]["off"]
    assert not text["Layer FX"]["off"]


def test_motion_lines_name_only_what_is_moving():
    still = replace(
        Patch(),
        vibrato_depth=0.0,
        tremolo_depth=0.0,
        pwm_depth=0.0,
        filter_lfo_depth=0.0,
        pitch_env=0.0,
        **{f"mod{i}_src": "none" for i in range(1, 5)},
    )
    assert motion_lines(still) == []
    p = replace(still, mod1_src="lfo", mod1_dst="cutoff", mod1_amt=0.4)
    assert "LFO 1 → Filter cutoff +40%" in motion_lines(p)
    # a route with no amount is not moving anything
    assert not any(
        "Route" in m or "Filter cutoff" in m
        for m in motion_lines(replace(p, mod1_amt=0.0))
    )


def test_labels_are_unique_full_words():
    labels = [s.label for _, specs in PARAM_GROUPS for s in specs]
    assert len(labels) == len(set(labels)), "XY/macro selects list labels bare"
    for bad in ("F.Atk", "Trem Rte", "P.Env", "U.Detune", "KeyTrk"):
        assert bad not in labels
    assert [g for g, _ in PARAM_GROUPS] == [
        "Source",
        "Filter",
        "Vowel",
        "Amp envelope",
        "Character",
        "Motion",
        "Modulators",  # envelopes 3 / 4, LFOs 2 / 3
    ]


def test_an_edit_refreshes_chain_and_identity_out_of_band(tmp_path):
    with make_client(tmp_path) as client:
        page = client.get("/").text
        assert 'id="signal-chain"' in page and 'id="sound-id"' in page
        r = client.post(
            "/fragment/patch",
            data={"cutoff": "500"},
            headers={"HX-Current-URL": "http://testserver/view/editor"},
        )
        assert 'id="signal-chain"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
        assert 'id="sound-id"' in r.text
        assert "500 Hz" in r.text.split('id="signal-chain"')[1]


def test_mod_targets_read_as_words_not_field_names(tmp_path):
    html = make_client(tmp_path).get("/").text
    route = html.split('id="row-mod4_dst"')[1].split("</select>")[0]
    assert ">Filter LFO depth<" in route and ">filter_lfo_depth<" not in route


def test_monitor_controls_say_they_are_not_saved(tmp_path):
    html = make_client(tmp_path).get("/").text
    mon = html.split('class="monitor"')[1].split("</fieldset>")[0]
    assert "not saved with the sound" in mon
    assert 'id="space"' in mon and 'id="volume"' in mon


def test_presets_saved_under_old_panel_names_still_load(tmp_path):
    import json

    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    old = tmp_path / "presets" / "panels" / "Oscillators"
    old.mkdir(parents=True)
    (old / "grit.json").write_text(json.dumps({"osc1": "square", "sub_level": 0.5}))
    filt = tmp_path / "presets" / "panels" / "Filter"
    filt.mkdir(parents=True)
    (filt / "wob.json").write_text(
        json.dumps({"cutoff": 900.0, "filter_lfo_depth": 800.0})
    )
    assert "grit" in s.list_panel_presets("Source")
    s.load_panel_preset("Source", "grit")
    assert s.patch.osc1 == "square" and s.patch.sub_level == 0.5
    # the filter LFO moved to Motion; an old Filter preset still brings it
    s.load_panel_preset("Filter", "wob")
    assert s.patch.cutoff == 900.0 and s.patch.filter_lfo_depth == 800.0
