# The Second Brain Series: funnel

Four vertical episodes (9:16, 1080x1920) that move a viewer from "this happens to me" to a free 15-minute clarity call. Content lives in `series.json`. Rendering and voiceover are generated from it, so copy changes don't need code changes.

## Funnel stages

| Stage | Episode | Job | Viewer's question | Exit action |
|---|---|---|---|---|
| Awareness | EP 1 "Your business forgets." (~19 s) | Name the pain | "Is this happening to us?" | Watch to the end, follow for Part 2 |
| Education | EP 2 "What a second brain is." (~24 s) | Explain the offer in plain terms | "What would this actually do for us?" | Watch the flyer section, follow for Part 3 |
| Proof | EP 3 "The proof." (~39 s) | Show what is verified and what is not | "Can I trust them?" | Follow for Part 4 |
| Conversion | EP 4 "Book the call." (~22 s) | Ask for the free clarity call | "What do I do next?" | Click through to book |

Order of publication is 1 to 4, one per week, so each episode can point to the next. Viewers who skip ahead still get a complete message on their own.

## Metrics per stage

- **Awareness:** 3-second views, then 50% watched (EP 1). Target: 50% watched is the signal to show EP 2 as the next ad.
- **Education:** completion rate of EP 2 among EP 1 viewers.
- **Proof:** completion of EP 3, and the share of EP 3 viewers who go on to EP 4.
- **Conversion:** clicks on the CTA link, then booked calls. Track calls booked per 100 episode completions.

Record these weekly in one sheet. Don't count a view as a lead.

## Link and tracking

Every CTA uses a tagged link so each episode's results can be separated:

- EP 1: `https://blackswanlabz.com/?utm_source=facebook&utm_medium=video&utm_campaign=second-brain&utm_content=ep1`
- EP 2, 3, 4: same pattern with `ep2`, `ep3`, `ep4`.

Put the same tag on the Reels, Stories, and Facebook posts.

**Booking URL: not set.** The episode CTA points to `blackswanlabz.com`, which is the public site, not a booking page. Replace `url` in EP 4's CTA scene with the real booking link before publishing EP 4, and update the other CTAs' `url` to match.

## Cadence

- One episode per week, same day and time.
- Publish each to Facebook and Instagram Reels.
- Pin EP 1 to the top of the page.
- Keep the captions. Most viewers watch without sound.

## Retargeting (optional, needs a pixel)

- Viewers of 50% or more of EP 1 get EP 2 as the next ad.
- Viewers of 50% or more of EP 2 or EP 3 get EP 4.
- Viewers of EP 4's CTA who didn't book get a reminder.

Skip this until the Meta pixel is set up. Without it, the retargeting audiences can't be built.

## Claims policy

Every number in the series comes from the research lab's `CLAIMS.md` with a `verified` status, or from the flyer. Pending claims are labelled pending on screen. Before you add a claim, find its row in `CLAIMS.md`. If it isn't verified, leave it out or label it.

## Open items

1. **Booking URL** for EP 4 and the other CTAs (see above).
2. **Clarity-call promise:** EP 4 says "We say where it fits, and where it doesn't." Confirm you'll deliver that on every call, or edit the line.
3. **Meta pixel and events** for the retargeting above.
4. **Lead capture:** no email capture yet. A booking form or a simple "send me the guide" form would catch viewers who aren't ready to book.
5. **Voice:** the narration is a synthetic voice. If you record your own, replace the mp3s in `public/vo/` and keep the same filenames.

## Rebuild

```bash
CA_BUNDLE=/path/to/ca.crt python3 scripts/make_series.py   # voiceover and timelines
npx remotion render src/index.ts Ep1 out/ep1.mp4           # repeat for Ep2..Ep4
```
