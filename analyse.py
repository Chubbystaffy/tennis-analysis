#!/usr/bin/env python3
"""
Forehand preparation-timing analysis built on Sports2D.

    python analyse.py clips/

For every video in the folder it:
  1. runs Sports2D (pose tracking + joint/segment angles),
  2. derives per-frame shoulder turn and smoothed wrist speed,
  3. optionally tracks fluorescent tape on the racquet tip (OpenCV HSV),
  4. finds each swing (contact = wrist / racquet speed peak) and the moment of
     maximum unit turn in the 1.5 s before it,
  5. writes an annotated video, a chart, CSVs and text summaries to
     results/<clip name>/, plus a combined summary in results/.

All tunable numbers live in the CONFIG section directly below.
"""

# =============================================================================
# CONFIG - everything you may want to tune is here
# =============================================================================

# --- Player / footage --------------------------------------------------------
PLAYER_HAND = "right"          # "right" or "left": which wrist swings the racquet
PLAYER_HEIGHT_M = 1.75         # used to convert pixels to metres
TORSO_TO_HEIGHT = 0.29         # neck-to-mid-hip length / body height (fallback scaling)
SLOWMO_FACTOR = 1              # 1 if the file's frame rate is the real capture rate
                               # (iPhone slo-mo AirDropped "as original" plays back
                               # as a 120/240 fps file -> 1). If the clip was exported
                               # as a slowed-down 30 fps video, use 240/30 = 8.
NET_DIRECTION = "auto"         # "auto", "left" or "right": where the net is in the
                               # image. "auto" uses the way the player's nose points.
VIDEO_EXTENSIONS = (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm")

# --- Sports2D settings (see `sports2d --help`) ---------------------------------
SPORTS2D_MODE = "balanced"              # "lightweight", "balanced", "performance"
SPORTS2D_PERSON_ORDERING = "largest_size"  # who to analyse when several people are
                               # seen (with the keypoint threshold below, partly hidden spectators
                               # are excluded). Alternatives: "greatest_displacement",
                               # "highest_likelihood", "on_click" (opens a window to choose)
SPORTS2D_DET_FREQUENCY = 4             # person detection every N frames (pose every frame)
SPORTS2D_FILTER_CUTOFF_HZ = 10         # Butterworth cut-off on keypoints (Sports2D default 6
                                       # smooths away too much of a fast swing)
SPORTS2D_KEYPOINT_NUMBER_THRESHOLD = 0.65  # fraction of keypoints a person needs to be
                               # tracked: 0.65 needs legs + feet, which rejects spectators /
                               # people cut off by the frame edge (Sports2D default 0.3)
SPORTS2D_FLOOR_ANGLE = 0               # camera assumed level (tripod); "auto" = from footwork
SPORTS2D_ANGLE_TEXT = "none"   # angle values drawn on Sports2D's video: "none", "body",
                               # "list" or "body list" (the list can hide a player who is
                               # near the left edge)
SPORTS2D_EXTRA_ARGS = []               # any other CLI flags, e.g. ["--input_size", "1920", "1080"]

# --- Signal processing -------------------------------------------------------
TURN_SOURCE = "keypoints"      # "keypoints": shoulder turn from the foreshortening of the
                               #   shoulder line in Sports2D's 2D keypoints (works side-on).
                               # "sports2d_shoulders": Sports2D's "shoulders" segment angle
                               #   (shoulder-line tilt in the image; only meaningful when the
                               #   camera looks down the player's vertical axis - rarely).
TURN_SMOOTH_S = 0.04           # Gaussian smoothing (sigma, seconds) of the turn signal
SHOULDER_WIDTH_PERCENTILE = 99 # |shoulder width| percentile that counts as "fully side-on"
MIN_SHOULDER_WIDTH_RATIO = 0.5 # floor for shoulder width / torso length (stops noise being
                               # scaled up to +/-90 deg when the player never turns)
MIN_HIP_WIDTH_RATIO = 0.3      # same for the hips
WRIST_SPEED_RELATIVE_TO_HIP = True  # measure the wrist relative to the hip centre, so
                               # running / camera panning do not look like a swing
WRIST_SPEED_SMOOTH_S = 0.03    # Gaussian smoothing (sigma, seconds) of wrist speed
MAX_HIP_SPEED_TORSO_PER_S = 15 # faster body jumps = tracking glitch, frames are ignored

# --- Swing detection ---------------------------------------------------------
MIN_SWING_GAP_S = 0.8          # contacts closer together than this are one swing
MIN_CONTACT_SPEED_MPS = 3.0    # absolute floor for a contact speed peak (m/s)
MIN_PEAK_FRACTION = 0.35       # ...and at least this fraction of the clip's typical fast
                               # swing (median of its 3 fastest peaks)
MIN_PEAK_PROMINENCE_FRACTION = 0.25  # peak must stand out by this fraction of that speed
TURN_SEARCH_WINDOW_S = 1.5     # max unit turn = peak turn in this window before contact
CONTACT_SIGNAL = "auto"        # "auto" (racquet tip if well tracked, else wrist),
                               # "wrist" or "racquet"
OVERHEAD_WRIST_ABOVE_NOSE = 0.15  # wrist this far (x torso length) above the nose at
                               # contact = serve / overhead, not rated
MIN_TURN_DEG = 20              # swings with less turn than this are flagged and not rated
PLATEAU_DEG = 75               # turn above this counts as "side-on" (see find_max_coil)
DIP_MIN_DEG = 8                # a dip this deep between two side-on peaks = coil past 90 deg
MAX_COIL_DEG = 130             # ...unless that would mean an impossible coil beyond this
RATE_STROKES = ("forehand",)   # stroke types that get a green/amber/red rating

# --- Ratings (preparation time = contact - max unit turn) ---------------------
GREEN_MIN_S = 0.5              # >= 0.5 s  -> green
AMBER_MIN_S = 0.3              # 0.3-0.5 s -> amber, below -> red

# --- Optional racquet-tip tracking (fluorescent tape) ------------------------
RACQUET_TRACKING = False       # or pass --racquet on the command line
TAPE_COLOUR = "pink"           # key into TAPE_HSV_RANGES (tune with --tune-colour)
# OpenCV HSV: H 0-179, S 0-255, V 0-255. Each colour is a list of (lower, upper)
# ranges, so hues that wrap around red can use two ranges.
TAPE_HSV_RANGES = {
    "pink":   [((150, 90, 120), (179, 255, 255)), ((0, 90, 120), (4, 255, 255))],
    "orange": [((5, 140, 150), (20, 255, 255))],
    "yellow": [((22, 100, 150), (35, 255, 255))],   # beware: tennis balls are this colour
    "green":  [((36, 90, 120), (75, 255, 255))],
}
TAPE_MIN_AREA_PX = 12          # ignore blobs smaller than this (noise)
TAPE_MAX_AREA_PX = 5000        # ...or bigger than this (clothing, signage)
TAPE_SEARCH_RADIUS_TORSO = 2.5 # only look within this many torso lengths of the wrist
TAPE_MIN_DIST_FROM_WRIST_TORSO = 0.5  # ...but not right at the hand (wristbands etc.)
TAPE_BODY_EXCLUSION_TORSO = 0.35  # ignore blobs this close to hips/knees/ankles/other hand
TAPE_STATIC_FRACTION = 0.4     # tape-coloured in >40% of sampled frames = background (needs a
                               # fixed camera)
TAPE_MAX_GAP_S = 0.05          # interpolate tape dropouts shorter than this
RACQUET_MIN_COVERAGE = 0.6     # "auto" uses racquet speed if tape found in >= 60% of frames

# --- Annotated video ---------------------------------------------------------
ANNOTATED_VIDEO_FPS = None     # None: source fps, but 30 for >=100 fps slow-mo sources
FREEZE_ON_MARKERS_S = 0.6      # pause (playback seconds) on each max-turn / contact frame
MARKER_LABEL_HOLD_S = 0.5      # keep the banner on screen this long (playback seconds)
WRITE_ANNOTATED_VIDEO = True

# --- Demo fallback (used when ./clips is empty) ---------------------------------
# The Sports2D demo is someone walking, not hitting: relax thresholds so the
# pipeline still finds "swings" (arm swings) to exercise every step.
DEMO_OVERRIDES = dict(MIN_CONTACT_SPEED_MPS=0.3, MIN_PEAK_FRACTION=0.3,
                      MIN_PEAK_PROMINENCE_FRACTION=0.1, RATE_STROKES=("forehand", "backhand"))

# =============================================================================
# END OF CONFIG
# =============================================================================

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

log = logging.getLogger("analyse")

RATING_HEX = {"green": "#0ca30c", "amber": "#fab219", "red": "#d03b3b"}
RATING_BGR = {k: tuple(int(v[i:i + 2], 16) for i in (5, 3, 1)) for k, v in RATING_HEX.items()}
TURN_BGR = (52, 104, 235)      # orange (#eb6834) for max-turn markers
NEUTRAL_BGR = (200, 200, 200)


def cfg(name):
    """Read a config value (lets the demo mode override module-level settings)."""
    return globals()[name]


# -----------------------------------------------------------------------------
# Sports2D
# -----------------------------------------------------------------------------
def sports2d_outputs(out_dir: Path, stem: str):
    d = out_dir / f"{stem}_Sports2D"
    pick = lambda pat: (sorted(d.glob(pat)) or [None])[0]
    return dict(dir=d,
                video=d / f"{stem}_Sports2D.mp4",
                trc_px=pick(f"{stem}_Sports2D_px_person*.trc"),
                trc_m=pick(f"{stem}_Sports2D_m_person*.trc"),
                mot=pick(f"{stem}_Sports2D_angles_person*.mot"))


def run_sports2d(clip: Path, out_dir: Path, rerun=False):
    out = sports2d_outputs(out_dir, clip.stem)
    settings = ["--nb_persons_to_detect", "1",
            "--person_ordering_method", SPORTS2D_PERSON_ORDERING,
            "--first_person_height", str(PLAYER_HEIGHT_M),
            "--slowmo_factor", str(SLOWMO_FACTOR),
            "--mode", SPORTS2D_MODE,
            "--det_frequency", str(SPORTS2D_DET_FREQUENCY),
            "--cut_off_frequency", str(SPORTS2D_FILTER_CUTOFF_HZ),
            "--floor_angle", str(SPORTS2D_FLOOR_ANGLE),
            "--keypoint_number_threshold", str(SPORTS2D_KEYPOINT_NUMBER_THRESHOLD),
            "--visible_side", "none",
            "--display_angle_values_on", *SPORTS2D_ANGLE_TEXT.split(),
            "--show_realtime_results", "false", "--show_graphs", "false",
            "--save_graphs", "false", "--save_img", "false",
            "--do_ik", "false", "--make_c3d", "false", "--save_calib", "false",
            *map(str, SPORTS2D_EXTRA_ARGS)]
    stamp = out_dir / "sports2d_settings.json"
    fresh = out["trc_px"] and out["mot"] and out["video"].exists() and \
        out["trc_px"].stat().st_mtime > clip.stat().st_mtime and \
        stamp.exists() and json.loads(stamp.read_text()) == settings
    if fresh and not rerun:
        log.info("  Sports2D results already exist - reusing them (use --rerun to redo).")
        return out
    if out["dir"].exists():
        shutil.rmtree(out["dir"])        # stale results from other settings
    exe = shutil.which("sports2d")
    cmd = [exe] if exe else [sys.executable, "-m", "Sports2D.Sports2D"]
    cmd += ["--video_input", str(clip.resolve()), "--result_dir", str(out_dir.resolve()), *settings]
    log.info("  Running Sports2D (this is the slow part)...")
    t0 = time.time()
    logf = out_dir / "sports2d_log.txt"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(logf, "w") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    out = sports2d_outputs(out_dir, clip.stem)
    if (rc != 0 or not out["trc_px"]) and "compute_height" in logf.read_text(errors="ignore"):
        # Sports2D could not measure the player's height (legs hidden) for its pixel->metre
        # conversion. Retry without it; analyse.py then scales by torso length instead.
        log.info("  Sports2D could not convert to metres - retrying without (slow again).")
        with open(logf, "a") as fh:
            rc = subprocess.run(cmd + ["--to_meters", "false"], stdout=fh,
                                stderr=subprocess.STDOUT).returncode
        out = sports2d_outputs(out_dir, clip.stem)
    if rc == 0 and not out["trc_px"]:
        raise RuntimeError("Sports2D found nobody fully visible for long enough (feet hidden? "
                           "out of frame?). Lower SPORTS2D_KEYPOINT_NUMBER_THRESHOLD if the "
                           f"player is partly hidden. Log: {logf}")
    if rc != 0 or not out["trc_px"]:
        raise RuntimeError(f"Sports2D failed (exit code {rc}); see {logf}")
    stamp.write_text(json.dumps(settings))
    log.info(f"  Sports2D done in {time.time() - t0:.0f} s.")
    return out


# -----------------------------------------------------------------------------
# Loading Sports2D files
# -----------------------------------------------------------------------------
def read_trc(path: Path) -> pd.DataFrame:
    """Return DataFrame indexed by frame with columns like ('RWrist', 'X')."""
    with open(path) as fh:
        lines = [next(fh) for _ in range(5)]
    names = lines[3].rstrip("\n").split("\t")
    markers = [n for n in names[2:] if n.strip()]
    raw = pd.read_csv(path, sep="\t", skiprows=5, header=None)
    raw = raw.iloc[:, :2 + 3 * len(markers)]
    raw.columns = ["frame", "time"] + [f"{m}_{a}" for m in markers for a in "XYZ"]
    raw = raw.dropna(subset=["frame"])
    raw["frame"] = raw["frame"].astype(int)
    return raw.set_index("frame").apply(pd.to_numeric, errors="coerce")


def read_mot(path: Path) -> pd.DataFrame:
    with open(path) as fh:
        for n, line in enumerate(fh):
            if line.strip().lower() == "endheader":
                break
    df = pd.read_csv(path, sep="\t", skiprows=n + 1)
    df.columns = [c.strip().lower() for c in df.columns]
    return df.apply(pd.to_numeric, errors="coerce")


# -----------------------------------------------------------------------------
# Per-frame kinematics
# -----------------------------------------------------------------------------
def gsmooth(x, sigma_frames):
    x = np.asarray(x, float)
    if sigma_frames <= 0.3:
        return x
    return gaussian_filter1d(x, sigma_frames, mode="nearest")


def fill_gaps(s: pd.Series) -> pd.Series:
    return s.interpolate(limit_area="inside").ffill().bfill()


def compute_kinematics(s2d, fps_eff):
    px = read_trc(s2d["trc_px"])
    m = read_trc(s2d["trc_m"]) if s2d["trc_m"] else None
    mot = read_mot(s2d["mot"])
    hand = "R" if PLAYER_HAND.lower().startswith("r") else "L"

    # Frames where Sports2D actually tracked the player (metres file is trimmed to these)
    frames = px.dropna(subset=[f"{hand}Wrist_X", "Neck_X", "Hip_X"]).index
    if not len(frames):
        raise RuntimeError("Sports2D did not track a full upper body - is the player in view?")
    first, last = int(frames.min()), int(frames.max())
    df = pd.DataFrame(index=pd.RangeIndex(first, last + 1, name="frame"))
    df["time_s"] = df.index / fps_eff
    raw = px.reindex(df.index)
    # Sports2D bridges tracking gaps by linear interpolation (or repeats the last value).
    # Real, filtered keypoints are never exactly linear on every joint at once, so frames
    # whose second difference is ~0 for all coordinates were not really tracked.
    xy = raw[[c for c in raw.columns if c.endswith(("_X", "_Y"))]]
    filled = (xy.diff().diff().abs().max(axis=1) < 0.005) | xy.isna().all(axis=1)
    # ...and when tracking is re-acquired the whole skeleton jumps: reject impossible
    # hip speeds (in torso lengths per second).
    t_med = np.nanmedian(np.hypot(raw["Neck_X"] - raw["Hip_X"], raw["Neck_Y"] - raw["Hip_Y"]))
    hip_v = np.hypot(raw["Hip_X"].diff(), raw["Hip_Y"].diff()) * fps_eff / t_med
    filled |= hip_v > MAX_HIP_SPEED_TORSO_PER_S
    pad = max(3, int(0.2 * fps_eff)) | 1     # also drop +/-0.1 s around each gap
    untracked = filled.astype(float).rolling(pad, center=True, min_periods=1).max().astype(bool)
    df["tracked"] = ~untracked.to_numpy()
    P = raw.apply(fill_gaps)

    torso = np.hypot(P["Neck_X"] - P["Hip_X"], P["Neck_Y"] - P["Hip_Y"])
    torso = pd.Series(torso).rolling(int(fps_eff) | 1, center=True, min_periods=1).median()
    df["torso_px"] = torso

    # Which way is the net?  The player looks at the ball / net most of the time.
    if NET_DIRECTION == "auto":
        facing = np.nanmedian(P["Nose_X"] - P["Neck_X"])
        net_dir = 1.0 if facing >= 0 else -1.0
    else:
        net_dir = 1.0 if NET_DIRECTION == "right" else -1.0
    # For a right-hander the LEFT shoulder points to the net at full unit turn;
    # sig keeps "forehand coil" positive for both hands.
    sig = 1.0 if hand == "R" else -1.0
    ok = df["tracked"].to_numpy()

    def turn_from(pair_l, pair_r, floor_ratio):
        ratio = net_dir * (P[f"{pair_l}_X"] - P[f"{pair_r}_X"]) / torso
        ratio = gsmooth(fill_gaps(ratio), TURN_SMOOTH_S * fps_eff)
        good = np.abs(ratio[ok]) if ok.any() else np.abs(ratio)
        ref = max(np.nanpercentile(good, SHOULDER_WIDTH_PERCENTILE), floor_ratio)
        return np.degrees(np.arcsin(np.clip(ratio / ref, -1, 1))), ratio, ref

    sh_turn, sh_ratio, sh_ref = turn_from("LShoulder", "RShoulder", MIN_SHOULDER_WIDTH_RATIO)
    hip_turn, _, _ = turn_from("LHip", "RHip", MIN_HIP_WIDTH_RATIO)
    df["shoulder_turn_kp_deg"] = sig * sh_turn
    df["hip_turn_deg"] = sig * hip_turn
    df["turn_ratio"] = sig * sh_ratio          # unclipped, used for peak timing

    # Sports2D segment angles (2D, image plane), kept for reference / alt. source
    mot["frame"] = np.round(mot["time"] * fps_eff).astype(int)
    mot = mot.drop_duplicates("frame").set_index("frame").reindex(df.index)
    # a shoulder line has no direction: Sports2D defines this angle in (-90, 90]
    df["s2d_shoulders_deg"] = (mot.get("shoulders") + 90) % 180 - 90
    df["s2d_trunk_deg"] = mot.get("trunk")
    df["s2d_pelvis_deg"] = mot.get("pelvis")

    if TURN_SOURCE == "sports2d_shoulders":
        a = fill_gaps(df["s2d_shoulders_deg"])
        a = gsmooth(np.degrees(np.unwrap(np.radians(a) * 2)) / 2, TURN_SMOOTH_S * fps_eff)
        df["shoulder_turn_deg"] = sig * net_dir * (a - np.nanmedian(a))
        df["turn_ratio"] = df["shoulder_turn_deg"]
    else:
        df["shoulder_turn_deg"] = df["shoulder_turn_kp_deg"]

    # Wrist speed in m/s. Prefer Sports2D's metres file; if its pixel->metre conversion failed (it needs the
    # full body, feet included), scale pixels by torso length instead.
    m_ok = m is not None and np.isfinite(m.reindex(df.index)[f"{hand}Wrist_X"]).mean() > 0.5
    if m_ok:   # sanity check: torso (neck to mid-hip) should be ~0.3-0.8 m
        M = m.reindex(df.index)
        torso_m = np.nanmedian(np.hypot(M["Neck_X"] - M["Hip_X"], M["Neck_Y"] - M["Hip_Y"]))
        m_ok = bool(0.25 < torso_m < 0.9)
    unit = "m/s"
    if m_ok:
        S, scale = m.reindex(df.index).apply(fill_gaps), 1.0
    else:
        S = P
        scale = TORSO_TO_HEIGHT * PLAYER_HEIGHT_M / np.nanmedian(torso)   # metres per pixel
        log.info("  Sports2D metre conversion unusable - scaling pixels by torso length.")
    wx, wy = S[f"{hand}Wrist_X"].to_numpy(), S[f"{hand}Wrist_Y"].to_numpy()
    if WRIST_SPEED_RELATIVE_TO_HIP:
        wx, wy = wx - S["Hip_X"].to_numpy(), wy - S["Hip_Y"].to_numpy()
    vx, vy = np.gradient(wx) * fps_eff * scale, np.gradient(wy) * fps_eff * scale
    df["wrist_speed"] = gsmooth(np.hypot(vx, vy), WRIST_SPEED_SMOOTH_S * fps_eff)
    for c in ("wrist_speed", "shoulder_turn_deg", "shoulder_turn_kp_deg", "hip_turn_deg", "turn_ratio"):
        df.loc[~df["tracked"], c] = np.nan
    df["wrist_vx_px"] = np.gradient(gsmooth(P[f"{hand}Wrist_X"], 1)) * fps_eff
    df["wrist_x_px"], df["wrist_y_px"] = P[f"{hand}Wrist_X"], P[f"{hand}Wrist_Y"]
    df["nose_y_px"] = P["Nose_Y"]
    # body points the racquet tip is never at (clothing / the other hand's wristband)
    other = "L" if hand == "R" else "R"
    for kp in (f"{other}Wrist", f"{other}Elbow", "LHip", "RHip", "Hip", "LKnee", "RKnee",
               "LAnkle", "RAnkle"):
        if f"{kp}_X" in P:
            df[f"_body_{kp}_x"], df[f"_body_{kp}_y"] = P[f"{kp}_X"], P[f"{kp}_Y"]
    # metres-per-pixel, to express racquet-tip speed in m/s
    if m_ok:
        tm = np.hypot(S["Neck_X"] - S["Hip_X"], S["Neck_Y"] - S["Hip_Y"])
        m_per_px = float(np.nanmedian(tm / torso))
    else:
        m_per_px = float(scale)
    meta = dict(net_dir="right" if net_dir > 0 else "left", speed_unit=unit,
                shoulder_width_ref=float(sh_ref), m_per_px=m_per_px,
                first_frame=first, last_frame=last,
                # Sports2D's video starts at the first row of its metres .trc
                video_offset=int(m.index.min()) if m is not None else 0)
    return df, meta


# -----------------------------------------------------------------------------
# Optional racquet-tip tracking
# -----------------------------------------------------------------------------
def tape_mask(hsv, colour=None):
    ranges = TAPE_HSV_RANGES[colour or TAPE_COLOUR]
    mask = None
    for lo, hi in ranges:
        mk = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = mk if mask is None else cv2.bitwise_or(mask, mk)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def track_racquet(clip: Path, df: pd.DataFrame, meta, fps_eff, out_dir: Path):
    cap = cv2.VideoCapture(str(clip))
    first, last_f = df.index.min(), df.index.max()
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    xs, ys = np.full(len(df), np.nan), np.full(len(df), np.nan)
    # Pink/orange things that never move (signs, a bag by the fence) are background:
    # sample frames across the clip and ignore pixels that are "tape coloured" in most.
    static = None
    samples = np.linspace(first, last_f, min(40, last_f - first + 1)).astype(int)
    for f in samples:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
        ok, frame = cap.read()
        if ok:
            m = (tape_mask(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)) > 0).astype(np.float32)
            static = m if static is None else static + m
    static = None if static is None else \
        cv2.dilate(((static / len(samples)) > TAPE_STATIC_FRACTION).astype(np.uint8) * 255,
                   np.ones((7, 7), np.uint8))
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    debug_saved = False
    last, gap_frames = None, max(2, int(0.1 * fps_eff))
    body_cols = [c for c in df.columns if c.startswith("_body_") and c.endswith("_x")]
    for i, f in enumerate(range(first, last_f + 1)):
        ok, frame = cap.read()
        if not ok:
            break
        mask = tape_mask(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV))
        if static is not None:
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(static))
        wx, wy, tl = df.at[f, "wrist_x_px"], df.at[f, "wrist_y_px"], df.at[f, "torso_px"]
        body = np.array([[df.at[f, c], df.at[f, c[:-1] + "y"]] for c in body_cols])
        body = body[np.isfinite(body).all(axis=1)] if len(body) else np.zeros((0, 2))
        if np.isfinite(wx) and np.isfinite(tl):   # restrict search to around the hand
            roi = np.zeros_like(mask)
            cv2.circle(roi, (int(wx), int(wy)), int(TAPE_SEARCH_RADIUS_TORSO * tl), 255, -1)
            mask = cv2.bitwise_and(mask, roi)
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask)
        cands = []
        for k in range(1, n):
            if not TAPE_MIN_AREA_PX <= stats[k, cv2.CC_STAT_AREA] <= TAPE_MAX_AREA_PX:
                continue
            cx, cy = cents[k]
            # skip blobs at the hand itself (wristbands, sleeves): the tip is a racquet away
            if np.isfinite(wx) and np.isfinite(tl) and \
                    np.hypot(cx - wx, cy - wy) < TAPE_MIN_DIST_FROM_WRIST_TORSO * tl:
                continue
            if np.isfinite(tl) and any(
                    np.hypot(cx - body[j, 0], cy - body[j, 1]) < TAPE_BODY_EXCLUSION_TORSO * tl
                    for j in range(len(body))):
                continue
            cands.append((cx, cy, stats[k, cv2.CC_STAT_AREA]))
        if cands:
            if last is not None and i - last[2] <= gap_frames:   # nearest to last tip position
                cx, cy, _ = min(cands, key=lambda c: np.hypot(c[0] - last[0], c[1] - last[1]))
            else:                                               # otherwise the biggest blob
                cx, cy, _ = max(cands, key=lambda c: c[2])
            xs[i], ys[i] = cx, cy
            last = (cx, cy, i)
        if not debug_saved and i == len(df) // 2:   # a mask preview for tuning
            prev = frame.copy()
            prev[mask > 0] = (255, 0, 255)
            cv2.imwrite(str(out_dir / "racquet_mask_preview.jpg"), prev)
            debug_saved = True
    cap.release()
    coverage = float(np.isfinite(xs).mean()) if len(xs) else 0.0
    gap = max(1, int(TAPE_MAX_GAP_S * fps_eff))
    x = pd.Series(xs, index=df.index).interpolate(limit=gap, limit_area="inside")
    y = pd.Series(ys, index=df.index).interpolate(limit=gap, limit_area="inside")
    df["racquet_x_px"], df["racquet_y_px"] = xs, ys
    sig = WRIST_SPEED_SMOOTH_S * fps_eff
    sp = np.hypot(np.gradient(x.to_numpy()), np.gradient(y.to_numpy())) * fps_eff
    sp = pd.Series(sp, index=df.index)
    sp_s = pd.Series(gsmooth(sp.fillna(0), sig), index=df.index).where(sp.notna())
    df["racquet_speed"] = sp_s * meta["m_per_px"]       # m/s
    return coverage


def tune_colour(clip: Path):
    """Interactive HSV range picker (needs a display - run it on your Mac)."""
    cap = cv2.VideoCapture(str(clip))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    win = "tape colour tuner - q to quit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    lo, hi = TAPE_HSV_RANGES[TAPE_COLOUR][0]
    for name, val, mx in [("frame", 0, max(n - 1, 1)), ("H lo", lo[0], 179), ("H hi", hi[0], 179),
                          ("S lo", lo[1], 255), ("S hi", hi[1], 255),
                          ("V lo", lo[2], 255), ("V hi", hi[2], 255)]:
        cv2.createTrackbar(name, win, val, mx, lambda _: None)
    last_f, frame = -1, None
    while True:
        f = cv2.getTrackbarPos("frame", win)
        if f != last_f:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, frame = cap.read()
            last_f = f
        if frame is None:
            break
        g = lambda k: cv2.getTrackbarPos(k, win)
        l, h = (g("H lo"), g("S lo"), g("V lo")), (g("H hi"), g("S hi"), g("V hi"))
        mask = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), np.array(l), np.array(h))
        show = frame.copy()
        show[mask > 0] = (255, 0, 255)
        cv2.imshow(win, show)
        if cv2.waitKey(30) & 0xFF in (ord("q"), 27):
            break
    cv2.destroyAllWindows()
    print(f'\nPaste into TAPE_HSV_RANGES in analyse.py:\n    "{TAPE_COLOUR}": [({l}, {h})],')


# -----------------------------------------------------------------------------
# Swing detection
# -----------------------------------------------------------------------------
@dataclass
class Swing:
    n: int
    contact_frame: int
    contact_time_s: float
    contact_speed: float
    stroke: str
    max_turn_frame: int = -1
    max_turn_time_s: float = float("nan")
    max_turn_deg: float = float("nan")
    hip_turn_at_max_deg: float = float("nan")
    separation_deg: float = float("nan")
    prep_time_s: float = float("nan")
    rating: str = ""
    flags: list = field(default_factory=list)


def rate(prep):
    if not np.isfinite(prep):
        return ""
    return "green" if prep >= GREEN_MIN_S else "amber" if prep >= AMBER_MIN_S else "red"


def find_max_coil(ratio, turn_deg):
    """Index and angle of maximum unit turn within a pre-contact window.

    ratio / turn_deg are oriented so the coil for this stroke is positive.
    From a side-on camera the shoulder line looks widest at 90 deg of turn and
    narrower again beyond it, so a coil past side-on shows up as two peaks near
    90 deg with a dip between them: the dip is the real maximum turn.
    """
    r = np.nan_to_num(ratio, nan=-np.inf)
    k = int(np.argmax(r))
    t = np.nan_to_num(turn_deg, nan=-999.0)
    if TURN_SOURCE == "keypoints" and t[k] >= PLATEAU_DEG:
        pk, _ = find_peaks(np.r_[-999.0, t, -999.0], height=PLATEAU_DEG, plateau_size=1)
        pk = pk - 1
        if len(pk) >= 2:
            a, b = pk[0], pk[-1]
            d = a + int(np.argmin(t[a:b + 1]))
            if min(t[a], t[b]) - t[d] >= DIP_MIN_DEG and 180.0 - t[d] <= MAX_COIL_DEG:
                return d, 180.0 - t[d], True
    return k, float(abs(turn_deg[k])), False


def detect_swings(df: pd.DataFrame, fps_eff, signal_col):
    speed = df[signal_col].fillna(0).to_numpy()
    if not len(speed) or speed.max() <= 0:
        return [], 0.0
    abs_min = cfg("MIN_CONTACT_SPEED_MPS")
    dist = max(1, int(MIN_SWING_GAP_S * fps_eff))
    # Reference swing speed = median of the 3 fastest candidate peaks, so a single
    # glitch (camera whip, tracking jump) cannot raise the bar for every other swing.
    cand, props = find_peaks(speed, height=max(abs_min, 1e-9), distance=dist)
    top = np.sort(props["peak_heights"])[::-1][:3]
    peak_ref = float(np.median(top)) if len(top) else float(speed.max())
    height = max(abs_min, cfg("MIN_PEAK_FRACTION") * peak_ref)
    peaks, _ = find_peaks(speed, height=height, distance=dist,
                          prominence=cfg("MIN_PEAK_PROMINENCE_FRACTION") * peak_ref)
    frames = df.index.to_numpy()
    turn, ratio = df["shoulder_turn_deg"].to_numpy(), df["turn_ratio"].to_numpy()
    hip = df["hip_turn_deg"].to_numpy()
    win = int(round(TURN_SEARCH_WINDOW_S * fps_eff))
    swings = []
    for p in peaks:
        f = int(frames[p])
        lo = p - win
        flags = []
        if lo < 0:
            flags.append("clip starts inside the 1.5 s window")
            lo = 0
        seg = ratio[lo:p + 1]
        if not np.isfinite(seg).any():
            continue
        if not np.isfinite(seg).all():
            flags.append("tracking gap before contact")
        # Stroke type from the direction of the coil before contact
        wrist_above = (df["nose_y_px"].iat[p] - df["wrist_y_px"].iat[p]) / df["torso_px"].iat[p]
        if wrist_above > OVERHEAD_WRIST_ABOVE_NOSE:
            stroke = "serve/overhead"
        elif np.nanmax(seg) >= -np.nanmin(seg):
            stroke = "forehand"
        else:
            stroke = "backhand"
        sw = Swing(len(swings) + 1, f, float(df["time_s"].iat[p]), float(speed[p]), stroke,
                   flags=flags)
        if stroke in ("forehand", "backhand"):
            sign = 1 if stroke == "forehand" else -1
            kk, angle, past = find_max_coil(sign * seg, sign * turn[lo:p + 1])
            k = lo + kk
            if past:
                flags.append("coiled past side-on: max turn taken at the foreshortening dip")
            sw.max_turn_frame = int(frames[k])
            sw.max_turn_time_s = float(df["time_s"].iat[k])
            sw.max_turn_deg = float(angle)
            sw.hip_turn_at_max_deg = float(abs(hip[k]))
            sw.separation_deg = float(abs(turn[k]) - abs(hip[k]))
            sw.prep_time_s = sw.contact_time_s - sw.max_turn_time_s
            first_valid = lo + int(np.argmax(np.isfinite(seg)))
            cut_off = k - first_valid <= 1 and (lo == 0 or first_valid > lo)
            if cut_off:   # turn peaked at the first frame we have: it may have peaked earlier
                flags.append("max turn may be before the clip / tracking starts - not rated")
            elif k - lo <= 1:
                flags.append("turn still rising at window start (prep may be longer)")
            small = sw.max_turn_deg < MIN_TURN_DEG
            if small:
                flags.append(f"small turn (<{MIN_TURN_DEG} deg) - probably not a groundstroke, "
                             "not rated")
            if stroke in cfg("RATE_STROKES") and not cut_off and not small:
                sw.rating = rate(sw.prep_time_s)
        swings.append(sw)
    return swings, height


# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------
def swings_table(swings, clip_name, unit):
    rows = []
    for s in swings:
        rows.append({"clip": clip_name, "swing": s.n, "stroke": s.stroke,
                     "contact_frame": s.contact_frame, "contact_time_s": round(s.contact_time_s, 3),
                     f"contact_speed_{unit.replace('/', '_per_')}": round(s.contact_speed, 2),
                     "max_turn_frame": s.max_turn_frame if s.max_turn_frame >= 0 else None,
                     "max_turn_time_s": round(s.max_turn_time_s, 3),
                     "max_turn_deg": round(s.max_turn_deg, 1),
                     "hip_turn_at_max_deg": round(s.hip_turn_at_max_deg, 1),
                     "shoulder_hip_separation_deg": round(s.separation_deg, 1),
                     "prep_time_s": round(s.prep_time_s, 3), "rating": s.rating,
                     "flags": "; ".join(s.flags)})
    return pd.DataFrame(rows)


def make_chart(df, swings, meta, signal_col, threshold, path: Path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, grid = "#0b0b0b", "#52514e", "#e4e3df"
    blue, orange = "#2a78d6", "#eb6834"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": ink2, "axes.labelcolor": ink2,
                         "xtick.color": ink2, "ytick.color": ink2, "axes.spines.top": False,
                         "axes.spines.right": False})
    fig, axes = plt.subplots(3, 1, figsize=(13, 8.5), sharex=True, facecolor="#fcfcfb",
                             gridspec_kw=dict(height_ratios=[3, 2.2, 1.4], hspace=0.28))
    t = df["time_s"].to_numpy()
    for ax in axes:
        ax.set_facecolor("#fcfcfb")
        ax.grid(axis="y", color=grid, lw=0.8)
    a0, a1, a2 = axes

    a0.plot(t, df["shoulder_turn_deg"], color=blue, lw=2, label="Shoulder turn")
    a0.plot(t, df["hip_turn_deg"], color=orange, lw=1.5, ls="--", label="Hip turn")
    a0.axhline(0, color=ink2, lw=0.8)
    a0.set_ylabel("Turn (deg)\n+ = forehand coil")
    a0.set_yticks([-90, -45, 0, 45, 90])
    ymax = np.nanmax(np.abs(df["shoulder_turn_deg"])) if len(df) else 90
    lo_y = -ymax * 1.1 - 5
    a0.set_ylim(lo_y, lo_y + (ymax * 1.05 + 5 - lo_y) / 0.68)   # top 32 % reserved for labels

    unit = meta["speed_unit"]
    lab = ("Wrist speed (relative to hips)" if WRIST_SPEED_RELATIVE_TO_HIP else "Wrist speed") \
        if signal_col == "wrist_speed" else "Racquet-tip speed"
    a1.plot(t, df[signal_col], color=blue, lw=2, label=lab)
    if signal_col != "wrist_speed":
        a1.plot(t, df["wrist_speed"], color=orange, lw=1.2, ls="--", label="Wrist speed")
    a1.axhline(threshold, color=ink2, lw=1, ls=":", label="Contact threshold")
    a1.set_ylabel(f"{lab.split(' (')[0]}\n({unit})")

    a2.plot(t, df["s2d_shoulders_deg"], color=blue, lw=1.5, label="Sports2D 'shoulders' segment")
    a2.plot(t, df["s2d_trunk_deg"], color=orange, lw=1.5, ls="--", label="Sports2D 'trunk' segment")
    a2.set_ylabel("Sports2D\nangles (deg)")
    a2.set_xlabel("Time (s)")

    for s in swings:
        c = RATING_HEX.get(s.rating, "#8a8984")
        for ax in axes:
            ax.axvline(s.contact_time_s, color=c, lw=1.6)
        if s.max_turn_frame >= 0:
            a0.axvspan(s.max_turn_time_s, s.contact_time_s, color=c, alpha=0.13, lw=0)
            yk = df.at[s.max_turn_frame, "shoulder_turn_deg"]
            a0.plot([s.max_turn_time_s], [yk], marker="v", ms=10, color=c,
                    mec="#fcfcfb", mew=2, zorder=5)
            label = f"#{s.n} {s.stroke}\n{s.prep_time_s:.2f} s"
            if s.rating:
                label += f" {s.rating.upper()}"
        else:
            label = f"#{s.n} {s.stroke}"
        row = (s.n - 1) % 2   # stagger labels so neighbouring swings don't collide
        a0.annotate(label, (s.contact_time_s, 0.985 - 0.15 * row), ha="right", va="top",
                    fontsize=8.5, color=ink, xytext=(-3, 0), textcoords="offset points",
                    xycoords=a0.get_xaxis_transform())
        a1.plot([s.contact_time_s], [s.contact_speed], "o", ms=8, color=c, mec="#fcfcfb", mew=2,
                zorder=5)

    from matplotlib.lines import Line2D
    h0, l0 = a0.get_legend_handles_labels()
    h0 += [Line2D([], [], marker="v", ls="", color=ink2, ms=8),
           Line2D([], [], color=ink2, lw=1.6)]
    l0 += ["Max unit turn", "Contact (colour = rating)"]
    kw = dict(fontsize=8.5, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0),
              borderaxespad=0.2)
    a0.legend(h0, l0, ncol=4, **kw)
    a1.legend(ncol=3, **kw)
    a2.legend(ncol=2, **kw)
    fig.suptitle(title, x=0.06, y=0.985, ha="left", fontsize=13, color=ink)
    fig.text(0.06, 0.945, f"Shaded span = preparation time (max turn -> contact). "
             f"Green >= {GREEN_MIN_S}s, amber {AMBER_MIN_S}-{GREEN_MIN_S}s, red < {AMBER_MIN_S}s. "
             f"Grey = not rated.", fontsize=9, color=ink2)
    fig.savefig(path, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


class VideoOut:
    """H.264 via imageio-ffmpeg when available (plays in QuickTime), else OpenCV mp4v."""

    def __init__(self, path: Path, fps, size):
        self.path, self.gen, self.cv = path, None, None
        try:
            import imageio_ffmpeg
            self.gen = imageio_ffmpeg.write_frames(
                str(path), size, fps=fps, codec="libx264", pix_fmt_in="bgr24",
                output_params=["-crf", "23", "-preset", "veryfast", "-movflags", "+faststart"],
                macro_block_size=2)
            self.gen.send(None)
        except Exception as e:  # noqa: BLE001
            log.info(f"  (imageio-ffmpeg unavailable: {e}; writing mp4v with OpenCV)")
            self.gen = None
            self.cv = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)

    def write(self, img):
        if self.gen is not None:
            self.gen.send(np.ascontiguousarray(img))
        else:
            self.cv.write(img)

    def close(self):
        if self.gen is not None:
            self.gen.close()
        else:
            self.cv.release()


def banner(img, text, colour, y, right=False):
    h, w = img.shape[:2]
    scale = max(0.6, w / 1600)
    th = max(2, int(2 * scale))
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    x = w - tw - 30 if right else 20
    cv2.rectangle(img, (x - 10, y - tht - 12), (x + tw + 10, y + 12), (25, 25, 25), -1)
    cv2.rectangle(img, (x - 10, y - tht - 12), (x - 3, y + 12), colour, -1)
    cv2.putText(img, text, (x + 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), th,
                cv2.LINE_AA)


def annotate_video(s2d, df, swings, meta, fps_src, fps_eff, out_path: Path, signal_col):
    cap = cv2.VideoCapture(str(s2d["video"]))
    n_vid = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    offset = meta["video_offset"]
    out_fps = ANNOTATED_VIDEO_FPS or (30 if fps_src >= 100 else fps_src)
    out_fps_ = ANNOTATED_VIDEO_FPS or (30 if fps_src >= 100 else fps_src)
    hold = int(MARKER_LABEL_HOLD_S * out_fps_)  # one source frame = one output frame
    freeze = int(FREEZE_ON_MARKERS_S * out_fps)
    events = {}
    for s in swings:
        c = RATING_BGR.get(s.rating, NEUTRAL_BGR)
        if s.max_turn_frame >= 0:
            past = any("past side-on" in fl for fl in s.flags)
            events.setdefault(s.max_turn_frame, []).append(
                ("turn", f"#{s.n} MAX TURN ~{s.max_turn_deg:.0f} deg"
                 + (" (past side-on)" if past else ""), TURN_BGR))
        txt = f"#{s.n} CONTACT ({s.stroke})"
        if np.isfinite(s.prep_time_s):
            txt += f"  prep {s.prep_time_s:.2f}s"
        if s.rating:
            txt += f"  {s.rating.upper()}"
        events.setdefault(s.contact_frame, []).append(("contact", txt, c))
    writer = VideoOut(out_path, out_fps, (w, h))
    first, last = int(df.index.min()), int(df.index.max())
    bar_y = h - 28
    tx = lambda f: int(20 + (w - 40) * (f - first) / max(1, last - first))
    active = []
    for k in range(n_vid):
        ok, img = cap.read()
        if not ok:
            break
        f = offset + k
        # timeline along the bottom with all markers
        cv2.rectangle(img, (10, bar_y - 16), (w - 10, h - 6), (25, 25, 25), -1)
        cv2.line(img, (20, bar_y), (w - 20, bar_y), (120, 120, 120), 2)
        for s in swings:
            if s.max_turn_frame >= 0:
                cv2.rectangle(img, (tx(s.max_turn_frame), bar_y - 5), (tx(s.contact_frame), bar_y + 5),
                              RATING_BGR.get(s.rating, NEUTRAL_BGR), -1)
                cv2.drawMarker(img, (tx(s.max_turn_frame), bar_y), TURN_BGR, cv2.MARKER_TRIANGLE_DOWN,
                               14, 2)
            cv2.line(img, (tx(s.contact_frame), bar_y - 10), (tx(s.contact_frame), bar_y + 10),
                     (255, 255, 255), 2)
        cv2.line(img, (tx(f), bar_y - 14), (tx(f), bar_y + 14), (0, 255, 255), 2)
        # live readout
        if f in df.index:
            r = df.loc[f]
            unit = meta["speed_unit"]
            txt = (f"t={r.time_s:5.2f}s  shoulder turn {r.shoulder_turn_deg:+4.0f} deg  "
                   f"wrist {r.wrist_speed:4.1f} {unit}")
            banner(img, txt, (120, 120, 120), int(h * 0.94) - 30)
            if "racquet_x_px" in df and np.isfinite(r.get("racquet_x_px", np.nan)):
                cv2.circle(img, (int(r.racquet_x_px), int(r.racquet_y_px)), 10, (255, 0, 255), 2)
        ev = events.get(f, [])
        active = [(e, until) for e, until in active if until >= f] + [(e, f + hold) for e in ev]
        # put event banners on the side of the frame away from the player
        px_ = df.at[f, "wrist_x_px"] if f in df.index else np.nan
        right = bool(np.isfinite(px_) and px_ < w / 2)
        for i, (e, _) in enumerate(active):
            banner(img, e[1], e[2], 60 + i * int(55 * max(1, w / 1600)), right=right)
        if ev:
            colour = ev[-1][2]
            cv2.rectangle(img, (0, 0), (w - 1, h - 1), colour, 12)
            for _ in range(freeze):
                writer.write(img)
        writer.write(img)
    cap.release()
    writer.close()


def text_summary(name, swings, meta, signal_name, fps_src, fps_eff, coverage=None):
    lines = [f"Clip: {name}",
             f"Frame rate: {fps_src:.1f} fps in file, {fps_eff:.1f} fps real time "
             f"(SLOWMO_FACTOR={SLOWMO_FACTOR})",
             f"Tracked: frames {meta['first_frame']}-{meta['last_frame']}, net to the "
             f"{meta['net_dir']} of the image, contact from {signal_name}"]
    if meta.get("tracked_fraction", 1) < 0.8:
        lines.append(f"WARNING: the player was only tracked in {meta['tracked_fraction']:.0%} of the "
                     "clip (out of frame, hidden, or another person picked). Swings outside "
                     "the tracked part are missing - check the annotated video.")
    if coverage is not None:
        lines.append(f"Racquet tape found in {coverage:.0%} of frames")
    lines.append("")
    rated = [s for s in swings if s.rating]
    if not swings:
        lines.append("No swings detected - check MIN_CONTACT_SPEED_MPS / MIN_PEAK_FRACTION.")
    for s in swings:
        line = f"Swing {s.n:2d} @ {s.contact_time_s:6.2f}s  {s.stroke:15s}"
        if np.isfinite(s.prep_time_s):
            line += f"prep {s.prep_time_s:4.2f}s  max turn {s.max_turn_deg:3.0f} deg"
        if s.rating:
            line += f"  -> {s.rating.upper()}"
        if s.flags:
            line += f"   [{'; '.join(s.flags)}]"
        lines.append(line)
    lines.append("")
    if rated:
        p = np.array([s.prep_time_s for s in rated])
        cnt = {r: sum(s.rating == r for s in rated) for r in ("green", "amber", "red")}
        lines.append(f"Rated swings ({', '.join(cfg('RATE_STROKES'))}): {len(rated)}  |  "
                     f"mean prep {p.mean():.2f}s (min {p.min():.2f}, max {p.max():.2f})  |  "
                     f"green {cnt['green']}, amber {cnt['amber']}, red {cnt['red']}")
    else:
        lines.append(f"No rated swings ({', '.join(cfg('RATE_STROKES'))}) in this clip.")
    return "\n".join(lines) + "\n"


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------
def analyse_clip(clip: Path, results: Path, args):
    name = clip.stem
    out = results / name
    out.mkdir(parents=True, exist_ok=True)
    log.info(f"\n=== {clip.name} ===")
    cap = cv2.VideoCapture(str(clip))
    fps_src = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    cap.release()
    fps_eff = fps_src * SLOWMO_FACTOR

    s2d = run_sports2d(clip, out / "sports2d", rerun=args.rerun)
    df, meta = compute_kinematics(s2d, fps_eff)
    meta["tracked_fraction"] = float(df["tracked"].sum() / n_frames)

    coverage, signal_col = None, "wrist_speed"
    if args.racquet or RACQUET_TRACKING:
        log.info(f"  Tracking '{TAPE_COLOUR}' racquet tape...")
        coverage = track_racquet(clip, df, meta, fps_eff, out)
        log.info(f"  Tape found in {coverage:.0%} of frames.")
        if CONTACT_SIGNAL == "racquet" or (CONTACT_SIGNAL == "auto" and coverage >= RACQUET_MIN_COVERAGE):
            signal_col = "racquet_speed"
    swings, thr = detect_swings(df, fps_eff, signal_col)
    signal_name = "racquet-tip speed" if signal_col != "wrist_speed" else \
        f"{PLAYER_HAND} wrist speed" + (" (relative to hips)" if WRIST_SPEED_RELATIVE_TO_HIP else "")

    # Per-frame data
    frames = df.drop(columns=[c for c in df.columns if c.startswith("_")])
    frames["is_contact"] = frames.index.isin([s.contact_frame for s in swings])
    frames["is_max_turn"] = frames.index.isin([s.max_turn_frame for s in swings])
    frames.round(4).to_csv(out / f"{name}_frames.csv")
    table = swings_table(swings, name, meta["speed_unit"])
    table.to_csv(out / f"{name}_swings.csv", index=False)
    summary = text_summary(name, swings, meta, signal_name, fps_src, fps_eff, coverage)
    (out / f"{name}_summary.txt").write_text(summary)
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    make_chart(df, swings, meta, signal_col, thr, out / f"{name}_chart.png",
               f"{name} - forehand preparation timing")
    if WRITE_ANNOTATED_VIDEO and not args.no_video:
        log.info("  Writing annotated video...")
        annotate_video(s2d, df, swings, meta, fps_src, fps_eff, out / f"{name}_annotated.mp4",
                       signal_col)
    log.info(summary)
    return table


def find_clips(paths):
    clips = []
    for p in map(Path, paths):
        if p.is_dir():
            clips += sorted(f for f in p.iterdir()
                            if f.suffix.lower() in VIDEO_EXTENSIONS and not f.name.startswith("."))
        elif p.is_file():
            clips.append(p)
    return clips


def combined_summary(tables, results: Path):
    if not tables:
        return
    tables = [t for t in tables if len(t)]
    if not tables:
        (results / "summary.txt").write_text("No swings detected in any clip.\n")
        return
    allt = pd.concat(tables, ignore_index=True)
    allt.to_csv(results / "all_swings.csv", index=False)
    lines = ["Forehand preparation timing - all clips", "=" * 40, ""]
    rated = allt[allt["rating"].astype(str).str.len() > 0]
    for clip, g in allt.groupby("clip", sort=False):
        r = g[g["rating"].astype(str).str.len() > 0]
        if len(r):
            lines.append(f"{clip}: {len(r)} rated of {len(g)} swings, mean prep "
                         f"{r['prep_time_s'].mean():.2f}s, green {(r.rating == 'green').sum()} / "
                         f"amber {(r.rating == 'amber').sum()} / red {(r.rating == 'red').sum()}")
        else:
            lines.append(f"{clip}: {len(g)} swings, none rated")
    lines.append("")
    if len(rated):
        lines.append(f"OVERALL: {len(rated)} rated swings, mean prep {rated.prep_time_s.mean():.2f}s, "
                     f"median {rated.prep_time_s.median():.2f}s, mean max turn "
                     f"{rated.max_turn_deg.mean():.0f} deg")
        for r in ("green", "amber", "red"):
            k = (rated.rating == r).sum()
            lines.append(f"  {r:6s} {k:3d}  ({k / len(rated):.0%})")
    (results / "summary.txt").write_text("\n".join(lines) + "\n")
    log.info("\n" + "\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", default=["clips"], help="folder(s) or video file(s)")
    ap.add_argument("-o", "--results", default="results", help="output folder (default: results)")
    ap.add_argument("--rerun", action="store_true", help="re-run Sports2D even if results exist")
    ap.add_argument("--no-video", action="store_true", help="skip the annotated video")
    ap.add_argument("--racquet", action="store_true", help="track fluorescent tape on the racquet tip")
    ap.add_argument("--tune-colour", metavar="CLIP", help="open the HSV tape-colour tuner on a clip")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.tune_colour:
        tune_colour(Path(args.tune_colour))
        return
    clips = find_clips(args.inputs)
    if not clips:
        import Sports2D
        demo = Path(Sports2D.__file__).parent / "Demo" / "demo.mp4"
        log.warning(f"No clips found in {args.inputs} - running on the Sports2D demo video "
                    f"({demo}).\nIt shows someone WALKING, so thresholds are relaxed "
                    "(DEMO_OVERRIDES) and the 'swings' are arm swings. Tune on your own footage.")
        globals().update(DEMO_OVERRIDES)
        clips = [demo]
    results = Path(args.results)
    results.mkdir(parents=True, exist_ok=True)
    tables = []
    for clip in clips:
        try:
            tables.append(analyse_clip(clip, results, args))
        except Exception as e:  # keep going with the other clips
            log.exception(f"  FAILED on {clip.name}: {e}")
    combined_summary(tables, results)
    log.info(f"\nResults in {results.resolve()}")


if __name__ == "__main__":
    main()
