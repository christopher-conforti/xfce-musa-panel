#!/usr/bin/env python3
"""
Panel -- a modular genmon script for the Xfce panel's Generic Monitor plugin.

Merges civil_clock_genmon.py (Annit, Hemerit, Solit, Dattit, Orit) with
weather_genmon.py (Thermit, Barit, Tachit, Azimit, Valit) into a single
script. Each invocation outputs exactly ONE unit, determined by --unit.
Run multiple genmon instances, each with a different --unit, to build a
composable panel where every slot is independently movable.

Panel text: the unit's short form (e.g. "An 1992")
Tooltip:    the unit's full form (e.g. "1992 Annit")

EPOCH is proleptic Gregorian April 1, AD 33, 12:15:00 UTC -- the
Humphreys-Waddington crucifixion date (Julian April 3, 14:15 Jerusalem
local time) converted to UTC. CHRONIT_SECONDS is the exact constant from
the cca skillset (janus-units/scripts/).

Annit, Hemerit, and Solit all require the `ephem` package. If ephem is not
installed the script still outputs Dattit, Orit, and all weather units, and
fills in "?" for the ephem-dependent values. Install with:
    pip install ephem --break-system-packages

Weather data is fetched from https://api.open-meteo.com using only the Python
standard library (urllib.request) -- no pip dependencies required for the
weather side. API responses are cached in /tmp to limit fetch frequency.

USAGE:
    python3 panel_genmon.py --unit annit
    python3 panel_genmon.py --unit solit --lokit "Lo w26②⑤4n14③3②"
    python3 panel_genmon.py --unit temp   --lokit "Lo w26②⑤4n14③3②" --cache-minutes 30

ARGUMENTS:
    --unit            Which value to display (required). One of:
                        annit, dattit, orit, hemerit, solit,
                        temp, pressure, wind_speed, wind_dir, wind, precip,
                        lokit
    --lokit           Lokit coordinate of the location (default: R-1992-Q4ETQXWDJD9).
                      Required for Solit and all weather units.
    --sig-digits      Significant digits for continuous Janus values (default 3).
    --cache-minutes   Cache the Open-Meteo API response for this many minutes
                      before re-fetching (default 15). Cache lives in /tmp.

UNITS:
    annit       An <janus>         <janus> Annit
    dattit      Da <janus>         <janus> Dattit
    orit        Or <janus>         <janus> Orit
    hemerit     He <acronym>       spoken calendar date (e.g. Fireday, Aphrodite of Earthweek of Leo)
    solit       So <janus>         <janus> Solit
    temp        Th <janus>         <janus> Thermit
    pressure    Ba <janus>         <janus> Barit
    wind_speed  Ta <janus>         <janus> Tachit
    wind_dir    Az <janus>         <janus> Azimit
    wind        Az <dir> Ta <spd>  <spd> Tachit  <dir> Azimit
    precip      Va <int>           <int> Valit
    lokit       <lokit string>     <lokit string>
"""

import argparse
import json
import math
import urllib.request
from datetime import datetime, timezone

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
_RHOMIT_W  = _DYNIT_N * _MACRIT_M / CHRONIT_SECONDS  # watts per Rhomit
_PLATIT_M2 = _MACRIT_M ** 2                           # m² per Platit
IRRADIANCE_CONV = _RHOMIT_W / _PLATIT_M2              # W/m² per Rhomit/Platit

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
# Weather fetch
# ---------------------------------------------------------------------------


def fetch_weather(lat, lon):
    """Fetch current weather from Open-Meteo.
    lat/lon are standard geographic decimal degrees (lon positive=east)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,surface_pressure,"
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "relative_humidity_2m,cloud_cover,visibility,"
        "shortwave_radiation,precipitation_probability"
        "&wind_speed_unit=ms"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        raw = json.loads(resp.read())
    current = raw["current"]
    return {
        "temp_c":       current["temperature_2m"],
        "pressure_hpa": current["surface_pressure"],
        "wind_ms":      current["wind_speed_10m"],
        "wind_deg":     current["wind_direction_10m"],
        "precip_pct":   current.get("precipitation_probability", 0),
        "feels_c":      current["apparent_temperature"],
        "gusts_ms":     current["wind_gusts_10m"],
        "humidity_pct": current["relative_humidity_2m"],
        "cloud_pct":    current["cloud_cover"],
        "visibility_m":    current["visibility"],
        "irradiance_wm2":  current.get("shortwave_radiation", 0),
    }

# ---------------------------------------------------------------------------
# Build tokens -- merged clock + weather
# ---------------------------------------------------------------------------

_FULL_SIG_DIGITS = 6  # sig digits for tooltip full forms


def _full_notation(value):
    """Janus mantissa digits only, no 'magnitude*' prefix -- for tooltip full
    forms.  Strips the prefix from a 6-sig-digit janus_notation() result so
    the tooltip reads as a plain digit string rather than scientific notation."""
    s = janus_notation(abs(value), sig_digits=_FULL_SIG_DIGITS)
    mantissa = s.split("*", 1)[1] if "*" in s else s
    if value < 0:
        neg_map = {"0": "0", "1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤", "6": "⑥",
                   "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5", "⑥": "6"}
        mantissa = "".join(neg_map.get(c, c) for c in mantissa)
    return mantissa


WEATHER_UNITS = {"temp", "pressure", "wind_speed", "wind_dir", "wind", "precip",
                 "feels", "gusts", "humidity", "cloud", "visibility", "irradiance"}


def build_tokens(now, lokit, sig_digits, need_weather=False):
    # Decode Lokit -> coordinates
    lon_west, lat_north = lokit_decode(lokit)
    ephem_lat = lat_north          # ephem: positive = north
    ephem_lon = -lon_west          # ephem: positive = east; lokit gives west-positive

    # Clock arithmetic (no ephem needed)
    dattit = int((now - EPOCH).days)
    orit = (now - EPOCH).total_seconds() / CHRONIT_SECONDS
    dattit_str = janus_integer(dattit)
    orit_str = janus_notation(orit, sig_digits=sig_digits)
    orit_bare_str = _full_notation(orit)

    tokens = {
        # Clock tokens
        "dattit": dattit_str, "dattit_short": f"Da {dattit_str}", "dattit_full": f"{dattit_str} Dattit",
        "orit": orit_str, "orit_short": f"Or {orit_str}", "orit_full": f"{orit_bare_str} Orit",
        "orit_bare": orit_bare_str,
        "annit": "?", "annit_short": "An ?", "annit_full": "? Annit",
        "hemerit": "?", "hemerit_short": "He ?", "hemerit_full": "? Hemerit",
        "holiday": "", "month": "", "week": "", "weekday": "",
        "day_of_month": "", "day_element": "",
        "date_label": "(needs ephem)",
        "solit": "?", "solit_short": "So ?", "solit_full": "? Solit", "solit_bare": "?",
        # Weather tokens (prefilled with ? for error cases)
        "temp": "?", "temp_short": "Th ?", "temp_full": "? Thermit", "temp_bare": "?",
        "pressure": "?", "pressure_short": "Ba ?", "pressure_full": "? Barit", "pressure_bare": "?",
        "wind_speed": "?", "wind_speed_short": "Ta ?", "wind_speed_full": "? Tachit", "wind_speed_bare": "?",
        "wind_dir": "?", "wind_dir_short": "Az ?", "wind_dir_full": "? Azimit", "wind_dir_bare": "?",
        "wind_short": "Az ? Ta ?", "wind_full": "? Tachit  ? Azimit", "wind_bare": "?  ?",
        "precip": "?", "precip_short": "Va ?", "precip_full": "? Valit",
        "feels": "?", "feels_short": "Th ?", "feels_full": "? Thermit", "feels_bare": "?",
        "gusts": "?", "gusts_short": "Ta ?", "gusts_full": "? Tachit", "gusts_bare": "?",
        "humidity": "?", "humidity_short": "Va ?", "humidity_full": "? Valit",
        "cloud": "?", "cloud_short": "Va ?", "cloud_full": "? Valit",
        "visibility": "?", "visibility_short": "Ma ?", "visibility_full": "? Macrit", "visibility_bare": "?",
        "irradiance": "?", "irradiance_short": "RhPl ?", "irradiance_full": "? Rhomit/Platit", "irradiance_bare": "?",
        "lokit": lokit,
        "lokit_short": lokit,
        "lokit_bare": lokit[3:] if lokit.startswith("Lo ") else lokit,
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
        solit_full_str = janus_mantissa_fixed(solit, fixed_magnitude=-1, sig_digits=5)
        tokens.update({
            "solit": solit_str,
            "solit_short": f"So {solit_str}",
            "solit_full": f"{solit_full_str} Solit",
            "solit_bare": solit_full_str,
        })

    # Weather tokens
    if not need_weather:
        return tokens
    try:
        # Open-Meteo uses standard geo (lon positive=east), same as ephem_lon
        weather = fetch_weather(lat_north, ephem_lon)
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
        temp_fixed_str = janus_mantissa_fixed(temp_th, fixed_magnitude=5, sig_digits=6)
        pres_full_str = _full_notation(pres_ba)
        wspd_full_str = _full_notation(wind_ta)
        wdir_full_str = _full_notation(wdir_az)
        tokens.update({
            "temp": temp_str, "temp_short": f"Th {temp_fixed_str}", "temp_full": f"{temp_fixed_str} Thermit",
            "temp_bare": temp_fixed_str,
            "pressure": pres_str, "pressure_short": f"Ba {pres_str}", "pressure_full": f"{pres_full_str} Barit",
            "pressure_bare": pres_full_str,
            "wind_speed": wspd_str, "wind_speed_short": f"Ta {wspd_str}", "wind_speed_full": f"{wspd_full_str} Tachit",
            "wind_speed_bare": wspd_full_str,
            "wind_dir": wdir_str, "wind_dir_short": f"Az {wdir_str}", "wind_dir_full": f"{wdir_full_str} Azimit",
            "wind_dir_bare": wdir_full_str,
            "wind_short": f"Az {wdir_str} Ta {wspd_str}",
            "wind_full": f"{wspd_full_str} Tachit  {wdir_full_str} Azimit",
            "wind_bare": f"{wdir_full_str}  {wspd_full_str}",
            "precip": prec_str, "precip_short": f"Va {prec_str}", "precip_full": f"{prec_str} Valit",
        })
        feels_th     = (weather["feels_c"] + 273.15) / THERMIT_K
        gusts_ta     = weather["gusts_ms"] / TACHIT_MS
        humidity_va  = round(weather["humidity_pct"])
        cloud_va     = round(weather["cloud_pct"])
        visibility_ma = weather["visibility_m"] / _MACRIT_M
        feels_fixed  = janus_mantissa_fixed(feels_th, fixed_magnitude=5, sig_digits=6)
        gusts_str    = janus_notation(gusts_ta, sig_digits=sig_digits)
        gusts_full_str = _full_notation(gusts_ta)
        hum_str      = janus_integer(humidity_va)
        cld_str      = janus_integer(cloud_va)
        vis_str      = janus_notation(visibility_ma, sig_digits=sig_digits)
        vis_full_str = _full_notation(visibility_ma)
        tokens.update({
            "feels": feels_fixed, "feels_short": f"Th {feels_fixed}",
            "feels_full": f"{feels_fixed} Thermit", "feels_bare": feels_fixed,
            "gusts": gusts_str, "gusts_short": f"Ta {gusts_str}",
            "gusts_full": f"{gusts_full_str} Tachit", "gusts_bare": gusts_full_str,
            "humidity": hum_str, "humidity_short": f"Va {hum_str}", "humidity_full": f"{hum_str} Valit",
            "cloud": cld_str, "cloud_short": f"Va {cld_str}", "cloud_full": f"{cld_str} Valit",
            "visibility": vis_str, "visibility_short": f"Ma {vis_str}",
            "visibility_full": f"{vis_full_str} Macrit", "visibility_bare": vis_full_str,
        })
        irr_rp      = weather["irradiance_wm2"] / IRRADIANCE_CONV
        irr_str     = janus_notation(irr_rp, sig_digits=sig_digits)
        irr_full_str = _full_notation(irr_rp)
        tokens.update({
            "irradiance": irr_str, "irradiance_short": f"RhPl {irr_str}",
            "irradiance_full": f"{irr_full_str} Rhomit/Platit", "irradiance_bare": irr_full_str,
        })
    except Exception:
        pass  # weather tokens stay as "?"

    return tokens

# ---------------------------------------------------------------------------
# Unit registry: unit name -> (short_key, full_key) into tokens dict
# ---------------------------------------------------------------------------
UNIT_DESC = {
    "annit":      "Civilization year",
    "dattit":     "Days since epoch",
    "orit":       "Elapsed Chronits since epoch",
    "hemerit":    "Civil date",
    "solit":      "Time of day (solar noon = 0)",
    "temp":       "Temperature",
    "pressure":   "Atmospheric pressure",
    "wind_speed": "Wind speed",
    "wind_dir":   "Wind direction",
    "wind":       "Wind",
    "precip":     "Precipitation probability",
    "feels":      "Apparent temperature",
    "gusts":      "Wind gusts",
    "humidity":   "Relative humidity",
    "cloud":      "Cloud cover",
    "visibility":  "Visibility",
    "irradiance":  "Solar irradiance",
    "lokit":       "Location",
}

# (short_key, full_key, bare_key)
# short/full used when --label; bare used when label is off (default).
UNIT_DISPLAY = {
    "annit":      ("annit_short",      "annit_full",      "annit"),
    "dattit":     ("dattit_short",     "dattit_full",     "dattit"),
    "orit":       ("orit_short",       "orit_full",       "orit_bare"),
    "hemerit":    ("hemerit_short",    "date_label",      "hemerit"),
    "solit":      ("solit_short",      "solit_full",      "solit_bare"),
    "temp":       ("temp_short",       "temp_full",       "temp_bare"),
    "pressure":   ("pressure_short",   "pressure_full",   "pressure_bare"),
    "wind_speed": ("wind_speed_short", "wind_speed_full", "wind_speed_bare"),
    "wind_dir":   ("wind_dir_short",   "wind_dir_full",   "wind_dir_bare"),
    "wind":       ("wind_short",       "wind_full",       "wind_bare"),
    "precip":     ("precip_short",     "precip_full",     "precip"),
    "feels":      ("feels_short",      "feels_full",      "feels_bare"),
    "gusts":      ("gusts_short",      "gusts_full",      "gusts_bare"),
    "humidity":   ("humidity_short",   "humidity_full",   "humidity"),
    "cloud":      ("cloud_short",      "cloud_full",      "cloud"),
    "visibility":  ("visibility_short",  "visibility_full",  "visibility_bare"),
    "irradiance":  ("irradiance_short",  "irradiance_full",  "irradiance_bare"),
    "lokit":       ("lokit_short",       "lokit_short",      "lokit_bare"),
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unit", required=True, choices=sorted(UNIT_DISPLAY),
                        help="which value to display (see UNITS in --help)")
    parser.add_argument("--label", action="store_true", default=False,
                        help="prefix output with unit abbreviation (e.g. 'Th', 'So')")
    parser.add_argument("--lokit", default=DEFAULT_LOKIT,
                        help="Lokit coordinate for location (Solit + weather)")
    parser.add_argument("--sig-digits", type=int, default=CONTINUOUS_SIG_DIGITS,
                        help=f"significant digits for continuous Janus values (default: {CONTINUOUS_SIG_DIGITS})")
    args = parser.parse_args()

    short_key, full_key, bare_key = UNIT_DISPLAY[args.unit]

    try:
        now = datetime.now(timezone.utc)
        tokens = build_tokens(now, args.lokit, args.sig_digits,
                              need_weather=args.unit in WEATHER_UNITS)
        if args.label:
            label = tokens[short_key]
        else:
            label = tokens[bare_key]
        if args.unit == "hemerit":
            # date_label is itself descriptive text (no numeric value)
            tooltip = tokens["date_label"]
        else:
            tooltip = UNIT_DESC.get(args.unit, "")
    except Exception as exc:
        label = f"[{args.unit} unavailable]"
        tooltip = str(exc)

    print("<txt>" + label + "</txt>")
    print("<tool>" + tooltip.replace("\n", "&#10;") + "</tool>")


if __name__ == "__main__":
    main()
