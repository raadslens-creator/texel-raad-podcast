#!/usr/bin/env python3
"""
Raadslens - Fetch Vergadering
Haalt nieuwe raadsvergaderingen op voor alle geconfigureerde gemeenten.
Configuratie via gemeenten.json.
"""
import json, os, re, shutil, subprocess, sys, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
SILENCE_THRESHOLD_DB = "-35dB"
SILENCE_MIN_DURATION = 90

MAANDEN = {
    1: "januari", 2: "februari", 3: "maart", 4: "april",
    5: "mei", 6: "juni", 7: "juli", 8: "augustus",
    9: "september", 10: "oktober", 11: "november", 12: "december"
}

VERANTWOORDING_TEMPLATE = """---
Raadslens maakt lokale democratie toegankelijk. We zetten raadsvergaderingen automatisch om naar een podcast.

De verwerking gebeurt volledig automatisch. Wij zijn niet verantwoordelijk voor de inhoud van de vergadering zelf. De originele uitzending vind je op {ibabs_link}.

Raadslens is een onafhankelijk initiatief, zonder politieke kleur en zonder commercieel belang.

Vragen, feedback of een fout gevonden? Mail naar raadslens@gmail.com"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


GEMEENTEN_CONFIG = {}

def laad_gemeenten():
    global GEMEENTEN_CONFIG
    config_file = Path("gemeenten.json")
    if not config_file.exists():
        log("FOUT: gemeenten.json niet gevonden")
        sys.exit(1)
    GEMEENTEN_CONFIG = json.loads(config_file.read_text())
    return [g for g in GEMEENTEN_CONFIG["gemeenten"] if g.get("actief", True)]


def parse_royalcast_timestamp(ts_str):
    if not ts_str:
        return None
    match = re.search(r"\d+", ts_str)
    if match:
        return int(match.group()) / 1000
    return None


def get_candidate_ids(gemeente, handmatige_ids=None):
    """Genereer kandidaat-IDs voor de opgegeven periode."""
    if handmatige_ids:
        return handmatige_ids
    ids = []
    today = datetime.now(timezone.utc)
    check_days = gemeente.get("check_days", 14)
    vanaf_datum = gemeente.get("vanaf_datum", "")
    for days_ago in range(0, check_days):
        date = today - timedelta(days=days_ago)
        date_str = date.strftime("%Y%m%d")
        if vanaf_datum and date_str < vanaf_datum:
            continue
        for n in [1, 2, 3, 4]:
            ids.append(f"{date_str}_{n}")
    return ids


def check_and_fetch_webcast(gemeente, date_id):
    slug = gemeente["royalcast_slug"]
    url = f"https://channel.royalcast.com/portal/api/1.0/{slug}/webcasts/{slug}/{date_id}?method=GET&key="
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            if data.get("id") or data.get("webcastId") or data.get("webcstId"):
                return data
            return None
    except urllib.error.HTTPError as e:
        if e.code not in [404, 403]:
            log(f"HTTP {e.code}: {date_id}")
        return None
    except Exception as e:
        log(f"Fout bij {date_id}: {e}")
        return None


def get_intro_duration(data):
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
    intro_sec = max(0, earliest_event - actual_start)
    if intro_sec > 120:
        log(f"Intro van {intro_sec:.0f}s te lang - op 0 gezet")
        return 0
    log(f"Intro: {intro_sec:.0f}s")
    return intro_sec


def get_chapter_times(data, actual_start_sec):
    chapters = []
    for topic in data.get("topics", []):
        titel = topic.get("title", "").strip()
        if not titel:
            continue
        events = topic.get("events", [])
        if events:
            event_start = parse_royalcast_timestamp(events[0].get("start"))
            start_sec = max(0, event_start - actual_start_sec) if event_start and actual_start_sec else 0
        else:
            start_sec = chapters[-1]["start_sec"] + 60 if chapters else 0
        chapters.append({"titel": titel[:80], "start_sec": start_sec})
    log(f"{len(chapters)} hoofdstukken gevonden")
    return chapters


def load_seen(gemeente):
    seen_file = Path(gemeente["seen_file"])
    if seen_file.exists():
        return json.loads(seen_file.read_text())
    return []


def save_seen(gemeente, seen):
    seen_file = Path(gemeente["seen_file"])
    seen_file.parent.mkdir(parents=True, exist_ok=True)
    seen_file.write_text(json.dumps(seen, indent=2))


def download_audio(date_id, data, gemeente_id):
    mp3_url = None
    mp4_url = None
    for att in data.get("attachments", []):
        ct = att.get("contentType", "")
        loc = att.get("location", "")
        if "audio/mpeg" in ct or loc.endswith(".mp3"):
            mp3_url = loc
            break
        if "video/mp4" in ct or loc.endswith(".mp4"):
            mp4_url = loc

    download_url = mp3_url or mp4_url
    if not download_url:
        log("Geen MP3/MP4 gevonden - vergadering mogelijk nog niet verwerkt")
        return None

    audio_dir = Path(f"audio/{gemeente_id}")
    audio_dir.mkdir(parents=True, exist_ok=True)
    output = str(audio_dir / f"{date_id}_raw.mp3")
    log(f"MP3 downloaden...")

    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "64K", "--output", output,
        "--no-playlist", download_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                with open(output, "wb") as f:
                    f.write(resp.read())
        except Exception as e:
            log(f"Download fout: {e}")
            return None

    if not Path(output).exists():
        log("Bestand niet gevonden na download")
        return None
    log(f"Download OK ({Path(output).stat().st_size / 1024 / 1024:.1f} MB)")
    return output


def trim_intro(input_file, output_file, intro_sec):
    if intro_sec <= 0:
        shutil.copy(input_file, output_file)
        return
    log(f"Intro wegknippen: eerste {intro_sec:.0f}s...")
    cmd = ["ffmpeg", "-y", "-i", input_file, "-ss", str(intro_sec), "-acodec", "copy", output_file]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        shutil.copy(input_file, output_file)


def remove_silences(input_file, output_file):
    log("Stiltedetectie...")
    detect_cmd = [
        "ffmpeg", "-i", input_file,
        "-af", f"silencedetect=noise={SILENCE_THRESHOLD_DB}:d={SILENCE_MIN_DURATION}",
        "-f", "null", "-"
    ]
    result = subprocess.run(detect_cmd, capture_output=True, text=True)
    starts = re.findall(r"silence_start: ([\d.]+)", result.stderr)
    ends = re.findall(r"silence_end: ([\d.]+)", result.stderr)
    silences = [(float(s), float(e)) for s, e in zip(starts, ends) if float(e) - float(s) >= SILENCE_MIN_DURATION]
    log(f"{len(silences)} schorsingen gevonden")

    # Schorsingen in de eerste 2 minuten negeren - dat is altijd intro, nooit echte schorsing
    MIN_START_SEC = 120.0
    silences = [(s, e) for s, e in silences if s >= MIN_START_SEC]
    log(f"{len(silences)} schorsingen na intro-filter")

    if not silences:
        shutil.copy(input_file, output_file)
        return silences

    parts = []
    prev_end = 0.0
    for s_start, s_end in silences:
        if s_start > prev_end:
            parts.append(f"between(t,{prev_end:.3f},{s_start:.3f})")
        prev_end = s_end
    parts.append(f"gte(t,{prev_end:.3f})")

    filter_expr = "+".join(parts)
    cmd = [
        "ffmpeg", "-y", "-i", input_file,
        "-af", f"aselect='{filter_expr}',asetpts=N/SR/TB",
        "-acodec", "libmp3lame", "-q:a", "4", output_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log("Stilte-verwijdering mislukt - origineel gebruiken")
        shutil.copy(input_file, output_file)
    return silences


def correct_chapter_times(chapters, intro_sec, silences):
    corrected = []
    for ch in chapters:
        t = max(0, ch["start_sec"] - intro_sec)
        removed = sum(
            min(s_end, t) - s_start
            for s_start, s_end in silences if s_start < t
        )
        corrected.append({"titel": ch["titel"], "start_sec": max(0, t - removed)})
    return corrected


def add_chapters_to_mp3(audio_file, chapters):
    try:
        import mutagen.id3
        from mutagen.mp3 import MP3
        tags = mutagen.id3.ID3(audio_file)
        tags.delall("CHAP")
        tags.delall("CTOC")
        child_ids = []
        for i, ch in enumerate(chapters):
            chap_id = f"chp{i}"
            child_ids.append(chap_id)
            start_ms = int(ch["start_sec"] * 1000)
            end_ms = int(chapters[i+1]["start_sec"] * 1000) if i+1 < len(chapters) else int(MP3(audio_file).info.length * 1000)
            tags.add(mutagen.id3.CHAP(
                element_id=chap_id, start_time=start_ms, end_time=end_ms,
                start_offset=0xFFFFFFFF, end_offset=0xFFFFFFFF,
                sub_frames=[mutagen.id3.TIT2(text=[ch["titel"]])]
            ))
        tags.add(mutagen.id3.CTOC(
            element_id="toc", flags=mutagen.id3.CTOCFlags.TOP_LEVEL | mutagen.id3.CTOCFlags.ORDERED,
            child_element_ids=child_ids, sub_frames=[]
        ))
        tags.save(audio_file)
        log(f"{len(chapters)} hoofdstukken ingebouwd")
    except Exception as e:
        log(f"Hoofdstukken toevoegen mislukt: {e}")


def build_shownotes(data, date_str, chapters, gemeente):
    topics = data.get("topics", [])
    regels = [f"Vergadering gemeente {gemeente['naam']} - {date_str}"]

    if topics:
        regels.append("\nAgenda:")
        for t in topics:
            titel = t.get("title", "").strip()
            if titel:
                regels.append(f"• {titel}")

    if chapters:
        regels.append("\nHoofdstukken:")
        for ch in chapters:
            h = int(ch["start_sec"] // 3600)
            m = int((ch["start_sec"] % 3600) // 60)
            s = int(ch["start_sec"] % 60)
            ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            regels.append(f"• {ts} {ch['titel']}")

    verantwoording = VERANTWOORDING_TEMPLATE.format(ibabs_link=gemeente["ibabs_link"])
    regels.append(f"\n{verantwoording}")
    return "\n".join(regels)


def upload_to_r2(date_id, audio_file, gemeente):
    """Upload MP3 naar Cloudflare R2 en geef de publieke URL terug."""
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    account_id = os.environ.get("R2_ACCOUNT_ID", "")
    bucket = "raadslens-audio"
    gemeente_id = gemeente["id"]
    r2_public_url = gemeente.get("r2_public_url", "https://pub-adbd8382dc214647bb3e307524dd94d6.r2.dev")

    if not access_key or not secret_key or not account_id:
        log("Geen R2 credentials gevonden (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID)")
        return None

    object_key = f"{gemeente_id}/{date_id}.mp3"
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    subprocess.run(["pip", "install", "boto3", "-q"], capture_output=True, check=False)
    import boto3
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

    try:
        with open(audio_file, "rb") as f:
            s3.upload_fileobj(f, bucket, object_key,
                ExtraArgs={"ContentType": "audio/mpeg"})
        public_url = f"{r2_public_url}/{object_key}"
        log(f"MP3 geüpload naar R2: {public_url}")
        return public_url
    except Exception as e:
        log(f"R2 upload mislukt: {e}")
        return None


def load_episodes(gemeente):
    feed_file = Path(gemeente["feed_file"])
    episodes = []
    if not feed_file.exists():
        return episodes
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(feed_file))
        root = tree.getroot()
        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            guid = item.findtext("guid", "")
            pub = item.findtext("pubDate", "")
            link = item.findtext("link", "")
            dur = item.findtext("itunes:duration", default="", namespaces=ns)
            desc = item.findtext("description", "")
            enc = item.find("enclosure")
            if not title or not guid or enc is None:
                continue
            audio_url = enc.get("url", "")
            size = int(enc.get("length", 0))
            if not audio_url:
                continue
            episodes.append({
                "title": title,
                "id": guid,
                "audio_url": audio_url,
                "pub_date": pub,
                "description": desc,
                "link": link,
                "duration": dur,
                "size": size,
            })
    except Exception as e:
        log(f"Feed inlezen mislukt: {e}")
    # Dedupliceer op guid
    seen_guids = set()
    unique = []
    for ep in episodes:
        if ep["id"] not in seen_guids:
            seen_guids.add(ep["id"])
            unique.append(ep)
    if len(unique) < len(episodes):
        log(f"  {len(episodes) - len(unique)} dubbele episode(s) verwijderd uit feed")
    return unique


def update_rss_feed(episodes, gemeente):
    feed_file = Path(gemeente["feed_file"])
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    logo_url = gemeente["logo_url"]
    ibabs_link = gemeente["ibabs_link"]
    podcast_titel = gemeente["podcast_titel"]
    beschrijving = gemeente["podcast_beschrijving"]

    items = ""
    for ep in episodes:
        episode_link = ep.get('link', ibabs_link)
        episode_link = ep.get('link', ibabs_link)
        items += f"""
  <item>
    <title>{ep['title']}</title>
    <link>{episode_link}</link>
    <description><![CDATA[{ep.get('description', ep['title'])}]]></description>
    <pubDate>{ep.get('pub_date', '')}</pubDate>
    <enclosure url="{ep['audio_url']}" type="audio/mpeg" length="{ep.get('size', 0)}"/>
    <guid isPermaLink="false">{ep['id']}</guid>
    <itunes:duration>{ep.get('duration', '')}</itunes:duration>
    <itunes:image href="{logo_url}"/>
  </item>"""

    feed_file.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>{podcast_titel}</title>
  <description>{beschrijving}</description>
  <link>{ibabs_link}</link>
  <language>nl</language>
  <itunes:author>Raadslens</itunes:author>
  <itunes:summary>{beschrijving}</itunes:summary>
  <itunes:image href="{logo_url}"/>
  <image>
    <url>{logo_url}</url>
    <title>{podcast_titel}</title>
    <link>{ibabs_link}</link>
  </image>
  <itunes:category text="Government"/>
  <itunes:explicit>false</itunes:explicit>{items}
</channel>
</rss>""".strip())
    log(f"RSS bijgewerkt: {len(episodes)} afleveringen")


def verwerk_gemeente(gemeente, handmatige_ids=None):
    log(f"\n=== {gemeente['naam']} ===")
    subprocess.run(["pip", "install", "mutagen", "-q"], check=False)

    seen = load_seen(gemeente)
    candidates = get_candidate_ids(gemeente, handmatige_ids)
    log(f"{len(candidates)} kandidaat-IDs")

    new_found = False
    for date_id in candidates:
        if date_id in seen:
            continue

        data = check_and_fetch_webcast(gemeente, date_id)
        if not data:
            continue

        # Filter op vergaderingstype (gemeente-specifiek, anders global default)
        vergadering_typen = gemeente.get("vergadering_typen") or GEMEENTEN_CONFIG.get("default_vergadering_typen", [])
        if vergadering_typen:
            titel = data.get("title", "")
            if not any(vtype.lower() in titel.lower() for vtype in vergadering_typen):
                log(f"  Overgeslagen: '{titel}' niet in vergadering_typen")
                continue

        # Filter op minimale starttijd (bijv. 18:30 voor avondvergaderingen)
        min_starttijd_uur = gemeente.get("min_starttijd_uur", 0)
        if min_starttijd_uur > 0:
            actual_start_ms = parse_royalcast_timestamp(data.get("actualStart"))
            if actual_start_ms:
                start_dt = datetime.fromtimestamp(actual_start_ms, tz=timezone.utc)
                if start_dt.hour < min_starttijd_uur:
                    log(f"  Overgeslagen: vergadering start om {start_dt.hour:02d}:{start_dt.minute:02d} UTC (voor {min_starttijd_uur}:00)")
                    seen.append(date_id)
                    save_seen(gemeente, seen)
                    continue

        new_found = True
        try:
            dt = datetime.strptime(date_id[:8], "%Y%m%d")
            date_str = f"{dt.day} {MAANDEN[dt.month]} {dt.year}"
            pub_date = dt.strftime("%a, %d %b %Y 03:00:00 +0000")
        except Exception:
            date_str = "onbekend"
            pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            dt = datetime.now(timezone.utc)

        vergadering_type = data.get("title", "Vergadering")
        full_title = f"{vergadering_type} {dt.day:02d}-{dt.month:02d}-{dt.year}"
        log(f"Verwerken: {full_title}")

        intro_sec = get_intro_duration(data)
        actual_start_sec = parse_royalcast_timestamp(data.get("actualStart"))
        chapters = get_chapter_times(data, actual_start_sec)

        raw_audio = download_audio(date_id, data, gemeente["id"])
        if not raw_audio:
            log(f"  Audio nog niet beschikbaar - volgende run opnieuw proberen")
            continue

        audio_dir = Path(f"audio/{gemeente['id']}")
        trimmed_audio = str(audio_dir / f"{date_id}_trimmed.mp3")
        trim_intro(raw_audio, trimmed_audio, intro_sec)

        processed = str(audio_dir / f"{date_id}.mp3")
        silences = remove_silences(trimmed_audio, processed)

        if chapters:
            chapters = correct_chapter_times(chapters, intro_sec, silences)
            add_chapters_to_mp3(processed, chapters)

        try:
            from mutagen.mp3 import MP3
            secs = int(MP3(processed).info.length)
            duration_str = f"{secs//3600}:{(secs%3600)//60:02d}:{secs%60:02d}"
        except Exception:
            secs = 0
            duration_str = ""

        # Minimale duur: vergaderingen korter dan 15 minuten overslaan
        min_duur = gemeente.get("min_duur_sec", 15 * 60)
        if secs > 0 and secs < min_duur:
            log(f"  Vergadering te kort ({secs//60} min) - overgeslagen")
            seen.append(date_id)
            save_seen(gemeente, seen)
            continue

        audio_url = upload_to_r2(date_id, processed, gemeente)
        if not audio_url:
            continue

        description = build_shownotes(data, date_str, chapters, gemeente)

        episodes = load_episodes(gemeente)
        episodes.insert(0, {
            "id": f"{gemeente['id']}-{date_id}",
            "title": full_title,
            "description": description,
            "audio_url": audio_url,
            "pub_date": pub_date,
            "size": Path(processed).stat().st_size,
            "duration": duration_str,
        })
        update_rss_feed(episodes, gemeente)
        seen.append(date_id)
        save_seen(gemeente, seen)
        log(f"Klaar: {full_title} ({duration_str})")

    if not new_found:
        log("Geen nieuwe vergaderingen")


def main():
    log("=== Raadslens - Fetch Vergadering ===")

    # Optionele gemeente-filter via argument
    gemeente_filter = sys.argv[1] if len(sys.argv) > 1 else None
    handmatige_ids = sys.argv[2].split(",") if len(sys.argv) > 2 else None

    gemeenten = laad_gemeenten()
    if gemeente_filter:
        gemeenten = [g for g in gemeenten if g["id"] == gemeente_filter]
        if not gemeenten:
            log(f"Gemeente '{gemeente_filter}' niet gevonden in gemeenten.json")
            sys.exit(1)

    for gemeente in gemeenten:
        verwerk_gemeente(gemeente, handmatige_ids)

    log("\n=== Klaar ===")


if __name__ == "__main__":
    main()
