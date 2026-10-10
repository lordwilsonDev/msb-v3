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

// 1080x1920 vertical, 30 fps, 30 s. Silver accents throughout.
export const WIDTH = 1080;
export const HEIGHT = 1920;
export const FPS = 30;

const S = {
  bg: '#050607',
  silver: '#D9DEE5',
  silverHot: '#FFFFFF',
  steel: '#8E96A3',
  muted: '#9AA3AE',
  ink: '#0b0d10',
};
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// [name, start, length]
const SCENES = [
  ['logo', 0, 90],
  ['collage', 90, 150],
  ['flyer', 240, 150],
  ['portrait', 390, 120],
  ['carousel', 510, 150],
  ['stats', 660, 120],
  ['outro', 780, 120],
] as const;
export const DURATION = 900;

const useRise = (delay: number, distance = 60) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - delay, fps, config: {damping: 200}});
  return {opacity: s, transform: `translateY(${(1 - s) * distance}px)`};
};

const SceneFade: React.FC<{dur: number; children: React.ReactNode}> = ({dur, children}) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 12, dur - 12, dur], [1, 0, 0, 1], clamp);
  return (
    <AbsoluteFill>
      {children}
      <AbsoluteFill style={{background: S.bg, opacity: o}} />
    </AbsoluteFill>
  );
};

// Silver comet of light travelling around a rounded rectangle.
const OrbitRect: React.FC<{w: number; h: number; r: number; speed: number; comet?: number; id: string}> = ({
  w,
  h,
  r,
  speed,
  comet = 14,
  id,
}) => {
  const frame = useCurrentFrame();
  const offset = -((frame * speed) % 100);
  return (
    <svg width={w} height={h} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none'}}>
      <defs>
        <filter id={`${id}-glow`} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
      </defs>
      <rect x={1} y={1} width={w - 2} height={h - 2} rx={r} fill="none" stroke={S.silver} strokeOpacity={0.18} strokeWidth={2} />
      <rect x={1} y={1} width={w - 2} height={h - 2} rx={r} pathLength={100} fill="none" stroke={S.silver} strokeWidth={12}
        strokeLinecap="round" strokeDasharray={`${comet} ${100 - comet}`} strokeDashoffset={offset} filter={`url(#${id}-glow)`} opacity={0.85} />
      <rect x={1} y={1} width={w - 2} height={h - 2} rx={r} pathLength={100} fill="none" stroke={S.silverHot} strokeWidth={3}
        strokeLinecap="round" strokeDasharray={`${comet} ${100 - comet}`} strokeDashoffset={offset} />
    </svg>
  );
};

const FrameLight: React.FC = () => {
  const frame = useCurrentFrame();
  const pulse = 0.55 + 0.45 * Math.sin((frame / FPS) * Math.PI);
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div style={{position: 'absolute', inset: 0, boxShadow: `inset 0 0 ${160 + 60 * pulse}px rgba(217,222,229,${0.08 + 0.08 * pulse})`}} />
      <OrbitRect w={WIDTH} h={HEIGHT} r={40} speed={0.4} comet={12} id="frame" />
    </AbsoluteFill>
  );
};

// Light sweep across any surface.
const Sheen: React.FC<{period: number; delay?: number}> = ({period, delay = 0}) => {
  const frame = useCurrentFrame();
  const local = (((frame - delay) % period) + period) % period;
  const p = interpolate(local, [0, period * 0.45], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const x = interpolate(p, [0, 1], [-120, 120]);
  return (
    <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none', mixBlendMode: 'screen'}}>
      <div
        style={{
          position: 'absolute',
          inset: '-20%',
          transform: `translateX(${x}%)`,
          background: 'linear-gradient(105deg, transparent 38%, rgba(255,255,255,0.22) 50%, transparent 62%)',
          opacity: p > 0 && p < 1 ? 1 : 0,
        }}
      />
    </AbsoluteFill>
  );
};

// Captions: short silver lines that rise in, one per scene.
const Caption: React.FC<{text: string; top?: number; bottom?: number; delay?: number}> = ({text, top, bottom = 260, delay = 10}) => {
  const r = useRise(delay, 30);
  return (
    <div
      style={{
        ...r,
        position: 'absolute',
        top,
        bottom,
        left: 0,
        right: 0,
        textAlign: 'center',
        fontFamily: FONT,
        color: S.silver,
        fontSize: 44,
        fontWeight: 800,
        letterSpacing: 6,
        textTransform: 'uppercase',
        textShadow: '0 0 24px rgba(217,222,229,0.4)',
      }}
    >
      {text}
    </div>
  );
};

// ---------------------------------------------------------------- scenes
const Logo: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - 4, fps, config: {damping: 18, stiffness: 90}});
  const glow = interpolate(frame, [0, 40, 90], [0, 1, 0.6], clamp);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <div style={{position: 'absolute', width: 1000, height: 1000, borderRadius: '50%',
        background: `radial-gradient(circle, rgba(217,222,229,${0.28 * glow}) 0%, transparent 62%)`}} />
      <Img src={staticFile('logo.png')} style={{width: 820, opacity: s, transform: `scale(${0.82 + 0.18 * s})`, mixBlendMode: 'screen'}} />
      <Caption text="Intelligence is abundant. Verification isn't." top={1400} delay={34} />
    </AbsoluteFill>
  );
};

// Eight tiles from the chat, rising in and drifting upward like a feed.
const TILES = [
  {src: 'flyer.png', w: 500, h: 760},
  {src: 'cert-a.jpg', w: 500, h: 760},
  {src: 'code.jpg', w: 500, h: 700},
  {src: 'cert-b.jpg', w: 500, h: 760},
  {src: 'referral.jpg', w: 500, h: 700},
  {src: 'pitchhut.jpg', w: 500, h: 760},
  {src: 'headshot.png', w: 500, h: 700},
  {src: 'logo.png', w: 500, h: 700},
];

const Collage: React.FC = () => {
  const frame = useCurrentFrame();
  const drift = interpolate(frame, [0, 150], [0, -360], {...clamp, easing: Easing.inOut(Easing.cubic)});
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <Caption text="Everything we've built" top={150} delay={0} />
      <div
        style={{
          position: 'absolute',
          top: 300,
          left: 40,
          width: 1000,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 20,
          transform: `translateY(${drift}px)`,
        }}
      >
        {TILES.map((t, i) => {
          const r = useRise(6 + i * 5, 260);
          return (
            <div
              key={t.src}
              style={{
                ...r,
                height: t.h,
                borderRadius: 26,
                overflow: 'hidden',
                border: '1.5px solid rgba(217,222,229,0.45)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.6)',
                position: 'relative',
                background: S.ink,
              }}
            >
              <Img src={staticFile(t.src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
              <Sheen period={120} delay={i * 14} />
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

const PHONE_W = 920;
const PHONE_H = 1000;
const FLYER_H = (PHONE_W * 1536) / 1024;
const Flyer: React.FC = () => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [20, 135], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const y = -(FLYER_H - PHONE_H) * p;
  const frameIn = useRise(4, 80);
  return (
    <AbsoluteFill>
      <Caption text="Not another chatbot" top={170} delay={0} />
      <div style={{position: 'absolute', top: 300, left: (WIDTH - PHONE_W) / 2, width: PHONE_W, height: PHONE_H, opacity: frameIn.opacity, transform: frameIn.transform}}>
        <div style={{position: 'absolute', inset: 0, borderRadius: 34, overflow: 'hidden', border: '2px solid rgba(217,222,229,0.6)',
          boxShadow: '0 40px 140px rgba(217,222,229,0.16)', background: S.ink}}>
          <Img src={staticFile('flyer.png')} style={{width: PHONE_W, height: FLYER_H, display: 'block', transform: `translateY(${y}px)`}} />
          <Sheen period={150} delay={30} />
        </div>
        <OrbitRect w={PHONE_W} h={PHONE_H} r={34} speed={0.9} comet={12} id="flyer" />
      </div>
      <Caption text="Your second brain" bottom={300} delay={24} />
    </AbsoluteFill>
  );
};

const Portrait: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame: frame - 2, fps, config: {damping: 16, stiffness: 110}});
  const RING = 560;
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <div style={{position: 'relative', width: RING, height: RING, transform: `scale(${0.6 + 0.4 * p})`, opacity: p}}>
        <div style={{position: 'absolute', inset: 0, borderRadius: '50%', padding: 8,
          background: `linear-gradient(135deg, ${S.silverHot}, ${S.steel} 50%, ${S.silver})`,
          boxShadow: '0 30px 120px rgba(217,222,229,0.25)'}}>
          <Img src={staticFile('headshot.png')} style={{width: '100%', height: '100%', borderRadius: '50%', objectFit: 'cover', objectPosition: 'center 25%'}} />
        </div>
        <svg width={RING} height={RING} style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
          <circle cx={RING / 2} cy={RING / 2} r={RING / 2 + 14} pathLength={100} fill="none" stroke={S.silver} strokeWidth={12}
            strokeLinecap="round" strokeDasharray="18 82" strokeDashoffset={-((frame * 0.9) % 100)} opacity={0.8} />
          <circle cx={RING / 2} cy={RING / 2} r={RING / 2 + 14} pathLength={100} fill="none" stroke={S.silverHot} strokeWidth={3}
            strokeLinecap="round" strokeDasharray="18 82" strokeDashoffset={-((frame * 0.9) % 100)} />
        </svg>
      </div>
      <Caption text="Lord Wilson" top={1340} delay={18} />
    </AbsoluteFill>
  );
};

// Certificate and Pitchhut screenshots sliding past as phone cards.
const CARDS = ['cert-a.jpg', 'pitchhut.jpg', 'cert-b.jpg'];
const CARD_W = 560;
const CARD_H = 1000;
const GAP = 60;
const Carousel: React.FC = () => {
  const frame = useCurrentFrame();
  const span = (CARD_W + GAP) * CARDS.length;
  const x = interpolate(frame, [0, 150], [WIDTH / 2 - CARD_W / 2, WIDTH / 2 - CARD_W / 2 - span + (CARD_W + GAP)], {
    ...clamp,
    easing: Easing.inOut(Easing.cubic),
  });
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <Caption text="Trained. Building. Shared." top={170} delay={0} />
      <div style={{position: 'absolute', top: 480, left: 0, height: CARD_H, width: WIDTH + span}}>
        {CARDS.map((src, i) => {
          const cx = x + i * (CARD_W + GAP);
          const dist = Math.abs(cx - (WIDTH / 2 - CARD_W / 2));
          const scale = interpolate(dist, [0, 600], [1.05, 0.9], clamp);
          return (
            <div
              key={src}
              style={{
                position: 'absolute',
                left: cx,
                top: 0,
                width: CARD_W,
                height: CARD_H,
                transform: `scale(${scale})`,
                borderRadius: 36,
                overflow: 'hidden',
                border: '2px solid rgba(217,222,229,0.55)',
                boxShadow: '0 40px 120px rgba(0,0,0,0.7)',
                background: S.ink,
              }}
            >
              <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
              <Sheen period={130} delay={i * 30} />
            </div>
          );
        })}
      </div>
      <Caption text="Claude Academy · OpenAI" bottom={330} delay={20} />
    </AbsoluteFill>
  );
};

const Stats: React.FC = () => {
  const frame = useCurrentFrame();
  const count = Math.round(interpolate(frame, [10, 70], [0, 32.5], {...clamp, easing: Easing.out(Easing.cubic)}) * 10) / 10;
  const a = useRise(0, 120);
  const b = useRise(20, 120);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: FONT}}>
      <div style={{...a, color: S.silverHot, fontSize: 210, fontWeight: 900, lineHeight: 1, textShadow: '0 0 60px rgba(217,222,229,0.35)'}}>
        {count.toFixed(1)}M
      </div>
      <div style={{...a, color: S.silver, fontSize: 40, letterSpacing: 6, marginTop: 10, textTransform: 'uppercase'}}>
        lines committed in one week
      </div>
      <div style={{...b, marginTop: 70, display: 'flex', gap: 30}}>
        <Img src={staticFile('referral.jpg')} style={{width: 420, height: 520, objectFit: 'cover', borderRadius: 28, border: '1.5px solid rgba(217,222,229,0.5)'}} />
        <Img src={staticFile('code.jpg')} style={{width: 420, height: 520, objectFit: 'cover', borderRadius: 28, border: '1.5px solid rgba(217,222,229,0.5)'}} />
      </div>
    </AbsoluteFill>
  );
};

const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 200}});
  const url = useRise(16, 20);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: FONT}}>
      <Img src={staticFile('logo.png')} style={{width: 700, opacity: s, transform: `scale(${0.94 + 0.06 * s})`, mixBlendMode: 'screen'}} />
      <div style={{...url, marginTop: 40, color: S.silver, fontSize: 40, letterSpacing: 6, fontWeight: 700}}>BLACKSWANLABZ.COM</div>
    </AbsoluteFill>
  );
};

const COMPONENTS: Record<string, React.FC> = {
  logo: Logo, collage: Collage, flyer: Flyer, portrait: Portrait, carousel: Carousel, stats: Stats, outro: Outro,
};

export const Montage: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: S.bg}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 35%, #15171b 0%, ${S.bg} 60%, #000 100%)`}} />
    {SCENES.map(([name, start, length]) => {
      const C = COMPONENTS[name];
      return (
        <Sequence key={name} from={start} durationInFrames={length}>
          <SceneFade dur={length}>
            <C />
          </SceneFade>
        </Sequence>
      );
    })}
    <FrameLight />
  </AbsoluteFill>
);
