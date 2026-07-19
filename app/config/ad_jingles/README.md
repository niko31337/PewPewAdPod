# Werbe-Jingles

Lege hier kurze mp3-Schnipsel (Jingles/Stinger/Sounddesign-Elemente) ab, die typischerweise
den Beginn und/oder das Ende eines Werbeblocks markieren. Die Anwendung sucht diese Schnipsel
per Audio-Fingerprint (normalisierte Kreuzkorrelation) automatisch im Episoden-Audio.

## Namenskonvention (optional)

- Dateiname enthält `start` oder `beginn` → wird als Marker für den **Beginn** eines Werbeblocks
  gewertet.
- Dateiname enthält `end` oder `ende` → wird als Marker für das **Ende** eines Werbeblocks gewertet.
- Alles andere (z. B. `sponsor_jingle.mp3`) wird als generischer Marker behandelt. Taucht ein
  solcher Jingle zweimal in einem plausiblen Abstand auf (z. B. vor und nach dem Werbeblock),
  werden beide Vorkommen automatisch als Start/Ende-Paar erkannt.

## Beispiele

```
app/config/ad_jingles/
  sponsor_bumper.mp3
  werbung_start.mp3
  werbung_ende.mp3
```

## Caching pro Feed

Sobald ein Jingle in einer Episode eines Feeds erkannt wurde, merkt sich die Anwendung das in
der Datenbank (Tabelle `feedjinglematch`) und sucht diesen Jingle bei künftigen Episoden desselben
Feeds bevorzugt zuerst — das beschleunigt die Analyse, da nicht bei jeder Episode alle Jingles
gegen die komplette Audiodatei geprüft werden müssen.
