"""Episode replay lookup from a batch's .episodes.jsonl.

Accepted layouts (the sim writes one JSONL per batch):
  1. one line per 10 Hz frame carrying an episode index key ("episode" / "ep" / "episode_idx" / "episode_index");
  2. one line per episode with a "frames" list;
  3. frame lines without an index key: a new episode starts whenever "t" goes backwards.
An offset index per file is cached (keyed by path, size, mtime) so replay requests do not rescan the file.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path

EPISODE_KEYS = ("episode", "ep", "episode_idx", "episode_index")


class EpisodeIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.layout = "frames_by_t"
        self.spans: list[list[tuple[int, int]]] = []  # per episode: [(offset, length), ...]
        self._build()

    def _build(self) -> None:
        ep_of: dict = {}
        prev_t = None
        current = -1
        with self.path.open("rb") as f:
            offset = 0
            for raw in f:
                length = len(raw)
                line = raw.strip()
                if line:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        offset += length
                        continue
                    if not isinstance(obj, dict):
                        offset += length
                        continue
                    key = next((k for k in EPISODE_KEYS if k in obj), None)
                    if "frames" in obj and isinstance(obj["frames"], list):
                        self.layout = "episode_per_line"
                        self.spans.append([(offset, length)])
                    elif key is not None:
                        self.layout = "frames_with_index"
                        ep = obj[key]
                        if ep not in ep_of:
                            ep_of[ep] = len(self.spans)
                            self.spans.append([])
                        self.spans[ep_of[ep]].append((offset, length))
                    else:
                        t = obj.get("t")
                        if current < 0 or (isinstance(t, (int, float)) and prev_t is not None and t < prev_t):
                            current = len(self.spans)
                            self.spans.append([])
                        self.spans[current].append((offset, length))
                        prev_t = t if isinstance(t, (int, float)) else prev_t
                offset += length
        if self.layout == "frames_with_index":
            # order episodes by their index value when the values are ints
            try:
                order = sorted(ep_of.items(), key=lambda kv: int(kv[0]))
                self.spans = [self.spans[i] for _, i in order]
            except (TypeError, ValueError):
                pass

    def __len__(self) -> int:
        return len(self.spans)

    def episode(self, n: int) -> dict:
        if not 0 <= n < len(self.spans):
            raise IndexError(n)
        lines = []
        with self.path.open("rb") as f:
            for off, length in self.spans[n]:
                f.seek(off)
                lines.append(json.loads(f.read(length)))
        if self.layout == "episode_per_line":
            ep = dict(lines[0])
            frames = ep.pop("frames")
            meta = ep
        else:
            frames, meta = lines, {}
        events = []
        for fr in frames:
            for ev in (fr.get("events") or []) if isinstance(fr, dict) else []:
                events.append({**ev, "t": fr.get("t")} if isinstance(ev, dict) else {"event": ev, "t": fr.get("t")})
        return {"episode": n, "n_episodes": len(self.spans), "hz": 10, "layout": self.layout,
                "meta": meta, "n_frames": len(frames), "frames": frames, "events": events}


class EpisodeCache:
    def __init__(self, max_files: int = 16):
        self._items: OrderedDict = OrderedDict()
        self._max = max_files
        self._lock = threading.Lock()

    def get(self, path: Path) -> EpisodeIndex:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
        with self._lock:
            idx = self._items.get(key)
            if idx is not None:
                self._items.move_to_end(key)
                return idx
        idx = EpisodeIndex(path)
        with self._lock:
            self._items[key] = idx
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return idx
