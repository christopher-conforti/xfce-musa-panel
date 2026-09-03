#!/usr/bin/env python3
"""
Panel -- a combined genmon script for the Xfce panel's Generic Monitor plugin.

Merges civil_clock_genmon.py (Annit, Hemerit, Solit, Dattit, Orit) with
weather_genmon.py (Thermit, Barit, Tachit, Azimit, Valit) into a single
plugin driven by a single --lokit argument. The Lokit supplies both the
geographic coordinates for Open-Meteo weather fetch AND the longitude/latitude
needed for Solit (true local solar noon), so only one argument is needed for
all per-location calculations.

EPOCH is proleptic Gregorian April 1, AD 33, 12:15:00 UTC -- the
Humphreys-Waddington crucifixion date (Julian April 3, 14:15 Jerusalem
local time) converted to UTC. CHRONIT_SECONDS is the exact constant from
the cca skillset (janus-units/scripts/).

Annit, Hemerit, and Solit all require the `ephem` package. If ephem is not
installed the script still outputs Dattit, Orit, and all weather tokens, and
fills in placeholders for the ephem-dependent values. Install with:
    pip install ephem --break-system-packages

Weather data is fetched from https://api.open-meteo.com using only the Python
standard library (urllib.request) -- no pip dependencies required for the
weather side. API responses are cached in /tmp to limit fetch frequency.

FORMAT STRINGS: --format controls the panel text, --tooltip-format controls
the hover tooltip. Both use Python str.format() with these tokens:

    Clock tokens (from civil_clock_genmon.py):

    {annit}         Annit in exact Janus balanced-dozenal notation, or ? if
                    ephem is unavailable
    {annit_short}   "An <janus notation>" (the official abbreviation)
    {annit_full}    "<janus notation> Annit"
    {dattit}        Dattit (days since epoch) in exact Janus notation
    {dattit_short}  "Da <janus notation>"
    {dattit_full}   "<janus notation> Dattit"
    {hemerit}       raw Hemerit acronym, e.g. "Aquita"
    {hemerit_short} "He Aquita"
    {hemerit_full}  "Aquita Hemerit"
    {orit}          Orit in Janus balanced-dozenal notation
    {orit_short}    "Or <janus notation>"
    {orit_full}     "<janus notation> Orit"
    {holiday}       holiday name (with day count if multi-day), or "" if not
                    a holiday
    {month}         zodiac month name, or "" during a holiday
    {week}          element week name ("Stoneweek" etc.), or ""
    {weekday}       Olympian day name ("Apollo" etc.), or ""
    {day_of_month}  1-30, or "" during a holiday
    {day_element}   the element this specific day counts as within its
                    week's rotation ("Fire", "Water", etc.), or "" during a
                    holiday
    {date_label}    holiday name, or musa.bet's spoken phrasing:
                    "Dayelementday, Weekday of Week of Month", e.g.
                    "Fireday, Aphrodite of Earthweek of Leo"
    {solit}         Solit as a 5-digit Janus mantissa pinned at magnitude
                    ① (12⁻¹ place), no magnitude prefix -- sign is carried
                    by the digits (negative in the morning, positive in the
                    afternoon)
    {solit_short}   "So <janus notation>"
    {solit_full}    "<janus notation> Solit"

    Weather tokens (from weather_genmon.py):

    {temp}              Temperature in Thermit, Janus notation
    {temp_short}        "Th <value>" (official unit abbreviation)
    {temp_full}         "<value> Thermit"
    {pressure}          Atmospheric pressure in Barit, Janus notation
    {pressure_short}    "Ba <value>"
    {pressure_full}     "<value> Barit"
    {wind_speed}        Wind speed in Tachit, Janus notation
    {wind_speed_short}  "Ta <value>"
    {wind_speed_full}   "<value> Tachit"
    {wind_dir}          Wind direction in Azimit, Janus notation
                        (0 = north, increases clockwise)
    {wind_dir_short}    "Az <value>"
    {wind_dir_full}     "<value> Azimit"
    {wind_short}        "Az <dir> Ta <speed>" (compact combined form)
    {precip}            Precipitation probability in Valit (0-100 integer
                        scale per musa.bet; stored as a whole-number
                        janus_integer)
    {precip_short}      "Va <value>"
    {precip_full}       "<value> Valit"
    {lokit}             The Lokit string as supplied to --lokit

Short forms use the unit's official abbreviation (An, Da, He, Or, So, Th,
Ba, Ta, Az, Va) the way musa.bet itself writes them. Full forms spell the
unit name out, with the number leading the way you'd say it aloud ("30
Dattit", not "Dattit 30") -- except Hemerit, which isn't a number at all,
so its full form is just the acronym followed by the unit name.

Examples:
    python3 panel_genmon.py
    python3 panel_genmon.py --lokit "Lo w26②⑤4n14③3②"
    python3 panel_genmon.py --format "{hemerit_short}, {annit_short}, {solit_short}  |  {temp_short}  {wind_short}"
    python3 panel_genmon.py --sig-digits 4 --cache-minutes 30
"""

import argparse
import hashlib
import json
import math
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

from janus_notation import janus_notation, janus_integer, janus_mantissa_fixed

# ---------------------------------------------------------------------------
# Civilization epoch and time constants
# ---------------------------------------------------------------------------
EPOCH = datetime(33, 4, 1, 12, 15, 0, tzinfo=timezone.utc)
CHRONIT_SECONDS = 643391.816709006
CONTINUOUS_SIG_DIGITS = 3   # panel default; --sig-digits overrides

# ---------------------------------------------------------------------------
# Janus unit constants from musa.bet/metrics.htm
# ---------------------------------------------------------------------------
_MACRIT_M  = 2.16332257927855e1
_DYNIT_N   = 3.78450497576255e-15
TACHIT_MS  = 3.36237192e-5          # m/s per Tachit
THERMIT_K  = 6.65077402e-4          # kelvin per Thermit
BARIT_PA   = _DYNIT_N / (_MACRIT_M ** 2)  # pascal per Barit
AZIMIT_RAD = 2 * math.pi / 12       # radians per Azimit (1/12 turn)

# ---------------------------------------------------------------------------
# Default location: R-1992-Q4ETQXWDJD9 observatory
# Lokit: Lo w3⑤④1④n14①②0  (lon ~76.678° W, lat ~39.757° N)
# ---------------------------------------------------------------------------
DEFAULT_LOKIT = "Lo w3⑤④1④n14①②0"

# ---------------------------------------------------------------------------
# Civil calendar data (verbatim from civil_clock_genmon.py)
# ---------------------------------------------------------------------------
MONTHS = ["Capricorn", "Aquarius", "Pisces", "Aries", "Taurus", "Gemini",
          "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius"]
WEEKS = ["Stoneweek", "Earthweek", "Waterweek", "Airweek", "Fireweek"]
WEEK_ELEMENTS = ["Stone", "Earth", "Water", "Air", "Fire"]
DAYS = ["Apollo", "Artemis", "Ares", "Hermes", "Athena", "Aphrodite"]

MONTH_STEMS = {
    "Capricorn": "Capr", "Aquarius": "Aqu", "Pisces": "Pisc",
    "Aries": "Ar", "Taurus": "Taur", "Gemini": "Gem",
    "Cancer": "Can", "Leo": "Ley", "Virgo": "Virg",
    "Libra": "Libr", "Scorpio": "Scorp", "Sagittarius": "Sagitt",
}

ELEMENT_VOWEL = {"Water": "a", "Air": "e", "Fire": "i", "Stone": "u", "Earth": "o"}

DAY_CONSONANT = {"Apollo": "p", "Artemis": "t", "Ares": "r",
                 "Hermes": "m", "Athena": "th", "Aphrodite": "f"}

DAY_ELEMENT_TABLE = {
    "Stone": ["Water", "Air", "Earth", "Fire", "Water", "Air"],
    "Earth": ["Air", "Fire", "Water", "Stone", "Air", "Fire"],
    "Water": ["Fire", "Stone", "Air", "Earth", "Fire", "Stone"],
    "Air":   ["Stone", "Earth", "Fire", "Water", "Stone", "Earth"],
    "Fire":  ["Earth", "Water", "Stone", "Air", "Earth", "Water"],
}

HOLIDAY_HEMERIT = {
    ("Year End", 1): "Holithi",
    ("Year End", 2): "Holifi",
    ("Easter", 1): "Holipu",
    ("Midyear", 1): "Holito",
    ("Midyear", 2): "Holira",
    ("Harfest", 1): "Holime",
}

# ---------------------------------------------------------------------------
# ephem import (optional -- clock still outputs Dattit/Orit without it)
# ---------------------------------------------------------------------------
try:
    import ephem
    HAVE_EPHEM = True
except ImportError:
    HAVE_EPHEM = False

# ---------------------------------------------------------------------------
# Lokit decode (inlined from janus_convert.py for self-containment)
# ---------------------------------------------------------------------------
_NEG_CIRCLED = {1: "\u2460", 2: "\u2461", 3: "\u2462", 4: "\u2463", 5: "\u2464", 6: "\u2465"}
_CIRCLED_NEG = {v: -k for k, v in _NEG_CIRCLED.items()}


def _lokit_parse_frac(s):
    total = 0.0
    for i, ch in enumerate(s, 1):
        if ch in _CIRCLED_NEG:
            d = _CIRCLED_NEG[ch]
        elif ch.isdigit():
            d = int(ch)
            if d > 6:
                raise ValueError(f"digit {d} not a valid Janus digit")
        else:
            raise ValueError(f"unexpected char {ch!r} in Lokit")
        total += d / (12 ** i)
    return total


def lokit_decode(lokit_str):
    """Decode Janus Lokit to (lon_deg_west, lat_deg_north)."""
    s = lokit_str.strip()
    if s.startswith("Lo "):
        s = s[3:]
    if len(s) != 12:
        raise ValueError(f"Lokit must be 12 chars after 'Lo ': {lokit_str!r}")
    lon_dir, lat_dir = s[0], s[6]
    if lon_dir not in ('w', 'e'):
        raise ValueError(f"bad lon direction: {lon_dir!r}")
    if lat_dir not in ('n', 's'):
        raise ValueError(f"bad lat direction: {lat_dir!r}")
    lon_deg = _lokit_parse_frac(s[1:6]) * 360.0
    lat_deg = _lokit_parse_frac(s[7:12]) * 360.0
    if lon_dir == 'e':
        lon_deg = -lon_deg
    if lat_dir == 's':
        lat_deg = -lat_deg
    return lon_deg, lat_deg

# ---------------------------------------------------------------------------
# Clock helper functions (verbatim from civil_clock_genmon.py)
# ---------------------------------------------------------------------------


def _to_utc(ephem_date):
    return ephem_date.datetime().replace(tzinfo=timezone.utc)


def equinox_solstice_year(gregorian_year):
    """March equinox, June solstice, September equinox, December solstice
    of the given Gregorian year, in that chronological order."""
    d = ephem.Date(f'{gregorian_year}/1/1')
    mar_eq = ephem.next_equinox(d)
    jun_sol = ephem.next_solstice(mar_eq)
    sep_eq = ephem.next_equinox(jun_sol)
    dec_sol = ephem.next_solstice(sep_eq)
    return _to_utc(mar_eq), _to_utc(jun_sol), _to_utc(sep_eq), _to_utc(dec_sol)


def year_boundaries(dt):
    """Returns (dec_sol_prev, mar_eq, jun_sol, sep_eq, dec_sol_next) such
    that dec_sol_prev <= dt < dec_sol_next."""
    y = dt.year
    mar_eq, jun_sol, sep_eq, dec_sol = equinox_solstice_year(y)
    if dec_sol <= dt:
        mar_eq2, jun_sol2, sep_eq2, dec_sol_next = equinox_solstice_year(y + 1)
        return dec_sol, mar_eq2, jun_sol2, sep_eq2, dec_sol_next
    else:
        _, _, _, dec_sol_prev = equinox_solstice_year(y - 1)
        return dec_sol_prev, mar_eq, jun_sol, sep_eq, dec_sol


def year_end_holiday_days(annit_year):
    """Second Year End day every fourth year, except years divisible by 128."""
    if annit_year % 4 == 0 and annit_year % 128 != 0:
        return 2
    return 1


def hemerit_acronym(month, week_name, weekday):
    """Builds the real Hemerit acronym: month stem + this week's element
    vowel + this weekday's consonant + the element vowel this specific
    (week, weekday) slot counts as. Verified against musa.bet's own
    worked example: Aquarius + Fireweek + Artemis = "Aquita", which is
    exactly what this produces."""
    week_index = WEEKS.index(week_name)
    week_element = WEEK_ELEMENTS[week_index]
    day_index = DAYS.index(weekday)
    day_element = DAY_ELEMENT_TABLE[week_element][day_index]

    stem = MONTH_STEMS[month]
    if month == "Aquarius" and week_name == "Stoneweek":
        stem = "Aq\u00fc"  # Aqu + u would double the letter; musa.bet writes Aqü + u

    acronym = stem + ELEMENT_VOWEL[week_element] + DAY_CONSONANT[weekday] + ELEMENT_VOWEL[day_element]
    return acronym, day_element


def hemerit_info(dt):
    """Returns a dict describing the civil calendar position of dt:
    annit, the Hemerit acronym, holiday (or None), and month/week/weekday/
    day_of_month/day_element (or None during a holiday)."""
    dec_sol_prev, mar_eq, jun_sol, sep_eq, dec_sol_next = year_boundaries(dt)
    annit = dec_sol_prev.year - EPOCH.year

    segments = [
        (dec_sol_prev, mar_eq, "Year End", year_end_holiday_days(annit),
         [MONTHS[0], MONTHS[1], MONTHS[2]]),
        (mar_eq, jun_sol, "Easter", 1, [MONTHS[3], MONTHS[4], MONTHS[5]]),
        (jun_sol, sep_eq, "Midyear", 2, [MONTHS[6], MONTHS[7], MONTHS[8]]),
        (sep_eq, dec_sol_next, "Harfest", 1, [MONTHS[9], MONTHS[10], MONTHS[11]]),
    ]

    for start, end, holiday_name, holiday_days, season_months in segments:
        if start <= dt < end:
            day_index = int((dt - start).total_seconds() // 86400)
            if day_index < holiday_days:
                which_day = day_index + 1  # 1 or 2
                hemerit = HOLIDAY_HEMERIT[(holiday_name, which_day)]
                label = holiday_name
                if holiday_days > 1:
                    label += f" (Day {which_day} of {holiday_days})"
                return {"annit": annit, "hemerit": hemerit, "holiday": label,
                        "month": None, "week": None, "weekday": None,
                        "day_of_month": None, "day_element": None}
            month_day_index = min(day_index - holiday_days, 89)  # clamp; see module docstring
            month = season_months[month_day_index // 30]
            week = WEEKS[(month_day_index % 30) // 6]
            weekday = DAYS[month_day_index % 6]
            day_of_month = (month_day_index % 30) + 1
            hemerit, day_element = hemerit_acronym(month, week, weekday)
            return {"annit": annit, "hemerit": hemerit, "holiday": None,
                    "month": month, "week": week, "weekday": weekday,
                    "day_of_month": day_of_month, "day_element": day_element}

    raise RuntimeError("date fell outside all four season segments -- this is a bug")


def solit_for(dt, lat, lon):
    """Elapsed Chronits since true local solar noon; negative in the morning.
    lat/lon follow ephem convention: lat positive=north, lon positive=east."""
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.date = dt.strftime("%Y/%m/%d %H:%M:%S")
    sun = ephem.Sun()
    prev_transit = _to_utc(obs.previous_transit(sun))
    next_transit = _to_utc(obs.next_transit(sun))
    since_prev = (dt - prev_transit).total_seconds()
    until_next = (next_transit - dt).total_seconds()
    if since_prev <= until_next:
        seconds = since_prev
    else:
        seconds = -until_next
    return seconds / CHRONIT_SECONDS

# ---------------------------------------------------------------------------
# Weather cache + fetch (verbatim from weather_genmon.py)
# ---------------------------------------------------------------------------
CACHE_DIR = "/tmp"


def _cache_path(lokit):
    h = hashlib.sha256(lokit.encode()).hexdigest()[:8]
    return os.path.join(CACHE_DIR, f"xfce_musa_janus_weather_{h}.json")


def _load_cache(lokit, max_age_seconds):
    path = _cache_path(lokit)
    try:
        with open(path) as f:
            data = json.load(f)
        if time.time() - data.get("fetched_at", 0) < max_age_seconds:
            return data["weather"]
    except (OSError, KeyError, json.JSONDecodeError):
        pass
    return None


def _save_cache(lokit, weather):
    path = _cache_path(lokit)
    with open(path, "w") as f:
        json.dump({"fetched_at": time.time(), "weather": weather}, f)


def fetch_weather(lat, lon, cache_minutes, lokit):
    """Fetch current weather from Open-Meteo; cache results.
    lat/lon are standard geographic decimal degrees (lon positive=east)."""
    cached = _load_cache(lokit, cache_minutes * 60)
    if cached is not None:
        return cached
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,surface_pressure,wind_speed_10m,"
        "wind_direction_10m,precipitation_probability"
        "&wind_speed_unit=ms"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        raw = json.loads(resp.read())
    current = raw["current"]
    weather = {
        "temp_c":       current["temperature_2m"],
        "pressure_hpa": current["surface_pressure"],
        "wind_ms":      current["wind_speed_10m"],
        "wind_deg":     current["wind_direction_10m"],
        "precip_pct":   current.get("precipitation_probability", 0),
    }
    _save_cache(lokit, weather)
    return weather

# ---------------------------------------------------------------------------
# Build tokens -- merged clock + weather
# ---------------------------------------------------------------------------


def build_tokens(now, lokit, cache_minutes, sig_digits):
    # Decode Lokit -> coordinates
    lon_west, lat_north = lokit_decode(lokit)
    ephem_lat = lat_north          # ephem: positive = north
    ephem_lon = -lon_west          # ephem: positive = east; lokit gives west-positive

    # Clock arithmetic (no ephem needed)
    dattit = int((now - EPOCH).days)
    orit = (now - EPOCH).total_seconds() / CHRONIT_SECONDS
    dattit_str = janus_integer(dattit)
    orit_str = janus_notation(orit, sig_digits=sig_digits)

    tokens = {
        # Clock tokens
        "dattit": dattit_str, "dattit_short": f"Da {dattit_str}", "dattit_full": f"{dattit_str} Dattit",
        "orit": orit_str, "orit_short": f"Or {orit_str}", "orit_full": f"{orit_str} Orit",
        "annit": "?", "annit_short": "An ?", "annit_full": "? Annit",
        "hemerit": "?", "hemerit_short": "He ?", "hemerit_full": "? Hemerit",
        "holiday": "", "month": "", "week": "", "weekday": "",
        "day_of_month": "", "day_element": "",
        "date_label": "(needs ephem)",
        "solit": "?", "solit_short": "So ?", "solit_full": "? Solit",
        # Weather tokens (prefilled with ? for error cases)
        "temp": "?", "temp_short": "Th ?", "temp_full": "? Thermit",
        "pressure": "?", "pressure_short": "Ba ?", "pressure_full": "? Barit",
        "wind_speed": "?", "wind_speed_short": "Ta ?", "wind_speed_full": "? Tachit",
        "wind_dir": "?", "wind_dir_short": "Az ?", "wind_dir_full": "? Azimit",
        "wind_short": "Az ? Ta ?",
        "precip": "?", "precip_short": "Va ?", "precip_full": "? Valit",
        "lokit": lokit,
    }

    # Clock tokens requiring ephem
    if HAVE_EPHEM:
        info = hemerit_info(now)
        annit_str = janus_integer(info["annit"])
        tokens.update({
            "annit": annit_str,
            "annit_short": f"An {annit_str}",
            "annit_full": f"{annit_str} Annit",
            "hemerit": info["hemerit"],
            "hemerit_short": f"He {info['hemerit']}",
            "hemerit_full": f"{info['hemerit']} Hemerit",
        })
        if info["holiday"]:
            tokens["holiday"] = info["holiday"]
            tokens["date_label"] = info["holiday"]
        else:
            tokens.update({
                "month": info["month"], "week": info["week"],
                "weekday": info["weekday"], "day_of_month": info["day_of_month"],
                "day_element": info["day_element"],
                "date_label": (
                    f"{info['day_element']}day, {info['weekday']} of "
                    f"{info['week']} of {info['month']}"
                ),
            })
        solit = solit_for(now, ephem_lat, ephem_lon)
        solit_str = janus_mantissa_fixed(solit, fixed_magnitude=-1, sig_digits=5)
        tokens.update({
            "solit": solit_str,
            "solit_short": f"So {solit_str}",
            "solit_full": f"{solit_str} Solit",
        })

    # Weather tokens
    try:
        # Open-Meteo uses standard geo (lon positive=east), same as ephem_lon
        weather = fetch_weather(lat_north, ephem_lon, cache_minutes, lokit)
        temp_th   = (weather["temp_c"] + 273.15) / THERMIT_K
        pres_ba   = (weather["pressure_hpa"] * 100.0) / BARIT_PA
        wind_ta   = weather["wind_ms"] / TACHIT_MS
        wdir_az   = (weather["wind_deg"] * math.pi / 180.0) / AZIMIT_RAD
        precip_va = round(weather["precip_pct"])
        temp_str  = janus_notation(temp_th,  sig_digits=sig_digits)
        pres_str  = janus_notation(pres_ba,  sig_digits=sig_digits)
        wspd_str  = janus_notation(wind_ta,  sig_digits=sig_digits)
        wdir_str  = janus_notation(wdir_az,  sig_digits=sig_digits)
        prec_str  = janus_integer(precip_va)
        tokens.update({
            "temp": temp_str, "temp_short": f"Th {temp_str}", "temp_full": f"{temp_str} Thermit",
            "pressure": pres_str, "pressure_short": f"Ba {pres_str}", "pressure_full": f"{pres_str} Barit",
            "wind_speed": wspd_str, "wind_speed_short": f"Ta {wspd_str}", "wind_speed_full": f"{wspd_str} Tachit",
            "wind_dir": wdir_str, "wind_dir_short": f"Az {wdir_str}", "wind_dir_full": f"{wdir_str} Azimit",
            "wind_short": f"Az {wdir_str} Ta {wspd_str}",
            "precip": prec_str, "precip_short": f"Va {prec_str}", "precip_full": f"{prec_str} Valit",
        })
    except Exception:
        pass  # weather tokens stay as "?"

    return tokens

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(fmt, tokens):
    try:
        return fmt.format(**tokens)
    except KeyError as e:
        valid = ", ".join(sorted(tokens.keys()))
        return f"[format error: unknown token {e}; valid: {valid}]"

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

DEFAULT_FORMAT = "{hemerit_short}, {annit_short}, {solit_short}  |  {temp_short}  {wind_short}  {precip_short}"
DEFAULT_TOOLTIP = (
    "{date_label}\n"
    "{annit_full}  ({dattit_full})\n"
    "{solit_full}\n"
    "{orit_full}\n"
    "\n"
    "{temp_full}\n"
    "{pressure_full}\n"
    "{wind_speed_full}  {wind_dir_full}\n"
    "{precip_full}\n"
    "{lokit}"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lokit", default=DEFAULT_LOKIT,
                        help="Lokit coordinate for location (Solit + weather)")
    parser.add_argument("--format", default=DEFAULT_FORMAT,
                        help="panel text format string")
    parser.add_argument("--tooltip-format", default=DEFAULT_TOOLTIP,
                        help="tooltip format string ('\\n' becomes a real line break)")
    parser.add_argument("--sig-digits", type=int, default=CONTINUOUS_SIG_DIGITS,
                        help=f"significant digits for continuous Janus values (default: {CONTINUOUS_SIG_DIGITS})")
    parser.add_argument("--cache-minutes", type=int, default=15,
                        help="cache API response for this many minutes (default: 15)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    tokens = build_tokens(now, args.lokit, args.cache_minutes, args.sig_digits)
    label = render(args.format, tokens)
    tooltip_text = render(args.tooltip_format, tokens)
    if not HAVE_EPHEM:
        tooltip_text += "\n(install ephem for Annit, Hemerit, Solit)"
    print("<txt>" + label + "</txt>")
    print("<tool>" + tooltip_text.replace("\n", "&#10;") + "</tool>")


if __name__ == "__main__":
    main()
