"""Demo: latency-compensating router over three parallel audio branches."""

from __future__ import annotations

from latency_router import (
    CompensationExceeded,
    CycleError,
    Engine,
    Graph,
)

BLOCK = 64


def build_graph(max_compensation: int = 4096) -> Graph:
    g = Graph(max_compensation=max_compensation)
    g.add_node("src", 0)
    g.add_node("a_eq", 120)
    g.add_node("a_comp", 40)
    g.add_node("b_fx", 300)
    g.add_node("c_gate", 30)
    g.add_node("c_verb", 90)
    g.add_node("mix", 10)
    g.add_edge("src", "a_eq")
    g.add_edge("a_eq", "a_comp")
    g.add_edge("src", "b_fx")
    g.add_edge("src", "c_gate")
    g.add_edge("c_gate", "c_verb")
    for tail in ("a_comp", "b_fx", "c_verb"):
        g.add_edge(tail, "mix")
    return g


def show_budgets(g: Graph) -> None:
    print("per-path latency budget at merge points:")
    for b in g.budgets():
        status = "OK" if b.ok else "EXCEEDED"
        route = " -> ".join(b.route)
        print(
            f"  [{b.merge}] {route:<28} path={b.path_latency:>4} "
            f"comp={b.compensation:>4} total={b.path_latency + b.compensation:>4} "
            f"target={b.target_latency:>4} headroom={b.headroom:>5} {status}"
        )
    print()


def run_impulse(engine: Engine, blocks: int, fire_at: int = 0) -> list[float]:
    out: list[float] = []
    for n in range(blocks):
        block = [0.0] * BLOCK
        if n == fire_at:
            block[0] = 1.0
        out.extend(engine.process_block(block))
    return out


def peaks(signal: list[float], threshold: float = 1e-6) -> list[tuple[int, float]]:
    return [(i, v) for i, v in enumerate(signal) if abs(v) > threshold]


def main() -> None:
    g = build_graph()
    engine = Engine(g, source="src", sink="mix")

    print("== 1. budgets (delays: A=160, B=300, C=120, mix=10) ==")
    show_budgets(g)

    print("== 2. impulse alignment: sum of three compensated paths ==")
    signal = run_impulse(engine, blocks=12)
    hits = peaks(signal)
    for i, v in hits:
        print(f"  sample {i:>4}: amplitude {v:.3f}")
    assert len(hits) == 1 and abs(hits[0][1] - 3.0) < 1e-9, "paths misaligned"
    print(f"  aligned: single peak of 3.0 at sample {hits[0][0]}\n")

    print("== 3. mid-flight delay change: b_fx 300 -> 80 at sample 128 ==")
    out: list[float] = []
    for n in range(14):
        block = [1.0] + [0.0] * (BLOCK - 1) if n == 0 else [0.0] * BLOCK
        if n == 2:  # impulse is in flight inside the graph
            engine.set_node_delay("b_fx", 80)
        out.extend(engine.process_block(block))
    show_budgets(g)
    hits = peaks(out)
    for i, v in hits:
        print(f"  sample {i:>4}: amplitude {v:.3f}")
    total = sum(v for _, v in hits)
    assert abs(total - 3.0) < 1e-9, f"impulse energy lost: {total}"
    print(f"  no impulse lost during migration (total energy {total:.3f})\n")

    print("== 3b. post-migration alignment at the new target latency ==")
    signal = run_impulse(engine, blocks=12)
    hits = peaks(signal)
    for i, v in hits:
        print(f"  sample {i:>4}: amplitude {v:.3f}")
    assert len(hits) == 1 and abs(hits[0][1] - 3.0) < 1e-9, "paths misaligned"
    print(f"  re-aligned: single peak of 3.0 at sample {hits[0][0]}\n")

    print("== 4. cyclic dependency rejection ==")
    try:
        g.add_edge("mix", "a_eq")
    except CycleError as e:
        print(f"  rejected: {e}\n")

    print("== 5. compensation limit overflow ==")
    small = build_graph(max_compensation=100)
    try:
        small.plan()
    except CompensationExceeded as e:
        print(f"  {e}")
        print(f"  path: {' -> '.join(e.path)}")
        print(f"  needs {e.required}, limit {e.limit}, deficit {e.deficit}")


if __name__ == "__main__":
    main()
