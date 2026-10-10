import React from 'react';
import {AbsoluteFill, Audio, Easing, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// Data-driven episode renderer. Scenes come from timelines/<ep>.json (see scripts/make_series.py).
export const WIDTH = 1080;
export const HEIGHT = 1920;

const S = {bg: '#050607', silver: '#D9DEE5', silverHot: '#FFFFFF', steel: '#8E96A3', muted: '#9AA3AE', ink: '#0b0d10'};
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

type Item = {t: string; b: string};
type Scene = {
  type: 'title' | 'cards' | 'image' | 'stat' | 'cta' | 'logo';
  start: number;
  length: number;
  kicker?: string;
  lines?: string[];
  sub?: string;
  heading?: string;
  items?: Item[];
  image?: string;
  value?: number;
  decimals?: number;
  suffix?: string;
  label?: string;
  headline?: string;
  body?: string;
  button?: string;
  url?: string;
  headshot?: boolean;
};
type Word = {text: string; startMs: number; endMs: number};
type Line = {id: string; startFrame: number; durationFrames: number; file: string; text: string; words: Word[]};
export type EpisodeData = {id: string; title: string; funnelStage: string; fps: number; durationInFrames: number; scenes: Scene[]; voiceover: Line[]};

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

const OrbitRect: React.FC<{w: number; h: number; r: number; speed: number; id: string}> = ({w, h, r, speed, id}) => {
  const frame = useCurrentFrame();
  const offset = -((frame * speed) % 100);
  return (
    <svg width={w} height={h} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none'}}>
      <defs>
        <filter id={`${id}-glow`} x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="9" /></filter>
      </defs>
      <rect x={1} y={1} width={w - 2} height={h - 2} rx={r} fill="none" stroke={S.silver} strokeOpacity={0.18} strokeWidth={2} />
      <rect x={1} y={1} width={w - 2} height={h - 2} rx={r} pathLength={100} fill="none" stroke={S.silver} strokeWidth={12}
        strokeLinecap="round" strokeDasharray="12 88" strokeDashoffset={offset} filter={`url(#${id}-glow)`} opacity={0.85} />
      <rect x={1} y={1} width={w - 2} height={h - 2} rx={r} pathLength={100} fill="none" stroke={S.silverHot} strokeWidth={3}
        strokeLinecap="round" strokeDasharray="12 88" strokeDashoffset={offset} />
    </svg>
  );
};

const FrameLight: React.FC = () => {
  const frame = useCurrentFrame();
  const pulse = 0.55 + 0.45 * Math.sin((frame / 30) * Math.PI);
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div style={{position: 'absolute', inset: 0, boxShadow: `inset 0 0 ${160 + 60 * pulse}px rgba(217,222,229,${0.08 + 0.08 * pulse})`}} />
      <OrbitRect w={WIDTH} h={HEIGHT} r={40} speed={0.4} id="frame" />
    </AbsoluteFill>
  );
};

const Sheen: React.FC<{period: number; delay?: number}> = ({period, delay = 0}) => {
  const frame = useCurrentFrame();
  const local = (((frame - delay) % period) + period) % period;
  const p = interpolate(local, [0, period * 0.45], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const x = interpolate(p, [0, 1], [-120, 120]);
  return (
    <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none', mixBlendMode: 'screen'}}>
      <div style={{position: 'absolute', inset: '-20%', transform: `translateX(${x}%)`,
        background: 'linear-gradient(105deg, transparent 38%, rgba(255,255,255,0.22) 50%, transparent 62%)', opacity: p > 0 && p < 1 ? 1 : 0}} />
    </AbsoluteFill>
  );
};

const Kicker: React.FC<{text?: string; top: number}> = ({text, top}) => {
  const r = useRise(0, 20);
  if (!text) return null;
  return <div style={{...r, position: 'absolute', top, left: 90, right: 90, color: S.silver, fontFamily: FONT, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>{text}</div>;
};

// Title: big stacked headline
const TitleScene: React.FC<{s: Scene}> = ({s}) => {
  const sub = useRise(60, 40);
  let i = 0;
  return (
    <AbsoluteFill style={{padding: '0 90px', justifyContent: 'center', fontFamily: FONT}}>
      <Kicker text={s.kicker} top={260} />
      <div style={{marginTop: 40, fontSize: 116, fontWeight: 900, lineHeight: 1.02, letterSpacing: -2, color: S.silver}}>
        {(s.lines ?? []).map((line) => (
          <div key={line} style={{whiteSpace: 'nowrap'}}>
            {line.split(' ').map((w) => {
              const r = useRise(8 + i * 7, 90);
              i++;
              return <span key={`${w}-${i}`} style={{display: 'inline-block', marginRight: 22, color: S.silverHot, ...r}}>{w}</span>;
            })}
          </div>
        ))}
      </div>
      {s.sub && <div style={{...sub, marginTop: 60, fontSize: 42, lineHeight: 1.4, color: S.muted, maxWidth: 860}}>{s.sub}</div>}
    </AbsoluteFill>
  );
};

// Cards: stacked items rising in
const CardsScene: React.FC<{s: Scene}> = ({s}) => {
  const head = useRise(0, 30);
  return (
    <AbsoluteFill style={{padding: '230px 80px', fontFamily: FONT}}>
      <div style={{...head}}>
        <div style={{color: S.silver, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>{s.kicker}</div>
        <div style={{color: S.silverHot, fontSize: 84, fontWeight: 900, marginTop: 14, lineHeight: 1.05}}>{s.heading}</div>
      </div>
      <div style={{marginTop: 70, display: 'flex', flexDirection: 'column', gap: 30}}>
        {(s.items ?? []).map((it, i) => <Card key={it.t} item={it} delay={18 + i * 30} />)}
      </div>
    </AbsoluteFill>
  );
};

const Card: React.FC<{item: Item; delay: number}> = ({item, delay}) => {
  const frame = useCurrentFrame();
  const r = useRise(delay, 300);
  const lit = interpolate(frame, [delay + 10, delay + 30, delay + 60], [0, 1, 0.4], clamp);
  return (
    <div style={{...r, padding: '36px 48px', borderRadius: 28,
      background: 'linear-gradient(135deg, rgba(217,222,229,0.10), rgba(255,255,255,0.02))',
      border: `1.5px solid rgba(217,222,229,${0.3 + 0.4 * lit})`, boxShadow: `0 0 ${40 * lit}px rgba(217,222,229,${0.2 * lit})`}}>
      <div style={{color: S.silverHot, fontSize: 46, fontWeight: 800}}>{item.t}</div>
      <div style={{color: S.muted, fontSize: 32, marginTop: 10, lineHeight: 1.35}}>{item.b}</div>
    </div>
  );
};

// Image: scrolling phone-frame viewport
const ImageScene: React.FC<{s: Scene}> = ({s}) => {
  const frame = useCurrentFrame();
  const W = 920, H = 1000;
  const imgH = (W * 1536) / 1024;
  const p = interpolate(frame, [20, 150], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const frameIn = useRise(4, 80);
  return (
    <AbsoluteFill style={{fontFamily: FONT}}>
      <div style={{position: 'absolute', top: 210, left: 90, right: 90}}>
        <div style={{color: S.silver, fontSize: 30, letterSpacing: 7, fontWeight: 600}}>{s.kicker}</div>
        {s.heading && <div style={{color: S.silverHot, fontSize: 50, fontWeight: 800, marginTop: 12, lineHeight: 1.15}}>{s.heading}</div>}
      </div>
      <div style={{position: 'absolute', top: 470, left: (WIDTH - W) / 2, width: W, height: H, opacity: frameIn.opacity, transform: frameIn.transform}}>
        <div style={{position: 'absolute', inset: 0, borderRadius: 34, overflow: 'hidden', border: '2px solid rgba(217,222,229,0.6)',
          boxShadow: '0 40px 140px rgba(217,222,229,0.16)', background: S.ink}}>
          <Img src={staticFile(s.image ?? 'flyer.png')} style={{width: W, display: 'block', transform: `translateY(${-(imgH - H) * p}px)`}} />
          <Sheen period={150} delay={30} />
        </div>
        <OrbitRect w={W} h={H} r={34} speed={0.9} id="img" />
      </div>
    </AbsoluteFill>
  );
};

// Stat: count-up
const StatScene: React.FC<{s: Scene}> = ({s}) => {
  const frame = useCurrentFrame();
  const raw = interpolate(frame, [10, 80], [0, s.value ?? 0], {...clamp, easing: Easing.out(Easing.cubic)});
  const shown = s.decimals ? raw.toFixed(s.decimals) : Math.round(raw).toString();
  const a = useRise(0, 120);
  const b = useRise(30, 60);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: FONT, padding: '0 80px'}}>
      <div style={{...a, color: S.silverHot, fontSize: 230, fontWeight: 900, lineHeight: 1, textShadow: '0 0 60px rgba(217,222,229,0.35)'}}>
        {shown + (s.suffix ?? '')}
      </div>
      <div style={{...b, color: S.silver, fontSize: 38, letterSpacing: 2, marginTop: 24, textAlign: 'center', lineHeight: 1.35}}>{s.label}</div>
    </AbsoluteFill>
  );
};

// CTA: headline, button, optional headshot
const CtaScene: React.FC<{s: Scene}> = ({s}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const photo = spring({frame: frame - 2, fps, config: {damping: 16, stiffness: 110}});
  const head = useRise(s.headshot ? 24 : 4, 50);
  const body = useRise(s.headshot ? 48 : 24, 30);
  const btn = useRise(s.headshot ? 66 : 44, 30);
  const pulse = 1 + 0.012 * Math.sin((frame / fps) * Math.PI * 2 * 1.2) * (frame > 90 ? 1 : 0);
  return (
    <AbsoluteFill style={{fontFamily: FONT, alignItems: 'center', justifyContent: 'center', padding: '0 80px', textAlign: 'center'}}>
      {s.headshot && (
        <div style={{width: 340, height: 340, borderRadius: '50%', padding: 8, marginBottom: 60, transform: `scale(${0.6 + 0.4 * photo})`, opacity: photo,
          background: `linear-gradient(135deg, ${S.silverHot}, ${S.steel} 50%, ${S.silver})`, boxShadow: '0 30px 120px rgba(217,222,229,0.25)'}}>
          <Img src={staticFile('headshot.png')} style={{width: '100%', height: '100%', borderRadius: '50%', objectFit: 'cover', objectPosition: 'center 25%'}} />
        </div>
      )}
      <div style={{...head, color: S.silverHot, fontSize: 84, fontWeight: 900, lineHeight: 1.05, whiteSpace: 'pre-line'}}>{s.headline}</div>
      <div style={{...body, color: S.muted, fontSize: 36, marginTop: 28, lineHeight: 1.4}}>{s.body}</div>
      <div style={{...btn, marginTop: 56, background: S.silver, color: S.ink, fontSize: 42, fontWeight: 900, padding: '32px 64px', borderRadius: 999,
        letterSpacing: 1, transform: `scale(${pulse})`, boxShadow: '0 20px 60px rgba(217,222,229,0.3)', position: 'relative', overflow: 'hidden'}}>
        {s.button} →
        <Sheen period={120} delay={100} />
      </div>
      <div style={{...btn, marginTop: 40, color: S.silver, fontSize: 34, letterSpacing: 5, fontWeight: 700}}>{(s.url ?? '').toUpperCase()}</div>
    </AbsoluteFill>
  );
};

const LogoScene: React.FC<{s: Scene}> = ({s}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame, fps, config: {damping: 200}});
  const url = useRise(14, 20);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: FONT}}>
      <Img src={staticFile('logo.png')} style={{width: 720, opacity: p, transform: `scale(${0.94 + 0.06 * p})`, mixBlendMode: 'screen'}} />
      <div style={{...url, marginTop: 40, color: S.silver, fontSize: 40, letterSpacing: 6, fontWeight: 700}}>{s.url}</div>
    </AbsoluteFill>
  );
};

const RENDER: Record<Scene['type'], React.FC<{s: Scene}>> = {
  title: TitleScene, cards: CardsScene, image: ImageScene, stat: StatScene, cta: CtaScene, logo: LogoScene,
};

// Captions: words in the spoken line light up silver-white
type Caption = {words: {text: string; from: number}[]; from: number; to: number};
const buildCaptions = (line: Line): Caption[] => {
  const tokens = line.text.split(/\s+/);
  const timed = tokens.map((text, i) => {
    const w = line.words[i];
    return {text, from: line.startFrame + Math.round(((w ? w.startMs : 0) / 1000) * 30), to: line.startFrame + Math.round(((w ? w.endMs : 0) / 1000) * 30)};
  });
  const chunks: Caption[] = [];
  let cur: Caption | null = null;
  timed.forEach((t) => {
    if (!cur) cur = {words: [], from: t.from, to: t.to};
    cur.words.push(t);
    cur.to = t.to;
    if (/[.?!,]$/.test(t.text) || cur.words.length >= 5) { chunks.push(cur); cur = null; }
  });
  if (cur) chunks.push(cur);
  return chunks;
};

const Captions: React.FC<{lines: Line[]}> = ({lines}) => {
  const frame = useCurrentFrame();
  const all = lines.flatMap(buildCaptions);
  const active = all.find((c) => frame >= c.from - 2 && frame < c.to + 8);
  if (!active) return null;
  const fadeIn = interpolate(frame, [active.from - 2, active.from + 4], [0, 1], clamp);
  const fadeOut = interpolate(frame, [active.to + 2, active.to + 8], [1, 0], clamp);
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', pointerEvents: 'none'}}>
      <div style={{marginBottom: 300, maxWidth: 940, padding: '20px 36px', borderRadius: 26, background: 'rgba(5,6,7,0.72)',
        border: '1.5px solid rgba(217,222,229,0.4)', boxShadow: '0 16px 50px rgba(0,0,0,0.6)', fontFamily: FONT, fontSize: 46, fontWeight: 800,
        lineHeight: 1.25, textAlign: 'center', opacity: fadeIn * fadeOut, transform: `translateY(${(1 - fadeIn) * 14}px)`}}>
        {active.words.map((w, i) => {
          const on = frame >= w.from;
          return <span key={`${w.text}-${i}`} style={{color: on ? S.silverHot : S.steel, textShadow: on ? '0 0 18px rgba(217,222,229,0.55)' : 'none', marginRight: 12, display: 'inline-block'}}>{w.text}</span>;
        })}
      </div>
    </AbsoluteFill>
  );
};

export const Episode: React.FC<{data: EpisodeData}> = ({data}) => (
  <AbsoluteFill style={{backgroundColor: S.bg}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 35%, #15171b 0%, ${S.bg} 60%, #000 100%)`}} />
    {data.scenes.map((scene, i) => {
      const C = RENDER[scene.type];
      return (
        <Sequence key={i} from={scene.start} durationInFrames={scene.length}>
          <SceneFade dur={scene.length}><C s={scene} /></SceneFade>
        </Sequence>
      );
    })}
    {data.voiceover.map((line) => (
      <Sequence key={line.id} from={line.startFrame} durationInFrames={line.durationFrames + 6}>
        <Audio src={staticFile(line.file)} />
      </Sequence>
    ))}
    <FrameLight />
    <Captions lines={data.voiceover} />
  </AbsoluteFill>
);
