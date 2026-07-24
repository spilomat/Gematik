# gematik-watch

Automatischer, wöchentlicher Watcher für öffentliche **gematik**-Quellen rund um
das E-Rezept. Der Workflow prüft Atom-Feeds und einzelne Dateien auf Änderungen,
filtert nach Relevanz für eine **Kostenträger-Integration** (Kommunikation
zwischen Verordnendem, Versichertem und Kostenträger sowie Stichtage für
FHIR-Profilversionen) und meldet Treffer als GitHub Issue.

Keine externen Secrets nötig – es wird nur das eingebaute `GITHUB_TOKEN`
verwendet. Alle beobachteten Quellen sind öffentlich.

## Bestandteile

| Datei | Zweck |
|-------|-------|
| `.github/workflows/gematik-watch.yml` | Zeitplan (Cron + manuell), Ausführung, State-Commit |
| `scripts/watch.py` | Feeds abrufen, filtern, Issue erstellen – **hier Quellen & Filter anpassen** |
| `state/last_seen.json` | Beobachtungsstand (gesehene Einträge, Inhalts-Hashes) |

## Funktionsweise

1. **Zeitplan:** Cron montags 04:00 UTC, zusätzlich `workflow_dispatch` für
   manuelle Läufe.
2. **Vergleich:** Feeds und Dateien werden mit `state/last_seen.json` abgeglichen;
   nur neue Einträge bzw. geänderte Datei-Inhalte werden weiterverarbeitet.
3. **Erster Lauf:** Es wird ein State angelegt. Aus Feeds werden nur Einträge der
   **letzten 7 Tage** gemeldet (nicht die komplette Historie); für beobachtete
   Dateien wird nur die Baseline gespeichert (erste Meldung ab der nächsten
   Änderung).
4. **State-Commit:** Nach jedem Lauf wird der neue Stand ins Repo committet. Da
   sich `last_run` immer ändert, gibt es **jeden Lauf einen Commit** – das
   verhindert, dass GitHub den geplanten Workflow wegen Inaktivität deaktiviert.
   Ohne Treffer wird **kein Issue** erstellt, der State-Commit erfolgt trotzdem.
5. **Issue:** Bei Treffern entsteht ein Issue mit Titel `gematik-Änderungen KW <nr>`
   (ISO-Kalenderwoche). Läuft der Workflow in derselben KW erneut und das Issue
   ist noch offen, wird kommentiert statt ein Duplikat anzulegen.

Ein fehlerhafter Feed bricht den Lauf **nicht** ab – die betroffene Quelle wird
stattdessen im Issue unter „Feed-/Quellen-Fehler" vermerkt. Bestehen nur Fehler
und keine Treffer, wird trotzdem ein Issue erstellt, damit dauerhaft kaputte
Quellen sichtbar werden.

## Beobachtete Quellen

**Atom-Feeds** (`gematik/api-erp`, `gematik/fhir-profiles-erp`,
`gematik/api-app-transport-framework` – jeweils Commits und/oder Releases).

**Dateien mit Inhaltsbeobachtung** (Priorität, werden im Issue nach oben
sortiert):

- `gematik/api-erp` → `docs/erp_fhirversion.adoc` (FHIR-Versionen & Stichtage)
- `gematik/fhir-profiles-erp` → `ReleaseNotes.md`

Bei den Dateien wird der Inhalt gehasht und die als relevant erkannten Zeilen
gespeichert. Ändert sich der Inhalt, meldet der Watcher die **hinzugekommenen**
bzw. **entfernten** relevanten Zeilen – also *was* sich geändert hat, nicht nur
*dass* ein Commit stattfand.

## Relevanzfilter

**Gemeldet wird u. a.:** Communication-Profile (insb.
`GEM_ERP_PR_Communication_DispReq`), Änderungen an Kardinalitäten/Pflichtfeldern
oder Value Sets, Workflow 162 und DiGA-Verordnungsprozesse, Auflösung von
Telematik-ID/IKNR und FHIR-VZD, neue FHIR-Paketversionen/Stichtage/
Übergangsfristen sowie KIM-/TIM-Transportthemen.

**Ignoriert wird:** Tippfehler, reine Formatierung, Linkkorrekturen, reine
Beispiel-/Testdateien, Dependency-Bumps und CI-Konfiguration.

Jeder Treffer enthält im Issue: **Repo**, **Datum**, **Link**, **was sich
geändert hat** und eine **Einschätzung der Auswirkung auf eine
Kostenträger-Integration**. Sortiert wird nach Priorität – **Stichtage/Fristen
zuerst**.

## Quellen und Filter anpassen

Alles Editierbare steht gebündelt am Anfang von
[`scripts/watch.py`](scripts/watch.py) im Block **„KONFIGURATION"**:

- **Quelle hinzufügen/entfernen (Feed):** Eintrag in der Liste `FEEDS` ergänzen
  bzw. löschen. Ein Eintrag besteht aus `repo`, `kind` (Label, z. B. `commits`
  oder `releases`) und `url` (die `.atom`-URL).

- **Datei mit Inhaltsbeobachtung:** Eintrag in `CONTENT_PAGES` mit `repo`,
  `label`, `raw_url` (`raw.githubusercontent.com/...`), `html_url` (Blob-Link
  fürs Issue) und `deadline` (`True`, wenn Inhaltsänderungen als Frist ganz nach
  oben sortiert werden sollen).

- **Relevanz „melden":** Regeln in `REPORT_RULES` –
  `(regex, Kategorie-Label, Gewicht, ist_Frist)`. Größeres Gewicht = höhere
  Priorität; `ist_Frist=True` sortiert unabhängig vom Gewicht nach oben.

- **Relevanz „ignorieren":** Regexe in `IGNORE_RULES`. Greift eine Ignore-Regel
  und das stärkste Melde-Gewicht liegt unter `IGNORE_VETO_BELOW`, wird der
  Eintrag verworfen (so überleben starke Signale wie `…DispReq` auch dann, wenn
  im selben Text „typo" o. Ä. vorkommt).

- **Auswirkungstexte:** `IMPACT_HINTS` – Zuordnung Kategorie → Einschätzungstext,
  der im Issue je Treffer erscheint.

- **Erstlauf-Fenster:** `LOOKBACK_DAYS_FIRST_RUN` (Standard 7 Tage).

Nach einer Änderung an den Regeln lässt sich der Workflow über den Reiter
**Actions → gematik-watch → Run workflow** manuell testen. Ein Lauf **ohne**
gesetztes `GITHUB_TOKEN` (z. B. lokal `python scripts/watch.py`) legt kein Issue
an, sondern gibt es als Trockenlauf auf der Konsole aus.

## Lokaler Testlauf

```bash
pip install feedparser
python scripts/watch.py        # nutzt/erzeugt state/last_seen.json, Dry-Run ohne Token
```

## Berechtigungen

Der Workflow setzt `contents: write` (State-Commit) und `issues: write`
(Issue anlegen/kommentieren). Beides deckt das automatische `GITHUB_TOKEN` ab –
in privaten Repos ggf. unter *Settings → Actions → General → Workflow
permissions* „Read and write permissions" aktivieren.
