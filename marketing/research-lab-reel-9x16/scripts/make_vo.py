"""Generate the voiceover and the timeline the Remotion composition reads.

For each scene: a fixed minimum length, plus voiceover lines anchored at
scene-relative frames. Scene length grows to fit its voiceover.

Writes:
  public/vo/<id>.mp3     one clip per line
  src/timeline.json      scene starts/lengths + voiceover lines with word timings

Run from the project root:
  PROJECT_DIR=. python3 <skill>/scripts/make_vo.py
CA_BUNDLE=/path/to/ca.crt optional (sandboxed networks that re-sign TLS).
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import certifi

# Sandboxed networks re-sign TLS with their own CA. Point certifi (which
# edge-tts loads at import time) at that bundle. Verification stays on.
if os.environ.get("CA_BUNDLE"):
    certifi.where = lambda: os.environ["CA_BUNDLE"]

import edge_tts  # noqa: E402

ROOT = Path(os.environ.get("PROJECT_DIR", Path.cwd())).resolve()
VOICE = "en-US-AndrewNeural"
RATE = "-4%"
FPS = 30

# (scene, minimum length in frames, [(line id, scene-relative anchor frame or None, text)])
# anchor None = start 12 frames after the previous line in the same scene.
SCENES = [
    ("intro", 75, [
        ("v1", 12, "Black Swan Labz Research Lab."),
    ]),
    ("hook", 100, [
        ("v2", 14, "Intelligence is becoming abundant. Verification isn't."),
    ]),
    ("cornerstone", 150, [
        ("v3", 14, "One week in December 2025: thirty two point five million lines committed."),
        ("v4", None, "About five million are source code."),
    ]),
    ("categories", 150, [
        ("v5", 14, "Thirty five categories, from orchestration to safety."),
    ]),
    ("timeline", 150, [
        ("v6", 14, "The folders came first. The industry followed."),
    ]),
    ("built", 150, [
        ("v7", 14, "What's built is checkable. Three thousand nine hundred forty two tests collected, at a pinned commit."),
    ]),
    ("status", 150, [
        ("v8", 14, "Not everything is built. Check the receipts on GitHub."),
    ]),
    ("cta", 240, [
        ("v9", 14, "New to AI? Book a free fifteen minute clarity call."),
        ("v10", None, "Blackswanlabz.com."),
    ]),
    ("outro", 120, [
        ("v11", 14, "Strategy. Automation. Results."),
    ]),
]
TAIL = 20  # frames of breathing room after the last line in a scene


async def synth(vid: str, text: str):
    out_mp3 = ROOT / "public" / "vo" / f"{vid}.mp3"
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    words = []
    with open(out_mp3, "wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000  # 100ns ticks -> ms
                dur = chunk["duration"] / 10_000
                words.append({"text": chunk["text"], "startMs": round(start), "endMs": round(start + dur)})
    dur_s = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_mp3)]
    ).decode().strip())
    return words, dur_s


async def main():
    (ROOT / "public" / "vo").mkdir(parents=True, exist_ok=True)
    scenes_out, vo_out = [], []
    start = 0
    for name, min_len, lines in SCENES:
        scene_lines = []
        end = 0
        for vid, anchor, text in lines:
            words, dur_s = await synth(vid, text)
            dur_f = round(dur_s * FPS)
            if anchor is None:  # place after the previous line in this scene
                anchor = end + 12
            end = max(end, anchor + dur_f)
            scene_lines.append({
                "id": vid,
                "startFrame": start + anchor,
                "durationFrames": dur_f,
                "file": f"vo/{vid}.mp3",
                "text": text,
                "words": words,
            })
            print(f"{name:9s} {vid:4s} at {start + anchor:5d} len {dur_f:4d}f  {text}")
        length = max(min_len, end + TAIL)
        scenes_out.append({"name": name, "start": start, "length": length})
        vo_out.extend(scene_lines)
        start += length
    total = start
    (ROOT / "src" / "timeline.json").write_text(json.dumps(
        {"fps": FPS, "durationInFrames": total, "scenes": scenes_out, "voiceover": vo_out}, indent=2))
    print(f"total {total} frames = {total / FPS:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
