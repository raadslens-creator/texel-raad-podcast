#!/usr/bin/env python3
"""
Raadslens - Transcriptie script
- Haalt de laatste vergadering op uit de GitHub releases
- Download de MP3
- Transcribeert met faster-whisper
- Koppelt sprekers op basis van tijdstempels uit de RoyalCast API
- Slaat op als tekstbestand in docs/transcripties/
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROYALCAST_API = "https://channel.royalcast.com/portal/api/1.0/gemeentetexel/webcasts/gemeentetexel"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
DATE_ID = os.environ.get("DATE_ID", "")  # Optioneel: handmatig opgeven
TRANSCRIPTIES_DIR = Path("docs/transcripties")

MAANDEN = {
    1: "januari", 2: "februari", 3: "maart", 4: "april",
    5: "mei", 6: "juni", 7: "juli", 8: "augustus",
    9: "september", 10: "oktober", 11: "november", 12: "december"
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Authorization": f"token {GITHUB_TOKEN}" if "api.github.com" in url else ""
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_latest_date_id():
    """Zoek de meest recente vergadering die nog geen transcriptie heeft."""
    TRANSCRIPTIES_DIR.mkdir(parents=True, exist_ok=True)

    # Haal releases op van GitHub
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
        # Tag formaat: vergadering-20260323_1
        match = re.search(r"vergadering-(\d{8}_\d+)", tag)
        if not match:
            continue
        date_id = match.group(1)

        # Controleer of transcriptie al bestaat
        transcript_file = TRANSCRIPTIES_DIR / f"{date_id}.txt"
        if transcript_file.exists():
            log(f"Transcriptie bestaat al: {date_id}")
            continue

        # Zoek MP3 in release assets
        for asset in release.get("assets", []):
            if asset["name"].endswith(".mp3"):
                return date_id, asset["browser_download_url"]

    return None, None


def download_mp3(url, date_id):
    """Download MP3 van GitHub release."""
    output = f"audio/{date_id}_transcript.mp3"
    Path("audio").mkdir(exist_ok=True)
    log(f"MP3 downloaden: {url[:60]}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        with open(output, "wb") as f:
            f.write(resp.read())
    log(f"Download OK ({Path(output).stat().st_size / 1024 / 1024:.1f} MB)")
    return output


def get_speaker_timeline(date_id):
    """
    Haal sprekerdata op uit de RoyalCast API.
    Geeft een gesorteerde lijst van (start_sec, eind_sec, naam) tuples.
    """
    url = f"{ROYALCAST_API}/{date_id}?method=GET&key="
    log(f"Sprekerdata ophalen...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log(f"API fout: {e}")
        return [], None

    actual_start = data.get("actualStart")
    if actual_start:
        # Formaat: /Date(1771437970000)/
        ms = int(re.search(r"\d+", actual_start).group())
        start_epoch = ms / 1000
    else:
        start_epoch = None

    speakers = []
    for spreker in data.get("speakers", []):
        naam = spreker.get("name", {})
        volledige_naam = " ".join(filter(None, [
            naam.get("title"),
            naam.get("first"),
            naam.get("middle"),
            naam.get("last"),
        ])).strip()
        if not volledige_naam:
            continue

        for event in spreker.get("events", []):
            start_ms = int(re.search(r"\d+", event.get("start", "/Date(0)/")).group())
            end_ms = int(re.search(r"\d+", event.get("end", "/Date(0)/")).group())

            if start_epoch:
                start_sec = (start_ms / 1000) - start_epoch
                end_sec = (end_ms / 1000) - start_epoch
            else:
                start_sec = start_ms / 1000
                end_sec = end_ms / 1000

            if start_sec >= 0:
                speakers.append((start_sec, end_sec, volledige_naam))

    speakers.sort(key=lambda x: x[0])
    log(f"{len(speakers)} spreekbeurten gevonden voor {len(set(s[2] for s in speakers))} sprekers")
    return speakers, data


def find_speaker_at(timestamp, speakers):
    """Zoek welke spreker aan het woord is op een gegeven tijdstip."""
    for start, end, naam in speakers:
        if start <= timestamp <= end:
            return naam
    # Zoek dichtstbijzijnde spreker binnen 30 seconden
    closest = None
    closest_dist = 30
    for start, end, naam in speakers:
        dist = min(abs(timestamp - start), abs(timestamp - end))
        if dist < closest_dist:
            closest_dist = dist
            closest = naam
    return closest


def transcribe_audio(audio_file):
    """Transcribeer audio met faster-whisper."""
    log("Transcriptie starten met faster-whisper...")
    log("Model downloaden (eenmalig, ~150MB)...")

    from faster_whisper import WhisperModel

    # Small model - goed genoeg voor Nederlands raadsvergadering
    # Gebruik int8 voor snelheid op CPU
    model = WhisperModel("small", device="cpu", compute_type="int8")

    log("Transcriberen...")
    segments, info = model.transcribe(
        audio_file,
        language="nl",
        beam_size=3,
        vad_filter=True,           # Voice activity detection - stiltes overslaan
        vad_parameters=dict(
            min_silence_duration_ms=500
        )
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
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_transcript(segments, speakers, data, date_str):
    """Bouw de volledige transcriptie op met sprekersnamen."""
    lines = []
    lines.append(f"RAADSVERGADERING TEXEL")
    lines.append(f"{date_str}")
    lines.append("=" * 60)
    lines.append("")

    # Agendapunten als referentie
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
            # Sla vorige spreker-blok op
            if current_block and current_speaker:
                ts = format_timestamp(current_start)
                lines.append(f"[{ts}] {current_speaker.upper()}")
                lines.append(" ".join(current_block))
                lines.append("")
            elif current_block:
                ts = format_timestamp(current_start)
                lines.append(f"[{ts}]")
                lines.append(" ".join(current_block))
                lines.append("")

            current_speaker = speaker
            current_block = [text]
            current_start = seg["start"]
        else:
            current_block.append(text)

    # Laatste blok
    if current_block:
        ts = format_timestamp(current_start)
        label = current_speaker.upper() if current_speaker else ""
        lines.append(f"[{ts}] {label}".strip())
        lines.append(" ".join(current_block))
        lines.append("")

    return "\n".join(lines)


def upload_transcript_to_release(date_id, transcript_text):
    """Upload transcriptie als tekstbestand bij de bestaande GitHub release."""
    if not GITHUB_TOKEN or not REPO:
        return

    # Zoek de release
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

    # Bepaal welke vergadering
    if DATE_ID:
        log(f"Handmatig opgegeven: {DATE_ID}")
        date_id = DATE_ID
        # Zoek MP3 URL in releases
        url = f"https://api.github.com/repos/{REPO}/releases/tags/vergadering-{date_id}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read())
        mp3_url = None
        for asset in release.get("assets", []):
            if asset["name"].endswith(".mp3"):
                mp3_url = asset["browser_download_url"]
                break
        if not mp3_url:
            log("Geen MP3 gevonden in release")
            sys.exit(1)
    else:
        date_id, mp3_url = get_latest_date_id()
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

    # Sprekerdata ophalen
    speakers, data = get_speaker_timeline(date_id)

    # MP3 downloaden
    audio_file = download_mp3(mp3_url, date_id)

    # Transcriberen
    segments = transcribe_audio(audio_file)

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
