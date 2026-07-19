# PewPewAdPod

Lokale, vollständig in Python entwickelte Webapplikation: hinterlegte Podcast-RSS-Feeds werden periodisch
auf neue Episoden geprüft, das Audio wird transkribiert und auf Werbeblöcke hin analysiert, erkannte Blöcke
werden (automatisch oder nach manueller Bestätigung) herausgeschnitten, und jeder Feed wird als eigener,
bereinigter RSS-Feed zum Abonnieren im Podcast-Player bereitgestellt — inklusive Original-Cover-Art und
Episodenbeschreibung.

## Setup (Windows)

1. **Python 3.11** installieren (empfohlen, da `faster-whisper`/`ctranslate2` für sehr neue Python-Versionen
   ggf. noch keine Wheels bereitstellen).
2. **ffmpeg** installieren (wird von `pydub` und `faster-whisper` zum Dekodieren/Schneiden benötigt):
   ```powershell
   winget install Gyan.FFmpeg
   ```
   Neues Terminal öffnen und prüfen: `ffmpeg -version`.
3. Virtuelle Umgebung anlegen und Abhängigkeiten installieren:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
4. Anwendung starten:
   ```powershell
   python run.py
   ```
   Beim ersten Start wird das Whisper-Modell (`small`, ca. 500 MB) automatisch heruntergeladen — dafür ist
   einmalig eine Internetverbindung nötig.
5. Im Browser öffnen: [http://localhost:8000](http://localhost:8000)
6. Windows-Firewall-Hinweis beim ersten Start akzeptieren (privates Netzwerk), damit Podcast-Player im
   selben Netzwerk zugreifen können.
7. Eigene LAN-IP ermitteln (für das Abonnieren von einem anderen Gerät): `ipconfig` → IPv4-Adresse notieren
   → im Podcast-Player `http://<diese-ip>:8000/feed/<feed-id>.xml` abonnieren.

## Setup (Docker)

Alternative zur lokalen Installation — Python, ffmpeg und alle Abhängigkeiten stecken im Image,
nichts muss auf dem Host installiert werden.

```powershell
docker compose up --build -d
```

- Danach unter [http://localhost:8000](http://localhost:8000) erreichbar.
- `./data` (Datenbank, heruntergeladene/geschnittene Audiodateien, Transkripte, Cover-Art, Whisper-
  Modell-Cache) und `./app/config/ad_jingles` (deine Jingle-Bibliothek) werden als Verzeichnisse neben
  `docker-compose.yml` gemountet — bleiben also bei `docker compose down`/Image-Neubau erhalten und sind
  identisch zum Layout des lokalen (Nicht-Docker-)Betriebs, damit sie sich einfach einsehen/sichern lassen.
- Beim ersten Start lädt der Container das Whisper-Modell herunter (Internetverbindung nötig), danach
  liegt es im gemounteten `./data`-Ordner und wird nicht erneut heruntergeladen.
- Logs ansehen: `docker compose logs -f`. Stoppen: `docker compose down` (Daten bleiben erhalten, da sie
  außerhalb des Containers liegen).
- Ohne Compose direkt mit `docker`:
  ```powershell
  docker build -t pewpewadpod .
  docker run -d -p 8000:8000 -v ${PWD}/data:/app/data -v ${PWD}/app/config/ad_jingles:/app/app/config/ad_jingles pewpewadpod
  ```
- Für den Zugriff von anderen Geräten im Netzwerk (Podcast-Player) den Port wie gewohnt über die
  Firewall freigeben; die im Container laufende Anwendung bindet bereits an `0.0.0.0:8000`.
- Läuft die Anwendung stattdessen auf einem Server/NAS, `http://<server-ip>:8000/feed/<feed-id>.xml`
  im Podcast-Player abonnieren.

**Hinweis:** Das Dockerfile wurde in dieser Umgebung nicht gegen einen echten `docker build` getestet
(kein Docker verfügbar) — die Konfiguration (Basis-Image, ffmpeg-Installation, Volumes, Umgebungsvariablen)
wurde aber sorgfältig auf Konsistenz mit dem restlichen Projekt geprüft. Bitte beim ersten Start kurz
verifizieren.

## Nutzung

1. Auf der Startseite einen Podcast-RSS-Feed hinzufügen. Für den ersten Test empfiehlt sich der Modus
   **"Manuelle Review"** (Standard), damit erkannte Werbeblöcke vor dem Schnitt bestätigt werden können.
2. Ein Hintergrund-Scheduler prüft alle Feeds automatisch (Standard: alle 30 Minuten) und verarbeitet neue
   Episoden nacheinander (Standard: alle 60 Sekunden wird die Warteschlange geprüft). Über "Jetzt
   aktualisieren" kann ein Feed auch manuell sofort geprüft werden.
3. Der Verarbeitungsstatus jeder Episode ist auf der Feed-Detailseite sichtbar
   (`neu → download → transkription → analyse → review/schnitt → veröffentlicht`).
4. Bei **manueller Review**: auf der Review-Seite werden erkannte Kandidaten mit Zeitstempel, Konfidenz,
   erkannten Schlüsselwörtern und Transkript-Ausschnitt angezeigt. Segmente können bestätigt, abgelehnt,
   in Start/Ende angepasst oder manuell ergänzt werden. "Schneiden & veröffentlichen" erzeugt die bereinigte
   Audiodatei und veröffentlicht die Episode im eigenen Feed.
5. Bei **Auto-Cut**: Segmente oberhalb der konfigurierten Konfidenz-Schwelle (Standard 0,75, pro Feed
   überschreibbar) werden automatisch geschnitten und die Episode direkt veröffentlicht.
6. Der eigene Feed jedes Podcasts ist unter `/feed/{feed_id}.xml` erreichbar und kann in jedem
   Podcast-Player abonniert werden.
7. Auf der Feed-Detailseite lässt sich jede bereits analysierte Episode (egal ob "veröffentlicht"
   oder "wartet auf Review", auch bei Auto-Cut-Feeds) über **"Neu analysieren"** erneut durch die
   Werbeerkennung laufen lassen — sinnvoll, wenn zwischenzeitlich neue Jingles hinzugekommen sind.
   Audio und Transkript werden dabei wiederverwendet (kein erneuter Download/Transkription nötig);
   erst wenn beides fehlt, läuft die volle Pipeline erneut.
8. **"Nikos pewpewadpod Feed"** (`/feed/master.xml`) bündelt automatisch die jeweils neueste
   veröffentlichte Episode aus allen hinterlegten Feeds in einem einzigen Sammel-Feed. Das
   Episoden-Artwork wird dafür mit einem kleinen PewPewAdPod-Logo unten rechts versehen (das
   Original-Artwork der Einzelfeeds bleibt unverändert).

## Ausgetauschte Episoden-Audiodateien

Manche Podcaster ersetzen die Audiodatei einer bereits veröffentlichten Episode nachträglich
(z. B. um einen Fehler zu korrigieren). Erkennt die Anwendung beim Poll eines Feeds, dass sich
die Enclosure-URL einer bereits heruntergeladenen Episode geändert hat, wird **nicht** die
lokale Original- oder Schnittdatei gelöscht oder überschrieben. Stattdessen wird die neue Datei
separat heruntergeladen, transkribiert, analysiert und geschnitten (`{episode_id}_correction.mp3`)
und auf der Feed-Detailseite als "Korrektur: ready" mit Vorhör-Player angezeigt. Erst ein
expliziter Klick auf "Korrektur übernehmen" macht sie zur veröffentlichten Version (die alte
Datei bleibt dabei unangetastet auf der Platte, wird nur nicht mehr referenziert); "Verwerfen"
löscht nur die Korrektur-Dateien und lässt die Original-Episode unverändert.

## Konfiguration

- `app/config.py`: Pfade, Scheduler-Intervalle, Whisper-Modellgröße, Standard-Schwellenwert.
- `app/config/ad_keywords.yaml`: Liste der Werbe-Signalphrasen (Deutsch/Englisch) mit Gewichten sowie
  Fenster- und Scoring-Parameter für die Erkennung. Kann frei erweitert werden, ohne Code anzupassen.

## Funktionsweise der Werbeerkennung

Vier Signale fließen gemeinsam in einen Konfidenz-Score pro erkanntem Block ein:

1. **Keyword-Spotting**: Das Audio wird lokal mit `faster-whisper` transkribiert; im Transkript wird nach
   konfigurierten Signalphrasen gesucht (`app/config/ad_keywords.yaml`).
2. **Jingle-Erkennung**: mp3-Schnipsel in `app/config/ad_jingles/` (z. B. ein wiederkehrender
   Sponsoren-Jingle) werden per normalisierter Kreuzkorrelation direkt im Episoden-Audio gesucht und
   markieren Beginn/Ende eines Werbeblocks sehr zuverlässig. Sobald ein Jingle bei einem Feed einmal
   erkannt wurde, merkt sich die Anwendung das (Tabelle `feedjinglematch`) und sucht ihn bei künftigen
   Episoden desselben Feeds bevorzugt zuerst — das beschleunigt die Analyse. Details und
   Namenskonvention: [app/config/ad_jingles/README.md](app/config/ad_jingles/README.md).
3. **Duplikat-Erkennung**: Jede Analyse vergleicht das Episoden-Audio zusätzlich per Audio-Fingerprint
   (dieselbe Technik wie im Jingle Finder) gegen die vorherige Episode desselben Feeds. Abschnitte, die
   in beiden Episoden akustisch identisch sind, können kein einzigartiger gesprochener Inhalt sein
   (Intro, Outro, unveränderter Werbeblock) und bekommen daher unabhängig von Keyword/Jingle-Treffern
   eine sehr hohe Mindest-Konfidenz (Parameter: `duplicates` in `app/config/ad_keywords.yaml`).
4. **Akustische Erhärtung**: Stille-Pausen (`pydub.silence`) in der Nähe der Fenstergrenzen sowie
   Lautheitssprünge (RMS/dBFS) verfeinern die Grenzen, da Werbeblöcke oft lauter abgemischt sind als der
   restliche Inhalt.

## Review-UI

Auf der Review-Seite (`/episodes/{id}/review`) werden erkannte Kandidaten zusätzlich zur Tabelle als
farbige Balken auf einer Zeitleiste dargestellt (rot = hohe, orange = mittlere, gelb = niedrige
Konfidenz). Zeiten werden als `Minuten:Sekunden` angezeigt und editiert. Die Ränder eines Balkens lassen
sich mit der Maus ziehen, um Start/Ende anzupassen; ein Klick in die Mitte eines Balkens verschiebt ihn
als Ganzes. Ein Klick auf eine freie Stelle der Zeitleiste springt im Audio-Player an diese Position.

## Episoden-Cache & Einstellungen

Unter `/config` lässt sich begrenzen, wie viele Episoden pro Feed und wie viel Speicherplatz
(MB) insgesamt lokal vorgehalten werden (beide leer = unbegrenzt). Wird ein Limit überschritten,
entfernt ein Hintergrundjob (alle 15 Minuten, zusätzlich direkt nach jedem Schnitt) automatisch
zuerst die älteste(n) gecachte(n) Episode(n) über alle Feeds hinweg — es bleibt aber immer
mindestens eine Episode pro Feed erhalten. Betroffene Episoden bleiben in der Historie sichtbar,
verschwinden aber aus dem veröffentlichten RSS-Feed, sobald ihre Audiodatei gelöscht wurde.

## Jingle Finder

Unter `/jingle-finder` lassen sich neue Jingle-Dateien für `app/config/ad_jingles/` finden bzw.
erstellen:

- **Automatischer Modus**: vergleicht die zwei neuesten lokal vorhandenen Episoden eines Feeds
  per Audio-Fingerprinting (Landmark-Hashing, ähnlich Shazam, reines numpy) und schlägt
  wiederkehrende Abschnitte vor — Audio, das in beiden Episoden identisch vorkommt, ist mit hoher
  Wahrscheinlichkeit ein Jingle/Sponsoren-Textbaustein, da sich gesprochener Inhalt sonst nicht
  wiederholt. Vorschläge lassen sich vor dem Speichern per Zeitleiste oder Zeitfeld anpassen.
- **Manueller Modus**: lädt die neueste Episode eines Feeds; per Klick wird ein Auswahlbereich an
  der aktuellen Abspielposition erzeugt, dessen Ränder sich wie auf der Review-Seite per Maus
  ziehen lassen, bevor er als neue Jingle-mp3 gespeichert wird.

## Bekannte Grenzen

- Die Werbeerkennung ist heuristisch und kann Fehltreffer liefern oder Werbung übersehen — bei wichtigen
  Feeds wird "Manuelle Review" empfohlen.
- Der neu erzeugte Feed übernimmt Standard-iTunes-Felder (Titel, Beschreibung, Cover, Dauer, Season/Episode),
  aber keine beliebigen Custom-Namespaces des Quell-Feeds (z. B. `podcast:transcript`).
- Es ist für den lokalen Einzelnutzer-Betrieb gedacht: keine Authentifizierung, kein Multi-User-Support.

## Tests

```powershell
pip install pytest
pytest
```
