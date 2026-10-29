"""Play-together is a per-game switch. Online titles start off."""

import tempfile
import unittest
from pathlib import Path

from src.gaming.boards import BoardTable
from src.gaming.companion import GameCompanion
from src.gaming.player2 import PlayerTwo
from src.gaming.safety import WARNING_TEXT, GameSafety
from src.gaming.steam import SteamLibrary, parse_vdf


ONLINE = {"appid": "730", "name": "Counter-Strike 2", "installed_path": ""}
LOCAL = {"appid": "413150", "name": "Stardew Valley", "installed_path": ""}


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.safety = GameSafety(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_online_title_defaults_off_and_is_not_hard_blocked(self):
        report = self.safety.assess(ONLINE)
        self.assertFalse(report["blocked"])
        self.assertTrue(report["online"])
        self.assertFalse(report["input_enabled"])
        self.assertFalse(report["input_allowed"])
        self.assertIn("anti-cheat", report["warning"].lower())

    def test_one_time_warning_then_private_room_toggle(self):
        first = self.safety.set_input(ONLINE["appid"], True, ONLINE)
        self.assertFalse(first["ok"])
        self.assertTrue(first["needs_warning"])
        self.assertEqual(first["warning"], WARNING_TEXT)
        self.assertFalse(self.safety.input_enabled(ONLINE["appid"], ONLINE))

        self.safety.acknowledge_warning()
        second = self.safety.set_input(ONLINE["appid"], True, ONLINE)
        self.assertTrue(second["ok"])
        self.assertTrue(second["safety"]["input_allowed"])

        third = self.safety.set_input(ONLINE["appid"], True, ONLINE)
        self.assertTrue(third["ok"])
        self.assertNotIn("shows once", third["safety"]["warning"])

    def test_player_two_waits_for_warning_then_drives_a_private_room(self):
        pad_holder = {}

        class Pad:
            def __init__(self):
                self.events = []
                self.connected = False

            def connect(self):
                self.connected = True

            def disconnect(self):
                self.connected = False

            def button(self, name, pressed):
                self.events.append((name, pressed))

            def move(self, axis, value):
                self.events.append((axis, value))

            def key(self, name):
                self.events.append(("key", name))

        pad = Pad()
        player = PlayerTwo(self.root, pad=pad, safety=self.safety)
        blocked = player.set_enabled(True, ONLINE)
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["needs_warning"])
        self.assertFalse(pad.connected)

        self.safety.acknowledge_warning()
        allowed = player.set_enabled(True, ONLINE)
        self.assertTrue(allowed["ok"])
        acted = player.act(ONLINE, {"type": "button", "name": "a", "pressed": True})
        self.assertTrue(acted["ok"])
        self.assertEqual(pad.events, [("a", True)])

    def test_companion_uses_the_same_switch(self):
        companion = GameCompanion(self.root, safety=self.safety)
        off = companion.set_enabled(True, LOCAL)
        self.assertFalse(off["ok"])
        self.safety.acknowledge_warning()
        on = companion.set_enabled(True, LOCAL)
        self.assertTrue(on["ok"])
        self.assertIn("watching", on["indicator"].lower())
        coached = companion.coach(LOCAL, frame_text="a quiet farm plot")
        self.assertTrue(coached["ok"])
        self.assertIn("farm", coached["line"])


class SteamAndBoardTests(unittest.TestCase):
    def test_vdf_and_manifests(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        steamapps = root / "steamapps"
        steamapps.mkdir()
        (steamapps / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n"0"\n{\n"path"\t"' + str(root).replace("\\", "/") + '"\n}\n}\n',
            encoding="utf-8",
        )
        (steamapps / "common").mkdir()
        (steamapps / "appmanifest_413150.acf").write_text(
            '"AppState"\n{\n"appid"\t"413150"\n"name"\t"Stardew Valley"\n"installdir"\t"Stardew Valley"\n}\n',
            encoding="utf-8",
        )
        library = SteamLibrary(roots=[root])
        games = library.installed_games()
        self.assertEqual(games[0]["appid"], "413150")
        self.assertTrue(games[0]["launch_url"].endswith("413150"))
        parsed = parse_vdf('"AppState" { "name" "Demo" }')
        self.assertEqual(parsed["AppState"]["name"], "Demo")
        missing = library.owned_games()
        self.assertFalse(missing["ok"])
        self.assertNotIn("super-secret", str(missing))
        tmp.cleanup()

    def test_word_and_board(self):
        table = BoardTable()
        state = table.new_word()
        self.assertIn("_", state["mask"])
        for letter in table.word:
            table.guess_letter(letter)
        self.assertTrue(table.word_state()["done"])
        move = table.tic_move(0)
        self.assertTrue(move["ok"])
        self.assertEqual(move["board"][0], "X")
        self.assertEqual(move["board"].count("O"), 1)


if __name__ == "__main__":
    unittest.main()
