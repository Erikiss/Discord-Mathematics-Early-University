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
| 5 | Research-Projekte erzeugen | **0,9 s** | pro Lauf |
| 6 | **Research-Agent pro Problem** | **~5–9 min** | pro Problem |
| 7 | Verifikation nachrechnen | **1,6 s** | pro Problem |

Die Schritte 3–5 zusammen brauchen bei 25 000 Nachrichten **1,65 s** — die
Aufbereitung ist also vernachlässigbar. Zeit kostet nur der Crawl (durch die
Discord-Ruhezeiten) und der Research-Agent.

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

## Schritte 3–5 — Aufbereitung · gemessen: 1,65 s bei 25 000 Nachrichten

```bash
python extract_resources.py                       # Merge + Links
python extract_discussions.py --guild-id <id>     # diskutierte Gleichungen
python make_research_projects.py \
    --instructions-template /pfad/zu/The-Agentic-Researcher/INSTRUCTIONS.md
```

| Nachrichten | `extract_resources` | `extract_discussions` | `make_research_projects` | Summe |
| --- | --- | --- | --- | --- |
| 5 000 | 0,06 s | 0,08 s | 0,23 s (281 Projekte) | **0,37 s** |
| 25 000 | 0,32 s | 0,47 s | 0,86 s (1 500 Projekte) | **1,65 s** |

### ⚠️ Wichtig: die Projektzahl explodiert

25 000 Nachrichten ergaben im Test **1 500 Projekte** (26 MB). Das Erzeugen
dauert zwar nur eine Sekunde — sie *durchrechnen* zu lassen wären bei ~5 min pro
Problem rund **125 Agent-Stunden**. Deshalb vorher filtern:

```bash
# nur die 10 gehaltvollsten Probleme
python make_research_projects.py --limit 10

# oder strenger schwellen (Standard: 8)
python extract_discussions.py --min-score 20
```

Erst die Liste ansehen (`math_discussions.csv`, nach Score sortiert), dann
entscheiden, was tatsächlich recherchiert wird.

## Schritt 6 — Research-Agent · gemessen: 5–9 min pro Problem

```bash
agentic-researcher --yolo research_projects/001-calculus-.../
```

Gemessen an realen Agent-Läufen dieser Session:

| Agent-Typ | Median | Maximum |
| --- | --- | --- |
| Mathematik-Recherche (Herleitung + numerische Verifikation) | **5,4 min** | 8,5 min |
| Code-Review / kürzere Analyse | 1,8 min | 3,6 min |

Ein Bündel von 17 parallel laufenden Agenten brauchte **16 min Wanduhr** bei
30,6 Agent-Minuten Rechenzeit — parallelisieren lohnt sich also deutlich.

**Faustregel:** rechne **~6 min pro Problem** und teile durch deine Parallelität.
10 Probleme sequenziell ≈ 1 Stunde; 10 Probleme parallel ≈ 10–15 min.

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
python make_research_projects.py --limit 5           #   0,1 s
```

**Bis hierher: unter einer Minute** (plus Token-Eingabe). Danach entscheidet nur
noch, wie viele Probleme du recherchieren lässt — **~6 min pro Stück**.

## Automatisch statt manuell

Der Workflow [`daily-crawl.yml`](.github/workflows/daily-crawl.yml) erledigt die
Schritte 2–5 täglich um 03:17 UTC von selbst; das Ergebnis liegt als Artefakt
bereit. Typische Gesamtlaufzeit des Jobs: **Checkout + Setup ~30 s, Crawl je
nach Aufkommen, Aufbereitung ~2 s** — das Job-Timeout steht auf 30 min.
