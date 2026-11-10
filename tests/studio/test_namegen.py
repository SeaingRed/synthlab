from synthlab.analysis.features import Features
from synthlab.studio.namegen import sound_name


def _features(**overrides) -> Features:
    base = dict(
        centroid=1000.0,
        rolloff=2000.0,
        flatness=0.1,
        crest=10.0,
        sub_20_60=0.1,
        sub_60_120=0.1,
        low_120_250=0.1,
        attack_time=0.01,
        stereo_width=0.0,
    )
    base.update(overrides)
    return Features(**base)


def test_same_inputs_give_same_name_twice():
    f = _features()
    a = sound_name(f, "genome-key-1")
    b = sound_name(f, "genome-key-1")
    assert a == b


def test_dark_deep_features_draw_from_dark_and_deep_banks():
    f = _features(centroid=200.0, sub_20_60=0.25, sub_60_120=0.2)
    name = sound_name(f, "some-genome-key")
    from synthlab.studio.namegen import ADJECTIVES, NOUNS

    adj, noun = name.split(" ")
    assert adj in ADJECTIVES["dark"]
    assert noun in NOUNS["deep"]


def test_bright_textured_features_draw_from_bright_and_textured_banks():
    f = _features(centroid=3000.0, flatness=0.5, sub_20_60=0.0, sub_60_120=0.0)
    name = sound_name(f, "another-key")
    from synthlab.studio.namegen import ADJECTIVES, NOUNS

    adj, noun = name.split(" ")
    assert adj in ADJECTIVES["bright"]
    assert noun in NOUNS["textured"]


def test_mid_clean_features_draw_from_mid_and_clean_banks():
    f = _features(centroid=1000.0, flatness=0.1, sub_20_60=0.0, sub_60_120=0.0)
    name = sound_name(f, "yet-another-key")
    from synthlab.studio.namegen import ADJECTIVES, NOUNS

    adj, noun = name.split(" ")
    assert adj in ADJECTIVES["mid"]
    assert noun in NOUNS["clean"]


def test_different_genome_keys_usually_produce_different_names():
    f = _features()
    names = {sound_name(f, f"key-{i}") for i in range(10)}
    assert len(names) >= 6
