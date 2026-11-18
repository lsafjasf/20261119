"""Signal-flow graph with latency planning for parallel audio branches.

The graph is a DAG of processing nodes.  Every node has a known but
runtime-variable processing delay (in samples).  From the topology the
planner derives, for every merge point, the cumulative latency of each
incoming path and the compensation delay that must be inserted on the
shorter paths so that all paths arrive aligned.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class CycleError(Exception):
    """Raised when an edge would introduce a cyclic dependency."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("cyclic dependency rejected: " + " -> ".join(cycle))


class CompensationExceeded(Exception):
    """Raised when a path needs more compensation than the configured limit."""

    def __init__(self, merge: str, path: list[str], required: int, limit: int):
        self.merge = merge
        self.path = path
        self.required = required
        self.limit = limit
        self.deficit = required - limit
        super().__init__(
            f"compensation limit exceeded at merge '{merge}': "
            f"path {' -> '.join(path)} needs {required} samples, "
            f"limit is {limit} (short by {self.deficit})"
        )


@dataclass
class Node:
    """A processing node with a variable delay, in samples."""

    name: str
    delay: int = 0

    def __post_init__(self) -> None:
        if self.delay < 0:
            raise ValueError("delay must be >= 0 samples")


@dataclass
class PathBudget:
    """Latency budget of one incoming path at a merge point."""

    merge: str
    route: list[str]
    path_latency: int
    compensation: int
    target_latency: int
    limit: int

    @property
    def headroom(self) -> int:
        return self.limit - self.compensation

    @property
    def ok(self) -> bool:
        return self.compensation <= self.limit


@dataclass
class Graph:
    """Directed acyclic graph of audio processing nodes."""

    max_compensation: int = 4096
    nodes: dict[str, Node] = field(default_factory=dict)
    _succ: dict[str, list[str]] = field(default_factory=dict)
    _pred: dict[str, list[str]] = field(default_factory=dict)

    def add_node(self, name: str, delay: int = 0) -> Node:
        if name in self.nodes:
            raise ValueError(f"duplicate node '{name}'")
        node = Node(name, delay)
        self.nodes[name] = node
        self._succ[name] = []
        self._pred[name] = []
        return node

    def add_edge(self, src: str, dst: str) -> None:
        self._require(src)
        self._require(dst)
        if src == dst:
            raise CycleError([src, dst])
        back = self._path_between(dst, src)
        if back is not None:
            raise CycleError(back + [dst])
        self._succ[src].append(dst)
        self._pred[dst].append(src)

    def set_delay(self, name: str, delay: int) -> None:
        if delay < 0:
            raise ValueError("delay must be >= 0 samples")
        self._require(name).delay = delay

    def predecessors(self, name: str) -> list[str]:
        return list(self._pred[name])

    def successors(self, name: str) -> list[str]:
        return list(self._succ[name])

    def merges(self) -> list[str]:
        return [n for n in self.nodes if len(self._pred[n]) > 1]

    def _require(self, name: str) -> Node:
        if name not in self.nodes:
            raise KeyError(f"unknown node '{name}'")
        return self.nodes[name]

    def _path_between(self, start: str, goal: str) -> list[str] | None:
        """DFS path start -> goal following existing edges, or None."""
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        visited: set[str] = set()
        while stack:
            node, path = stack.pop()
            if node == goal:
                return path
            if node in visited:
                continue
            visited.add(node)
            for nxt in self._succ[node]:
                stack.append((nxt, path + [nxt]))
        return None

    def topological_order(self) -> list[str]:
        indeg = {n: len(self._pred[n]) for n in self.nodes}
        ready = [n for n in self.nodes if indeg[n] == 0]
        order: list[str] = []
        while ready:
            node = ready.pop()
            order.append(node)
            for nxt in self._succ[node]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
        if len(order) != len(self.nodes):
            raise CycleError(self._cycle_witness())
        return order

    def _cycle_witness(self) -> list[str]:
        for name in self.nodes:
            back = self._path_between(name, name)
            if back:
                return back
        return []

    def latencies(self) -> tuple[dict[str, int], dict[str, str | None]]:
        """Longest-path cumulative latency at each node's output.

        Returns (latency, parent) where parent reconstructs the
        longest incoming route for each node.
        """
        latency: dict[str, int] = {}
        parent: dict[str, str | None] = {}
        for name in self.topological_order():
            best_pred: str | None = None
            best = 0
            for pred in self._pred[name]:
                if latency[pred] > best or best_pred is None:
                    best = latency[pred]
                    best_pred = pred
            latency[name] = best + self.nodes[name].delay
            parent[name] = best_pred
        return latency, parent

    def route_to(self, name: str) -> list[str]:
        """Longest-latency route from a source down to ``name``."""
        _, parent = self.latencies()
        route = [name]
        while parent[route[-1]] is not None:
            route.append(parent[route[-1]])  # type: ignore[arg-type]
        route.reverse()
        return route

    def plan(self) -> dict[str, dict[str, int]]:
        """Compensation delay per merge input: {merge: {pred: samples}}.

        Raises CompensationExceeded naming the offending path and the
        deficit when the required padding exceeds ``max_compensation``.
        """
        latency, _ = self.latencies()
        plan: dict[str, dict[str, int]] = {}
        for merge in self.merges():
            target = max(latency[p] for p in self._pred[merge])
            entry: dict[str, int] = {}
            for pred in self._pred[merge]:
                comp = target - latency[pred]
                if comp > self.max_compensation:
                    raise CompensationExceeded(
                        merge, self.route_to(pred), comp, self.max_compensation
                    )
                entry[pred] = comp
            plan[merge] = entry
        return plan

    def budgets(self) -> list[PathBudget]:
        """Per-path latency budget at every merge point."""
        latency, _ = self.latencies()
        budgets: list[PathBudget] = []
        for merge in self.merges():
            target = max(latency[p] for p in self._pred[merge])
            for pred in self._pred[merge]:
                budgets.append(
                    PathBudget(
                        merge=merge,
                        route=self.route_to(pred),
                        path_latency=latency[pred],
                        compensation=target - latency[pred],
                        target_latency=target,
                        limit=self.max_compensation,
                    )
                )
        return budgets
