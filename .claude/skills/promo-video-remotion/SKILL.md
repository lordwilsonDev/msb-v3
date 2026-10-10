---
name: promo-video-remotion
description: Builds a finished social promo video (Facebook/Instagram feed or Reels) with Remotion from brand assets. Covers scene design with scroll and reveal effects, edge-traveling light effects, a narrated voiceover generated with edge-tts, word-synced captions, a timeline that follows the audio, and render plus verification. Use when the user asks for a promo, ad, or social video, says "use remotion," asks for a voiceover, captions, scrolling effects, or light effects on a video, or wants a reusable video skill built from a previous video.
---

# Promo Video with Remotion

Produces a vertical or square promo video from a flyer, logo, and headshot. The
picture is driven by a timeline that the voiceover generator writes, so narration,
captions, and scene lengths always agree.

## Inputs to collect first

Ask only for what is missing. Otherwise assume the defaults.

- **Brand assets:** logo (transparent or dark background), headshot, flyer or
  key visual (note its pixel size), brand colors if they differ from the defaults.
- **Copy:** the narration lines and on-screen headlines. Keep every claim tied to
  source material. Do not invent services, locations, or credentials.
- **Destination:** Facebook feed is 1080x1350 (4:5). Reels/Stories is 1080x1920.
  Square is 1080x1080.
- **Voice:** default `en-US-AndrewNeural`. A user recording replaces the mp3s
  and keeps the same timing contract.

## Project layout

```
<project>/
  package.json          remotion 4.0.x pinned, react 18.3.1
  remotion.config.ts
  tsconfig.json         resolveJsonModule: true
  public/logo.png, flyer.png, headshot.png
  public/vo/<id>.mp3    written by make_vo.py
  scripts/make_vo.py    copied from this skill
  src/index.ts          registerRoot
  src/Root.tsx          Composition, durationInFrames from timeline.json
  src/FoxValley.tsx     scenes, lighting, captions, audio
  src/timeline.json     written by make_vo.py (do not hand-edit)
  out/                  renders (gitignored)
```

Reference implementation: `marketing/fox-valley-video` on `claude/new-session-simsar`.

## Workflow

1. **Scaffold.** Create the layout above. Install with
   `npm install` (remotion, @remotion/cli, react, react-dom). Pin exact versions.
   Put `node_modules/` and `out/` in `.gitignore`.
2. **Write the script.** Edit `SCENES` in `scripts/make_vo.py`. Each scene has a
   minimum length and voiceover lines with scene-relative anchors (or `None` to
   follow the previous line). Scenes grow to fit their audio.
3. **Generate the voiceover.**
   `PROJECT_DIR=. python3 scripts/make_vo.py`. In sandboxed networks, add
   `CA_BUNDLE=/root/.ccr/ca-bundle.crt`. This points certifi at the proxy CA and
   keeps verification on. Never disable TLS verification.
   Check that each line's word count matches its tokens. The script prints the
   timeline and total length.
4. **Build the scenes** in `src/FoxValley.tsx`:
   - Read scene starts and lengths from `timeline.json`. Do not hard-code them.
   - Use `spring` rises for entrances, staggered per word or card.
   - Scroll effects: translate a tall image inside an `overflow: hidden` frame with
     `Easing.inOut(Easing.cubic)`, plus a scroll-track indicator.
   - Marquee: linear `translateX` on two counter-moving rows.
5. **Lighting:**
   - `OrbitRect`: an SVG rounded rectangle with `pathLength={100}`, a dash comet
     moved by `strokeDashoffset`, a blurred bloom layer, and a hot core. Use it on
     frames, viewports, and rings.
   - `FrameLight`: full-canvas border comet plus a pulsing inset glow.
   - `Sheen`: a screen-blended diagonal gradient swept across a surface.
   - Keep effects subtle and gold-toned. Use `mix-blend-mode: screen` only on
     elements without an opacity-wrapped parent.
6. **Captions:** built from `timeline.voiceover` word timings. Show 3 to 5 words per
   card, break on punctuation, and highlight the spoken word. Place them at the
   bottom of the frame with `marginBottom` of about 70px, and keep scene content
   above that band. Re-check the frames where captions and content meet.
7. **Audio:** one `<Sequence from={line.startFrame}><Audio/></Sequence>` per line.
8. **Verify before rendering:**
   - `npx tsc --noEmit -p .`
   - Still frames at key moments: `npx remotion still src/index.ts <Comp> out/check-N.png --frame=N`.
     Read them. Check for text overflow, collisions with captions, and cut-off content.
   - After rendering, `ffprobe` should show the expected size, fps, H.264, and AAC.
     Check that `volumedetect` is audible inside each line and silent between them.
9. **Render:** `npx remotion render src/index.ts <Comp> out/<name>.mp4`
   (about 40 seconds for 38 seconds of video).
10. **Deliver:** commit the source and the generated `timeline.json` and mp3s, without
    `node_modules` or `out/`. Push to the designated branch. Send the mp4 to the user.
    Report the duration, the voice, and any copy the user should confirm.

## Timeline contract

`timeline.json`:

```json
{ "fps": 30, "durationInFrames": 1143,
  "scenes": [{"name": "intro", "start": 0, "length": 131}],
  "voiceover": [{"id": "v1", "startFrame": 12, "durationFrames": 99,
                 "file": "vo/v1.mp3", "text": "...",
                 "words": [{"text": "Black", "startMs": 0, "endMs": 210}]}] }
```

Scene names must match the component map in `FoxValley.tsx`.

## Pitfalls

- **Missing word timings:** edge-tts returns no `WordBoundary` events unless
  `boundary="WordBoundary"` is passed. Captions silently break without it.
- **Overlapping lines:** make the generator place each line after the previous one.
  A clip longer than the gap to the next anchor will overlap it.
- **Blend modes:** `mix-blend-mode` is cancelled by an opacity-wrapped parent. Fade
  with an overlay element instead of group opacity.
- **Stale drafts:** delete old timeline or voiceover JSON. The composition must read only
  `timeline.json`.
- **Sandbox TLS:** `edge-tts` loads `certifi.where()` at import time. `SSL_CERT_FILE`
  does not affect it. Use `CA_BUNDLE` as described above.
- **Claims:** keep copy within the source material. Ask before adding locations,
  credentials, or numbers.
