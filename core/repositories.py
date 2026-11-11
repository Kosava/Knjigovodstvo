"""Repozitorijumi: čitanje i upis partnera, faktura i blagajne.

Sva poslovna pravila (validacija, jedinstvenost broja fakture, povezivanje
plaćene fakture sa blagajnom/računom, zaključavanje i storno, gotovina bez
minusa) nalaze se ovde, a ne u GUI-ju.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterator

from i18n import tr, tr_noop

from . import tax
from .database import DatabaseManager
from .errors import DatabaseError, ValidationError
from .models import (
    CASH_IN,
    CASH_OUT,
    DOC_ADVANCE,
    DOC_INVOICE,
    DOC_PREFIXES,
    DOC_PROFORMA,
    INVOICE_IN,
    INVOICE_OUT,
    PAY_BANK,
    PAY_CASH,
    PAYMENT_METHODS,
    STATUS_CANCELLED,
    STATUS_OPEN,
    STATUS_PAID,
    STATUS_REALIZED,
    STATUS_UNPAID,
    VAT_CATEGORIES,
    VAT_GENERAL,
    VAT_NONE,
    AdvanceDeduction,
    VatRate,
    Article,
    CashEntry,
    Invoice,
    InvoiceItem,
    Partner,
)


def _to_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


# =========================================================================== #
# Partneri
# =========================================================================== #
class PartnerRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Partner:
        return Partner(
            id=row["id"], name=row["name"], pib=row["pib"], address=row["address"],
            city=row["city"], account=row["account"], partner_type=row["partner_type"],
            email=row["email"], phone=row["phone"], entity_type=row["entity_type"],
        )

    def list(self) -> list[Partner]:
        rows = self.db.query("SELECT * FROM partners ORDER BY name COLLATE NOCASE")
        return [self._from_row(r) for r in rows]

    def get(self, partner_id: int) -> Partner | None:
        row = self.db.query_one("SELECT * FROM partners WHERE id = ?", (partner_id,))
        return self._from_row(row) if row else None

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM partners", default=0))

    def _ensure_unique_pib(self, partner: Partner) -> None:
        if not partner.pib:
            return                       # fizička lica i strani partneri bez broja
        row = self.db.query_one(
            "SELECT id, name FROM partners WHERE pib = ? AND id IS NOT ?",
            (partner.pib, partner.id),
        )
        if row:
            raise ValidationError(tr("{label} {pib} već koristi partner „{name}“.",
                                     label=partner.tax_id_label or tr("PIB"), pib=partner.pib,
                                     name=row["name"]))

    def add(self, partner: Partner) -> int:
        partner.validate()
        self._ensure_unique_pib(partner)
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO partners (name, pib, address, city, account, partner_type, email, phone,"
                " entity_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (partner.name, partner.pib, partner.address, partner.city, partner.account,
                 partner.partner_type, partner.email, partner.phone, partner.entity_type),
            )
            partner.id = int(cur.lastrowid)
        return partner.id

    def update(self, partner: Partner) -> None:
        if partner.id is None:
            raise ValidationError(tr("Partner nema ID."))
        partner.validate()
        self._ensure_unique_pib(partner)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE partners SET name=?, pib=?, address=?, city=?, account=?,"
                " partner_type=?, email=?, phone=?, entity_type=? WHERE id=?",
                (partner.name, partner.pib, partner.address, partner.city, partner.account,
                 partner.partner_type, partner.email, partner.phone, partner.entity_type,
                 partner.id),
            )

    def delete(self, partner_id: int) -> None:
        used = self.db.scalar(
            "SELECT COUNT(*) FROM invoices WHERE partner_id = ?", (partner_id,), default=0
        )
        if used:
            raise ValidationError(tr(
                "Partner se ne može obrisati jer ima fakture (broj: {count}). "
                "Prvo obrišite ili prebacite njegove fakture.", count=used))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM partners WHERE id = ?", (partner_id,))


# =========================================================================== #
# Stope PDV-a
# =========================================================================== #
class VatRateRepository:
    """Stope PDV-a po vrsti i datumu od kada važe.

    Stopa za neki dan je poslednja promena te vrste sa ``valid_from <= dan``; za dan pre
    prve promene važi najstarija stopa. Fakture čuvaju broj stope po stavci, pa nova stopa
    ne menja ranije fakture.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _from_row(row: sqlite3.Row) -> VatRate:
        return VatRate(id=row["id"], category=row["category"], rate=tax.to_decimal(row["rate"]),
                       valid_from=date.fromisoformat(row["valid_from"]))

    def list(self) -> list[VatRate]:
        order = " ".join(f"WHEN ? THEN {i}" for i in range(len(VAT_CATEGORIES)))
        rows = self.db.query(f"SELECT * FROM vat_rates ORDER BY CASE category {order} END, valid_from DESC",
                             VAT_CATEGORIES)
        return [self._from_row(r) for r in rows]

    def rate_on(self, category: str, day: date | None = None) -> Decimal:
        day = day or date.today()
        row = self.db.query_one(
            "SELECT rate FROM vat_rates WHERE category = ? ORDER BY valid_from <= ? DESC,"
            " CASE WHEN valid_from <= ? THEN valid_from END DESC, valid_from LIMIT 1",
            (category, day.isoformat(), day.isoformat()))
        if row is None:
            return tax.ZERO if category == VAT_NONE else tax.DEFAULT_VAT_RATE
        return tax.to_decimal(row["rate"])

    def rates_on(self, day: date | None = None) -> dict[str, Decimal]:
        """Vrsta stope -> stopa na dati dan (redosled: opšta, posebna, bez PDV-a)."""
        return {category: self.rate_on(category, day) for category in VAT_CATEGORIES}

    def category_of(self, rate: Decimal, day: date | None = None) -> str | None:
        """Vrsta stope kojoj odgovara broj ``rate`` na dati dan (``None`` ako nijedna)."""
        for category, value in self.rates_on(day).items():
            if value == rate:
                return category
        return None

    def add(self, vat_rate: VatRate) -> int:
        vat_rate.validate()
        if self.db.query_one("SELECT 1 FROM vat_rates WHERE category = ? AND valid_from = ?",
                             (vat_rate.category, vat_rate.valid_from.isoformat())):
            raise ValidationError(tr("Za „{category}“ već postoji stopa od {date}.",
                                     category=tr(vat_rate.category),
                                     date=tax.format_date(vat_rate.valid_from)))
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO vat_rates (category, rate, valid_from) VALUES (?, ?, ?)",
                               (vat_rate.category, f"{vat_rate.rate.normalize():f}",
                                vat_rate.valid_from.isoformat()))
            vat_rate.id = int(cur.lastrowid)
        return vat_rate.id

    def delete(self, rate_id: int) -> None:
        row = self.db.query_one("SELECT category FROM vat_rates WHERE id = ?", (rate_id,))
        if row is None:
            return
        left = self.db.scalar("SELECT COUNT(*) FROM vat_rates WHERE category = ?", (row["category"],), 0)
        if left <= 1:
            raise ValidationError(tr("„{category}“ mora imati bar jednu stopu.", category=tr(row["category"])))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM vat_rates WHERE id = ?", (rate_id,))


# =========================================================================== #
# Šifarnik artikala i usluga
# =========================================================================== #
class ArticleRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.vat_rates = VatRateRepository(db)

    def _from_row(self, row: sqlite3.Row, rates: dict[str, Decimal] | None = None) -> Article:
        rates = rates or self.vat_rates.rates_on()
        return Article(
            id=row["id"], code=row["code"], name=row["name"], kind=row["kind"], unit=row["unit"],
            price=tax.to_decimal(row["price"]), vat_category=row["vat_category"],
            vat_rate=rates.get(row["vat_category"], tax.DEFAULT_VAT_RATE),
            active=bool(row["active"]), note=row["note"],
        )

    def list(self, active_only: bool = False) -> list[Article]:
        sql = "SELECT * FROM articles"
        if active_only:
            sql += " WHERE active = 1"
        rows = self.db.query(sql + " ORDER BY name COLLATE NOCASE")
        rates = self.vat_rates.rates_on()
        return [self._from_row(r, rates) for r in rows]

    def get(self, article_id: int) -> Article | None:
        row = self.db.query_one("SELECT * FROM articles WHERE id = ?", (article_id,))
        return self._from_row(row) if row else None

    def find_by_name(self, name: str) -> Article | None:
        row = self.db.query_one("SELECT * FROM articles WHERE name = ? COLLATE NOCASE",
                                ((name or "").strip(),))
        return self._from_row(row) if row else None

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM articles", default=0))

    def _ensure_unique(self, article: Article) -> None:
        if article.code:
            row = self.db.query_one("SELECT name FROM articles WHERE code = ? AND id IS NOT ?",
                                    (article.code, article.id))
            if row:
                raise ValidationError(tr("Šifru {code} već koristi artikal „{name}“.",
                                         code=article.code, name=row["name"]))
        row = self.db.query_one("SELECT id FROM articles WHERE name = ? COLLATE NOCASE AND id IS NOT ?",
                                (article.name, article.id))
        if row:
            raise ValidationError(tr("Artikal „{name}“ već postoji u šifarniku.", name=article.name))

    def _values(self, a: Article) -> tuple:
        return (a.code, a.name, a.kind, a.unit, str(tax.round_money(a.price)), a.vat_category,
                int(a.active), a.note)

    def add(self, article: Article) -> int:
        article.validate()
        self._ensure_unique(article)
        article.vat_rate = self.vat_rates.rate_on(article.vat_category)
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO articles (code, name, kind, unit, price, vat_category, active, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", self._values(article))
            article.id = int(cur.lastrowid)
        return article.id

    def update(self, article: Article) -> None:
        if article.id is None:
            raise ValidationError(tr("Artikal nema ID."))
        article.validate()
        self._ensure_unique(article)
        with self.db.transaction() as conn:
            conn.execute("UPDATE articles SET code=?, name=?, kind=?, unit=?, price=?, vat_category=?,"
                         " active=?, note=? WHERE id=?", self._values(article) + (article.id,))

    def delete(self, article_id: int) -> None:
        """Briše artikal iz šifarnika; stavke ranijih faktura ostaju kakve su bile."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))


# =========================================================================== #
# Gotovina ne sme u minus
# =========================================================================== #
def _cash_low_point(conn: sqlite3.Connection) -> tuple[int, str | None]:
    """Najniže stanje gotovine (u parama) kroz vreme i datum kad je dostignuto.

    Unosi se slažu po datumu; istog dana uplate idu pre isplata.
    """
    rows = conn.execute(
        "SELECT entry_date, entry_type, amount FROM cash_entries WHERE method = ?"
        " ORDER BY entry_date, CASE entry_type WHEN ? THEN 0 ELSE 1 END, id",
        (PAY_CASH, CASH_IN),
    ).fetchall()
    running = low = 0
    low_date = None
    for entry_date, entry_type, amount in rows:
        running += amount if entry_type == CASH_IN else -amount
        if running < low:
            low, low_date = running, entry_date
    return low, low_date


@contextmanager
def _cash_guarded(db: DatabaseManager) -> Iterator[sqlite3.Connection]:
    """Transakcija koja odbija promenu ako bi gotovina u blagajni otišla u minus.

    Ako je stanje već ranije bilo negativno (stari podaci), odbija se samo promena
    koja ga dodatno pogoršava, da se postojeći podaci i dalje mogu ispravljati.
    """
    with db.transaction() as conn:
        before, _ = _cash_low_point(conn)
        yield conn
        after, when = _cash_low_point(conn)
        if after < 0 and after < before:
            raise ValidationError(tr(
                "Gotovine u blagajni nema dovoljno: stanje bi {date} bilo {amount} RSD. "
                "Proverite datum i iznos, ili izaberite plaćanje preko računa.",
                date=tax.format_date(when), amount=tax.format_money(tax.from_minor(after))))


# =========================================================================== #
# Fakture
# =========================================================================== #
_NUMBER_RE = re.compile(r"^(?:[A-Z]+-)?(\d{4})/(\d+)$")


class InvoiceRepository:
    _SELECT = (
        "SELECT i.*, p.name AS partner_name, s.number AS storno_of_number,"
        " src.number AS source_number FROM invoices i "
        "JOIN partners p ON p.id = i.partner_id "
        "LEFT JOIN invoices s ON s.id = i.storno_of "
        "LEFT JOIN invoices src ON src.id = i.source_id"
    )

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    # -- čitanje -------------------------------------------------------------#
    @staticmethod
    def _from_row(row: sqlite3.Row) -> Invoice:
        return Invoice(
            id=row["id"], invoice_type=row["invoice_type"], number=row["number"],
            issue_date=date.fromisoformat(row["issue_date"]),
            supply_date=_to_date(row["supply_date"]),
            due_date=_to_date(row["due_date"]), partner_id=row["partner_id"],
            partner_name=row["partner_name"], status=row["status"],
            paid_date=_to_date(row["paid_date"]), note=row["note"],
            net_amount=tax.from_minor(row["net_amount"]),
            vat_amount=tax.from_minor(row["vat_amount"]),
            total_amount=tax.from_minor(row["total_amount"]),
            storno_of=row["storno_of"], storno_of_number=row["storno_of_number"] or "",
            doc_kind=row["doc_kind"], source_id=row["source_id"],
            source_number=row["source_number"] or "",
            advance_net=tax.from_minor(row["advance_net"]),
            advance_vat=tax.from_minor(row["advance_vat"]),
            advance_total=tax.from_minor(row["advance_total"]),
        )

    def list(self, invoice_type: str | None = None, status: str | None = None,
             limit: int | None = None, doc_kind: str | None = None) -> list[Invoice]:
        """Fakture bez stavki (za tabele); najnovije prve."""
        sql, params = self._SELECT + " WHERE 1=1", []
        if invoice_type:
            sql += " AND i.invoice_type = ?"
            params.append(invoice_type)
        if doc_kind:
            sql += " AND i.doc_kind = ?"
            params.append(doc_kind)
        if status:
            sql += " AND i.status = ?"
            params.append(status)
        sql += " ORDER BY i.issue_date DESC, i.id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [self._from_row(r) for r in self.db.query(sql, params)]

    def get(self, invoice_id: int, with_items: bool = True) -> Invoice | None:
        row = self.db.query_one(self._SELECT + " WHERE i.id = ?", (invoice_id,))
        if row is None:
            return None
        invoice = self._from_row(row)
        if with_items:
            invoice.items = self._items(invoice_id)
            invoice.advances = self._advances(invoice_id)
        return invoice

    def _items(self, invoice_id: int) -> list[InvoiceItem]:
        rows = self.db.query(
            "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY position, id", (invoice_id,))
        return [
            InvoiceItem(
                id=r["id"], description=r["description"],
                quantity=tax.to_decimal(r["quantity"]), unit=r["unit"],
                unit_price=tax.to_decimal(r["unit_price"]),
                vat_rate=tax.to_decimal(r["vat_rate"]),
            )
            for r in rows
        ]

    def _deduction(self, row: sqlite3.Row) -> AdvanceDeduction:
        items = self._items(row["id"])
        return AdvanceDeduction(
            advance_id=row["id"], number=row["number"],
            issue_date=date.fromisoformat(row["issue_date"]),
            amounts=tax.Amounts(tax.from_minor(row["net_amount"]), tax.from_minor(row["vat_amount"]),
                                tax.from_minor(row["total_amount"])),
            breakdown=dict(tax.vat_breakdown((i.vat_rate, i.amounts) for i in items)),
        )

    def _advances(self, invoice_id: int) -> list[AdvanceDeduction]:
        rows = self.db.query(
            "SELECT a.* FROM invoice_advances l JOIN invoices a ON a.id = l.advance_id"
            " WHERE l.invoice_id = ? ORDER BY a.issue_date, a.id", (invoice_id,))
        return [self._deduction(r) for r in rows]

    def available_advances(self, partner_id: int | None,
                           invoice_id: int | None = None) -> list[AdvanceDeduction]:
        """Plaćeni avansni računi kupca koji još nisu odbijeni ni od jedne druge fakture."""
        if partner_id is None:
            return []
        rows = self.db.query(
            "SELECT a.* FROM invoices a LEFT JOIN invoice_advances l ON l.advance_id = a.id"
            " WHERE a.doc_kind = ? AND a.invoice_type = ? AND a.partner_id = ? AND a.status = ?"
            " AND a.storno_of IS NULL AND (l.invoice_id IS NULL OR l.invoice_id IS ?)"
            " ORDER BY a.issue_date, a.id",
            (DOC_ADVANCE, INVOICE_OUT, partner_id, STATUS_PAID, invoice_id))
        return [self._deduction(r) for r in rows]

    def advance_used_in(self, advance_id: int) -> str | None:
        """Broj fakture od koje je avans odbijen (ili ``None``)."""
        return self.db.scalar(
            "SELECT i.number FROM invoice_advances l JOIN invoices i ON i.id = l.invoice_id"
            " WHERE l.advance_id = ?", (advance_id,))

    def documents_from(self, proforma_id: int) -> list[Invoice]:
        """Dokumenti (avansi i fakture) napravljeni po predračunu."""
        rows = self.db.query(self._SELECT + " WHERE i.source_id = ? ORDER BY i.issue_date, i.id",
                             (proforma_id,))
        return [self._from_row(r) for r in rows]

    def draft_from_proforma(self, proforma_id: int, doc_kind: str = DOC_INVOICE,
                            gross: Decimal | None = None, on: date | None = None) -> Invoice:
        """Nov (nesačuvan) dokument po predračunu.

        * faktura: iste stavke; odmah se odbijaju plaćeni avansi napravljeni po tom predračunu;
        * avansni račun: jedna stavka po stopi PDV-a za primljeni iznos ``gross`` (sa PDV-om),
          raspoređen srazmerno iznosima predračuna po stopama.
        """
        proforma = self.get(proforma_id)
        if proforma is None or not proforma.is_proforma:
            raise ValidationError(tr("Predračun ne postoji."))
        if proforma.status == STATUS_REALIZED:
            raise ValidationError(tr("Po predračunu {number} je već izdata faktura.", number=proforma.number))
        on = max(on or date.today(), proforma.issue_date)
        draft = Invoice(invoice_type=INVOICE_OUT, doc_kind=doc_kind, issue_date=on, supply_date=on,
                        partner_id=proforma.partner_id, partner_name=proforma.partner_name,
                        source_id=proforma.id, source_number=proforma.number, note=proforma.note,
                        due_date=on + ((proforma.due_date - proforma.issue_date)
                                       if proforma.due_date else timedelta(0)))
        if doc_kind == DOC_INVOICE:
            draft.items = [InvoiceItem(i.description, i.quantity, i.unit, i.unit_price, i.vat_rate)
                           for i in proforma.items]
            draft.advances = [a for a in self.available_advances(proforma.partner_id)
                              if self._source_of(a.advance_id) == proforma.id]
        elif doc_kind == DOC_ADVANCE:
            draft.items = self._advance_items(proforma, gross)
            draft.status, draft.paid_date, draft.due_date = STATUS_PAID, on, on
            draft.note = ""
        else:
            raise ValidationError(tr("Nepoznata vrsta dokumenta."))
        draft.recalculate()
        return draft

    def _source_of(self, invoice_id: int) -> int | None:
        return self.db.scalar("SELECT source_id FROM invoices WHERE id = ?", (invoice_id,))

    @staticmethod
    def _advance_items(proforma: Invoice, gross: Decimal | None) -> list[InvoiceItem]:
        proforma.recalculate()
        total = proforma.total_amount
        gross = total if gross is None else tax.round_money(gross)
        if gross <= 0:
            raise ValidationError(tr("Iznos avansa mora biti veći od nule."))
        if gross > total:
            raise ValidationError(tr("Avans ({amount}) je veći od iznosa predračuna ({total}).",
                                     amount=tax.format_money(gross), total=tax.format_money(total)))
        items, left = [], gross
        rates = list(proforma.vat_breakdown().items())
        for index, (rate, amounts) in enumerate(rates):
            share = left if index == len(rates) - 1 else tax.round_money(gross * amounts.total / total)
            left -= share
            net = tax.round_money(share * 100 / (100 + rate))
            label = (tr("Avans po predračunu {number}", number=proforma.number) if len(rates) == 1 else
                     tr("Avans po predračunu {number} (PDV {rate} %)", number=proforma.number,
                        rate=tax.format_quantity(rate)))
            items.append(InvoiceItem(label, Decimal("1"), "usluga", net, rate))
        return items

    def storno_document(self, invoice_id: int) -> Invoice | None:
        """Storno dokument za datu fakturu (ako je stornirana)."""
        row = self.db.query_one(self._SELECT + " WHERE i.storno_of = ?", (invoice_id,))
        return self._from_row(row) if row else None

    def _numbers_in_year(self, year: int, doc_kind: str = DOC_INVOICE) -> list[int]:
        prefix = f"{DOC_PREFIXES[doc_kind]}{year}/"
        rows = self.db.query(
            "SELECT number FROM invoices WHERE invoice_type = ? AND doc_kind = ? AND number LIKE ?",
            (INVOICE_OUT, doc_kind, prefix.replace("%", "") + "%"),
        )
        found = []
        for r in rows:
            tail = r["number"][len(prefix):]
            if r["number"].startswith(prefix) and tail.isdigit():
                found.append(int(tail))
        return found

    def next_number(self, invoice_type: str = INVOICE_OUT, year: int | None = None,
                    doc_kind: str = DOC_INVOICE) -> str:
        """Sledeći slobodan broj: ``GGGG/NNNN``, ``PR-GGGG/NNNN`` (predračun), ``AV-GGGG/NNNN`` (avans)."""
        year = year or date.today().year
        number = max(self._numbers_in_year(year, doc_kind), default=0) + 1
        return f"{DOC_PREFIXES[doc_kind]}{year}/{number:04d}"

    # -- pravila -------------------------------------------------------------#
    def _ensure_unique_number(self, invoice: Invoice) -> None:
        if invoice.invoice_type == INVOICE_OUT:
            row = self.db.query_one(
                "SELECT id FROM invoices WHERE invoice_type = ? AND number = ? AND id IS NOT ?",
                (INVOICE_OUT, invoice.number, invoice.id),
            )
        else:
            row = self.db.query_one(
                "SELECT id FROM invoices WHERE invoice_type = ? AND number = ?"
                " AND partner_id = ? AND id IS NOT ?",
                (INVOICE_IN, invoice.number, invoice.partner_id, invoice.id),
            )
        if row:
            raise ValidationError(tr("Dokument broj {number} već postoji.", number=invoice.number))

    def _ensure_partner_matches(self, invoice: Invoice) -> None:
        """Kupac ide na izlaznu, dobavljač na ulaznu fakturu ("Kupac i dobavljač" na obe)."""
        row = self.db.query_one(
            "SELECT name, partner_type FROM partners WHERE id = ?", (invoice.partner_id,)
        )
        if row is None:
            raise ValidationError(tr("Izabrani partner ne postoji."))
        partner = Partner(name=row["name"], partner_type=row["partner_type"])
        if not partner.matches_invoice_type(invoice.invoice_type):
            if invoice.invoice_type == INVOICE_OUT:
                text = tr_noop("Partner „{name}“ je evidentiran kao „{type}“, a na izlaznoj fakturi "
                               "mora biti kupac. Izaberite drugog partnera ili mu promenite tip u "
                               "modulu „Partneri“.")
            else:
                text = tr_noop("Partner „{name}“ je evidentiran kao „{type}“, a na ulaznoj fakturi "
                               "mora biti dobavljač. Izaberite drugog partnera ili mu promenite tip u "
                               "modulu „Partneri“.")
            raise ValidationError(tr(text, name=partner.name, type=tr(partner.partner_type)))

    @staticmethod
    def _ensure_unlocked(invoice: Invoice, storno: bool = False) -> None:
        """Plaćena i stornirana faktura (i storno dokument) se ne menjaju i ne storniraju."""
        if invoice.status == STATUS_REALIZED:
            text = tr_noop("Po predračunu {number} je izdata faktura, pa se on više ne menja.")
        elif invoice.is_storno:
            text = (tr_noop("Storno dokument {number} se ne može stornirati.") if storno else
                    tr_noop("Storno dokument {number} se ne može menjati."))
        elif invoice.is_cancelled:
            text = (tr_noop("Faktura {number} je već stornirana.") if storno else
                    tr_noop("Faktura {number} je stornirana i ne može se menjati."))
        elif invoice.is_paid:
            text = (tr_noop("Faktura {number} je plaćena i ne može se stornirati. "
                            "Ako je potrebno, prvo je vratite na neplaćeno.") if storno else
                    tr_noop("Faktura {number} je plaćena i ne može se menjati. "
                            "Ako je potrebno, prvo je vratite na neplaćeno."))
        else:
            return
        raise ValidationError(tr(text, number=invoice.number))

    @staticmethod
    def _payment_entry(invoice: Invoice) -> tuple[str, str]:
        """Tip i opis unosa u blagajni za plaćenu fakturu."""
        if invoice.is_advance:
            return CASH_IN, tr("Avans po avansnom računu {number}", number=invoice.number)
        if invoice.invoice_type == INVOICE_OUT:
            return CASH_IN, tr("Naplata fakture {number}", number=invoice.number)
        return CASH_OUT, tr("Plaćanje fakture {number}", number=invoice.number)

    def _check_advances(self, invoice: Invoice) -> None:
        """Odbijeni avansi: plaćeni avansni računi istog kupca, izdati pre fakture, neiskorišćeni."""
        if not invoice.advances:
            return
        if invoice.doc_kind != DOC_INVOICE or invoice.invoice_type != INVOICE_OUT:
            raise ValidationError(tr("Avansi se odbijaju samo od izlazne fakture."))
        allowed = {a.advance_id: a for a in self.available_advances(invoice.partner_id, invoice.id)}
        fresh = []
        for adv in invoice.advances:
            current = allowed.get(adv.advance_id)
            if current is None:
                raise ValidationError(tr("Avansni račun {number} nije moguće odbiti: mora biti plaćen, "
                                         "istog kupca i ne sme biti odbijen od druge fakture.",
                                         number=adv.number or adv.advance_id))
            if current.issue_date > invoice.issue_date:
                raise ValidationError(tr("Avansni račun {number} je izdat posle datuma fakture.",
                                         number=current.number))
            fresh.append(current)
        invoice.advances = fresh          # iznosi uvek iz baze, ne iz forme

    @staticmethod
    def _save_advances(conn: sqlite3.Connection, invoice: Invoice) -> None:
        conn.execute("DELETE FROM invoice_advances WHERE invoice_id = ?", (invoice.id,))
        for adv in invoice.advances:
            conn.execute("INSERT INTO invoice_advances (invoice_id, advance_id) VALUES (?, ?)",
                         (invoice.id, adv.advance_id))

    @staticmethod
    def _refresh_proforma(conn: sqlite3.Connection, proforma_id: int | None) -> None:
        """Predračun je "Realizovan" dok po njemu postoji važeća (nestornirana) faktura."""
        if proforma_id is None:
            return
        used = conn.execute(
            "SELECT 1 FROM invoices WHERE source_id = ? AND doc_kind = ? AND status <> ?",
            (proforma_id, DOC_INVOICE, STATUS_CANCELLED)).fetchone()
        conn.execute("UPDATE invoices SET status = ? WHERE id = ? AND doc_kind = ?",
                     (STATUS_REALIZED if used else STATUS_OPEN, proforma_id, DOC_PROFORMA))

    def _check_source(self, invoice: Invoice) -> None:
        if invoice.source_id is None:
            return
        source = self.get(invoice.source_id, with_items=False)
        if source is None or not source.is_proforma:
            raise ValidationError(tr("Predračun ne postoji."))
        if invoice.doc_kind == DOC_PROFORMA:
            raise ValidationError(tr("Predračun se ne pravi po drugom predračunu."))
        if source.partner_id != invoice.partner_id:
            raise ValidationError(tr("Kupac mora biti isti kao na predračunu {number}.", number=source.number))
        if invoice.doc_kind == DOC_INVOICE and source.status == STATUS_REALIZED:
            raise ValidationError(tr("Po predračunu {number} je već izdata faktura.", number=source.number))

    @staticmethod
    def _insert_items(conn: sqlite3.Connection, invoice_id: int, items: list[InvoiceItem]) -> None:
        for pos, item in enumerate(items, start=1):
            a = item.amounts
            conn.execute(
                "INSERT INTO invoice_items (invoice_id, position, description, quantity, unit,"
                " unit_price, vat_rate, net_amount, vat_amount, total_amount)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (invoice_id, pos, item.description.strip(), str(item.quantity), item.unit.strip(),
                 str(item.unit_price), str(item.vat_rate),
                 tax.to_minor(a.net), tax.to_minor(a.vat), tax.to_minor(a.total)),
            )

    @staticmethod
    def _insert_header(conn: sqlite3.Connection, invoice: Invoice) -> int:
        cur = conn.execute(
            "INSERT INTO invoices (invoice_type, number, issue_date, supply_date, due_date,"
            " partner_id, net_amount, vat_amount, total_amount, status, paid_date, storno_of, note,"
            " doc_kind, source_id, advance_net, advance_vat, advance_total)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (invoice.invoice_type, invoice.number, invoice.issue_date.isoformat(),
             invoice.effective_supply_date.isoformat(), _iso(invoice.due_date), invoice.partner_id,
             tax.to_minor(invoice.net_amount), tax.to_minor(invoice.vat_amount),
             tax.to_minor(invoice.total_amount), invoice.status,
             _iso(invoice.paid_date) if invoice.is_paid else None, invoice.storno_of,
             invoice.note.strip(), invoice.doc_kind, invoice.source_id,
             tax.to_minor(invoice.advance_net), tax.to_minor(invoice.advance_vat),
             tax.to_minor(invoice.advance_total)),
        )
        return int(cur.lastrowid)

    @classmethod
    def _record_payment(cls, conn: sqlite3.Connection, invoice: Invoice, paid_on: date,
                        method: str) -> None:
        """Upisuje naplatu/plaćanje fakture (jedan unos po fakturi).

        Upisuje se iznos za uplatu; ako su avansi pokrili celu fakturu, unosa nema.
        """
        conn.execute("DELETE FROM cash_entries WHERE invoice_id = ?", (invoice.id,))
        if invoice.amount_due <= 0:
            return
        entry_type, text = cls._payment_entry(invoice)
        conn.execute(
            "INSERT INTO cash_entries (entry_date, entry_type, description, amount,"
            " partner_id, invoice_id, method) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (paid_on.isoformat(), entry_type, text, tax.to_minor(invoice.amount_due),
             invoice.partner_id, invoice.id, method),
        )

    # -- upis ----------------------------------------------------------------#
    def add(self, invoice: Invoice) -> int:
        if invoice.is_proforma:
            invoice.status, invoice.paid_date = STATUS_OPEN, None
        if invoice.is_paid and invoice.paid_date is None:
            invoice.paid_date = max(date.today(), invoice.issue_date)
        invoice.storno_of = None            # storno dokumenti nastaju samo preko ``storno``
        if not invoice.advances:
            invoice.advance_net = invoice.advance_vat = invoice.advance_total = tax.ZERO
        invoice.validate()
        self._ensure_unique_number(invoice)
        self._ensure_partner_matches(invoice)
        self._check_source(invoice)
        self._check_advances(invoice)
        invoice.recalculate()
        invoice.validate()                  # ponovo, sa iznosima avansa iz baze
        with _cash_guarded(self.db) as conn:
            invoice.id = self._insert_header(conn, invoice)
            self._insert_items(conn, invoice.id, invoice.items)
            self._save_advances(conn, invoice)
            if invoice.is_paid:
                self._record_payment(conn, invoice, invoice.paid_date, invoice.payment_method)
            self._refresh_proforma(conn, invoice.source_id)
        return invoice.id

    def update(self, invoice: Invoice) -> None:
        """Menja zaglavlje i stavke neplaćene fakture.

        Status se menja samo preko ``set_paid`` i ``storno``; plaćena i stornirana
        faktura su zaključane.
        """
        if invoice.id is None:
            raise ValidationError(tr("Faktura nema ID."))
        current = self.get(invoice.id, with_items=False)
        if current is None:
            raise ValidationError(tr("Faktura ne postoji."))
        self._ensure_unlocked(current)
        invoice.status, invoice.paid_date, invoice.storno_of = current.status, None, None
        # Vrsta dokumenta i veza na predračun se ne menjaju.
        invoice.doc_kind, invoice.source_id = current.doc_kind, current.source_id
        if not invoice.advances:
            invoice.advance_net = invoice.advance_vat = invoice.advance_total = tax.ZERO
        invoice.validate()
        self._ensure_unique_number(invoice)
        # Provera tipa partnera samo kad se partner ili tip fakture menja, da se
        # stare fakture mogu ispraviti i ako je partneru u međuvremenu promenjen tip.
        if (current.partner_id, current.invoice_type) != (invoice.partner_id, invoice.invoice_type):
            self._ensure_partner_matches(invoice)
        if current.source_id is not None and invoice.partner_id != current.partner_id:
            raise ValidationError(tr("Kupac mora biti isti kao na predračunu {number}.",
                                     number=current.source_number))
        self._check_advances(invoice)
        invoice.recalculate()
        invoice.validate()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE invoices SET invoice_type=?, number=?, issue_date=?, supply_date=?,"
                " due_date=?, partner_id=?, net_amount=?, vat_amount=?, total_amount=?, note=?,"
                " advance_net=?, advance_vat=?, advance_total=? WHERE id=?",
                (invoice.invoice_type, invoice.number, invoice.issue_date.isoformat(),
                 invoice.effective_supply_date.isoformat(), _iso(invoice.due_date),
                 invoice.partner_id, tax.to_minor(invoice.net_amount),
                 tax.to_minor(invoice.vat_amount), tax.to_minor(invoice.total_amount),
                 invoice.note.strip(), tax.to_minor(invoice.advance_net),
                 tax.to_minor(invoice.advance_vat), tax.to_minor(invoice.advance_total), invoice.id),
            )
            conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice.id,))
            self._insert_items(conn, invoice.id, invoice.items)
            self._save_advances(conn, invoice)

    def set_paid(self, invoice_id: int, paid: bool, paid_on: date | None = None,
                 method: str = PAY_BANK) -> None:
        """Označava fakturu plaćenom/neplaćenom i upisuje/briše naplatu."""
        invoice = self.get(invoice_id, with_items=False)
        if invoice is None:
            raise ValidationError(tr("Faktura ne postoji."))
        if invoice.is_proforma:
            raise ValidationError(tr("Predračun se ne plaća – po uplati napravite avansni račun ili fakturu."))
        if invoice.is_cancelled:
            text = (tr_noop("Storno dokument {number} se ne plaća.") if invoice.is_storno else
                    tr_noop("Stornirana faktura {number} se ne plaća."))
            raise ValidationError(tr(text, number=invoice.number))
        if not paid and invoice.is_advance:
            used_in = self.advance_used_in(invoice.id)
            if used_in:
                raise ValidationError(tr("Avansni račun {number} je odbijen od fakture {invoice}. "
                                         "Prvo ga uklonite iz te fakture.",
                                         number=invoice.number, invoice=used_in))
        if paid:
            paid_on = paid_on or max(date.today(), invoice.issue_date)
            if paid_on < invoice.issue_date:
                raise ValidationError(tr("Datum plaćanja ne može biti pre datuma fakture."))
            if method not in PAYMENT_METHODS:
                raise ValidationError(tr("Nepoznat način plaćanja."))
            if invoice.total_amount <= 0:
                raise ValidationError(tr(
                    "Faktura sa iznosom 0 ne može se označiti plaćenom (nema šta da se upiše u blagajnu)."))
            invoice.advances = self._advances(invoice.id)
        with _cash_guarded(self.db) as conn:
            if paid:
                conn.execute(
                    "UPDATE invoices SET status=?, paid_date=? WHERE id=?",
                    (STATUS_PAID, paid_on.isoformat(), invoice_id),
                )
                self._record_payment(conn, invoice, paid_on, method)
            else:
                conn.execute(
                    "UPDATE invoices SET status=?, paid_date=NULL WHERE id=?",
                    (STATUS_UNPAID, invoice_id),
                )
                conn.execute("DELETE FROM cash_entries WHERE invoice_id = ?", (invoice_id,))

    def can_delete(self, invoice: Invoice) -> str | None:
        """``None`` ako se faktura sme obrisati, inače razlog (za poruku korisniku)."""
        if invoice.is_proforma:
            if invoice.status == STATUS_REALIZED:
                return tr("Po predračunu {number} je izdata faktura, pa se on čuva kao trag.",
                          number=invoice.number)
            return None          # predračun nije poreska isprava: rupa u brojevima nije problem
        if invoice.is_storno or invoice.is_cancelled:
            text = (tr_noop("Storno dokument {number} se ne briše – ostaje kao trag u evidenciji.")
                    if invoice.is_storno else
                    tr_noop("Stornirana faktura {number} se ne briše – ostaje kao trag u evidenciji."))
            return tr(text, number=invoice.number)
        if invoice.is_paid:
            return tr("Faktura {number} je plaćena. Prvo je vratite na neplaćeno "
                      "(ili je stornirajte ako je izdata greškom).", number=invoice.number)
        if invoice.invoice_type == INVOICE_OUT:
            match = _NUMBER_RE.match(invoice.number)
            if match and int(match.group(2)) < max(
                    self._numbers_in_year(int(match.group(1)), invoice.doc_kind), default=0):
                return tr("Izlazna faktura {number} nije poslednja u numeraciji, pa bi brisanjem "
                          "nastala rupa u brojevima. Umesto brisanja je stornirajte.",
                          number=invoice.number)
        if invoice.is_advance:
            used_in = self.advance_used_in(invoice.id)
            if used_in:
                return tr("Avansni račun {number} je odbijen od fakture {invoice}. "
                          "Prvo ga uklonite iz te fakture.", number=invoice.number, invoice=used_in)
        return None

    def delete(self, invoice_id: int) -> None:
        """Briše neplaćenu ulaznu fakturu ili poslednju izlaznu (bez rupe u numeraciji)."""
        invoice = self.get(invoice_id, with_items=False)
        if invoice is None:
            return
        reason = self.can_delete(invoice)
        if reason:
            raise ValidationError(reason)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM cash_entries WHERE invoice_id = ?", (invoice_id,))
            # Dokumenti napravljeni po obrisanom predračunu ostaju, samo bez veze.
            conn.execute("UPDATE invoices SET source_id = NULL WHERE source_id = ?", (invoice_id,))
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            self._refresh_proforma(conn, invoice.source_id)

    def storno(self, invoice_id: int, storno_date: date | None = None) -> int:
        """Stornira izlaznu fakturu: pravi storno dokument sa negativnim iznosima.

        Originalna faktura dobija status "Stornirano", a storno dokument dobija
        sledeći broj u numeraciji i ponavlja stavke sa negativnom količinom, pa se
        prihod i PDV iz te dve fakture međusobno poništavaju.
        """
        original = self.get(invoice_id)
        if original is None:
            raise ValidationError(tr("Faktura ne postoji."))
        if original.invoice_type != INVOICE_OUT:
            raise ValidationError(tr("Storniraju se izlazne fakture. Pogrešnu ulaznu fakturu obrišite "
                                     "ili tražite storno od dobavljača."))
        if original.is_proforma:
            raise ValidationError(tr("Predračun se ne stornira – nije poreska isprava, pa se može obrisati."))
        self._ensure_unlocked(original, storno=True)
        storno_date = storno_date or max(date.today(), original.issue_date)
        if storno_date < original.issue_date:
            raise ValidationError(tr("Datum storna ne može biti pre datuma fakture."))
        doc = Invoice(
            invoice_type=INVOICE_OUT, doc_kind=original.doc_kind,
            number=self.next_number(INVOICE_OUT, storno_date.year, original.doc_kind),
            issue_date=storno_date, supply_date=original.effective_supply_date,
            due_date=storno_date, partner_id=original.partner_id, status=STATUS_CANCELLED,
            note=tr("Storno fakture {number}", number=original.number), storno_of=original.id,
            items=[InvoiceItem(i.description, -i.quantity, i.unit, i.unit_price, i.vat_rate)
                   for i in original.items],
            # Odbijeni avansi se poništavaju zajedno sa fakturom i postaju ponovo slobodni.
            advance_net=-original.advance_net, advance_vat=-original.advance_vat,
            advance_total=-original.advance_total, source_id=original.source_id,
        )
        doc.recalculate()
        with self.db.transaction() as conn:
            doc.id = self._insert_header(conn, doc)
            self._insert_items(conn, doc.id, doc.items)
            conn.execute("UPDATE invoices SET status=? WHERE id=?", (STATUS_CANCELLED, original.id))
            conn.execute("DELETE FROM invoice_advances WHERE invoice_id = ?", (original.id,))
            self._refresh_proforma(conn, original.source_id)
        return doc.id


# =========================================================================== #
# Blagajna i račun
# =========================================================================== #
class CashRepository:
    _SELECT = (
        "SELECT c.*, COALESCE(p.name, '') AS partner_name FROM cash_entries c "
        "LEFT JOIN partners p ON p.id = c.partner_id"
    )

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _from_row(row: sqlite3.Row) -> CashEntry:
        return CashEntry(
            id=row["id"], entry_date=date.fromisoformat(row["entry_date"]),
            entry_type=row["entry_type"], description=row["description"],
            amount=tax.from_minor(row["amount"]), partner_id=row["partner_id"],
            partner_name=row["partner_name"], invoice_id=row["invoice_id"],
            method=row["method"],
        )

    def list(self) -> list[CashEntry]:
        rows = self.db.query(self._SELECT + " ORDER BY c.entry_date DESC, c.id DESC")
        return [self._from_row(r) for r in rows]

    def get(self, entry_id: int) -> CashEntry | None:
        row = self.db.query_one(self._SELECT + " WHERE c.id = ?", (entry_id,))
        return self._from_row(row) if row else None

    def add(self, entry: CashEntry) -> int:
        entry.validate()
        with _cash_guarded(self.db) as conn:
            cur = conn.execute(
                "INSERT INTO cash_entries (entry_date, entry_type, description, amount,"
                " partner_id, invoice_id, method) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry.entry_date.isoformat(), entry.entry_type, entry.description,
                 tax.to_minor(entry.amount), entry.partner_id, entry.invoice_id, entry.method),
            )
            entry.id = int(cur.lastrowid)
        return entry.id

    def _ensure_not_linked(self, entry_id: int) -> None:
        """Unos nastao iz plaćene fakture menja se samo preko fakture."""
        row = self.db.query_one(
            "SELECT i.number FROM cash_entries c JOIN invoices i ON i.id = c.invoice_id"
            " WHERE c.id = ?", (entry_id,))
        if row:
            raise ValidationError(tr(
                "Ovaj unos je nastao iz plaćene fakture {number}. Promenu uradite u modulu „Fakture“ "
                "(vraćanje na neplaćeno pa ponovo označavanje kao plaćene).", number=row["number"]))

    def update(self, entry: CashEntry) -> None:
        if entry.id is None:
            raise ValidationError(tr("Unos nema ID."))
        self._ensure_not_linked(entry.id)
        entry.validate()
        with _cash_guarded(self.db) as conn:
            conn.execute(
                "UPDATE cash_entries SET entry_date=?, entry_type=?, description=?, amount=?,"
                " partner_id=?, method=? WHERE id=?",
                (entry.entry_date.isoformat(), entry.entry_type, entry.description,
                 tax.to_minor(entry.amount), entry.partner_id, entry.method, entry.id),
            )

    def delete(self, entry_id: int) -> None:
        self._ensure_not_linked(entry_id)
        with _cash_guarded(self.db) as conn:
            conn.execute("DELETE FROM cash_entries WHERE id = ?", (entry_id,))

    def balance(self, method: str | None = None) -> Decimal:
        """Stanje: ukupno, ili samo gotovina (``PAY_CASH``) / samo račun (``PAY_BANK``)."""
        sql = "SELECT SUM(CASE entry_type WHEN ? THEN amount ELSE -amount END) FROM cash_entries"
        params: list = [CASH_IN]
        if method:
            sql += " WHERE method = ?"
            params.append(method)
        return tax.from_minor(self.db.scalar(sql, params, default=0))


__all__ = [
    "PartnerRepository", "ArticleRepository", "VatRateRepository", "InvoiceRepository", "CashRepository",
    "DatabaseError", "ValidationError",
]
