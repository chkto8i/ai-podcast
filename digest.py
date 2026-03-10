#!/usr/bin/env python3
"""
AI Podcast Digest — Hauptskript
Ablauf: RSS → Download → Transkription → Zusammenfassung → TTS → Mix → MP3
"""

import os
import sys
import yaml
import json
import feedparser
import subprocess
import requests
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

# ── Konfiguration ────────────────────────────────────────────────────────────
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

BASE_DIR    = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config" / "feeds.yaml"
OUTPUT_DIR  = BASE_DIR / "output"
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
        "--max-filesize", "80m",
        "-o", str(out_path.with_suffix("")),
        episode["audio_url"]
    ], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        # Fallback: direkter Download mit requests
        print(f"  🔄 yt-dlp fehlgeschlagen, versuche direkten Download...")
        r = requests.get(episode["audio_url"], stream=True, timeout=120)
        if r.status_code == 200:
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return out_path
        print(f"  ❌ Download fehlgeschlagen: {episode['show_name']}")
        return None
    # yt-dlp fügt .mp3 selbst an
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

# ── Komprimierung auf 64kbps für größere Daten──────────────────────────────────────
def compress_audio(audio_path: Path) -> Path:
    compressed = audio_path.parent / f"compressed_{audio_path.name}"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_path),
        "-acodec", "libmp3lame",
        "-b:a", "64k",          # 64kbps — reicht für Sprache, ~12MB/25Min
        "-ar", "22050",          # Samplerate reduzieren
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
    max_len = config["output"].get("max_summary_length", 300)
    lang_instruction = "auf Deutsch" if lang == "de" else "in English"

    prompt = f"""Du bist Moderator eines wöchentlichen KI-Podcast-Digests.
Fasse diese Podcast-Folge {lang_instruction} zusammen — prägnant, für unterwegs,.

Show: {show_name}
Folge: {episode_title}

Transkript:
{transcript[:25000]}

Liefere NUR folgendes, ohne Überschriften oder Markdown:
1. Einen Einleitungssatz (Show + Thema, max. 40 Wörter)
2. Drei Key-Insights als Fließtext (je 1-2 Sätze, max. {max_len} Zeichen gesamt)
2. Fünf bis sieben Key-Insights mit konkreten Zahlen, Daten, Fakten und vor allem Beispiele mit welchen Tools welches Problem gelöst werden kann \
   (je 6-8 Sätze, Beispiele nennen, Quellen aus dem Podcast zitieren, \
   keine allgemeinen Aussagen. \
   max. {max_len} Zeichen gesamt)
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
        "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
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

# ── Haupt-Pipeline ────────────────────────────────────────────────────────────
def main():
    print("🎙️  AI Podcast Digest — Pipeline startet\n")
    config = load_config()
    OUTPUT_DIR.mkdir(exist_ok=True)

    voice   = config["output"].get("tts_voice", "onyx")
    pause   = config["output"].get("intro_pause_ms", 800)
    prefix  = config["output"].get("episode_title_prefix", "Weekly AI Digest")
    date_str = datetime.now().strftime("%Y-%m-%d")

    audio_segments = []
    silence_path = WORK_DIR / "silence.mp3"
    create_silence(pause, silence_path)

    processed = 0
    for i, feed_conf in enumerate(config["feeds"]):
        print(f"\n[{i+1}/{len(config['feeds'])}] {feed_conf['name']}")
        try:
            episode = get_latest_episode(feed_conf)
            if not episode:
                continue

            audio_raw  = download_audio(episode, WORK_DIR)
            if not audio_raw:
                continue

            audio_trim = trim_audio(audio_raw)
            audio_trim = compress_audio(audio_trim)
            transcript = transcribe(audio_trim)
            if not transcript.strip():
                print(f"  ⚠️  Leeres Transkript, überspringe.")
                continue

            summary    = summarize(transcript, feed_conf["name"], episode["title"], config)
            tts_path   = WORK_DIR / f"tts_{i:02d}.mp3"
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

    output_file = OUTPUT_DIR / f"{prefix.replace(' ', '_')}_{date_str}.mp3"
    mix_episodes(audio_segments, output_file)

    size_mb = output_file.stat().st_size / 1024 / 1024
    print(f"\n✅ Fertig! {processed} Shows verarbeitet.")
    print(f"📁 Output: {output_file}")
    print(f"📦 Größe: {size_mb:.1f} MB")

    # Metadaten für GitHub Actions Summary
    summary_data = {
        "date": date_str,
        "shows_processed": processed,
        "output_file": str(output_file.name),
        "size_mb": round(size_mb, 1)
    }
    meta_path = OUTPUT_DIR / "latest_meta.json"
    with open(meta_path, "w") as f:
        json.dump(summary_data, f, indent=2)

    # Aufräumen
    shutil.rmtree(WORK_DIR, ignore_errors=True)

if __name__ == "__main__":
    main()
