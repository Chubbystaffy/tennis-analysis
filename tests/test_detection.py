"""Synthetic-signal tests for swing detection, max-turn search and ratings.

Run with:  python -m pytest -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyse  # noqa: E402

FPS = 240.0


def bump(t, centre, width, height):
    return height * np.exp(-0.5 * ((t - centre) / width) ** 2)


def make_df(events, duration=8.0):
    """events: list of (kind, turn_time, contact_time). kind: forehand/backhand/serve."""
    t = np.arange(0, duration, 1 / FPS)
    turn = np.zeros_like(t)
    speed = np.full_like(t, 0.5)
    wrist_y = np.full_like(t, 500.0)
    for kind, t_turn, t_contact in events:
        sign = -1 if kind == "backhand" else 1
        turn += sign * bump(t, t_turn, 0.15, 80)
        speed += bump(t, t_contact, 0.03, 12)
        if kind == "serve":
            wrist_y -= bump(t, t_contact, 0.05, 300)   # wrist far above the head
    df = pd.DataFrame(index=pd.RangeIndex(len(t), name="frame"))
    df["time_s"] = t
    df["shoulder_turn_deg"] = turn
    df["turn_ratio"] = turn / 90
    df["hip_turn_deg"] = turn * 0.6
    df["wrist_speed"] = speed
    df["wrist_y_px"] = wrist_y
    df["nose_y_px"] = 400.0
    df["torso_px"] = 200.0
    return df


def test_prep_times_and_ratings():
    df = make_df([("forehand", 1.6, 2.05),    # 0.45 s -> amber
                  ("forehand", 3.0, 3.6),     # 0.60 s -> green
                  ("forehand", 5.0, 5.2)])    # 0.20 s -> red
    swings, _ = analyse.detect_swings(df, FPS, "wrist_speed")
    assert [s.stroke for s in swings] == ["forehand"] * 3
    preps = [round(s.prep_time_s, 2) for s in swings]
    assert preps == [0.45, 0.60, 0.20]
    assert [s.rating for s in swings] == ["amber", "green", "red"]
    assert all(abs(s.max_turn_deg - 80) < 1 for s in swings)


def test_rating_boundaries():
    assert analyse.rate(0.5) == "green"
    assert analyse.rate(0.4999) == "amber"
    assert analyse.rate(0.3) == "amber"
    assert analyse.rate(0.2999) == "red"


def test_stroke_classification_and_unrated_strokes():
    df = make_df([("backhand", 1.5, 2.0), ("serve", 3.2, 4.0), ("forehand", 5.5, 6.0)])
    swings, _ = analyse.detect_swings(df, FPS, "wrist_speed")
    assert [s.stroke for s in swings] == ["backhand", "serve/overhead", "forehand"]
    assert [s.rating for s in swings] == ["", "", "green"]   # only forehands are rated


def test_min_gap_merges_close_peaks():
    df = make_df([("forehand", 1.0, 1.5)])
    t = df["time_s"].to_numpy()
    df["wrist_speed"] += analyse.np.exp(-0.5 * ((t - 1.9) / 0.03) ** 2) * 10  # 0.4 s later
    swings, _ = analyse.detect_swings(df, FPS, "wrist_speed")
    assert len(swings) == 1


def test_window_truncated_flag():
    df = make_df([("forehand", 0.3, 0.6)])
    swings, _ = analyse.detect_swings(df, FPS, "wrist_speed")
    assert swings and "clip starts" in swings[0].flags[0]


def test_slow_movement_is_not_a_swing():
    df = make_df([])
    df["wrist_speed"] += bump(df["time_s"].to_numpy(), 2.0, 0.2, 1.5)  # 2 m/s arm wave
    swings, _ = analyse.detect_swings(df, FPS, "wrist_speed")
    assert swings == []


def test_coil_past_side_on_uses_the_dip():
    # true turn rises to 110 deg at t=1.4 then uncoils; contact at 1.8 s.
    t = np.arange(0, 3, 1 / FPS)
    true_turn = 110 * np.exp(-0.5 * ((t - 1.4) / 0.2) ** 2)
    seen = np.degrees(np.arcsin(np.sin(np.radians(true_turn))))  # side-on foreshortening
    df = make_df([])
    df = df.iloc[:len(t)].copy()
    df["shoulder_turn_deg"] = seen
    df["turn_ratio"] = np.sin(np.radians(true_turn))
    df["hip_turn_deg"] = seen * 0.6
    df["wrist_speed"] = 0.5 + bump(t, 1.8, 0.03, 12)
    swings, _ = analyse.detect_swings(df, FPS, "wrist_speed")
    s = swings[0]
    assert s.stroke == "forehand"
    assert abs(s.max_turn_time_s - 1.4) < 0.02
    assert abs(s.max_turn_deg - 110) < 2
    assert abs(s.prep_time_s - 0.4) < 0.02
