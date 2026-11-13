#!/usr/bin/env python3
"""
Download openly licensed professional-tennis example clips from Wikimedia Commons.

    python fetch_examples.py            # -> example_clips/
    python analyse.py example_clips/

Djokovic (2012 US Open) is filmed at court level, roughly side-on: closest to the
intended set-up, but 24 fps and the camera pans. The 2026 US Open clips are 60 fps
from a fixed phone, but high in the stands and ~45 deg behind the baseline, so
they test tracking and swing detection only (turn angles are not valid there).
Licences: CC BY-SA 2.0 / 4.0 - see example_clips/ATTRIBUTION.txt after downloading.
"""
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "TennisPrepTimingExamples/1.0 (personal analysis script; python-urllib)"
API = "https://commons.wikimedia.org/w/api.php"
CLIPS = {  # local name: Commons file title
    # court level, roughly side-on (panning camera, 24 fps): the closest to the intended set-up
    "djokovic_2012_usopen": "File:2012 US Open Djokovic point.webm",
    "sharapova_amelia_island": "File:Maria Sharapova on Amelia Island.ogv",
    # 2026 US Open from the stands (fixed phone, 60 fps, high and ~45 deg behind the baseline;
    # the crowd sometimes hides the near player's feet, so tracking is partial in some)
    "alcaraz_wu_18": "File:Carlos Alcaraz vs. Wu Yibing (2026 US Open) - 18.webm",
    "alcaraz_wu_35": "File:Carlos Alcaraz vs. Wu Yibing (2026 US Open) - 35.webm",
    "pegula_fernandez_02": "File:Jessica Pegula vs. Leylah Fernandez (2026 US Open) - 02.webm",
}
# Commons transcodes to try, best first (the 4K originals are ~100-200 MB each, and
# Wikimedia rate-limits downloads of originals much harder than transcodes).
TRANSCODES = ["1080p.vp9.webm", "480p.vp9.webm", "360p.mpeg4.mov"]


def get(url, tries=6):
    for a in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}),
                                          timeout=120).read()
        except Exception as e:  # noqa: BLE001  (429 rate limits are common - back off)
            print(f"  retry ({e})")
            time.sleep(15 * (a + 1))
    raise RuntimeError(f"could not download {url}")


def to_mp4(src: Path, dest: Path):
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
                    "-an", "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-pix_fmt", "yuv420p", str(dest)], check=True)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "example_clips")
    out.mkdir(parents=True, exist_ok=True)
    q = urllib.parse.urlencode(dict(action="query", format="json", prop="imageinfo|videoinfo",
                                    iiprop="url|extmetadata", viprop="derivatives",
                                    iiextmetadatafilter="LicenseShortName|Artist",
                                    titles="|".join(CLIPS.values())))
    pages = {p["title"]: p for p in json.loads(get(f"{API}?{q}"))["query"]["pages"].values()}
    credits = []
    for name, title in CLIPS.items():
        p = pages[title]
        derivs = {d.get("transcodekey"): d["src"] for d in p["videoinfo"][0]["derivatives"]}
        key = next((k for k in TRANSCODES if k in derivs), None)
        src = derivs[key] if key else p["imageinfo"][0]["url"]
        ext = ".mov" if key and key.endswith(".mov") else ".webm"
        meta = p["imageinfo"][0].get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "unknown")).strip()
        credits.append(f"{name}.mp4: \"{title[5:]}\" by {artist}, "
                       f"{meta.get('LicenseShortName', {}).get('value', '?')}, "
                       f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}")
        dest = out / f"{name}.mp4"
        if dest.exists() and dest.stat().st_size > 0:
            print(f"{dest} exists"); continue
        print(f"Downloading {name} ...")
        tmp = out / f".{name}{ext}"
        tmp.write_bytes(get(src))
        to_mp4(tmp, dest)   # VP9 .webm reports wrong frame counts to OpenCV; H.264 does not
        tmp.unlink()
        time.sleep(5)   # be polite to Wikimedia
    (out / "ATTRIBUTION.txt").write_text(
        "Clips from Wikimedia Commons. Each file page credits its author; reuse under the\n"
        "licence shown (CC BY-SA 4.0 = attribution + share-alike).\n\n" + "\n".join(credits) + "\n")
    print(f"Done -> {out}/  (now run: python analyse.py {out}/)")


if __name__ == "__main__":
    main()
