import tempfile
import unittest
from pathlib import Path

import guildmem as gm


class TestLimits(unittest.TestCase):
    def test_limits(self):
        self.assertEqual(gm.block_limit("threads"), 600)
        self.assertEqual(gm.block_limit("people"), 600)
        self.assertEqual(gm.block_limit("jokes"), 400)
        self.assertEqual(gm.block_limit("unfinished"), 400)
        self.assertEqual(gm.block_limit("drift"), 300)
        self.assertIsNone(gm.block_limit("kurt"))
        with self.assertRaises(KeyError):
            gm.block_limit("secrets")

    def test_fits_and_enforce(self):
        self.assertTrue(gm.fits("drift", "x" * 300))
        self.assertFalse(gm.fits("drift", "x" * 301))
        self.assertEqual(len(gm.enforce_block("drift", "x" * 1000)), 300)
        self.assertEqual(gm.enforce_block("drift", "  keep  "), "keep")

    def test_enforce_prefers_line_boundary(self):
        text = ("word " * 20 + "\n") * 10  # 1010 chars, newline every 101
        out = gm.enforce_block("drift", text)
        self.assertLessEqual(len(out), 300)
        self.assertTrue(out.endswith("word"))

    def test_estimate_and_truncate(self):
        self.assertAlmostEqual(gm.estimate_tokens("x" * 35), 10.0)
        text = ("x" * 100 + "\n") * 10
        out = gm.truncate_to_tokens(text, 100)  # ~350 chars
        self.assertLessEqual(len(out), 350)
        self.assertGreater(len(out), 250)
        self.assertEqual(gm.truncate_to_tokens("short", 100), "short")
        self.assertEqual(gm.truncate_to_tokens("anything", 0), "")


class TestMemory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "guild"
        self.mem = gm.GuildMemory(self.root)
        self.mem.ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_write_blocks(self):
        self.assertEqual(self.mem.read_block("threads"), "")
        written = self.mem.write_block("threads", "  hello  ")
        self.assertEqual(written, "hello")
        self.assertEqual(self.mem.read_block("threads"), "hello")
        self.assertEqual(self.mem.read_block("jokes"), "")
        self.assertEqual(self.mem.read_blocks(["threads", "jokes"]), {"threads": "hello", "jokes": ""})

    def test_write_block_truncates(self):
        written = self.mem.write_block("people", "x" * 900)
        self.assertEqual(len(written), 600)
        self.assertEqual(self.mem.read_block("people"), written)
        raw = self.mem.write_block("people", "x" * 900, truncate=False)
        self.assertEqual(len(raw), 900)

    def test_kurt_is_read_only(self):
        with self.assertRaises(PermissionError):
            self.mem.write_block("kurt", "edited by the bot")
        self.assertFalse((self.root / "profile" / "kurt.md").exists())

    def test_seed_kurt_once(self):
        self.assertTrue(self.mem.seed_kurt("hand-written"))
        self.assertEqual(self.mem.read_block("kurt"), "hand-written")
        self.assertFalse(self.mem.seed_kurt("overwritten"))
        self.assertEqual(self.mem.read_block("kurt"), "hand-written")

    def test_oversized(self):
        blocks = {"threads": "x" * 700, "people": "short", "jokes": "x" * 10, "kurt": "x" * 5000}
        self.assertEqual(self.mem.oversized(blocks), ["threads"])

    def test_profile_text_reads_only_registered_blocks(self):
        self.mem.seed_kurt("KURT-NOTES")
        self.mem.write_block("threads", "THREAD-NOTES")
        self.mem.write_block("jokes", "JOKE-NOTES")
        text = self.mem.profile_text(("kurt", "jokes"), 500)
        self.assertIn("KURT-NOTES", text)
        self.assertIn("JOKE-NOTES", text)
        self.assertNotIn("THREAD-NOTES", text)
        self.assertIn("=== jokes ===", text)

    def test_profile_text_token_budget(self):
        self.mem.write_block("threads", "a" * 1000, truncate=False)
        self.mem.write_block("people", "b" * 1000, truncate=False)
        text = self.mem.profile_text(("threads", "people"), 100)  # ~350 chars total
        parts = ("\n\n" + text).split("\n\n=== ")[1:]
        bodies = [part.split("\n", 1)[1] for part in parts]
        self.assertLessEqual(sum(len(b) for b in bodies), 350)
        self.assertGreater(len(bodies[0]), 100)  # truncated proportionally, not dropped
        self.assertEqual(self.mem.profile_text(("threads",), 0), "")


class TestRecaps(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mem = gm.GuildMemory(Path(self._tmp.name) / "guild")
        self.mem.ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_read(self):
        self.mem.save_recap("2026-11-12", "RECAP-12")
        self.mem.save_recap("2026-11-14", "RECAP-14")
        self.mem.save_recap("2026-11-13", "RECAP-13")
        self.assertEqual(
            self.mem.recent_recaps(3),
            [("2026-11-12", "RECAP-12"), ("2026-11-13", "RECAP-13"), ("2026-11-14", "RECAP-14")],
        )
        self.assertEqual(self.mem.recent_recaps(2), [("2026-11-13", "RECAP-13"), ("2026-11-14", "RECAP-14")])
        self.assertEqual(self.mem.recent_recaps(0), [])

    def test_keeps_last_ten(self):
        for day in range(1, 13):
            self.mem.save_recap(f"2026-11-{day:02d}", f"day {day}")
        names = sorted(p.stem for p in self.mem.recaps_dir.glob("*.md"))
        self.assertEqual(len(names), gm.KEEP_RECAPS)
        self.assertNotIn("2026-11-01", names)
        self.assertNotIn("2026-11-02", names)
        self.assertIn("2026-11-12", names)

    def test_prune_returns_removed(self):
        for day in range(1, 4):
            self.mem.save_recap(f"2026-11-{day:02d}", f"day {day}")
        self.assertEqual(self.mem.prune_recaps(keep=1), ["2026-11-01", "2026-11-02"])

    def test_recaps_in_profile_text(self):
        self.mem.save_recap("2026-11-14", "YESTERDAY-WAS-FINE")
        text = self.mem.profile_text((), 500, recent_recaps=3)
        self.assertIn("=== recap 2026-11-14 ===", text)
        self.assertIn("YESTERDAY-WAS-FINE", text)

    def test_bad_date_rejected(self):
        with self.assertRaises(ValueError):
            self.mem.save_recap("../evil", "x")
        with self.assertRaises(ValueError):
            self.mem.save_recap("2026-1-1", "x")


if __name__ == "__main__":
    unittest.main()
