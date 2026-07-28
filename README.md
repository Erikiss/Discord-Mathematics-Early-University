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

# Größeres Zeitfenster (z.B. letzte 30 Tage)
python discord_math_crawl.py --days 30

# Komplette Historie statt Zeitfenster
python discord_math_crawl.py --full

# Anderes Limit / anderer Ausgabeordner
python discord_math_crawl.py --days 7 --max 2000 --out my_exports
```

Die Nachrichten landen als eine JSON-Datei pro Kanal unter
`discord_exports/Mathematics/<kanal>.json`.

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
(`extract_resources.py`) aus und legt das Ergebnis als **privates
Workflow-Artefakt** ab (es wird bewusst **nicht** ins Repo committet, da es
fremde Discord-Nachrichten enthält). Das Artefakt enthält:

- `Mathematics/<kanal>.json` – die Rohnachrichten pro Kanal,
- `MATH_MERGED.json` – alle Kanäle zusammengeführt (mit Herkunfts-Tags),
- `math_resources.csv` – die extrahierten Mathematik-/Paper-Links.

**Einrichtung (einmalig):**

1. Token als Repository-Secret hinterlegen: Repo → **Settings** → **Secrets and
   variables** → **Actions** → **New repository secret**
   - Name: `DISCORD_TOKEN_Backupper123`
   - Wert: `<dein Discord-Token>`
2. Den Branch mit dem Workflow nach `main` mergen. **Wichtig:** Der Zeitplan
   (`schedule`) feuert nur auf dem Standard-Branch – erst nach dem Merge läuft
   der Cron automatisch.

**Zeitplan & manueller Start:**

- Läuft täglich um **03:17 UTC** (`cron: "17 3 * * *"`).
- Manuell startbar über den Reiter **Actions** → *Daily Math Crawl* → **Run
  workflow**; dort lassen sich `days`, `full` und `max_per_channel` pro Lauf
  setzen.

**Ergebnis abholen:** Im jeweiligen Actions-Lauf unter **Artifacts** die Datei
`discord-math-export-<datum>` herunterladen (Aufbewahrung: 90 Tage).

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
