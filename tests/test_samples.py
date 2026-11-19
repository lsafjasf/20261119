from __future__ import annotations

import unittest

from portraitcrop.samples import get_scenario


class SampleTests(unittest.TestCase):
    def test_dual_crossing_has_two_subjects_every_frame(self) -> None:
        scenario = get_scenario("dual_crossing")
        payload = scenario.annotation_payload()
        self.assertEqual(payload["fps"], 30)
        self.assertEqual(len(payload["frames"]), scenario.frame_count)
        self.assertTrue(
            all(len(frame["regions"]) == 2 for frame in payload["frames"])
        )

    def test_side_entry_appears_after_entry_time(self) -> None:
        scenario = get_scenario("side_entry")
        before = scenario.regions_at(2.3)
        after = scenario.regions_at(2.5)
        self.assertEqual([region.id for region in before], ["anchor"])
        self.assertEqual(
            [region.id for region in after], ["anchor", "newcomer"]
        )


if __name__ == "__main__":
    unittest.main()
