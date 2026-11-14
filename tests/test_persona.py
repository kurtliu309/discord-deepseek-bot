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

    def test_default_speed_is_unchanged(self):
        for text in ("", "x", "x" * 40, "x" * 500):
            self.assertEqual(
                persona.typing_delay(text, speed=1.0), persona.typing_delay(text)
            )

    def test_speed_divides_the_delay(self):
        self.assertEqual(persona.typing_delay("x" * 40, speed=2.0), 1.0)
        self.assertEqual(persona.typing_delay("x" * 40, speed=0.5), 4.0)

    def test_clamped_to_0_6_and_8_seconds(self):
        self.assertEqual(persona.typing_delay("", speed=2.0), 0.6)
        self.assertEqual(persona.typing_delay("x" * 500, speed=0.5), 8.0)

    def test_bad_speed_falls_back_to_1(self):
        for speed in (0, -2, None, "2", float("nan"), float("inf")):
            self.assertEqual(persona.typing_delay("x" * 40, speed=speed), 2.0)


class TestPersonaTraits(unittest.TestCase):
    def setUp(self):
        persona.clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        persona.clear_cache()
        self._tmp.cleanup()

    def _traits(self, text, name="tiredfx"):
        path = self.root / name / "persona.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return persona.load_traits(self.root, name)

    def test_missing_file_means_defaults(self):
        self.assertEqual(persona.load_traits(self.root, "tiredfx"), persona.PersonaTraits())

    def test_defaults_keep_legacy_behaviour(self):
        traits = persona.load_traits(self.root, "tiredfx")
        self.assertEqual(traits.chance_scale, 1.0)
        self.assertIsNone(traits.fx_multiplier)  # None = FX talk gets no bonus
        self.assertFalse(traits.has_fx_multiplier)
        self.assertEqual(traits.cooldown_scale, 1.0)
        self.assertEqual(traits.burst_scale, 1.0)
        self.assertEqual(traits.typing_speed, 1.0)

    def test_broken_file_means_defaults(self):
        for text in ("interject: {chance_scale: 1.0", "- just\n- a list\n", "a bare string\n", ""):
            self.assertEqual(self._traits(text), persona.PersonaTraits(), msg=repr(text))

    def test_wrong_types_mean_defaults(self):
        traits = self._traits(
            "interject:\n"
            "  chance_scale: lots\n"
            "  fx_multiplier: true\n"
            "  cooldown_scale: [1]\n"
            "  burst_scale: many\n"
            "typing:\n"
            "  speed: fast\n"
        )
        self.assertEqual(traits, persona.PersonaTraits())

    def test_missing_fields_mean_defaults(self):
        traits = self._traits("interject:\n  chance_scale: 1.5\n")
        self.assertEqual(traits.chance_scale, 1.5)
        self.assertIsNone(traits.fx_multiplier)
        self.assertEqual(traits.cooldown_scale, 1.0)
        self.assertEqual(traits.typing_speed, 1.0)

    def test_burst_scale(self):
        traits = self._traits("interject:\n  burst_scale: 1.2\n")
        self.assertEqual(traits.burst_scale, 1.2)
        self.assertFalse(traits.has_fx_multiplier)
        self.assertEqual(self._traits("interject:\n  burst_scale: 0.9\n").burst_scale, 0.9)

    def test_values_are_clamped(self):
        traits = self._traits(
            "interject:\n"
            "  chance_scale: 9\n"
            "  fx_multiplier: 0.01\n"
            "  cooldown_scale: -3\n"
            "  burst_scale: 9\n"
            "typing:\n"
            "  speed: 9\n"
        )
        self.assertEqual(traits.chance_scale, persona.TRAIT_MAX)
        self.assertEqual(traits.fx_multiplier, persona.TRAIT_MIN)
        self.assertEqual(traits.cooldown_scale, persona.TRAIT_MIN)
        self.assertEqual(traits.burst_scale, persona.BURST_MAX)
        self.assertEqual(traits.typing_speed, persona.SPEED_MAX)
        self.assertEqual(
            self._traits("interject:\n  burst_scale: 0.1\n").burst_scale, persona.BURST_MIN
        )
        self.assertEqual(
            self._traits("typing:\n  speed: 0.1\n").typing_speed, persona.SPEED_MIN
        )

    def test_non_finite_numbers_mean_defaults(self):
        traits = self._traits(
            "interject:\n  chance_scale: .nan\n  fx_multiplier: .inf\n  burst_scale: .nan\n"
            "typing:\n  speed: -.inf\n"
        )
        self.assertEqual(traits, persona.PersonaTraits())

    def test_only_typing_block(self):
        traits = self._traits("typing:\n  speed: 0.8\n")
        self.assertEqual(traits.typing_speed, 0.8)
        self.assertIsNone(traits.fx_multiplier)

    def test_mtime_cache_reloads(self):
        path = self.root / "tiredfx" / "persona.yaml"
        self._traits("typing:\n  speed: 0.8\n")
        self.assertEqual(persona.load_traits(self.root, "tiredfx").typing_speed, 0.8)

        mtime = path.stat().st_mtime_ns
        path.write_text("typing:\n  speed: 1.5\n", encoding="utf-8")
        os.utime(path, ns=(mtime + 10 ** 9, mtime + 10 ** 9))
        self.assertEqual(persona.load_traits(self.root, "tiredfx").typing_speed, 1.5)

    def test_numbers_never_reach_the_prompt(self):
        self._traits("interject:\n  chance_scale: 1.7\n  fx_multiplier: 2.2\n")
        (self.root / "tiredfx" / "character.md").write_text("CHAR-ACTER\n", encoding="utf-8")
        prompt = persona.build_system_prompt(self.root, persona="tiredfx")
        self.assertIn("CHAR-ACTER", prompt)
        self.assertNotIn("chance_scale", prompt)
        self.assertNotIn("2.2", prompt)


if __name__ == "__main__":
    unittest.main()
