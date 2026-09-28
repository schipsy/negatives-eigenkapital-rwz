# negatives-eigenkapital-rwz

> **English summary:** Companion code to the RWZ article series *Anhangangaben zum negativen Eigenkapital – Eine KI-gestützte Analyse* (Part II).
> It documents how structured annual accounts were retrieved from the Austrian commercial register's High-Value Dataset (HVD) and how the § 225 (1) UGB notes on negative equity were analysed in three LLM stages (Qwen via Ollama; GPT-5.2 and text-embedding-ada-002 via Azure OpenAI).
> The repository contains code and prompts only: no data and no results.

## Zweck

Dieses Repository ist der Begleitcode zur Beitragsserie „Anhangangaben zum negativen Eigenkapital – Eine KI-gestützte Analyse“ in der RWZ, Teil II (Empirische Befunde). Es dokumentiert den Analyseweg: wie die Anhangangaben nach § 225 Abs 1 UGB aus dem High-Value-Dataset (HVD) des Firmenbuchs abgerufen und in drei Stufen mit Sprachmodellen analysiert wurden.

**Nicht enthalten sind:**

- Daten: keine Firmenbuchdaten, keine Anhangtexte, keine Listen von Firmenbuchnummern;
- Ergebnisse: weder Zwischenstände noch Klassifikationsergebnisse;
- die Auswahl der untersuchten Anhangangaben und sämtliche Auswertungen (Tabellen, Abbildungen, Statistik);
- Zugangsschlüssel: Sie sind nur als Platzhalter in `.env.example` hinterlegt.

Die Skripte sind bereinigte Fassungen der verwendeten Skripte. Versuchszweige, Auswertungen, lokale Pfade und Zugangsdaten wurden entfernt. Die Prompts in `prompts/` sind die deutschsprachigen Originale.

## Analyseweg im Überblick

| Schritt | Skript | Modell bzw. Schnittstelle | Ergebnis |
|---|---|---|---|
| 1 Abruf und Extraktion | `01_hvd_abruf/hvd_abruf.js` | SOAP-Schnittstelle des Firmenbuchs (HVD) | je Rechtsträger: Eigenkapital (HGB_224_3_A) und Text der Anhangangabe |
| Stufe 1: deduktive Klassifikation | `02_klassifikation_stufe1_qwen/klassifikation_stufe1.js` | Qwen, lokal über Ollama | je Anhangangabe und Merkmal: 0 oder 1 |
| Stufe 2: induktive Extraktion | `03_extraktion_stufe2_gpt/extraktion_stufe2.py` | GPT-5.2 (Azure OpenAI) | je Anhangangabe: Begründungen mit Oberkategorie; Substanztiefe |
| Stufe 2: Deduplizierung | `03_extraktion_stufe2_gpt/deduplizierung.py` | text-embedding-ada-002 (Azure OpenAI) | zusammengeführte Begründungstypen |
| Stufe 3: Brauchbarkeit | `04_bewertung_stufe3/bewertung_stufe3.py` | GPT-5.2 (Azure OpenAI) | je Anhangangabe: eher brauchbar, eher unbrauchbar oder unbrauchbar |

### Schritt 1: HVD-Abruf und Extraktion der Anhangangabe

Ausgangspunkt ist ein Verzeichnis mit Firmenbuch-Kurzinformationen: je Rechtsträger eine XML-Datei (`AUSZUG_V2_RESPONSE`, Umfang „Kurzinformation“). Das Skript geht für jeden Rechtsträger so vor:

1. Firmenbuchnummer und Status aus der Kurzinformation lesen. Gelöschte Rechtsträger werden nicht abgefragt.
2. `SUCHEURKUNDE`: Die Urkundenliste wird abgefragt. Gewählt wird die strukturiert eingereichte Urkunde (`CONTENTTYPE` `application/xml`) mit dem jüngsten Bilanzstichtag.
3. `URKUNDE`: Das XML des Jahresabschlusses wird abgerufen und aus Base64 dekodiert.
4. Extraktion: Das Eigenkapital wird aus `HGB_224_3_A/BETRAG` gelesen. Ist es negativ, wird der Text der Anhangangabe aus `HGB_224_3_A/ANHANG` übernommen, ersatzweise aus `HGB_Form_3_5/TEXT`. Fehlt beides, wird „Anhang nicht gefunden“ vermerkt, bei nicht negativem Eigenkapital „HGB_224_3_A ist nicht negativ“.

Textaufbereitung: Übernommen wird der reine Textinhalt des jeweiligen XML-Elements. Der Parser löst dabei die XML-Kodierung auf. Darüber hinaus wird der Text nicht verändert.

Die Ausgabe ist eine JSON-Zeile je Rechtsträger (NDJSON) mit den Feldern `filename`, `fnr`, `status`, `rechtsform`, `stichtag`, `eingereicht`, `einordnungUGB` (Größenklasse), `klassifizierung`, `hgbform`, `abschlussart`, `hgb224Betrag` (Eigenkapital), `hgb224Analysis` (Text der Anhangangabe oder Vermerk), `error` und `timestamp`. Ein abgebrochener Lauf wird beim nächsten Aufruf fortgesetzt.

### Übergabe an die Stufen 1 bis 3

Die Stufen 1 bis 3 erwarten eine Excel-Tabelle mit einer Zeile je Anhangangabe und den Spalten `Firma` (Firmenbuchnummer), `Anhangtext` und `Negatives Eigenkapital` (Betrag). Welche Anhangangaben in diese Tabelle aufgenommen werden, ist nicht Teil dieses Repositorys.

### Stufe 1: deduktive Klassifikation (Qwen über Ollama)

- Prompts: `prompts/stufe1_qwen/`, eine Datei je Merkmal. Die vier Begründungskategorien sind Patronats- bzw. Haftungserklärung, stille Reserven, positive Fortbestehensprognose und Gesellschafterdarlehen. Drei weitere Merkmale betreffen die Aussage zur insolvenzrechtlichen Überschuldung, deren Begründung und die Quantifizierung.
- Für stille Reserven gab es einen Folgelauf mit zwei präzisierten Prompts: `prompts/stufe1_qwen_folgelauf_stille_reserven/`.
- Jede Anhangangabe wird mit jedem Prompt einzeln an das Modell geschickt. Die Nachricht besteht aus dem Prompt, einem Zeilenumbruch und dem Anhangtext. Das Modell antwortet mit 0 oder 1.
- Gibt das Modell einen Denkprozess (`<think>…</think>`) aus, wird er abgetrennt und in einer eigenen Spalte gespeichert.
- Das Modell wird lokal über Ollama ausgeführt. Die Anhangtexte verlassen in dieser Stufe also das lokale Netzwerk nicht. Das Ollama-Modell wird mit `--modell` angegeben. Die Temperatur beträgt 0,7 (Voreinstellung).

### Stufe 2: induktive Extraktion und Deduplizierung (GPT-5.2, Embeddings)

**Extraktion** (`extraktion_stufe2.py`, Prompts `prompts/stufe2_extraktion_system.txt` und `prompts/stufe2_extraktion_user.txt`):

- Je Anhangangabe erfolgt ein Aufruf im JSON-Modus.
- Für jede Begründung liefert das Modell eine Kurzbezeichnung (`typ`, höchstens fünf Wörter), ein wörtliches Zitat, die Oberkategorie, ob die Begründung quantifiziert ist, und gegebenenfalls einen Betrag.
- Dazu kommen Metaangaben, darunter die Substanztiefe (`qualitaet`: SUBSTANZIELL, PAUSCHAL oder LEER).
- Die Zahl der Begründungen je Anhangangabe ist nicht begrenzt.

**Zuordnung zu den sechs Oberkategorien:** Das Modell ordnet jede einzelne Nennung im selben Aufruf einer Kategorie zu:

| Code im Prompt | Oberkategorie |
|---|---|
| `PROGNOSE` | Fortbestehensprognose |
| `RANGRUECKTRITT` | Rangrücktrittserklärung |
| `FINANZIERUNG` | Gesellschafterfinanzierung |
| `VERMÖGEN` | Stille Reserven / Vermögenswerte |
| `HAFTUNG` | Patronats- / Haftungserklärung |
| `SONSTIGE` | Sonstige Begründungen |

**Deduplizierung** (`deduplizierung.py`):

1. Alle Kurzbezeichnungen werden gesammelt und gezählt.
2. Jede eindeutige Bezeichnung wird mit text-embedding-ada-002 kodiert (1.536 Dimensionen).
3. Bezeichnungen mit einer Kosinus-Ähnlichkeit von mindestens 0,92 werden zusammengeführt. Das Verfahren ist gierig (greedy): Jede Gruppe erhält die häufigste noch nicht zugeordnete Bezeichnung als Namen.
4. Ketten von Zuordnungen (A → B, B → C) werden transitiv aufgelöst.
5. Die bereinigten Typen werden auf die Anhangangaben übertragen. Gleiche Kombinationen aus Typ und Kategorie werden je Anhangangabe nur einmal gezählt.

### Stufe 3: Substanztiefe und Brauchbarkeit (GPT-5.2)

- **Substanztiefe:** Die Klasse (substanziell, pauschal, leer) vergibt das Modell im Extraktionsaufruf der Stufe 2 (siehe oben).
- **Brauchbarkeit** (`bewertung_stufe3.py`, Prompts `prompts/stufe3_brauchbarkeit_system.txt` und `prompts/stufe3_brauchbarkeit_user.txt`): Der Prompt setzt das Schema von Rebernig/Scheinecker um.
  - Entscheidungsregel 1 trennt „unbrauchbar“ von „eher unbrauchbar“.
  - Entscheidungsregel 2 prüft, ob eine der sechs Konstellationen (a) bis (f) vollständig erfüllt ist („eher brauchbar“).
  - Eingaben je Aufruf: der Anhangtext (höchstens 6.000 Zeichen), die in Stufe 2 extrahierten Begründungen und der Betrag des negativen Eigenkapitals.
- **Drei Durchläufe:** Jede Anhangangabe wird in drei vollständigen, unabhängigen Durchläufen bewertet. Die finale Klasse ist die Mehrheit der drei Durchläufe. Liefern alle drei Durchläufe ein anderes Ergebnis, gilt die niedrigste Stufe („unbrauchbar“).
- **Ausgabe je Anhangangabe:** die Klasse, die erfüllte Konstellation und ein Score von 0 bis 10 (Median der drei Durchläufe).

## Voraussetzungen

- Node.js 18 oder neuer, danach `npm install` (Schritt 1 und Stufe 1)
- Python 3.10 oder neuer, danach `pip install -r requirements.txt` (Stufen 2 und 3)
- für Stufe 1 [Ollama](https://ollama.com) mit einem Qwen-Modell: `ollama pull <modell>`
- für die Stufen 2 und 3 eine Azure-OpenAI-Ressource mit je einem Deployment für GPT-5.2 und für text-embedding-ada-002
- für Schritt 1 ein HVD-Zugang (siehe unten)

## Konfiguration

Die Datei `.env.example` wird nach `.env` kopiert und mit den eigenen Werten befüllt. Die Datei `.env` wird nicht versioniert.

| Variable | Verwendung |
|---|---|
| `HVD_API_KEY` | Token für die Firmenbuch-Schnittstelle (Schritt 1) |
| `AZURE_OPENAI_API_KEY` | Schlüssel der Azure-OpenAI-Ressource (Stufen 2 und 3) |
| `AZURE_OPENAI_ENDPOINT` | Endpunkt der Azure-OpenAI-Ressource |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Name des GPT-5.2-Deployments |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Name des Embedding-Deployments (text-embedding-ada-002) |
| `OLLAMA_HOST` | Adresse des Ollama-Servers (Stufe 1) |

## HVD-Zugang beantragen

Der Zugang zum HVD des Firmenbuchs erfolgt über JustizOnline:

1. Bei JustizOnline anmelden. Die Authentifizierung erfolgt mit ID Austria oder mit der eID eines anderen EU-Mitgliedstaats.
2. Dort einen Antrag auf Informationsweiterverwendung stellen.
3. Nach der Genehmigung steht der Token im Profil unter „Informationsweiterverwendung“: <https://justizonline.gv.at/jop/web/iwg>.
4. Den Token in `.env` als `HVD_API_KEY` eintragen. Das Skript übergibt ihn im HTTP-Header `X-API-KEY` an die SOAP-Schnittstelle `https://justizonline.gv.at/jop/api/at.gv.justiz.fbw/ws`.

## Ausführen

Alle Befehle werden im Hauptverzeichnis des Repositorys ausgeführt. Eingaben liegen in `data/`, Ausgaben in `output/`. Beide Verzeichnisse werden nicht versioniert.

```bash
# Schritt 1: Abruf und Extraktion
node 01_hvd_abruf/hvd_abruf.js --kurzinfo data/kurzinformation --ausgabe output/hvd_abruf.ndjson

# Stufe 1: deduktive Klassifikation
node 02_klassifikation_stufe1_qwen/klassifikation_stufe1.js --modell <ollama-modell> \
     --eingabe data/anhangangaben.xlsx --prompts prompts/stufe1_qwen \
     --ausgabe output/stufe1_klassifikation.xlsx
# Folgelauf stille Reserven
node 02_klassifikation_stufe1_qwen/klassifikation_stufe1.js --modell <ollama-modell> \
     --eingabe data/anhangangaben.xlsx --prompts prompts/stufe1_qwen_folgelauf_stille_reserven \
     --ausgabe output/stufe1_stille_reserven.xlsx

# Stufe 2: Extraktion und Deduplizierung
python 03_extraktion_stufe2_gpt/extraktion_stufe2.py --eingabe data/anhangangaben.xlsx --ausgabe output/stufe2_extraktion.pkl
python 03_extraktion_stufe2_gpt/deduplizierung.py --eingabe output/stufe2_extraktion.pkl --ausgabe output/stufe2_dedup.pkl

# Stufe 3: Brauchbarkeit (drei Durchläufe, Mehrheitsentscheid)
python 04_bewertung_stufe3/bewertung_stufe3.py --eingabe output/stufe2_extraktion.pkl --ausgabe-dir output
```

Weitere Optionen:

- `hvd_abruf.js --pause-ms`: Pause vor jeder Anfrage, Voreinstellung 3.000 ms.
- `klassifikation_stufe1.js --temperatur`, `--system` und `--limit`.
- `--workers` in den Stufen 2 und 3: Zahl paralleler Anfragen, Voreinstellung 60.

Die Stufen 2 und 3 speichern regelmäßig Zwischenstände. Nach einem Abbruch setzen sie beim nächsten Aufruf fort.

## Lizenz

MIT-Lizenz, siehe [LICENSE](LICENSE).
