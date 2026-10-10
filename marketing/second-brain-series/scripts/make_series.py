"""Generate voiceover and per-episode timelines from series.json.

Writes public/vo/<ep>-<n>.mp3 and src/timelines/<ep>.json.
Run from the project root:  CA_BUNDLE=... python3 scripts/make_series.py
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import certifi

if os.environ.get("CA_BUNDLE"):
    certifi.where = lambda: os.environ["CA_BUNDLE"]

import edge_tts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VOICE = "en-US-AndrewNeural"
RATE = "-4%"
FPS = 30
LEAD = 14   # frames from scene start to first spoken word
TAIL = 20


async def synth(path: Path, text: str):
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    words = []
    with open(path, "wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000
                dur = chunk["duration"] / 10_000
                words.append({"text": chunk["text"], "startMs": round(start), "endMs": round(start + dur)})
    dur_s = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    ).decode().strip())
    return words, dur_s


async def main():
    series = json.loads((ROOT / "series.json").read_text())
    (ROOT / "public" / "vo").mkdir(parents=True, exist_ok=True)
    (ROOT / "src" / "timelines").mkdir(parents=True, exist_ok=True)
    for ep in series["episodes"]:
        cursor = 0
        scenes_out, vo_out = [], []
        for n, scene in enumerate(ep["scenes"]):
            start = cursor
            length = scene["dur"]
            if scene.get("vo"):
                file = f"{ep['id']}-{n}.mp3"
                words, dur_s = await synth(ROOT / "public" / "vo" / file, scene["vo"])
                dur_f = round(dur_s * FPS)
                length = max(length, LEAD + dur_f + TAIL)
                vo_out.append({
                    "id": f"{ep['id']}-{n}",
                    "startFrame": start + LEAD,
                    "durationFrames": dur_f,
                    "file": f"vo/{file}",
                    "text": scene["vo"],
                    "words": words,
                })
            scenes_out.append({**scene, "start": start, "length": length})
            cursor += length
        out = {"id": ep["id"], "title": ep["title"], "funnelStage": ep["funnelStage"],
               "fps": FPS, "durationInFrames": cursor, "scenes": scenes_out, "voiceover": vo_out}
        (ROOT / "src" / "timelines" / f"{ep['id']}.json").write_text(json.dumps(out, indent=2))
        print(f"{ep['id']} {ep['funnelStage']:<11} {cursor} frames = {cursor / FPS:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
