# Discord-Mathematics-Early-University

Crawlt den **Mathematics**-Discord-Server und beschränkt sich bewusst auf **vier
Kanäle**, die den Anfangssemestern eines Mathematikstudiums entsprechen:

| Kanal                | Themengebiet                                   |
| -------------------- | ---------------------------------------------- |
| `calculus`           | Analysis I (Differential-/Integralrechnung)    |
| `linear-algebra`     | Lineare Algebra I                              |
| `proofs-and-logic`   | Beweise & Logik                                |
| `computing-software` | Mathematische Software & Programmierung        |

Der Ablauf ist identisch zum bestehenden **Machine-Learning-Crawl**
(`Discord_Crawl`): Login über den in den **Colab-Secrets** hinterlegten Token,
ein REST-Client mit **Rate-Limit-Handling** und zufälligen Pausen zwischen den
Anfragen, dann `Server → Kanäle → Nachrichten` als JSON. Getauscht wurden nur
Server und Kanäle – und die Kanalliste ist auf die vier oben beschränkt, weil
der Mathematics-Server sehr viele Kanäle hat.

> **Schnelleinstieg mit Zeitangaben:** [QUICKSTART.md](QUICKSTART.md) — was wie
> lange dauert, vom Setup bis zum Research-Agent (alle Zeiten gemessen).

## Zwei Varianten

- **`notebooks/discord_math_crawl.ipynb`** – Google-Colab-Notebook, das den
  ML-Ablauf 1:1 nachbildet: Crawl → Merge nach Google Drive → optionale
  Ressourcen-/Paper-Extraktion als CSV. Direkt in Colab öffnen und ausführen.
- **`discord_math_crawl.py`** – dasselbe Crawl-Kernstück als eigenständiges
  Skript für die Kommandozeile (läuft auch lokal, ohne Colab).

## Token hinterlegen (Colab-Secret)

Der Token wird – wie im ML-Crawl – aus den Colab-Secrets gelesen. Lege ihn unter
dem Schlüssel-Symbol (links in Colab) an:

```
Name:  DISCORD_TOKEN_Backupper123
Wert:  <dein Discord-Token>
```

Alternativ (lokal) als Umgebungsvariable:

```bash
export DISCORD_TOKEN_Backupper123="<dein Discord-Token>"
```

Der Schlüssel `DISCORD_TOKEN` wird ebenfalls akzeptiert.

## Nutzung: Kommandozeile

```bash
pip install -r requirements.txt

# Standard: letzte 3 Tage (wie das ML-Original), nur die vier Ziel-Kanäle
python discord_math_crawl.py

# Server eindeutig per ID auswählen (ersetzt den Standardnamen Mathematics)
python discord_math_crawl.py --server-id 123456789012345678

# Größeres Zeitfenster (z.B. letzte 30 Tage)
python discord_math_crawl.py --days 30

# Komplette Historie statt Zeitfenster
python discord_math_crawl.py --full

# Anderes Limit / anderer Ausgabeordner
python discord_math_crawl.py --days 7 --max 2000 --out my_exports
```

Die Nachrichten landen als eine JSON-Datei pro Kanal unter
`discord_exports/Mathematics/<kanal>.json`.

### Discord aufbereiten und an The Agentic Researcher übergeben

Dieses Repository ist der **Discord-Adapter**. Die oberste Orchestrierung,
Konsensbildung und eigentliche Recherche liegen im Fork
[Erikiss/The-Agentic-Researcher](https://github.com/Erikiss/The-Agentic-Researcher).
So bleibt die teure, fachlich anspruchsvolle Interpretation klein, während die
vielen nachgelagerten Rechercheaufträge mit einem lokalen Open-Weight-Modell
laufen können.

```text
vier Discord-Kanäle
  → ingest_bundle.json + lokale Bilder
  → Claude Code │ OpenAI Codex │ Google Antigravity
  → 2-von-3-Konsens
  → synthetische Research-Queue
  → Agentic Researcher + lokales Open-Weight-Modell
```

Zuerst werden Gleichungen, Gesprächskontext und Anhänge neutral aufbereitet:

```bash
python extract_discussions.py --guild-id <server-id>
# -> discord_exports/math_discussions.json + .csv
# -> discord_exports/ingest_bundle.json

# Optional, aber für visuelle Beiträge empfohlen: nur allow-gelistete
# Discord-Bild-URLs, mit Größen- und Typgrenzen, lokal materialisieren.
python materialize_media.py
# -> discord_exports/curation_media/
```

`ingest_bundle.json` enthält atomare Nachrichten und deterministische
Themenblöcke mit stabilen IDs, LaTeX, Reply-/Zeitkontext und TeXit-Zuordnung.
Die `public`-Projektion pseudonymisiert Teilnehmer und entfernt Discord-Links;
die `private`-Projektion hält die Rückverfolgbarkeit für den Besitzer. Das
gesamte Bundle und `curation_media/` bleiben deshalb **private**. Verdächtige
Prompt-Texte werden nicht entfernt, sondern ausdrücklich als nicht vertrauenswürdige
Daten markiert.

Danach wird im Agentic-Researcher-Checkout kuratiert und expandiert:

```bash
cd /pfad/zu/The-Agentic-Researcher

python -m agentic_researcher curate \
  /pfad/zu/Discord-Mathematics-Early-University/discord_exports/ingest_bundle.json \
  --provider claude \
  --provider codex \
  --provider antigravity \
  --media-root /pfad/zu/Discord-Mathematics-Early-University/discord_exports/curation_media \
  --output curated_topics.json

python -m agentic_researcher expand curated_topics.json \
  --output research_queue.json
```

Die drei kommerziellen Systeme arbeiten unabhängig. Erst Übereinstimmung von
mindestens zwei Systemen übernimmt kritische Felder wie Themenzuordnung und
Formeln; Konflikte landen als `needs_review`. Der anschließende resumierbare
Batch benutzt standardmäßig OpenCode als Adapter für ein lokales Modell. Das
Agentic-Researcher-Repo enthält dafür auch ein Colab/A100-Notebook mit
`gpt-oss-20b` beziehungsweise, bei genügend GPU-Speicher, `gpt-oss-120b`.
Alle drei gültigen Antworten sind standardmäßig Pflicht; ein degradierter
Zwei-System-Lauf muss ausdrücklich aktiviert und manuell geprüft werden.

Die Recherche kann Bücher, Code,
[die arXiv-Mathematik-Karte](https://lmcinnes.github.io/datamapplot_examples/arXiv_math/),
OpenAlex, Erdős Problems, OEIS und das Journal of Integer Sequences einbeziehen.
Die Karte basiert auf `nomic-embed`/Sentence Transformers und t-SNE; Model2Vec
war nur ein späterer Diskussionsvorschlag. Ihre 2D-Distanz und scheinbare Dichte
dienen nur zur Entdeckung, nicht als Rankingmetrik. Offene Erdős-Probleme werden
ausschließlich als Ausblick behandelt; gelöste, bewiesene oder widerlegte
Probleme können als Lernbeispiele vorgeschlagen werden.

`make_research_projects.py` bleibt als **Legacy-Direktweg** verfügbar. Der
Standardworkflow nutzt ihn nicht mehr, weil er ohne unabhängige Kuration aus
jeder Heuristik direkt ein Projekt erzeugt.

**Vollständiges Beispiel:** [`examples/demo-fresnel-integral/`](examples/demo-fresnel-integral/)
zeigt eine komplett durchgeführte Recherche zur Gleichung aus `#calculus` —
inklusive Herleitung, Forschungsprotokoll (`report.tex`) und laufender
numerischer Verifikation. Ergebnis:
$\int_{0}^{\pi/2}\sin(\cot^{2}x)\sec^{2}x\,dx=\sqrt{\pi/2}$.

### Ressourcen-/Paper-CSV erzeugen (optional, lokal)

`extract_resources.py` führt alle Kanal-JSONs zusammen und extrahiert
Mathematik-/Paper-Links (arXiv, MathOverflow, projecteuclid, … plus
Social-Links mit Signalwörtern) – genau wie die Merge-/Extraktions-Zellen im
Notebook, aber ohne Colab/Drive und ohne pandas:

```bash
python extract_resources.py            # liest discord_exports/, schreibt
                                       # discord_exports/MATH_MERGED.json
                                       # und discord_exports/math_resources.csv
```

### Optionen

| Option           | Bedeutung                                             | Default            |
| ---------------- | ----------------------------------------------------- | ------------------ |
| `--days N`       | Nachrichten der letzten N Tage                        | `3`                |
| `--full`         | Komplette Historie (ignoriert `--days`)               | aus                |
| `--max N`        | Höchstzahl Nachrichten pro Kanal                      | `5000`             |
| `--out DIR`      | Basis-Ausgabeordner                                   | `discord_exports`  |
| `--server NAME`  | Ziel-Server überschreiben (mehrfach nutzbar)          | `Mathematics`      |
| `--channel NAME` | Ziel-Kanäle überschreiben (mehrfach nutzbar)          | die vier oben      |

## Wie funktioniert es?

1. **Auth** – Token aus Colab-Secret bzw. Umgebungsvariable.
2. **Server finden** – `GET /users/@me/guilds` liefert die beigetretenen Server.
   Gesucht wird nach Name `Mathematics`; bei mehreren Treffern gewinnt der
   mitgliederstärkste Server. Optional lässt sich die Guild-ID fest verdrahten.
3. **Kanäle filtern** – aus allen Kanälen werden nur die vier Ziel-Kanäle
   (Textkanäle) behalten.
4. **Nachrichten sammeln** – pro Kanal wird paginiert (`before`-Cursor, neueste
   zuerst) bis zum Zeitfenster-Ende oder zum `--max`-Limit.
5. **Rate Limits & Fehler** – nach jeder Anfrage eine zufällige Pause (1–2 s);
   bei HTTP 429 wird `retry_after` (JSON-Body oder `Retry-After`-Header,
   gedeckelt) respektiert; `403` markiert nicht lesbare Kanäle; `401` bricht mit
   klarer Meldung ab. Netzwerkfehler und `5xx` werden mit begrenztem
   exponentiellem Backoff wiederholt.
6. **Unvollständige Exporte** – bricht ein Kanal-Download nach ausgeschöpften
   Retries wegen eines API-Fehlers ab, wird die Datei als
   `<kanal>.INCOMPLETE.json` gespeichert und in der Zusammenfassung als
   „unvollständig" gewarnt – Teil-Daten werden nie stillschweigend als
   vollständig gemeldet.

## Automatisch täglich per GitHub Actions

Der Workflow [`.github/workflows/daily-crawl.yml`](.github/workflows/daily-crawl.yml)
führt jeden Tag automatisch den Crawler **und** die Aufbereitung
(`extract_resources.py`) aus und legt das Ergebnis als **clientseitig
verschlüsseltes Workflow-Artefakt** ab (es wird bewusst **nicht** ins Repo
committet, da es fremde Discord-Nachrichten enthält). Der verschlüsselte Export
enthält:

- `Mathematics/<kanal>.json` – die Rohnachrichten pro Kanal,
- `MATH_MERGED.json` – alle Kanäle zusammengeführt (mit Herkunfts-Tags),
- `math_discussions.json` / `.csv` – die **diskutierten Gleichungen/Probleme**,
- `ingest_bundle.json` – validierter, versionsgebundener Übergabevertrag,
- `curation_media/` – lokal materialisierte Bilder plus URL-freies Manifest,
- `AGENTIC_RESEARCHER_COMMIT.txt` – getesteter Framework-Commit,
- `math_resources.csv` – zusätzlich gefundene Links (in diesen Kanälen selten).

Der Job checkt The Agentic Researcher nur zur Vertragsprüfung aus. Claude Code,
Codex und Antigravity werden **nicht** im GitHub-Runner aufgerufen. Da dieses
Repository öffentlich ist, sind normale Actions-Artefakte nicht privat: Der
Workflow lädt deshalb ausschließlich ein mit
[age](https://age-encryption.org/) clientseitig verschlüsseltes
`discord-math-export.tar.gz.age` hoch. Hinterlege zuvor den öffentlichen
Empfänger (`age1...`) als Repository-Variable
`DISCORD_EXPORT_AGE_RECIPIENT`; der private Schlüssel bleibt ausschließlich
lokal. Ohne die Variable schlägt der Upload geschlossen fehl und es werden
keine Klartextdaten veröffentlicht.

Nach dem Download:

```bash
age --decrypt --identity /sicherer/pfad/discord-export-key.txt \
  --output discord-math-export.tar.gz \
  discord-math-export.tar.gz.age
tar -xzf discord-math-export.tar.gz
```

Danach wird die Kuration lokal mit den eigenen Anmeldedaten gestartet. Mit der
optionalen Repository-Variable `AGENTIC_RESEARCHER_REF` lässt sich die
Vertragsprüfung auf einen Tag oder Commit pinnen.

Empfohlen: Setze das Repository-Secret `DISCORD_GUILD_ID` (Settings → Secrets
and variables → Actions → *Secrets*). Der Workflow wählt damit ausschließlich
diesen Server statt der Namenssuche nach `Mathematics`; außerdem enthält die
private Projektion dadurch klickbare Discord-Links. Ohne das Secret bleibt die
Namenssuche als Fallback aktiv.

**Einrichtung (einmalig):**

1. Token als Repository-Secret hinterlegen: Repo → **Settings** → **Secrets and
   variables** → **Actions** → **New repository secret**
   - Name: `DISCORD_TOKEN_Backupper123`
   - Wert: `<dein Discord-Token>`
2. Mit `age-keygen --output /sicherer/pfad/discord-export-key.txt` lokal ein
   Schlüsselpaar erzeugen. Den privaten Schlüssel niemals hochladen.
3. Den von `age-keygen` ausgegebenen öffentlichen Empfänger (`age1...`) unter
   **Actions → Variables** als `DISCORD_EXPORT_AGE_RECIPIENT` hinterlegen.
4. Den Branch mit dem Workflow nach `main` mergen. **Wichtig:** Der Zeitplan
   (`schedule`) feuert nur auf dem Standard-Branch – erst nach dem Merge läuft
   der Cron automatisch.

**Zeitplan & manueller Start:**

- Läuft täglich um **03:17 UTC** (`cron: "17 3 * * *"`).
- Manuell startbar über den Reiter **Actions** → *Daily Math Crawl* → **Run
  workflow**; dort lassen sich `days`, `full` und `max_per_channel` pro Lauf
  setzen.

**Ergebnis abholen:** Im jeweiligen Actions-Lauf unter **Artifacts** die Datei
`discord-math-export-<datum>` herunterladen (Aufbewahrung: 30 Tage).

**Gut zu wissen:**

- GitHub deaktiviert geplante Workflows nach **60 Tagen** ohne Repository-
  Aktivität – dann im Actions-Tab einmal reaktivieren.
- Der Token wird nur als Secret in die Umgebungsvariable geladen und **nie
  ausgegeben**. Fehlt das Secret, bricht der Lauf mit klarer Meldung ab.
- Ein täglicher Lauf mit `--days 3` überschneidet sich bewusst leicht, damit
  keine Lücken entstehen. Jeder Lauf erzeugt einen eigenständigen Snapshot.

## Hinweis zu den Discord-Nutzungsbedingungen

Wie das ML-Original nutzt dieses Projekt einen **User-Account-Token**
("Self-Bot"). Das Automatisieren eines User-Accounts verstößt gegen die
[Discord Terms of Service](https://discord.com/terms). Verwende die Skripte nur
mit deinem **eigenen** Account, ausschließlich für Server, denen du beigetreten
bist, ausschließlich zum persönlichen Lesen/Archivieren – auf eigenes Risiko.
Lass die eingebauten Ratelimits aktiv, um die API zu schonen.
