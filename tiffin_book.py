#!/usr/bin/env python3
"""tiffin-book: zero-dependency CLI for small tiffin kitchens.

Tiffin subscribers want a default daily meal but also want to skip a day,
swap a dish, or add an extra roti. Kitchens usually track this in chat
messages and a notebook. tiffin-book keeps the menu, each subscriber's
default weekly plan, and per-date changes, then answers two questions:
what do I cook for each subscriber on this date, and what is this
subscriber's itemized bill for the month?

Python 3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import re
import sys
import tempfile
from datetime import date

DEFAULT_DB = "tiffin_book.json"
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class TiffinError(Exception):
    """User-facing error (bad input, unknown subscriber, ...)."""


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise TiffinError("bad date %r (expected YYYY-MM-DD)" % (text,))


def parse_month(text):
    if not re.fullmatch(r"\d{4}-\d{2}", text or ""):
        raise TiffinError("bad month %r (expected YYYY-MM)" % (text,))
    year, month = int(text[:4]), int(text[5:])
    if not 1 <= month <= 12:
        raise TiffinError("bad month %r (expected YYYY-MM)" % (text,))
    return year, month


def parse_amount(text):
    """Rupees string -> integer paise, positive, max 2 decimals."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise TiffinError("bad amount %r (e.g. 90 or 90.50)" % text)
    rupees, _, frac = text.partition(".")
    paise = int(rupees) * 100 + int((frac + "00")[:2])
    if paise <= 0:
        raise TiffinError("amount must be positive")
    return paise


def parse_qty(text):
    try:
        qty = int(text)
    except (TypeError, ValueError):
        raise TiffinError("bad quantity %r (whole number)" % (text,))
    if qty <= 0:
        raise TiffinError("quantity must be at least 1")
    return qty


def parse_days(text):
    """'mon,tue,wed' -> sorted list of weekday numbers (Mon=0)."""
    out = set()
    for part in (text or "").lower().split(","):
        part = part.strip()
        if part == "weekdays":
            out.update(range(5))
        elif part == "all":
            out.update(range(7))
        elif part in DAY_NAMES:
            out.add(DAY_NAMES.index(part))
        else:
            raise TiffinError(
                "bad day %r (use mon..sun, weekdays or all)" % part)
    if not out:
        raise TiffinError("at least one delivery day is required")
    return sorted(out)


def format_paise(paise):
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    s = str(paise // 100)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return "%s%s.%02d" % (sign, s, paise % 100)


class Book:
    def __init__(self, menu=None, subscribers=None, changes=None):
        self.menu = menu or {}
        self.subscribers = subscribers or {}
        # changes[name][iso_date] = {"mode": None|"skip"|"swap",
        #                            "item": str, "extras": {item: qty}}
        self.changes = changes or {}

    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return cls(data["menu"], data["subscribers"], data["changes"])
        except (ValueError, KeyError, TypeError) as exc:
            raise TiffinError("cannot read %s: %s" % (path, exc))

    def save(self, path):
        data = {"menu": self.menu, "subscribers": self.subscribers,
                "changes": self.changes}
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # menu
    def add_menu_item(self, item, price):
        item = (item or "").strip()
        if not item:
            raise TiffinError("item name cannot be empty")
        if item in self.menu:
            raise TiffinError("menu item %r already exists (use: menu price)"
                              % item)
        self.menu[item] = price

    def set_price(self, item, price):
        self.need_item(item)
        self.menu[item] = price

    def need_item(self, item):
        if item not in self.menu:
            raise TiffinError("unknown menu item %r (add it first)" % (item,))

    # subscribers
    def add_subscriber(self, name, item, days, start):
        name = (name or "").strip()
        if not name:
            raise TiffinError("subscriber name cannot be empty")
        if name in self.subscribers:
            raise TiffinError("subscriber %r already exists" % name)
        self.need_item(item)
        self.subscribers[name] = {"item": item, "days": days,
                                  "start": start.isoformat(), "end": None}
        self.changes[name] = {}

    def need_sub(self, name):
        if name not in self.subscribers:
            raise TiffinError("unknown subscriber %r (add it first)"
                              % (name,))
        return self.subscribers[name]

    def stop_subscriber(self, name, when):
        sub = self.need_sub(name)
        if when < parse_date(sub["start"]):
            raise TiffinError("stop date is before the start date")
        sub["end"] = when.isoformat()

    def is_scheduled(self, name, day):
        sub = self.need_sub(name)
        if day < parse_date(sub["start"]):
            return False
        if sub["end"] and day > parse_date(sub["end"]):
            return False
        return day.weekday() in sub["days"]

    def _entry(self, name, day):
        return self.changes[name].setdefault(
            day.isoformat(), {"mode": None, "item": None, "extras": {}})

    def skip(self, name, day):
        self.need_sub(name)
        if not self.is_scheduled(name, day):
            raise TiffinError("%s has no delivery on %s" % (name, day))
        entry = self._entry(name, day)
        entry["mode"], entry["item"] = "skip", None

    def swap(self, name, day, item):
        self.need_sub(name)
        self.need_item(item)
        if not self.is_scheduled(name, day):
            raise TiffinError("%s has no delivery on %s" % (name, day))
        entry = self._entry(name, day)
        entry["mode"], entry["item"] = "swap", item

    def extra(self, name, day, item, qty):
        self.need_sub(name)
        self.need_item(item)
        entry = self._entry(name, day)
        entry["extras"][item] = entry["extras"].get(item, 0) + qty

    def clear(self, name, day):
        self.need_sub(name)
        if self.changes[name].pop(day.isoformat(), None) is None:
            raise TiffinError("no changes recorded for %s on %s"
                              % (name, day))

    # reads
    def day_sheet(self, day):
        """Kitchen sheet rows for one date: (name, what, extras list)."""
        rows = []
        for name in sorted(self.subscribers):
            entry = self.changes[name].get(day.isoformat())
            scheduled = self.is_scheduled(name, day)
            extras = sorted((entry or {}).get("extras", {}).items())
            if not scheduled and not extras:
                continue
            if not scheduled:
                what = "(no meal today)"
            elif entry and entry["mode"] == "skip":
                what = "SKIP"
            elif entry and entry["mode"] == "swap":
                what = entry["item"] + " (swapped)"
            else:
                what = self.subscribers[name]["item"]
            rows.append((name, what, extras))
        return rows

    def bill(self, name, year, month):
        """Itemized bill lines for a month. Each line:
        (date, description, qty, amount_paise)."""
        self.need_sub(name)
        lines = []
        last = calendar.monthrange(year, month)[1]
        for d in range(1, last + 1):
            day = date(year, month, d)
            entry = self.changes[name].get(day.isoformat())
            if self.is_scheduled(name, day):
                if entry and entry["mode"] == "skip":
                    lines.append((day, "skipped", 0, 0))
                else:
                    item = (entry["item"] if entry and entry["mode"] == "swap"
                            else self.subscribers[name]["item"])
                    label = item + (" (swap)" if entry and entry["mode"]
                                    == "swap" else "")
                    lines.append((day, label, 1, self.menu[item]))
            if entry:
                for item, qty in sorted(entry["extras"].items()):
                    lines.append((day, item + " (extra)", qty,
                                  self.menu[item] * qty))
        return lines


def month_totals(lines):
    meals = sum(1 for _, desc, _, _ in lines
                if desc != "skipped" and not desc.endswith("(extra)"))
    skipped = sum(1 for _, desc, _, _ in lines if desc == "skipped")
    extras = sum(amt for _, desc, _, amt in lines if desc.endswith("(extra)"))
    total = sum(amt for _, _, _, amt in lines)
    return {"meals": meals, "skipped": skipped, "extras": extras,
            "total": total}


# commands
def cmd_menu_add(book, args):
    book.add_menu_item(args.item, parse_amount(args.price))
    print("added %s at %s" % (args.item, args.price))


def cmd_menu_price(book, args):
    book.set_price(args.item, parse_amount(args.price))
    print("%s now %s (applies to bills generated from now on)" % (
        args.item, format_paise(book.menu[args.item])))


def cmd_menu_list(book, args):
    if not book.menu:
        print("menu is empty")
    for item in sorted(book.menu):
        print("  %-24s %10s" % (item, format_paise(book.menu[item])))


def cmd_sub_add(book, args):
    start = parse_date(args.start) if args.start else date.today()
    book.add_subscriber(args.name, args.item, parse_days(args.days), start)
    print("added subscriber %s: %s on %s from %s" % (
        args.name, args.item,
        ",".join(DAY_NAMES[d] for d in book.subscribers[args.name]["days"]),
        start.isoformat()))


def cmd_sub_stop(book, args):
    book.stop_subscriber(args.name, parse_date(args.date))
    print("%s stops after %s" % (args.name, args.date))


def cmd_skip(book, args):
    book.skip(args.name, parse_date(args.date))
    print("%s: skip on %s" % (args.name, args.date))


def cmd_swap(book, args):
    book.swap(args.name, parse_date(args.date), args.item)
    print("%s: %s on %s instead of the default" % (
        args.name, args.item, args.date))


def cmd_extra(book, args):
    book.extra(args.name, parse_date(args.date), args.item,
               parse_qty(args.qty))
    print("%s: +%s x %s on %s" % (args.name, args.qty, args.item, args.date))


def cmd_clear(book, args):
    book.clear(args.name, parse_date(args.date))
    print("%s: changes for %s cleared" % (args.name, args.date))


def cmd_day(book, args):
    day = parse_date(args.date)
    rows = book.day_sheet(day)
    print("kitchen sheet for %s (%s):" % (day.isoformat(),
                                          DAY_NAMES[day.weekday()]))
    if not rows:
        print("  nothing to cook")
    for name, what, extras in rows:
        line = "  %-14s %s" % (name, what)
        if extras:
            line += "  + " + ", ".join("%d x %s" % (q, i) for i, q in extras)
        print(line)
    cooking = sum(1 for _, w, _ in rows if w not in ("SKIP", "(no meal today)"))
    print("meals to cook: %d" % cooking)


def render_bill(book, name, year, month):
    lines = book.bill(name, year, month)
    out = ["bill for %s, %04d-%02d:" % (name, year, month)]
    for day, desc, qty, amt in lines:
        out.append("  %s  %-26s %3s %10s" % (
            day.isoformat(), desc, qty if amt else "-", format_paise(amt)))
    t = month_totals(lines)
    out.append("meals billed: %d, skipped days (not billed): %d, "
               "extras: %s" % (t["meals"], t["skipped"],
                               format_paise(t["extras"])))
    out.append("total due: %s" % format_paise(t["total"]))
    return out, lines


def cmd_bill(book, args):
    year, month = parse_month(args.month)
    out, lines = render_bill(book, args.name, year, month)
    print("\n".join(out))
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "description", "qty", "amount"])
            for day, desc, qty, amt in lines:
                writer.writerow([day.isoformat(), desc, qty,
                                 format_paise(amt)])
        print("wrote %s" % args.csv)


def build_parser():
    p = argparse.ArgumentParser(
        prog="tiffin_book.py",
        description="Menu, subscriber plans, daily changes and monthly "
                    "bills for a small tiffin kitchen.")
    p.add_argument("--db", default=DEFAULT_DB,
                   help="data file (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    menu = sub.add_parser("menu", help="manage the menu")
    msub = menu.add_subparsers(dest="mcmd", required=True)
    m = msub.add_parser("add", help="add a dish")
    m.add_argument("item")
    m.add_argument("--price", required=True)
    m.set_defaults(func=cmd_menu_add)
    m = msub.add_parser("price", help="change a dish price")
    m.add_argument("item")
    m.add_argument("price")
    m.set_defaults(func=cmd_menu_price)
    m = msub.add_parser("list", help="list the menu")
    m.set_defaults(func=cmd_menu_list)

    s = sub.add_parser("subscriber", help="add a subscriber")
    s.add_argument("name")
    s.add_argument("--item", required=True, help="default daily dish")
    s.add_argument("--days", required=True,
                   help="mon,tue,... or weekdays or all")
    s.add_argument("--start", help="first delivery date (default today)")
    s.set_defaults(func=cmd_sub_add)

    s = sub.add_parser("stop", help="end a subscription after a date")
    s.add_argument("name")
    s.add_argument("date")
    s.set_defaults(func=cmd_sub_stop)

    s = sub.add_parser("skip", help="skip a scheduled day")
    s.add_argument("name")
    s.add_argument("date")
    s.set_defaults(func=cmd_skip)

    s = sub.add_parser("swap", help="swap the dish on a scheduled day")
    s.add_argument("name")
    s.add_argument("date")
    s.add_argument("item")
    s.set_defaults(func=cmd_swap)

    s = sub.add_parser("extra", help="add an extra item on a date")
    s.add_argument("name")
    s.add_argument("date")
    s.add_argument("item")
    s.add_argument("--qty", default="1")
    s.set_defaults(func=cmd_extra)

    s = sub.add_parser("clear", help="remove all changes for a date")
    s.add_argument("name")
    s.add_argument("date")
    s.set_defaults(func=cmd_clear)

    s = sub.add_parser("day", help="kitchen sheet for a date")
    s.add_argument("date")
    s.set_defaults(func=cmd_day)

    s = sub.add_parser("bill", help="itemized monthly bill")
    s.add_argument("name")
    s.add_argument("month", help="YYYY-MM")
    s.add_argument("--csv", help="also write the lines to a CSV file")
    s.set_defaults(func=cmd_bill)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        book = Book.load(args.db)
        args.func(book, args)
        book.save(args.db)
    except TiffinError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
