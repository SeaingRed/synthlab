import numpy as np

from synthlab import drums


def test_every_drum_ends_on_silence():
    """A truncated decay steps to zero and clicks — every hit must fade out."""
    for name in drums.KIT:
        x = np.asarray(drums.hit(name))
        assert np.abs(x[-1]).max() < 1e-3, name


def test_hot_patches_are_scaled_not_clipped():
    """_to_wav_bytes used to np.clip: a patch peaking over 1.0 got hard
    clipping baked into the WAV. It must be scaled down instead."""
    import numpy as np

    from synthlab.studio.render import _to_wav_bytes

    hot = np.linspace(0, 1.4, 4410)  # peaks well over full scale
    pcm = np.frombuffer(_to_wav_bytes(hot)[44:], dtype="<i2")
    assert int((np.abs(pcm) >= 32767).sum()) <= 2  # only the peak itself
