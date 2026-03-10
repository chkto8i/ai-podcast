# 🎙️ AI Podcast Digest

Vollautomatischer wöchentlicher Podcast-Digest.  
7 Stammshows → 1 kompakte Episode → jeden Montag automatisch in deinem Player.

---

## Wie es funktioniert

```
RSS-Feeds → Download → Whisper (Transkription) → Claude (Zusammenfassung) → OpenAI TTS → ffmpeg Mix → MP3
```

Läuft jeden Montag 06:00 Uhr automatisch via **GitHub Actions** — kein Server nötig.

---

## Einrichtung (einmalig, ~30 Minuten)

### 1. Repository forken / klonen
```bash
git clone https://github.com/DEIN-USERNAME/ai-podcast-digest.git
cd ai-podcast-digest
```

### 2. API Keys als GitHub Secrets hinterlegen

Im GitHub Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Wo besorgen |
|---|---|
| `OPENAI_API_KEY` | platform.openai.com/api-keys |
| `ANTHROPIC_API_KEY` | console.anthropic.com/keys |

### 3. Feeds konfigurieren

`config/feeds.yaml` öffnen und die 7 RSS-Feed-URLs eintragen:

```yaml
feeds:
  - name: "Dein Podcast Name"
    rss_url: "https://beispiel.com/feed.xml"
```

**RSS-URL finden:** In jedem Podcast-Player gibt es "RSS-Feed kopieren". Alternativ:  
`https://podcastindex.org` → Show suchen → RSS-Link kopieren.

### 4. Ersten Lauf manuell testen

GitHub → **Actions → Weekly AI Podcast Digest → Run workflow**

Die fertige MP3 erscheint unter **Artifacts** im Job.

---

## Output abhören

### Option A: Direkt aus GitHub Actions herunterladen
Nach jedem Lauf: Actions → letzter Job → **Artifacts → podcast-digest-XXX** → Download

### Option B: Spotify for Podcasters (empfohlen)
1. Account erstellen auf [podcasters.spotify.com](https://podcasters.spotify.com) (kostenlos)
2. Neuen Podcast anlegen (privat)
3. MP3 wöchentlich manuell hochladen **oder** via Spotify API automatisieren (Phase 2)

### Option C: Privater RSS-Feed (für Fortgeschrittene)
MP3 auf eigenem Server/NAS ablegen + RSS-XML generieren → in jedem Podcast-Player abonnieren.

---

## Kosten

| Service | Kosten/Monat | Basis |
|---|---|---|
| GitHub Actions | **€0** | 2.000 Gratis-Minuten reichen |
| Whisper API | **~€2.50** | 7 × 25 Min @ $0.006/Min |
| Claude Haiku API | **~€0.05** | 7 Zusammenfassungen |
| OpenAI TTS | **~€0.05** | 7 × ~3.000 Zeichen |
| **Gesamt** | **~€3/Monat** | |

---

## Konfigurationsoptionen (`config/feeds.yaml`)

```yaml
output:
  episode_title_prefix: "Weekly AI Digest"  # Dateiname-Präfix
  max_summary_length: 300                    # Zeichen pro Show
  tts_voice: "onyx"                          # Stimme: alloy/echo/fable/onyx/nova/shimmer
  intro_pause_ms: 800                        # Pause zwischen Shows
  language: "de"                             # de oder en
```

---

## Troubleshooting

**Download schlägt fehl:**  
Einige Podcasts blockieren automatische Downloads. Lösung: direkte MP3-URL aus dem RSS-Feed verwenden statt der Show-URL.

**Transkript ist leer:**  
Audio-Qualität zu schlecht oder Datei zu groß. Max. 25 Minuten werden verarbeitet (automatisches Trimmen).

**GitHub Actions Timeout:**  
Timeout auf 60 Minuten gesetzt. Bei sehr langen Shows: `max_seconds` in `digest.py` auf 900 (15 Min) reduzieren.

---

## Skalierungs-Pfad

| Phase | Aktion |
|---|---|
| ✅ Privat (<10 Hörer) | Artifacts manuell herunterladen |
| 🔜 Semi-öffentlich | Spotify for Podcasters, Link teilen |
| 🔮 Öffentlich | Eigene Domain + Buzzsprout (~€7/Monat) |
