"""Code Graph subsystem (Sovereign Architecture v4.0 §4.2.1, P0).

Lets agents understand repository structure without loading whole files:
symbols (functions/classes/methods/types), their callers/callees, and the
blast radius of a change — answered from a local SQLite graph in well
under a second (validation gate G1).

Honest scope: the parser is stdlib-only (the SQLite-storage decision was
made to keep zero new dependencies). Python is parsed with the real `ast`
module (accurate); other languages use per-language regex heuristics and
are labeled ``approximate`` in node metadata. The graph is a static
approximation — it never claims dynamic call resolution.
"""

from msb_v3.codegraph.indexer import CodeGraphIndexer
from msb_v3.codegraph.queries import CodeGraphQueries
from msb_v3.codegraph.store import CodeGraphStore

__all__ = ["CodeGraphStore", "CodeGraphQueries", "CodeGraphIndexer"]
