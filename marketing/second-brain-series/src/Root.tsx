import React from 'react';
import {Composition} from 'remotion';
import {Episode, EpisodeData} from './Episode';
import ep1 from './timelines/ep1.json';
import ep2 from './timelines/ep2.json';
import ep3 from './timelines/ep3.json';
import ep4 from './timelines/ep4.json';
import ep5 from './timelines/ep5.json';
import tc1 from './timelines/tc1.json';
import tc2 from './timelines/tc2.json';
import tc3 from './timelines/tc3.json';
import tc4 from './timelines/tc4.json';

// Composition ids: Ep1..Ep5 for The Second Brain Series, Trust1..Trust4 for The Trust Check.
const COMPOSITIONS: [string, unknown][] = [
  ['Ep1', ep1], ['Ep2', ep2], ['Ep3', ep3], ['Ep4', ep4], ['Ep5', ep5],
  ['Trust1', tc1], ['Trust2', tc2], ['Trust3', tc3], ['Trust4', tc4],
];

export const RemotionRoot: React.FC = () => (
  <>
    {COMPOSITIONS.map(([id, ep]) => {
      const data = ep as EpisodeData;
      return (
        <Composition
          key={id}
          id={id}
          component={Episode}
          durationInFrames={data.durationInFrames}
          fps={30}
          width={1080}
          height={1920}
          defaultProps={{data}}
        />
      );
    })}
  </>
);
