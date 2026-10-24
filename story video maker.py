# -*- coding: utf-8 -*-
"""
صانع فيديوهات حكايات الليل
--------------------------------
يحول ملف القصة (story.txt) + صور المشاهد (images/) الى:
  - فيديو يوتيوب افقي 1920x1080 بصوت راوي + حركة زوم + ترجمة عربية + موسيقى
  - شورتس عمودية 1080x1920 من المشاهد اللي تختارها

التشغيل:
  python story_video_maker.py              -> الفيديو + الشورتس
  python story_video_maker.py --preview    -> نسخة سريعة صغيرة للتجربة
  python story_video_maker.py --no-shorts  -> الفيديو الطويل فقط
  python story_video_maker.py --shorts-only
  python story_video_maker.py --silent     -> بدون صوت (لتجربة الصور والترجمة بدون انترنت)
"""
import os
import re
import sys
import json
import wave
import math
import asyncio
import hashlib
import argparse
import random
import subprocess
import urllib.request
import urllib.error

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, features
import imageio_ffmpeg

# =====================================================================
#  الاعدادات - عدلها حسب ما تريد
# =====================================================================
CONFIG = {
    "story_file": "story.txt",
    "images_dir": "images",
    "music_file": "music.mp3",          # اختياري - اذا ما موجود يطلع بدون موسيقى
    "output_dir": "output",
    "work_dir": "work",

    # ---- الصوت ----
    # azure      = خدمة مايكروسوفت الرسمية - الخطة المجانية F0 تسمح بالربح (المفتاح بملف settings.json)
    # edge       = مجاني بدون مفتاح، بس غير رسمي (للتجربة فقط)
    # elevenlabs = اعلى جودة، يحتاج مفتاح API (اشتراك Starter فما فوق للربح)
    "voice_engine": "azure",
    "azure_key": "",
    "azure_region": "",
    "edge_voice": "ar-SA-HamedNeural",   # بدائل: ar-EG-ShakirNeural , ar-IQ-BasselNeural
    "edge_rate": "-8%",                  # ابطأ شوية = احلى للحكايات
    "edge_pitch": "-2Hz",
    "eleven_api_key": "",
    "eleven_voice_id": "",
    "eleven_model": "eleven_multilingual_v2",

    # ---- الفيديو ----
    "fps": 25,
    "size": (1920, 1080),
    "short_size": (1080, 1920),
    "zoom": 0.12,              # قوة الزوم البطيء
    "crossfade": 0.7,          # مدة الانتقال بين الصور (ثانية)
    "scene_gap": 0.7,          # سكتة بين المشاهد (ثانية)
    "lead_in": 0.6,
    "tail": 2.0,
    "music_volume": 0.08,
    "subtitles": True,
    "effects": "غبار",         # المؤثر الافتراضي لكل المشاهد (غبار، لمعان، مطر، ثلج، ضباب، فانوس، بدون)
    "vignette": True,          # تعتيم خفيف على الاطراف (سينمائي)
    "font_file": "",           # اتركه فارغ = يختار خط تلقائي (او حط ملف ttf بمجلد fonts)
    "quality": 8,              # 1-10

    # ---- الشورتس ----
    "channel_name": "حكايات الليل",
    "shorts": [[7], [11, 12]],         # كل قائمة = شورت واحد من هالمشاهد
    "short_end_text": "القصة كاملة على القناة",
}

BASE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif")
try:
    from pillow_heif import register_heif_opener   # صور الايفون HEIC
    register_heif_opener()
except Exception:
    pass


def P(*parts):
    return os.path.join(BASE, *parts)


def log(msg):
    print(msg, flush=True)


def load_settings():
    """اي قيمة بملف settings.json تغطي على الاعدادات اللي فوق"""
    for env, key in (("AZURE_KEY", "azure_key"), ("AZURE_REGION", "azure_region"),
                     ("ELEVEN_API_KEY", "eleven_api_key")):
        if os.environ.get(env, "").strip():
            CONFIG[key] = os.environ[env].strip()
    path = P("settings.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        log(f"settings.json error: {e}")
        sys.exit(1)
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if k in ("azure_key", "azure_region") and not str(v).strip():
            continue
        if k in ("size", "short_size"):
            v = tuple(v)
        CONFIG[k] = v


# =====================================================================
#  النص العربي
# =====================================================================
RAQM = features.check_feature("raqm")
if not RAQM:
    try:
        import arabic_reshaper
        try:
            from bidi.algorithm import get_display
        except ImportError:
            from bidi import get_display
    except ImportError:
        log("Missing: pip install arabic-reshaper python-bidi")
        sys.exit(1)


def shape(text):
    if RAQM:
        return text
    return get_display(arabic_reshaper.reshape(text))


def text_w(text, font):
    if RAQM:
        return font.getlength(text, direction="rtl")
    return font.getlength(shape(text))


def draw_txt(draw, xy, text, font, **kw):
    if RAQM:
        draw.text(xy, text, font=font, direction="rtl", **kw)
    else:
        draw.text(xy, shape(text), font=font, **kw)


def find_font():
    cands = []
    if CONFIG["font_file"]:
        cands.append(P(CONFIG["font_file"]))
        cands.append(CONFIG["font_file"])
    fdir = P("fonts")
    if os.path.isdir(fdir):
        cands += [os.path.join(fdir, f) for f in sorted(os.listdir(fdir))
                  if f.lower().endswith((".ttf", ".otf"))]
    win = os.environ.get("WINDIR", "C:\\Windows")
    cands += [os.path.join(win, "Fonts", f) for f in
              ("tahomabd.ttf", "arialbd.ttf", "segoeuib.ttf", "tahoma.ttf", "arial.ttf")]
    cands += ["/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
              "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    log("No font found. Put a .ttf file inside the 'fonts' folder.")
    sys.exit(1)


FONT_PATH = None


def font(size):
    return ImageFont.truetype(FONT_PATH, size)


def wrap(text, fnt, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if not cur or text_w(t, fnt) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_caption(text, fnt, max_w, bg_alpha=150):
    lines = wrap(text, fnt, max_w)
    lh = int(fnt.size * 1.5)
    pad_x, pad_y = int(fnt.size * 0.8), int(fnt.size * 0.45)
    widths = [text_w(l, fnt) for l in lines]
    w = int(max(widths) + pad_x * 2)
    h = int(lh * len(lines) + pad_y * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bg_alpha:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(fnt.size * 0.55),
                            fill=(0, 0, 0, bg_alpha))
    y = pad_y + int((lh - fnt.size) * 0.25)
    for line, lw in zip(lines, widths):
        draw_txt(d, ((w - lw) / 2, y), line, fnt, fill=(255, 255, 255, 255),
                 stroke_width=max(2, fnt.size // 18), stroke_fill=(0, 0, 0, 255))
        y += lh
    return img


def split_captions(text, max_chars=85):
    text = text.replace("(وقفة)", "\nفكّر بالجواب...\n")
    parts = []
    for para in text.split("\n"):
        for s in re.split(r"(?<=[\.!؟\?…])\s+", para.strip()):
            s = s.strip()
            if not s:
                continue
            if len(s) <= max_chars:
                parts.append(s)
                continue
            chunk = ""
            for piece in re.split(r"(?<=[،,:])\s+", s):
                if chunk and len(chunk) + len(piece) + 1 > max_chars:
                    parts.append(chunk)
                    chunk = piece
                else:
                    chunk = (chunk + " " + piece).strip()
            if chunk:
                parts.append(chunk)
    final = []
    for p in parts:   # اي قطعة بعدها طويلة نقسمها على الكلمات
        words = p.split()
        while len(" ".join(words)) > max_chars * 1.3:
            half = len(words) // 2
            final.append(" ".join(words[:half]))
            words = words[half:]
        final.append(" ".join(words))
    return final


# =====================================================================
#  القصة
# =====================================================================
def parse_story(path):
    if not os.path.isfile(path):
        log(f"Story file not found: {path}")
        log("Drag an episode folder (from 'episodes') onto make_video.bat")
        sys.exit(1)
    title, scenes, cur = "", [], None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if s.startswith("#"):
                if "العنوان" in s and ":" in s:
                    title = s.split(":", 1)[1].strip()
                elif "شورتس" in s and ":" in s:
                    groups = []
                    for g in s.split(":", 1)[1].split("|"):
                        nums = [int(x) for x in re.findall(r"\d+", g)]
                        if nums:
                            groups.append(nums)
                    if groups:
                        CONFIG["shorts"] = groups
                continue
            m = re.match(r"^\[\s*(\d+)\s*\]\s*(?:\{(.*?)\})?\s*$", s)
            if m:
                cur = {"n": int(m.group(1)), "lines": [], "fx": parse_fx(m.group(2)) or None}
                scenes.append(cur)
            elif cur is not None and s:
                cur["lines"].append(s)
    for sc in scenes:
        sc["text"] = "\n".join(sc.pop("lines"))
    scenes = [s for s in scenes if s["text"]]
    if not scenes:
        log("No scenes found. Each scene must start with [1], [2], ...")
        sys.exit(1)
    return title, scenes


# =====================================================================
#  الصوت
# =====================================================================
def run_ff(args):
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error"] + args,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


def tts_edge(text, out):
    try:
        import edge_tts
    except ImportError:
        log("Missing: pip install edge-tts")
        sys.exit(1)

    async def go():
        c = edge_tts.Communicate(text.replace(PAUSE, "..."), CONFIG["edge_voice"],
                                 rate=CONFIG["edge_rate"], pitch=CONFIG["edge_pitch"])
        await c.save(out)
    asyncio.run(go())


PAUSE = "(وقفة)"   # بالقصة = سكتة 3 ثواني حتى المشاهد يفكر باللغز


def ssml_text(text):
    from xml.sax.saxutils import escape
    t = escape(text)
    t = t.replace(PAUSE, '<break time="3000ms"/>')
    t = re.sub(r"\.{3,}|…", '<break time="450ms"/>', t)   # النقاط الثلاث = سكتة
    t = t.replace("\n", ' <break time="350ms"/> ')
    return t


def tts_azure(text, out):
    key, region = CONFIG["azure_key"].strip(), CONFIG["azure_region"].strip()
    if not key or not region:
        log("Put your Azure key and region in settings.json")
        sys.exit(1)
    voice = CONFIG["edge_voice"]
    lang = "-".join(voice.split("-")[:2])
    ssml = (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang}'>"
            f"<voice name='{voice}'><prosody rate='{CONFIG['edge_rate']}' pitch='{CONFIG['edge_pitch']}'>"
            f"{ssml_text(text)}</prosody></voice></speak>")
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    req = urllib.request.Request(url, data=ssml.encode("utf-8"), headers={
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
        "User-Agent": "StoryVideoMaker"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r, open(out, "wb") as f:
            f.write(r.read())
    except urllib.error.HTTPError as e:
        hint = {401: "wrong key or region", 429: "too many requests / monthly free limit reached",
                400: "bad voice name"}.get(e.code, "")
        raise RuntimeError(f"Azure HTTP {e.code} {hint}")


def tts_eleven(text, out):
    key, vid = CONFIG["eleven_api_key"], CONFIG["eleven_voice_id"]
    if not key or not vid:
        log("Set eleven_api_key and eleven_voice_id in CONFIG.")
        sys.exit(1)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_128"
    body = json.dumps({"text": text.replace(PAUSE, "..."), "model_id": CONFIG["eleven_model"],
                       "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=180) as r, open(out, "wb") as f:
        f.write(r.read())


def silent_wav(path, seconds, rate=44100):
    n = int(seconds * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00\x00\x00" * n)


def wav_info(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def make_voice(scene, work, silent=False):
    n = scene["n"]
    wav = os.path.join(work, f"voice_{n:02d}.wav")
    engine = "silent" if silent else CONFIG["voice_engine"]
    voice = CONFIG["eleven_voice_id"] if engine == "elevenlabs" else CONFIG["edge_voice"]
    sig = hashlib.md5(f"{engine}|{voice}|{CONFIG['edge_rate']}|{scene['text']}".encode()).hexdigest()
    sig_file = wav + ".sig"
    if os.path.isfile(wav) and os.path.isfile(sig_file) and open(sig_file).read() == sig:
        return wav  # جاهز من قبل
    if silent:
        silent_wav(wav, max(3.0, len(scene["text"]) / 13.0))
    else:
        mp3 = os.path.join(work, f"voice_{n:02d}.mp3")
        for attempt in range(3):
            try:
                {"edge": tts_edge, "azure": tts_azure,
                 "elevenlabs": tts_eleven}[engine](scene["text"], mp3)
                break
            except Exception as e:
                log(f"  voice error scene {n} (try {attempt + 1}): {e}")
                if attempt == 2:
                    raise
                import time
                time.sleep(3)
        run_ff(["-i", mp3, "-ar", "44100", "-ac", "2", "-sample_fmt", "s16", wav])
    with open(sig_file, "w") as f:
        f.write(sig)
    return wav


def concat_wavs(items, out, lead, gap, tail, rate=44100):
    """items = [(wav_path, ...)] -> يرجع اوقات بداية ومدة كل مقطع"""
    times = []
    with wave.open(out, "wb") as o:
        o.setnchannels(2)
        o.setsampwidth(2)
        o.setframerate(rate)
        o.writeframes(b"\x00" * 4 * int(lead * rate))
        t = lead
        for i, path in enumerate(items):
            with wave.open(path, "rb") as w:
                data = w.readframes(w.getnframes())
                dur = w.getnframes() / w.getframerate()
            o.writeframes(data)
            times.append((t, dur))
            t += dur
            pause = gap if i < len(items) - 1 else tail
            o.writeframes(b"\x00" * 4 * int(pause * rate))
            t += pause
    return times, t


def mix_music(voice_wav, out, total):
    music = P(CONFIG["music_file"])
    if not os.path.isfile(music):
        run_ff(["-i", voice_wav, "-c:a", "pcm_s16le", out])
        return
    v = CONFIG["music_volume"]
    fo = max(0.0, total - 3)
    run_ff(["-i", voice_wav, "-stream_loop", "-1", "-i", music, "-filter_complex",
            f"[1:a]volume={v},afade=t=in:d=2[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"afade=t=out:st={fo:.2f}:d=3[a]",
            "-map", "[a]", "-ar", "44100", "-c:a", "pcm_s16le", out])


# =====================================================================
#  الصور والمقاطع
# =====================================================================
VID_EXT = (".mp4", ".mov", ".webm", ".mkv", ".gif")
WORK = None   # يتحدد بالـ main


IMAGE_MAP = {}


def _natural(path):
    name = os.path.basename(path).lower()
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", name)]


def assign_images(scenes):
    """الملفات المرقمة (5.png , 05b.jpg , 05.mp4) تروح لمشهدها.
    الملفات بدون رقم (مثل IMG_5710.jpg) تتوزع بالترتيب على المشاهد اللي ما الها صورة."""
    d = P(CONFIG["images_dir"])
    out = {}
    if not os.path.isdir(d):
        return out
    loose = []
    for f in sorted(os.listdir(d), key=lambda x: _natural(x)):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in IMG_EXT + VID_EXT:
            continue
        path = os.path.join(d, f)
        m = re.match(r"^0*(\d{1,3})(?:[ _\-.]?[a-zA-Z]{0,3}\d?)?$", stem)
        if m:
            out.setdefault(int(m.group(1)), []).append(path)
        else:
            loose.append(path)
    missing = [s["n"] for s in scenes if s["n"] not in out]
    for n, f in zip(missing, loose):
        out[n] = [f]
        log(f"  scene {n} <- {os.path.basename(f)}")
    if len(loose) > len(missing):
        log(f"  ! {len(loose) - len(missing)} extra image(s) not used")
    return out


def scene_images(n):
    return IMAGE_MAP.get(n, [])


def placeholder(n, size=(1920, 1080)):
    w, h = size
    y = np.linspace(0, 1, h)[:, None, None]
    top, bot = np.array([12, 20, 48]), np.array([150, 90, 30])
    arr = (top * (1 - y) + bot * y).repeat(w, axis=1).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(img)
    f = font(110)
    t = f"المشهد {n}"
    draw_txt(d, ((w - text_w(t, f)) / 2, h / 2 - 90), t, f, fill=(255, 230, 170))
    f2 = font(40)
    t2 = "صورة هذا المشهد غير موجودة بعد"
    draw_txt(d, ((w - text_w(t2, f2)) / 2, h / 2 + 60), t2, f2, fill=(230, 230, 230))
    return img


def cover(img, size):
    W, H = size
    s = max(W / img.width, H / img.height)
    img = img.resize((max(W, round(img.width * s)), max(H, round(img.height * s))), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


class KenBurns:
    """صورة ثابتة بحركة كاميرا: زوم وانزلاق بطيء"""
    def __init__(self, img, size, idx):
        self.W, self.H = size
        self.base = cover(img, (int(self.W * 1.15), int(self.H * 1.15)))
        self.zoom_in = idx % 2 == 0
        dirs = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)]
        self.pan = dirs[idx % len(dirs)]

    def frame(self, local, dur):
        p = max(0.0, min(1.0, local / max(0.001, dur)))
        e = p * p * (3 - 2 * p)
        z = 1 + CONFIG["zoom"] * (e if self.zoom_in else 1 - e)
        bw, bh = self.base.size
        cw, ch = bw / z, bh / z
        mx, my = (bw - cw) / 2, (bh - ch) / 2
        k = (e - 0.5) * 0.8
        cx = bw / 2 + self.pan[0] * mx * k
        cy = bh / 2 + self.pan[1] * my * k
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return self.base.resize((self.W, self.H), Image.BILINEAR, box=box)


class ClipSource:
    """مقطع فيديو قصير للمشهد (من موقع تحريك الصور) - يعيد نفسه اذا كان اقصر من المشهد"""
    def __init__(self, path, size, fps):
        self.W, self.H = size
        self.fps = fps
        sig = f"{path}|{os.path.getmtime(path)}|{self.W}x{self.H}|{fps}"
        key = hashlib.md5(sig.encode()).hexdigest()[:12]
        self.path = os.path.join(WORK, f"clip_{key}.mp4")
        if not os.path.isfile(self.path):
            log(f"  preparing clip {os.path.basename(path)}")
            run_ff(["-i", path, "-an", "-vf",
                    f"scale={self.W}:{self.H}:force_original_aspect_ratio=increase,"
                    f"crop={self.W}:{self.H},fps={fps},setsar=1",
                    "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", self.path])
        self.gen, self.idx, self.n, self.last = None, -1, None, None
        self.base = self._grab_first()

    def _grab_first(self):
        g = imageio_ffmpeg.read_frames(self.path)
        next(g)
        img = Image.frombytes("RGB", (self.W, self.H), next(g))
        g.close()
        return img

    def _open(self):
        if self.gen is not None:
            self.gen.close()
        self.gen = imageio_ffmpeg.read_frames(self.path)
        next(self.gen)
        self.idx = -1

    def frame(self, local, dur):
        target = max(0, int(local * self.fps))
        if self.n:
            target %= self.n
        if self.gen is None or target < self.idx:
            self._open()
        while self.idx < target:
            try:
                data = next(self.gen)
            except StopIteration:
                self.n = max(1, self.idx + 1)
                target %= self.n
                self._open()
                continue
            self.idx += 1
            if self.idx >= target:
                self.last = Image.frombytes("RGB", (self.W, self.H), data)
        return self.last.copy() if self.last else self.base.copy()


def make_source(path, img, size, idx, fps):
    if path and path.lower().endswith(VID_EXT):
        return ClipSource(path, size, fps)
    return KenBurns(img, size, idx)


# =====================================================================
#  المؤثرات: غبار، لمعان، مطر، ثلج، ضباب، فانوس
# =====================================================================
FX_WORDS = {"غبار": "dust", "لمعان": "sparkle", "مطر": "rain", "ثلج": "snow",
            "ضباب": "fog", "فانوس": "flicker", "بدون": "none"}


def parse_fx(text):
    out = []
    for w in re.split(r"[\s،,+]+", text or ""):
        w = w.strip().lower()
        if w:
            out.append(FX_WORDS.get(w, w))
    return out


def _glow(radius, color, alpha):
    d = radius * 2 + 1
    yy, xx = np.mgrid[0:d, 0:d]
    r = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2) / max(1, radius)
    a = (np.clip(np.clip(1 - r, 0, 1) ** 1.6 * 1.25, 0, 1) * 255 * alpha).astype(np.uint8)
    img = Image.new("RGBA", (d, d), color + (0,))
    img.putalpha(Image.fromarray(a, "L"))
    return img


def _streak(length, color, alpha, width):
    dx = int(length * 0.25)
    img = Image.new("RGBA", (dx + 4, length + 4), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(dx + 2, 2), (2, length + 2)], fill=color + (int(255 * alpha),), width=width)
    return img


class Effects:
    def __init__(self, kinds, size, seed=1):
        self.kinds = set(k for k in kinds if k != "none")
        self.W, self.H = W, H = size
        rng = random.Random(seed)
        sc = H / 1080
        self.parts = []
        if self.kinds & {"dust", "sparkle"}:
            sparkle = "sparkle" in self.kinds
            col = (190, 225, 255) if sparkle else (255, 214, 150)
            sprites = [[_glow(max(2, int(r * sc)), col, a) for a in (0.25, 0.5, 0.75, 1.0)]
                       for r in ((5, 8, 12, 17) if sparkle else (4, 6, 9, 12))]
            for _ in range(int(80 * (W * H) / (1920 * 1080)) + 20):
                self.parts.append(("glow", rng.random() * W, rng.random() * H,
                                   rng.uniform(-12, 12) * sc, rng.uniform(-22, -5) * sc,
                                   rng.choice(sprites), rng.random() * 6.28,
                                   rng.uniform(1.5, 4.0) if sparkle else rng.uniform(0.4, 1.2)))
        if "snow" in self.kinds:
            sprites = [[_glow(max(2, int(r * sc)), (255, 255, 255), a) for a in (0.5, 0.7, 0.85, 1.0)]
                       for r in (3, 5, 7)]
            for _ in range(int(110 * (W * H) / (1920 * 1080)) + 20):
                self.parts.append(("snow", rng.random() * W, rng.random() * H,
                                   0, rng.uniform(40, 110) * sc, rng.choice(sprites),
                                   rng.random() * 6.28, rng.uniform(0.5, 1.5)))
        if "rain" in self.kinds:
            sprites = [[_streak(int(L * sc), (210, 220, 240), a, max(1, int(2 * sc)))]
                       for L in (40, 60, 80) for a in (0.25, 0.4)]
            for _ in range(int(170 * (W * H) / (1920 * 1080)) + 30):
                vy = rng.uniform(1100, 1600) * sc
                self.parts.append(("rain", rng.random() * W, rng.random() * H,
                                   -vy * 0.25, vy, rng.choice(sprites), 0, 0))
        self.fog = None
        if "fog" in self.kinds:
            small = np.array([[rng.random() for _ in range(9)] for _ in range(5)]) * 255
            tex = Image.fromarray(small.astype(np.uint8), "L").resize(
                (int(W * 1.4), H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(int(40 * sc)))
            self.fog = tex.point(lambda v: int(v * 0.42))
            self.fog_col = Image.new("RGB", (W, H), (205, 215, 230))
        self.vignette = None
        if CONFIG.get("vignette", True):
            yy, xx = np.mgrid[0:H, 0:W]
            r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
            v = (255 * (1 - 0.38 * np.clip(r - 0.45, 0, 1) ** 1.4)).astype(np.uint8)
            self.vignette = Image.fromarray(np.dstack([v, v, v]), "RGB")

    def apply(self, fr, t):
        W, H = self.W, self.H
        if self.fog is not None:
            off = int((self.fog.width - W) * (0.5 + 0.5 * math.sin(t * 0.07)))
            fr = Image.composite(self.fog_col, fr, self.fog.crop((off, 0, off + W, H)))
        for kind, x0, y0, vx, vy, spr, ph, tw in self.parts:
            if kind == "snow":
                x = (x0 + 25 * math.sin(t * tw + ph)) % (W + 40) - 20
            else:
                x = (x0 + vx * t) % (W + 60) - 30
            y = (y0 + vy * t) % (H + 60) - 30
            if kind in ("glow", "snow"):
                a = 0.5 + 0.5 * math.sin(ph + t * tw)
                img = spr[min(len(spr) - 1, int(a * len(spr)))]
            else:
                img = spr[0]
            fr.paste(img, (int(x), int(y)), img)
        if "flicker" in self.kinds:
            f = 1 + 0.045 * math.sin(t * 7.3) + 0.025 * math.sin(t * 13.7 + 1.3)
            lut = [min(255, int(i * f)) for i in range(256)]
            fr = fr.point(lut * 3)
        if self.vignette is not None:
            fr = ImageChops.multiply(fr, self.vignette)
        return fr


# =====================================================================
#  بناء الفيديو
# =====================================================================
def build_timeline(scenes, times, size, fps):
    segs, caps = [], []
    idx = 0
    default_fx = CONFIG.get("effects", "dust")
    if isinstance(default_fx, str):
        default_fx = parse_fx(default_fx)
    fx_cache = {}
    for sc, (st, dur) in zip(scenes, times):
        files = scene_images(sc["n"])
        if not files:
            log(f"  ! no image for scene {sc['n']} - using placeholder")
        items = files or [None]
        span = dur + CONFIG["scene_gap"]
        part = span / len(items)
        kinds = tuple(sorted(sc.get("fx") or default_fx))
        if kinds not in fx_cache:
            fx_cache[kinds] = Effects(kinds, size, seed=len(fx_cache) + 7) if kinds else None
        for k, f in enumerate(items):
            img = None
            if f is None:
                img = placeholder(sc["n"])
            elif not f.lower().endswith(VID_EXT):
                img = Image.open(f).convert("RGB")
            segs.append({"src": make_source(f, img, size, idx, fps), "path": f, "img": img,
                         "idx": idx, "fx": fx_cache[kinds],
                         "start": st + k * part, "end": st + (k + 1) * part})
            idx += 1
        if CONFIG["subtitles"]:
            pieces = split_captions(sc["text"], 50 if size[1] > size[0] else 85)
            def weight(x):   # سكتة الوقفة = 3 ثواني تقريباً من الكلام
                return 40 if x == "فكّر بالجواب..." else len(x)
            total_chars = sum(weight(x) for x in pieces) or 1
            t = st
            for x in pieces:
                d = dur * weight(x) / total_chars
                caps.append({"text": x, "start": t, "end": t + d})
                t += d
    if segs:
        segs[0]["start"] = 0.0
    for a, b in zip(segs, segs[1:]):
        a["end"] = b["start"]
    return segs, caps


def render(path, size, segs, caps, audio, total, fps, layout="wide"):
    W, H = size
    cf = CONFIG["crossfade"]
    nframes = int(total * fps)
    short = layout == "short"

    if short:
        cap_font = font(int(W * 0.058))
        cap_max = W * 0.82
    else:
        cap_font = font(int(H * 0.048))
        cap_max = W * 0.72
    for c in caps:
        c["img"] = render_caption(c["text"], cap_font, cap_max)

    if short:
        img_w = W
        img_h = round(W * 9 / 16)
        img_y = int(H * 0.30)
        title_img = render_caption(CONFIG["channel_name"], font(int(W * 0.075)), W * 0.9, bg_alpha=0)
        end_img = render_caption(CONFIG["short_end_text"], font(int(W * 0.06)), W * 0.9, bg_alpha=190)
        for s in segs:
            bg = cover(s["src"].base, (W, H)).filter(ImageFilter.GaussianBlur(28))
            s["bg"] = Image.blend(bg, Image.new("RGB", (W, H)), 0.45)
            s["small"] = make_source(s["path"], s["img"] or s["src"].base, (img_w, img_h), s["idx"], fps)
            if s["fx"] is not None:
                s["fx_small"] = Effects(tuple(s["fx"].kinds) or ("none",), (img_w, img_h), seed=s["idx"])

    def seg_frame(s, t):
        local = max(0.0, t - s["start"])
        dur = s["end"] - s["start"] + cf
        if not short:
            fr = s["src"].frame(local, dur)
            return s["fx"].apply(fr, t) if s["fx"] else fr
        fr = s["bg"].copy()
        im = s["small"].frame(local, dur)
        if s.get("fx_small"):
            im = s["fx_small"].apply(im, t)
        fr.paste(im, (0, img_y))
        return fr

    writer = imageio_ffmpeg.write_frames(
        path, (W, H), fps=fps, codec="libx264", quality=CONFIG["quality"],
        macro_block_size=1, audio_path=audio, audio_codec="aac",
        ffmpeg_log_level="error",
        output_params=["-shortest", "-movflags", "+faststart"])
    writer.send(None)

    si, ci = 0, 0
    black = Image.new("RGB", (W, H))
    last_pct = -1
    for i in range(nframes):
        t = i / fps
        while si < len(segs) - 1 and t >= segs[si]["end"]:
            si += 1
        s = segs[si]
        fr = seg_frame(s, t)
        if si < len(segs) - 1 and t > s["end"] - cf:
            a = (t - (s["end"] - cf)) / cf
            fr = Image.blend(fr, seg_frame(segs[si + 1], t), a * a * (3 - 2 * a))

        if short:
            fr.paste(title_img, ((W - title_img.width) // 2, int(H * 0.12)), title_img)

        while ci < len(caps) and t >= caps[ci]["end"]:
            ci += 1
        if ci < len(caps) and caps[ci]["start"] <= t:
            c = caps[ci]["img"]
            y = (img_y + img_h + int(H * 0.04)) if short else (H - c.height - int(H * 0.06))
            fr.paste(c, ((W - c.width) // 2, y), c)

        if short and t > total - 3.5:
            fr.paste(end_img, ((W - end_img.width) // 2, int(H * 0.80)), end_img)

        if t < 1.0:
            fr = Image.blend(black, fr, t / 1.0)
        elif t > total - 1.2:
            fr = Image.blend(black, fr, max(0.0, (total - t) / 1.2))

        writer.send(fr.tobytes())
        pct = int(i * 100 / nframes)
        if pct != last_pct and pct % 5 == 0:
            log(f"  rendering {os.path.basename(path)}: {pct}%")
            last_pct = pct
    writer.close()
    log(f"  done -> {path}")


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|]+', "", s).strip()
    return s[:60] or "story"


# =====================================================================
def main():
    global FONT_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("story", nargs="?", default=None, help="story.txt or an episode folder")
    ap.add_argument("--story", dest="story_opt", default=None)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--no-shorts", action="store_true")
    ap.add_argument("--shorts-only", action="store_true")
    ap.add_argument("--silent", action="store_true")
    argv = []
    for x in sys.argv[1:]:          # النسخ من الموبايل يحول -- الى شرطة طويلة
        x = re.sub(r"^[\u2010-\u2015\u2212]+", "--", x)
        if re.match(r"^-[a-z]", x):
            x = "-" + x
        argv.append(x)
    a = ap.parse_args(argv)

    load_settings()
    a.story = (a.story or a.story_opt or CONFIG["story_file"]).strip('"')
    FONT_PATH = find_font()
    work, outd = P(CONFIG["work_dir"]), P(CONFIG["output_dir"])
    os.makedirs(work, exist_ok=True)
    os.makedirs(outd, exist_ok=True)

    story_path = a.story if os.path.isabs(a.story) else P(a.story)
    if os.path.isdir(story_path):          # مجلد حلقة: story.txt + images بداخله
        ep = story_path
        story_path = os.path.join(ep, "story.txt")
        if not os.path.isfile(story_path) and os.path.isfile(P("story.txt")):
            story_path = P("story.txt")      # القصة مرفوعة بالصفحة الرئيسية
            log("Using story.txt from the main folder")
        CONFIG["images_dir"] = os.path.join(ep, "images")
        work = os.path.join(ep, "work")
        os.makedirs(work, exist_ok=True)
        os.makedirs(CONFIG["images_dir"], exist_ok=True)
    global WORK
    WORK = work
    title, scenes = parse_story(story_path)
    global IMAGE_MAP
    IMAGE_MAP = assign_images(scenes)
    name = safe_name(title or os.path.splitext(os.path.basename(story_path))[0])
    chars = sum(len(x["text"]) for x in scenes)
    log(f"Voice: {'silent' if a.silent else CONFIG['voice_engine']} | {chars} characters")
    log(f"Story: {len(scenes)} scenes | font: {os.path.basename(FONT_PATH)} | raqm: {RAQM}")

    log("1) Voice...")
    wavs = {}
    for sc in scenes:
        log(f"  scene {sc['n']}")
        wavs[sc["n"]] = make_voice(sc, work, silent=a.silent)

    size = CONFIG["size"]
    ssize = CONFIG["short_size"]
    if a.preview:
        size = (size[0] // 2, size[1] // 2)
        ssize = (ssize[0] // 2, ssize[1] // 2)
    tag = "_preview" if a.preview else ""
    fps = 15 if a.preview else CONFIG["fps"]

    if not a.shorts_only:
        log("2) Main video...")
        vwav = os.path.join(work, "narration.wav")
        times, total = concat_wavs([wavs[s["n"]] for s in scenes], vwav,
                                   CONFIG["lead_in"], CONFIG["scene_gap"], CONFIG["tail"])
        fwav = os.path.join(work, "final_audio.wav")
        mix_music(vwav, fwav, total)
        segs, caps = build_timeline(scenes, times, size, fps)
        render(os.path.join(outd, f"{name}{tag}.mp4"), size, segs, caps, fwav, total, fps)
        log(f"  length: {int(total // 60)}:{int(total % 60):02d}")

    if not a.no_shorts:
        log("3) Shorts...")
        by_n = {s["n"]: s for s in scenes}
        for k, group in enumerate(CONFIG["shorts"], 1):
            sel = [by_n[n] for n in group if n in by_n]
            if not sel:
                continue
            swav = os.path.join(work, f"short_{k}.wav")
            times, total = concat_wavs([wavs[s["n"]] for s in sel], swav, 0.5, 0.5, 3.5)
            if total > 179:
                log(f"  ! short {k} is {total:.0f}s (YouTube Shorts max is 3 min)")
            fwav = os.path.join(work, f"short_{k}_final.wav")
            mix_music(swav, fwav, total)
            segs, caps = build_timeline(sel, times, ssize, fps)
            render(os.path.join(outd, f"{name}_short{k}{tag}.mp4"), ssize, segs, caps,
                   fwav, total, fps, layout="short")

    log("\nAll done! Files are in the 'output' folder.")


if __name__ == "__main__":
    main()
