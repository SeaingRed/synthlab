"""SFZ import (`synthlab/sfz.py`): an archive of an .sfz and its samples
-> zones. The mapping of each opcode, what is reported and not dropped,
and a hostile archive (zip bombs, traversal, #include cycles, #define
blow-ups, garbled bytes) that is one `SfzError` and nothing else."""

from __future__ import annotations

import random
import struct
import zipfile

import pytest

from synthlab import sampler, sfz
from synthlab.sfz import Limits, SfzError
from tests.sfzkit import encoded, small_instrument, wav, zip_of


class Store:
    """A `save` for `sfz.load`: the audio counted, ids made up."""

    def __init__(self):
        self.saved: list[tuple[int, int]] = []

    def __call__(self, audio, sr):
        self.saved.append((len(audio), sr))
        return f"{len(self.saved):016x}"


def load(files, lim=None, store=None, **kw):
    data = zip_of(files) if isinstance(files, dict) else files
    return sfz.load(data, store or Store(), lim, **kw)


def one(sfz_text, extra=None, **kw):
    return load({"a.sfz": sfz_text, "s.wav": wav(220), **(extra or {})}, **kw)


# --- the mapping -------------------------------------------------


def test_the_small_instrument_maps_each_region_to_a_zone():
    inst = sfz.load(small_instrument(), Store())
    z = inst.zones
    assert [(x.lo, x.hi, x.root) for x in z] == [
        (36, 47, 45),  # c2..b2, root a2 (c4 = 60)
        (48, 59, 57),
        (48, 59, 57),
        (60, 71, 69),
    ]
    assert (z[1].vlo, z[1].vhi, z[2].vlo, z[2].vhi) == (0, 80, 81, 127)
    assert z[2].gain == -3.0
    assert z[3].own_loop and 0 < z[3].loop_start < z[3].loop_end < 1
    assert z[1].sample == z[2].sample  # one file, one sample
    assert inst.samples == 3 and inst.name == "inst"
    assert inst.envelope == {"attack": 0.01, "release": 0.4}
    assert not inst.unsupported and not inst.skipped
    # the zones string a patch holds round-trips through the engine's parser
    text = sampler.format_zones(z)
    assert sampler.format_zones(sampler.parse_zones(text)) == text


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("60", 60),
        ("c4", 60),
        ("C4", 60),
        ("c#4", 61),
        ("db4", 61),
        ("bb3", 58),
        ("b3", 59),
        ("c-1", 0),
        ("g9", 127),
        ("a0", 21),
    ],
)
def test_note_names_and_numbers(text, want):
    assert sfz.key_number(text) == want


def test_both_octave_conventions_through_the_offsets():
    # a file that writes c3 for the middle C (the older convention)
    inst = one("<control> octave_offset=1\n<region> sample=s.wav key=c3")
    assert (inst.zones[0].lo, inst.zones[0].hi, inst.zones[0].root) == (60, 60, 60)
    inst = one("<control> note_offset=2\n<region> sample=s.wav key=60")
    assert inst.zones[0].root == 62


def test_key_sets_the_range_and_the_root_and_transpose_tune_and_volume():
    z = one(
        "<region> sample=s.wav key=c4 transpose=2 tune=30 volume=-6 amplitude=50"
    ).zones[0]
    assert (z.lo, z.hi, z.root) == (60, 60, 58)  # up two: the root two lower
    assert z.tune == 30.0
    assert z.gain == pytest.approx(-6 - 6.0206, abs=1e-3)


def test_a_tune_past_a_semitone_moves_the_root_instead():
    z = one("<region> sample=s.wav pitch_keycenter=60 tune=250").zones[0]
    assert (z.root, z.tune) == (58, 50.0)


def test_headers_inherit_and_a_new_header_ends_the_lower_ones():
    inst = one(
        """
<global> lovel=10
<master> hivel=100
<group> lokey=40 hikey=50
<region> sample=s.wav hikey=45
<group> lokey=60
<region> sample=s.wav
<master> lovel=20
<region> sample=s.wav
"""
    )
    got = [(z.lo, z.hi, z.vlo, z.vhi) for z in inst.zones]
    # the second group does not keep the first's hikey; the new master drops
    # its group's keys and its own lovel replaces the global's
    assert got == [(40, 45, 10, 100), (60, 127, 10, 100), (0, 127, 20, 127)]


def test_define_and_include_within_the_zip():
    files = {
        "a.sfz": '#define $DIR Samples\n#define $KEY c4\n#include "inc/r.sfz"\n',
        "inc/r.sfz": "<region> sample=$DIR/s.wav key=$KEY // a comment\n"
        "/* block\ncomment */",
        "Samples/s.wav": wav(220),
    }
    assert load(files).zones[0].lo == 60


def test_offset_end_and_a_loop_with_no_points():
    n = int(0.3 * 44100)
    z = one(f"<region> sample=s.wav offset={n // 4} end={n // 2 - 1}").zones[0]
    assert z.start == pytest.approx(0.25, abs=1e-3)
    assert z.end == pytest.approx(0.5, abs=1e-3)
    inst = one("<region> sample=s.wav loop_mode=loop_sustain")
    assert inst.zones[0].own_loop
    assert any("whole sample" in k for k in inst.notes)


def test_a_wav_smpl_loop_is_read():
    smpl = bytearray(36 + 24)
    struct.pack_into("<I", smpl, 28, 1)
    struct.pack_into("<IIIIII", smpl, 36, 0, 0, 2000, 8000, 0, 0)
    riff = wav(220) + b"smpl" + struct.pack("<I", len(smpl)) + bytes(smpl)
    riff = riff[:4] + struct.pack("<I", len(riff) - 8) + riff[8:]
    inst = one("<region> sample=s.wav loop_mode=loop_continuous", {"s.wav": riff})
    z = inst.zones[0]
    n = int(0.3 * 44100)
    assert z.loop_start == pytest.approx(2000 / n, abs=1e-4)
    assert z.loop_end == pytest.approx(8001 / n, abs=1e-4)


def test_seq_position_makes_takes():
    inst = one(
        "<group> key=c4 seq_length=3\n"
        + "\n".join(f"<region> sample=s.wav seq_position={i}" for i in (1, 2, 3))
    )
    assert [z.rr for z in inst.zones] == [0, 1, 2]


def test_amp_envelope_of_the_first_region_and_a_differing_one_is_said():
    inst = one(
        "<region> sample=s.wav ampeg_attack=0.2 ampeg_sustain=50 ampeg_decay=1\n"
        "<region> sample=s.wav ampeg_attack=0.5"
    )
    assert inst.envelope == {"attack": 0.2, "decay": 1.0, "sustain": 0.5}
    assert any("envelope differs" in k for k in inst.notes)


def test_samples_may_be_flac_ogg_mono_and_another_rate():
    files = {
        "a.sfz": "<region> sample=a.flac key=36\n<region> sample=b.ogg key=40\n"
        "<region> sample=c.wav key=44",
        "a.flac": encoded(220, "FLAC", "PCM_16"),
        "b.ogg": encoded(220, "OGG", "VORBIS"),
        "c.wav": wav(220, stereo=False, sr=22050),
    }
    store = Store()
    inst = load(files, store=store)
    assert len(inst.zones) == 3 and inst.samples == 3
    assert sorted(sr for _n, sr in store.saved) == [22050, 44100, 44100]


def test_the_sample_is_found_in_any_case_and_with_backslashes():
    inst = one(
        "<control> default_path=Samples\\\n<region> sample=S.WAV key=c4",
        {"Samples/s.wav": wav(220)},
    )
    assert len(inst.zones) == 1


def test_a_header_with_no_space_and_unknown_headers():
    inst = one(
        "<curve>curve_index=1 v000=0 v127=1\n<region>sample=s.wav key=c4\n"
        "<effect>reverb_size=1"
    )
    assert len(inst.zones) == 1
    assert inst.unsupported["<curve>"] == 1 and inst.unsupported["<effect>"] == 1


def test_a_root_of_sample_is_the_detected_pitch():
    seen = []

    def root_of(sid):
        seen.append(sid)
        return 57

    z = one("<region> sample=s.wav pitch_keycenter=sample", root_of=root_of).zones[0]
    assert z.root == 57 and len(seen) == 1


# --- what is not imported is said --------------------------------


def test_every_other_opcode_is_counted_by_the_regions_it_is_on():
    text = (
        "<control> label_cc1=x\n"
        "<global> cutoff=500 fil_type=lpf_2p\n"
        "<group> ampeg_hold=0.1\n"
        + "\n".join(f"<region> sample=s.wav key={36 + i} pan=20" for i in range(4))
        + "\n<group> ampeg_release=1\n<region> sample=s.wav key=80 pan=-5"
    )
    inst = one(text)
    u = inst.unsupported
    assert u["cutoff"] == 5 and u["fil_type"] == 5 and u["pan"] == 5
    assert u["ampeg_hold"] == 4 and u["label_cc1"] == 1
    assert "ampeg_release" not in u  # that one is imported
    msg = sfz.summary(inst)
    assert "Not imported:" in msg and "cutoff ×5" in msg and "pan ×5" in msg


def test_an_opcode_on_a_header_with_no_region_is_still_listed():
    inst = one("<group> cutoff=3\n<region> sample=s.wav\n<group> resonance=2")
    assert inst.unsupported == {"cutoff": 1, "resonance": 1}


def test_regions_left_out_are_counted_with_their_reason():
    inst = one(
        "<region> sample=s.wav key=c4\n"
        "<region> sample=s.wav key=d4 trigger=release\n"
        "<region> sample=s.wav key=e4 trigger=release_key\n"
        "<region> sample=gone.wav key=f4\n"
        "<region> sample=*sine key=g4\n"
        "<region> key=a4\n"
        "<region> sample=s.wav key=b4 end=-1"
    )
    assert len(inst.zones) == 1
    assert inst.skipped["release / first / legato triggers"] == 2
    assert inst.skipped["sample not in the zip: gone.wav"] == 1
    assert inst.skipped["generated waveforms (*sine)"] == 1
    assert inst.skipped["regions with no sample"] == 1
    assert inst.skipped["regions switched off (end=-1)"] == 1
    assert "Left out:" in sfz.summary(inst)


def test_a_number_that_is_not_one_is_noted_and_the_region_kept():
    inst = one("<region> sample=s.wav key=c4 volume=nan lovel=1e999 tune=inf hivel=abc")
    z = inst.zones[0]
    assert (z.gain, z.vlo, z.vhi, z.tune) == (0.0, 0, 127, 0.0)
    assert set(inst.notes) == {
        "volume is not a number",
        "lovel is not a number",
        "tune is not a number",
        "hivel is not a number",
    }


def test_values_past_the_zone_ranges_are_clamped_and_said():
    z = one("<region> sample=s.wav lokey=-5 hikey=300 volume=40 seq_position=40").zones[
        0
    ]
    assert (z.lo, z.hi, z.gain, z.rr) == (0, 127, 12.0, sampler.MAX_TAKES - 1)


def test_more_regions_than_a_sound_holds_keeps_the_first_and_says_so():
    text = "\n".join(
        f"<region> sample=s.wav key={i % 100 + 20} seq_position={i // 100 + 1}"
        for i in range(sampler.MAX_ZONES + 7)
    )
    inst = one(text)
    assert len(inst.zones) == sampler.MAX_ZONES and inst.truncated == 7
    msg = sfz.summary(inst)
    assert f"{sampler.MAX_ZONES + 7} regions" in msg and "7 were not" in msg


def test_a_zip_with_several_sfz_reads_the_shallowest_and_says_so():
    files = {
        "deep/x/b.sfz": "<region> sample=../../s.wav key=c4",
        "a.sfz": "<region> sample=s.wav key=d4",
        "s.wav": wav(220),
    }
    inst = load(files)
    assert inst.sfz_path == "a.sfz" and inst.zones[0].lo == 62
    assert "2 .sfz files" in sfz.summary(inst)


def test_nothing_importable_is_refused_in_words():
    with pytest.raises(SfzError, match="No region"):
        one("<region> sample=gone.wav key=c4")
    with pytest.raises(SfzError, match="no .sfz"):
        load({"s.wav": wav(220)})


# --- hostile input -----------------------------------------------


def test_a_bare_sfz_or_garbage_is_not_a_zip():
    for blob in (b"<region> sample=x.wav", b"", b"PK\x03\x04" + b"\0" * 40):
        with pytest.raises(SfzError):
            sfz.load(blob, Store())


@pytest.mark.parametrize(
    "name",
    [
        "../evil.wav",
        "a/../../evil.wav",
        "/abs.wav",
        "C:/x.wav",
        "C:evil.wav",
        "..\\evil.wav",
        "a//b.wav",
        "a/\x01b.wav",
        "a/./b.wav",
    ],
)
def test_a_member_name_that_leaves_the_archive_refuses_it(name):
    files = {"a.sfz": "<region> sample=s.wav", "s.wav": wav(220), name: b"x"}
    with pytest.raises(SfzError, match="will not read"):
        load(files)


def test_a_sample_path_cannot_leave_the_zip_either():
    for ref in ("../../etc/x.wav", "/etc/x.wav", "C:/x.wav", "..\\x.wav"):
        with pytest.raises(SfzError, match="No region"):
            one(f"<region> sample={ref}")
    inst = one("<region> sample=../x.wav key=c4\n<region> sample=s.wav")
    assert inst.skipped == {"a sample path outside the zip": 1}


def test_too_many_members_is_refused_before_the_zip_is_built():
    files = {f"f{i}.txt": b"x" for i in range(11)}
    with pytest.raises(SfzError, match="more than 10 files"):
        load(files, Limits(members=10))


def test_a_member_over_the_size_cap():
    with pytest.raises(SfzError, match="too large"):
        one("<region> sample=s.wav", lim=Limits(member_bytes=1000))


def test_a_total_over_the_uncompressed_cap():
    with pytest.raises(SfzError, match="unpacks to more than"):
        one("<region> sample=s.wav", lim=Limits(uncompressed=20_000))


def test_a_zip_bomb_by_compression_ratio():
    bomb = zip_of({"a.sfz": "<region> sample=b.wav", "b.wav": bytes(3 << 20)})
    assert len(bomb) < 20_000
    with pytest.raises(SfzError, match="compressed suspiciously well"):
        sfz.load(bomb, Store())


def test_a_member_that_lies_about_its_size():
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("a.sfz", "<region> sample=s.wav\n" + "// pad\n" * 50)
        z.writestr("s.wav", wav(220))
        z.infolist()[0].file_size = 10  # the central directory says ten
    with pytest.raises(SfzError):
        sfz.load(buf.getvalue(), Store())


def test_an_encrypted_member_is_refused():
    data = bytearray(zip_of({"a.sfz": "<region> sample=s.wav", "s.wav": wav(220)}))
    # the general-purpose flag bit 0, in the first local header and the directory
    for sig, at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        data[i + at] |= 1
    with pytest.raises(SfzError):
        sfz.load(bytes(data), Store())


def test_a_duplicate_name_in_another_case_is_refused():
    with pytest.raises(SfzError, match="twice"):
        load({"a.sfz": "<region>", "A.SFZ": "<region>"})


def test_the_text_cap_lines_and_headers():
    with pytest.raises(SfzError, match="too long"):
        one("<region> sample=s.wav", lim=Limits(text_bytes=10))
    with pytest.raises(SfzError, match="lines"):
        one("<region> sample=s.wav\n" * 50, lim=Limits(lines=10))
    with pytest.raises(SfzError, match="headers"):
        one("<group>\n" * 50, lim=Limits(headers=10))
    with pytest.raises(SfzError, match="regions"):
        one("<region> sample=s.wav\n" * 50, lim=Limits(regions=10))


def test_include_depth_count_cycles_and_escapes():
    chain = {f"i{n}.sfz": f'#include "i{n + 1}.sfz"' for n in range(20)}
    with pytest.raises(SfzError, match="deeper than 3"):
        load({"a.sfz": '#include "i0.sfz"', **chain}, Limits(include_depth=3))
    with pytest.raises(SfzError, match="loops back"):
        load({"a.sfz": '#include "b.sfz"', "b.sfz": '#include "a.sfz"'})
    with pytest.raises(SfzError, match="loops back"):
        load({"a.sfz": '#include "A.sfz"'})
    flat = '#include "b.sfz"\n' * 20
    with pytest.raises(SfzError, match="more than 5 files"):
        load({"a.sfz": flat, "b.sfz": "// x"}, Limits(includes=5))
    for target in ("../x.sfz", "/x.sfz", "C:/x.sfz"):
        with pytest.raises(SfzError, match="outside the zip"):
            load({"a.sfz": f'#include "{target}"'})
    with pytest.raises(SfzError, match="no file"):
        load({"a.sfz": '#include "gone.sfz"'})


def test_define_count_and_expansion_are_bounded():
    many = "\n".join(f"#define $V{i} x" for i in range(30))
    with pytest.raises(SfzError, match="more than 10 #defines"):
        one(many, lim=Limits(defines=10))
    # a doubling chain: each define is twice the last
    names = ["A"] + [f"B{i}" for i in range(40)]
    grow = "#define $A xxxxxxxx\n" + "".join(
        f"#define $B{i} ${names[i]}${names[i]}\n" for i in range(40)
    )
    with pytest.raises(SfzError, match="expands past"):
        one(grow)
    # a short define used many times on one line
    line = "#define $A " + "y" * 4000 + "\n" + "$A" * 100
    with pytest.raises(SfzError, match="expands past"):
        one(line)
    # and many long lines together
    text = "#define $A " + "y" * 1000 + "\n" + ("$A" * 10 + "\n") * 400
    with pytest.raises(SfzError, match="too large"):
        one(text, lim=Limits(expanded=100_000))


def test_the_decoded_audio_is_bounded_and_a_bad_sample_is_left_out_not_fatal():
    with pytest.raises(SfzError, match="of audio"):
        one(
            "<region> sample=s.wav key=1\n<region> sample=t.wav key=2",
            {"t.wav": wav(330)},
            lim=Limits(audio_seconds=0.4),
        )
    inst = one(
        "<region> sample=s.wav key=1\n<region> sample=junk.wav key=2",
        {"junk.wav": b"RIFF not really a wav"},
    )
    assert len(inst.zones) == 1
    assert any("junk.wav" in k and "audio" in k for k in inst.skipped)
    with pytest.raises(SfzError, match="No region"):  # a rate past the cap
        one("<region> sample=s.wav", lim=Limits(max_sr=8000))


def test_a_long_sample_is_cut_at_the_samplers_cap_and_noted(monkeypatch):
    monkeypatch.setattr(sampler, "MAX_SAMPLE_SECONDS", 0.1)
    inst = one("<region> sample=s.wav offset=100000")
    assert any("cut to" in k for k in inst.notes)
    assert inst.zones[0].start is None  # the offset is past the kept audio


# --- garbled input: one error type -------------------------------


def _fuzz(blob: bytes) -> None:
    try:
        sfz.load(blob, Store())
    except SfzError:
        pass


def test_truncated_and_garbled_archives_raise_only_sfzerror():
    good = small_instrument()
    for cut in range(0, len(good), max(1, len(good) // 400)):
        _fuzz(good[:cut])
    rng = random.Random(7)
    for _ in range(300):
        b = bytearray(good)
        for _ in range(rng.randint(1, 12)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        _fuzz(bytes(b))


def test_garbled_sfz_text_raises_only_sfzerror_or_imports():
    rng = random.Random(11)
    base = (
        "#define $K c4\n<control> default_path=S/\n<group> lovel=1 // x\n"
        "<region> sample=s.wav key=$K tune=5\n/* c */<region> sample=s.wav\n"
    )
    alphabet = list('<>=$#"/*\\\n \x00\xff') + list("abc012.-e")
    for _ in range(400):
        chars = list(base)
        for _ in range(rng.randint(1, 15)):
            chars[rng.randrange(len(chars))] = rng.choice(alphabet)
        text = "".join(chars)[: rng.randint(0, len(base))]
        try:
            load({"a.sfz": text, "S/s.wav": wav(220), "s.wav": wav(220)})
        except SfzError:
            pass


def test_an_unexpected_failure_inside_is_still_an_sfzerror(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("anything")

    monkeypatch.setattr(sfz, "parse", boom)
    with pytest.raises(SfzError):
        one("<region> sample=s.wav")
