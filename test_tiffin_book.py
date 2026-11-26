#!/usr/bin/env python3
"""Tests for tiffin_book. Run: python3 -m unittest test_tiffin_book -v

No test depends on the local clock or timezone: every date is explicit.
November 2026 starts on a Sunday (Mon 2, Thu 5, ..., Mon 30).
"""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date

import tiffin_book as tb


def make_book():
    book = tb.Book()
    book.add_menu_item("dal-rice", 9000)
    book.add_menu_item("rajma-chawal", 11000)
    book.add_menu_item("roti", 800)
    book.add_subscriber("Meera", "dal-rice", tb.parse_days("mon,thu"),
                        date(2026, 11, 1))
    return book


class ParseTest(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(tb.parse_amount("90"), 9000)
        self.assertEqual(tb.parse_amount("7.5"), 750)
        for bad in ("0", "abc", "1.234", "-5", "1,000", ""):
            with self.assertRaises(tb.TiffinError, msg=bad):
                tb.parse_amount(bad)

    def test_parse_qty(self):
        self.assertEqual(tb.parse_qty("4"), 4)
        for bad in ("0", "-1", "x", "1.5"):
            with self.assertRaises(tb.TiffinError, msg=bad):
                tb.parse_qty(bad)

    def test_parse_days(self):
        self.assertEqual(tb.parse_days("mon,thu"), [0, 3])
        self.assertEqual(tb.parse_days("weekdays"), [0, 1, 2, 3, 4])
        self.assertEqual(tb.parse_days("all"), list(range(7)))
        self.assertEqual(tb.parse_days("sun, MON"), [0, 6])
        for bad in ("", "funday", "mon,xyz"):
            with self.assertRaises(tb.TiffinError, msg=bad):
                tb.parse_days(bad)

    def test_parse_date_and_month(self):
        self.assertEqual(tb.parse_date("2026-11-17"), date(2026, 11, 17))
        self.assertEqual(tb.parse_month("2026-11"), (2026, 11))
        for bad in ("17-11-2026", "2026-13-01", "nov"):
            with self.assertRaises(tb.TiffinError, msg=bad):
                tb.parse_date(bad)
        for bad in ("2026-13", "2026-1", "2026-11-01", ""):
            with self.assertRaises(tb.TiffinError, msg=bad):
                tb.parse_month(bad)

    def test_format_paise(self):
        self.assertEqual(tb.format_paise(9000), "90.00")
        self.assertEqual(tb.format_paise(123456789), "12,34,567.89")
        self.assertEqual(tb.format_paise(-250), "-2.50")


class MenuTest(unittest.TestCase):
    def test_duplicate_and_empty_items(self):
        book = make_book()
        with self.assertRaises(tb.TiffinError):
            book.add_menu_item("dal-rice", 100)
        with self.assertRaises(tb.TiffinError):
            book.add_menu_item("  ", 100)

    def test_price_change_applies_to_later_bills(self):
        book = make_book()
        before = tb.month_totals(book.bill("Meera", 2026, 11))["total"]
        book.set_price("dal-rice", 10000)
        after = tb.month_totals(book.bill("Meera", 2026, 11))["total"]
        self.assertEqual(before, 9 * 9000)
        self.assertEqual(after, 9 * 10000)

    def test_unknown_item_rejected(self):
        book = make_book()
        with self.assertRaises(tb.TiffinError):
            book.set_price("ghost", 100)
        with self.assertRaises(tb.TiffinError):
            book.swap("Meera", date(2026, 11, 2), "ghost")


class SubscriberTest(unittest.TestCase):
    def test_schedule_follows_weekdays(self):
        book = make_book()
        self.assertTrue(book.is_scheduled("Meera", date(2026, 11, 2)))   # Mon
        self.assertTrue(book.is_scheduled("Meera", date(2026, 11, 5)))   # Thu
        self.assertFalse(book.is_scheduled("Meera", date(2026, 11, 3)))  # Tue

    def test_start_and_stop_dates(self):
        book = make_book()
        self.assertFalse(book.is_scheduled("Meera", date(2026, 10, 29)))
        book.stop_subscriber("Meera", date(2026, 11, 12))
        self.assertTrue(book.is_scheduled("Meera", date(2026, 11, 12)))
        self.assertFalse(book.is_scheduled("Meera", date(2026, 11, 16)))
        self.assertEqual(tb.month_totals(book.bill("Meera", 2026, 11))
                         ["meals"], 4)

    def test_duplicate_subscriber_and_bad_item(self):
        book = make_book()
        with self.assertRaises(tb.TiffinError):
            book.add_subscriber("Meera", "dal-rice", [0], date(2026, 11, 1))
        with self.assertRaises(tb.TiffinError):
            book.add_subscriber("New", "ghost", [0], date(2026, 11, 1))

    def test_stop_before_start_rejected(self):
        book = make_book()
        with self.assertRaises(tb.TiffinError):
            book.stop_subscriber("Meera", date(2026, 10, 1))

    def test_unknown_subscriber(self):
        with self.assertRaises(tb.TiffinError):
            make_book().skip("Ghost", date(2026, 11, 2))


class ChangeTest(unittest.TestCase):
    def test_skip_removes_charge(self):
        book = make_book()
        book.skip("Meera", date(2026, 11, 12))
        t = tb.month_totals(book.bill("Meera", 2026, 11))
        self.assertEqual(t["skipped"], 1)
        self.assertEqual(t["meals"], 8)
        self.assertEqual(t["total"], 8 * 9000)

    def test_swap_uses_swapped_price(self):
        book = make_book()
        book.swap("Meera", date(2026, 11, 16), "rajma-chawal")
        t = tb.month_totals(book.bill("Meera", 2026, 11))
        self.assertEqual(t["total"], 8 * 9000 + 11000)

    def test_skip_then_swap_replaces_earlier_choice(self):
        book = make_book()
        day = date(2026, 11, 16)
        book.skip("Meera", day)
        book.swap("Meera", day, "rajma-chawal")
        self.assertEqual(book.day_sheet(day)[0][1], "rajma-chawal (swapped)")
        book.skip("Meera", day)
        self.assertEqual(book.day_sheet(day)[0][1], "SKIP")

    def test_skip_or_swap_on_unscheduled_day_rejected(self):
        book = make_book()
        with self.assertRaises(tb.TiffinError):
            book.skip("Meera", date(2026, 11, 3))
        with self.assertRaises(tb.TiffinError):
            book.swap("Meera", date(2026, 11, 3), "roti")

    def test_extras_accumulate_and_bill(self):
        book = make_book()
        book.extra("Meera", date(2026, 11, 16), "roti", 2)
        book.extra("Meera", date(2026, 11, 16), "roti", 2)
        t = tb.month_totals(book.bill("Meera", 2026, 11))
        self.assertEqual(t["extras"], 4 * 800)
        self.assertEqual(t["total"], 9 * 9000 + 4 * 800)

    def test_extra_allowed_on_unscheduled_day(self):
        book = make_book()
        day = date(2026, 11, 3)  # Tuesday, no meal
        book.extra("Meera", day, "roti", 3)
        sheet = book.day_sheet(day)
        self.assertEqual(sheet, [("Meera", "(no meal today)",
                                  [("roti", 3)])])

    def test_clear(self):
        book = make_book()
        day = date(2026, 11, 12)
        book.skip("Meera", day)
        book.clear("Meera", day)
        self.assertEqual(tb.month_totals(book.bill("Meera", 2026, 11))
                         ["skipped"], 0)
        with self.assertRaises(tb.TiffinError):
            book.clear("Meera", day)


class SheetAndBillTest(unittest.TestCase):
    def test_day_sheet_default_and_empty(self):
        book = make_book()
        self.assertEqual(book.day_sheet(date(2026, 11, 2)),
                         [("Meera", "dal-rice", [])])
        self.assertEqual(book.day_sheet(date(2026, 11, 3)), [])

    def test_bill_line_count_and_order(self):
        book = make_book()
        lines = book.bill("Meera", 2026, 11)
        self.assertEqual([d.day for d, _, _, _ in lines],
                         [2, 5, 9, 12, 16, 19, 23, 26, 30])

    def test_bill_for_month_before_start_is_empty(self):
        book = make_book()
        self.assertEqual(book.bill("Meera", 2026, 10), [])

    def test_leap_february_length(self):
        book = tb.Book()
        book.add_menu_item("meal", 1000)
        book.add_subscriber("Q", "meal", tb.parse_days("all"),
                            date(2028, 1, 1))
        self.assertEqual(len(book.bill("Q", 2028, 2)), 29)
        self.assertEqual(len(book.bill("Q", 2027, 2)), 0)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "book.json")

    def test_roundtrip(self):
        book = make_book()
        book.skip("Meera", date(2026, 11, 12))
        book.extra("Meera", date(2026, 11, 16), "roti", 2)
        book.save(self.path)
        loaded = tb.Book.load(self.path)
        self.assertEqual(tb.month_totals(loaded.bill("Meera", 2026, 11)),
                         tb.month_totals(book.bill("Meera", 2026, 11)))

    def test_missing_file_is_empty_book(self):
        book = tb.Book.load(os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(book.menu, {})

    def test_corrupt_file_is_user_error(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ nope")
        with self.assertRaises(tb.TiffinError):
            tb.Book.load(self.path)

    def test_save_is_atomic_replace(self):
        make_book().save(self.path)
        self.assertEqual(os.listdir(self.tmp.name), ["book.json"])
        with open(self.path, encoding="utf-8") as fh:
            json.load(fh)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "book.json")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tb.main(["--db", self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def seed(self):
        self.run_cli("menu", "add", "dal-rice", "--price", "90")
        self.run_cli("menu", "add", "rajma-chawal", "--price", "110")
        self.run_cli("menu", "add", "roti", "--price", "8")
        self.run_cli("subscriber", "Meera", "--item", "dal-rice",
                     "--days", "mon,thu", "--start", "2026-11-01")

    def test_full_flow(self):
        self.seed()
        self.assertEqual(self.run_cli("skip", "Meera", "2026-11-12")[0], 0)
        self.run_cli("swap", "Meera", "2026-11-16", "rajma-chawal")
        self.run_cli("extra", "Meera", "2026-11-16", "roti", "--qty", "4")
        code, out, _ = self.run_cli("day", "2026-11-16")
        self.assertEqual(code, 0)
        self.assertIn("Meera          rajma-chawal (swapped)  + 4 x roti",
                      out)
        self.assertIn("meals to cook: 1", out)
        code, out, _ = self.run_cli("bill", "Meera", "2026-11")
        self.assertIn("2026-11-12  skipped", out)
        self.assertIn("meals billed: 8, skipped days (not billed): 1, "
                      "extras: 32.00", out)
        self.assertIn("total due: 772.00", out)

    def test_menu_list_and_price(self):
        self.seed()
        code, out, _ = self.run_cli("menu", "list")
        self.assertIn("dal-rice", out)
        code, out, _ = self.run_cli("menu", "price", "dal-rice", "95")
        self.assertIn("now 95.00", out)

    def test_error_exit_codes(self):
        self.seed()
        for argv, text in (
                (("skip", "Meera", "2026-11-03"), "no delivery"),
                (("skip", "Ghost", "2026-11-02"), "unknown subscriber"),
                (("bill", "Meera", "2026-13"), "bad month"),
                (("extra", "Meera", "2026-11-02", "roti", "--qty", "0"),
                 "at least 1"),
                (("menu", "add", "dal-rice", "--price", "5"), "already")):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(text, err)

    def test_failed_command_does_not_change_file(self):
        self.seed()
        with open(self.db, encoding="utf-8") as fh:
            before = fh.read()
        self.run_cli("skip", "Meera", "2026-11-03")
        with open(self.db, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_bill_csv(self):
        self.seed()
        self.run_cli("extra", "Meera", "2026-11-16", "roti", "--qty", "2")
        path = os.path.join(self.tmp.name, "bill.csv")
        code, out, _ = self.run_cli("bill", "Meera", "2026-11", "--csv", path)
        self.assertEqual(code, 0)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["date", "description", "qty", "amount"])
        self.assertEqual(rows[1], ["2026-11-02", "dal-rice", "1", "90.00"])
        self.assertIn(["2026-11-16", "roti (extra)", "2", "16.00"], rows)


if __name__ == "__main__":
    unittest.main()
