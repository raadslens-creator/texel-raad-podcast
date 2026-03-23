#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

CHANNEL = "gemeentetexel"
ROYALCAST_BASE = f"https://channel.royalcast.com/texel/#!/{CHANNEL}"
ROYALCAST_LANDING = f"https://channel.royalcast.com/landingpage/texel"
ARCHIVE_URL = f"https://channel.royalcast.com/texel/#!/archived"
SEEN_FILE = Path("docs/seen.json")
FEED_FILE = Path("docs/feed.xml")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
SILENCE_THRESHOLD_DB = "-35dB"
SILENCE_MIN_DURATION = 45

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

def fetch_webcasts():
    """Haal lijst van webcasts op via RoyalCast JSON API."""
    url = f"https://channel.royalcast.com/portal/api/1.0/texel/webcasts/texel?pageSize=10&pageIndex=0&sort=date&order=desc"
    log(f"API proberen: {url}")
    try:
        data = json.loads(fetch_text(url))
        items = data.get("webcasts") or data.get("items") or (data if isinstance(data, list) else [])
        if items:
            return items
    except Exception as e:
        log(f"API-fout: {e}")

    # Fallback: scrape de archiefpagina
    log("Fallback: archiefpagina scrapen...")
    try:
        html = fetch_text("https://channel.royalcast.com/texel/")
        # Zoek IDs in de HTML
        ids = re.findall(rf'{CHANNEL}/(\d{{8}}_\d+)', html)
        ids = list(dict.fromkeys(ids))  # dedupliceren
        log(f"{len(ids)} IDs gevonden in HTML")
        return [{"id": f"{CHANNEL}/{i}", "title": "Raadsvergadering", "date": i[:8]} for i in ids]
    except Exception as e:
        log(f"Scrapen mislukt: {e}")
        return []

def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return []

def save_seen(seen):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2))

def download_audio(webcast_id, title):
    # webcast_id is bijv. "gemeentetexel/20260218_1"
    url = f"https://channel.royalcast.com/landingpage/texel/{webcast_id}/"
    output = f"audio/{webcast_id.replace('/', '_')}_raw.mp3"
    Path("audio").mkdir(exist_ok=True)
    log(f"Downloaden: {url}")
    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "64K", "--output", output,
        "--no-playlist", "--socket-timeout", "60", "--retries", "5",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"yt-dlp fout:\n{result.stderr[-500:]}")
        return None
    if not Path(output).exists():
        log("Bestand niet gevonden")
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
    log(f"{len(silences)} schorsingen, totaal {sum(d for _,_,d in silences):.0f}s")
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
        log(f"Knippen mislukt:\n{result.stderr[-400:]}")
        shutil.copy(input_file, output_file)
        return []
    log(f"Geknipt: {Path(output_file).stat().st_size / 1024 / 1024:.1f} MB")
    return silences

def add_chapters_to_mp3(audio_file, chapters):
    if not chapters:
        return
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, CHAP, TIT2, CTOC, CTOCFlags
    except ImportError:
        log("mutagen niet gevonden")
        return
    log(f"Hoofdstukken toevoegen ({len(chapters)})...")
    audio = MP3(audio_file)
    total_ms = int(audio.info.length * 1000)
    try:
        tags = ID3(audio_file)
    except Exception:
        tags = ID3()
    tags.delall("CHAP")
    tags.delall("CTOC")
    chapter_ids = []
    for i, ch in enumerate(chapters):
        start_ms = ch["start_sec"] * 1000
        end_ms = chapters[i + 1]["start_sec"] * 1000 if i + 1 < len(chapters) else total_ms
        cid = f"chp{i}"
        chapter_ids.append(cid)
        tags.add(CHAP(
            element_id=cid, start_time=start_ms, end_time=end_ms,
            start_offset=0xFFFFFFFF, end_offset=0xFFFFFFFF,
            sub_frames=[TIT2(encoding=3, text=ch["titel"])],
        ))
    tags.add(CTOC(
        element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
        child_element_ids=chapter_ids,
        sub_frames=[TIT2(encoding=3, text="Inhoudsopgave")],
    ))
    tags.save(audio_file)
    log("Hoofdstukken opgeslagen")

def create_github_release(webcast_id, title, date_str, audio_file):
    if not GITHUB_TOKEN or not REPO:
        log("Geen GitHub token/repo")
        return None
    safe_id = webcast_id.replace("/", "-")
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    release_data = json.dumps({
        "tag_name": f"vergadering-{safe_id}",
        "name": title,
        "body": f"Raadsvergadering Texel - {date_str}",
        "draft": False, "prerelease": False,
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases",
        data=release_data, headers=headers, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        release = json.loads(resp.read())
    upload_url = release["upload_url"].replace("{?name,label}", "")
    log(f"Release: {release['html_url']}")
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
  </item>"""
    FEED_FILE.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Gemeenteraad Texel</title>
  <description>Raadsvergaderingen van de gemeente Texel</description>
  <link>https://texel.bestuurlijkeinformatie.nl/Calendar</link>
  <language>nl</language>
  <itunes:author>Gemeente Texel</itunes:author>
  <itunes:category text="Government"/>
  <itunes:explicit>false</itunes:explicit>{items}
</channel>
</rss>""".strip())
    log(f"RSS bijgewerkt: {len(episodes)} afleveringen")

def main():
    log("=== Texel Raadsvergadering Podcast ===")
    subprocess.run(["pip", "install", "mutagen", "-q"], check=False)

    seen = load_seen()
    webcasts = fetch_webcasts()

    if not webcasts:
        log("Geen webcasts gevonden")
        sys.exit(0)

    log(f"{len(webcasts)} webcasts gevonden")
    new_found = False

    for wc in webcasts:
        wc_id = wc.get("id", "")
        title = wc.get("title", "Raadsvergadering")
        date_raw = wc.get("date", "") or wc.get("startDate", "")

        if not wc_id or wc_id in seen:
            continue

        log(f"Nieuwe vergadering: {title} ({wc_id})")
        new_found = True

        # Datum formatteren vanuit YYYYMMDD
        try:
            date_part = wc_id.split("/")[-1][:8]
            dt = datetime.strptime(date_part, "%Y%m%d")
            date_str = dt.strftime("%d %B %Y")
            pub_date = dt.strftime("%a, %d %b %Y 03:00:00 +0000")
        except Exception:
            date_str = "onbekend"
            pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

        full_title = f"Raadsvergadering Texel - {date_str}"

        raw_audio = download_audio(wc_id, full_title)
        if not raw_audio:
            continue

        processed = f"audio/{wc_id.replace('/', '_')}.mp3"
        silences = remove_silences(raw_audio, processed)

        try:
            from mutagen.mp3 import MP3
            secs = int(MP3(processed).info.length)
            duration_str = f"{secs//3600}:{(secs%3600)//60:02d}:{secs%60:02d}"
        except Exception:
            duration_str = ""

        audio_url = create_github_release(wc_id, full_title, date_str, processed)
        if not audio_url:
            continue

        episodes = load_episodes()
        episodes.insert(0, {
            "id": wc_id, "title": full_title,
            "description": f"Raadsvergadering gemeente Texel, {date_str}.",
            "audio_url": audio_url, "pub_date": pub_date,
            "size": Path(processed).stat().st_size,
            "duration": duration_str,
        })
        update_rss_feed(episodes)
        seen.append(wc_id)
        save_seen(seen)
        log(f"Klaar: {full_title} ({duration_str})")

    if not new_found:
        log("Geen nieuwe vergaderingen")

if __name__ == "__main__":
    main()
