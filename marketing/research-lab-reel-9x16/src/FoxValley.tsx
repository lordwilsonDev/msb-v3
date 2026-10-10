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

type SceneName = 'intro' | 'hook' | 'cornerstone' | 'categories' | 'timeline' | 'built' | 'status' | 'cta' | 'outro';
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
    {words: ['INTELLIGENCE', 'IS']},
    {words: ['ABUNDANT.'], gold: true},
  ];
  let i = 0;
  return (
    <AbsoluteFill style={{padding: '0 90px', justifyContent: 'center', fontFamily: FONT}}>
      <div style={{...kicker, color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>
        AI CONSULTING &amp; STRATEGY
      </div>
      <div style={{marginTop: 50, fontSize: 96, fontWeight: 900, lineHeight: 1.02, letterSpacing: -2}}>
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
        Verification isn't. This lab is what one builder made of that idea.
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Shared: a stat with a count-up
// ---------------------------------------------------------------------------
const StatCard: React.FC<{
  to: number;
  decimals?: number;
  suffix?: string;
  label: string;
  delay: number;
}> = ({to, decimals = 0, suffix = '', label, delay}) => {
  const frame = useCurrentFrame();
  const r = useRise(delay, 40);
  const raw = interpolate(frame, [delay, delay + 60], [0, to], {...clamp, easing: Easing.out(Easing.cubic)});
  const shown = decimals ? raw.toFixed(decimals) : Math.round(raw).toString();
  return (
    <div
      style={{
        ...r,
        padding: '30px 40px',
        borderRadius: 28,
        background: 'linear-gradient(135deg, rgba(212,175,90,0.10), rgba(255,255,255,0.02))',
        border: '1.5px solid rgba(212,175,90,0.35)',
      }}
    >
      <div style={{color: C.gold, fontSize: 150, fontWeight: 900, lineHeight: 1}}>{shown + suffix}</div>
      <div style={{color: C.muted, fontSize: 32, marginTop: 14, lineHeight: 1.3}}>{label}</div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 3. Cornerstone: verified line counts (C-001, C-002)
// ---------------------------------------------------------------------------
const Cornerstone: React.FC = () => {
  const head = useRise(0, 30);
  const note = useRise(110, 30);
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '220px 80px'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>THE CORNERSTONE</div>
        <div style={{color: C.silver, fontSize: 84, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>
          One week. December 2025.
        </div>
      </div>
      <div style={{marginTop: 70, display: 'flex', flexDirection: 'column', gap: 30}}>
        <StatCard to={32.5} decimals={1} suffix="M" label="Lines committed in the week of 14 Dec 2025" delay={20} />
        <StatCard to={5.1} decimals={1} suffix="M" label="Of those are source code, outside vendored and build folders" delay={60} />
      </div>
      <div style={{...note, marginTop: 60, color: C.muted, fontSize: 32, lineHeight: 1.4}}>
        Lines committed is not lines written. The lab publishes the breakdown and the check behind each figure.
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 4. Categories: the 35 folders, scrolling
// ---------------------------------------------------------------------------
const CATEGORIES = [
  'Core operating systems', 'Autonomous intelligence', 'Orchestration & coordination', 'Memory & knowledge',
  'Graph & network', 'Decision & reasoning', 'Optimization', 'Learning & adaptation', 'Swarm & collective',
  'Ecosystem evolution', 'Sovereignty & security', 'Vy platform', 'NanoApex', 'Singularity & transcendence',
  'Love & meaning', 'Communication protocols', 'Compute fabric', 'Intelligence amplification',
  'Fractal scaling', 'State & environment', 'Semantic translation', 'Task & resource management',
  'Integration & harmonization', 'Code language', 'Pattern & creative', 'Evolution & improvement',
  'Safety & recovery', 'Testing & synthesis', 'Workflow blueprint', 'Mini-mind', 'Monitoring & metrics',
  'Gateways & APIs', 'Specialized engines', 'Documentation automation', 'Other projects',
];
const ROW_H = 64;
const LIST_VIEW_H = 1000;

const Categories: React.FC = () => {
  const frame = useCurrentFrame();
  const head = useRise(0, 30);
  const progress = interpolate(frame, [20, 130], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const range = CATEGORIES.length * ROW_H - LIST_VIEW_H;
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '200px 80px 0'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>35 CATEGORIES</div>
        <div style={{color: C.silver, fontSize: 84, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>
          From orchestration to safety.
        </div>
      </div>
      <div
        style={{
          position: 'absolute',
          top: 520,
          left: 80,
          right: 80,
          height: LIST_VIEW_H,
          overflow: 'hidden',
          WebkitMaskImage: 'linear-gradient(transparent, #000 12%, #000 88%, transparent)',
          maskImage: 'linear-gradient(transparent, #000 12%, #000 88%, transparent)',
        }}
      >
        <div style={{transform: `translateY(${-range * progress}px)`}}>
          {CATEGORIES.map((name, i) => (
            <div
              key={name}
              style={{
                height: ROW_H,
                display: 'flex',
                alignItems: 'center',
                gap: 30,
                borderBottom: '1px solid rgba(212,175,90,0.18)',
              }}
            >
              <span style={{color: C.gold, fontSize: 30, fontWeight: 700, width: 60}}>
                {String(i + 1).padStart(2, '0')}
              </span>
              <span style={{color: C.silver, fontSize: 40, fontWeight: 700}}>{name}</span>
            </div>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 5. Timeline: dates from CLAIMS.md (C-007 to C-012)
// ---------------------------------------------------------------------------
const TIMELINE = [
  {date: '17 Feb 2026', what: 'Small local models', who: 'Cohere Labs, Tiny Aya launch', folder: '30 · mini-mind'},
  {date: '3 Apr 2026', what: 'Agent orchestration', who: 'Microsoft Agent Framework 1.0 GA', folder: '03 · orchestration'},
  {date: '23 Apr 2026', what: 'Agent memory', who: 'Anthropic, Memory for Claude Managed Agents beta', folder: '04 · memory'},
  {date: '23 Jul 2026', what: 'AI safety law', who: 'AI Kill Switch Act (H.R. 9917) introduced', folder: '27 · safety'},
  {date: '28 Jul 2026', what: 'Protocols', who: 'Model Context Protocol specification release', folder: '16 · protocols'},
];

const Timeline: React.FC = () => {
  const head = useRise(0, 30);
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '200px 80px'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>PREDICTION TIMELINE</div>
        <div style={{color: C.silver, fontSize: 80, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>
          The folders came first.
        </div>
        <div style={{color: C.muted, fontSize: 32, marginTop: 16, lineHeight: 1.35}}>
          Each category was in the package by 18 Dec 2025. The industry moved after.
        </div>
      </div>
      <div style={{position: 'relative', marginTop: 70, paddingLeft: 60}}>
        <div style={{position: 'absolute', left: 9, top: 10, bottom: 10, width: 2, background: 'rgba(212,175,90,0.35)'}} />
        {TIMELINE.map((t, i) => (
          <TimelineRow key={t.date} {...t} delay={18 + i * 26} />
        ))}
      </div>
    </AbsoluteFill>
  );
};

const TimelineRow: React.FC<{date: string; what: string; who: string; folder: string; delay: number}> = ({
  date,
  what,
  who,
  folder,
  delay,
}) => {
  const frame = useCurrentFrame();
  const r = useRise(delay, 90);
  const dot = interpolate(frame, [delay, delay + 20], [0, 1], clamp);
  return (
    <div style={{...r, position: 'relative', marginBottom: 46}}>
      <div
        style={{
          position: 'absolute',
          left: -60,
          top: 8,
          width: 20,
          height: 20,
          borderRadius: 10,
          background: C.gold,
          transform: `scale(${dot})`,
          boxShadow: `0 0 ${24 * dot}px rgba(212,175,90,0.8)`,
        }}
      />
      <div style={{color: C.gold, fontSize: 28, fontWeight: 700, letterSpacing: 2}}>{date}</div>
      <div style={{color: C.silver, fontSize: 46, fontWeight: 800, marginTop: 6}}>{what}</div>
      <div style={{color: C.muted, fontSize: 30, marginTop: 6, lineHeight: 1.35}}>{who}</div>
      <div style={{color: C.gold, fontSize: 24, marginTop: 8, letterSpacing: 2, opacity: 0.8}}>
        PACKAGE FOLDER {folder.toUpperCase()}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Pill used for status tags
// ---------------------------------------------------------------------------
const Tag: React.FC<{text: string; tone: 'gold' | 'amber'}> = ({text, tone}) => {
  const col = tone === 'gold' ? C.gold : '#D98B3A';
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '8px 18px',
        borderRadius: 999,
        border: `1.5px solid ${col}`,
        color: col,
        fontSize: 22,
        fontWeight: 800,
        letterSpacing: 3,
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </span>
  );
};

// ---------------------------------------------------------------------------
// 6. Built: each row cites a verified claim (C-005, C-030, C-033)
// ---------------------------------------------------------------------------
const BUILT = [
  {name: 'MSB v3', line: 'A governed, local-first agent runtime. 3,942 tests collected at a pinned commit.', tag: 'BUILT'},
  {name: 'FCVE', line: 'Formal claim-verification engine. Two Lean patches sent upstream: one merged, one open.', tag: 'ARCHIVED'},
  {name: 'Adaptive Infrastructure', line: 'Reproduces its published numbers byte for byte. The repository is private.', tag: 'RUN'},
];

const Built: React.FC = () => {
  const head = useRise(0, 30);
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '200px 80px'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>WHAT IS BUILT</div>
        <div style={{color: C.silver, fontSize: 84, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>
          Checkable, row by row.
        </div>
      </div>
      <div style={{marginTop: 70, display: 'flex', flexDirection: 'column', gap: 30}}>
        {BUILT.map((b, i) => (
          <BuiltCard key={b.name} {...b} delay={20 + i * 24} />
        ))}
      </div>
    </AbsoluteFill>
  );
};

const BuiltCard: React.FC<{name: string; line: string; tag: string; delay: number}> = ({name, line, tag, delay}) => {
  const r = useRise(delay, 200);
  return (
    <div
      style={{
        ...r,
        padding: '40px 46px',
        borderRadius: 28,
        background: 'linear-gradient(135deg, rgba(212,175,90,0.10), rgba(255,255,255,0.02))',
        border: '1.5px solid rgba(212,175,90,0.35)',
      }}
    >
      <Tag text={tag} tone="gold" />
      <div style={{color: C.silver, fontSize: 58, fontWeight: 900, marginTop: 20}}>{name}</div>
      <div style={{color: C.muted, fontSize: 32, marginTop: 10, lineHeight: 1.4}}>{line}</div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 7. Status: what is not built or not run, quoted from the repo's own banners
// ---------------------------------------------------------------------------
const NOT_YET = [
  {name: 'Hermes12 benchmark', tag: 'NOT RUN', note: 'Whether it beats compute-matched baselines is untested.'},
  {name: 'CVT-1', tag: 'PRE-REGISTERED', note: 'Designed and pre-registered. Nothing on it is a result.'},
  {name: 'Mixture of Inversion Experts', tag: 'NOT RUN', note: 'The benchmark harness is validated on synthetic data only.'},
  {name: 'BlackSwanLabz OS', tag: 'NOT BUILT', note: 'A plan. Nothing has been tested.'},
];

const Status: React.FC = () => {
  const head = useRise(0, 30);
  const foot = useRise(110, 30);
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '200px 80px'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>WHAT IS NOT YET</div>
        <div style={{color: C.silver, fontSize: 84, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>
          Read the banners.
        </div>
      </div>
      <div style={{marginTop: 60, display: 'flex', flexDirection: 'column', gap: 26}}>
        {NOT_YET.map((n, i) => (
          <StatusRow key={n.name} {...n} delay={20 + i * 22} />
        ))}
      </div>
      <div style={{...foot, marginTop: 56, color: C.gold, fontSize: 36, fontWeight: 700, lineHeight: 1.35}}>
        Check the receipts on GitHub: github.com/lordwilsonDev/blackswanlabz-research-lab
      </div>
    </AbsoluteFill>
  );
};

const StatusRow: React.FC<{name: string; tag: string; note: string; delay: number}> = ({name, tag, note, delay}) => {
  const r = useRise(delay, 150);
  return (
    <div
      style={{
        ...r,
        padding: '30px 40px',
        borderRadius: 24,
        border: '1.5px solid rgba(217,139,58,0.45)',
        background: 'rgba(217,139,58,0.05)',
      }}
    >
      <Tag text={tag} tone="amber" />
      <div style={{color: C.silver, fontSize: 44, fontWeight: 800, marginTop: 14}}>{name}</div>
      <div style={{color: C.muted, fontSize: 30, marginTop: 8, lineHeight: 1.35}}>{note}</div>
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
  cornerstone: Cornerstone,
  categories: Categories,
  timeline: Timeline,
  built: Built,
  status: Status,
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
