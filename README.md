# Računovodstvo – evidencija poslovanja (Python + PyQt6 + SQLite)

Desktop aplikacija za partnere (kupci/dobavljači), šifarnik artikala i usluga, fakture sa PDV-om,
predračune, avansne račune i blagajnu.

## Pokretanje

```bash
pip install -r requirements.txt     # na Arch Linuxu: sudo pacman -S python-pyqt6 qt6-svg qt6-translations python-segno
python main.py
```

Pri prvom pokretanju program nudi demo podatke (izmišljeni partneri, fakture i blagajna).
Ikonica programa je `assets/icon.svg` (za prikaz SVG-a Qt koristi `qt6-svg`; `qt6-translations`
prevodi standardna dugmad u Qt dijalozima, npr. „Otvori“ / „Otkaži“).

## Jezik

Program je podrazumevano na srpskom (latinica, ekavica). U **Podešavanja → Jezik** može se
izabrati i engleski, mađarski, nemački, hrvatski, italijanski, francuski ili španski; promena
važi posle ponovnog pokretanja, koje program nudi odmah.

* Uz jezik se menjaju i formati: datum (npr. `24.05.2026.`, `24/05/2026`, `2026.05.24.`) i
  separatori u iznosima (`1.234,56`, `1,234.56`, `1 234,56`).
* Vrednosti u bazi (statusi, vrste partnera, način plaćanja...) ostaju na srpskom i prevode se
  samo za prikaz, pa promena jezika ne dira postojeće podatke.
* Demo podaci (nazivi partnera i opisi stavki) su na srpskom.

Prevodi su u `i18n/<kod>.py` – rečnik `TRANSLATIONS` gde je ključ srpski tekst iz koda. Novi
jezik se dodaje ovako:

1. dodati unos u `LANGUAGES` u `i18n/__init__.py` (naziv, formati datuma, separatori);
2. napraviti `i18n/<kod>.py` po uzoru na postojeće;
3. `python -m i18n.extract <kod>` ispisuje tekstove koji nedostaju ili su višak;
4. `python -m pytest tests/test_i18n.py` proverava da su svi tekstovi prevedeni i da se
   zamene `{...}` i prečice `&` poklapaju.

Prevode je pisao program, pa ih je dobro da pogleda neko kome je taj jezik maternji.

## Testovi

Core sloj nema Qt, pa se testira bez ekrana:

```bash
pip install pytest                  # na Arch Linuxu: sudo pacman -S python-pytest
python -m pytest
```

Testovi su u `tests/` i svaki radi nad svojom privremenom bazom (ne dira pravu bazu).

## Struktura

```
main.py                 ulazna tačka (QApplication, jezik, tema, baza, prozor)
assets/icon.svg         ikonica programa
i18n/                   prevodi: __init__.py (tr, jezici i formati), extract.py, en/hu/de/hr/it/fr/es.py
core/                   poslovna logika, bez GUI koda
  database.py             SQLite konekcija, šema i migracije, transakcije, rezervna kopija
  models.py               Partner, Invoice, InvoiceItem, CashEntry + validacija
  repositories.py         upis/čitanje; pravila (jedinstven broj fakture, plaćanje -> blagajna)
  tax.py                  PDV (Decimal, half-up), formatiranje 1.234,56
  validators.py           PIB (ISO 7064), tekući račun (mod 97), e-pošta
  stats.py                podaci za Dashboard
  printing.py             HTML fakture za štampu/PDF (logo, IPS QR kod)
  ips_qr.py               sadržaj NBS IPS QR koda i slika QR koda
  demo_data.py            demo podaci
gui/                    PyQt6 prozori, stranice i dijalozi
  main_window.py          bočna navigacija, meni, statusna traka
  dashboard_page.py · partners_page.py · invoices_page.py · cash_page.py · settings_page.py
  articles_page.py        šifarnik artikala i izbor artikala za fakturu
  invoice_dialog.py       forma fakture sa PDV-om uživo
  table_models.py         generički model + proxy (sortiranje, pretraga u realnom vremenu)
settings/               konfiguracija
  settings_manager.py     JSON podešavanja (atomski upis, zaštita od oštećenog fajla)
  theme_manager.py        Light/Dark QSS, akcentne boje (plava / tamnozelena)
tests/                  pytest testovi za core (tax, validators, repozitorijumi, stats,
                          gotovina/račun, storno, vrste partnera, migracije, vraćanje kopije,
                          prevodi i ekavica – test_i18n.py,
                          artikli, predračuni i avansi – test_documents.py,
                          IPS QR i logo – test_ips_qr.py, stope PDV-a – test_vat_rates.py)
```

## Gde su podaci

Podešavanja (`settings.json`), baza (`racunovodstvo.db`) i log se čuvaju u
`~/.config/Racunovodstvo/` (Linux), `%APPDATA%\Racunovodstvo` (Windows) ili
`~/Library/Application Support/Racunovodstvo` (macOS). Folder se može promeniti
promenljivom okruženja `RACUNOVODSTVO_HOME`.

## Napomene o ponašanju

* Fakture mogu biti **izlazne** (kupcima) i **ulazne** (od dobavljača). Broj izlazne fakture
  se predlaže automatski (`GGGG/NNNN`) i prati godinu iz datuma fakture.
* Faktura ima **datum prometa**; podrazumevano je isti kao datum fakture i štampa se na fakturi.
* Na izlaznoj fakturi može biti samo kupac, na ulaznoj samo dobavljač (partner tipa
  „Kupac i dobavljač“ može na obe). Ukupan iznos fakture mora biti veći od nule.
* Označavanje fakture kao **plaćene** upisuje uplatu/isplatu sa izabranim **načinom plaćanja**:
  „Gotovina“ (blagajna) ili „Račun“ (tekući račun, podrazumevano). Vraćanje na neplaćeno taj
  unos uklanja. Datum plaćanja ne može biti pre datuma fakture.
* **Gotovina u blagajni ne može u minus** – ni ukupno ni na bilo koji dan. Provera ide hronološki,
  pa se ne može isplatiti gotovina koja je uplaćena tek kasnije. Promet po računu nema tu proveru.
* **Plaćena faktura je zaključana** (izmena i brisanje tek kad se vrati na neplaćeno).
* Izlazna faktura se **briše samo ako je poslednja u numeraciji**; ostale se **storniraju**:
  nastaje storno dokument sa sledećim brojem i negativnim iznosima, a original dobija status
  „Stornirano“. Prihod i PDV se tako poništavaju bez rupe u brojevima. Stornirane fakture i
  storno dokumenti se ne menjaju i ne brišu. Neplaćena ulazna faktura se može obrisati.
* Partner ima **vrstu**: „Pravno lice“ (domaće, PIB obavezan i proverava se kontrolnom cifrom),
  „Fizičko lice“ (bez PIB-a) ili „Strano lice“ (strani poreski broj nije obavezan, račun može biti
  IBAN). Popunjen PIB / poreski broj mora biti jedinstven.
* Partner sa fakturama ne može se obrisati.

### Stope PDV-a

* Stope se ne nalaze u kodu, nego u tabeli **Podešavanja → Stope PDV-a**: vrsta stope (opšta,
  posebna, bez obračuna PDV-a), procenat i datum od kada važi. Početno su upisane stope od uvođenja
  PDV-a: opšta 18 % pa 20 % od 1. 10. 2012, posebna 8 % pa 10 % od 1. 1. 2014.
* Kad država propiše novu stopu, dodaje se red („Nova stopa…“) sa datumom od kada važi – nova
  verzija programa nije potrebna.
* Na fakturi se stopa bira prema **datumu prometa**: promena datuma prometa menja iznos stope, a
  stavka zadržava vrstu (opšta / posebna). Artikal u šifarniku pamti vrstu stope, ne broj.
* Stavke sačuvanih faktura čuvaju svoj procenat, pa se ranije fakture nikad ne menjaju; stavka čiji
  procenat ne odgovara nijednoj vrsti na taj dan ostaje sa svojim procentom.
* Podrazumevana vrsta stope za nove stavke bira se u „Podrazumevane vrednosti za nove fakture“.

### Logo i IPS QR kod

* U **Podešavanja → Podaci o firmi** može se izabrati logo (PNG, JPG, SVG…). Kopira se u folder sa
  podacima programa, pa ne zavisi od originalnog fajla, i štampa se iznad podataka izdavaoca.
* Na neplaćenim izlaznim fakturama i predračunima štampa se **NBS IPS QR kod** sa računom za
  uplatu i pozivom na broj, koji kupac skenira aplikacijom banke. Iznos je iznos **za uplatu**
  (umanjen za avanse). QR kod se ne štampa na plaćenim, storniranim i avansnim računima, ni
  kad valuta nije RSD ili tekući račun firme nije ispravan.
* Sadržaj prati preporuke NBS: račun kao 18 cifara, iznos `RSD1234,5`, naziv i mesto primaoca,
  podaci platioca, šifra plaćanja (podrazumevano 221; za kupca fizičko lice uvek 289), svrha
  „Faktura 2026/0001“ i poziv na broj po modelu 00 (`00 2026-0001`) ili 97 (sa kontrolnim brojem).
* QR kod pravi biblioteka `segno`; ako nije instalirana, faktura se štampa bez QR koda.

### Šifarnik artikala i usluga

* Modul **Artikli** čuva robu i usluge sa šifrom, jedinicom mere, cenom bez PDV-a i stopom PDV-a.
  Šifra nije obavezna, ali je jedinstvena; naziv je jedinstven.
* U fakturi se artikal bira kucanjem dela naziva u opisu stavke (padajuća lista popuni jedinicu,
  cenu i PDV) ili dugmetom **Iz šifarnika…** (više artikala odjednom).
* Stavka se u fakturu prepisuje, pa izmena ili brisanje artikla ne menja ranije fakture.
  Artikal koji se više ne koristi može se označiti kao neaktivan (ne nudi se pri unosu).

### Predračuni

* Predračun je ponuda kupcu sa posebnom numeracijom `PR-GGGG/NNNN`. Nije poreska isprava: ne ulazi
  u potraživanja, PDV ni prihode, ne plaća se i ne stornira (može se obrisati).
* Izabrani predračun se dugmetom **Iz predračuna** pretvara u fakturu (iste stavke) ili u avansni
  račun. Kad se po njemu izda faktura, predračun dobija status „Realizovan“ i zaključava se;
  ako se ta faktura obriše ili stornira, predračun je ponovo „Otvoren“.

### Avansni računi

* Avansni račun (`AV-GGGG/NNNN`) se izdaje na primljeni avans i nosi PDV na avans. Napravljen iz
  predračuna, primljeni iznos se raspoređuje po stopama PDV-a srazmerno predračunu; podrazumevano
  je odmah plaćen, pa se uplata upisuje u blagajnu ili promet po računu.
* U konačnoj fakturi istog kupca označavaju se avansi koji se odbijaju (faktura iz predračuna
  sama označi avanse napravljene po tom predračunu). Odbija se ceo avans, samo plaćen, izdat pre
  fakture i najviše do osnovice fakture po svakoj stopi PDV-a.
* Na fakturi se štampaju umanjenje za avans i iznos **za uplatu**; pri naplati se upisuje samo
  taj ostatak. Ako avans pokriva celu fakturu, novi unos u blagajni se ne pravi.
* PDV: avansni račun nosi PDV na avans, a konačna faktura samo razliku. Prihod je osnovica cele
  konačne fakture (avans nije prihod).
* Iskorišćen avans se ne vraća na neplaćeno i ne briše; prvo se ukloni iz fakture. Storno konačne
  fakture oslobađa njene avanse.
* Unos u blagajni koji je nastao iz plaćene fakture menja se samo preko fakture.
* **Rezervna kopija** se pravi i vraća u Podešavanjima. Pre vraćanja se trenutna baza čuva kao
  `racunovodstvo.pre-vracanja-<datum_vreme>.db`, a starija kopija se odmah nadograđuje.
* Iznosi se u bazi čuvaju kao celi brojevi (pare), računaju se sa `Decimal`.
* PIB i tekući račun se proveravaju kontrolnom cifrom; demo podaci imaju ispravne vrednosti.

## Nadogradnja baze

Baza iz prethodne verzije se pri prvom pokretanju sama nadograđuje na tekuću šemu (5).
Pre toga se pravi rezervna kopija pored baze (`racunovodstvo.pre-v2.db`, `racunovodstvo.pre-v3.db`
`racunovodstvo.pre-v4.db` ili `racunovodstvo.pre-v5.db`, zavisno od toga sa koje verzije se kreće).

* 1 → 2: postojeće naplate faktura se vode kao plaćanje preko računa, ručni unosi kao gotovina,
  a datum prometa dobija vrednost datuma fakture.
* 2 → 3: svi postojeći partneri postaju „Pravno lice“.
* 3 → 4: sve postojeće fakture postaju vrsta „Faktura“; dodaju se šifarnik artikala, predračuni i
  avansni računi.
* 4 → 5: dodaje se tabela stopa PDV-a; artikli umesto procenta dobijaju vrstu stope
  (20 → opšta, 10 → posebna, 0 → bez obračuna PDV-a).
