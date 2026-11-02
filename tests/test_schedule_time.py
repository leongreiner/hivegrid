"""bin/hivegrid_schedule.py, time half: the cron grammar (lists, ranges, steps, names, the Vixie day rule),
interval anchors, DST gaps and repeats in two zones and in the process's local zone, describe(), and the
plain-text parser against a fixed `now`. Everything is a pure function of explicit epochs: no clock, no sleep."""
import calendar
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
sys.dont_write_bytecode = True

import hivegrid_schedule as S  # noqa: E402


def E(y, mo, d, h=0, mi=0, s=0):
    """UTC epoch."""
    return calendar.timegm((y, mo, d, h, mi, s))


def cron(expr, tz="UTC"):
    return {"type": "cron", "expr": expr, "tz": tz}


def zone_available(name):
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001
        return False


class CronGrammar(unittest.TestCase):
    def fires(self, expr, after, n=3, tz="UTC"):
        return S.next_fires(cron(expr, tz), after, n)

    def test_every_minute_field_forms(self):
        after = E(2026, 11, 2, 8, 0)
        self.assertEqual(self.fires("*/15 * * * *", after), [E(2026, 11, 2, 8, 15), E(2026, 11, 2, 8, 30), E(2026, 11, 2, 8, 45)])
        self.assertEqual(self.fires("5,50 9 * * *", after, 3), [E(2026, 11, 2, 9, 5), E(2026, 11, 2, 9, 50), E(2026, 11, 3, 9, 5)])
        self.assertEqual(self.fires("10-50/20 9 * * *", after, 4), [E(2026, 11, 2, 9, 10), E(2026, 11, 2, 9, 30), E(2026, 11, 2, 9, 50), E(2026, 11, 3, 9, 10)])
        self.assertEqual(self.fires("30/20 9 * * *", after, 2), [E(2026, 11, 2, 9, 30), E(2026, 11, 2, 9, 50)])

    def test_strictly_after(self):
        at = E(2026, 11, 2, 9, 0)
        self.assertEqual(S.next_fire(cron("0 9 * * *"), at - 1), at)
        self.assertEqual(S.next_fire(cron("0 9 * * *"), at), at + 86400)
        self.assertEqual(S.next_fire(cron("0 9 * * *"), at + 30), at + 86400)

    def test_names_and_sunday_forms(self):
        after = E(2026, 11, 2, 12, 0)  # Monday
        self.assertEqual(self.fires("0 9 * * mon-fri", after, 3), [E(2026, 11, 3, 9), E(2026, 11, 4, 9), E(2026, 11, 5, 9)])
        self.assertEqual(self.fires("0 9 * * SUN", after, 1), [E(2026, 11, 8, 9)])
        self.assertEqual(self.fires("0 9 * * 7", after, 1), [E(2026, 11, 8, 9)])
        self.assertEqual(self.fires("0 9 * * 0", after, 1), [E(2026, 11, 8, 9)])
        self.assertEqual(self.fires("0 9 1 jan,mar *", after, 3), [E(2027, 1, 1, 9), E(2027, 3, 1, 9), E(2028, 1, 1, 9)])
        self.assertEqual(self.fires("0 9 1 feb-mar *", after, 2), [E(2027, 2, 1, 9), E(2027, 3, 1, 9)])

    def test_macros(self):
        after = E(2026, 11, 2, 8, 30)
        self.assertEqual(self.fires("@hourly", after, 2), [E(2026, 11, 2, 9), E(2026, 11, 2, 10)])
        self.assertEqual(self.fires("@daily", after, 1), [E(2026, 11, 3, 0)])
        self.assertEqual(self.fires("@weekly", after, 1), [E(2026, 11, 8, 0)])
        self.assertEqual(self.fires("@monthly", after, 1), [E(2026, 12, 1, 0)])

    def test_vixie_dom_and_dow_are_ored_when_both_restricted(self):
        after = E(2026, 11, 1, 12, 0)  # Sunday Nov 1
        # 13th of the month OR any Friday: Fri 6, Fri 13 (both rules), Fri 20, Fri 27
        got = self.fires("0 9 13 * fri", after, 4)
        self.assertEqual(got, [E(2026, 11, 6, 9), E(2026, 11, 13, 9), E(2026, 11, 20, 9), E(2026, 11, 27, 9)])
        # only the 13th restricted: dow `*` leaves a plain AND
        self.assertEqual(self.fires("0 9 13 * *", after, 2), [E(2026, 11, 13, 9), E(2026, 12, 13, 9)])
        # a starred field with a step still counts as unrestricted-star: AND (every 2nd day that is a Monday)
        got = self.fires("0 9 */2 * mon", after, 3)
        self.assertTrue(all(S._zone("UTC").naive(e).day % 2 == 1 and S._zone("UTC").naive(e).weekday() == 0 for e in got))

    def test_bad_expressions_name_the_problem(self):
        for expr, word in (("61 * * * *", "minute"), ("* 24 * * *", "hour"), ("* * 0 * *", "day-of-month"),
                           ("* * * 13 *", "month"), ("* * * * 8", "weekday"), ("* * * *", "5 fields"),
                           ("* * * * * *", "5 fields"), ("*/0 * * * *", "step"), ("5-1 * * * *", "out of range"),
                           ("0 9 * * funday", "weekday"), ("@sometimes", "macro"), ("a b c d e", "minute"),
                           ("0 0 31 feb *", "never fires")):
            with self.assertRaises(ValueError, msg=expr) as cm:
                S.parse_cron(expr)
            self.assertIn(word, str(cm.exception), expr)

    def test_recurrence_floor_is_five_minutes(self):
        for expr in ("* * * * *", "*/4 * * * *", "0,3 * * * *"):
            with self.assertRaises(ValueError, msg=expr):
                S.validate_when(cron(expr))
        for expr in ("*/5 * * * *", "0 * * * *", "0 9 * * *"):
            S.validate_when(cron(expr))
        with self.assertRaises(ValueError):
            S.validate_when({"type": "interval", "every_s": 299, "anchor": 0})

    def test_unknown_keys_and_zone(self):
        with self.assertRaises(ValueError):
            S.validate_when({"type": "cron", "expr": "0 9 * * *", "tz": "local", "extra": 1})
        with self.assertRaises(ValueError):
            S.validate_when(cron("0 9 * * *", "Mars/Olympus"))
        with self.assertRaises(ValueError):
            S.validate_when({"type": "once", "at": "next tuesday", "tz": "local"})


class IntervalAndOnce(unittest.TestCase):
    def test_interval_grid_from_anchor(self):
        w = {"type": "interval", "every_s": 600, "anchor": 1000}
        self.assertEqual(S.next_fire(w, 0), 1600)
        self.assertEqual(S.next_fire(w, 1000), 1600)
        self.assertEqual(S.next_fire(w, 1599), 1600)
        self.assertEqual(S.next_fire(w, 1600), 2200)
        self.assertEqual(S.next_fire(w, 5000), 5200)
        self.assertEqual(S.next_fires(w, 1000, 3), [1600, 2200, 2800])

    def test_once_fires_once_in_the_future_only(self):
        w = {"type": "once", "at": "2026-11-03T09:00", "tz": "UTC"}
        at = E(2026, 11, 3, 9)
        self.assertEqual(S.next_fire(w, at - 1), at)
        self.assertIsNone(S.next_fire(w, at))
        self.assertIsNone(S.next_fire(w, at - 1, last_slot="2026-11-03T09:00"))
        self.assertEqual(S.next_fires(w, 0, 5), [at])
        sec = S.validate_when({"type": "once", "at": "2026-11-03T09:00:30", "tz": "UTC"})
        self.assertEqual(S.next_fire(sec, 0), at + 30)


@unittest.skipUnless(zone_available("America/New_York") and zone_available("Europe/Berlin"), "no tz database")
class DaylightSaving(unittest.TestCase):
    NY = "America/New_York"
    DE = "Europe/Berlin"

    def test_ny_spring_forward_fires_once_at_the_shifted_time(self):
        w = cron("30 2 * * *", self.NY)
        got = S.next_fires(w, E(2026, 3, 7, 18), 3)
        # Mar 8 02:30 does not exist; it runs at 03:30 EDT = 07:30 UTC, then the days go on at 02:30 EDT
        self.assertEqual(got, [E(2026, 3, 8, 7, 30), E(2026, 3, 9, 6, 30), E(2026, 3, 10, 6, 30)])

    def test_ny_fall_back_fires_once(self):
        w = cron("30 1 * * *", self.NY)
        got = S.next_fires(w, E(2026, 10, 31, 12), 3)
        # Nov 1 01:30 happens twice (EDT then EST); only the first fires
        self.assertEqual(got, [E(2026, 11, 1, 5, 30), E(2026, 11, 2, 6, 30), E(2026, 11, 3, 6, 30)])

    def test_ny_hourly_over_the_repeated_hour(self):
        w = cron("0 * * * *", self.NY)
        first = E(2026, 11, 1, 5)  # 01:00 EDT
        self.assertEqual(S.next_fire(w, first - 3600), first)
        self.assertEqual(S.next_fire(w, first), E(2026, 11, 1, 7))  # 02:00 EST; the 01:00 EST repeat is skipped

    def test_last_slot_blocks_a_second_fire_of_the_same_local_slot(self):
        w = cron("30 1 * * *", self.NY)
        self.assertEqual(S.next_fire(w, E(2026, 11, 1, 4), last_slot="2026-11-01T01:30"), E(2026, 11, 2, 6, 30))
        self.assertEqual(S.next_fire(w, E(2026, 11, 1, 4)), E(2026, 11, 1, 5, 30))
        self.assertEqual(S._slot_local(w, E(2026, 11, 1, 5, 30)), "2026-11-01T01:30")

    def test_berlin_gap_and_repeat(self):
        w = cron("30 2 * * *", self.DE)
        # Mar 29 02:30 does not exist (02:00 -> 03:00): fires at 03:30 CEST = 01:30 UTC
        self.assertEqual(S.next_fires(w, E(2026, 3, 28, 12), 2), [E(2026, 3, 29, 1, 30), E(2026, 3, 30, 0, 30)])
        # Oct 25 02:30 happens twice; the first (CEST, 00:30 UTC) fires, the next fire is the day after in CET
        self.assertEqual(S.next_fires(w, E(2026, 10, 24, 12), 3), [E(2026, 10, 25, 0, 30), E(2026, 10, 26, 1, 30), E(2026, 10, 27, 1, 30)])

    def test_once_in_a_gap_and_in_a_repeat(self):
        gap = {"type": "once", "at": "2026-03-08T02:30", "tz": self.NY}
        self.assertEqual(S.next_fire(gap, E(2026, 3, 8, 0)), E(2026, 3, 8, 7, 30))
        rep = {"type": "once", "at": "2026-11-01T01:30", "tz": self.NY}
        self.assertEqual(S.next_fire(rep, E(2026, 11, 1, 0)), E(2026, 11, 1, 5, 30))

    def test_zone_is_used_not_utc(self):
        w = cron("0 9 * * *", self.NY)
        self.assertEqual(S.next_fire(w, E(2026, 7, 1, 0)), E(2026, 7, 1, 13))  # EDT
        self.assertEqual(S.next_fire(w, E(2026, 12, 1, 0)), E(2026, 12, 1, 14))  # EST


class LocalZone(unittest.TestCase):
    """`tz: "local"` follows the process zone (TZ + tzset), checked in New York when libc knows it."""

    def setUp(self):
        self.old = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        if time.localtime(E(2026, 7, 1, 12)).tm_gmtoff != -4 * 3600:
            self.tearDown()
            self.skipTest("libc has no America/New_York")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        time.tzset()

    def test_local_gap_repeat_and_plain_day(self):
        w = cron("30 2 * * *", "local")
        self.assertEqual(S.next_fire(w, E(2026, 3, 7, 18)), E(2026, 3, 8, 7, 30))
        w = cron("30 1 * * *", "local")
        self.assertEqual(S.next_fires(w, E(2026, 10, 31, 12), 3), [E(2026, 11, 1, 5, 30), E(2026, 11, 2, 6, 30), E(2026, 11, 3, 6, 30)])
        w = cron("0 9 * * *", "local")
        self.assertEqual(S.next_fire(w, E(2026, 7, 1, 0)), E(2026, 7, 1, 13))

    def test_once_local(self):
        w = {"type": "once", "at": "2026-11-03T09:00", "tz": "local"}
        self.assertEqual(S.next_fire(w, E(2026, 11, 1)), E(2026, 11, 3, 14))


class Describe(unittest.TestCase):
    def test_short_texts(self):
        d = S.describe
        self.assertEqual(d(cron("0 9 * * 1-5", "local")), "weekdays 09:00")
        self.assertEqual(d(cron("30 9 * * 1-5", "local")), "weekdays 09:30")
        self.assertEqual(d(cron("0 9 * * *", "local")), "daily 09:00")
        self.assertEqual(d(cron("0 10 * * 0,6", "local")), "weekends 10:00")
        self.assertEqual(d(cron("0 7 * * 1", "local")), "mon 07:00")
        self.assertEqual(d(cron("0 9 * * 1,5", "local")), "mon,fri 09:00")
        self.assertEqual(d(cron("0 * * * *", "local")), "hourly")
        self.assertEqual(d(cron("*/10 * * * *", "local")), "every 10m")
        self.assertEqual(d(cron("0 9 1 * *", "local")), "monthly 1st 09:00")
        self.assertEqual(d(cron("0 9 13 * fri", "local")), "cron 0 9 13 * fri")
        self.assertEqual(d({"type": "interval", "every_s": 1800, "anchor": 0}), "every 30m")
        self.assertEqual(d({"type": "interval", "every_s": 7200, "anchor": 0}), "every 2h")
        self.assertEqual(d({"type": "interval", "every_s": 86400, "anchor": 0}), "every 1d")
        self.assertEqual(d({"type": "once", "at": "2026-11-03T09:00", "tz": "local"}), "once Nov 3 09:00")
        self.assertEqual(d(cron("0 9 * * *", "Europe/Berlin")), "daily 09:00 (Europe/Berlin)")


class ParseWhen(unittest.TestCase):
    NOW = E(2026, 11, 2, 8, 0)  # Monday 08:00 UTC

    def parse(self, text):
        return S.parse_when(text, now=self.NOW, tz="UTC")

    def test_table(self):
        c = lambda e: {"type": "cron", "expr": e, "tz": "UTC"}  # noqa: E731
        o = lambda a: {"type": "once", "at": a, "tz": "UTC"}  # noqa: E731
        table = [
            ("0 9 * * 1-5", c("0 9 * * 1-5")),
            ("*/15 * * * *", c("*/15 * * * *")),
            ("@daily", c("@daily")),
            ("every 30m", {"type": "interval", "every_s": 1800, "anchor": self.NOW}),
            ("every 2h", {"type": "interval", "every_s": 7200, "anchor": self.NOW}),
            ("EVERY 1d", {"type": "interval", "every_s": 86400, "anchor": self.NOW}),
            ("daily at 9", c("0 9 * * *")),
            ("daily 09:00", c("0 9 * * *")),
            ("every day at 9am", c("0 9 * * *")),
            ("weekdays 9:30", c("30 9 * * 1-5")),
            ("every weekday at 8pm", c("0 20 * * 1-5")),
            ("weekends 10:00", c("0 10 * * 0,6")),
            ("every monday at 7", c("0 7 * * 1")),
            ("monday and friday 9am", c("0 9 * * 1,5")),
            ("every tuesday and thursday at 12:15pm", c("15 12 * * 2,4")),
            ("hourly", c("0 * * * *")),
            ("every hour", c("0 * * * *")),
            ("weekly", c("0 0 * * 0")),
            ("monthly on the 1st at 9", c("0 9 1 * *")),
            ("monthly on the 15th at 6pm", c("0 18 15 * *")),
            ("once 2026-11-03 09:00", o("2026-11-03T09:00")),
            ("tomorrow 9am", o("2026-11-03T09:00")),
            ("today 17:30", o("2026-11-02T17:30")),
            ("in 2h", o("2026-11-02T10:00")),
            ("in 45 minutes", o("2026-11-02T08:45")),
            ("at 17:00", o("2026-11-02T17:00")),
            ("at 7:00", o("2026-11-03T07:00")),
            ("2026-11-05T14:30", o("2026-11-05T14:30")),
            ("2026-11-05 14:30:15", o("2026-11-05T14:30:15")),
            ("  Daily   At 9AM ", c("0 9 * * *")),
        ]
        for text, want in table:
            with self.subTest(text):
                self.assertEqual(self.parse(text)["when"], want)

    def test_next_fire_times(self):
        r = self.parse("weekdays 9:30")
        self.assertEqual(r["next"], [E(2026, 11, 2, 9, 30), E(2026, 11, 3, 9, 30), E(2026, 11, 4, 9, 30)])
        self.assertEqual(self.parse("every 30m")["next"], [self.NOW + 1800, self.NOW + 3600, self.NOW + 5400])
        self.assertEqual(self.parse("in 2h")["next"], [E(2026, 11, 2, 10)])
        self.assertEqual(self.parse("every monday at 7")["next"][0], E(2026, 11, 9, 7))

    def test_errors_are_helpful(self):
        for text, word in (("", "empty"), ("bogus", "cannot understand"), ("every 1m", "shortest"),
                           ("today 7:00", "already passed"), ("weekdays", "time"), ("at 25:00", "hour"),
                           ("* * * * *", "often"), ("0 9 * * 9", "weekday"), ("monthly on the 40th", "monthly"),
                           ("tomorrow", "time"), ("2026-02-30 09:00", "bad time")):
            with self.subTest(text):
                with self.assertRaises(ValueError) as cm:
                    self.parse(text)
                self.assertIn(word, str(cm.exception))
        with self.assertRaises(ValueError):
            S.parse_when("daily 9", now=self.NOW, tz="Mars/Olympus")

    def test_zone_applies_to_wall_clock_words(self):
        if not zone_available("America/New_York"):
            self.skipTest("no tz database")
        r = S.parse_when("tomorrow 9am", now=self.NOW, tz="America/New_York")  # 03:00 in New York
        self.assertEqual(r["when"], {"type": "once", "at": "2026-11-03T09:00", "tz": "America/New_York"})
        self.assertEqual(r["next"], [E(2026, 11, 3, 14)])

    def test_duration(self):
        self.assertEqual(S.parse_duration("30m"), 1800)
        self.assertEqual(S.parse_duration("2h"), 7200)
        self.assertEqual(S.parse_duration("1d"), 86400)
        self.assertEqual(S.parse_duration("90"), 90)
        with self.assertRaises(ValueError):
            S.parse_duration("soon")


if __name__ == "__main__":
    unittest.main()
