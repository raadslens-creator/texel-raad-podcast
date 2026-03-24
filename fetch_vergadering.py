#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROYALCAST_API = "https://channel.royalcast.com/portal/api/1.0/gemeentetexel/webcasts/gemeentetexel"
SEEN_FILE = Path("docs/seen.json")
FEED_FILE = Path("docs/feed.xml")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
SILENCE_THRESHOLD_DB = "-35dB"
SILENCE_MIN_DURATION = 45
LOGO_URL = "https://raadslens-creator.github.io/texel-raad-podcast/logo.png"

MAANDEN = {
    1: "januari", 2: "februari", 3: "maart", 4: "april",
    5: "mei", 6: "juni", 7: "juli", 8: "augustus",
    9: "september", 10: "oktober", 11: "november", 12: "december"
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_recent_webcast_ids():
    """Genereer mogelijke webcast IDs voor de afgelopen 14 dagen."""
    ids = []
    today = datetime.now(timezone.utc)
    for days_ago in range(0, 14):
        date = today - timedelta(days=days_ago)
        date_str = date.strftime("%Y%m%d")
        for n in [1, 2, 3]:
            ids.append(f"{date_str}_{n}")
    return ids


def check_and_fetch_webcast(date_id):
    """
    Controleer of een webcast beschikbaar is via de API.
    Geeft de volledige API-response terug als die beschikbaar is, anders None.
    """
    url = f"{ROYALCAST_API}/{date_id}?method=GET&key="
    log(f"Controleren: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            if data.get("id") or data.get("webcastId") or data.get("webcstId"):
                log(f"Gevonden!")
                return data
            log(f"Leeg antwoord")
            return None
    except urllib.error.HTTPError as e:
        log(f"Niet beschikbaar: {e.code}")
        return None
    except Exception as e:
        log(f"Fout: {e}")
        return None


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return []


def save_seen(seen):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


def download_audio(date_id, data):
    """Download audio via directe MP3 of MP4 link uit de API."""
    mp3_url = None
    mp4_url = None
    for att in data.get("attachments", []):
        ct = att.get("contentType", "")
        loc = att.get("location", "")
        if "audio/mpeg" in ct or loc.endswith(".mp3"):
            mp3_url = loc
            log(f"MP3 gevonden")
            break
        if "video/mp4" in ct or loc.endswith(".mp4"):
            mp4_url = loc

    download_url = mp3_url or mp4_url
    if not download_url:
        log("Geen MP3 of MP4 gevonden - vergadering mogelijk nog niet verwerkt")
        return None

    output = f"audio/{date_id}_raw.mp3"
    Path("audio").mkdir(exist_ok=True)
    log(f"Downloaden...")

    # Probeer eerst yt-dlp
    cmd = [
        "yt-dlp",
        "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "64K",
        "--output", output,
        "--no-playlist",
        download_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"yt-dlp fout - directe download proberen...")
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
    silences = [
        (float(s), float(e), float(e) - float(s))
        for s, e in zip(starts, ends)
        if float(e) - float(s) >= SILENCE_MIN_DURATION
    ]
    if not silences:
        log("Geen schorsingen gevonden")
        shutil.copy(input_file, output_file)
        return []
    log(f"{len(silences)} schorsingen, totaal {sum(d for _,_,d in silences):.0f}s verwijderd")
    KEEP_PAUSE = 1.0
    segments = []
    prev_end = 0.0
    for silence_start, silence_end, _ in silences:
        seg_end = silence_start + KEEP_PAUSE
        if seg_end > prev_end:
            segments.append((prev_end, seg_end))
        prev_end = silence_end - KEEP_PAUSE
    segments.append((prev_end, None))
    filter_parts = []
    for i, (start, end) in enumerate(segments):
        if end is not None:
            filter_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]")
        else:
            filter_parts.append(f"[0:a]atrim=start={start},asetpts=PTS-STARTPTS[a{i}]")
    concat_inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(segments)}:v=0:a=1[outa]"
    cut_cmd = [
        "ffmpeg", "-y", "-i", input_file,
        "-filter_complex", filter_complex,
        "-map", "[outa]", "-codec:a", "libmp3lame", "-q:a", "4",
        output_file,
    ]
    log("Audio knippen...")
    result = subprocess.run(cut_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Knippen mislukt - origineel gebruiken")
        shutil.copy(input_file, output_file)
        return []
    log(f"Geknipt: {Path(output_file).stat().st_size / 1024 / 1024:.1f} MB")
    return silences


def build_shownotes(data, date_str):
    """Bouw shownotes op uit agendapunten."""
    topics = data.get("topics", [])
    if not topics:
        return f"Vergadering gemeente Texel, {date_str}."
    regels = [f"Vergadering gemeente Texel\n{date_str}\n\nAgenda:"]
    for t in topics:
        titel = t.get("title", "").strip()
        if titel:
            regels.append(f"• {titel}")
    return "\n".join(regels)


def create_github_release(date_id, title, date_str, audio_file):
    if not GITHUB_TOKEN or not REPO:
        log("Geen GitHub token/repo")
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    release_data = json.dumps({
        "tag_name": f"vergadering-{date_id}",
        "name": title,
        "body": f"Vergadering Texel - {date_str}",
        "draft": False, "prerelease": False,
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases",
        data=release_data, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            release = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 422:
            log("Release bestaat al - bestaande release ophalen...")
            req2 = urllib.request.Request(
                f"https://api.github.com/repos/{REPO}/releases/tags/vergadering-{date_id}",
                headers={
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github+json",
                }
            )
            with urllib.request.urlopen(req2) as resp:
                release = json.loads(resp.read())
        else:
            raise
    upload_url = release["upload_url"].replace("{?name,label}", "")
    log(f"Release aangemaakt/gevonden: {release['html_url']}")
    upload_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "audio/mpeg",
    }
    with open(audio_file, "rb") as f:
        audio_data = f.read()
    req = urllib.request.Request(
        f"{upload_url}?name={Path(audio_file).name}",
        data=audio_data, headers=upload_headers, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        asset = json.loads(resp.read())
    log(f"MP3 geüpload: {asset['browser_download_url']}")
    return asset["browser_download_url"]


def load_episodes():
    episodes = []
    if not FEED_FILE.exists():
        return episodes
    content = FEED_FILE.read_text()
    for item in re.findall(r"<item>(.*?)</item>", content, re.DOTALL):
        title = re.search(r"<title>(.*?)</title>", item)
        guid = re.search(r"<guid[^>]*>(.*?)</guid>", item)
        enc = re.search(r'<enclosure url="([^"]+)"[^/]*/>', item)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item)
        if title and guid and enc:
            episodes.append({
                "title": title.group(1), "id": guid.group(1),
                "audio_url": enc.group(1),
                "pub_date": pub.group(1) if pub else "",
            })
    return episodes


def update_rss_feed(episodes):
    FEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    items = ""
    for ep in episodes:
        items += f"""
  <item>
    <title>{ep['title']}</title>
    <description><![CDATA[{ep.get('description', ep['title'])}]]></description>
    <pubDate>{ep.get('pub_date', '')}</pubDate>
    <enclosure url="{ep['audio_url']}" type="audio/mpeg" length="{ep.get('size', 0)}"/>
    <guid isPermaLink="false">{ep['id']}</guid>
    <itunes:duration>{ep.get('duration', '')}</itunes:duration>
    <itunes:image href="{LOGO_URL}"/>
  </item>"""
    FEED_FILE.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Raadslens Texel</title>
  <description>Vergaderingen van de gemeente Texel - automatisch als podcast</description>
  <link>https://texel.bestuurlijkeinformatie.nl/Calendar</link>
  <language>nl</language>
  <itunes:author>Raadslens</itunes:author>
  <itunes:image href="{LOGO_URL}"/>
  <image>
    <url>{LOGO_URL}</url>
    <title>Raadslens Texel</title>
    <link>https://texel.bestuurlijkeinformatie.nl/Calendar</link>
  </image>
  <itunes:category text="Government"/>
  <itunes:explicit>false</itunes:explicit>{items}
</channel>
</rss>""".strip())
    log(f"RSS bijgewerkt: {len(episodes)} afleveringen")


def main():
    log("=== Raadslens Texel ===")
    subprocess.run(["pip", "install", "mutagen", "-q"], check=False)

    seen = load_seen()
    candidates = get_recent_webcast_ids()
    log(f"{len(candidates)} kandidaat-IDs om te controleren")

    new_found = False
    for date_id in candidates:
        if date_id in seen:
            continue

        data = check_and_fetch_webcast(date_id)
        if not data:
            continue

        new_found = True

        # Nederlandse datum
        try:
            dt = datetime.strptime(date_id[:8], "%Y%m%d")
            date_str = f"{dt.day} {MAANDEN[dt.month]} {dt.year}"
            pub_date = dt.strftime("%a, %d %b %Y 03:00:00 +0000")
        except Exception:
            date_str = "onbekend"
            pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            dt = datetime.now(timezone.utc)

        # Titel: type + datum in formaat DD-MM-YYYY
        vergadering_type = data.get("title", "Vergadering")
        full_title = f"{vergadering_type} {dt.day:02d}-{dt.month:02d}-{dt.year}"

        log(f"Verwerken: {full_title}")

        # Shownotes
        description = build_shownotes(data, date_str)
        log(f"Shownotes: {len(data.get('topics', []))} agendapunten")

        # Download
        raw_audio = download_audio(date_id, data)
        if not raw_audio:
            seen.append(date_id)
            save_seen(seen)
            continue

        # Stiltes verwijderen
        processed = f"audio/{date_id}.mp3"
        remove_silences(raw_audio, processed)

        # Duur bepalen
        try:
            from mutagen.mp3 import MP3
            secs = int(MP3(processed).info.length)
            duration_str = f"{secs//3600}:{(secs%3600)//60:02d}:{secs%60:02d}"
        except Exception:
            duration_str = ""

        # GitHub Release
        audio_url = create_github_release(date_id, full_title, date_str, processed)
        if not audio_url:
            continue

        # RSS bijwerken
        episodes = load_episodes()
        episodes.insert(0, {
            "id": date_id,
            "title": full_title,
            "description": description,
            "audio_url": audio_url,
            "pub_date": pub_date,
            "size": Path(processed).stat().st_size,
            "duration": duration_str,
        })
        update_rss_feed(episodes)
        seen.append(date_id)
        save_seen(seen)
        log(f"Klaar: {full_title} ({duration_str})")

    if not new_found:
        log("Geen nieuwe vergaderingen gevonden")


if __name__ == "__main__":
    main()
