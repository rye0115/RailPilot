import time

from railpilot.core.clock import Clock, ClockState, calibrate


def test_calibrate_uses_min_rtt_sample():
    base = time.time()
    samples = [
        (base + 0.10, 0.80),
        (base + 0.50, 0.02),
        (base + 0.30, 0.20),
    ]

    def fake_fetch(_url, _timeout):
        return samples.pop(0)

    state = calibrate(samples=3, fetcher=fake_fetch)
    # best sample: server = base + .50, rtt = .02 -> offset ~ +510ms
    assert 400 <= state.offset_ms <= 650
    assert state.min_rtt_ms == 20.0
    assert state.samples == 3


def test_clock_uses_monotonic_and_offset():
    state = ClockState(offset_ms=1000, measured_at="", samples=1, min_rtt_ms=10.0)
    clock = Clock(state, base_mono=0.0, base_wall=1000.0, monotonic=lambda: 0.5, wall=lambda: 0.0)
    # 1000s base + 1s offset + 0.5s elapsed = 1001500ms
    assert clock.now_ms() == 1_001_500
    assert clock.remaining_ms(1_002_000) == 500
