# Quickstart — mit Zeitangaben

Von null zur fertigen Recherche. **Alle Zeiten sind gemessen**, nicht geschätzt;
wo eine Zahl nicht gemessen werden konnte, steht das ausdrücklich dabei.

Messumgebung: Linux-Container, Python 3.11, 4 Kanäle. Die Netzwerkzeiten des
Crawls hängen vom Nachrichtenaufkommen ab — dafür steht unten eine Formel.

## Überblick

| # | Schritt | Dauer | Einmalig? |
| --- | --- | --- | --- |
| 0 | Abhängigkeiten installieren | **~18 s** | ja |
| 1 | Token hinterlegen | ~1 min (manuell) | ja |
| 2 | Crawl der vier Kanäle | **~20 s bis ~5 min** | pro Lauf |
| 3 | Merge + Ressourcen-CSV | **0,3 s** | pro Lauf |
| 4 | Diskutierte Gleichungen extrahieren | **0,5 s** | pro Lauf |
| 5 | Ingest-Bundle + Bilder vorbereiten | lokal, abhängig von Anhängen | pro Lauf |
| 6 | Drei-Agenten-Kuration + 2-von-3-Konsens | abhängig von den Agenten | pro Bundle |
| 7 | Open-Weight-Research-Batch | abhängig von Queue und Modell | pro Batch |

Die früher gemessenen Legacy-Schritte 3–5 brauchten bei 25 000 Nachrichten
**1,65 s**. Für den neuen Vertrags-, Bild- und Konsenspfad liegen noch keine
belastbaren End-to-End-Messwerte vor; Bilddownloads und drei externe Agenten
hängen von Datenmenge, Netzwerk und Anmeldung ab.

---

## Schritt 0 — Installation · gemessen: 17,4 s

```bash
python3 -m venv .venv && source .venv/bin/activate   # 3,9 s
pip install -r requirements.txt                      # 13,5 s
```

Für die numerische Verifikation zusätzlich (**7,7 s**):

```bash
pip install mpmath sympy
```

## Schritt 1 — Token · ~1 min (manuell)

```bash
export DISCORD_TOKEN_Backupper123="<dein Discord-Token>"
```

In Colab stattdessen als Secret unter demselben Namen; in GitHub Actions als
Repository-Secret. Details: [README](README.md#token-hinterlegen-colab-secret).

## Schritt 2 — Crawl · 20 s bis 5 min

```bash
python discord_math_crawl.py            # letzte 3 Tage (Standard)
python discord_math_crawl.py --days 30  # größeres Fenster
```

Die Laufzeit ist **bewusst** durch die API-Ruhezeiten bestimmt (1–2 s Pause nach
jeder Anfrage, 0,5 s zwischen Kanälen). Gemessenes Modell:

```
Dauer ≈ 1,5 s × (2 + Σ ⌈Nachrichten_Kanal / 100⌉) + 0,5 s × Anzahl_Kanäle
```

Gemessene Stützpunkte:

| Datenmenge | Requests | Dauer (gemessen) |
| --- | --- | --- |
| 4 Kanäle × 50 Nachrichten | 6 | **10,6 s** |
| 4 Kanäle × 250 Nachrichten | 14 | **20,6 s** |
| 4 × 1 000 (hochgerechnet) | 42 | ~65 s |
| 4 × 5 000 (hochgerechnet, `--days 30`) | 202 | ~5 min |

> Bei `--full` auf einem lebhaften Kanal können daraus schnell 20 min und mehr
> werden. Das ist kein Fehler, sondern die eingebaute API-Schonung — nicht
> herunterdrehen.

## Schritte 3–5 — Aufbereitung und Übergabevertrag

```bash
python extract_resources.py                       # Merge + Links
python extract_discussions.py --guild-id <id>     # diskutierte Gleichungen
python materialize_media.py                        # lokale Bilder + Manifest
```

`extract_discussions.py` schreibt neben den Legacy-Dateien jetzt
`discord_exports/ingest_bundle.json`. Es enthält atomare, pseudonymisierte
Nachrichten und deterministische Themenblöcke; die private Projektion ermöglicht
lokale Rückverfolgung und darf nicht veröffentlicht werden.

Die historischen Messwerte des weiterhin verfügbaren Legacy-Generators:

| Nachrichten | `extract_resources` | `extract_discussions` | `make_research_projects` | Summe |
| --- | --- | --- | --- | --- |
| 5 000 | 0,06 s | 0,08 s | 0,23 s (281 Projekte) | **0,37 s** |
| 25 000 | 0,32 s | 0,47 s | 0,86 s (1 500 Projekte) | **1,65 s** |

### Wichtig: erst kuratieren, dann expandieren

25 000 Nachrichten ergaben im Legacy-Test **1 500 Projekte** (26 MB). Genau
diese Explosion verhindert der neue Standardpfad: Claude Code, OpenAI Codex und
Google Antigravity interpretieren unabhängig nur die Themenblöcke; kritische
Felder werden erst mit 2-von-3-Konsens übernommen.

```bash
cd /pfad/zu/The-Agentic-Researcher
python -m agentic_researcher curate \
  /pfad/zum/Discord-Repo/discord_exports/ingest_bundle.json \
  --provider claude --provider codex --provider antigravity \
  --media-root /pfad/zum/Discord-Repo/discord_exports/curation_media \
  --output curated_topics.json
python -m agentic_researcher expand curated_topics.json \
  --output research_queue.json
```

Bei Formel- oder Themenkonflikten lautet der Status `needs_review`; solche
Blöcke werden standardmäßig nicht automatisch expandiert.

## Schritt 6 — günstiger High-Volume-Research-Batch

```bash
python -m agentic_researcher run-batch research_queue.json \
  --work-root research_runs \
  --state batch-state.json \
  --provider opencode
```

Der Batch ist idempotent und resumierbar. OpenCode zeigt dabei auf einen lokalen
OpenAI-kompatiblen Modellserver. Für Colab/A100 liegt im Agentic-Researcher-Repo
`notebooks/open_weight_bulk_research.ipynb`: unter etwa 75 GiB GPU-Speicher
startet es `gpt-oss-20b`, darüber `gpt-oss-120b`.

### Container-Build (einmalig) — hier nicht messbar

`agentic-researcher --build` baut das Sandbox-Image (Basis `ubuntu:22.04`).
**Diese Zahl konnte in dieser Umgebung nicht gemessen werden**, weil der
Image-Pull von der Egress-Policy blockiert wird
(`production.cloudfront.docker.com` → HTTP 403). Erfahrungswert für einen
vergleichbaren Ubuntu-Build mit Python-Toolchain: **5–15 min**, stark abhängig
von Netzwerk und Cache. Bitte als grobe Hausnummer behandeln, nicht als Messung.

## Schritt 7 — Verifikation nachrechnen · gemessen: 1,63 s

```bash
python examples/demo-fresnel-integral/scripts/verify.py
```

Erwartete Ausgabe: Abweichung `9.1215e-18`, `VERIFICATION: PASS`.

---

## Der schnellste sinnvolle Durchlauf

```bash
python3 -m venv .venv && source .venv/bin/activate   #  3,9 s
pip install -r requirements.txt mpmath               # ~15   s
export DISCORD_TOKEN_Backupper123="..."              #  manuell
python discord_math_crawl.py                         # ~20   s
python extract_resources.py                          #   0,1 s
python extract_discussions.py                        #   0,1 s
python materialize_media.py                          # abhängig von Anhängen
```

Danach das private Artefakt mit den drei kommerziellen Systemen kuratieren,
expandieren und die resultierende Queue lokal oder in Colab ausführen.

## Automatisch statt manuell

Der Workflow [`daily-crawl.yml`](.github/workflows/daily-crawl.yml) erledigt
Crawl, Bundle- und Bildaufbereitung täglich um 03:17 UTC. Er prüft den Vertrag
gegen einen Checkout von Erikiss/The-Agentic-Researcher und speichert dessen
Commit im Export. Weil das Repository öffentlich ist, wird dieser Export vor
dem Upload mit `age` verschlüsselt; normale Actions-Artefakte sind hier nicht
privat.

Einmalig lokal ein Schlüsselpaar erzeugen und nur den ausgegebenen öffentlichen
Empfänger (`age1...`) als Repository-Variable
`DISCORD_EXPORT_AGE_RECIPIENT` hinterlegen:

```bash
age-keygen --output /sicherer/pfad/discord-export-key.txt
```

Der private Schlüssel bleibt außerhalb des Repositories. Ohne die Variable
lädt der Workflow absichtlich keinen Klartext-Export hoch. Nach dem Download:

```bash
age --decrypt --identity /sicherer/pfad/discord-export-key.txt \
  --output discord-math-export.tar.gz \
  discord-math-export.tar.gz.age
tar -xzf discord-math-export.tar.gz
```

Die drei kommerziellen Agenten laufen anschließend lokal mit den Zugangsdaten
des Besitzers.
