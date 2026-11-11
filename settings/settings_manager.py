"""Upravljanje podešavanjima aplikacije u JSON fajlu.

Ovaj modul ne zavisi od Qt-a. Podešavanja se spajaju sa podrazumevanim
vrednostima (pa novi ključevi iz kasnijih verzija ne prave problem), upis je
atomski (privremeni fajl + ``os.replace``), a oštećen fajl se premešta u
``.corrupt`` kopiju umesto da sruši aplikaciju.
"""

from __future__ import annotations

from i18n import tr

import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_NAME = "Racunovodstvo"
SETTINGS_VERSION = 1

#: Podrazumevana podešavanja; jedini izvor istine o ključevima i tipovima.
DEFAULTS: dict[str, Any] = {
    "version": SETTINGS_VERSION,
    "first_run": True,
    "appearance": {
        "theme": "light",      # "light" | "dark"
        "accent": "blue",      # "blue" | "green"
        "language": "sr",      # sr | en | hu | de | hr | it | fr | es (vidi i18n.LANGUAGES)
    },
    "window": {
        "width": 1280,
        "height": 800,
        "x": None,
        "y": None,
        "maximized": False,
        "last_page_key": "dashboard",   # ključ stranice iz MainWindow.NAV
    },
    "company": {
        "name": "",
        "pib": "",
        "registration_number": "",
        "address": "",
        "city": "",
        "account": "",
        "email": "",
        "phone": "",
        "currency": "RSD",
        "logo": "",            # putanja do kopije logoa u folderu sa podacima ("" = bez logoa)
        "ips_qr": True,        # NBS IPS QR kod na izlaznim fakturama i predračunima
        "payment_code": "221", # šifra plaćanja u QR kodu (pravna lica; građani uvek 289)
        "payment_model": "00", # model poziva na broj: "00" ili "97"
    },
    "defaults": {
        "vat_category": "Opšta stopa",   # vrsta stope PDV-a za nove stavke (core.VAT_CATEGORIES)
        "payment_term_days": 15,
    },
    "database": {
        "path": "",            # prazno = podrazumevana lokacija u folderu sa podacima
    },
}


def default_data_dir() -> Path:
    """Folder za podešavanja, bazu i keš (zavisi od operativnog sistema)."""
    override = os.environ.get("RACUNOVODSTVO_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def _upgrade_legacy(loaded: dict[str, Any]) -> None:
    """Starija podešavanja: podrazumevana stopa PDV-a kao broj -> vrsta stope."""
    defaults = loaded.get("defaults")
    if isinstance(defaults, dict) and "vat_rate" in defaults and "vat_category" not in defaults:
        defaults["vat_category"] = {"10": "Posebna stopa", "0": "Bez obračuna PDV-a"}.get(
            str(defaults.pop("vat_rate")).split(".")[0], "Opšta stopa")


def _merge(defaults: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    """Rekurzivno spaja učitane vrednosti preko podrazumevanih.

    Ključevi koji ne postoje u ``defaults`` se odbacuju, a vrednost čiji se tip
    razlikuje od podrazumevanog (npr. tekst umesto broja) se zamenjuje.
    """
    result: dict[str, Any] = {}
    for key, default in defaults.items():
        if key not in loaded:
            result[key] = copy.deepcopy(default)
        elif isinstance(default, dict):
            result[key] = _merge(default, loaded[key]) if isinstance(loaded[key], dict) \
                else copy.deepcopy(default)
        elif default is None or isinstance(loaded[key], type(default)) \
                or (isinstance(default, (int, float)) and isinstance(loaded[key], (int, float))
                    and not isinstance(loaded[key], bool)):
            result[key] = loaded[key]
        else:
            result[key] = copy.deepcopy(default)
    return result


class SettingsManager:
    """Čita i piše ``settings.json``; pristup preko putanja tipa ``"company.name"``."""

    def __init__(self, data_dir: Path | None = None, filename: str = "settings.json") -> None:
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.path = self.data_dir / filename
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load_error: str | None = None

    # -- učitavanje / čuvanje ----------------------------------------------- #
    def load(self) -> None:
        """Učitava podešavanja; pri grešci ostaju podrazumevana."""
        self.load_error = None
        if not self.path.exists():
            self._data = copy.deepcopy(DEFAULTS)
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                raise ValueError("koren JSON-a nije objekat")
            _upgrade_legacy(loaded)
            self._data = _merge(DEFAULTS, loaded)
        except (OSError, ValueError) as exc:     # JSONDecodeError je podklasa ValueError
            self.load_error = tr("Podešavanja se ne mogu pročitati ({error}). Korišćene su podrazumevane "
                                 "vrednosti.", error=exc)
            log.error(self.load_error)
            self._quarantine_corrupt_file()
            self._data = copy.deepcopy(DEFAULTS)

    def _quarantine_corrupt_file(self) -> None:
        try:
            self.path.replace(self.path.with_suffix(".json.corrupt"))
        except OSError:
            log.exception("Oštećeni fajl podešavanja nije premešten")

    def save(self) -> bool:
        """Atomski upisuje podešavanja. Vraća ``False`` (uz log) ako upis ne uspe."""
        tmp = self.path.with_suffix(".json.tmp")
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError:
            log.exception("Podešavanja nisu sačuvana (%s)", self.path)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    # -- pristup ---------------------------------------------------------------#
    def get(self, key: str, default: Any = None) -> Any:
        """``get("company.name")``; vraća ``default`` ako ključ ne postoji."""
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any) -> None:
        """``set("appearance.theme", "dark")``; ne upisuje na disk (vidi ``save``)."""
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def section(self, name: str) -> dict[str, Any]:
        """Kopija cele sekcije (npr. ``"company"``) - izmene ne utiču na originale."""
        value = self._data.get(name, {})
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def update_section(self, name: str, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set(f"{name}.{key}", value)

    # -- prečice ----------------------------------------------------------------#
    @property
    def database_path(self) -> Path:
        configured = str(self.get("database.path", "") or "").strip()
        return Path(configured).expanduser() if configured else self.data_dir / "racunovodstvo.db"

    @property
    def company(self) -> dict[str, Any]:
        return self.section("company")
