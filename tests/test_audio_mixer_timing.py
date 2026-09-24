"""Timing problems in the audio mix must never end a meeting capture (field report).

The mixer is driven with a simulated clock: each write is one device callback
(1024 frames at 48 kHz, resampled to 16 kHz) and `pump` plays out what is due.
"""
import numpy as np
import pytest

from engine.speech.mixed import DELAY, LEAD, RATE, TimelineMixer

CHUNK = 341


def pump(mixer, now):
    while mixer.take_until(int((now - DELAY) * RATE)):
        pass


def steady(mixer, first, count, period, start_time=0.0):
    now = start_time
    for n in range(first, first + count):
        now = start_time + n * period
        mixer.write("mic", np.full(CHUNK, .1, np.float32), now)
        pump(mixer, now)
    return now


def test_fast_device_clock_is_trimmed_instead_of_overflowing_the_mix():
    mixer = TimelineMixer(0.0)
    period = CHUNK / RATE / 1.012  # the device delivers 1.2 % more audio than the clock
    count = int(600 / period)
    now = steady(mixer, 1, count, period)
    assert mixer.next["mic"] - round(now * RATE) <= LEAD + CHUNK
    assert .008 < mixer.dropped_samples / (count * CHUNK) < .016


@pytest.mark.parametrize("stall", [.8, 2.0])
def test_a_stall_of_the_whole_process_keeps_the_backlog_in_place(stall):
    mixer = TimelineMixer(0.0)
    period = CHUNK / RATE
    now = steady(mixer, 1, 200, period)
    start = mixer.next["mic"]
    backlog = int(stall / period)
    for _ in range(backlog):  # delivered at once after the stall, before playout resumes
        mixer.write("mic", np.full(CHUNK, .1, np.float32), now + stall)
    pump(mixer, now + stall)
    assert mixer.next["mic"] == start + backlog * CHUNK
    assert mixer.dropped_samples == 0 and mixer.late_blocks == 0


def test_callbacks_delayed_past_playout_do_not_end_the_capture():
    mixer = TimelineMixer(0.0)
    period = CHUNK / RATE
    now = steady(mixer, 1, 200, period)
    arrival = now + .6
    pump(mixer, arrival)  # playout continued while the device callbacks were blocked
    backlog = int(.6 / period)
    for _ in range(backlog):
        mixer.write("mic", np.full(CHUNK, .1, np.float32), arrival)
    now = steady(mixer, 1, 200, period, arrival)
    assert mixer.next["mic"] - round(now * RATE) <= LEAD + CHUNK
    assert mixer.dropped_samples <= backlog * CHUNK
