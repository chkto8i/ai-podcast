#!/usr/bin/env python3
"""
AI Podcast Digest — Hauptskript
Ablauf: RSS → Download → Transkription → Zusammenfassung → TTS → Mix → MP3 → RSS-Feed
"""

import os
import sys
import yaml
import json
import email.utils
import feedparser
import subprocess
import requests
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

# ── Konfiguration ────────────────────────────────────────────────────────────
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_PAGES_URL  = os.environ.get("GITHUB_PAGES_URL", "")    # z.B. "https://username.github.io/ai-podcast-digest"

BASE_DIR    = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config" / "feeds.yaml"
OUTPUT_DIR  = BASE_DIR / "output"
DOCS_DIR    = BASE_DIR / "docs"
WORK_DIR    = Path(tempfile.mkdtemp(prefix="podcast_digest_"))

anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Config laden ─────────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

# ── RSS: Neueste Episode je Feed holen ───────────────────────────────────────
def get_latest_episode(feed_config: dict) -> dict | None:
    print(f"  📡 Lade Feed: {feed_config['name']}")
    feed = feedparser.parse(feed_config["rss_url"])
    if not feed.entries:
        print(f"  ⚠️  Keine Einträge in Feed: {feed_config['name']}")
        return None
    entry = feed.entries[0]
    audio_url = None
    for link in entry.get("links", []):
        if link.get("type", "").startswith("audio"):
            audio_url = link["href"]
            break
    if not audio_url and entry.get("enclosures"):
        audio_url = entry["enclosures"][0].get("url")
    if not audio_url:
        print(f"  ⚠️  Kein Audio-Link gefunden: {feed_config['name']}")
        return None
    return {
        "show_name": feed_config["name"],
        "title": entry.get("title", "Unbekannter Titel"),
        "audio_url": audio_url,
        "published": entry.get("published", ""),
    }

# ── Audio herunterladen (yt-dlp) ─────────────────────────────────────────────
def download_audio(episode: dict, dest_dir: Path) -> Path | None:
    safe_name = episode["show_name"].replace(" ", "_").replace("/", "-")
    out_path = dest_dir / f"{safe_name}.mp3"
    print(f"  ⬇️  Lade Audio: {episode['title'][:60]}")
    result = subprocess.run([
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--max-filesize", "100m",
        "-o", str(out_path.with_suffix("")),
        episode["audio_url"]
    ], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"  🔄 yt-dlp fehlgeschlagen, versuche direkten Download...")
        r = requests.get(episode["audio_url"], stream=True, timeout=120)
        if r.status_code == 200:
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return out_path
        print(f"  ❌ Download fehlgeschlagen: {episode['show_name']}")
        return None
    actual = out_path.parent / (out_path.stem + ".mp3")
    return actual if actual.exists() else None

# ── Audio kürzen: max. 25 Minuten ────────────────────────────────────────────
def trim_audio(audio_path: Path, max_seconds: int = 1500) -> Path:
    trimmed = audio_path.parent / f"trimmed_{audio_path.name}"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_path),
        "-t", str(max_seconds),
        "-acodec", "copy",
        str(trimmed)
    ], capture_output=True)
    return trimmed if trimmed.exists() else audio_path

# ── Audio komprimieren (verhindert 413-Fehler bei Whisper API) ───────────────
def compress_audio(audio_path: Path) -> Path:
    compressed = audio_path.parent / f"compressed_{audio_path.name}"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_path),
        "-acodec", "libmp3lame",
        "-b:a", "64k",
        "-ar", "22050",
        str(compressed)
    ], capture_output=True)
    return compressed if compressed.exists() else audio_path

# ── Transkription via Whisper API ─────────────────────────────────────────────
def transcribe(audio_path: Path) -> str:
    print(f"  🎙️  Transkribiere...")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    with open(audio_path, "rb") as f:
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            files={"file": (audio_path.name, f, "audio/mpeg")},
            data={"model": "whisper-1", "language": "de", "response_format": "text"},
            timeout=300
        )
    r.raise_for_status()
    return r.text

# ── Zusammenfassung via Claude ────────────────────────────────────────────────
def summarize(transcript: str, show_name: str, episode_title: str, config: dict) -> str:
    print(f"  🧠  Erstelle Zusammenfassung...")
    lang = config["output"].get("language", "de")
    max_len = config["output"].get("max_summary_length", 2500)
    lang_instruction = "auf Deutsch" if lang == "de" else "in English"

    prompt = f"""Du bist Moderator eines wöchentlichen KI-Podcast-Digests.
Fasse diese Podcast-Folge {lang_instruction} zusammen — detailliert, faktenreich, für unterwegs.

Show: {show_name}
Folge: {episode_title}

Transkript:
{transcript[:25000]}

Liefere NUR folgendes, ohne Überschriften oder Markdown:
1. Einen Einleitungssatz (Show + Thema, max. 20 Wörter)
2. Fünf bis sieben Key-Insights mit ausschließlich konkreten Fakten: Zahlen, \
Jahreszahlen, Firmennamen, Studienergebnisse, direkte Aussagen der Sprecher, \
Prozentwerte, Produktnamen. Jeder Satz muss einen neuen Fakt enthalten. \
Keine allgemeinen Aussagen. Max. {max_len} Zeichen gesamt.
3. Einen abschließenden Satz als Überleitung zur nächsten Show

Schreibe natürlich gesprochen — es wird vorgelesen."""

    message = anthropic.messages.create(
        model="claude-haiku-4-5",
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()

# ── TTS via OpenAI ────────────────────────────────────────────────────────────
def text_to_speech(text: str, out_path: Path, voice: str = "onyx") -> Path:
    print(f"  🔊  Text zu Sprache...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"model": "tts-1", "input": text, "voice": voice}
    r = requests.post("https://api.openai.com/v1/audio/speech",
                      headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path

# ── Stille erzeugen (Pause zwischen Shows) ───────────────────────────────────
def create_silence(duration_ms: int, out_path: Path) -> Path:
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-t", str(duration_ms / 1000),
        "-acodec", "libmp3lame", str(out_path)
    ], capture_output=True)
    return out_path

# ── Alle Audio-Segmente zusammenführen ───────────────────────────────────────
def mix_episodes(segment_paths: list[Path], output_path: Path) -> Path:
    print(f"\n🎚️  Mixe {len(segment_paths)} Segmente...")
    list_file = WORK_DIR / "concat_list.txt"
    with open(list_file, "w") as f:
        for p in segment_paths:
            f.write(f"file '{p.resolve()}'\n")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-acodec", "libmp3lame", "-q:a", "4",
        str(output_path)
    ], capture_output=True)
    return output_path

# ── Audio-Dauer ermitteln ─────────────────────────────────────────────────────
def get_duration(mp3_path: Path) -> int:
    result = subprocess.run([
        "ffprobe", "-v", "quiet", "-show_entries",
        "format=duration", "-of", "csv=p=0", str(mp3_path)
    ], capture_output=True, text=True)
    try:
        return int(float(result.stdout.strip()))
    except Exception:
        return 0

# ── MP3 in docs/episodes ablegen (GitHub Pages) ──────────────────────────────
def copy_to_docs(mp3_path: Path) -> Path:
    episodes_dir = DOCS_DIR / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    dest = episodes_dir / mp3_path.name
    shutil.copy2(mp3_path, dest)
    print(f"  ✅ MP3 nach docs/episodes kopiert")
    return dest

# ── RSS-Feed aktualisieren ────────────────────────────────────────────────────
def update_rss_feed(mp3_filename: str, mp3_size: int, episode_title: str,
                    duration_seconds: int, config: dict):
    print(f"📡 Aktualisiere RSS-Feed...")
    DOCS_DIR.mkdir(exist_ok=True)
    feed_path = DOCS_DIR / "feed.xml"
    base_url  = GITHUB_PAGES_URL.rstrip("/")
    prefix    = config["output"].get("episode_title_prefix", "Weekly AI Digest")
    pub_date  = email.utils.formatdate(localtime=False)

    # Bestehende Episoden laden (max. 10 behalten)
    existing_items = ""
    if feed_path.exists():
        content = feed_path.read_text()
        start = content.find("<item>")
        end   = content.rfind("</item>")
        if start != -1 and end != -1:
            existing_items = content[start:end + len("</item>")]
            items = existing_items.split("</item>")
            items = [i for i in items if i.strip()]
            if len(items) >= 10:
                existing_items = "</item>".join(items[:9]) + "</item>"

    mp3_url  = f"{base_url}/episodes/{mp3_filename}"
    new_item = f"""  <item>
    <title>{episode_title}</title>
    <enclosure url="{mp3_url}" length="{mp3_size}" type="audio/mpeg"/>
    <guid isPermaLink="false">{mp3_filename}</guid>
    <pubDate>{pub_date}</pubDate>
    <itunes:duration>{duration_seconds}</itunes:duration>
    <description>{episode_title}</description>
  </item>"""

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{prefix}</title>
    <link>{base_url}</link>
    <description>Wöchentlicher KI-Podcast-Digest — automatisch generiert</description>
    <language>de</language>
    <itunes:category text="Technology"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:author>AI Podcast Digest</itunes:author>
    <image>
      <url>{base_url}/cover.jpg</url>
      <title>{prefix}</title>
      <link>{base_url}</link>
    </image>
{new_item}
{existing_items}
  </channel>
</rss>"""

    feed_path.write_text(rss)
    print(f"  ✅ RSS-Feed: {base_url}/feed.xml")

# ── Haupt-Pipeline ────────────────────────────────────────────────────────────
def main():
    print("🎙️  AI Podcast Digest — Pipeline startet\n")
    config   = load_config()
    OUTPUT_DIR.mkdir(exist_ok=True)

    voice    = config["output"].get("tts_voice", "onyx")
    pause    = config["output"].get("intro_pause_ms", 800)
    prefix   = config["output"].get("episode_title_prefix", "Weekly AI Digest")
    date_str = datetime.now().strftime("%Y-%m-%d")

    audio_segments = []
    silence_path   = WORK_DIR / "silence.mp3"
    create_silence(pause, silence_path)

    processed = 0
    for i, feed_conf in enumerate(config["feeds"]):
        print(f"\n[{i+1}/{len(config['feeds'])}] {feed_conf['name']}")
        try:
            episode = get_latest_episode(feed_conf)
            if not episode:
                continue

            audio_raw      = download_audio(episode, WORK_DIR)
            if not audio_raw:
                continue

            audio_trim     = trim_audio(audio_raw)
            audio_compress = compress_audio(audio_trim)
            transcript     = transcribe(audio_compress)
            if not transcript.strip():
                print(f"  ⚠️  Leeres Transkript, überspringe.")
                continue

            summary  = summarize(transcript, feed_conf["name"], episode["title"], config)
            tts_path = WORK_DIR / f"tts_{i:02d}.mp3"
            text_to_speech(summary, tts_path, voice)

            audio_segments.append(tts_path)
            audio_segments.append(silence_path)
            processed += 1
            print(f"  ✅ {feed_conf['name']} fertig")

        except Exception as e:
            print(f"  ❌ Fehler bei {feed_conf['name']}: {e}")
            continue

    if not audio_segments:
        print("\n❌ Keine Episoden verarbeitet. Abbruch.")
        sys.exit(1)

    mp3_filename = f"{prefix.replace(' ', '_')}_{date_str}.mp3"
    output_file  = OUTPUT_DIR / mp3_filename
    mix_episodes(audio_segments, output_file)

    size_mb  = output_file.stat().st_size / 1024 / 1024
    duration = get_duration(output_file)
    print(f"\n✅ Fertig! {processed} Shows | {size_mb:.1f} MB | {duration//60}:{duration%60:02d} Min")

    # GitHub Pages: MP3 + RSS
    copy_to_docs(output_file)
    episode_title = f"{prefix} – {datetime.now().strftime('%d.%m.%Y')}"
    update_rss_feed(
        mp3_filename=mp3_filename,
        mp3_size=output_file.stat().st_size,
        episode_title=episode_title,
        duration_seconds=duration,
        config=config
    )

    meta = {
        "date": date_str,
        "shows_processed": processed,
        "output_file": mp3_filename,
        "size_mb": round(size_mb, 1),
        "duration_min": round(duration / 60, 1),
        "rss_feed": f"{GITHUB_PAGES_URL}/feed.xml" if GITHUB_PAGES_URL else "nicht konfiguriert"
    }
    (OUTPUT_DIR / "latest_meta.json").write_text(json.dumps(meta, indent=2))
    shutil.rmtree(WORK_DIR, ignore_errors=True)

if __name__ == "__main__":
    main()
