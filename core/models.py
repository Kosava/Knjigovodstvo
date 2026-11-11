"""Modeli podataka (dataclass-ovi) i njihova validacija."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from i18n import tr, tr_noop

from . import tax, validators
from .errors import ValidationError

# --------------------------------------------------------------------------- #
# Konstante (iste vrednosti se koriste i u CHECK ograničenjima u bazi)
# --------------------------------------------------------------------------- #
PARTNER_CUSTOMER = tr_noop("Kupac")
PARTNER_SUPPLIER = tr_noop("Dobavljač")
PARTNER_BOTH = tr_noop("Kupac i dobavljač")
PARTNER_TYPES = (PARTNER_CUSTOMER, PARTNER_SUPPLIER, PARTNER_BOTH)

ENTITY_LEGAL = tr_noop("Pravno lice")       # domaća firma ili preduzetnik: PIB obavezan
ENTITY_PERSON = tr_noop("Fizičko lice")     # građanin: bez PIB-a
ENTITY_FOREIGN = tr_noop("Strano lice")     # strani partner: strani poreski broj (nije obavezan)
ENTITY_TYPES = (ENTITY_LEGAL, ENTITY_PERSON, ENTITY_FOREIGN)

INVOICE_OUT = tr_noop("Izlazna")  # izdata kupcu (prihod)
INVOICE_IN = tr_noop("Ulazna")    # primljena od dobavljača (rashod)
INVOICE_TYPES = (INVOICE_OUT, INVOICE_IN)

#: Vrsta dokumenta. Predračun i avansni račun postoje samo kao izlazni dokumenti.
DOC_INVOICE = tr_noop("Faktura")
DOC_PROFORMA = tr_noop("Predračun")         # ponuda kupcu: ne knjiži se i ne plaća
DOC_ADVANCE = tr_noop("Avansni račun")      # izdaje se na primljeni avans (sa PDV-om)
DOC_KINDS = (DOC_INVOICE, DOC_PROFORMA, DOC_ADVANCE)
#: Prefiks broja: fakture "2026/0001", predračuni "PR-2026/0001", avansi "AV-2026/0001".
DOC_PREFIXES = {DOC_INVOICE: "", DOC_PROFORMA: "PR-", DOC_ADVANCE: "AV-"}

STATUS_PAID = tr_noop("Plaćeno")
STATUS_UNPAID = tr_noop("Neplaćeno")
STATUS_CANCELLED = tr_noop("Stornirano")   # stornirana faktura i sam storno dokument
STATUS_OPEN = tr_noop("Otvoren")           # predračun bez fakture
STATUS_REALIZED = tr_noop("Realizovan")    # predračun po kome je izdata faktura
INVOICE_STATUSES = (STATUS_PAID, STATUS_UNPAID, STATUS_CANCELLED, STATUS_OPEN, STATUS_REALIZED)

#: Vrste stope PDV-a. Iznos stope zavisi od datuma prometa (tabela "Stope PDV-a" u podešavanjima),
#: pa artikal pamti vrstu stope, a ne broj.
VAT_GENERAL = tr_noop("Opšta stopa")
VAT_REDUCED = tr_noop("Posebna stopa")
VAT_NONE = tr_noop("Bez obračuna PDV-a")   # oslobođen promet ili izdavalac van sistema PDV-a
VAT_CATEGORIES = (VAT_GENERAL, VAT_REDUCED, VAT_NONE)

ARTICLE_GOODS = tr_noop("Roba")
ARTICLE_SERVICE = tr_noop("Usluga")
ARTICLE_KINDS = (ARTICLE_GOODS, ARTICLE_SERVICE)

CASH_IN = tr_noop("Uplata")
CASH_OUT = tr_noop("Isplata")
CASH_TYPES = (CASH_IN, CASH_OUT)

PAY_CASH = tr_noop("Gotovina")   # blagajna
PAY_BANK = tr_noop("Račun")      # tekući račun
PAYMENT_METHODS = (PAY_CASH, PAY_BANK)


# --------------------------------------------------------------------------- #
# Partner
# --------------------------------------------------------------------------- #
@dataclass
class Partner:
    """Kupac i/ili dobavljač."""

    id: int | None = None
    name: str = ""
    pib: str = ""
    address: str = ""
    city: str = ""
    account: str = ""
    partner_type: str = PARTNER_CUSTOMER
    email: str = ""
    phone: str = ""
    entity_type: str = ENTITY_LEGAL

    @property
    def is_foreign(self) -> bool:
        return self.entity_type == ENTITY_FOREIGN

    @property
    def tax_id_label(self) -> str:
        """Natpis za poreski broj na fakturi ("" za fizičko lice)."""
        if self.entity_type == ENTITY_PERSON:
            return ""
        return tr("Poreski broj") if self.is_foreign else tr("PIB")

    def normalize(self) -> None:
        """Uklanja suvišne razmake i dovodi domaći račun u standardni oblik."""
        for attr in ("name", "pib", "address", "city", "account", "email", "phone"):
            setattr(self, attr, (getattr(self, attr) or "").strip())
        if self.entity_type == ENTITY_PERSON:
            self.pib = ""                      # fizičko lice nema PIB
        if not self.is_foreign:                # strani račun (npr. IBAN) ostaje kako je upisan
            self.account = validators.normalize_account(self.account)

    def validate(self) -> None:
        self.normalize()
        errors: list[str] = []
        if not self.name:
            errors.append(tr("Naziv partnera je obavezan."))
        if self.entity_type not in ENTITY_TYPES:
            errors.append(tr("Nepoznata vrsta partnera."))
        elif self.entity_type == ENTITY_LEGAL:
            if not self.pib:
                errors.append(tr("PIB je obavezan za pravno lice."))
            elif not validators.is_valid_pib(self.pib):
                errors.append(tr("PIB nije ispravan (9 cifara sa ispravnom kontrolnom cifrom)."))
        elif self.is_foreign and len(self.pib) > 30:
            errors.append(tr("Strani poreski broj je predugačak (najviše 30 znakova)."))
        if self.account and not self.is_foreign and not validators.is_valid_account(self.account):
            errors.append(tr("Tekući račun nije ispravan (oblik 160-0000000000000-00)."))
        if self.email and not validators.is_valid_email(self.email):
            errors.append(tr("E-adresa nije ispravna."))
        if self.partner_type not in PARTNER_TYPES:
            errors.append(tr("Nepoznat tip partnera."))
        if errors:
            raise ValidationError(errors)

    def matches_invoice_type(self, invoice_type: str) -> bool:
        """Da li partner može biti na fakturi datog tipa."""
        if self.partner_type == PARTNER_BOTH:
            return True
        if invoice_type == INVOICE_OUT:
            return self.partner_type == PARTNER_CUSTOMER
        return self.partner_type == PARTNER_SUPPLIER


# --------------------------------------------------------------------------- #
# Šifarnik artikala i usluga
# --------------------------------------------------------------------------- #
@dataclass
class Article:
    """Artikal ili usluga iz šifarnika; u fakturu se prepisuje kao nova stavka."""

    id: int | None = None
    code: str = ""                       # šifra (nije obavezna, ali je jedinstvena)
    name: str = ""
    kind: str = ARTICLE_SERVICE
    unit: str = "kom"
    price: Decimal = tax.ZERO            # cena bez PDV-a
    vat_category: str = VAT_GENERAL      # vrsta stope; iznos se određuje po datumu prometa
    active: bool = True                  # neaktivan se ne nudi pri unosu fakture
    note: str = ""
    vat_rate: Decimal = tax.DEFAULT_VAT_RATE   # stopa te vrste danas (ne čuva se u bazi)

    @property
    def price_with_vat(self) -> Decimal:
        return tax.calc_line(1, self.price, self.vat_rate).total

    def to_item(self, quantity: Decimal = Decimal("1"), vat_rate: Decimal | None = None) -> "InvoiceItem":
        """Stavka fakture; ``vat_rate`` je stopa na datum prometa (podrazumevano današnja)."""
        rate = self.vat_rate if vat_rate is None else vat_rate
        return InvoiceItem(self.name, quantity, self.unit, self.price, rate)

    def validate(self) -> None:
        errors: list[str] = []
        for attr in ("code", "name", "unit", "note"):
            setattr(self, attr, (getattr(self, attr) or "").strip())
        if not self.name:
            errors.append(tr("Naziv artikla je obavezan."))
        if len(self.code) > 30:
            errors.append(tr("Šifra je predugačka (najviše 30 znakova)."))
        if self.kind not in ARTICLE_KINDS:
            errors.append(tr("Nepoznata vrsta artikla."))
        if not self.unit:
            errors.append(tr("Jedinica mere je obavezna."))
        if self.price < 0:
            errors.append(tr("Cena ne može biti negativna."))
        if self.vat_category not in VAT_CATEGORIES:
            errors.append(tr("Nepoznata vrsta stope PDV-a."))
        if errors:
            raise ValidationError(errors)


@dataclass
class VatRate:
    """Iznos jedne vrste stope PDV-a od datuma ``valid_from`` (do sledeće promene)."""

    id: int | None = None
    category: str = VAT_GENERAL
    rate: Decimal = tax.DEFAULT_VAT_RATE
    valid_from: date = field(default_factory=date.today)

    def validate(self) -> None:
        errors: list[str] = []
        if self.category not in VAT_CATEGORIES:
            errors.append(tr("Nepoznata vrsta stope PDV-a."))
        elif self.category == VAT_NONE:
            if self.rate != 0:
                errors.append(tr("Stopa „Bez obračuna PDV-a“ je uvek 0 %."))
        elif not (Decimal("0") < self.rate < Decimal("100")):
            errors.append(tr("Stopa PDV-a mora biti veća od 0 i manja od 100."))
        if self.rate != self.rate.quantize(Decimal("0.01")):
            errors.append(tr("Stopa PDV-a može imati najviše dve decimale."))
        if errors:
            raise ValidationError(errors)


# --------------------------------------------------------------------------- #
# Faktura i stavke
# --------------------------------------------------------------------------- #
@dataclass
class InvoiceItem:
    """Jedna stavka fakture."""

    description: str = ""
    quantity: Decimal = Decimal("1")
    unit: str = "kom"
    unit_price: Decimal = tax.ZERO
    vat_rate: Decimal = tax.DEFAULT_VAT_RATE
    id: int | None = None

    @property
    def amounts(self) -> tax.Amounts:
        return tax.calc_line(self.quantity, self.unit_price, self.vat_rate)


@dataclass
class AdvanceDeduction:
    """Avansni račun koji se odbija od konačne fakture (ceo iznos avansa)."""

    advance_id: int
    number: str = ""
    issue_date: date | None = None
    amounts: tax.Amounts = tax.EMPTY_AMOUNTS
    breakdown: dict = field(default_factory=dict)    # stopa PDV-a -> Amounts


@dataclass
class Invoice:
    """Faktura sa stavkama. Iznosi se izračunavaju metodom ``recalculate``."""

    id: int | None = None
    invoice_type: str = INVOICE_OUT
    number: str = ""
    issue_date: date = field(default_factory=date.today)
    due_date: date | None = None
    partner_id: int | None = None
    partner_name: str = ""
    status: str = STATUS_UNPAID
    paid_date: date | None = None
    note: str = ""
    net_amount: Decimal = tax.ZERO
    vat_amount: Decimal = tax.ZERO
    total_amount: Decimal = tax.ZERO
    items: list[InvoiceItem] = field(default_factory=list)
    supply_date: date | None = None       # datum prometa; prazno = isti kao datum fakture
    storno_of: int | None = None          # za storno dokument: ID stornirane fakture
    storno_of_number: str = ""            # broj stornirane fakture (samo za prikaz)
    payment_method: str = PAY_BANK        # kad se nova faktura odmah upisuje kao plaćena
    doc_kind: str = DOC_INVOICE           # faktura, predračun ili avansni račun
    source_id: int | None = None          # predračun iz koga je dokument napravljen
    source_number: str = ""               # broj tog predračuna (samo za prikaz)
    advances: list[AdvanceDeduction] = field(default_factory=list)   # odbijeni avansi
    advance_net: Decimal = tax.ZERO       # zbir odbijenih avansa (osnovica, PDV, ukupno)
    advance_vat: Decimal = tax.ZERO
    advance_total: Decimal = tax.ZERO

    # -- izračunavanja ------------------------------------------------------ #
    def recalculate(self) -> tax.Amounts:
        """Preračunava ukupne iznose iz stavki."""
        totals = tax.sum_amounts(item.amounts for item in self.items)
        self.net_amount, self.vat_amount, self.total_amount = totals
        if self.advances:   # bez veza (npr. storno dokument) zbir avansa ostaje kako je upisan
            self.advance_net, self.advance_vat, self.advance_total = tax.sum_amounts(
                a.amounts for a in self.advances)
        return totals

    def vat_breakdown(self):
        """Rekapitulacija PDV-a po stopama."""
        return tax.vat_breakdown((i.vat_rate, i.amounts) for i in self.items)

    def advance_breakdown(self):
        """Odbijeni avansi po stopama PDV-a."""
        lines = [(rate, a) for adv in self.advances for rate, a in adv.breakdown.items()]
        return tax.vat_breakdown(lines)

    @property
    def amount_due(self) -> Decimal:
        """Iznos za uplatu: ukupno umanjeno za odbijene avanse."""
        return self.total_amount - self.advance_total

    # -- stanje ------------------------------------------------------------- #
    @property
    def is_proforma(self) -> bool:
        return self.doc_kind == DOC_PROFORMA

    @property
    def is_advance(self) -> bool:
        return self.doc_kind == DOC_ADVANCE

    @property
    def kind_label(self) -> str:
        """Ključ prevoda za kolonu "Tip": izlazna/ulazna faktura, predračun, avansni račun."""
        return self.invoice_type if self.doc_kind == DOC_INVOICE else self.doc_kind

    @property
    def is_paid(self) -> bool:
        return self.status == STATUS_PAID

    @property
    def is_cancelled(self) -> bool:
        """Stornirana faktura ili sam storno dokument."""
        return self.status == STATUS_CANCELLED

    @property
    def is_storno(self) -> bool:
        return self.storno_of is not None

    @property
    def is_locked(self) -> bool:
        """Plaćena i stornirana faktura se ne menja (plaćenu prvo vratiti na neplaćeno),
        a ni realizovan predračun."""
        return self.is_paid or self.is_cancelled or self.status == STATUS_REALIZED

    @property
    def effective_supply_date(self) -> date:
        return self.supply_date or self.issue_date

    def is_overdue(self, today: date | None = None) -> bool:
        today = today or date.today()
        return self.status == STATUS_UNPAID and self.due_date is not None and self.due_date < today

    # -- validacija --------------------------------------------------------- #
    def validate(self) -> None:
        errors: list[str] = []
        self.number = (self.number or "").strip()
        if self.invoice_type not in INVOICE_TYPES:
            errors.append(tr("Nepoznat tip fakture."))
        if self.doc_kind not in DOC_KINDS:
            errors.append(tr("Nepoznata vrsta dokumenta."))
        elif self.doc_kind != DOC_INVOICE and self.invoice_type != INVOICE_OUT:
            errors.append(tr("Predračun i avansni račun mogu biti samo izlazni dokumenti."))
        if not self.number:
            errors.append(tr("Broj dokumenta je obavezan."))
        if self.partner_id is None:
            errors.append(tr("Izaberite partnera."))
        if self.due_date and self.due_date < self.issue_date:
            errors.append(tr("Rok plaćanja ne može biti pre datuma fakture."))
        if self.status not in INVOICE_STATUSES:
            errors.append(tr("Nepoznat status fakture."))
        elif self.is_proforma:
            if self.status not in (STATUS_OPEN, STATUS_REALIZED):
                errors.append(tr("Predračun se ne plaća – po uplati napravite avansni račun ili fakturu."))
        elif self.status in (STATUS_OPEN, STATUS_REALIZED):
            errors.append(tr("Status „Otvoren“ i „Realizovan“ važe samo za predračun."))
        if self.status == STATUS_CANCELLED:
            errors.append(tr("Status „Stornirano“ se ne bira ručno – koristite „Storniraj“."))
        if self.is_paid and self.paid_date and self.paid_date < self.issue_date:
            errors.append(tr("Datum plaćanja ne može biti pre datuma fakture."))
        if self.is_paid and self.payment_method not in PAYMENT_METHODS:
            errors.append(tr("Nepoznat način plaćanja."))
        if not self.items:
            errors.append(tr("Faktura mora imati bar jednu stavku."))
        errors_before_items = len(errors)
        for pos, item in enumerate(self.items, start=1):
            if not (item.description or "").strip():
                errors.append(tr("Stavka {pos}: opis je obavezan.", pos=pos))
            if item.quantity <= 0:
                errors.append(tr("Stavka {pos}: količina mora biti veća od nule.", pos=pos))
            if item.unit_price < 0:
                errors.append(tr("Stavka {pos}: cena ne može biti negativna.", pos=pos))
            if not (Decimal("0") <= item.vat_rate <= Decimal("100")):
                errors.append(tr("Stavka {pos}: stopa PDV-a mora biti između 0 i 100.", pos=pos))
        items_ok = self.items and len(errors) == errors_before_items
        if items_ok and tax.sum_amounts(i.amounts for i in self.items).total <= 0:
            # Plaćena faktura pravi unos u blagajni, a tamo iznos mora biti > 0.
            errors.append(tr("Ukupan iznos fakture mora biti veći od nule."))
        if self.advances:
            errors.extend(self._advance_errors() if items_ok else [])
        if errors:
            raise ValidationError(errors)

    def _advance_errors(self) -> list[str]:
        """Avans se odbija samo od izlazne fakture, i to najviše do iznosa po svakoj stopi PDV-a."""
        if self.doc_kind != DOC_INVOICE or self.invoice_type != INVOICE_OUT:
            return [tr("Avansi se odbijaju samo od izlazne fakture.")]
        errors = []
        items = self.vat_breakdown()
        for rate, adv in self.advance_breakdown().items():
            have = items.get(rate)
            if have is None or adv.net > have.net:
                errors.append(tr("Avans po stopi {rate} % ({amount}) je veći od osnovice fakture po toj stopi.",
                                 rate=tax.format_quantity(rate), amount=tax.format_money(adv.net)))
        return errors


# --------------------------------------------------------------------------- #
# Blagajna
# --------------------------------------------------------------------------- #
@dataclass
class CashEntry:
    """Uplata ili isplata, gotovinom (blagajna) ili preko tekućeg računa."""

    id: int | None = None
    entry_date: date = field(default_factory=date.today)
    entry_type: str = CASH_IN
    description: str = ""
    amount: Decimal = tax.ZERO
    partner_id: int | None = None
    partner_name: str = ""
    invoice_id: int | None = None
    method: str = PAY_CASH

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.entry_type == CASH_IN else -self.amount

    def validate(self) -> None:
        errors: list[str] = []
        self.description = (self.description or "").strip()
        if self.entry_type not in CASH_TYPES:
            errors.append(tr("Nepoznat tip unosa."))
        if self.method not in PAYMENT_METHODS:
            errors.append(tr("Nepoznat način plaćanja."))
        if not self.description:
            errors.append(tr("Opis je obavezan."))
        if self.amount <= 0:
            errors.append(tr("Iznos mora biti veći od nule."))
        if errors:
            raise ValidationError(errors)
