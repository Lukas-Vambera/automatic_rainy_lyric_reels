"""
Rainy Lyric Reels – autonomous 9:16 lyric video generator.

Rain on a window + song lyrics perfectly synced with the vocals.

What it does:
  1. Takes popular songs from the iTunes Top Chart.
  2. Finds time-synced lyrics (LRC) on LRCLIB – songs without synced lyrics are skipped.
  3. Downloads the FULL track (yt-dlp) with the same duration as the synced lyrics,
     so the lyric timestamps line up exactly with the audio.
  4. Picks a 30 s section containing the chorus (the most repeated lines).
  5. Renders rain on glass: blurred night lights (bokeh), drops on the glass that
     refract light, sliding drops with wet trails, new impacts and rain outside.
  6. Lyrics appear word by word exactly when they are sung.

Installation:
  pip install -r requirements.txt

Optional automatic Instagram upload:
  Set the environment variables IG_USER_ID and IG_ACCESS_TOKEN and IG_UPLOAD = True.

Please read the "Legal notice" section in README.md before using this tool.
"""

import os
import re
import math
import time
import glob
import random
import bisect
from collections import Counter

import numpy as np
import requests
import yt_dlp
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageEnhance
from moviepy import VideoClip, AudioFileClip, VideoFileClip, afx

# ================= SETTINGS =================
SONG_COUNT = 10                # how many videos to make
CLIP_DURATION = 30.0           # clip length in seconds
OUT_DIR = "output_songs"
TEMP_DIR = "temp_work"
W, H = 1080, 1920
FPS = 30
CHART_COUNTRY = "us"           # iTunes chart country code (us, gb, cz, de, ...)

LYRICS_OFFSET = 0.0            # fine lyric offset in s (+ = later, - = earlier)
MAX_DURATION_DIFF = 2.5        # max. allowed difference between audio and lyrics duration (s)
MIN_LYRIC_LINES = 8            # skip songs with fewer lyric lines

# Text
WORD_BY_WORD = True            # words appear one by one
LOWERCASE = True               # lowercase text (typical for this kind of reel)
FONT_PATH = ""                 # custom font (.ttf/.otf), otherwise system fonts are tried
FONT_SIZE = 66
TEXT_MAX_WIDTH = W - 200
TEXT_BOX_H = 760
TEXT_BOX_Y = (H - TEXT_BOX_H) // 2
WORD_FADE = 0.18               # how fast a word fades in (s)

# Background
BOKEH_LIGHTS = False           # colorful blurred city lights in the background (True = on)
BACKGROUND_VIDEO = ""          # optional: path to a real rain-on-window video (e.g. "rain.mp4")
STATIC_DROPS = 520             # drops on the glass at the start
SLIDING_DROPS = 14             # drops sliding down the glass
RAIN_STREAKS = 140             # rain falling outside behind the glass
HITS_PER_SEC = 12              # new drops hitting the glass per second

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# Instagram (official Instagram Graph API, Business/Creator account linked to a FB Page)
IG_UPLOAD = False              # publish each rendered video as a Reel right away
IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v26.0")
IG_CAPTION = "{title} – {artist} 🌧️\n\n#lyrics #songlyrics #rain #music #reels"
IG_POST_DELAY = 0              # pause between published videos in s (e.g. 3600 = 1 h)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RainyLyricReels/3.0"}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def load_font(size):
    for p in ([FONT_PATH] if FONT_PATH else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


# ================= 1. TRENDING SONGS =================
def clean_title(title):
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", title)   # (feat. X), [Remix] ...
    t = re.sub(r"\s+-\s+.*$", "", t)                # " - From the Movie ..."
    return t.strip()


def primary_artist(artist):
    return re.split(r"\s*(?:&|,| x | feat\.?| ft\.?| with )\s*", artist, flags=re.I)[0].strip()


def get_popular_songs(limit=50):
    url = f"https://itunes.apple.com/{CHART_COUNTRY}/rss/topsongs/limit={limit}/json"
    print("🔍 Fetching popular songs...")
    try:
        data = requests.get(url, headers=UA, timeout=10).json()
        songs = []
        for entry in data.get("feed", {}).get("entry", []):
            title = entry.get("im:name", {}).get("label", "")
            artist = entry.get("im:artist", {}).get("label", "")
            if title and artist:
                songs.append({"title": title, "clean_title": clean_title(title), "artist": artist})
        return songs
    except Exception as e:
        print(f"⚠️ Failed to load the chart: {e}")
        return []


# ================= 2. SYNCED LYRICS (LRCLIB) =================
def norm(s):
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def lrclib_candidates(artist, title):
    """Return all versions with synced lyrics (each one has its own duration)."""
    attempts = [
        {"artist_name": artist, "track_name": title},
        {"artist_name": primary_artist(artist), "track_name": title},
        {"q": f"{primary_artist(artist)} {title}"},
    ]
    want = norm(title)
    seen, out = set(), []
    for params in attempts:
        try:
            r = requests.get("https://lrclib.net/api/search", params=params, headers=UA, timeout=10)
            if r.ok:
                for c in r.json():
                    if (c.get("syncedLyrics") and not c.get("instrumental") and c.get("duration")
                            and c["id"] not in seen and want in norm(c.get("trackName", ""))):
                        seen.add(c["id"])
                        out.append(c)
        except Exception as e:
            print(f"   ⚠️ LRCLIB: {e}")
        if out:
            break
    return out


TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
WORD_TS = re.compile(r"<\d+:\d+(?:\.\d+)?>")


def parse_lrc(lrc_text):
    """LRC -> [(time_s, "text"), ...]. Empty lines = pause (text disappears)."""
    lines = []
    for raw in lrc_text.splitlines():
        stamps = TS.findall(raw)
        if not stamps:
            continue
        text = WORD_TS.sub("", TS.sub("", raw)).replace("♪", "").strip()
        for m, s in stamps:
            lines.append((int(m) * 60 + float(s) + LYRICS_OFFSET, text))
    return sorted(lines, key=lambda x: x[0])


def find_best_part(lyrics, song_duration, clip=CLIP_DURATION):
    """Pick the 30 s window with the most repeated lines (= the chorus)."""
    texts = [norm(t) for _, t in lyrics]
    counts = Counter(x for x in texts if x)
    best_score, best_start = -1, None
    for i, (t0, _) in enumerate(lyrics):
        if not texts[i]:
            continue
        start = max(0.0, t0 - 0.6)
        if start + clip > song_duration:
            break
        window = [texts[j] for j, (tj, _) in enumerate(lyrics)
                  if start <= tj < start + clip - 1.0 and texts[j]]
        if len(window) < 4:
            continue
        score = sum(counts[x] for x in window) + 2 * counts[texts[i]]
        if score > best_score:
            best_score, best_start = score, start
    if best_start is None:
        first = next((t for t, x in lyrics if x), 0.0)
        best_start = clamp(first - 0.6, 0.0, max(0.0, song_duration - clip))
    return best_start


# ================= 3. FULL TRACK (yt-dlp) =================
BAD_WORDS = ["cover", "live", "remix", "sped up", "slowed", "karaoke", "instrumental",
             "8d", "nightcore", "reverb", "acoustic", "extended"]


def find_youtube_audio(artist, title, target_durations):
    """Find a YouTube version whose duration matches the synced lyrics."""
    query = f"ytsearch10:{primary_artist(artist)} - {title} official audio"
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.extract_info(query, download=False)
    title_l = title.lower()
    for e in res.get("entries") or []:
        d = e.get("duration")
        name = (e.get("title") or "").lower()
        if not d:
            continue
        if any(re.search(rf"\b{re.escape(b)}\b", name) and b not in title_l for b in BAD_WORDS):
            continue
        if min(abs(d - td) for td in target_durations) <= MAX_DURATION_DIFF:
            return e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
    return None


def download_audio(url, out_base):
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": out_base + ".%(ext)s",
        "quiet": True, "no_warnings": True, "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


# ================= 4. RAIN ON THE WINDOW =================
class RainyWindow:
    """Night window in the rain: bokeh lights, refracting drops, sliding drops, impacts."""
    PAD = 200

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self._mask_cache = {}
        self.bg, self.lens_src = self._make_scene()
        self.vignette = self._make_vignette()
        self.black = Image.new("RGB", (W, H), (0, 0, 0))

        # slightly fogged glass + drops
        self.glass = Image.new("RGBA", (W, H), (205, 215, 230, 20))
        for _ in range(STATIC_DROPS):
            self._stamp_drop(self.rng.uniform(0, W), self.rng.uniform(0, H), self._rand_radius())

        self.streaks = [self._new_streak(True) for _ in range(RAIN_STREAKS)]
        self.sliders = [self._new_slider(True) for _ in range(SLIDING_DROPS)]
        self.hit_acc = 0.0
        self.last_t = None
        self._last = None

    # ---------- scene ----------
    def _make_scene(self):
        rng = self.rng
        yy = np.linspace(0, 1, H)[:, None]
        top, bot = np.array([20, 26, 40], float), np.array([5, 7, 12], float)
        grad = top * (1 - yy) + bot * yy
        base = Image.fromarray(np.repeat(grad[:, None, :], W, axis=1).astype(np.uint8), "RGB")

        palette = [(255, 170, 80), (255, 200, 120), (255, 120, 60), (120, 170, 255),
                   (200, 220, 255), (255, 80, 90), (150, 255, 220)]
        lights = [(rng.uniform(-50, W + 50), rng.uniform(H * 0.12, H * 1.05),
                   rng.choice(palette), rng.uniform(0.3, 1.0)) for _ in range(85)]
        if not BOKEH_LIGHTS:
            # just a dark, slightly foggy background without colored lights
            lens = ImageEnhance.Brightness(base).enhance(1.6).filter(ImageFilter.GaussianBlur(2))
            arr = np.pad(np.array(lens), ((self.PAD, self.PAD), (self.PAD, self.PAD), (0, 0)), mode="edge")
            return base, Image.fromarray(arr)

        glow = Image.new("RGB", (W, H), (0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for _ in range(7):
            x, y, r = rng.uniform(0, W), rng.uniform(H * 0.3, H), rng.uniform(250, 500)
            c = rng.choice(palette)
            gd.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(v * 0.18) for v in c))
        glow = glow.filter(ImageFilter.GaussianBlur(120))

        # blurred background (what is behind the glass)
        bg = ImageChops.add(base, glow).convert("RGBA")
        bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bokeh)
        for x, y, c, b in lights:
            r = rng.uniform(18, 70)
            a = int(48 * b)
            bd.ellipse([x - r, y - r, x + r, y + r], fill=(*c, a), outline=(*c, min(255, a + 40)), width=2)
        bg.alpha_composite(bokeh.filter(ImageFilter.GaussianBlur(6)))
        bg = bg.convert("RGB")

        # sharper version that the drops "see" (lens effect)
        lens = ImageChops.add(base, glow)
        ld = ImageDraw.Draw(lens, "RGBA")
        for x, y, c, b in lights:
            r = rng.uniform(3, 8)
            ld.ellipse([x - r, y - r, x + r, y + r], fill=(*c, int(255 * b)))
        lens = lens.filter(ImageFilter.GaussianBlur(2))
        arr = np.pad(np.array(lens), ((self.PAD, self.PAD), (self.PAD, self.PAD), (0, 0)), mode="edge")
        return bg, Image.fromarray(arr)

    def _make_vignette(self):
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        dx, dy = (xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2)
        d = np.sqrt(dx ** 2 * 0.9 + dy ** 2 * 0.6)
        m = 30 + np.clip((d - 0.45) / 0.9, 0, 1) ** 1.6 * 190
        return Image.fromarray(np.clip(m, 0, 255).astype(np.uint8), "L")

    # ---------- drops ----------
    def _rand_radius(self):
        return min(14, max(2, int(self.rng.paretovariate(1.6) * 2.8)))

    def _mask(self, w, h):
        key = (w, h)
        if key not in self._mask_cache:
            big = Image.new("L", (w * 4, h * 4), 0)
            ImageDraw.Draw(big).ellipse([0, 0, w * 4 - 1, h * 4 - 1], fill=235)
            self._mask_cache[key] = big.resize((w, h), Image.LANCZOS)
        return self._mask_cache[key]

    def _drop_sprite(self, cx, cy, r):
        rx, ry = max(2, int(r)), max(2, int(r * 1.12))
        w, h = 2 * rx, 2 * ry
        k, P = 5, self.PAD
        box = (int(cx - rx * k) + P, int(cy - ry * k) + P, int(cx + rx * k) + P, int(cy + ry * k) + P)
        src = self.lens_src.crop(box).resize((w, h), Image.BILINEAR).transpose(Image.ROTATE_180)
        spr = ImageEnhance.Brightness(src).enhance(1.35).convert("RGBA")
        d = ImageDraw.Draw(spr, "RGBA")
        d.ellipse([0, 0, w - 1, h - 1], outline=(0, 0, 0, 110), width=max(1, rx // 4))   # dark rim
        hr = max(1.0, rx * 0.28)
        hx, hy = rx * 0.62, ry * 0.55
        d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr * 0.8], fill=(255, 255, 255, 200))  # highlight
        d.arc([w * 0.18, h * 0.25, w * 0.82, h * 0.92], start=30, end=150,
              fill=(255, 255, 255, 70), width=max(1, rx // 5))                           # light crescent
        spr.putalpha(ImageChops.multiply(spr.getchannel("A"), self._mask(w, h)))
        return spr

    def _stamp_drop(self, x, y, r):
        if r <= 2.5:
            ImageDraw.Draw(self.glass).ellipse([x - r, y - r, x + r, y + r * 1.1], fill=(220, 230, 245, 75))
            return
        spr = self._drop_sprite(x, y, r)
        px = int(clamp(x - spr.width / 2, 0, W - spr.width))
        py = int(clamp(y - spr.height / 2, 0, H - spr.height))
        self.glass.alpha_composite(spr, (px, py))

    def _new_slider(self, initial=False):
        rng = self.rng
        return {"x": rng.uniform(30, W - 30),
                "y": rng.uniform(0, H) if initial else rng.uniform(-120, -30),
                "r": rng.uniform(8, 15), "v": 0.0,
                "pause": rng.uniform(0, 2.5), "phase": rng.uniform(0, 6.28)}

    def _update_slider(self, s, dt):
        rng = self.rng
        if s["pause"] > 0:
            s["pause"] -= dt
            s["v"] = 0.0
            return
        s["v"] = min(s["v"] + 900 * dt, 260 + s["r"] * 30)
        if rng.random() < 0.6 * dt:                       # sometimes the drop stops for a moment
            s["pause"] = rng.uniform(0.15, 1.0)
        old = (s["x"], s["y"])
        s["phase"] += dt * 3
        s["x"] += math.sin(s["phase"]) * 25 * dt
        s["y"] += s["v"] * dt
        new = (s["x"], s["y"])
        d = ImageDraw.Draw(self.glass)
        d.line([old, new], fill=(205, 215, 230, 9), width=int(s["r"] * 1.5))              # wipes drops (cleaner glass)
        d.line([old, new], fill=(220, 230, 245, 26), width=max(2, int(s["r"] * 0.45)))   # wet trail
        if rng.random() < 0.25:
            self._stamp_drop(old[0] + rng.uniform(-3, 3), old[1], rng.uniform(1.5, 3.5))
        s["r"] = max(6.0, s["r"] - 0.01)
        if s["y"] - s["r"] * 2 > H:
            s.update(self._new_slider())

    def _new_streak(self, initial=False):
        rng = self.rng
        return {"x": rng.uniform(-100, W + 100),
                "y": rng.uniform(-H, H) if initial else rng.uniform(-400, -50),
                "len": rng.uniform(40, 120), "v": rng.uniform(1400, 2400),
                "a": rng.randint(18, 55), "w": rng.choice([1, 1, 2])}

    # ---------- frame ----------
    def render(self, t):
        if self.last_t is not None and abs(t - self.last_t) < 1e-6 and self._last is not None:
            return self._last.copy()
        dt = 1.0 / FPS if self.last_t is None else t - self.last_t
        if dt <= 0 or dt > 0.5:
            dt = 1.0 / FPS
        self.last_t = t

        for s in self.streaks:
            s["y"] += s["v"] * dt
            s["x"] -= s["v"] * dt * 0.08
            if s["y"] > H + 150:
                s.update(self._new_streak())
        self.hit_acc += HITS_PER_SEC * dt
        while self.hit_acc >= 1:
            self.hit_acc -= 1
            self._stamp_drop(self.rng.uniform(0, W), self.rng.uniform(0, H), self._rand_radius())
        for s in self.sliders:
            self._update_slider(s, dt)

        frame = self.bg.copy()
        d = ImageDraw.Draw(frame, "RGBA")
        for s in self.streaks:
            x, y, L = s["x"], s["y"], s["len"]
            d.line([(x, y), (x - L * 0.08, y + L)], fill=(200, 215, 235, s["a"]), width=s["w"])
        frame.paste(self.glass, (0, 0), self.glass)
        for s in self.sliders:
            spr = self._drop_sprite(s["x"], s["y"], s["r"])
            frame.paste(spr, (int(s["x"] - spr.width / 2), int(s["y"] - spr.height / 2)), spr)
        frame.paste(self.black, (0, 0), self.vignette)

        self._last = frame
        return frame.copy()

    def close(self):
        pass


class VideoBackground:
    """Real rain footage (optional) – cropped to 9:16 and looped."""
    def __init__(self, path):
        self.clip = VideoFileClip(path, audio=False)

    def render(self, t):
        fr = self.clip.get_frame(t % max(self.clip.duration - 0.05, 0.1))
        img = Image.fromarray(fr).convert("RGB")
        s = max(W / img.width, H / img.height)
        img = img.resize((int(img.width * s + 0.5), int(img.height * s + 0.5)), Image.BILINEAR)
        l, tp = (img.width - W) // 2, (img.height - H) // 2
        return ImageEnhance.Brightness(img.crop((l, tp, l + W, tp + H))).enhance(0.8)

    def close(self):
        self.clip.close()


# ================= 5. LYRICS SYNCED WITH THE VOCALS =================
class LyricsRenderer:
    def __init__(self, lyrics):
        self.lyrics = lyrics
        self.times = [t for t, _ in lyrics]
        self.font = load_font(FONT_SIZE)
        self.space = self.font.getlength(" ")
        self._layout_cache = {}

    def _layout(self, text):
        if text in self._layout_cache:
            return self._layout_cache[text]
        lines, cur, cur_w = [], [], 0.0
        for w in text.split():
            ww = self.font.getlength(w)
            new_w = ww if not cur else cur_w + self.space + ww
            if cur and new_w > TEXT_MAX_WIDTH:
                lines.append(cur)
                cur, cur_w = [w], ww
            else:
                cur.append(w)
                cur_w = new_w
        if cur:
            lines.append(cur)

        line_h = int(FONT_SIZE * 1.35)
        y0 = (TEXT_BOX_H - line_h * len(lines)) // 2
        out = []
        for li, lw in enumerate(lines):
            widths = [self.font.getlength(w) for w in lw]
            x = (W - (sum(widths) + self.space * (len(lw) - 1))) / 2
            for w, ww in zip(lw, widths):
                out.append((w, x, y0 + li * line_h))
                x += ww + self.space
        self._layout_cache[text] = out
        return out

    def render(self, t):
        """Return an RGBA layer (W x TEXT_BOX_H) with the line sung at time t, or None."""
        idx = bisect.bisect_right(self.times, t) - 1
        if idx < 0:
            return None
        start, text = self.lyrics[idx]
        if not text:
            return None
        nxt = self.lyrics[idx + 1][0] if idx + 1 < len(self.lyrics) else start + 6
        end = min(nxt, start + 7)
        if t >= end:
            return None
        if LOWERCASE:
            text = text.lower()

        words = self._layout(text)
        n = len(words)
        reveal = clamp((end - start) * 0.65, 0.3, 3.0)
        out_a = clamp((end - t) / 0.15, 0, 1)

        layer = Image.new("RGBA", (W, TEXT_BOX_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for k, (w, x, y) in enumerate(words):
            appear = start + (k / n) * reveal if WORD_BY_WORD else start
            p = clamp((t - appear) / WORD_FADE, 0, 1)
            if p <= 0:
                continue
            e = 1 - (1 - p) ** 3
            d.text((x, y + (1 - e) * 18), w, font=self.font, fill=(255, 255, 255, int(255 * e * out_a)))

        alpha = layer.getchannel("A")
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(6)).point(lambda v: int(v * 0.8)))
        glow = Image.new("RGBA", layer.size, (255, 255, 255, 0))
        glow.putalpha(alpha.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.4)))

        result = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        result.alpha_composite(shadow, (3, 4))
        result.alpha_composite(glow)
        result.alpha_composite(layer)
        return result


# ================= 6. VIDEO ASSEMBLY =================
def build_song_reel(song, out_path):
    artist, title = song["artist"], song["clean_title"]

    print("   📜 Looking for synced lyrics...")
    cands = lrclib_candidates(artist, title)
    if not cands:
        print("   ⏭️ No synced lyrics – skipping.")
        return False

    print("   🎧 Looking for a full version of the track with matching duration...")
    url = find_youtube_audio(artist, title, [c["duration"] for c in cands])
    if not url:
        print("   ⏭️ No audio matching the lyrics found – skipping.")
        return False

    os.makedirs(TEMP_DIR, exist_ok=True)
    audio_path = download_audio(url, os.path.join(TEMP_DIR, "full_song"))
    full = bg = video = None
    try:
        full = AudioFileClip(audio_path)
        dur = full.duration
        best = min(cands, key=lambda c: abs(c["duration"] - dur))
        lyrics = parse_lrc(best["syncedLyrics"])
        if sum(1 for _, x in lyrics if x) < MIN_LYRIC_LINES:
            print("   ⏭️ Not enough lyrics – skipping.")
            return False

        start = find_best_part(lyrics, dur)
        end = min(start + CLIP_DURATION, dur)
        print(f"   ✂️ Cut {start:.1f}s – {end:.1f}s (audio/lyrics duration diff {abs(best['duration'] - dur):.1f}s)")

        audio = full.subclipped(start, end).with_effects([afx.AudioFadeIn(0.4), afx.AudioFadeOut(1.2)])

        if BACKGROUND_VIDEO and os.path.exists(BACKGROUND_VIDEO):
            bg = VideoBackground(BACKGROUND_VIDEO)
        else:
            bg = RainyWindow(seed=random.randint(0, 10 ** 9))
        text = LyricsRenderer(lyrics)

        def make_frame(t):
            frame = bg.render(t)
            layer = text.render(start + t)
            if layer is not None:
                frame.paste(layer, (0, TEXT_BOX_Y), layer)
            return np.array(frame)

        print("🎬 Rendering video...")
        video = VideoClip(make_frame, duration=end - start).with_audio(audio)
        video.write_videofile(out_path, fps=FPS, codec="libx264", audio_codec="aac",
                              preset="medium", threads=os.cpu_count() or 4, logger="bar")
        print(f"✅ Done! Saved to: {out_path}")
        return True
    finally:
        for c in (video, full, bg):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        for f in glob.glob(os.path.join(TEMP_DIR, "full_song.*")):
            try:
                os.remove(f)
            except OSError:
                pass


# ================= 7. INSTAGRAM UPLOAD =================
def _ig_check(r, what):
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    if not r.ok or "error" in data:
        msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data
        raise RuntimeError(f"Instagram ({what}): {msg}")
    return data


def upload_to_instagram(video_path, caption):
    """Upload a local MP4 as a Reel (resumable upload) and publish it. Returns the post ID."""
    graph = f"https://graph.facebook.com/{GRAPH_VERSION}"

    # 1) create the Reel container
    r = requests.post(f"{graph}/{IG_USER_ID}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": IG_ACCESS_TOKEN}, timeout=30)
    container_id = _ig_check(r, "container creation")["id"]

    # 2) upload the file to Meta's servers
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{container_id}",
                          headers={"Authorization": f"OAuth {IG_ACCESS_TOKEN}",
                                   "offset": "0", "file_size": str(size)},
                          data=f, timeout=600)
    _ig_check(r, "video upload")

    # 3) wait until Instagram finishes processing the video
    for _ in range(60):
        r = requests.get(f"{graph}/{container_id}",
                         params={"fields": "status_code,status", "access_token": IG_ACCESS_TOKEN}, timeout=30)
        st = _ig_check(r, "processing status")
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram is taking too long to process the video (10 min).")

    # 4) publish
    r = requests.post(f"{graph}/{IG_USER_ID}/media_publish",
                      data={"creation_id": container_id, "access_token": IG_ACCESS_TOKEN}, timeout=60)
    return _ig_check(r, "publishing")["id"]


# ================= MAIN =================
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ig_on = IG_UPLOAD and IG_USER_ID and IG_ACCESS_TOKEN
    if IG_UPLOAD and not ig_on:
        print("⚠️ IG_UPLOAD is on, but IG_USER_ID / IG_ACCESS_TOKEN is missing – videos will only be saved.")
    songs = get_popular_songs(limit=50)
    if not songs:
        print("❌ Could not load any songs.")
        return
    random.shuffle(songs)

    made = 0
    for song in songs:
        if made >= SONG_COUNT:
            break
        out_file = os.path.join(OUT_DIR, f"LyricReel_{made + 1:02d}.mp4")
        print(f"\n▶️ [{made + 1}/{SONG_COUNT}] {song['title']} – {song['artist']}")
        try:
            if build_song_reel(song, out_file):
                made += 1
                if ig_on:
                    if made > 1 and IG_POST_DELAY:
                        print(f"   ⏳ Waiting {IG_POST_DELAY} s before the next post...")
                        time.sleep(IG_POST_DELAY)
                    print("📤 Uploading to Instagram...")
                    caption = IG_CAPTION.format(title=song["title"], artist=song["artist"])
                    try:
                        post_id = upload_to_instagram(out_file, caption)
                        print(f"   ✅ Published to Instagram (ID {post_id})")
                    except Exception as e:
                        print(f"   ⚠️ Instagram upload failed: {e}")
        except Exception as e:
            print(f"⚠️ Processing error: {e}")

    print(f"\n🏁 Finished, created {made} videos in '{OUT_DIR}'.")


if __name__ == "__main__":
    main()
