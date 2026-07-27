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

# Standard: letzte 30 Tage, nur die vier Ziel-Kanäle
python discord_math_crawl.py

# Komplette Historie statt Zeitfenster
python discord_math_crawl.py --full

# Anderes Zeitfenster / anderes Limit / anderer Ausgabeordner
python discord_math_crawl.py --days 7 --max 2000 --out my_exports
```

Die Nachrichten landen als eine JSON-Datei pro Kanal unter
`discord_exports/Mathematics/<kanal>.json`.

### Optionen

| Option           | Bedeutung                                             | Default            |
| ---------------- | ----------------------------------------------------- | ------------------ |
| `--days N`       | Nachrichten der letzten N Tage                        | `30`               |
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
5. **Rate Limits** – nach jeder Anfrage eine zufällige Pause (1–2 s); bei
   HTTP 429 wird `retry_after` respektiert; 403 markiert nicht lesbare Kanäle.

## Hinweis zu den Discord-Nutzungsbedingungen

Wie das ML-Original nutzt dieses Projekt einen **User-Account-Token**
("Self-Bot"). Das Automatisieren eines User-Accounts verstößt gegen die
[Discord Terms of Service](https://discord.com/terms). Verwende die Skripte nur
mit deinem **eigenen** Account, ausschließlich für Server, denen du beigetreten
bist, ausschließlich zum persönlichen Lesen/Archivieren – auf eigenes Risiko.
Lass die eingebauten Ratelimits aktiv, um die API zu schonen.
