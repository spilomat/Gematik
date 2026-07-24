# gematik-watch

Wöchentlicher Watcher für öffentliche **gematik**-Quellen rund um das E-Rezept.
Er prüft Atom-Feeds und einzelne Dateien auf Änderungen, filtert grob nach
Relevanz für eine **Kostenträger-Integration** und lässt die Treffer von einer
**KI (Claude) verständlich aufbereiten**, die daraus ein GitHub-Issue schreibt.

Die Aufteilung:

- **`scripts/watch.py`** – der *Sammler*. Reine Mechanik: Feeds/Dateien abrufen,
  mit dem gespeicherten Stand vergleichen, neue relevante Treffer als Daten
  ausgeben. Legt selbst **kein** Issue an.
- **Claude-Routine (Zeitplan)** – startet montags automatisch eine
  Claude-Sitzung, führt den Sammler aus, **formuliert die Änderungen
  verständlich** (was hat sich geändert, warum relevant, welche Auswirkung auf
  eine Kostenträger-Integration) und erstellt das Issue.

So macht die Aufbereitung dieselbe KI, die auch dieses Repo betreut – **ohne
zusätzlichen API-Schlüssel und ohne externe Secrets**.

## Bestandteile

| Datei | Zweck |
|-------|-------|
| `scripts/watch.py` | Sammler: Feeds abrufen, filtern, Treffer als JSON ausgeben – **hier Quellen & Filter anpassen** |
| `state/last_seen.json` | Beobachtungsstand (gesehene Einträge, Inhalts-Hashes) |

## Ablauf pro Woche

1. Der Zeitplan (Routine) startet montags eine Claude-Sitzung.
2. Claude holt den aktuellen Repo-Stand und führt `python scripts/watch.py` aus.
3. Der Sammler vergleicht die Quellen mit `state/last_seen.json` und gibt die
   **neuen** relevanten Treffer als JSON aus (bereits nach Priorität sortiert,
   Fristen oben). Der Beobachtungsstand wird aktualisiert.
4. Claude liest die Treffer, **schreibt eine verständliche deutsche
   Einschätzung** und legt ein Issue `gematik-Änderungen KW <nr>` an (bzw.
   kommentiert ein schon offenes Issue derselben Woche).
5. Claude committet den neuen Stand zurück ins Repo. Gibt es keine Treffer:
   kein Issue, aber trotzdem ein State-Commit, damit der Stand aktuell bleibt.

**Erster Lauf:** Es wird ein Stand angelegt. Aus Feeds werden nur Einträge der
**letzten 7 Tage** gemeldet (nicht die komplette Historie); für beobachtete
Dateien wird nur die Baseline gespeichert (Meldung ab der nächsten Änderung).

**Fehlerhafte Quelle:** Ein kaputter Feed bricht den Lauf **nicht** ab – er wird
im Issue vermerkt.

## Beobachtete Quellen

**Atom-Feeds** (`gematik/api-erp`, `gematik/fhir-profiles-erp`,
`gematik/api-app-transport-framework` – Commits und/oder Releases).

**Dateien mit Inhaltsbeobachtung** (Priorität, im Issue nach oben sortiert):

- `gematik/api-erp` → `docs/erp_fhirversion.adoc` (FHIR-Versionen & Stichtage)
- `gematik/fhir-profiles-erp` → `ReleaseNotes.md`

Bei den Dateien wird der Inhalt gehasht und die als relevant erkannten Zeilen
gespeichert. Ändert sich der Inhalt, meldet der Sammler die **hinzugekommenen**
bzw. **entfernten** relevanten Zeilen – also *was* sich geändert hat.

## Relevanzfilter (Vorauswahl)

Der Sammler wählt grob vor, damit die KI nur Sinnvolles bekommt.

**Gemeldet wird u. a.:** Communication-Profile (insb.
`GEM_ERP_PR_Communication_DispReq`), Kardinalitäten/Pflichtfelder, Value Sets,
Workflow 162 und DiGA, Auflösung von Telematik-ID/IKNR und FHIR-VZD, neue
FHIR-Paketversionen/Stichtage/Übergangsfristen sowie KIM-/TIM-Transport.

**Ignoriert wird:** Tippfehler, reine Formatierung, Linkkorrekturen, reine
Beispiel-/Testdateien, Dependency-Bumps und CI-Konfiguration. Die endgültige
Relevanz- und Verständlichkeitsbewertung übernimmt die KI.

## Quellen und Filter anpassen

Alles Editierbare steht gebündelt am Anfang von
[`scripts/watch.py`](scripts/watch.py) im Block **„KONFIGURATION"**:

- **Feed hinzufügen/entfernen:** Liste `FEEDS` (`repo`, `kind`, `url`).
- **Datei mit Inhaltsbeobachtung:** Liste `CONTENT_PAGES` (`repo`, `label`,
  `raw_url`, `html_url`, `deadline`).
- **Relevanz „melden":** `REPORT_RULES` – `(regex, Kategorie, Gewicht, ist_Frist)`.
  Größeres Gewicht = höhere Priorität; `ist_Frist=True` sortiert nach oben.
- **Relevanz „ignorieren":** `IGNORE_RULES`; `IGNORE_VETO_BELOW` steuert, ab
  welchem Gewicht ein Treffer einen Ignore-Treffer übersteht.
- **Erstlauf-Fenster:** `LOOKBACK_DAYS_FIRST_RUN` (Standard 7 Tage).

## Zeitplan ändern / anhalten

Der wöchentliche Lauf ist eine **Claude-Routine** (Standard: montags 04:00 UTC).
Ändern oder pausieren kannst du sie, indem du Claude einfach bittest, z. B.
„ändere den gematik-Watch auf freitags" oder „pausiere den gematik-Watch".

## Manueller Lauf / Test

```bash
pip install feedparser

# Nur sammeln (gibt die Treffer als JSON aus, legt kein Issue an):
python scripts/watch.py

# Regelbasierter Notlauf OHNE KI – legt direkt ein einfaches Issue an
# (braucht GITHUB_TOKEN und GITHUB_REPOSITORY in der Umgebung):
python scripts/watch.py --emit-issue
```

Der Normalfall ist die Claude-Routine mit KI-Aufbereitung; `--emit-issue` ist nur
ein einfacher Rückfall, falls einmal ohne KI gearbeitet werden soll.
