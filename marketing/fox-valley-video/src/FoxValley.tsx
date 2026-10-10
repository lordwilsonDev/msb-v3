import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

// ---------------------------------------------------------------------------
// Format: Facebook feed, 4:5 vertical (1080x1350), 30 fps, ~28 s.
// ---------------------------------------------------------------------------
export const WIDTH = 1080;
export const HEIGHT = 1350;
export const FPS = 30;

const C = {
  bg: '#050608',
  gold: '#D4AF5A',
  silver: '#E6EAF0',
  muted: '#9AA3AE',
  ink: '#0b0d12',
};

const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';

type Scene = 'intro' | 'headline' | 'flyer' | 'pillars' | 'marquee' | 'cta' | 'outro';

const ORDER: Scene[] = ['intro', 'headline', 'flyer', 'pillars', 'marquee', 'cta', 'outro'];

const LENGTH: Record<Scene, number> = {
  intro: 75,
  headline: 120,
  flyer: 210,
  pillars: 135,
  marquee: 105,
  cta: 135,
  outro: 75,
};

const START = {} as Record<Scene, number>;
let cursor = 0;
for (const s of ORDER) {
  START[s] = cursor;
  cursor += LENGTH[s];
}
export const DURATION = cursor;

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
          width: 760,
          transform: `scale(${0.82 + 0.18 * s})`,
          opacity: s,
          mixBlendMode: 'screen',
        }}
      />
      <div
        style={{
          ...tag,
          position: 'absolute',
          bottom: 300,
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
const Word: React.FC<{text: string; delay: number; gold?: boolean}> = ({text, delay, gold}) => {
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

const Headline: React.FC = () => {
  const kicker = useRise(0, 20);
  const sub = useRise(64, 40);
  const lines: {words: string[]; gold?: boolean}[] = [
    {words: ['WE', 'BUILD', 'YOUR']},
    {words: ['BUSINESS', 'A']},
    {words: ['SECOND', 'BRAIN.'], gold: true},
  ];
  let i = 0;
  return (
    <AbsoluteFill style={{padding: '150px 90px', fontFamily: FONT}}>
      <div
        style={{
          ...kicker,
          color: C.gold,
          fontSize: 30,
          letterSpacing: 7,
          fontWeight: 600,
        }}
      >
        AI CONSULTING &amp; STRATEGY
      </div>
      <div
        style={{
          marginTop: 60,
          fontSize: 124,
          fontWeight: 900,
          lineHeight: 1,
          letterSpacing: -3,
        }}
      >
        {lines.map((line) => (
          <div key={line.words.join('-')} style={{whiteSpace: 'nowrap'}}>
            {line.words.map((w) => {
              const delay = 10 + i * 7;
              i++;
              return <Word key={w + i} text={w} delay={delay} gold={line.gold} />;
            })}
          </div>
        ))}
      </div>
      <div
        style={{
          ...sub,
          marginTop: 70,
          fontSize: 40,
          lineHeight: 1.4,
          color: C.muted,
          maxWidth: 820,
        }}
      >
        An AI system that captures everything your business knows — and gives it back the moment you
        need it.
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 3. Flyer: scrolling viewport over the full flyer
// ---------------------------------------------------------------------------
const VIEW_W = 920;
const VIEW_H = 840;
const VIEW_TOP = 400;
const VIEW_LEFT = (WIDTH - VIEW_W) / 2;
const FLYER_H = (VIEW_W * 1536) / 1024; // 1380
const SCROLL = FLYER_H - VIEW_H; // 480

const Flyer: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const head = useRise(0, 30);
  const sub = useRise(14, 30);
  const frameIn = useRise(10, 80);

  const progress = interpolate(frame, [24, 190], [0, 1], {
    ...clamp,
    easing: Easing.inOut(Easing.cubic),
  });
  const y = -SCROLL * progress;

  const thumbH = (VIEW_H * VIEW_H) / FLYER_H;
  const thumbTop = (VIEW_H - thumbH) * progress;

  const barIn = spring({frame: frame - 30, fps, config: {damping: 200}});

  return (
    <AbsoluteFill style={{fontFamily: FONT}}>
      <div style={{position: 'absolute', top: 100, left: 90, right: 90, ...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>
          NOT ANOTHER CHATBOT.
        </div>
        <div style={{color: C.silver, fontSize: 52, fontWeight: 800, marginTop: 14, lineHeight: 1.12}}>
          A living memory for your company.
        </div>
      </div>
      <div style={{position: 'absolute', top: 290, left: 90, right: 90, ...sub, color: C.muted, fontSize: 28, lineHeight: 1.35}}>
        Your processes. Your customers. Your decisions — searchable, instant, yours.
      </div>

      <div
        style={{
          position: 'absolute',
          top: VIEW_TOP,
          left: VIEW_LEFT,
          width: VIEW_W,
          height: VIEW_H,
          borderRadius: 34,
          overflow: 'hidden',
          border: `2px solid rgba(212,175,90,0.55)`,
          boxShadow: '0 40px 140px rgba(212,175,90,0.18), 0 20px 60px rgba(0,0,0,0.6)',
          background: C.ink,
          opacity: frameIn.opacity,
          transform: frameIn.transform,
        }}
      >
        <Img
          src={staticFile('flyer.png')}
          style={{width: VIEW_W, height: FLYER_H, display: 'block', transform: `translateY(${y}px)`}}
        />
      </div>

      {/* Scroll track */}
      <div
        style={{
          position: 'absolute',
          top: VIEW_TOP,
          left: VIEW_LEFT + VIEW_W + 22,
          width: 6,
          height: VIEW_H,
          borderRadius: 3,
          background: 'rgba(255,255,255,0.08)',
          opacity: barIn,
        }}
      >
        <div
          style={{
            position: 'absolute',
            top: thumbTop,
            width: 6,
            height: thumbH,
            borderRadius: 3,
            background: C.gold,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// 4. Pillars: cards that scroll up into place
// ---------------------------------------------------------------------------
const PILLARS = [
  {n: '01', title: 'Capture knowledge', body: "Nothing your team knows walks out the door."},
  {n: '02', title: 'Instant answers', body: 'Find any process, client detail, or decision in seconds.'},
  {n: '03', title: 'Smarter decisions', body: 'Your whole business context, working for you 24/7.'},
];

const Pillars: React.FC = () => {
  const head = useRise(0, 30);
  return (
    <AbsoluteFill style={{fontFamily: FONT, padding: '130px 80px'}}>
      <div style={{...head}}>
        <div style={{color: C.gold, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>WHAT CHANGES</div>
        <div style={{color: C.silver, fontSize: 80, fontWeight: 900, marginTop: 16, lineHeight: 1.05}}>
          Your business, remembered.
        </div>
      </div>
      <div style={{marginTop: 70, display: 'flex', flexDirection: 'column', gap: 30}}>
        {PILLARS.map((p, i) => (
          <PillarCard key={p.n} {...p} delay={14 + i * 22} />
        ))}
      </div>
    </AbsoluteFill>
  );
};

const PillarCard: React.FC<{n: string; title: string; body: string; delay: number}> = ({
  n,
  title,
  body,
  delay,
}) => {
  const r = useRise(delay, 320);
  return (
    <div
      style={{
        ...r,
        display: 'flex',
        alignItems: 'center',
        gap: 36,
        padding: '36px 48px',
        borderRadius: 28,
        background: 'linear-gradient(135deg, rgba(212,175,90,0.10), rgba(255,255,255,0.02))',
        border: '1.5px solid rgba(212,175,90,0.35)',
      }}
    >
      <div style={{color: C.gold, fontSize: 96, fontWeight: 900, minWidth: 130}}>{n}</div>
      <div>
        <div style={{color: C.silver, fontSize: 50, fontWeight: 800}}>{title}</div>
        <div style={{color: C.muted, fontSize: 34, marginTop: 10, lineHeight: 1.35}}>{body}</div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 5. Marquee: counter-scrolling industry rows
// ---------------------------------------------------------------------------
const INDUSTRIES = [
  'SMALL & MID-SIZED BUSINESSES',
  'MANUFACTURING & INDUSTRIAL',
  'CONSTRUCTION & SKILLED TRADES',
  'PROFESSIONAL SERVICES & MORE',
];

const Marquee: React.FC = () => {
  const frame = useCurrentFrame();
  const head = useRise(0, 30);
  const tail = useRise(50, 30);
  const row1 = interpolate(frame, [0, 105], [0, -1500], clamp);
  const row2 = interpolate(frame, [0, 105], [-1500, 0], clamp);
  const line = INDUSTRIES.join('   ◆   ') + '   ◆   ';

  const rowStyle: React.CSSProperties = {
    whiteSpace: 'nowrap',
    fontFamily: FONT,
    fontWeight: 900,
    fontSize: 84,
    letterSpacing: -1,
  };

  return (
    <AbsoluteFill style={{fontFamily: FONT}}>
      <div style={{position: 'absolute', top: 150, left: 80, right: 80, ...head}}>
        <div style={{color: C.silver, fontSize: 92, fontWeight: 900, lineHeight: 1.02}}>
          Built for local business.
        </div>
        <div style={{color: C.gold, fontSize: 40, marginTop: 22, fontWeight: 600}}>
          Wisconsin's AI partner — built for Fox Valley business.
        </div>
      </div>

      <div style={{position: 'absolute', top: 560, left: 0, width: WIDTH, overflow: 'hidden'}}>
        <div style={{...rowStyle, color: C.silver, transform: `translateX(${row1}px)`}}>
          {line.repeat(2)}
        </div>
      </div>
      <div style={{position: 'absolute', top: 700, left: 0, width: WIDTH, overflow: 'hidden'}}>
        <div
          style={{
            ...rowStyle,
            color: 'transparent',
            WebkitTextStroke: `2px ${C.gold}`,
            transform: `translateX(${row2}px)`,
          }}
        >
          {line.repeat(2)}
        </div>
      </div>

      <div
        style={{
          position: 'absolute',
          bottom: 170,
          left: 80,
          right: 80,
          textAlign: 'center',
          color: C.muted,
          fontSize: 38,
          letterSpacing: 6,
          ...tail,
        }}
      >
        STRATEGY · AUTOMATION · RESULTS
      </div>
    </AbsoluteFill>
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

  return (
    <AbsoluteFill style={{fontFamily: FONT, alignItems: 'center', paddingTop: 110}}>
      <div
        style={{
          width: 330,
          height: 330,
          borderRadius: '50%',
          padding: 7,
          background: `linear-gradient(135deg, ${C.gold}, #7a5c1e)`,
          transform: `scale(${0.6 + 0.4 * photo})`,
          opacity: photo,
          boxShadow: '0 30px 100px rgba(212,175,90,0.25)',
        }}
      >
        <Img
          src={staticFile('headshot.png')}
          style={{width: '100%', height: '100%', borderRadius: '50%', objectFit: 'cover', objectPosition: 'center 25%'}}
        />
      </div>

      <div style={{...kicker, marginTop: 56, color: C.gold, fontSize: 34, letterSpacing: 7, fontWeight: 700}}>
        NEW TO AI?
      </div>
      <div
        style={{
          ...head,
          marginTop: 18,
          color: C.silver,
          fontSize: 78,
          fontWeight: 900,
          lineHeight: 1.04,
          textAlign: 'center',
          padding: '0 70px',
        }}
      >
        Start with a FREE<br />15-minute clarity call.
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
          marginTop: 52,
          background: C.gold,
          color: C.ink,
          fontSize: 44,
          fontWeight: 900,
          padding: '34px 70px',
          borderRadius: 999,
          letterSpacing: 1,
          transform: `translateY(${(1 - Math.min(1, btn.opacity)) * 30}px) scale(${pulse})`,
          boxShadow: '0 20px 60px rgba(212,175,90,0.35)',
        }}
      >
        BOOK YOUR FREE 15-MIN CALL →
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
      <Img
        src={staticFile('logo.png')}
        style={{width: 640, opacity: s, transform: `scale(${0.94 + 0.06 * s})`, mixBlendMode: 'screen'}}
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
const SCENE_COMPONENTS: Record<Scene, React.FC> = {
  intro: Intro,
  headline: Headline,
  flyer: Flyer,
  pillars: Pillars,
  marquee: Marquee,
  cta: CTA,
  outro: Outro,
};

export const FoxValley: React.FC = () => {
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      <Background />
      {ORDER.map((scene) => {
        const Comp = SCENE_COMPONENTS[scene];
        return (
          <Sequence key={scene} from={START[scene]} durationInFrames={LENGTH[scene]}>
            <SceneFade dur={LENGTH[scene]}>
              <Comp />
            </SceneFade>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
