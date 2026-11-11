"""Prevodi i formati po jeziku (bez Qt-a, pa ga koristi i core).

Izvorni tekstovi u kodu su na srpskom (latinica, ekavica) i služe kao ključevi:

    tr("Faktura {number} je sačuvana.", number="2026/0001")

Za ostale jezike prevod se traži u rečniku ``TRANSLATIONS`` iz modula
``i18n/<kod>.py``. Ako prevod ne postoji, prikazuje se srpski tekst, pa program
nikad ne pukne zbog nedostajućeg prevoda (test ``tests/test_i18n.py`` proverava
da nijedan ne fali).

Vrednosti koje se čuvaju u bazi ("Izlazna", "Plaćeno", "Gotovina"...) ostaju na
srpskom i prevode se samo pri prikazu, preko istog ``tr``.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "sr"


@dataclass(frozen=True)
class Language:
    code: str
    name: str            # naziv na tom jeziku (za izbor u podešavanjima)
    qt_language: str     # naziv člana QLocale.Language
    qt_country: str      # naziv člana QLocale.Country
    qt_script: str       # naziv člana QLocale.Script ("" = podrazumevano)
    date_qt: str         # format za QDateEdit
    date_py: str         # format za strftime
    thousands: str       # separator hiljada
    decimal: str         # decimalni separator


LANGUAGES: dict[str, Language] = {
    "sr": Language("sr", "Srpski", "Serbian", "Serbia", "LatinScript", "dd.MM.yyyy.", "%d.%m.%Y.", ".", ","),
    "en": Language("en", "English", "English", "UnitedKingdom", "", "dd/MM/yyyy", "%d/%m/%Y", ",", "."),
    "hu": Language("hu", "Magyar", "Hungarian", "Hungary", "", "yyyy.MM.dd.", "%Y.%m.%d.", " ", ","),
    "de": Language("de", "Deutsch", "German", "Germany", "", "dd.MM.yyyy", "%d.%m.%Y", ".", ","),
    "hr": Language("hr", "Hrvatski", "Croatian", "Croatia", "", "dd.MM.yyyy.", "%d.%m.%Y.", ".", ","),
    "it": Language("it", "Italiano", "Italian", "Italy", "", "dd/MM/yyyy", "%d/%m/%Y", ".", ","),
    "fr": Language("fr", "Français", "French", "France", "", "dd/MM/yyyy", "%d/%m/%Y", " ", ","),
    "es": Language("es", "Español", "Spanish", "Spain", "", "dd/MM/yyyy", "%d/%m/%Y", ".", ","),
}

_current: str = DEFAULT_LANGUAGE
_table: dict[str, str] = {}


def set_language(code: str) -> str:
    """Postavlja jezik programa; nepoznat kod daje srpski. Vraća kod koji je postavljen."""
    global _current, _table
    code = code if code in LANGUAGES else DEFAULT_LANGUAGE
    table: dict[str, str] = {}
    if code != DEFAULT_LANGUAGE:
        try:
            table = dict(importlib.import_module(f"{__name__}.{code}").TRANSLATIONS)
        except (ImportError, AttributeError):   # pragma: no cover - fajl prevoda uvek postoji
            log.exception("Prevod za jezik %s nije učitan", code)
            code = DEFAULT_LANGUAGE
    _current, _table = code, table
    return code


def current() -> Language:
    return LANGUAGES[_current]


def current_code() -> str:
    return _current


def tr(text: str, /, **values: object) -> str:
    """Prevod teksta na trenutni jezik, uz zamenu ``{naziv}`` vrednostima."""
    translated = _table.get(text, text) if _table else text
    if values:
        try:
            return translated.format(**values)
        except (KeyError, IndexError, ValueError):   # loš prevod ne sme da sruši program
            log.error("Neispravan prevod (%s): %r", _current, translated)
            return text.format(**values)
    return translated


def tr_noop(text: str) -> str:
    """Označava tekst za prevod bez prevođenja (za konstante koje se prevode kasnije)."""
    return text


__all__ = ["LANGUAGES", "DEFAULT_LANGUAGE", "Language", "set_language", "current", "current_code",
           "tr", "tr_noop"]
