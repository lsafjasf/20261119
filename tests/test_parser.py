import unittest
from pathlib import Path

from hevc_tiles.analysis import analyze, frame_count
from hevc_tiles.parser import build_tile_metrics, parse_stream


ROOT = Path(__file__).resolve().parents[1]
INDEPENDENT = ROOT / "samples" / "tiles_independent.hevc"
ENTRYPOINTS = ROOT / "samples" / "tiles_entrypoints.hevc"
DEPENDENT = ROOT / "samples" / "tiles_loopfilter_dependency.hevc"


class ParserTests(unittest.TestCase):
    def test_independent_layout_and_metrics(self):
        info = parse_stream(INDEPENDENT)
        self.assertEqual((info.sps.width, info.sps.height), (256, 192))
        self.assertEqual((info.layout.num_columns, info.layout.num_rows), (2, 2))
        self.assertEqual(info.layout.column_widths_ctbs, [2, 2])
        self.assertEqual(info.layout.row_heights_ctbs, [1, 2])
        self.assertEqual([tile.id for tile in info.layout.tiles], [0, 1, 2, 3])
        self.assertEqual(frame_count(info), 3)
        self.assertEqual(len(analyze(info, 0).frame_slices), 4)
        self.assertFalse(info.pps.loop_filter_across_tiles_enabled)

        metrics = {item.tile_id: item for item in build_tile_metrics(info)}
        self.assertEqual(metrics[0].slice_count, 3)
        self.assertGreater(metrics[0].average_bytes, 0)
        self.assertGreater(metrics[3].average_bytes, metrics[1].average_bytes)

    def test_entry_point_offsets_are_cumulative(self):
        info = parse_stream(ENTRYPOINTS)
        analysis = analyze(info, 0)
        metrics = {item.tile_id: item for item in analysis.metrics}
        self.assertEqual(len(analysis.frame_slices), 1)
        self.assertEqual(metrics[0].entry_offsets, [0, 0, 0])
        self.assertEqual(metrics[1].entry_offsets, [1786, 1300, 1345])
        self.assertEqual(
            metrics[2].entry_offsets,
            [2301, 2074, 1768],
        )
        self.assertEqual(metrics[3].entry_offsets, [2413, 3153, 2934])
        self.assertTrue(analysis.local_allowed)

    def test_loop_filter_dependency_is_reported(self):
        info = parse_stream(DEPENDENT)
        analysis = analyze(info, 0)
        self.assertTrue(info.pps.loop_filter_across_tiles_enabled)
        self.assertFalse(analysis.local_allowed)
        self.assertTrue(analysis.cross_tile_dependencies)
        kinds = {edge.kind for edge in analysis.cross_tile_dependencies}
        self.assertEqual(kinds, {"loop-filter"})


if __name__ == "__main__":
    unittest.main()
