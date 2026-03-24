#!/usr/bin/env python3
"""
Raadslens - Transcriptie script
- Haalt de laatste vergadering op uit de GitHub releases
- Download de MP3
- Transcribeert met faster-whisper + vocabulary (achternamen + naam-cache)
- Koppelt sprekers op basis van tijdstempels uit de RoyalCast API
- Corrigeert timing voor weggeknipt intro en schorsingen
- Bouwt automatisch een naam-cache op uit de API
- Slaat op als tekstbestand in docs/transcripties/
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROYALCAST_API = "https://channel.royalcast.com/portal/api/1.0/gemeentetexel/webcasts/gemeentetexel"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
DATE_ID = os.environ.get("DATE_ID", "")
TRANSCRIPTIES_DIR = Path("docs/transcripties")
NAMEN_CACHE_FILE = Path("docs/namen_cache.json")

MAANDEN = {
    1: "januari", 2: "februari", 3: "maart", 4: "april",
    5: "mei", 6: "juni", 7: "juli", 8: "augustus",
    9: "september", 10: "oktober", 11: "november", 12: "december"
}

# Achternamen die zeker kloppen uit het officiële proces-verbaal
# Alleen achternamen - voornamen worden automatisch geleerd via de API
ACHTERNAMEN_TEXEL = [
    # Texels Belang
    "van der Werf", "de Lugt", "Koot", "Timmermans", "Oosterhof", "Kuip",
    "Hooijschuur", "Duinker", "Zwezerijn", "Heijne", "van Dee", "Dros",
    "Groeskamp", "van der Wal", "Kikkert", "Hoogerheide",
    # PvdA
    "Visser-Veltkamp", "van de Belt", "Komdeur", "Breedveld", "van Ouwendorp",
    "Boschman", "van Bruggen", "Schooneman", "Witte", "Lira", "Barnard",
    "van IJsseldijk", "Oosterbaan", "Rudolph", "Lelij", "Hercules",
    # GroenLinks
    "Dros", "Mokveld", "Wiersma", "ter Borg", "von Meyjenfeldt", "Soyer",
    "Festen", "Berger", "te Sligte", "Bale", "Bohnen", "de Vrind",
    "Kompier", "de Jong", "Zegel", "Ridderinkhof", "Kieft",
    # VVD
    "Ran", "Albers", "Huisman", "Bakker", "van der Werff", "van Wijk",
    "de Lange", "van den Heuvel", "Ciçek", "van Lingen", "Schuiringa",
    "Mantje", "Korl", "Koenen", "Timmer", "Eelman", "Knol", "Pellen",
    "Kaak", "Rab", "Tromp", "Steenvoorden", "van der Kooi",
    # CDA
    "Rutten", "Houwing", "van der Knaap", "Zegeren",
    # D66
    "van de Wetering", "Leclou", "Holman", "Barendregt", "Aardema",
    "Huitema", "van Overmeeren", "Bas", "Verbraeken", "Snijders",
    "Brinkman", "van Damme", "Eijzinga", "Lindeboom", "van Heerwaarden",
    # Hart voor Texel
    "Polderman", "Kooiman", "Vonk", "Bloem", "Schouwstra", "Boumans-Beijert",
    "Kaercher", "van Beek", "Röpke", "Ris", "Zegers", "Kalis", "Krab",
    "de Porto", "Daalder", "Stroes", "van der Vaart", "Ekker",
    "van 't Noordende", "Winnubst",
    # SP
    "Spelç", "Overbeeke", "Adema", "Hoven", "Hilhorst", "Geurtz",
    "Cremers", "Dijksen",
    # Bestuur
    "Pol", "Heijne-Dros",
]

# Plaatsnamen en vaste begrippen die altijd meegaan
VASTE_BEGRIPPEN = [
    "Texel", "Texels", "Texelaar", "Texelaars",
    "Den Burg", "De Koog", "De Cocksdorp", "Oosterend", "Oudeschild",
    "De Waal", "Midsland", "De Westereen", "Eijerland",
    "Waddeneilanden", "Waddenzee", "Schiermonnikoog", "Vlieland",
    "Terschelling", "Ameland", "TESO", "Marsdiep", "Slufter",
    "gemeenteraad", "raadsvergadering", "raadslid", "wethouder",
    "burgemeester", "griffier", "college van B en W",
    "raadsbesluit", "amendement", "zienswijze", "kadernota",
    "coalitieakkoord", "hamerstuk", "bespreekstuk", "motie",
    "initiatiefvoorstel", "reglement van orde",
    "Texels Belang", "GroenLinks", "Hart voor Texel",
    "Omgevingsdienst Noord-Holland Noord", "ODNHN",
    "Veiligheidsregio Noord-Holland Noord", "GGD Hollands Noorden",
    "Regionaal Historisch Centrum Alkmaar", "RHCA",
    "Regionale Raadscommissie Noordkop", "RRN",
    "toeristenbelasting", "bestemmingsplan", "omgevingsvisie",
    "woningbouwprogramma", "klimaatadaptatieplan", "energietransitie",
    "Stappeland", "karthal", "vuurwerkverbod", "kustverdediging",
    "dijknormering", "waterveiligheid", "waddengebied",
]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_royalcast_timestamp(ts_str):
    """Zet /Date(1771437970000)/ om naar seconden."""
    if not ts_str:
        return None
    match = re.search(r"\d+", ts_str)
    if match:
        return int(match.group()) / 1000
    return None


def load_namen_cache():
    """Laad de naam-cache met bekende volledige namen."""
    if NAMEN_CACHE_FILE.exists():
        return json.loads(NAMEN_CACHE_FILE.read_text())
    return {}


def save_namen_cache(cache):
    """Sla de naam-cache op."""
    NAMEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NAMEN_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def update_namen_cache(data, cache):
    """
    Voeg nieuwe namen uit de API toe aan de cache.
    Slaat volledige namen op gekoppeld aan achternaam als sleutel.
    """
    updated = False
    for spreker in data.get("speakers", []):
        naam = spreker.get("name", {})
        voornaam = naam.get("first", "")
        achternaam = naam.get("last", "")
        tussenvoegsel = naam.get("middle", "")

        if not achternaam or not voornaam:
            continue

        volledige_naam = " ".join(filter(None, [voornaam, tussenvoegsel, achternaam]))
        sleutel = achternaam.lower()

        if sleutel not in cache or cache[sleutel] != volledige_naam:
            cache[sleutel] = volledige_naam
            log(f"Naam geleerd: {volledige_naam}")
            updated = True

    return cache, updated


def build_vocabulary(cache):
    """Bouw de volledige vocabulary op voor Whisper."""
    namen = list(VASTE_BEGRIPPEN)

    # Voeg achternamen toe
    namen.extend(ACHTERNAMEN_TEXEL)

    # Voeg volledige namen uit cache toe
    for volledige_naam in cache.values():
        if volledige_naam not in namen:
            namen.append(volledige_naam)

    return ", ".join(namen)


def get_latest_release_with_mp3():
    """Zoek de meest recente release die nog geen transcriptie heeft."""
    TRANSCRIPTIES_DIR.mkdir(parents=True, exist_ok=True)

    url = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        releases = json.loads(resp.read())

    for release in releases:
        tag = release.get("tag_name", "")
        match = re.search(r"vergadering-(\d{8}_\d+)", tag)
        if not match:
            continue
        date_id = match.group(1)

        transcript_file = TRANSCRIPTIES_DIR / f"{date_id}.txt"
        if transcript_file.exists():
            log(f"Transcriptie bestaat al: {date_id}")
            continue

        for asset in release.get("assets", []):
            if asset["name"].endswith(".mp3"):
                return date_id, asset["browser_download_url"]

    return None, None


def get_release_mp3_url(date_id):
    """Haal MP3 URL op van een specifieke release."""
    url = f"https://api.github.com/repos/{REPO}/releases/tags/vergadering-{date_id}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read())
    for asset in release.get("assets", []):
        if asset["name"].endswith(".mp3"):
            return asset["browser_download_url"]
    return None


def download_mp3(url, date_id):
    """Download MP3 van GitHub release."""
    output = f"audio/{date_id}_transcript.mp3"
    Path("audio").mkdir(exist_ok=True)
    log(f"MP3 downloaden...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        with open(output, "wb") as f:
            f.write(resp.read())
    log(f"Download OK ({Path(output).stat().st_size / 1024 / 1024:.1f} MB)")
    return output


def get_webcast_data(date_id):
    """Haal volledige webcast-data op inclusief sprekers en tijdstempels."""
    url = f"{ROYALCAST_API}/{date_id}?method=GET&key="
    log(f"Webcast-data ophalen...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"API fout: {e}")
        return {}


def get_intro_duration(data):
    """Bereken intro-duur: tijd tussen actualStart en eerste topic-event."""
    actual_start = parse_royalcast_timestamp(data.get("actualStart"))
    if not actual_start:
        return 0

    earliest_event = None
    for topic in data.get("topics", []):
        for event in topic.get("events", []):
            event_start = parse_royalcast_timestamp(event.get("start"))
            if event_start and (earliest_event is None or event_start < earliest_event):
                earliest_event = event_start

    if not earliest_event:
        return 0

    return max(0, earliest_event - actual_start)


def detect_silences(audio_file, threshold_db="-35dB", min_duration=45):
    """Detecteer schorsingen via ffmpeg."""
    detect_cmd = [
        "ffmpeg", "-i", audio_file,
        "-af", f"silencedetect=noise={threshold_db}:d={min_duration}",
        "-f", "null", "-"
    ]
    result = subprocess.run(detect_cmd, capture_output=True, text=True)
    starts = re.findall(r"silence_start: ([\d.]+)", result.stderr)
    ends = re.findall(r"silence_end: ([\d.]+)", result.stderr)
    return [
        (float(s), float(e))
        for s, e in zip(starts, ends)
        if float(e) - float(s) >= min_duration
    ]


def get_speaker_timeline(data):
    """Bouw sprekerlijst op met tijdstempels relatief aan actualStart."""
    actual_start = parse_royalcast_timestamp(data.get("actualStart"))
    if not actual_start:
        log("Geen actualStart - sprekerherkenning beperkt")
        return []

    speakers = []
    for spreker in data.get("speakers", []):
        naam = spreker.get("name", {})
        volledige_naam = " ".join(filter(None, [
            naam.get("first", ""),
            naam.get("middle", ""),
            naam.get("last", ""),
        ])).strip()
        if not volledige_naam:
            continue

        for event in spreker.get("events", []):
            start_sec = parse_royalcast_timestamp(event.get("start"))
            end_sec = parse_royalcast_timestamp(event.get("end"))
            if not start_sec or not end_sec:
                continue

            rel_start = start_sec - actual_start
            rel_end = end_sec - actual_start

            if rel_start >= 0:
                speakers.append((rel_start, rel_end, volledige_naam))

    speakers.sort(key=lambda x: x[0])
    log(f"{len(speakers)} spreekbeurten voor {len(set(s[2] for s in speakers))} sprekers")
    return speakers


def correct_speaker_times(speakers, intro_sec, silences):
    """Corrigeer spreker-tijdstempels voor intro en schorsingen."""
    corrected = []
    for start, end, naam in speakers:
        t_start = max(0, start - intro_sec)
        t_end = max(0, end - intro_sec)

        removed_start = sum(
            min(sil_end, t_start) - sil_start
            for sil_start, sil_end in silences
            if sil_start < t_start
        )
        removed_end = sum(
            min(sil_end, t_end) - sil_start
            for sil_start, sil_end in silences
            if sil_start < t_end
        )

        corrected.append((
            max(0, t_start - removed_start),
            max(0, t_end - removed_end),
            naam
        ))

    return corrected


def find_speaker_at(timestamp, speakers):
    """Zoek welke spreker aan het woord is op een gegeven tijdstip."""
    for start, end, naam in speakers:
        if start <= timestamp <= end:
            return naam
    return None


def transcribe_audio(audio_file, vocabulary):
    """Transcribeer audio met faster-whisper en custom vocabulary."""
    log("Transcriptie starten met faster-whisper...")
    log("Model laden...")

    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")

    log("Transcriberen...")
    segments, info = model.transcribe(
        audio_file,
        language="nl",
        beam_size=3,
        initial_prompt=vocabulary,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )

    log(f"Taal gedetecteerd: {info.language} ({info.language_probability:.0%})")

    result = []
    for segment in segments:
        result.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
        })

    log(f"{len(result)} segmenten getranscribeerd")
    return result


def format_timestamp(sec):
    """Formatteer seconden naar HH:MM:SS."""
    if sec is None:
        return "00:00:00"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_transcript(segments, speakers, data, date_str):
    """Bouw de volledige transcriptie op met sprekersnamen en tijdstempels."""
    lines = []
    lines.append("RAADSVERGADERING TEXEL")
    lines.append(date_str)
    lines.append("=" * 60)
    lines.append("")

    topics = data.get("topics", []) if data else []
    if topics:
        lines.append("AGENDA")
        lines.append("-" * 30)
        for t in topics:
            lines.append(f"  {t.get('title', '')}")
        lines.append("")
        lines.append("=" * 60)
        lines.append("")

    lines.append("TRANSCRIPTIE")
    lines.append("-" * 30)
    lines.append("")

    current_speaker = None
    current_block = []
    current_start = None

    for seg in segments:
        speaker = find_speaker_at(seg["start"], speakers) if speakers else None
        text = seg["text"]

        if speaker != current_speaker:
            if current_block and current_start is not None:
                ts = format_timestamp(current_start)
                label = f" {current_speaker.upper()}" if current_speaker else ""
                lines.append(f"[{ts}]{label}")
                lines.append(" ".join(current_block))
                lines.append("")

            current_speaker = speaker
            current_block = [text]
            current_start = seg["start"]
        else:
            current_block.append(text)

    if current_block and current_start is not None:
        ts = format_timestamp(current_start)
        label = f" {current_speaker.upper()}" if current_speaker else ""
        lines.append(f"[{ts}]{label}")
        lines.append(" ".join(current_block))
        lines.append("")

    return "\n".join(lines)


def upload_transcript_to_release(date_id, transcript_text):
    """Upload transcriptie als tekstbestand bij de bestaande GitHub release."""
    if not GITHUB_TOKEN or not REPO:
        return

    url = f"https://api.github.com/repos/{REPO}/releases/tags/vergadering-{date_id}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read())
    except Exception as e:
        log(f"Release niet gevonden: {e}")
        return

    upload_url = release["upload_url"].replace("{?name,label}", "")
    filename = f"{date_id}_transcriptie.txt"

    upload_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "text/plain",
    }
    req = urllib.request.Request(
        f"{upload_url}?name={filename}",
        data=transcript_text.encode("utf-8"),
        headers=upload_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            asset = json.loads(resp.read())
            log(f"Transcriptie geüpload: {asset['browser_download_url']}")
    except Exception as e:
        log(f"Upload fout: {e}")


def main():
    log("=== Raadslens Transcriptie ===")

    if not REPO or not GITHUB_TOKEN:
        log("Geen REPO of GITHUB_TOKEN")
        sys.exit(1)

    # Naam-cache laden
    cache = load_namen_cache()
    log(f"Naam-cache: {len(cache)} bekende namen")

    # Bepaal welke vergadering
    if DATE_ID:
        log(f"Handmatig opgegeven: {DATE_ID}")
        date_id = DATE_ID
        mp3_url = get_release_mp3_url(date_id)
        if not mp3_url:
            log("Geen MP3 gevonden in release")
            sys.exit(1)
    else:
        date_id, mp3_url = get_latest_release_with_mp3()
        if not date_id:
            log("Geen vergadering gevonden om te transcriberen")
            sys.exit(0)

    log(f"Vergadering: {date_id}")

    # Nederlandse datum
    try:
        dt = datetime.strptime(date_id[:8], "%Y%m%d")
        date_str = f"{dt.day} {MAANDEN[dt.month]} {dt.year}"
    except Exception:
        date_str = date_id[:8]

    # Webcast-data ophalen
    data = get_webcast_data(date_id)

    # Namen uit API toevoegen aan cache
    if data:
        cache, updated = update_namen_cache(data, cache)
        if updated:
            save_namen_cache(cache)
            log(f"Naam-cache bijgewerkt: {len(cache)} namen")

    # Vocabulary opbouwen
    vocabulary = build_vocabulary(cache)
    log(f"Vocabulary: {len(cache)} volledige namen + achternamen + begrippen")

    # Intro-duur berekenen
    intro_sec = get_intro_duration(data)
    log(f"Intro: {intro_sec:.0f}s")

    # MP3 downloaden
    audio_file = download_mp3(mp3_url, date_id)

    # Stiltes detecteren voor timing-correctie
    log("Stiltes detecteren voor timing-correctie...")
    silences = detect_silences(audio_file)
    log(f"{len(silences)} schorsingen gevonden")

    # Sprekerdata ophalen - zonder tijdcorrectie
    raw_speakers = get_speaker_timeline(data)
    speakers = raw_speakers  # Direct gebruiken, geen correctie

    # Transcriberen
    segments = transcribe_audio(audio_file, vocabulary)
    if not segments:
        log("Geen transcriptie gegenereerd")
        sys.exit(1)

    # Transcript opbouwen
    transcript = build_transcript(segments, speakers, data, date_str)

    # Opslaan
    TRANSCRIPTIES_DIR.mkdir(parents=True, exist_ok=True)
    transcript_file = TRANSCRIPTIES_DIR / f"{date_id}.txt"
    transcript_file.write_text(transcript, encoding="utf-8")
    log(f"Transcriptie opgeslagen: {transcript_file}")

    # Uploaden bij release
    upload_transcript_to_release(date_id, transcript)

    log("Klaar!")


if __name__ == "__main__":
    main()
