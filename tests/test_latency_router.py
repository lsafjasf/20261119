import unittest

from latency_router import (
    CompensationExceeded,
    CycleError,
    Engine,
    Graph,
    SmoothDelay,
)

BLOCK = 64


def build_graph(max_compensation=4096):
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


def run(engine, blocks, fire_at=None, change_at=None, change=None):
    out = []
    for n in range(blocks):
        block = [0.0] * BLOCK
        if n == fire_at:
            block[0] = 1.0
        if n == change_at and change:
            engine.set_node_delay(*change)
        out.extend(engine.process_block(block))
    return out


def peaks(signal, threshold=1e-6):
    return [(i, v) for i, v in enumerate(signal) if abs(v) > threshold]


class TestPlanning(unittest.TestCase):
    def test_cumulative_latencies(self):
        g = build_graph()
        latency, _ = g.latencies()
        self.assertEqual(latency["a_comp"], 160)
        self.assertEqual(latency["b_fx"], 300)
        self.assertEqual(latency["c_verb"], 120)
        self.assertEqual(latency["mix"], 310)

    def test_compensation_plan_pads_short_paths(self):
        plan = build_graph().plan()
        self.assertEqual(plan["mix"], {"a_comp": 140, "b_fx": 0, "c_verb": 180})

    def test_budgets_report_per_path(self):
        budgets = {tuple(b.route): b for b in build_graph().budgets()}
        b = budgets[("src", "c_gate", "c_verb")]
        self.assertEqual(b.path_latency, 120)
        self.assertEqual(b.compensation, 180)
        self.assertEqual(b.target_latency, 300)
        self.assertEqual(b.headroom, 4096 - 180)
        self.assertTrue(all(x.ok for x in budgets.values()))

    def test_cycle_rejected_with_path(self):
        g = build_graph()
        with self.assertRaises(CycleError) as ctx:
            g.add_edge("mix", "a_eq")
        self.assertEqual(ctx.exception.cycle, ["a_eq", "a_comp", "mix", "a_eq"])
        with self.assertRaises(CycleError):
            g.add_edge("b_fx", "b_fx")

    def test_compensation_exceeded_reports_path_and_deficit(self):
        g = build_graph(max_compensation=100)
        with self.assertRaises(CompensationExceeded) as ctx:
            g.plan()
        err = ctx.exception
        self.assertEqual(err.merge, "mix")
        self.assertEqual(err.path, ["src", "a_eq", "a_comp"])
        self.assertEqual(err.required, 140)
        self.assertEqual(err.limit, 100)
        self.assertEqual(err.deficit, 40)


class TestEngine(unittest.TestCase):
    def test_impulse_aligns_at_merge(self):
        engine = Engine(build_graph(), "src", "mix")
        hits = peaks(run(engine, 12, fire_at=0))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], 310)
        self.assertAlmostEqual(hits[0][1], 3.0)

    def test_midflight_shrink_preserves_impulse(self):
        engine = Engine(build_graph(), "src", "mix")
        out = run(engine, 14, fire_at=0, change_at=2, change=("b_fx", 80))
        hits = peaks(out)
        self.assertAlmostEqual(sum(v for _, v in hits), 3.0)
        # After settling, a fresh impulse aligns at the new target.
        hits = peaks(run(engine, 12, fire_at=0))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], 170)
        self.assertAlmostEqual(hits[0][1], 3.0)

    def test_midflight_growth_preserves_impulse(self):
        engine = Engine(build_graph(), "src", "mix")
        out = run(engine, 16, fire_at=0, change_at=1, change=("c_verb", 400))
        hits = peaks(out)
        self.assertAlmostEqual(sum(v for _, v in hits), 3.0)
        hits = peaks(run(engine, 12, fire_at=0))
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0][1], 3.0)

    def test_rejected_change_rolls_back(self):
        g = build_graph(max_compensation=200)
        engine = Engine(g, "src", "mix")
        with self.assertRaises(CompensationExceeded):
            engine.set_node_delay("b_fx", 600)
        self.assertEqual(g.nodes["b_fx"].delay, 300)
        hits = peaks(run(engine, 12, fire_at=0))
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0][1], 3.0)


class TestSmoothDelay(unittest.TestCase):
    def test_plain_delay(self):
        line = SmoothDelay(4)
        out = [line.process_sample(x) for x in [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]]
        self.assertEqual(out, [0.0, 0.0, 0.0, 0.0, 1.0, 0.0])

    def test_retune_never_loses_energy(self):
        for new_delay in (2, 9, 0, 15):
            line = SmoothDelay(6)
            total = 0.0
            for n in range(40):
                if n == 3:
                    line.set_delay(new_delay)
                total += line.process_sample(1.0 if n == 1 else 0.0)
            self.assertAlmostEqual(total, 1.0, msg=f"new_delay={new_delay}")


if __name__ == "__main__":
    unittest.main()
