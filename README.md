# tiffin-book

A zero-dependency Python 3.9+ CLI for a small tiffin kitchen. Keep a menu,
give each subscriber a default weekly meal, record per-date changes (skip a
day, swap a dish, add extras), and get two answers: what to cook on a given
date, and an itemized bill for the month.

## The itch

From Razorpay's "Fix My Itch" list (https://razorpay.com/m/fix-my-itch/),
Food & Beverage category: **"Why can't tiffin subscribers customize daily
meals?"** (itch score 74.5). Verified against the live page on 2026-11-26.
The list is a curated set of problems people submitted; appearing on it is
not proof of market demand.

## What it does

- `menu`: dishes with prices.
- `subscriber`: a default dish, the delivery days (mon..sun, `weekdays`,
  `all`) and a start date. `stop` ends a subscription after a date.
- `skip`, `swap`, `extra`: per-date changes. A skipped day is not billed. A
  swap bills the swapped dish. Extras are billed per piece on top. `clear`
  removes the changes for a date.
- `day DATE`: the kitchen sheet for that date, so cooking follows the
  latest changes instead of a chat history.
- `bill NAME YYYY-MM`: one line per delivery, skips shown as skipped, extras
  listed separately, with totals. `--csv FILE` writes the same lines.

Amounts are stored as integer paise, so there is no floating-point drift.

## Landscape (checked 2026-11-26)

A web search that day shows many hosted tiffin and meal-subscription
software products already advertising plans, skips and billing, for example
MessMitra, TiffinCloud, DailyBhojan, TiffinManager and PlateOS (see each
vendor's own site for what it does). tiffin-book is not a replacement for
those. It is a small offline notebook: one JSON file on your machine, no
account, no app for subscribers, no payments, no delivery routing.

## Run it

Needs Python 3.9 or newer. Nothing else. Output below is a real run:

```console
$ python3 tiffin_book.py menu add dal-rice --price 90
added dal-rice at 90
$ python3 tiffin_book.py menu add rajma-chawal --price 110
added rajma-chawal at 110
$ python3 tiffin_book.py menu add roti --price 8
added roti at 8
$ python3 tiffin_book.py subscriber Meera --item dal-rice --days mon,thu --start 2026-11-01
added subscriber Meera: dal-rice on mon,thu from 2026-11-01
$ python3 tiffin_book.py skip Meera 2026-11-12
Meera: skip on 2026-11-12
$ python3 tiffin_book.py swap Meera 2026-11-16 rajma-chawal
Meera: rajma-chawal on 2026-11-16 instead of the default
$ python3 tiffin_book.py extra Meera 2026-11-16 roti --qty 4
Meera: +4 x roti on 2026-11-16
$ python3 tiffin_book.py day 2026-11-16
kitchen sheet for 2026-11-16 (mon):
  Meera          rajma-chawal (swapped)  + 4 x roti
meals to cook: 1
$ python3 tiffin_book.py day 2026-11-12
kitchen sheet for 2026-11-12 (thu):
  Meera          SKIP
meals to cook: 0
$ python3 tiffin_book.py bill Meera 2026-11
bill for Meera, 2026-11:
  2026-11-02  dal-rice                     1      90.00
  2026-11-05  dal-rice                     1      90.00
  2026-11-09  dal-rice                     1      90.00
  2026-11-12  skipped                      -       0.00
  2026-11-16  rajma-chawal (swap)          1     110.00
  2026-11-16  roti (extra)                 4      32.00
  2026-11-19  dal-rice                     1      90.00
  2026-11-23  dal-rice                     1      90.00
  2026-11-26  dal-rice                     1      90.00
  2026-11-30  dal-rice                     1      90.00
meals billed: 8, skipped days (not billed): 1, extras: 32.00
total due: 772.00
$ python3 tiffin_book.py skip Meera 2026-11-03
error: Meera has no delivery on 2026-11-03
```

The last command fails on purpose: Meera has no delivery on a Tuesday, so
a skip there is rejected with exit code 2 and the file is left unchanged.
Data lives in a JSON file (`--db PATH`, default `tiffin_book.json` in the
current directory).

## Commands

```
menu add ITEM --price AMT      add a dish
menu price ITEM AMT            change a dish price
menu list                      list the menu
subscriber NAME --item ITEM --days DAYS [--start YYYY-MM-DD]
stop NAME DATE                 last delivery date
skip NAME DATE                 skip a scheduled day
swap NAME DATE ITEM            different dish on a scheduled day
extra NAME DATE ITEM [--qty N] extra items on any date
clear NAME DATE                remove changes for a date
day DATE                       kitchen sheet
bill NAME YYYY-MM [--csv FILE] itemized monthly bill
```

Amounts are in rupees with up to 2 decimals. Use dish names without spaces
(for example `dal-rice`) or quote them.

## Limits

- One meal per subscriber per scheduled day. No lunch/dinner slots, no
  plans with several dishes a day.
- Prices are not snapshotted: `menu price` changes apply to every bill
  generated afterwards, including bills for past months.
- A skip or swap is only accepted on a scheduled day. There is no cutoff
  time rule: the tool records what you tell it and does not decide whether
  a change request came too late.
- No payments, discounts, taxes or deposits. The bill is a record, not an
  accounting document.
- Durability is limited to the tested behavior: saves are an atomic file
  replace (write a temp file, then rename), and a failed command leaves the
  file untouched.

## Tests

```
python3 -m unittest test_tiffin_book -v
```

33 tests cover parsing, weekday scheduling, start and stop dates,
skip/swap/extra billing, replacement of earlier choices, kitchen sheets,
leap-year month length, storage round-trip and atomic save, CLI flows and
error exit codes. No test depends on the local clock. CI runs them on
ubuntu-24.04 and windows-latest against Python 3.9 and 3.13.

## License

MIT (c) 2026 Mehul Jhabak. See LICENSE.
