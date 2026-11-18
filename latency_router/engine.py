"""Block-based processing engine with lossless delay migration.

Every processing node and every compensation tap is a SmoothDelay: a
delay line whose length can change at runtime.  Retuning uses
tail-drain semantics -- the previous line is frozen and keeps playing
out whatever it already holds, while new input takes the new line.
In-flight transients (e.g. a verification impulse) are therefore never
truncated, duplicated or dropped, and the migration is click-free.
"""

from __future__ import annotations

from collections import deque

from .graph import Graph


class SmoothDelay:
    """Integer-sample delay line with tail-drain runtime retuning."""

    def __init__(self, delay: int = 0):
        self._delay = delay
        self._buf: deque[float] = deque([0.0] * (delay + 1), maxlen=delay + 1)
        self._tails: list[list] = []  # frozen lines draining out

    @property
    def delay(self) -> int:
        return self._delay

    def set_delay(self, delay: int) -> None:
        if delay == self._delay:
            return
        # Freeze the current line; it drains its remaining samples while
        # new input flows through the retuned line.  The new line starts
        # with zero history: everything already captured belongs to the
        # draining tail, so no sample is ever duplicated or dropped.
        self._tails.append([deque(self._buf), len(self._buf)])
        self._delay = delay
        self._buf = deque([0.0] * (delay + 1), maxlen=delay + 1)

    def process_sample(self, x: float) -> float:
        self._buf.append(x)
        out = self._buf[0]
        for tail in self._tails:
            out += tail[0].popleft()
            tail[1] -= 1
        self._tails = [t for t in self._tails if t[1] > 0]
        return out

    def process_block(self, block: list[float]) -> list[float]:
        return [self.process_sample(x) for x in block]


class Engine:
    """Runs a Graph block-by-block and keeps merge inputs aligned."""

    def __init__(
        self,
        graph: Graph,
        source: str,
        sink: str,
    ):
        if source not in graph.nodes or sink not in graph.nodes:
            raise KeyError("source and sink must be graph nodes")
        self.graph = graph
        self.source = source
        self.sink = sink
        self._node_delay: dict[str, SmoothDelay] = {
            name: SmoothDelay(node.delay)
            for name, node in graph.nodes.items()
        }
        self._comp: dict[tuple[str, str], SmoothDelay] = {}
        self.replan()

    def replan(self) -> None:
        """Recompute compensation taps from current node delays."""
        plan = self.graph.plan()
        wanted = {
            (merge, pred): comp
            for merge, entry in plan.items()
            for pred, comp in entry.items()
        }
        for key in list(self._comp):
            if key not in wanted:
                del self._comp[key]
        for key, comp in wanted.items():
            line = self._comp.setdefault(key, SmoothDelay(comp))
            line.set_delay(comp)

    def set_node_delay(self, name: str, delay: int) -> None:
        """Change a node's processing delay and migrate smoothly."""
        old = self.graph.nodes[name].delay
        self.graph.set_delay(name, delay)
        try:
            self.graph.plan()  # validate before touching any delay line
        except Exception:
            self.graph.set_delay(name, old)
            raise
        self._node_delay[name].set_delay(delay)
        self.replan()

    def process_block(self, inputs: list[float]) -> list[float]:
        """Push one block of source samples, return sink output block."""
        out: dict[str, list[float]] = {}
        for name in self.graph.topological_order():
            if name == self.source:
                raw = list(inputs)
            else:
                raw = [0.0] * len(inputs)
                for pred in self.graph.predecessors(name):
                    contrib = out[pred]
                    comp = self._comp.get((name, pred))
                    if comp is not None:
                        contrib = comp.process_block(contrib)
                    for i, v in enumerate(contrib):
                        raw[i] += v
            out[name] = self._node_delay[name].process_block(raw)
        return out[self.sink]
