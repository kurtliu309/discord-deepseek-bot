import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

import persona


NOW = dt.datetime(2026, 11, 14, 22, 5, tzinfo=dt.timezone.utc)


class TestPersonaPrompt(unittest.TestCase):
    def setUp(self):
        persona.clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._write("_base.md", "BASE-FLOOR")
        self._write("tiredfx/character.md", "CHAR-ACTER")
        self._write("turtle/character.md", "TURTLE-CHAR")
        self._write("_styles/normal.md", "STYLE-NORMAL")
        self._write("_styles/chatty.md", "STYLE-CHATTY")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def _build(self, **kw):
        kw.setdefault("now", NOW)
        return persona.build_system_prompt(self.root, **kw)

    def test_assembly_order(self):
        prompt = self._build(style="chatty", extra_prompt="EXTRA-PROMPT")
        positions = [
            prompt.index("BASE-FLOOR"),
            prompt.index("CHAR-ACTER"),
            prompt.index("STYLE-CHATTY"),
            prompt.index("Default to English"),
            prompt.index("EXTRA-PROMPT"),
            prompt.index("Today's date is November 14 2026"),
            prompt.index("<@ID>"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_persona_and_style_selection(self):
        prompt = self._build(persona="turtle", style="normal")
        self.assertIn("TURTLE-CHAR", prompt)
        self.assertNotIn("CHAR-ACTER", prompt)
        self.assertIn("STYLE-NORMAL", prompt)
        self.assertNotIn("STYLE-CHATTY", prompt)

    def test_language_line(self):
        self.assertIn("Default to English", self._build(language="en"))
        self.assertIn("Default to Chinese", self._build(language="zh"))
        self.assertIn("Default to klingon", self._build(language="klingon"))

    def test_time_lines_last(self):
        prompt = self._build()
        self.assertIn("November 14 2026", prompt)
        self.assertIn("22:05 UTC", prompt)
        self.assertTrue(prompt.endswith("Use that format to mention or call out users."))

    def test_extra_prompt_optional(self):
        self.assertNotIn("None", self._build(extra_prompt=""))
        self.assertIn("be grumpy", self._build(extra_prompt="  be grumpy  "))

    def test_missing_style_is_ignored(self):
        prompt = self._build(style="does-not-exist")
        self.assertIn("CHAR-ACTER", prompt)

    def test_missing_persona_raises(self):
        with self.assertRaises(FileNotFoundError):
            self._build(persona="nobody")

    def test_mtime_cache_reloads(self):
        path = self.root / "tiredfx" / "character.md"
        first = self._build()
        self.assertIn("CHAR-ACTER", first)

        mtime = path.stat().st_mtime_ns
        path.write_text("CHANGED-CHAR\n", encoding="utf-8")
        os.utime(path, ns=(mtime + 10 ** 9, mtime + 10 ** 9))
        second = self._build()
        self.assertIn("CHANGED-CHAR", second)
        self.assertNotIn("CHAR-ACTER", second)


class TestSplitTurns(unittest.TestCase):
    def test_single(self):
        self.assertEqual(persona.split_turns("one short line"), ["one short line"])

    def test_marker_lines(self):
        text = "first\n[[next]]\nsecond\n[[next]]\nthird"
        self.assertEqual(persona.split_turns(text), ["first", "second", "third"])

    def test_blank_parts_dropped(self):
        self.assertEqual(persona.split_turns("a\n[[next]]\n\n[[next]]\nb"), ["a", "b"])

    def test_extra_turns_merge_into_last(self):
        text = "[[next]]".join(str(i) for i in range(1, 8))
        turns = persona.split_turns(text)
        self.assertEqual(len(turns), persona.MAX_TURNS)
        self.assertIn("6", turns[-1])
        self.assertIn("7", turns[-1])

    def test_empty(self):
        self.assertEqual(persona.split_turns(""), [])
        self.assertEqual(persona.split_turns(None), [])


class TestTypingDelay(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(persona.typing_delay(""), 1.2)
        self.assertEqual(persona.typing_delay("x" * 10), 1.2)
        self.assertEqual(persona.typing_delay("x" * 40), 2.0)
        self.assertEqual(persona.typing_delay("x" * 500), 4.0)


if __name__ == "__main__":
    unittest.main()
