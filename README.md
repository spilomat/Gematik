# gematik-watch

Wöchentlicher Watcher für öffentliche **gematik**-Quellen rund um das E-Rezept.
Er prüft Atom-Feeds und einzelne Dateien auf Änderungen, filtert nach Relevanz
für eine **Kostenträger-Integration** (Kommunikation zwischen Verordnendem,
Versichertem und Kostenträger sowie Stichtage für FHIR-Profilversionen) und
meldet neue Treffer als **GitHub-Issue**.

Die verständliche Aufbereitung übernimmt **Claude**, sobald ein API-Schlüssel
hinterlegt ist. Ohne Schlüssel läuft alles trotzdem – dann mit einer einfachen,
regelbasierten Meldung. Du kannst also sofort starten und jederzeit auf
Claude-Qualität upgraden.

## Warum GitHub Actions (und nicht ein Claude-Zeitplan im Web)?

Der Watcher läuft als **GitHub-Actions-Cron** – bewusst auf GitHubs Servern.
Nur dort ist beides möglich: die **öffentlichen gematik-Seiten erreichen** und
ein **Issue schreiben**. Die Cloud-Umgebung von Claude Code (Web) darf aus
Sicherheitsgründen nur mit dem eigenen Repo sprechen und käme an die
gematik-Feeds gar nicht heran. Der Actions-Cron ist also der Zeitplan – die
KI-Aufbereitung passiert innerhalb dieses Laufs.

## Bestandteile

| Datei | Zweck |
|-------|-------|
| `.github/workflows/gematik-watch.yml` | Zeitplan (Cron + manuell), Ausführung, State-Commit |
| `scripts/watch.py` | Feeds abrufen, filtern, Claude-Aufbereitung, Issue anlegen – **hier Quellen & Filter anpassen** |
| `state/last_seen.json` | Beobachtungsstand (gesehene Einträge, Inhalts-Hashes) |

## Ablauf

1. **Zeitplan:** Cron montags ~04:07 UTC, zusätzlich „Run workflow" für manuelle
   Läufe.
2. **Vergleich:** Feeds/Dateien werden mit `state/last_seen.json` abgeglichen;
   nur neue Einträge bzw. geänderte Datei-Inhalte werden weiterverarbeitet.
3. **Erster Lauf:** State anlegen. Aus Feeds werden nur Einträge der **letzten 7
   Tage** gemeldet (nicht die komplette Historie); für beobachtete Dateien wird
   nur die Baseline gespeichert (Meldung ab der nächsten Änderung).
4. **Aufbereitung:** Ist `ANTHROPIC_API_KEY` gesetzt, schreibt Claude eine
   verständliche Meldung; sonst wird eine regelbasierte Fassung erstellt. Ein
   fehlgeschlagener KI-Aufruf fällt automatisch auf die regelbasierte Fassung
   zurück – der Lauf bricht nie deswegen ab.
5. **Issue:** Bei Treffern entsteht ein Issue `gematik-Änderungen KW <nr>`
   (ISO-Woche). Läuft der Workflow in derselben Woche erneut und das Issue ist
   noch offen, wird kommentiert statt ein Duplikat anzulegen.
6. **State-Commit:** Nach jedem Lauf wird der neue Stand committet. Da sich
   `last_run` immer ändert, gibt es **jeden Lauf einen Commit** – das verhindert,
   dass GitHub den geplanten Workflow wegen Inaktivität deaktiviert. Ohne Treffer:
   kein Issue, aber trotzdem State-Commit.

Ein fehlerhafter Feed bricht den Lauf **nicht** ab – die betroffene Quelle wird
im Issue unter „Feed-/Quellen-Fehler" vermerkt.

## Einrichtung

1. **Rechte** (bei privatem Repo): *Settings → Actions → General → Workflow
   permissions* → **„Read and write permissions"** aktivieren.
2. **Optional – Claude-Aufbereitung aktivieren:** einen Anthropic-API-Schlüssel
   als Repository-Secret hinterlegen:
   *Settings → Secrets and variables → Actions → New repository secret* →
   Name **`ANTHROPIC_API_KEY`**, Wert = dein Schlüssel (von
   <https://console.anthropic.com>). Ohne diesen Secret läuft der Watcher
   regelbasiert weiter.
3. **Testlauf:** Reiter **Actions → gematik-watch → „Run workflow"**.

## Beobachtete Quellen

**Atom-Feeds** (`gematik/api-erp`, `gematik/fhir-profiles-erp`,
`gematik/api-app-transport-framework` – Commits und/oder Releases).

**Dateien mit Inhaltsbeobachtung** (Priorität, im Issue nach oben sortiert):

- `gematik/api-erp` → `docs/erp_fhirversion.adoc` (FHIR-Versionen & Stichtage)
- `gematik/fhir-profiles-erp` → `ReleaseNotes.md`

Bei den Dateien wird der Inhalt gehasht und die als relevant erkannten Zeilen
gespeichert. Ändert sich der Inhalt, werden die **hinzugekommenen** bzw.
**entfernten** relevanten Zeilen gemeldet – also *was* sich geändert hat.

## Relevanzfilter

**Gemeldet wird u. a.:** Communication-Profile (insb.
`GEM_ERP_PR_Communication_DispReq`), Kardinalitäten/Pflichtfelder, Value Sets,
Workflow 162 und DiGA, Auflösung von Telematik-ID/IKNR und FHIR-VZD, neue
FHIR-Paketversionen/Stichtage/Übergangsfristen sowie KIM-/TIM-Transport.

**Ignoriert wird:** Tippfehler, reine Formatierung, Linkkorrekturen, reine
Beispiel-/Testdateien, Dependency-Bumps und CI-Konfiguration.

Jeder Treffer im Issue enthält: **Repo**, **Datum**, **Link**, **was sich
geändert hat** und eine **Einschätzung der Auswirkung auf eine
Kostenträger-Integration**. Sortiert nach Priorität – **Stichtage/Fristen zuerst**.

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
- **Auswirkungstexte (Regel-Fallback):** `IMPACT_HINTS`.
- **Erstlauf-Fenster:** `LOOKBACK_DAYS_FIRST_RUN` (Standard 7 Tage).
- **KI-Modell:** `AI_MODEL` (Standard `claude-haiku-4-5-20251001`) bzw. per
  Umgebungsvariablen `GEMATIK_AI_MODEL` / `GEMATIK_AI_ENDPOINT`.

## Lokaler Testlauf

```bash
pip install feedparser        # optional; ohne feedparser nutzt das Skript einen
                              # eingebauten Atom-Parser aus der Standardbibliothek

# Nur sammeln und den Meldungstext ausgeben, KEIN Issue anlegen:
python scripts/watch.py --dry-run

# Mit Claude-Aufbereitung testen:
ANTHROPIC_API_KEY=sk-... python scripts/watch.py --dry-run
```
