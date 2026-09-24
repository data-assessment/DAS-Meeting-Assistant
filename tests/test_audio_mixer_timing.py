"""Timing problems in the audio mix must never end a meeting capture or lose speech.

The mixer is driven with a simulated clock: each write is one device callback
(1024 frames at 48 kHz, resampled to 16 kHz) and `pump` plays out what is due.
Every block carries its own constant value, so the played output shows which
blocks arrived, in which order and where on the timeline.
"""
import numpy as np
import pytest

from engine.speech.mixed import DELAY, LEAD, RATE, TimelineMixer

CHUNK = 341
SCALE = 100000  # block number -> sample value, small enough to never clip


class Player:
    def __init__(self):
        self.mixer = TimelineMixer(0.0)
        self.output = bytearray()
        self.blocks = 0

    def pump(self, now):
        while data := self.mixer.take_until(int((now - DELAY) * RATE)):
            self.output.extend(data)

    def write(self, source, arrival, samples=CHUNK):
        self.blocks += 1
        self.mixer.write(source, np.full(samples, self.blocks / SCALE, np.float32), arrival)
        return self.blocks

    def steady(self, count, period, start=0.0, source="mic"):
        now = start
        for n in range(1, count + 1):
            now = start + n * period
            self.write(source, now)
            self.pump(now)
        return now

    def played(self):
        return np.frombuffer(self.output, dtype="<f4")

    def blocks_heard(self):
        values = np.round(self.played() * 2 * SCALE).astype(int)  # each source is mixed at -6 dB
        return list(dict.fromkeys(v for v in values if v))


def test_backlog_after_a_short_stall_is_played_completely_in_its_place():
    player, period = Player(), CHUNK / RATE
    now = player.steady(100, period)
    stall = 9 * period
    for _ in range(9):  # captured during the stall, delivered at once afterwards
        player.write("mic", now + stall)
    now = player.steady(50, period, now + stall)
    player.pump(now + 1)
    heard = player.blocks_heard()
    assert heard == list(range(1, player.blocks + 1))
    played = player.played()
    first, last = np.flatnonzero(played)[[0, -1]]
    assert not np.any(played[first:last + 1] == 0), "The stream has no hole where the stall was"
    assert player.mixer.dropped_samples == 0


@pytest.mark.parametrize("stall", [.6, 2.0])
def test_backlog_longer_than_the_playout_cushion_is_kept_and_caught_up(stall):
    player, period = Player(), CHUNK / RATE
    now = player.steady(100, period)
    player.pump(now + stall)  # playout continued while the callbacks were blocked
    for _ in range(int(stall / period)):
        player.write("mic", now + stall)
    now = player.steady(int(40 / period), period, now + stall)
    player.pump(now + 1)
    assert player.blocks_heard() == list(range(1, player.blocks + 1)), "No block may be lost"
    assert player.mixer.dropped_samples == 0
    assert player.mixer.next["mic"] - round(now * RATE) <= LEAD + CHUNK, "The delay is caught up"


def test_fast_device_clock_is_absorbed_without_losing_blocks():
    player = Player()
    period = CHUNK / RATE / 1.012  # the device delivers 1.2 % more audio than the clock
    now = player.steady(int(600 / period), period)
    player.pump(now + 1)
    assert player.mixer.next["mic"] - round(now * RATE) <= LEAD + CHUNK
    assert player.blocks_heard() == list(range(1, player.blocks + 1))
    assert player.mixer.dropped_samples == 0


def test_a_pause_of_one_source_is_kept_and_does_not_overlap_the_other():
    player = Player()
    player.write("loopback", .020, 320)          # remote: 0-20 ms
    player.write("mic", .200, 2880)              # local: 20-200 ms, while remote pauses
    remote = player.write("loopback", .220, 320)  # remote again: 200-220 ms
    player.pump(1)
    played = player.played()
    speech_at = lambda ms: round(played[ms * RATE // 1000] * 2 * SCALE)
    assert speech_at(10) == 1 and speech_at(100) == 2 and speech_at(210) == remote


def test_only_audio_beyond_the_buffer_capacity_is_dropped():
    player, period = Player(), CHUNK / RATE
    now = player.steady(100, period)
    for _ in range(int(4 / period)):  # a four-second stall exceeds the 3 s buffer
        player.write("mic", now + 4)
    assert RATE < player.mixer.dropped_samples < RATE * 3 // 2
    now = player.steady(20, period, now + 4)
    player.pump(now + 1)
    heard = player.blocks_heard()
    assert heard == sorted(heard) and heard[-1] == player.blocks, "The newest audio is kept, in order"
