import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import timeline from './timeline.json';

// ---------------------------------------------------------------------------
// Format: Facebook feed, 4:5 vertical (1080x1350), 30 fps.
// Scene starts/lengths and the voiceover come from timeline.json, which
// scripts/make_vo.py writes, so the picture always follows the narration.
// ---------------------------------------------------------------------------
export const WIDTH = 1080;
export const HEIGHT = 1920;
export const FPS = timeline.fps;
export const DURATION = timeline.durationInFrames;

const C = {
  bg: '#050608',
  gold: '#D4AF5A',
  goldHot: '#FFE7A3',
  silver: '#E6EAF0',
  muted: '#9AA3AE',
  ink: '#0b0d12',
};

const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';

type SceneName = 'intro' | 'hook' | 'creds' | 'stats' | 'cta' | 'outro';
const SCENE_STARTS = Object.fromEntries(timeline.scenes.map((s) => [s.name, s])) as unknown as Record<
  SceneName,
  {start: number; length: number}
>;

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// Spring-based rise-in: slides up from `distance` px and fades in.
const useRise = (delay: number, distance = 60) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - delay, fps, config: {damping: 200}});
  return {opacity: s, transform: `translateY(${(1 - s) * distance}px)`};
};

// Crossfade-through-black between scenes. Uses an overlay rather than group
// opacity so mix-blend-mode inside a scene keeps working.
const SceneFade: React.FC<{dur: number; children: React.ReactNode}> = ({dur, children}) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 14, dur - 14, dur], [1, 0, 0, 1], clamp);
  return (
    <AbsoluteFill>
      {children}
      <AbsoluteFill style={{background: C.bg, opacity: o}} />
    </AbsoluteFill>
  );
};

const Background: React.FC = () => {
  const frame = useCurrentFrame();
  const drift = interpolate(frame, [0, DURATION], [0, 1], clamp);
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(ellipse at ${50 + drift * 6}% ${30 + drift * 25}%, #1c1809 0%, ${C.bg} 55%, #000 100%)`,
      }}
    />
  );
};

// ---------------------------------------------------------------------------
// Lighting
// ---------------------------------------------------------------------------

// A bright comet of light that travels around any rounded rectangle.
// pathLength=100 lets the dash math work in percent of the perimeter.
const OrbitRect: React.FC<{
  w: number;
  h: number;
  r: number;
  inset?: number;
  speed: number; // percent of perimeter per frame
  comet?: number; // comet length, percent
  color?: string;
  id: string;
}> = ({w, h, r, inset = 0, speed, comet = 18, color = C.goldHot, id}) => {
  const frame = useCurrentFrame();
  const offset = -((frame * speed) % 100);
  const x = inset;
  const y = inset;
  const rw = w - inset * 2;
  const rh = h - inset * 2;
  return (
    <svg
      width={w}
      height={h}
      style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none'}}
    >
      <defs>
        <filter id={`${id}-glow`} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
      </defs>
      {/* faint static rim so the shape reads even between passes */}
      <rect x={x} y={y} width={rw} height={rh} rx={r} fill="none" stroke={C.gold} strokeOpacity={0.18} strokeWidth={2} />
      {/* soft bloom */}
      <rect
        x={x}
        y={y}
        width={rw}
        height={rh}
        rx={r}
        pathLength={100}
        fill="none"
        stroke={C.gold}
        strokeWidth={12}
        strokeLinecap="round"
        strokeDasharray={`${comet} ${100 - comet}`}
        strokeDashoffset={offset}
        filter={`url(#${id}-glow)`}
        opacity={0.9}
      />
      {/* hot core */}
      <rect
        x={x}
        y={y}
        width={rw}
        height={rh}
        rx={r}
        pathLength={100}
        fill="none"
        stroke={color}
        strokeWidth={3}
        strokeLinecap="round"
        strokeDasharray={`${comet} ${100 - comet}`}
        strokeDashoffset={offset}
      />
    </svg>
  );
};

// Light sheen that sweeps across a surface every `period` frames.
const Sheen: React.FC<{period: number; delay?: number; angle?: number; strength?: number}> = ({
  period,
  delay = 0,
  angle = 105,
  strength = 0.22,
}) => {
  const frame = useCurrentFrame();
  const local = (((frame - delay) % period) + period) % period;
  const p = interpolate(local, [0, period * 0.45], [0, 1], {
    ...clamp,
    easing: Easing.inOut(Easing.cubic),
  });
  const x = interpolate(p, [0, 1], [-120, 120]);
  return (
    <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none', mixBlendMode: 'screen'}}>
      <div
        style={{
          position: 'absolute',
          inset: '-20%',
          transform: `translateX(${x}%)`,
          background: `linear-gradient(${angle}deg, transparent 38%, rgba(255,231,163,${strength}) 50%, transparent 62%)`,
          opacity: p > 0 && p < 1 ? 1 : 0,
        }}
      />
    </AbsoluteFill>
  );
};

// Full-frame light that runs around the outer border of the video, with a
// slow edge-glow pulse inside it.
const FrameLight: React.FC = () => {
  const frame = useCurrentFrame();
  const pulse = 0.55 + 0.45 * Math.sin((frame / FPS) * Math.PI * 2 * 0.5);
  const inset = 14;
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          boxShadow: `inset 0 0 ${150 + 60 * pulse}px rgba(212,175,90,${0.10 + 0.10 * pulse})`,
        }}
      />
      <OrbitRect w={WIDTH} h={HEIGHT} r={40} inset={inset} speed={0.42} comet={14} id="frame" />
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Captions: karaoke-style, the spoken word is gold
// ---------------------------------------------------------------------------
type Word = {text: string; startMs: number; endMs: number};
type Caption = {words: {text: string; from: number; to: number}[]; from: number; to: number};

const buildCaptions = (line: (typeof timeline.voiceover)[number]): Caption[] => {
  const tokens = line.text.split(/\s+/);
  const timed: {text: string; from: number; to: number}[] = tokens.map((text, i) => {
    const w: Word | undefined = line.words[i];
    const from = line.startFrame + Math.round(((w ? w.startMs : 0) / 1000) * FPS);
    const to = line.startFrame + Math.round(((w ? w.endMs : 0) / 1000) * FPS);
    return {text, from, to};
  });
  // Chunk on sentence/phrase punctuation and at most 5 words per card.
  const chunks: Caption[] = [];
  let cur: Caption | null = null;
  timed.forEach((t) => {
    if (!cur) cur = {words: [], from: t.from, to: t.to};
    cur.words.push(t);
    cur.to = t.to;
    const breaks = /[.?!,]$/.test(t.text) || cur.words.length >= 5;
    if (breaks) {
      chunks.push(cur);
      cur = null;
    }
  });
  if (cur) chunks.push(cur);
  return chunks;
};

const CAPTIONS = timeline.voiceover.flatMap((line) =>
  buildCaptions(line as (typeof timeline.voiceover)[number]).map((c) => ({...c, file: line.file})),
);

const Captions: React.FC = () => {
  const frame = useCurrentFrame();
  const active = CAPTIONS.find((c) => frame >= c.from - 2 && frame < c.to + 8);
  if (!active) return null;
  const fadeIn = interpolate(frame, [active.from - 2, active.from + 4], [0, 1], clamp);
  const fadeOut = interpolate(frame, [active.to + 2, active.to + 8], [1, 0], clamp);
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', pointerEvents: 'none'}}>
      <div
        style={{
          marginBottom: 300,
          maxWidth: 940,
          padding: '20px 36px',
          borderRadius: 26,
          background: 'rgba(5,6,8,0.72)',
          border: '1.5px solid rgba(212,175,90,0.45)',
          boxShadow: '0 16px 50px rgba(0,0,0,0.6)',
          fontFamily: FONT,
          fontSize: 46,
          fontWeight: 800,
          lineHeight: 1.25,
          textAlign: 'center',
          opacity: fadeIn * fadeOut,
          transform: `translateY(${(1 - fadeIn) * 14}px)`,
        }}
      >
        {active.words.map((w, i) => {
          const on = frame >= w.from;
          return (
            <span
              key={`${w.text}-${i}`}
              style={{
                color: on ? C.gold : C.silver,
                textShadow: on ? '0 0 18px rgba(212,175,90,0.55)' : 'none',
                marginRight: 12,
                display: 'inline-block',
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 1. Intro: logo reveal
// ---------------------------------------------------------------------------
const Intro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - 4, fps, config: {damping: 18, stiffness: 90}});
  const glow = interpolate(frame, [0, 35, 75], [0, 1, 0.7], clamp);
  const tag = useRise(28, 30);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <div
        style={{
          position: 'absolute',
          width: 1000,
          height: 1000,
          borderRadius: '50%',
          background: `radial-gradient(circle, rgba(212,175,90,${0.32 * glow}) 0%, transparent 62%)`,
        }}
      />
      <Img
        src={staticFile('logo.png')}
        style={{
          width: 860,
          transform: `scale(${0.82 + 0.18 * s})`,
          opacity: s,
          mixBlendMode: 'screen',
        }}
      />
      <div
        style={{
          ...tag,
          position: 'absolute',
          bottom: 500,
          fontFamily: FONT,
          color: C.gold,
          fontSize: 34,
          letterSpacing: 10,
          fontWeight: 600,
        }}
      >
        WISCONSIN'S AI PARTNER
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 2. Headline: word-by-word rise
// ---------------------------------------------------------------------------
const Word_: React.FC<{text: string; delay: number; gold?: boolean}> = ({text, delay, gold}) => {
  const r = useRise(delay, 90);
  return (
    <span
      style={{
        display: 'inline-block',
        marginRight: 22,
        color: gold ? C.gold : C.silver,
        ...r,
      }}
    >
      {text}
    </span>
  );
};

const Hook: React.FC = () => {
  const kicker = useRise(0, 20);
  const sub = useRise(70, 40);
  const lines: {words: string[]; gold?: boolean}[] = [
    {words: ['PROOF,']},
    {words: ['NOT PROMISES.'], gold: true},
  ];
  let i = 0;
  return (
    <AbsoluteFill style={{padding: '0 90px', justifyContent: 'center', fontFamily: FONT}}>
      <div style={{...kicker, color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>
        AI CONSULTING &amp; STRATEGY
      </div>
      <div style={{marginTop: 50, fontSize: 100, fontWeight: 900, lineHeight: 1.02, letterSpacing: -2}}>
        {lines.map((line) => (
          <div key={line.words.join('-')} style={{whiteSpace: 'nowrap'}}>
            {line.words.map((w) => {
              const delay = 10 + i * 8;
              i++;
              return <Word_ key={`${w}-${i}`} text={w} delay={delay} gold={line.gold} />;
            })}
          </div>
        ))}
      </div>
      <div style={{...sub, marginTop: 70, fontSize: 44, lineHeight: 1.4, color: C.muted, maxWidth: 860}}>
        Trained in AI. Building in public. Here's what's behind Black Swan Labz.
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 3. Credentials: certificate names as a scrolling list (no IDs)
// ---------------------------------------------------------------------------
const CREDS = [
  {org: 'ANTHROPIC', items: [
    {name: 'Claude Academy: AI Fluency — Framework & Foundations', id: '1f519d58f5830d11b01004f75d8b0cf8'},
    {name: 'Claude Academy: AI Fluency for Educators', id: '3cfe8644c91f7c45e183b531ba89e24e'},
    {name: 'AI Fluency for Small Businesses'},
    {name: 'Claude 101', id: 'b93jetbu8x86'},
  ]},
  {org: 'OPENAI', items: [
    {name: 'Applied AI Foundations', id: 'x9y4t1txt6'},
    {name: 'Agents and Workflows', id: 'o7k2nywu68'},
    {name: 'AI Foundations', id: '6jfsibsk19'},
  ]},
];

const Creds: React.FC = () => {
  const head = useRise(0, 30);
  let k = 0;
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '200px 80px'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>CREDENTIALS</div>
        <div style={{color: C.silver, fontSize: 92, fontWeight: 900, marginTop: 16, lineHeight: 1.04}}>
          Trained, not guessing.
        </div>
      </div>
      <div style={{marginTop: 50}}>
        {CREDS.map((group) => (
          <div key={group.org} style={{marginBottom: 48}}>
            <div style={{color: C.gold, fontSize: 28, letterSpacing: 6, fontWeight: 700, marginBottom: 22}}>
              {group.org}
            </div>
            {group.items.map((item: {name: string; id?: string}) => {
              const delay = 18 + k * 12;
              k++;
              return <CredRow key={item.name} text={item.name} id={item.id} delay={delay} />;
            })}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const CredRow: React.FC<{text: string; id?: string; delay: number}> = ({text, id, delay}) => {
  const frame = useCurrentFrame();
  const r = useRise(delay, 120);
  const bar = interpolate(frame, [delay, delay + 24], [0, 1], clamp);
  return (
    <div style={{...r, display: 'flex', alignItems: 'center', gap: 28, marginBottom: 22}}>
      <div style={{width: 8, height: 80, borderRadius: 4, background: C.gold, transform: `scaleY(${bar})`, boxShadow: `0 0 ${20 * bar}px rgba(212,175,90,0.6)`}} />
      <div>
        <div style={{color: C.silver, fontSize: 40, fontWeight: 800, lineHeight: 1.2}}>{text}</div>
        {id && (
          <div style={{color: C.muted, fontSize: 24, marginTop: 6, fontFamily: 'Menlo, Consolas, monospace', letterSpacing: 0.5}}>
            Credential ID {id}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 4. Stats: counters plus the Pitchhut page scrolling past
// ---------------------------------------------------------------------------
const COUNTERS = [
  {to: 32.5, decimals: 1, suffix: 'M', label: 'Lines of code added in one week'},
  {to: 35, label: 'AI project categories'},
  {to: 274, label: 'Referral views from GitHub & Hacker News'},
  {to: 64, label: 'Showcase page views'},
];

const Stats: React.FC = () => {
  const head = useRise(0, 30);
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '200px 80px 0'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>THE PROOF</div>
        <div style={{color: C.silver, fontSize: 84, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>
          Real projects. Real attention.
        </div>
      </div>
      <div style={{marginTop: 56, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24}}>
        {COUNTERS.map((c, i) => (
          <CounterCard key={c.label} to={c.to} decimals={c.decimals} suffix={c.suffix} label={c.label} delay={20 + i * 12} />
        ))}
      </div>
      <PhoneScroll />
    </AbsoluteFill>
  );
};

const CounterCard: React.FC<{
  to: number;
  decimals?: number;
  suffix?: string;
  label: string;
  delay: number;
}> = ({to, decimals = 0, suffix = '', label, delay}) => {
  const frame = useCurrentFrame();
  const r = useRise(delay, 40);
  const raw = interpolate(frame, [delay, delay + 50], [0, to], {...clamp, easing: Easing.out(Easing.cubic)});
  const shown = decimals ? raw.toFixed(decimals) : Math.round(raw).toString();
  return (
    <div
      style={{
        ...r,
        padding: '26px 30px',
        borderRadius: 26,
        background: 'linear-gradient(135deg, rgba(212,175,90,0.10), rgba(255,255,255,0.02))',
        border: '1.5px solid rgba(212,175,90,0.35)',
      }}
    >
      <div style={{color: C.gold, fontSize: 84, fontWeight: 900, lineHeight: 1}}>{shown + suffix}</div>
      <div style={{color: C.muted, fontSize: 26, marginTop: 10, lineHeight: 1.3}}>{label}</div>
    </div>
  );
};

const PHONE_W = 600;
const PHONE_H = 500;
const PHONE_IMG_H = (PHONE_W * 2340) / 1080; // 1300
const PhoneScroll: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = interpolate(frame, [110, 260], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const y = -(PHONE_IMG_H - PHONE_H) * progress;
  const thumbH = (PHONE_H * PHONE_H) / PHONE_IMG_H;
  const thumbTop = (PHONE_H - thumbH) * progress;
  const frameIn = useRise(90, 80);
  const barIn = spring({frame: frame - 100, fps, config: {damping: 200}});
  return (
    <div style={{position: 'absolute', top: 965, left: (WIDTH - PHONE_W) / 2, width: PHONE_W, height: PHONE_H, opacity: frameIn.opacity, transform: frameIn.transform}}>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: 34,
          overflow: 'hidden',
          border: '2px solid rgba(212,175,90,0.55)',
          boxShadow: '0 40px 120px rgba(212,175,90,0.18), 0 20px 60px rgba(0,0,0,0.6)',
          background: C.ink,
        }}
      >
        <Img src={staticFile('proof.jpg')} style={{width: PHONE_W, height: PHONE_IMG_H, display: 'block', transform: `translateY(${y}px)`}} />
        <Sheen period={160} delay={60} strength={0.14} />
      </div>
      <OrbitRect w={PHONE_W} h={PHONE_H} r={34} inset={1} speed={0.9} comet={12} id="proof" />
      <div style={{position: 'absolute', top: 0, left: PHONE_W + 22, width: 6, height: PHONE_H, borderRadius: 3, background: 'rgba(255,255,255,0.08)', opacity: barIn}}>
        <div style={{position: 'absolute', top: thumbTop, width: 6, height: thumbH, borderRadius: 3, background: C.gold, boxShadow: '0 0 14px rgba(212,175,90,0.8)'}} />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 6. CTA: headshot + free clarity call
// ---------------------------------------------------------------------------
const CTA: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const photo = spring({frame: frame - 2, fps, config: {damping: 16, stiffness: 110}});
  const kicker = useRise(16, 30);
  const head = useRise(24, 50);
  const body = useRise(48, 30);
  const btn = useRise(66, 30);
  const url = useRise(88, 20);
  const pulse = 1 + 0.012 * Math.sin((frame / fps) * Math.PI * 2 * 1.2) * (frame > 90 ? 1 : 0);
  const RING = 330;

  return (
    <AbsoluteFill style={{fontFamily: FONT, alignItems: 'center', paddingTop: 230}}>
      <div
        style={{
          position: 'relative',
          width: RING,
          height: RING,
          transform: `scale(${0.6 + 0.4 * photo})`,
          opacity: photo,
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            padding: 7,
            background: `linear-gradient(135deg, ${C.gold}, #7a5c1e)`,
            boxShadow: '0 30px 100px rgba(212,175,90,0.25)',
          }}
        >
          <Img
            src={staticFile('headshot.png')}
            style={{width: '100%', height: '100%', borderRadius: '50%', objectFit: 'cover', objectPosition: 'center 25%'}}
          />
        </div>
        <svg width={RING} height={RING} style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
          <defs>
            <filter id="ring-glow" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="8" />
            </filter>
          </defs>
          <circle
            cx={RING / 2}
            cy={RING / 2}
            r={RING / 2 + 6}
            pathLength={100}
            fill="none"
            stroke={C.gold}
            strokeWidth={14}
            strokeLinecap="round"
            strokeDasharray="20 80"
            strokeDashoffset={-((frame * 0.8) % 100)}
            filter="url(#ring-glow)"
            opacity={0.85}
          />
          <circle
            cx={RING / 2}
            cy={RING / 2}
            r={RING / 2 + 6}
            pathLength={100}
            fill="none"
            stroke={C.goldHot}
            strokeWidth={3}
            strokeLinecap="round"
            strokeDasharray="20 80"
            strokeDashoffset={-((frame * 0.8) % 100)}
          />
        </svg>
      </div>

      <div style={{...kicker, marginTop: 56, color: C.gold, fontSize: 34, letterSpacing: 7, fontWeight: 700}}>
        NEW TO AI?
      </div>
      <div
        style={{
          ...head,
          marginTop: 18,
          color: C.silver,
          fontSize: 86,
          fontWeight: 900,
          lineHeight: 1.04,
          textAlign: 'center',
          padding: '0 70px',
        }}
      >
        Start with a FREE
        <br />
        15-minute clarity call.
      </div>
      <div
        style={{
          ...body,
          marginTop: 28,
          color: C.muted,
          fontSize: 36,
          lineHeight: 1.4,
          textAlign: 'center',
          maxWidth: 840,
        }}
      >
        No slide deck. No obligation. We'll show you where a second brain fits your business.
      </div>
      <div
        style={{
          ...btn,
          position: 'relative',
          marginTop: 52,
          background: C.gold,
          color: C.ink,
          fontSize: 44,
          fontWeight: 900,
          padding: '34px 70px',
          borderRadius: 999,
          letterSpacing: 1,
          overflow: 'hidden',
          transform: `translateY(${(1 - Math.min(1, btn.opacity)) * 30}px) scale(${pulse})`,
          boxShadow: '0 20px 60px rgba(212,175,90,0.35), 0 0 0 1px rgba(255,231,163,0.6)',
        }}
      >
        BOOK YOUR FREE 15-MIN CALL →
        <Sheen period={120} delay={100} strength={0.5} angle={100} />
      </div>
      <div style={{...url, marginTop: 44, color: C.gold, fontSize: 36, letterSpacing: 5, fontWeight: 700}}>
        BLACKSWANLABZ.COM
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 7. Outro: logo lockup
// ---------------------------------------------------------------------------
const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 200}});
  const url = useRise(14, 20);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: FONT}}>
      <div
        style={{
          position: 'absolute',
          width: 900,
          height: 900,
          borderRadius: '50%',
          background: `radial-gradient(circle, rgba(212,175,90,${0.28 * s}) 0%, transparent 60%)`,
        }}
      />
      <Img
        src={staticFile('logo.png')}
        style={{width: 820, opacity: s, transform: `scale(${0.94 + 0.06 * s})`, mixBlendMode: 'screen'}}
      />
      <div style={{...url, marginTop: 40, color: C.gold, fontSize: 40, letterSpacing: 6, fontWeight: 700}}>
        BLACKSWANLABZ.COM
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Composition
// ---------------------------------------------------------------------------
const SCENE_COMPONENTS: Record<SceneName, React.FC> = {
  intro: Intro,
  hook: Hook,
  creds: Creds,
  stats: Stats,
  cta: CTA,
  outro: Outro,
};

export const FoxValley: React.FC = () => {
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      <Background />
      {(Object.keys(SCENE_COMPONENTS) as SceneName[]).map((name) => {
        const Comp = SCENE_COMPONENTS[name];
        const {start, length} = SCENE_STARTS[name];
        return (
          <Sequence key={name} from={start} durationInFrames={length}>
            <SceneFade dur={length}>
              <Comp />
            </SceneFade>
          </Sequence>
        );
      })}

      {timeline.voiceover.map((line) => (
        <Sequence key={line.id} from={line.startFrame} durationInFrames={line.durationFrames + 6}>
          <Audio src={staticFile(line.file)} volume={1} />
        </Sequence>
      ))}

      <FrameLight />
      <Captions />
    </AbsoluteFill>
  );
};
