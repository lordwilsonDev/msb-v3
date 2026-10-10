import React from 'react';
import {Composition} from 'remotion';
import {DURATION, FoxValley, FPS, HEIGHT, WIDTH} from './FoxValley';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Reel"
    component={FoxValley}
    durationInFrames={DURATION}
    fps={FPS}
    width={WIDTH}
    height={HEIGHT}
  />
);
