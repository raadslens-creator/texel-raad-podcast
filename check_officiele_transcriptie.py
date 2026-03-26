#!/usr/bin/env python3
"""
Raadslens - Check Officiële Transcriptie
Draait dagelijks. Controleert voor elke tijdelijke transcriptie of er
al een officiële ondertiteling beschikbaar is op bestuurlijkeinformatie.nl.
Zo ja, vervangt de tijdelijke transcriptie met de officiële versie.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import sys

def laad_gemeente_config(gemeente_id):
    config_file = Path("gemeenten.json")
    if config_file.exists():
        config = json.loads(config_file.read_text())
        for g in config["gemeenten"]:
            if g["id"] == gemeente_id:
                return g
    return {
        "id": gemeente_id,
        "ibabs_base": "https://texel.bestuurlijkeinformatie.nl",
        "transcripties_dir": "docs/transcripties",
    }

GEMEENTE_ID = os.environ.get("GEMEENTE_ID", "texel") if "os" in dir() else "texel"
GEMEENTE = laad_gemeente_config(GEMEENTE_ID)
IBABS_BASE = GEMEENTE["ibabs_base"]
TRANSCRIPTIES_DIR = Path(GEMEENTE.get("transcripties_dir", "docs/transcripties"))
MAANDEN = {
    1: "januari", 2: "februari", 3: "maart", 4: "april",
    5: "mei", 6: "juni", 7: "juli", 8: "augustus",
    9: "september", 10: "oktober", 11: "november", 12: "december"
}

# Handmatige mapping van date_id naar iBabs agenda-ID
# Vul aan als de automatische koppeling niet werkt
IBABS_ID_MAPPING = {
    "20260218_1": "acb3b1b7-db21-463d-863f-bc48af364882",
    "20260323_1": "4a8be1a5-97dd-4d90-b90c-7aa780c893f3",
    "20260331_1": "c15f1ab0-f3b1-4d9c-ae6e-07ab8b1ce32b",
    "20260401_1": "167b14c9-3ec1-42b7-b40a-71fae1fe886d",
}

DISCLAIMER_OFFICIEEL = (
    "Deze transcriptie is de officiële uitgeschreven ondertiteling van de vergadering, "
    "beschikbaar gesteld via texel.bestuurlijkeinformatie.nl. "
    "Raadslens is niet verantwoordelijk voor de inhoud van de vergadering."
)

DISCLAIMER_TIJDELIJK = (
    "Deze transcriptie is automatisch gegenereerd door Raadslens en kan fouten bevatten. "
    "Raadslens is niet verantwoordelijk voor de inhoud van de vergadering of de "
    "nauwkeurigheid van de transcriptie. De officiële verslaggeving is te vinden op "
    "texel.bestuurlijkeinformatie.nl."
)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def is_tijdelijk(transcript_path):
    """Controleer of een transcriptie de tijdelijke (automatische) versie is."""
    if not transcript_path.exists():
        return False
    tekst = transcript_path.read_text(encoding="utf-8")
    return DISCLAIMER_TIJDELIJK[:50] in tekst


def get_tijdelijke_transcripties():
    """Geef lijst van date_ids met tijdelijke transcripties."""
    if not TRANSCRIPTIES_DIR.exists():
        return []
    tijdelijk = []
    for f in TRANSCRIPTIES_DIR.glob("*.txt"):
        if is_tijdelijk(f):
            date_id = f.stem
            tijdelijk.append(date_id)
    return tijdelijk


def get_ibabs_agenda_id(date_id):
    """Zoek de iBabs agenda-ID - eerst handmatige mapping, dan automatisch."""
    if date_id in IBABS_ID_MAPPING:
        log(f"  iBabs ID via mapping: {IBABS_ID_MAPPING[date_id]}")
        return IBABS_ID_MAPPING[date_id]

    # Automatisch opzoeken via kalender
    # Sla gevonden IDs op voor toekomstig gebruik
    gevonden = _zoek_ibabs_id_automatisch(date_id)
    if gevonden:
        # Voeg toe aan mapping voor volgende keer
        IBABS_ID_MAPPING[date_id] = gevonden
    return gevonden


def _zoek_ibabs_id_automatisch(date_id):
    """Zoek iBabs agenda-ID automatisch op basis van datum."""
    try:
        dt = datetime.strptime(date_id[:8], "%Y%m%d")
        jaar = dt.year
        maand = dt.month
        dag = dt.day
        maand_naam = MAANDEN[maand]

        # Zoek in de kalender voor dit jaar/maand
        url = f"{IBABS_BASE}/Calendar?year={jaar}&month={maand}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Zoek alle agenda-IDs op de pagina
        alle_ids = list(set(re.findall(r'/Agenda/Index/([a-f0-9-]{36})', html)))

        # Controleer elke ID op de juiste datum
        # iBabs toont datums als "woensdag 18 februari 2026" of "18 februari 2026"
        datum_patronen = [
            f"{dag} {maand_naam} {jaar}",
            f"{dag:02d} {maand_naam} {jaar}",
        ]

        for agenda_id in alle_ids[:15]:
            try:
                page_url = f"{IBABS_BASE}/Agenda/Index/{agenda_id}"
                req2 = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    page_html = resp2.read().decode("utf-8", errors="ignore")
                for patroon in datum_patronen:
                    if patroon in page_html:
                        log(f"  iBabs ID automatisch gevonden: {agenda_id} ({patroon})")
                        return agenda_id
            except Exception:
                pass

        log(f"  Geen iBabs ID gevonden voor {date_id}")
        return None
    except Exception as e:
        log(f"Agenda-ID ophalen mislukt: {e}")
        return None


def fetch_officiele_ondertiteling(agenda_id):
    """
    Haal de officiële ondertiteling PDF op van bestuurlijkeinformatie.nl.
    Geeft (pdf_bytes, url) of (None, None) als niet beschikbaar.
    """
    try:
        url = f"{IBABS_BASE}/Agenda/Index/{agenda_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Zoek ondertiteling-links
        ondertiteling_ids = []
        for m in re.finditer(r'[Oo]ndertiteling.{0,200}?documentId=([a-f0-9-]{36})', html, re.DOTALL):
            ondertiteling_ids.append(m.group(1))
        for m in re.finditer(r'documentId=([a-f0-9-]{36}).{0,200}?[Oo]ndertiteling', html, re.DOTALL):
            if m.group(1) not in ondertiteling_ids:
                ondertiteling_ids.append(m.group(1))

        if not ondertiteling_ids:
            return None, None

        for doc_id in ondertiteling_ids[:2]:
            pdf_url = f"{IBABS_BASE}/Agenda/Document/{agenda_id}?documentId={doc_id}"
            try:
                req2 = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req2, timeout=15) as resp2:
                    content_type = resp2.headers.get("Content-Type", "")
                    data = resp2.read()
                    if "pdf" in content_type.lower() or data[:4] == b'%PDF':
                        return data, pdf_url
            except Exception:
                pass
        return None, None
    except Exception as e:
        log(f"Ondertiteling ophalen mislukt: {e}")
        return None, None


def parse_officiele_transcriptie(pdf_bytes, date_str, agenda_topics):
    """
    Parseer de officiële ondertiteling PDF naar een gestructureerde transcriptie.
    """
    try:
        subprocess.run(["pip", "install", "pdfminer.six", "-q"],
                       capture_output=True, check=False)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name

        result = subprocess.run(
            ["python3", "-m", "pdfminer.high_level", tmp_path],
            capture_output=True, text=True, timeout=60
        )
        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode != 0 or not result.stdout.strip():
            return None

        tekst = result.stdout.strip()

        # Bouw het transcriptiebestand op
        lines = []
        lines.append("RAADSVERGADERING TEXEL")
        lines.append(date_str)
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"[ {DISCLAIMER_OFFICIEEL} ]")
        lines.append("")

        if agenda_topics:
            lines.append("AGENDA")
            lines.append("-" * 30)
            for t in agenda_topics:
                lines.append(f"  {t}")
            lines.append("")
            lines.append("=" * 60)
            lines.append("")

        lines.append("TRANSCRIPTIE (officieel)")
        lines.append("-" * 30)
        lines.append("")
        lines.append(tekst)
        lines.append("")

        return "\n".join(lines)
    except Exception as e:
        log(f"PDF parsen mislukt: {e}")
        return None


def get_agenda_topics(agenda_id):
    """Haal agendapunten op van de iBabs-pagina."""
    try:
        url = f"{IBABS_BASE}/Agenda/Index/{agenda_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Zoek agendapunten
        topics = re.findall(r'<h3[^>]*>\s*([^<]{5,100}?)\s*</h3>', html)
        return [t.strip() for t in topics if t.strip()][:20]
    except Exception:
        return []


def main():
    log("=== Check Officiële Transcriptie ===")

    tijdelijke = get_tijdelijke_transcripties()
    if not tijdelijke:
        log("Geen tijdelijke transcripties gevonden")
        return

    log(f"{len(tijdelijke)} tijdelijke transcriptie(s) gevonden: {tijdelijke}")
    vervangen = 0

    for date_id in tijdelijke:
        log(f"\nChecken: {date_id}")

        try:
            dt = datetime.strptime(date_id[:8], "%Y%m%d")
            date_str = f"{dt.day} {MAANDEN[dt.month]} {dt.year}"
        except Exception:
            date_str = date_id[:8]

        # iBabs agenda-ID ophalen
        agenda_id = get_ibabs_agenda_id(date_id)
        if not agenda_id:
            log(f"  Geen iBabs agenda-ID gevonden voor {date_id}")
            continue

        log(f"  iBabs ID: {agenda_id}")

        # Officiële ondertiteling ophalen
        pdf_bytes, pdf_url = fetch_officiele_ondertiteling(agenda_id)
        if not pdf_bytes:
            log(f"  Geen officiële ondertiteling beschikbaar (nog niet gepubliceerd)")
            continue

        log(f"  Officiële ondertiteling gevonden: {pdf_url}")

        # Agendapunten ophalen
        topics = get_agenda_topics(agenda_id)

        # PDF parsen
        transcriptie = parse_officiele_transcriptie(pdf_bytes, date_str, topics)
        if not transcriptie:
            log(f"  PDF parsen mislukt")
            continue

        # Tijdelijke transcriptie vervangen
        transcript_path = TRANSCRIPTIES_DIR / f"{date_id}.txt"
        transcript_path.write_text(transcriptie, encoding="utf-8")
        log(f"  Tijdelijke transcriptie vervangen door officiële versie!")
        vervangen += 1

    log(f"\nKlaar: {vervangen} transcriptie(s) vervangen")


if __name__ == "__main__":
    main()
