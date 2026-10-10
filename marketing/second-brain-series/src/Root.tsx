import React from 'react';
import {Composition} from 'remotion';
import {Episode, EpisodeData} from './Episode';
import ep1 from './timelines/ep1.json';
import ep2 from './timelines/ep2.json';
import ep3 from './timelines/ep3.json';
import ep4 from './timelines/ep4.json';

const EPISODES = [ep1, ep2, ep3, ep4] as unknown as EpisodeData[];

export const RemotionRoot: React.FC = () => (
  <>
    {EPISODES.map((ep) => (
      <Composition
        key={ep.id}
        id={`Ep${ep.id.replace('ep', '')}`}
        component={Episode}
        durationInFrames={ep.durationInFrames}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{data: ep}}
      />
    ))}
  </>
);
