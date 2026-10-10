import React from 'react';
import {Composition} from 'remotion';
import {DURATION, Montage, FPS, HEIGHT, WIDTH} from './Montage';

export const RemotionRoot: React.FC = () => (
  <Composition id="Montage" component={Montage} durationInFrames={DURATION} fps={FPS} width={WIDTH} height={HEIGHT} />
);
