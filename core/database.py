"""SQLite sloj: otvaranje baze, šema, transakcije i rezervna kopija."""

from __future__ import annotations

from i18n import tr

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from . import models
from .errors import DatabaseError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 5


def _in_list(values: Sequence[str]) -> str:
    """Pravi ``'a', 'b'`` za CHECK (kolona IN (...)) ograničenja."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


#: Tabela partnera. PIB je obavezan samo za domaće pravno lice (provera u modelu),
#: pa je jedinstven samo kad je popunjen (indeks ``ux_partners_pib``).
_PARTNERS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    entity_type   TEXT NOT NULL DEFAULT '{legal}'
                  CHECK (entity_type IN ({entities})),
    pib           TEXT NOT NULL DEFAULT '',     -- PIB ili strani poreski broj
    address       TEXT NOT NULL DEFAULT '',
    city          TEXT NOT NULL DEFAULT '',
    account       TEXT NOT NULL DEFAULT '',
    partner_type  TEXT NOT NULL DEFAULT '{customer}'
                  CHECK (partner_type IN ({types})),
    email         TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _partners_table(name: str = "partners") -> str:
    return _PARTNERS_TABLE.format(
        name=name, legal=models.ENTITY_LEGAL, entities=_in_list(models.ENTITY_TYPES),
        customer=models.PARTNER_CUSTOMER, types=_in_list(models.PARTNER_TYPES))


#: Tabela faktura (koristi se i za novu bazu i za prebacivanje stare u migraciji).
_INVOICES_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_type  TEXT NOT NULL
                  CHECK (invoice_type IN ({types})),
    number        TEXT NOT NULL,
    issue_date    TEXT NOT NULL,
    supply_date   TEXT,                         -- datum prometa
    due_date      TEXT,
    partner_id    INTEGER NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    net_amount    INTEGER NOT NULL DEFAULT 0,   -- u parama
    vat_amount    INTEGER NOT NULL DEFAULT 0,   -- u parama
    total_amount  INTEGER NOT NULL DEFAULT 0,   -- u parama
    status        TEXT NOT NULL DEFAULT '{unpaid}'
                  CHECK (status IN ({statuses})),
    paid_date     TEXT,
    storno_of     INTEGER REFERENCES invoices(id) ON DELETE RESTRICT,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    doc_kind      TEXT NOT NULL DEFAULT '{invoice}'
                  CHECK (doc_kind IN ({kinds})),
    source_id     INTEGER REFERENCES invoices(id) ON DELETE SET NULL,   -- predračun
    advance_net   INTEGER NOT NULL DEFAULT 0,   -- odbijeni avansi, u parama
    advance_vat   INTEGER NOT NULL DEFAULT 0,
    advance_total INTEGER NOT NULL DEFAULT 0
);
"""


#: Način plaćanja: gotovina (blagajna) ili tekući račun.
_METHOD_COLUMN = (f"TEXT NOT NULL DEFAULT '{models.PAY_CASH}'"
                  f" CHECK (method IN ({_in_list(models.PAYMENT_METHODS)}))")

#: Migracija 1 -> 2: tabela faktura dobija datum prometa, vezu na storno i status
#: "Stornirano" (CHECK se u SQLite-u ne može izmeniti, pa se tabela prepisuje),
#: a blagajna dobija način plaćanja. Postojeće naplate faktura se vode kao "Račun".
_INVOICE_COLUMNS_V1 = ("id, invoice_type, number, issue_date, due_date, partner_id, net_amount,"
                       " vat_amount, total_amount, status, paid_date, note, created_at")


def _invoices_table(name: str = "invoices") -> str:
    return _INVOICES_TABLE.format(
        name=name, types=_in_list(models.INVOICE_TYPES), unpaid=models.STATUS_UNPAID,
        statuses=_in_list(models.INVOICE_STATUSES), invoice=models.DOC_INVOICE,
        kinds=_in_list(models.DOC_KINDS))


def _migration_v2_sql() -> str:
    return f"""
PRAGMA foreign_keys = OFF;
BEGIN;
{_invoices_table("invoices_v2")}
INSERT INTO invoices_v2 ({_INVOICE_COLUMNS_V1}, supply_date)
    SELECT {_INVOICE_COLUMNS_V1}, issue_date FROM invoices;
DROP TABLE invoices;
ALTER TABLE invoices_v2 RENAME TO invoices;
ALTER TABLE cash_entries ADD COLUMN method {_METHOD_COLUMN};
UPDATE cash_entries SET method = '{models.PAY_BANK}' WHERE invoice_id IS NOT NULL;
PRAGMA user_version = 2;
COMMIT;
PRAGMA foreign_keys = ON;
"""


#: Migracija 2 -> 3: partner dobija vrstu (pravno / fizičko / strano lice), a PIB više
#: nije obavezan za sve (UNIQUE na koloni se menja delimičnim indeksom).
_PARTNER_COLUMNS_V2 = ("id, name, pib, address, city, account, partner_type, email, phone,"
                       " created_at")


def _migration_v3_sql() -> str:
    return f"""
PRAGMA foreign_keys = OFF;
BEGIN;
{_partners_table("partners_v3")}
INSERT INTO partners_v3 ({_PARTNER_COLUMNS_V2}) SELECT {_PARTNER_COLUMNS_V2} FROM partners;
DROP TABLE partners;
ALTER TABLE partners_v3 RENAME TO partners;
PRAGMA user_version = 3;
COMMIT;
PRAGMA foreign_keys = ON;
"""


#: Migracija 3 -> 4: vrsta dokumenta (predračun, avansni račun), veza na predračun i
#: odbijeni avansi; nove tabele (šifarnik artikala, veze avansa) pravi SCHEMA_SQL.
_INVOICE_COLUMNS_V3 = _INVOICE_COLUMNS_V1 + ", supply_date, storno_of"


def _migration_v4_sql() -> str:
    return f"""
PRAGMA foreign_keys = OFF;
BEGIN;
{_invoices_table("invoices_v4")}
INSERT INTO invoices_v4 ({_INVOICE_COLUMNS_V3}) SELECT {_INVOICE_COLUMNS_V3} FROM invoices;
DROP TABLE invoices;
ALTER TABLE invoices_v4 RENAME TO invoices;
PRAGMA user_version = 4;
COMMIT;
PRAGMA foreign_keys = ON;
"""


#: Šifarnik artikala; od verzije 5 artikal pamti vrstu stope PDV-a umesto broja.
_ARTICLES_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT '{service}'
                  CHECK (kind IN ({kinds})),
    unit          TEXT NOT NULL DEFAULT 'kom',
    price         TEXT NOT NULL DEFAULT '0',     -- bez PDV-a
    vat_category  TEXT NOT NULL DEFAULT '{general}'
                  CHECK (vat_category IN ({categories})),
    active        INTEGER NOT NULL DEFAULT 1,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _articles_table(name: str = "articles") -> str:
    return _ARTICLES_TABLE.format(
        name=name, service=models.ARTICLE_SERVICE, kinds=_in_list(models.ARTICLE_KINDS),
        general=models.VAT_GENERAL, categories=_in_list(models.VAT_CATEGORIES))


#: Stope PDV-a u Srbiji od uvođenja PDV-a (2005): opšta 18 % pa 20 % od 1. 10. 2012,
#: posebna 8 % pa 10 % od 1. 1. 2014. Nove stope korisnik dodaje u podešavanjima.
INITIAL_VAT_RATES = (
    (models.VAT_GENERAL, "18", "2005-01-01"), (models.VAT_GENERAL, "20", "2012-10-01"),
    (models.VAT_REDUCED, "8", "2005-01-01"), (models.VAT_REDUCED, "10", "2014-01-01"),
    (models.VAT_NONE, "0", "2005-01-01"),
)


def _migration_v5_sql(conn: sqlite3.Connection) -> str:
    """Migracija 4 -> 5: artikal dobija vrstu stope umesto broja (20 -> opšta, 10 -> posebna,
    0 -> bez PDV-a, ostalo -> opšta). Tabelu stopa pravi i puni ``_initialize_schema``."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    rewrite = ""
    if "vat_rate" in columns:           # šifarnik iz verzije 4 (u starijim bazama ga nema)
        rewrite = f"""
{_articles_table("articles_v5")}
INSERT INTO articles_v5 (id, code, name, kind, unit, price, vat_category, active, note, created_at)
    SELECT id, code, name, kind, unit, price,
           CASE CAST(vat_rate AS REAL) WHEN 10 THEN '{models.VAT_REDUCED}'
                                       WHEN 0 THEN '{models.VAT_NONE}' ELSE '{models.VAT_GENERAL}' END,
           active, note, created_at FROM articles;
DROP TABLE articles;
ALTER TABLE articles_v5 RENAME TO articles;"""
    return f"""
PRAGMA foreign_keys = OFF;
BEGIN;{rewrite}
PRAGMA user_version = 5;
COMMIT;
PRAGMA foreign_keys = ON;
"""


SCHEMA_SQL = f"""
{_partners_table()}
CREATE UNIQUE INDEX IF NOT EXISTS ux_partners_pib ON partners(pib) WHERE pib <> '';

{_invoices_table()}
CREATE INDEX IF NOT EXISTS ix_invoices_partner ON invoices(partner_id);
CREATE INDEX IF NOT EXISTS ix_invoices_issue   ON invoices(issue_date);
CREATE INDEX IF NOT EXISTS ix_invoices_status  ON invoices(status);
-- Izlazne fakture: broj je jedinstven; ulazne: jedinstven po dobavljaču.
CREATE UNIQUE INDEX IF NOT EXISTS ux_invoices_out_number
    ON invoices(number) WHERE invoice_type = '{models.INVOICE_OUT}';
CREATE UNIQUE INDEX IF NOT EXISTS ux_invoices_in_number
    ON invoices(partner_id, number) WHERE invoice_type = '{models.INVOICE_IN}';
-- Faktura se može stornirati samo jednom.
CREATE UNIQUE INDEX IF NOT EXISTS ux_invoices_storno
    ON invoices(storno_of) WHERE storno_of IS NOT NULL;

CREATE TABLE IF NOT EXISTS invoice_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id    INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL DEFAULT 0,
    description   TEXT NOT NULL,
    quantity      TEXT NOT NULL DEFAULT '1',
    unit          TEXT NOT NULL DEFAULT 'kom',
    unit_price    TEXT NOT NULL DEFAULT '0',
    vat_rate      TEXT NOT NULL DEFAULT '20',
    net_amount    INTEGER NOT NULL DEFAULT 0,
    vat_amount    INTEGER NOT NULL DEFAULT 0,
    total_amount  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_items_invoice ON invoice_items(invoice_id);

CREATE TABLE IF NOT EXISTS cash_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date    TEXT NOT NULL,
    entry_type    TEXT NOT NULL
                  CHECK (entry_type IN ({_in_list(models.CASH_TYPES)})),
    description   TEXT NOT NULL,
    amount        INTEGER NOT NULL CHECK (amount > 0),   -- u parama
    partner_id    INTEGER REFERENCES partners(id) ON DELETE SET NULL,
    invoice_id    INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    method        {_METHOD_COLUMN}
);
CREATE INDEX IF NOT EXISTS ix_cash_date ON cash_entries(entry_date);

CREATE INDEX IF NOT EXISTS ix_invoices_source ON invoices(source_id);

-- Avansni računi odbijeni od konačne fakture; jedan avans se koristi samo jednom.
CREATE TABLE IF NOT EXISTS invoice_advances (
    invoice_id    INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    advance_id    INTEGER NOT NULL UNIQUE REFERENCES invoices(id) ON DELETE RESTRICT,
    PRIMARY KEY (invoice_id, advance_id)
);

-- Šifarnik artikala i usluga
{_articles_table()}CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_code ON articles(code) WHERE code <> '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_name ON articles(name COLLATE NOCASE);

-- Stope PDV-a po vrsti i datumu od kada važe
CREATE TABLE IF NOT EXISTS vat_rates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category      TEXT NOT NULL CHECK (category IN ({_in_list(models.VAT_CATEGORIES)})),
    rate          TEXT NOT NULL,
    valid_from    TEXT NOT NULL,
    UNIQUE (category, valid_from)
);
"""


class DatabaseManager:
    """Upravlja jednom SQLite konekcijom za celu aplikaciju."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # -- otvaranje / zatvaranje -------------------------------------------- #
    def open(self) -> None:
        """Otvara (i po potrebi kreira) bazu i inicijalizuje šemu."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._initialize_schema()
            log.info("Baza otvorena: %s", self.path)
        except (sqlite3.Error, OSError) as exc:
            self._conn = None
            raise DatabaseError(tr("Baza se ne može otvoriti ({path}): {error}", path=self.path, error=exc)) from exc

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - zatvaranje ne sme rušiti izlaz
                log.exception("Greška pri zatvaranju baze")
            finally:
                self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError(tr("Baza podataka nije otvorena."))
        return self._conn

    @property
    def status_text(self) -> str:
        return tr("Povezana · {name}", name=self.path.name) if self.is_open else tr("Nije povezana")

    # -- šema ----------------------------------------------------------------#
    def _initialize_schema(self) -> None:
        conn = self.conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoices'").fetchone()
        if has_tables and version < SCHEMA_VERSION:
            self._migrate(version)
        conn.executescript(SCHEMA_SQL)          # nova baza + indeksi (i posle migracije)
        if not conn.execute("SELECT 1 FROM vat_rates LIMIT 1").fetchone():
            conn.executemany("INSERT INTO vat_rates (category, rate, valid_from) VALUES (?, ?, ?)",
                             INITIAL_VAT_RATES)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _migrate(self, version: int) -> None:
        """Nadograđuje šemu korak po korak; pre toga pravi rezervnu kopiju baze.

        * 1 -> 2: datum prometa, storno, način plaćanja
        * 2 -> 3: vrsta partnera, PIB nije obavezan za fizička i strana lica
        * 3 -> 4: predračuni, avansni računi, šifarnik artikala
        * 4 -> 5: stope PDV-a po datumu; artikal pamti vrstu stope
        """
        version = max(version, 1)          # baza bez oznake verzije je iz prve verzije
        backup = self.path.with_name(f"{self.path.stem}.pre-v{version + 1}{self.path.suffix}")
        if not backup.exists():
            self.backup(backup)
            log.info("Rezervna kopija pre migracije: %s", backup)
        steps = {2: lambda _c: _migration_v2_sql(), 3: lambda _c: _migration_v3_sql(),
                 4: lambda _c: _migration_v4_sql(), 5: _migration_v5_sql}
        for target in range(version + 1, SCHEMA_VERSION + 1):
            try:
                self.conn.executescript(steps[target](self.conn))
            except sqlite3.Error:
                self.conn.rollback()
                self.conn.execute("PRAGMA foreign_keys = ON")
                raise
            problems = self.conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:   # pragma: no cover - ne bi smelo da se desi
                raise sqlite3.IntegrityError(
                    f"Migracija je narušila veze među tabelama: {problems[:3]}")
            log.info("Baza migrirana na verziju %d", target)

    # -- upiti -------------------------------------------------------------- #
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transakcija: commit na uspeh, rollback na bilo koju grešku."""
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            log.exception("SQL greška (rollback)")
            raise DatabaseError(tr("Greška baze podataka: {error}", error=exc)) from exc
        except Exception:
            conn.rollback()
            raise

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """SELECT koji vraća sve redove."""
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            log.exception("SQL greška: %s", sql)
            raise DatabaseError(tr("Greška pri čitanju iz baze: {error}", error=exc)) from exc

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        return default if row is None or row[0] is None else row[0]

    # -- rezervna kopija ------------------------------------------------------#
    def backup(self, destination: str | Path) -> Path:
        """Pravi konzistentnu kopiju baze (SQLite backup API)."""
        destination = Path(destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            target = sqlite3.connect(str(destination))
            try:
                self.conn.backup(target)
            finally:
                target.close()
        except (sqlite3.Error, OSError) as exc:
            raise DatabaseError(tr("Rezervna kopija nije napravljena: {error}", error=exc)) from exc
        return destination

    @staticmethod
    def check_backup_file(source: str | Path) -> int:
        """Proverava da je fajl baza ovog programa; vraća verziju njene šeme."""
        source = Path(source)
        if not source.is_file():
            raise DatabaseError(tr("Fajl ne postoji: {path}", path=source))
        try:
            con = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise DatabaseError(tr("Rezervna kopija je oštećena (provera integriteta nije prošla)."))
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                version = con.execute("PRAGMA user_version").fetchone()[0]
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(tr("Izabrani fajl nije SQLite baza: {error}", error=exc)) from exc
        needed = {"partners", "invoices", "invoice_items", "cash_entries"}
        if not needed <= tables:
            raise DatabaseError(tr("Izabrani fajl nije baza ovog programa (nedostaju tabele: {tables}).",
                                   tables=", ".join(sorted(needed - tables))))
        if version > SCHEMA_VERSION:
            raise DatabaseError(tr("Rezervna kopija je iz novije verzije programa. "
                                   "Nadogradite program pa pokušajte ponovo."))
        return version

    def restore(self, source: str | Path) -> Path:
        """Zamenjuje sadržaj baze rezervnom kopijom.

        Pre toga se trenutna baza čuva kao ``<baza>.pre-vracanja-GGGGMMDD_HHMMSS.db``,
        pa se vraćanje može poništiti. Starija kopija se odmah nadograđuje na tekuću šemu.
        Vraća putanju do sačuvane trenutne baze.
        """
        source = Path(source)
        self.check_backup_file(source)
        if source.resolve() == self.path.resolve():
            raise DatabaseError(tr("Izabran je fajl trenutne baze, a ne rezervna kopija."))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety = self.backup(self.path.with_name(f"{self.path.stem}.pre-vracanja-{stamp}{self.path.suffix}"))
        try:
            src = sqlite3.connect(str(source))
            try:
                src.backup(self.conn)
            finally:
                src.close()
            self.conn.execute("PRAGMA foreign_keys = ON")
            self._initialize_schema()
        except sqlite3.Error as exc:
            raise DatabaseError(tr("Vraćanje nije uspelo: {error}. Trenutna baza je sačuvana u {path}.",
                                   error=exc, path=safety)) from exc
        log.info("Baza vraćena iz %s (prethodno stanje: %s)", source, safety)
        return safety
