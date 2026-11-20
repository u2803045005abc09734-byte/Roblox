import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts_store import (  # noqa: E402
    AccountStore,
    DuplicateAccountError,
    StoreCorruptError,
    StoreError,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = AccountStore(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- unbegrenzt ----------------------------------------------------
    def test_no_account_limit_individual_saves(self):
        for i in range(60):
            self.store.add(f"Acc {i}")
        self.assertEqual(len(self.store), 60)
        self.assertEqual(len(AccountStore(self.dir)), 60)

    def test_no_account_limit_large_batch(self):
        with self.store.batch():
            for i in range(3000):
                self.store.add(f"Acc {i:04d}")
        self.assertEqual(len(self.store), 3000)
        reloaded = AccountStore(self.dir)
        self.assertEqual(len(reloaded), 3000)
        self.assertEqual(len({a.id for a in reloaded.view()}), 3000)

    def test_view_is_fast_with_many_accounts(self):
        with self.store.batch():
            for i in range(5000):
                self.store.add(f"Acc {i:04d}")
        start = time.perf_counter()
        for _ in range(20):
            self.store.view(search="42", sort="alpha")
        self.assertLess(time.perf_counter() - start, 3.0)

    # ---- Persistenz / Sicherheit ----------------------------------------
    def test_data_survives_reload(self):
        a = self.store.add("Main")
        self.store.set_identity(a.id, "RealName", 12345)
        self.store.set_favorite(a.id, True)
        b = AccountStore(self.dir).get(a.id)
        self.assertEqual((b.name, b.username, b.user_id, b.favorite), ("Main", "RealName", 12345, True))

    def test_corrupt_main_file_recovers_from_backup(self):
        a = self.store.add("One")
        self.store.add("Two")  # zweites Speichern erzeugt die .bak mit "One"
        (self.dir / "accounts.json").write_text("{kaputt", encoding="utf-8")
        recovered = AccountStore(self.dir)
        self.assertIn(a.id, {x.id for x in recovered.view()})
        self.assertTrue(list(self.dir.glob("accounts.json.corrupt-*")))

    def test_both_files_corrupt_raises_and_keeps_files(self):
        self.store.add("One")
        self.store.add("Two")
        (self.dir / "accounts.json").write_text("{x", encoding="utf-8")
        (self.dir / "accounts.json.bak").write_text("{y", encoding="utf-8")
        with self.assertRaises(StoreCorruptError):
            AccountStore(self.dir)
        self.assertEqual((self.dir / "accounts.json").read_text(encoding="utf-8"), "{x")
        self.assertEqual((self.dir / "accounts.json.bak").read_text(encoding="utf-8"), "{y")

    def test_no_secrets_in_file(self):
        a = self.store.add("Main")
        self.store.set_identity(a.id, "RealName", 1)
        text = (self.dir / "accounts.json").read_text(encoding="utf-8").lower()
        for forbidden in ("cookie", "token", "password", "roblosecurity"):
            self.assertNotIn(forbidden, text)

    def test_unknown_fields_in_file_are_ignored(self):
        a = self.store.add("Main")
        raw = json.loads((self.dir / "accounts.json").read_text(encoding="utf-8"))
        raw["accounts"][0]["future_field"] = "x"
        (self.dir / "accounts.json").write_text(json.dumps(raw), encoding="utf-8")
        self.assertIsNotNone(AccountStore(self.dir).get(a.id))

    # ---- Verwaltung -------------------------------------------------------
    def test_duplicate_roblox_id_does_not_overwrite(self):
        a = self.store.add("A")
        b = self.store.add("B")
        self.store.set_identity(a.id, "Same", 777)
        with self.assertRaises(DuplicateAccountError) as ctx:
            self.store.set_identity(b.id, "Same", 777)
        self.assertEqual(ctx.exception.existing.id, a.id)
        self.assertEqual(self.store.get(b.id).user_id, 0)
        self.assertEqual(self.store.get(a.id).user_id, 777)

    def test_same_identity_can_be_reconfirmed(self):
        a = self.store.add("A")
        self.store.set_identity(a.id, "Same", 777)
        self.store.set_identity(a.id, "Same", 777)

    def test_remove_only_removes_that_account_and_profile(self):
        a = self.store.add("A")
        b = self.store.add("B")
        (self.store.profile_dir(a.id) / "session.dat").write_text("x")
        (self.store.profile_dir(b.id) / "session.dat").write_text("y")
        self.store.remove(a.id)
        self.assertIsNone(self.store.get(a.id))
        self.assertFalse(self.store.profile_dir(a.id).exists())
        self.assertTrue((self.store.profile_dir(b.id) / "session.dat").exists())
        self.assertIsNotNone(AccountStore(self.dir).get(b.id))

    def test_remove_unknown_id_is_harmless(self):
        a = self.store.add("A")
        self.store.remove("../../etc")
        self.assertEqual(len(self.store), 1)
        self.assertTrue(self.store.profile_dir(a.id).exists())

    def test_unknown_account_operations_raise(self):
        with self.assertRaises(StoreError):
            self.store.rename("nope", "x")

    def test_custom_avatar_copy(self):
        a = self.store.add("A")
        src = self.dir / "pic.png"
        src.write_bytes(b"\x89PNG")
        name = self.store.set_custom_avatar(a.id, src)
        self.assertTrue((self.store.avatars_dir / name).exists())
        self.assertEqual(self.store.get(a.id).avatar, name)
        self.store.remove(a.id)
        self.assertFalse((self.store.avatars_dir / name).exists())

    # ---- Suche / Sortierung -------------------------------------------------
    def test_search_matches_name_and_username_case_insensitive(self):
        a = self.store.add("Alpha")
        b = self.store.add("Beta")
        self.store.set_identity(b.id, "ZetaUser", 5)
        self.assertEqual([x.id for x in self.store.view(search="alp")], [a.id])
        self.assertEqual([x.id for x in self.store.view(search="ZETAU")], [b.id])
        self.assertEqual(self.store.view(search="gibtsnicht"), [])

    def test_sort_alpha_and_favorites_first(self):
        for n in ("charlie", "Alpha", "bravo"):
            self.store.add(n)
        self.assertEqual([a.name for a in self.store.view(sort="alpha")], ["Alpha", "bravo", "charlie"])
        charlie = next(a for a in self.store.view() if a.name == "charlie")
        self.store.set_favorite(charlie.id, True)
        self.assertEqual(self.store.view(sort="alpha")[0].name, "charlie")
        self.assertEqual(
            [a.name for a in self.store.view(sort="alpha", favorites_first=False)],
            ["Alpha", "bravo", "charlie"],
        )

    def test_sort_recent_and_recent_list(self):
        a = self.store.add("A")
        b = self.store.add("B")
        c = self.store.add("C")
        self.store.mark_used(a.id)
        time.sleep(0.01)
        self.store.mark_used(c.id)
        self.assertEqual([x.id for x in self.store.view(sort="recent")][:2], [c.id, a.id])
        self.assertEqual([x.id for x in self.store.recent()], [c.id, a.id])
        self.assertNotIn(b.id, [x.id for x in self.store.recent()])

    def test_invalid_sort_mode(self):
        with self.assertRaises(ValueError):
            self.store.view(sort="bogus")


if __name__ == "__main__":
    unittest.main()
