#!/usr/bin/env python3
"""
Test de ondertiteling scraper van bestuurlijkeinformatie.nl
Draai lokaal met: python3 test_scraper.py
"""

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from datetime import datetime

IBABS_BASE = "https://texel.bestuurlijkeinformatie.nl"
VOCABULARY_CACHE_FILE = Path("docs/vocabulary_cache.json")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_vergadering_ids():
    """Haal vergadering-IDs op via de kalender (meerdere jaren)."""
    alle_ids = []
    jaren = [2022, 2023, 2024, 2025, 2026]
    maanden = range(1, 13)

    # Haal eerst de hoofdkalender op
    url = f"{IBABS_BASE}/Calendar"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        ids = re.findall(r'/Agenda/Index/([a-f0-9-]{36})', html)
        alle_ids.extend(ids)
        log(f"Hoofdkalender: {len(ids)} IDs gevonden")
    except Exception as e:
        log(f"Kalender ophalen mislukt: {e}")

    # Haal per jaar/maand op voor historische data
    for jaar in jaren:
        for maand in maanden:
            url = f"{IBABS_BASE}/Calendar?year={jaar}&month={maand}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                ids = re.findall(r'/Agenda/Index/([a-f0-9-]{36})', html)
                if ids:
                    alle_ids.extend(ids)
                    log(f"  {jaar}/{maand:02d}: {len(ids)} IDs")
            except Exception:
                pass

    uniek = list(set(alle_ids))
    log(f"Totaal unieke vergadering-IDs: {len(uniek)}")
    return uniek


def fetch_ondertiteling(agenda_id):
    """Haal ondertiteling PDF op van een specifieke vergadering."""
    url = f"{IBABS_BASE}/Agenda/Index/{agenda_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        alle_doc_ids = re.findall(r'documentId=([a-f0-9-]{36})', html)
        ondertiteling_ids = []

        for m in re.finditer(r'[Oo]ndertiteling.{0,200}?documentId=([a-f0-9-]{36})', html, re.DOTALL):
            ondertiteling_ids.append(m.group(1))
        for m in re.finditer(r'documentId=([a-f0-9-]{36}).{0,200}?[Oo]ndertiteling', html, re.DOTALL):
            if m.group(1) not in ondertiteling_ids:
                ondertiteling_ids.append(m.group(1))

        te_proberen = ondertiteling_ids[:2] or alle_doc_ids[:3]

        for doc_id in te_proberen:
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
        return None, None


def extract_woorden(pdf_bytes):
    """Extraheer woorden uit PDF."""
    try:
        subprocess.run(["pip", "install", "pdfminer.six", "-q"], capture_output=True, check=False)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name

        result = subprocess.run(
            ["python3", "-m", "pdfminer.high_level", tmp_path],
            capture_output=True, text=True, timeout=30
        )
        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode != 0:
            return [], ""

        tekst = result.stdout
        stopwoorden = {"voor", "deze", "maar", "door", "over", "naar", "zijn",
                       "heeft", "worden", "kunnen", "moet", "wordt", "geen",
                       "meer", "ook", "niet", "veel", "heel", "even", "want",
                       "zoals", "omdat", "wanneer", "zodat", "toch", "reeds"}
        woorden = re.findall(r'\b[A-Za-zÀ-ÿ]{4,}\b', tekst)
        gefilterd = [w for w in set(woorden) if w.lower() not in stopwoorden]
        return gefilterd, tekst[:500]
    except Exception as e:
        return [], str(e)


def main():
    log("=== Test Ondertiteling Scraper ===")

    # Installeer pdfminer
    subprocess.run(["pip", "install", "pdfminer.six", "-q"], capture_output=True, check=False)

    # Haal IDs op
    log("Vergadering-IDs ophalen...")
    ids = fetch_vergadering_ids()

    if not ids:
        log("Geen IDs gevonden!")
        return

    # Test eerste 10 vergaderingen
    log(f"\nTest eerste 10 vergaderingen op ondertiteling...")
    gevonden = 0
    alle_woorden = set()

    # Laad bestaande cache
    if VOCABULARY_CACHE_FILE.exists():
        cache = json.loads(VOCABULARY_CACHE_FILE.read_text())
        verwerkt = set(cache.get("vergaderingen_verwerkt", []))
        alle_woorden = set(cache.get("woorden", []))
    else:
        verwerkt = set()

    for agenda_id in ids[:20]:
        if agenda_id in verwerkt:
            log(f"  {agenda_id}: al verwerkt")
            continue

        pdf_bytes, pdf_url = fetch_ondertiteling(agenda_id)
        if not pdf_bytes:
            log(f"  {agenda_id}: geen ondertiteling gevonden")
            verwerkt.add(agenda_id)
            continue

        woorden, preview = extract_woorden(pdf_bytes)
        if woorden:
            alle_woorden.update(woorden)
            gevonden += 1
            log(f"  {agenda_id}: {len(woorden)} woorden - {pdf_url}")
            log(f"    Preview: {preview[:100].strip()}")
        else:
            log(f"  {agenda_id}: PDF gevonden maar geen woorden extracted")

        verwerkt.add(agenda_id)

        if gevonden >= 3:
            break

    log(f"\nResultaat: {gevonden} ondertitelingen verwerkt, {len(alle_woorden)} unieke woorden")

    if alle_woorden:
        # Sla op in cache
        VOCABULARY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cache = {
            "woorden": list(alle_woorden),
            "vergaderingen_verwerkt": list(verwerkt),
            "laatste_update": datetime.now().isoformat()
        }
        VOCABULARY_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
        log(f"Cache opgeslagen: {VOCABULARY_CACHE_FILE}")

        # Toon sample woorden
        sample = sorted(alle_woorden)[:50]
        log(f"\nSample woorden: {', '.join(sample)}")
    else:
        log("\nGeen woorden gevonden - scraper werkt mogelijk niet goed")
        log("Controleer of bestuurlijkeinformatie.nl bereikbaar is")


if __name__ == "__main__":
    main()
