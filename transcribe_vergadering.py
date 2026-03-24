#!/usr/bin/env python3
"""
Raadslens - Transcriptie script
- Haalt de laatste vergadering op uit de GitHub releases
- Download de MP3
- Transcribeert met faster-whisper + custom vocabulary
- Koppelt sprekers op basis van tijdstempels uit de RoyalCast API
- Corrigeert timing voor weggeknipt intro en schorsingen
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
DATE_ID = os.environ.get("DATE_ID", "")
TRANSCRIPTIES_DIR = Path("docs/transcripties")

MAANDEN = {
    1: "januari", 2: "februari", 3: "maart", 4: "april",
    5: "mei", 6: "juni", 7: "juli", 8: "augustus",
    9: "september", 10: "oktober", 11: "november", 12: "december"
}

# Custom vocabulary voor Whisper - Texelse namen, plaatsen en politici
TEXEL_VOCABULARY = """
Texel, Texels, Texelaar, Texelaars, Den Burg, De Koog, De Cocksdorp, Oosterend, 
Oudeschild, De Waal, Midsland, Sint Maartenszee, De Westereen,
Waddeneilanden, Waddenzee, Schiermonnikoog, Vlieland, Terschelling, Ameland,
TESO, Marsdiep, Eijerland, Slufter, Muy, Texelse Courant,
gemeenteraad, raadsvergadering, raadslid, wethouder, burgemeester, griffier,
college van B en W, burgemeester en wethouders, raadsbesluit, amendement,
zienswijze, kadernota, coalitieakkoord, bestuursakkoord, hamerstuk, bespreekstuk,
motie, initiatiefvoorstel, interpellatie, reglement van orde,
Texels Belang, PvdA, GroenLinks, VVD, CDA, D66, Hart voor Texel, SP,
Mark Pol, burgemeester Pol,
Anneke Visser-Veltkamp, Remko van de Belt, Cecile Komdeur, Daniëlle Breedveld,
Margreet van Ouwendorp, Ruud Boschman, Aafke van Bruggen, Marloes Schooneman,
Natasja Lelij, Eric Hercules,
Jacquelien Dros, Jolanda Mokveld, Anke Wiersma, Arnout ter Borg,
Felix von Meyjenfeldt, Henry Soyer, Hanneke Festen, Gerrit Berger,
Wouter te Sligte, Nathalie Bale, Nicolien Bohnen, Ferry Zegel, Hans Ridderinkhof,
Rikus Kieft,
Nick Ran, Nannette Albers, Albert Huisman, Jan Bakker, Cees van der Werff,
Joost van Wijk, Inge de Lange, Ellen van den Heuvel, Sema Ciçek,
Gert van Lingen, Jan Schuiringa, Sjaak Mantje, Rob Korl, Piet Koenen,
Maaike Timmer, Jeroen Eelman, Jan Knol, Marianne Pellen, Paul Kaak,
Jan Rab, René Tromp,
Niels Rutten, Teun Houwing, Cobie van der Knaap, Bert Zegeren,
Erik Witte, Pieter Hoogerheide,
Astrid van de Wetering, Mathilde Leclou, Danny Holman, Hans Barendregt,
Marjolein Holman, Anke Aardema, Sjoerd Huitema, Floris van Overmeeren,
Jan Bas, Christian Verbraeken, Mirjam Snijders, Björn Bakker, Yette Brinkman,
Robin van Damme, Rik Eijzinga, Henk Lindeboom, Coen van Heerwaarden,
Mariët van der Werf, Sander de Lugt, Vera Koot, Hans Timmermans,
Frank Oosterhof, Arie Kuip, Jildert Hooijschuur, René Duinker,
Renske Zwezerijn, Elly Hooijschuur, Marianne Heijne, Kees van Dee,
Wijnand Dros, Robert Kuip, Annemieke de Boer, Jan Groeskamp,
Hendrik van der Wal, Klazien Kikkert, Carla Dros, Cor Hoogerheide,
Esther Polderman, Eric Kooiman, Dennis Vonk, Jan Bloem,
Diana Schouwstra, Greetje Boumans-Beijert, Andi Kaercher, Wijnand van Beek,
Marian Eelman, Merel Röpke, Marianne Ris, Marloes Zegers, Jan Kalis,
Joke Krab, Cora de Porto, Sybren Daalder, Theo Stroes,
Edwin van der Vaart, Sander Ekker, Sandra van 't Noordende, Pierre Kooiman,
Jokien Winnubst,
Jan Spelç, Sabien Overbeeke, Ronnie Adema, Anne Hoven,
Miriam Hilhorst, Peter Geurtz, Astrid Cremers, Marlies Dijksen,
Omgevingsdienst Noord-Holland Noord, ODNHN, Veiligheidsregio Noord-Holland Noord,
GGD Hollands Noorden, Regionaal Historisch Centrum Alkmaar, RHCA,
Regionale Raadscommissie Noordkop, RRN, Wvggz,
toeristenbelasting, bestemmingsplan, omgevingsvisie, omgevingsplan,
woningbouwprogramma, klimaatadaptatieplan, energietransitie,
Stappeland, karthal, De Koog, vuurwerkverbod, natuurbeheer,
kustverdediging, dijknormering, waterveiligheid, waddengebied,
schuldenregeling, registratietermijn, sociale zekerheid
"""


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


def get_silence_duration_before(audio_file, threshold_db="-35dB", min_duration=45):
    """Bereken totale verwijderde stilte-duur via ffmpeg stiltedetectie."""
    detect_cmd = [
        "ffmpeg", "-i", audio_file,
        "-af", f"silencedetect=noise={threshold_db}:d={min_duration}",
        "-f", "null", "-"
    ]
    result = subprocess.run(detect_cmd, capture_output=True, text=True)
    starts = re.findall(r"silence_start: ([\d.]+)", result.stderr)
    ends = re.findall(r"silence_end: ([\d.]+)", result.stderr)
    silences = [
        (float(s), float(e))
        for s, e in zip(starts, ends)
        if float(e) - float(s) >= min_duration
    ]
    return silences


def get_speaker_timeline(data):
    """
    Bouw sprekerlijst op met gecorrigeerde tijdstempels.
    Geeft lijst van (start_sec, end_sec, naam) tuples relatief aan actualStart.
    """
    actual_start = parse_royalcast_timestamp(data.get("actualStart"))
    if not actual_start:
        log("Geen actualStart - sprekerherkenning beperkt")
        return []

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
            start_ms = parse_royalcast_timestamp(event.get("start"))
            end_ms = parse_royalcast_timestamp(event.get("end"))
            if not start_ms or not end_ms:
                continue

            start_sec = start_ms - actual_start
            end_sec = end_ms - actual_start

            if start_sec >= 0:
                speakers.append((start_sec, end_sec, volledige_naam))

    speakers.sort(key=lambda x: x[0])
    log(f"{len(speakers)} spreekbeurten voor {len(set(s[2] for s in speakers))} sprekers")
    return speakers


def correct_speaker_times(speakers, intro_sec, silences):
    """
    Corrigeer spreker-tijdstempels voor:
    1. Weggeknipt intro
    2. Verwijderde schorsingen
    """
    corrected = []
    for start, end, naam in speakers:
        # Corrigeer voor intro
        t_start = max(0, start - intro_sec)
        t_end = max(0, end - intro_sec)

        # Corrigeer voor verwijderde schorsingen
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


def transcribe_audio(audio_file):
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
        initial_prompt=TEXEL_VOCABULARY,
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
    lines.append(f"RAADSVERGADERING TEXEL")
    lines.append(f"{date_str}")
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

    # Intro-duur berekenen
    intro_sec = get_intro_duration(data)
    log(f"Intro: {intro_sec:.0f}s")

    # MP3 downloaden
    audio_file = download_mp3(mp3_url, date_id)

    # Stiltes detecteren voor timing-correctie
    log("Stiltes detecteren voor timing-correctie...")
    silences = get_silence_duration_before(audio_file)
    log(f"{len(silences)} schorsingen gevonden")

    # Sprekerdata ophalen en corrigeren
    raw_speakers = get_speaker_timeline(data)
    if raw_speakers:
        speakers = correct_speaker_times(raw_speakers, intro_sec, silences)
        log(f"Spreker-tijden gecorrigeerd voor intro ({intro_sec:.0f}s) en {len(silences)} schorsingen")
    else:
        speakers = []

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
