#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gematik-watch - Sammler.

Prueft oeffentliche gematik-Quellen (Atom-Feeds sowie einzelne Dateien) auf
Aenderungen, gleicht sie mit dem gespeicherten Stand ab und gibt die neuen,
relevanten Treffer als JSON aus. Legt selbst KEIN Issue an: die verstaendliche
Aufbereitung und die Issue-Erstellung uebernimmt woechentlich eine Claude-Routine
(siehe README.md). Fuer einen einfachen Notlauf ohne KI: --emit-issue.

Nur Standardbibliothek plus feedparser.

Anpassen:
- Quellen:  Listen FEEDS und CONTENT_PAGES weiter unten.
- Relevanz: REPORT_RULES (melden) und IGNORE_RULES (ignorieren).
- Wirkung:  IMPACT_HINTS (Text der Auswirkungs-Einschaetzung je Kategorie).

Siehe README.md fuer Details.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import urllib.error
import urllib.request

try:
    import feedparser  # type: ignore
except ImportError:  # pragma: no cover - nur ausserhalb des Workflows relevant
    feedparser = None


# ======================================================================
# KONFIGURATION - hier Quellen und Filter anpassen
# ======================================================================

# Atom-Feeds. "kind" ist nur ein Label fuer die Ausgabe (commits/releases).
FEEDS = [
    {
        "repo": "gematik/api-erp",
        "kind": "commits",
        "url": "https://github.com/gematik/api-erp/commits/master.atom",
    },
    {
        "repo": "gematik/api-erp",
        "kind": "releases",
        "url": "https://github.com/gematik/api-erp/releases.atom",
    },
    {
        "repo": "gematik/fhir-profiles-erp",
        "kind": "commits",
        "url": "https://github.com/gematik/fhir-profiles-erp/commits/master.atom",
    },
    {
        "repo": "gematik/fhir-profiles-erp",
        "kind": "releases",
        "url": "https://github.com/gematik/fhir-profiles-erp/releases.atom",
    },
    {
        "repo": "gematik/api-app-transport-framework",
        "kind": "commits",
        "url": "https://github.com/gematik/api-app-transport-framework/commits/master.atom",
    },
]

# Einzelne Dateien, bei denen der Inhalt beobachtet wird (nicht nur der Commit).
# Diese Quellen gelten als Prioritaet und werden im Issue nach oben sortiert.
CONTENT_PAGES = [
    {
        "repo": "gematik/api-erp",
        "label": "docs/erp_fhirversion.adoc",
        "raw_url": "https://raw.githubusercontent.com/gematik/api-erp/master/docs/erp_fhirversion.adoc",
        "html_url": "https://github.com/gematik/api-erp/blob/master/docs/erp_fhirversion.adoc",
        # erp_fhirversion.adoc listet FHIR-Versionen und Stichtage -> als Frist behandeln.
        "deadline": True,
    },
    {
        "repo": "gematik/fhir-profiles-erp",
        "label": "ReleaseNotes.md",
        "raw_url": "https://raw.githubusercontent.com/gematik/fhir-profiles-erp/master/ReleaseNotes.md",
        "html_url": "https://github.com/gematik/fhir-profiles-erp/blob/master/ReleaseNotes.md",
        "deadline": False,
    },
]

# Relevanzregeln zum Melden.
# (regex, Kategorie-Label, Gewicht, ist_Frist). Groesseres Gewicht = hoehere
# Prioritaet. Fristen werden unabhaengig vom Gewicht ganz nach oben sortiert.
REPORT_RULES = [
    (r"stichtag|übergangsfrist|uebergangsfrist|transition period|deadline|"
     r"gültig ab|gueltig ab|verpflichtend ab|verbindlich ab|ab dem \d|"
     r"in kraft|inbetriebnahme", "Stichtag/Frist", 100, True),
    (r"GEM_ERP_PR_Communication_DispReq", "Communication DispReq", 92, False),
    (r"communication", "Communication-Profil", 62, False),
    (r"kardinalit|cardinality|pflichtfeld|mandatory|\bmuss\b|must[- ]support|"
     r"\bMS\b|\b1\.\.1\b|\b0\.\.1\b", "Kardinalitaet/Pflichtfeld", 78, False),
    (r"value ?set|valueset|codesystem|concept ?map|slicing|binding",
     "Value Set / Codes", 58, False),
    (r"workflow[\s_/-]?162|\bwf[\s_-]?162\b", "Workflow 162", 80, False),
    (r"\bdiga\b|digitale gesundheitsanwendung", "DiGA-Verordnung", 72, False),
    (r"telematik[- ]?id|\biknr\b|fhir[- ]?vzd|\bvzd\b|verzeichnisdienst",
     "Telematik-ID/IKNR/VZD", 66, False),
    (r"paketversion|package[- ]?version|fhir[- ]?version|profilversion|"
     r"neue version|package[- ]?stichtag|dependencies\.json", "FHIR-Version/Paket", 55, False),
    (r"\bkim\b|\btim\b|transport", "KIM/TIM-Transport", 57, False),
]

# Ignorierregeln. Greift eine dieser Regeln und das staerkste Melde-Gewicht
# liegt unter IGNORE_VETO_BELOW, wird der Eintrag verworfen (z. B. reiner
# Dependency-Bump, der zufaellig "version" enthaelt).
IGNORE_RULES = [
    r"typo|tippfehler|rechtschreib|spelling",
    r"formatting|formatierung|whitespace|einrückung|einrueckung|\bindent",
    r"broken link|toter link|tote links|linkkorrektur|link[- ]?fix|fix.*link",
    r"\bexample\b|beispiel|\bsample\b",
    r"testdatei|\btestfile|unit[- ]?test|\btests?\b(?!uite)",
    r"\bbump\b|dependabot|dependency|dependencies bump|abhängigkeit|renovate",
    r"\bci\b|pipeline|github[- ]?actions|\.ya?ml\b|gradle wrapper|npm audit",
]

IGNORE_VETO_BELOW = 75  # Gewichte >= diesem Wert ueberstehen einen Ignore-Treffer.

# Auswirkungs-Einschaetzung je Kategorie (Sicht Kostentraeger-Integration).
IMPACT_HINTS = {
    "Stichtag/Frist":
        "Terminkritisch: Ab dem genannten Datum gilt eine neue Version/Regel. "
        "Rollout- und Testfenster der Kostentraeger-Schnittstelle daran ausrichten.",
    "Communication DispReq":
        "Betrifft Dispensieranfragen (DispReq) im Nachrichtenaustausch. "
        "Payload-Aufbau und Pflichtfelder der Kommunikationsschicht pruefen.",
    "Communication-Profil":
        "Aenderung an einem Communication-Profil kann Nachrichten zwischen "
        "Verordnendem, Versichertem und Kostentraeger betreffen. Mapping pruefen.",
    "Kardinalitaet/Pflichtfeld":
        "Geaenderte Kardinalitaeten/Pflichtfelder brechen ggf. Validierung und "
        "Persistenz. Feld-Mapping und Nullbarkeit auf beiden Seiten abgleichen.",
    "Value Set / Codes":
        "Geaenderte Value Sets/CodeSystems koennen Ablehnungen bei der Validierung "
        "ausloesen. Kataloge und Uebersetzungstabellen aktualisieren.",
    "Workflow 162":
        "Workflow 162 (u. a. DiGA/Direktzuweisung) beeinflusst Verordnungs- und "
        "Abrechnungspfad. Fallunterscheidung in der Integration pruefen.",
    "DiGA-Verordnung":
        "DiGA-bezogene Verordnungsprozesse koennen eigene Regeln/Workflows haben. "
        "Abrechnungs- und Genehmigungspfad des Kostentraegers pruefen.",
    "Telematik-ID/IKNR/VZD":
        "Aufloesung von Telematik-ID/IKNR bzw. FHIR-VZD betrifft die Zuordnung "
        "von Leistungserbringer und Kostentraeger. Verzeichnis-Lookups pruefen.",
    "FHIR-Version/Paket":
        "Neue FHIR-Paketversion: Profile, Abhaengigkeiten und Validator-Pakete "
        "einplanen; Kompatibilitaet zur aktuell verarbeiteten Version pruefen.",
    "KIM/TIM-Transport":
        "Transportthema (KIM/TIM): betrifft Zustellung/Adressierung der "
        "Nachrichten. Anbindung und Fehlerbehandlung des Transports pruefen.",
}

LOOKBACK_DAYS_FIRST_RUN = 7
STATE_PATH = os.environ.get("GEMATIK_STATE_PATH", "state/last_seen.json")
MAX_SEEN_PER_FEED = 300
USER_AGENT = "gematik-watch"
HTTP_TIMEOUT = 30

# ======================================================================
# Ende Konfiguration
# ======================================================================


REPORT_COMPILED = [
    (re.compile(pat, re.IGNORECASE), label, weight, deadline)
    for pat, label, weight, deadline in REPORT_RULES
]
IGNORE_COMPILED = [re.compile(pat, re.IGNORECASE) for pat in IGNORE_RULES]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------

def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("feeds", {})
    data.setdefault("content", {})
    return data


def save_state(state: dict) -> None:
    state["last_run"] = now_utc().replace(microsecond=0).isoformat()
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# ----------------------------------------------------------------------
# Relevanz
# ----------------------------------------------------------------------

def classify(text: str):
    """Bewertet Text. Gibt dict(weight, deadline, categories) zurueck oder None."""
    if not text:
        return None
    matches = [(label, weight, deadline)
               for rx, label, weight, deadline in REPORT_COMPILED
               if rx.search(text)]
    if not matches:
        return None

    max_weight = max(w for _, w, _ in matches)
    ignored = any(rx.search(text) for rx in IGNORE_COMPILED)
    if ignored and max_weight < IGNORE_VETO_BELOW:
        return None

    # Kategorien nach Gewicht absteigend, ohne Duplikate.
    seen = set()
    categories = []
    for label, weight, _dl in sorted(matches, key=lambda m: -m[1]):
        if label not in seen:
            seen.add(label)
            categories.append(label)
    return {
        "weight": max_weight,
        "deadline": any(dl for _, _, dl in matches),
        "categories": categories,
    }


def impact_text(categories) -> str:
    hints = [IMPACT_HINTS[c] for c in categories if c in IMPACT_HINTS]
    return " ".join(hints) if hints else "Auswirkung manuell pruefen."


# ----------------------------------------------------------------------
# Netzwerk
# ----------------------------------------------------------------------

def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


class _StdFeed:
    """Minimaler feedparser-Ersatz (nur die hier genutzten Felder)."""

    def __init__(self, entries):
        self.entries = entries
        self.bozo = 0


def _iso_to_structtime(text: str):
    """ISO-8601/RFC-3339 -> UTC-struct_time (wie feedparsers *_parsed)."""
    if not text:
        return None
    try:
        d = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(dt.timezone.utc)
    return d.utctimetuple()


def parse_atom_stdlib(raw: bytes) -> _StdFeed:
    """Atom-Feed nur mit der Standardbibliothek parsen (Fallback ohne feedparser).

    Wirft ET.ParseError bei kaputtem XML; ein leerer, aber valider Feed ergibt
    schlicht eine leere Eintragsliste.
    """
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = []
    for e in root.findall("a:entry", ns):
        def _txt(tag):
            el = e.find("a:" + tag, ns)
            return (el.text or "") if el is not None else ""

        link = ""
        for l in e.findall("a:link", ns):
            href = l.get("href")
            if not href:
                continue
            link = href
            if l.get("rel", "alternate") == "alternate":
                break
        content_el = e.find("a:content", ns)
        content = [{"value": content_el.text or ""}] if content_el is not None else []
        entries.append({
            "id": _txt("id"),
            "title": _txt("title"),
            "summary": _txt("summary"),
            "link": link,
            "content": content,
            "published_parsed": _iso_to_structtime(_txt("published")),
            "updated_parsed": _iso_to_structtime(_txt("updated")),
        })
    return _StdFeed(entries)


def parse_feed(url: str):
    """Feed laden und parsen. Wirft bei nicht verwertbarem Feed eine Exception.

    Nutzt feedparser, falls installiert; sonst den Standardbibliotheks-Fallback.
    """
    raw = http_get(url)
    if feedparser is not None:
        parsed = feedparser.parse(raw)
        if parsed.bozo and not parsed.entries:
            raise RuntimeError(
                f"Feed nicht parsebar: {getattr(parsed, 'bozo_exception', '?')}")
        return parsed
    return parse_atom_stdlib(raw)


def entry_datetime(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if val:
            # feedparser wie Fallback liefern UTC-struct_time -> timegm (nicht mktime).
            return dt.datetime.fromtimestamp(calendar.timegm(val), tz=dt.timezone.utc)
    return None


def entry_text(entry) -> str:
    parts = [entry.get("title", ""), entry.get("summary", "")]
    for c in entry.get("content", []) or []:
        parts.append(c.get("value", ""))
    return "\n".join(p for p in parts if p)


# ----------------------------------------------------------------------
# Verarbeitung
# ----------------------------------------------------------------------

def process_feed(cfg: dict, state: dict, errors: list, now: dt.datetime) -> list:
    key = f"{cfg['repo']}::{cfg['kind']}"
    hits = []
    try:
        parsed = parse_feed(cfg["url"])
    except Exception as exc:  # noqa: BLE001 - ein Feed darf den Lauf nicht abbrechen
        errors.append({"source": cfg["url"], "error": str(exc)})
        return hits

    feed_state = state["feeds"].get(key)
    first_run = feed_state is None
    seen_ids = set(feed_state["seen_ids"]) if feed_state else set()
    cutoff = now - dt.timedelta(days=LOOKBACK_DAYS_FIRST_RUN)

    current_ids = []
    for entry in parsed.entries:
        eid = entry.get("id") or entry.get("link") or entry.get("title", "")
        if not eid:
            continue
        current_ids.append(eid)
        if eid in seen_ids:
            continue

        when = entry_datetime(entry)
        # Erster Lauf: nur die letzten 7 Tage, nicht die komplette Historie.
        if first_run and when is not None and when < cutoff:
            continue

        verdict = classify(entry_text(entry))
        if verdict is None:
            continue

        hits.append({
            "repo": cfg["repo"],
            "kind": cfg["kind"],
            "title": entry.get("title", "(ohne Titel)").strip(),
            "link": entry.get("link", cfg["url"]),
            "date": when.date().isoformat() if when else "unbekannt",
            "sort_ts": when.timestamp() if when else 0.0,
            "categories": verdict["categories"],
            "weight": verdict["weight"],
            "deadline": verdict["deadline"],
            "detail": "",
        })

    # State fortschreiben: gesehene IDs (neue + bisherige), getrimmt.
    merged = current_ids + [i for i in (feed_state["seen_ids"] if feed_state else []) if i not in set(current_ids)]
    state["feeds"][key] = {
        "seen_ids": merged[:MAX_SEEN_PER_FEED],
        "last_checked": now.replace(microsecond=0).isoformat(),
    }
    return hits


def relevant_lines(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and classify(line) is not None:
            out.append(line)
    return out


def process_content(cfg: dict, state: dict, errors: list, now: dt.datetime) -> list:
    key = cfg["raw_url"]
    hits = []
    try:
        content = http_get(cfg["raw_url"]).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": cfg["raw_url"], "error": str(exc)})
        return hits

    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    rel_now = relevant_lines(content)
    prev = state["content"].get(key)

    if prev is None:
        # Erster Lauf: nur Baseline anlegen, noch keine Meldung.
        state["content"][key] = {
            "sha256": sha,
            "relevant_lines": rel_now,
            "last_checked": now.replace(microsecond=0).isoformat(),
        }
        return hits

    if prev.get("sha256") != sha:
        old_rel = set(prev.get("relevant_lines", []))
        new_rel = set(rel_now)
        added = [l for l in rel_now if l not in old_rel]
        removed = [l for l in prev.get("relevant_lines", []) if l not in new_rel]

        if added or removed:
            detail_parts = []
            if added:
                detail_parts.append("**Neu/relevant hinzugefuegt:**\n"
                                    + "\n".join(f"  + {l}" for l in added[:25]))
            if removed:
                detail_parts.append("**Entfernt/geaendert:**\n"
                                    + "\n".join(f"  - {l}" for l in removed[:25]))
            detail = "\n\n".join(detail_parts)
            weight = 88
            deadline = cfg.get("deadline", False)
        else:
            # Inhalt geaendert, aber keine als relevant erkannten Zeilen betroffen.
            detail = ("Inhalt geaendert, keine als relevant klassifizierten Zeilen "
                      "betroffen (vermutlich Formatierung/Redaktion). Zur Sicherheit pruefen.")
            weight = 40
            deadline = False

        cats = []
        for l in (added or rel_now):
            v = classify(l)
            if v:
                for c in v["categories"]:
                    if c not in cats:
                        cats.append(c)
        if deadline and "Stichtag/Frist" not in cats:
            cats.insert(0, "Stichtag/Frist")

        hits.append({
            "repo": cfg["repo"],
            "kind": "Inhaltsaenderung",
            "title": cfg["label"],
            "link": cfg["html_url"],
            "date": now.date().isoformat(),
            "sort_ts": now.timestamp(),
            "categories": cats or ["FHIR-Version/Paket"],
            "weight": weight,
            "deadline": deadline,
            "detail": detail,
        })

    state["content"][key] = {
        "sha256": sha,
        "relevant_lines": rel_now,
        "last_checked": now.replace(microsecond=0).isoformat(),
    }
    return hits


# ----------------------------------------------------------------------
# Issue
# ----------------------------------------------------------------------

def sort_hits(hits: list) -> list:
    # Fristen zuerst, dann Gewicht, dann Datum (neu zuerst).
    return sorted(hits, key=lambda h: (not h["deadline"], -h["weight"], -h["sort_ts"]))


def build_issue_body_from_result(result: dict, now: dt.datetime) -> str:
    """Regelbasierter Issue-Text (Fallback ohne KI, nur bei --emit-issue)."""
    lines = []
    lines.append(f"Automatischer gematik-Watch-Lauf vom "
                 f"{now.replace(microsecond=0).isoformat()} (regelbasiert, ohne KI).")
    lines.append("")
    candidates = result.get("candidates", [])
    if candidates:
        lines.append(f"**{len(candidates)} relevante Aenderung(en) gefunden**, "
                     "sortiert nach Prioritaet (Fristen oben).")
        lines.append("")
        for i, h in enumerate(candidates, 1):
            flag = "⏰ FRIST " if h["deadline"] else ""
            lines.append(f"### {i}. {flag}{h['repo']} — {h['title']}")
            lines.append("")
            lines.append(f"- **Repo:** `{h['repo']}` ({h['kind']})")
            lines.append(f"- **Datum:** {h['date']}")
            lines.append(f"- **Link:** {h['link']}")
            lines.append(f"- **Kategorien:** {', '.join(h['categories'])}")
            if h.get("changed"):
                lines.append(f"- **Was sich geaendert hat:**\n\n{_indent(h['changed'])}")
            lines.append(f"- **Auswirkung (Kostentraeger-Integration):** "
                         f"{h['rule_based_impact']}")
            lines.append("")
    else:
        lines.append("Keine relevanten Aenderungen in diesem Lauf.")
        lines.append("")

    if result.get("errors"):
        lines.append("---")
        lines.append("### ⚠️ Feed-/Quellen-Fehler")
        lines.append("")
        lines.append("Folgende Quellen konnten nicht ausgewertet werden "
                     "(Lauf wurde fortgesetzt):")
        lines.append("")
        for e in result["errors"]:
            lines.append(f"- `{e['source']}`: {e['error']}")
        lines.append("")

    lines.append("---")
    lines.append("_Regelbasierter Notlauf von `scripts/watch.py --emit-issue`. "
                 "Normalfall: woechentliche Claude-Routine mit KI-Aufbereitung._")
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n".join("  " + l if l else l for l in text.splitlines())


def _api(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_open_issue(repo: str, token: str, title: str):
    """Sucht ein offenes Issue mit exakt diesem Titel. Gibt Nummer oder None."""
    try:
        issues = _api("GET",
                      f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100",
                      token)
    except Exception as exc:  # noqa: BLE001 - Dedupe darf den Lauf nicht abbrechen
        print(f"Dedupe-Suche fehlgeschlagen: {exc}", file=sys.stderr)
        return None
    for issue in issues:
        if "pull_request" in issue:
            continue
        if issue.get("title") == title:
            return issue.get("number")
    return None


def create_issue(title: str, body: str) -> bool:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY nicht gesetzt - Issue wird nur "
              "ausgegeben, nicht erstellt.", file=sys.stderr)
        print(f"\n=== ISSUE (dry-run) ===\n{title}\n\n{body}\n", file=sys.stderr)
        return False

    try:
        existing = find_open_issue(repo, token, title)
        if existing is not None:
            # Gleiche KW laeuft erneut -> als Kommentar anhaengen statt Duplikat.
            _api("POST",
                 f"https://api.github.com/repos/{repo}/issues/{existing}/comments",
                 token, {"body": "Weiterer Lauf in derselben KW:\n\n" + body})
            print(f"Bestehendes Issue #{existing} kommentiert.")
            return True
        data = _api("POST", f"https://api.github.com/repos/{repo}/issues",
                    token, {"title": title, "body": body})
        print(f"Issue erstellt: {data.get('html_url')}")
        return True
    except urllib.error.HTTPError as exc:
        print(f"Issue-Erstellung fehlgeschlagen: HTTP {exc.code} "
              f"{exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        raise


def write_step_summary(title: str, body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"# {title}\n\n{body}\n")


# ----------------------------------------------------------------------
# Sammeln
# ----------------------------------------------------------------------

def collect(now: dt.datetime) -> dict:
    """Feeds/Dateien abrufen, mit State abgleichen, State speichern.

    Legt selbst KEIN Issue an. Gibt ein Ergebnis-Objekt zurueck, das die woechentliche
    Claude-Routine liest, verstaendlich aufbereitet und als Issue schreibt.
    """
    iso = now.isocalendar()
    state = load_state()
    hits: list = []
    errors: list = []

    for cfg in FEEDS:
        hits.extend(process_feed(cfg, state, errors, now))
    for cfg in CONTENT_PAGES:
        hits.extend(process_content(cfg, state, errors, now))

    # State immer speichern (last_run aendert sich stets) -> Stand bleibt aktuell,
    # es gibt jeden Lauf einen Commit.
    save_state(state)

    ordered = sort_hits(hits)
    return {
        "run_at": now.replace(microsecond=0).isoformat(),
        "iso_week": iso[1],
        "iso_year": iso[0],
        "issue_title": f"gematik-Änderungen KW {iso[1]}",
        "candidate_count": len(ordered),
        # bereits nach Prioritaet sortiert (Fristen zuerst)
        "candidates": [{
            "repo": h["repo"],
            "kind": h["kind"],
            "title": h["title"],
            "date": h["date"],
            "link": h["link"],
            "categories": h["categories"],
            "deadline": h["deadline"],
            "weight": h["weight"],
            "changed": h.get("detail", ""),
            "rule_based_impact": impact_text(h["categories"]),
        } for h in ordered],
        "errors": errors,
    }


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    emit_issue = "--emit-issue" in argv  # mechanischer Notlauf ohne KI-Aufbereitung

    now = now_utc()
    result = collect(now)

    # Kandidaten als JSON ausgeben -> die Claude-Routine liest das aus stdout.
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if emit_issue:
        # Fallback: regelbasiertes Issue ohne KI (z. B. wenn keine Routine laeuft).
        hits_present = result["candidate_count"] > 0
        if hits_present or result["errors"]:
            title = result["issue_title"]
            body = build_issue_body_from_result(result, now)
            write_step_summary(f"{title} ({result['iso_year']})", body)
            create_issue(title, body)
        else:
            print("Keine Treffer und keine Fehler - kein Issue. State aktualisiert.",
                  file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
