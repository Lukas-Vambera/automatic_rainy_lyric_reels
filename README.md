# Rainy Lyric Reels

**Autonomous generator of vertical (9:16) lyric videos – rain on a night window with song lyrics perfectly synced to the vocals.**

Pick songs from the charts → find time-synced lyrics → cut the chorus → render a cozy rainy-window animation with word-by-word lyrics → (optionally) publish as an Instagram Reel. All in one Python script.

<!-- Add a demo GIF or screenshot here, e.g.:
![demo](docs/demo.gif)
-->

## Features

- **Trending songs** – pulls the current iTunes Top Songs chart (any country).
- **Synced lyrics** – fetches time-stamped LRC lyrics from [LRCLIB](https://lrclib.net); songs without synced lyrics are skipped.
- **Duration matching** – finds an audio version whose length matches the lyrics, so timing stays accurate across the whole track.
- **Automatic chorus detection** – picks the 30 s window with the most repeated lines.
- **Procedural rain-on-glass background** – refracting droplets, drops sliding down with wet trails, fresh impacts, rain streaks outside, vignette and optional bokeh city lights. Or use your own rain footage.
- **Word-by-word lyrics** – smooth fade/slide-in, soft glow and shadow.
- **Optional Instagram auto-publishing** via the official Instagram Graph API (resumable Reels upload).

## Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) (MoviePy bundles one via `imageio-ffmpeg`, but a system install is recommended for yt-dlp)

```bash
git clone https://github.com/YOUR_USERNAME/rainy-lyric-reels.git
cd rainy-lyric-reels
pip install -r requirements.txt
```

## Usage

```bash
python rainy_lyric_reels.py
```

Finished videos are saved to `output_songs/` as `LyricReel_01.mp4`, `LyricReel_02.mp4`, …

## Configuration

All settings are constants at the top of `rainy_lyric_reels.py`:

| Setting | Default | Description |
|---|---|---|
| `SONG_COUNT` | `10` | Number of videos to generate |
| `CLIP_DURATION` | `30.0` | Clip length in seconds |
| `CHART_COUNTRY` | `"us"` | iTunes chart country code |
| `LYRICS_OFFSET` | `0.0` | Shift lyrics in seconds (+ later / − earlier) |
| `WORD_BY_WORD` | `True` | Reveal words one by one |
| `LOWERCASE` | `True` | Lowercase lyrics |
| `FONT_PATH` | `""` | Custom `.ttf`/`.otf` font |
| `FONT_SIZE` | `66` | Lyrics font size |
| `BOKEH_LIGHTS` | `False` | Colorful blurred city lights behind the glass |
| `BACKGROUND_VIDEO` | `""` | Path to your own rain footage (cropped to 9:16 and looped) |
| `STATIC_DROPS` / `SLIDING_DROPS` / `RAIN_STREAKS` / `HITS_PER_SEC` | `520` / `14` / `140` / `12` | Rain density |
| `IG_UPLOAD` | `False` | Publish videos to Instagram after rendering |
| `IG_POST_DELAY` | `0` | Pause between Instagram posts (s) |

## 📱 Instagram auto-upload (optional)

Requires an Instagram **Business** or **Creator** account connected to a Facebook Page and an access token with the `instagram_content_publish` permission.

```bash
export IG_USER_ID="1784xxxxxxxxxxxx"
export IG_ACCESS_TOKEN="EAAG..."
# optional: export IG_GRAPH_VERSION="v26.0"
```

Then set `IG_UPLOAD = True` in the script. **Never commit your access token** – keep it in environment variables or a `.env` file (already in `.gitignore`).

## How it works

1. **Charts** – iTunes RSS feed → list of popular songs.
2. **Lyrics** – LRCLIB search → all versions with synced lyrics and their durations.
3. **Audio** – yt-dlp search → first result whose duration is within `MAX_DURATION_DIFF` seconds of a lyrics version (covers, remixes, sped-up etc. are filtered out).
4. **Chorus** – sliding 30 s window scored by how often its lines repeat in the song.
5. **Render** – MoviePy calls a frame function that composites the rain scene and the lyrics layer at 30 fps, then encodes H.264/AAC.

## ⚠️ Legal notice

This project is shared for **educational and personal use**. Song recordings and lyrics are protected by copyright.

- Downloading audio from YouTube may violate YouTube's Terms of Service.
- Publishing videos with copyrighted music and lyrics may require a license from the rights holders, and platforms may mute, remove or strike such content.
- Music added via the Graph API is **not** covered by Instagram's in-app music library licensing.

You are solely responsible for how you use this tool and for making sure you have the rights to any content you download or publish. The authors provide the software "as is" without any warranty (see [LICENSE](LICENSE)).

## Credits

- [LRCLIB](https://lrclib.net) – free synced lyrics database
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [MoviePy](https://github.com/Zulko/moviepy)
- [Pillow](https://python-pillow.org/)

## License

[MIT](LICENSE)
