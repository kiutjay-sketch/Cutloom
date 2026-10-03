import os, re, sys, json, time, uuid, random, socket, zipfile, atexit, threading, subprocess
import requests, imageio_ffmpeg, uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
OUT, DATA = os.path.join(HERE, "outputs"), os.path.join(HERE, "data")
BGM_DIR, CUSTOM_BGM_DIR = os.path.join(HERE, "assets", "bgm"), os.path.join(DATA, "bgm_custom")
CUSTOM_CLIP_DIR = os.path.join(DATA, "clips_custom")
os.makedirs(OUT, exist_ok=True); os.makedirs(DATA, exist_ok=True); os.makedirs(CUSTOM_BGM_DIR, exist_ok=True); os.makedirs(CUSTOM_CLIP_DIR, exist_ok=True)
FF, SET, JOBS = imageio_ffmpeg.get_ffmpeg_exe(), os.path.join(DATA, "settings.json"), {}
D3 = os.path.join(HERE, "3d")
PY3 = os.path.join(D3, "env", "Scripts", "python.exe") if os.name == "nt" else os.path.join(D3, "env", "bin", "python")
app = FastAPI(title="Cutloom")

def has3d(): return os.path.exists(PY3) and os.path.exists(os.path.join(D3, "TripoSR"))

def start3d():
    if has3d():  # the optional 3D module runs as its own small server; its output goes to a log so the Studio address stays first
        p = subprocess.Popen([PY3, "main3d.py"], cwd=D3, stdout=open(os.path.join(DATA, "3d.log"), "w"), stderr=subprocess.STDOUT)
        atexit.register(p.terminate)

def keys():
    try: return json.load(open(SET))
    except Exception: return {}

CFG = json.load(open(os.path.join(HERE, "license_config.json")))  # product_id + buy_url; set these before selling
ULT = json.load(open(os.path.join(HERE, "ultimate_config.json")))  # Cutloom Ultimate: separate product_id/buy_url, plus api_base pointing at your deployed Ultimate server
LIC, DAY = os.path.join(DATA, "license.json"), 86400

def lic_load():
    try: return json.load(open(LIC))
    except Exception: return {}

def ls(path, **data):
    r = requests.post("https://api.lemonsqueezy.com/v1/licenses/" + path, data=data, headers={"Accept": "application/json"}, timeout=20)
    return r.json()

def right_product(d): return str(d.get("meta", {}).get("product_id")) == str(CFG.get("product_id"))

def is_pro():
    if not CFG.get("product_id"): return True  # licensing not configured yet: test mode, everything unlocked
    L = lic_load()
    if not L.get("key"): return False
    if time.time() - L.get("checked", 0) > 3 * DAY:  # re-check online every few days, with 14 days of offline grace
        try:
            d = ls("validate", license_key=L["key"], instance_id=L["instance"])
            if d.get("valid") and right_product(d): L["checked"] = time.time()
            else: L["key"] = ""
            json.dump(L, open(LIC, "w"))
        except Exception:
            pass
        if L.get("key") and time.time() - L.get("checked", 0) > 14 * DAY: return False
    return bool(L.get("key"))

class LicReq(BaseModel):
    key: str

@app.get("/api/license")
def lic_status():
    return {"pro": is_pro(), "test": not CFG.get("product_id"), "buy_url": CFG.get("buy_url", "")}

@app.post("/api/license")
def lic_activate(r: LicReq):
    if not CFG.get("product_id"): return lic_status()
    d = ls("activate", license_key=r.key.strip(), instance_name=socket.gethostname())
    if not d.get("activated") or not right_product(d): raise HTTPException(400, d.get("error") or "That key is not valid for Cutloom.")
    json.dump({"key": r.key.strip(), "instance": d["instance"]["id"], "checked": time.time()}, open(LIC, "w"))
    return lic_status()

@app.post("/api/license/deactivate")
def lic_off():
    L = lic_load()
    if L.get("key"):
        try: ls("deactivate", license_key=L["key"], instance_id=L["instance"])
        except Exception: pass
    if os.path.exists(LIC): os.remove(LIC)
    return lic_status()

@app.exception_handler(requests.RequestException)
def _net(request, exc):
    body = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    return JSONResponse({"detail": "The AI service said: " + body[:300]}, status_code=502)

class Keys(BaseModel):
    openai: str = ""
    anthropic: str = ""
    pexels: str = ""

@app.get("/api/settings")
def get_settings():
    k = keys(); return {n: bool(k.get(n)) for n in ("openai", "anthropic", "pexels")}

@app.post("/api/settings")
def set_settings(s: Keys):
    k = keys(); k.update({n: v.strip() for n, v in dict(s).items() if v.strip()})
    json.dump(k, open(SET, "w")); return get_settings()

MODELS = {  # (anthropic model, openai model) per quality tier
    "standard": ("claude-haiku-4-5-20251001", "gpt-4o-mini"),
    "better": ("claude-sonnet-5", "gpt-4o"),
}

def llm(prompt, quality="standard"):
    k, sysm = keys(), "You write short-video content. Reply with valid JSON only, no commentary."
    a_model, o_model = MODELS.get(quality, MODELS["standard"])
    if k.get("anthropic"):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
            headers={"x-api-key": k["anthropic"], "anthropic-version": "2023-06-01"},
            json={"model": a_model, "max_tokens": 2000, "system": sysm, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status(); t = r.json()["content"][0]["text"]
    elif k.get("openai"):
        r = requests.post("https://api.openai.com/v1/chat/completions", timeout=90, headers={"Authorization": "Bearer " + k["openai"]},
            json={"model": o_model, "messages": [{"role": "system", "content": sysm}, {"role": "user", "content": prompt}]})
        r.raise_for_status(); t = r.json()["choices"][0]["message"]["content"]
    else:
        raise HTTPException(400, "Add an Anthropic or OpenAI key in Settings first, or use \"Write my own script\", which needs no key.")
    try: return json.loads(re.search(r"[\[{].*[\]}]", t, re.S).group(0))
    except Exception: raise HTTPException(502, "The AI reply could not be read. Please try again.")

class TopicReq(BaseModel):
    niche: str

@app.post("/api/topics")
def topics(r: TopicReq):
    return llm(f"Give 10 specific, fresh short-video topic ideas for the niche: {r.niche}. Each must be a concrete angle, not a generic subject. Return a JSON array of strings.")

class ScriptReq(BaseModel):
    topic: str
    niche: str = ""
    seconds: int = 30
    tone: str = "engaging"
    notes: str = ""
    language: str = "English"
    script_quality: str = "standard"

@app.post("/api/script")
def script(r: ScriptReq):
    pro = is_pro()
    if r.seconds != 15 and not pro: raise HTTPException(402, "30s and 60s videos are part of Cutloom Pro. Add your license key in Settings, or use 15s.")
    if r.script_quality != "standard" and not pro: raise HTTPException(402, "Better script quality is part of Cutloom Pro. Add your license key in Settings, or use Standard quality.")
    n, w = {15: 3, 30: 5, 60: 8}.get(r.seconds, 5), int(r.seconds * 2.5)
    return llm(f"""Write a {r.seconds}-second vertical short-video script in {r.language}.
Topic: {r.topic}. Niche: {r.niche or 'general'}. Tone: {r.tone}.
The creator's own notes and facts to include: {r.notes or 'none'}.
Rules: about {w} spoken words in total; open with a strong hook; end with a short call to action; be specific and original; never invent statistics or quotes.
Return JSON: {{"title": str, "scenes": [{{"text": "spoken line", "keywords": "2-3 words describing stock footage to show"}}] (exactly {n} scenes), "description": str, "hashtags": [str]}}""", r.script_quality)

FORMATS = {"vertical": (9, 16), "horizontal": (16, 9), "square": (1, 1)}

class Render(BaseModel):
    title: str = "short"
    scenes: list[dict]
    voice: str = "alloy"
    speed: float = 1.0
    quality: str = "fast"
    format: str = "vertical"
    width: int = 0
    height: int = 0
    caption: str = "white"
    bgm: str = "none"
    music_volume: float = 0.18
    description: str = ""
    hashtags: list[str] = []

PRESETS = {"calm": "A soft, slow pad for calm or informative videos", "upbeat": "A pulsing beat for energetic or funny videos",
           "dramatic": "A deep, swelling tone for serious or dramatic videos", "hiphop": "A punchy, bass-driven pulse for hip hop style videos",
           "rnb": "A warm, smooth groove for R&B style videos", "jazz": "A mellow, softly swung pad for jazz style videos",
           "pop": "A bright, upbeat pulse for pop style videos"}

def bgm_path(bgm):
    if not bgm or bgm == "none": return None
    if bgm in PRESETS:
        p = os.path.join(BGM_DIR, bgm + ".mp3")
        return p if os.path.exists(p) else None
    if bgm.startswith("custom:"):
        p = os.path.join(CUSTOM_BGM_DIR, re.sub(r"[^a-f0-9]", "", bgm[7:]) + ".mp3")
        if not os.path.exists(p): raise HTTPException(400, "That uploaded music track was not found. Upload it again.")
        return p
    return None

DISPLAY_NAME = {"hiphop": "Hip Hop", "rnb": "R&B"}

@app.get("/api/bgm")
def bgm_list():
    return {"presets": [{"id": k, "name": DISPLAY_NAME.get(k, k.title()), "description": v} for k, v in PRESETS.items() if os.path.exists(os.path.join(BGM_DIR, k + ".mp3"))]}

@app.post("/api/bgm/upload")
def bgm_upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".mp3", ".wav", ".m4a")): raise HTTPException(400, "Please upload an MP3, WAV or M4A file.")
    data = file.file.read()
    if len(data) > 20 * 1024 * 1024: raise HTTPException(400, "That file is over 20MB. Please use a shorter or more compressed track.")
    bid = uuid.uuid4().hex
    raw = os.path.join(CUSTOM_BGM_DIR, bid + "_raw" + os.path.splitext(file.filename)[1])
    open(raw, "wb").write(data)
    out = os.path.join(CUSTOM_BGM_DIR, bid + ".mp3")
    try:
        ff(["-i", os.path.abspath(raw), "-ac", "1", "-b:a", "96k", os.path.abspath(out)], CUSTOM_BGM_DIR)
    finally:
        if raw != out and os.path.exists(raw): os.remove(raw)
    return {"id": "custom:" + bid, "name": file.filename}

def clip_path(clip):
    if not clip or not clip.startswith("custom:"): return None
    p = os.path.join(CUSTOM_CLIP_DIR, re.sub(r"[^a-f0-9]", "", clip[7:]) + ".mp4")
    if not os.path.exists(p): raise HTTPException(400, "That uploaded clip was not found. Upload it again.")
    return p

@app.post("/api/clip/upload")
def clip_upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".mp4", ".mov", ".webm", ".mkv")): raise HTTPException(400, "Please upload an MP4, MOV, WEBM or MKV file.")
    data = file.file.read()
    if len(data) > 150 * 1024 * 1024: raise HTTPException(400, "That file is over 150MB. Please use a shorter or more compressed clip.")
    cid = uuid.uuid4().hex
    raw = os.path.join(CUSTOM_CLIP_DIR, cid + "_raw" + os.path.splitext(file.filename)[1])
    open(raw, "wb").write(data)
    out = os.path.join(CUSTOM_CLIP_DIR, cid + ".mp4")
    try:
        ff(["-i", os.path.abspath(raw), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", os.path.abspath(out)], CUSTOM_CLIP_DIR)
    finally:
        if raw != out and os.path.exists(raw): os.remove(raw)
    return {"id": "custom:" + cid, "name": file.filename}

# --- Cutloom Ultimate: paid AI clip generation, proxied through a separate server you host ---
ULT_KEY_FILE = os.path.join(DATA, "ultimate_key.json")

def ult_key():
    try: return json.load(open(ULT_KEY_FILE)).get("key", "")
    except Exception: return ""

def ult_ready():
    return bool(ULT.get("api_base") and ULT.get("product_id"))

@app.get("/api/ultimate")
def ultimate_status():
    if not ult_ready(): return {"available": False}
    key = ult_key()
    credits = None
    if key:
        try:
            r = requests.post(ULT["api_base"] + "/api/credits", json={"license_key": key}, timeout=15)
            if r.ok: credits = r.json().get("credits")
        except Exception: pass
    return {"available": True, "has_key": bool(key), "credits": credits, "buy_url": ULT.get("buy_url", "")}

class UltKeyReq(BaseModel):
    key: str

@app.post("/api/ultimate/key")
def ultimate_set_key(r: UltKeyReq):
    json.dump({"key": r.key.strip()}, open(ULT_KEY_FILE, "w"))
    return ultimate_status()

class UltGenReq(BaseModel):
    prompt: str
    aspect_ratio: str = "9:16"

@app.post("/api/ultimate/generate")
def ultimate_generate(r: UltGenReq):
    if not ult_ready(): raise HTTPException(400, "Cutloom Ultimate is not set up yet.")
    key = ult_key()
    if not key: raise HTTPException(400, "Add your Cutloom Ultimate license key in Settings first.")
    try:
        resp = requests.post(ULT["api_base"] + "/api/generate", json={"license_key": key, "prompt": r.prompt, "aspect_ratio": r.aspect_ratio}, timeout=30)
    except requests.RequestException:
        raise HTTPException(502, "Could not reach the Cutloom Ultimate server. Try again in a moment.")
    if not resp.ok:
        try: why = (resp.json() or {}).get("detail")
        except Exception: why = None
        raise HTTPException(resp.status_code, why or "The Ultimate server rejected the request.")
    return resp.json()

@app.get("/api/ultimate/generate/{job_id}")
def ultimate_job(job_id: str):
    if not ult_ready(): raise HTTPException(400, "Cutloom Ultimate is not set up yet.")
    try:
        resp = requests.get(ULT["api_base"] + f"/api/generate/{job_id}", timeout=15)
    except requests.RequestException:
        raise HTTPException(502, "Could not reach the Cutloom Ultimate server.")
    try: j = resp.json()
    except Exception: raise HTTPException(502, "The Ultimate server sent an unreadable reply.")
    if not resp.ok: raise HTTPException(resp.status_code, j.get("detail") or "That AI clip job was not found.")
    if j.get("done") and j.get("video_url") and not j.get("error"):
        # Pull the finished clip down and register it exactly like an uploaded custom clip,
        # so the rest of the render pipeline (already tested) needs no changes at all.
        cid = uuid.uuid4().hex
        out = os.path.join(CUSTOM_CLIP_DIR, cid + ".mp4")
        saved = False
        for _ in range(3):
            try:
                v = requests.get(j["video_url"], timeout=120)
                if v.content: open(out, "wb").write(v.content); saved = True; break
            except Exception:
                time.sleep(2)
        if not saved:
            return {"done": True, "error": "The clip finished but could not be downloaded to this PC. Please contact support so your credit can be restored."}
        j["clip_id"] = "custom:" + cid
    return j

def frame_size(r):
    short = 720 if r.quality == "fast" else 1080
    if r.format == "custom":
        w = max(240, min(2160, int(r.width) or 1080)) // 2 * 2
        h = max(240, min(2160, int(r.height) or 1920)) // 2 * 2
        return w, h
    aw, ah = FORMATS.get(r.format, FORMATS["vertical"])
    if aw >= ah: return round(short * aw / ah / 2) * 2, short  # wide: fix height, scale width
    return short, round(short * ah / aw / 2) * 2               # tall or square: fix width, scale height

def ff(args, cwd):
    p = subprocess.run([FF, "-y", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode: raise RuntimeError(p.stderr[-500:])

def duration(path):
    p = subprocess.run([FF, "-i", path], capture_output=True, text=True)
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)

def clip_orientation(W, H):
    if W > H * 1.08: return "landscape"
    if H > W * 1.08: return "portrait"
    return "square"

def file_matches(x, orient):
    xw, xh = x.get("width", 0), x.get("height", 0)
    if min(xw, xh) < 480: return False
    if orient == "landscape": return xw >= xh
    if orient == "portrait": return xh >= xw
    return abs(xw - xh) <= max(xw, xh) * 0.2  # roughly square

def get_clip(q, d, i, W, H, used, warn):
    key = keys().get("pexels")
    if not key: return None
    orient = clip_orientation(W, H)  # bug fix: this used to always request/accept portrait
                                      # clips, so horizontal and square videos got a portrait
                                      # clip stretched into the wrong shape, heavily cropped
                                      # and blurry. Now it asks Pexels for a clip shaped like
                                      # the video being made, and only accepts a matching shape.
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key}, timeout=30,
                         params={"query": q, "orientation": orient, "per_page": 8})
        r.raise_for_status(); vs = r.json().get("videos", [])
        # keep Pexels' own best-match order; only shuffle within the strongest few results,
        # so we still get some variety without trading relevance for a random pick
        best, rest = vs[:3], vs[3:]
        random.shuffle(best); vs = best + rest
        target = max(W, H)
        for v in vs:
            if v["id"] in used: continue
            f = [x for x in v["video_files"] if file_matches(x, orient)]
            if not f: continue
            f.sort(key=lambda x: abs(max(x["width"], x["height"]) - target)); used.add(v["id"])
            open(f"{d}/clip{i}.mp4", "wb").write(requests.get(f[0]["link"], timeout=120).content)
            return f"clip{i}.mp4"
    except Exception as e:
        if "footage" not in " ".join(warn): warn.append("Footage search failed (check your Pexels key); plain backgrounds were used.")
    return None

def ts_ass(t): return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"
def ts_srt(t):
    ms = int(round(t * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

def run(jid, r, pro=True):
    J, d, k, warn = JOBS[jid], os.path.join(OUT, jid), keys(), []
    os.makedirs(d, exist_ok=True)
    try:
        W, H = frame_size(r)
        J["step"] = "Recording the voiceover"
        text = " ".join(s["text"] for s in r.scenes)
        if r.voice.startswith("sys:"):  # free voice from this computer
            vfile = "voice.wav"; open(f"{d}/voice.txt", "w", encoding="utf-8").write(text)
            p = subprocess.run([sys.executable, os.path.join(HERE, "sys_tts.py"), "say", os.path.abspath(f"{d}/voice.txt"), os.path.abspath(f"{d}/{vfile}"), str(int(175 * r.speed)), r.voice[4:]],
                               capture_output=True, text=True, timeout=300)
            if p.returncode or not os.path.exists(f"{d}/{vfile}"):
                raise RuntimeError("The free voice could not be made. This feature needs Windows. " + (p.stderr or "")[-200:])
        else:
            vfile = "voice.mp3"
            v = requests.post("https://api.openai.com/v1/audio/speech", timeout=180, headers={"Authorization": "Bearer " + k.get("openai", "")},
                              json={"model": "tts-1", "voice": r.voice, "speed": r.speed, "input": text})
            v.raise_for_status(); open(f"{d}/{vfile}", "wb").write(v.content)
        D = duration(f"{d}/{vfile}"); wc = [max(1, len(s["text"].split())) for s in r.scenes]
        t0, used, caps, lst = 0.0, set(), [], []
        for i, (s, w) in enumerate(zip(r.scenes, wc)):
            sp = D * w / sum(wc); sd = sp + (0.6 if i == len(wc) - 1 else 0)
            J["step"] = f"Finding footage ({i + 1}/{len(wc)})"
            custom = clip_path(s.get("clip"))
            clip = os.path.abspath(custom) if custom else get_clip(s.get("keywords") or s["text"], d, i, W, H, used, warn)
            J["step"] = f"Building scene {i + 1}/{len(wc)}"
            src = ["-stream_loop", "-1", "-i", clip] if clip else ["-f", "lavfi", "-i", f"color=c=0x1d1442:s={W}x{H}:r=30"]
            ff([*src, "-t", f"{sd:.2f}", "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,setsar=1",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", f"seg{i}.mp4"], d)
            lst.append(f"file 'seg{i}.mp4'")
            ws = s["text"].split(); ch = [ws[j:j + 3] for j in range(0, len(ws), 3)]
            cw = [sum(len(x) + 1 for x in c) for c in ch]; a = t0
            for c, x in zip(ch, cw):
                b = a + sp * x / sum(cw); caps.append((a, b, " ".join(c))); a = b
            t0 += sd
        col = "&H0000FFFF" if r.caption == "yellow" else "&H00FFFFFF"
        ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n\n[V4+ Styles]\n"
               "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
               "Style: C,Arial,%d,%s,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,%d,1,2,60,60,%d,1\n\n[Events]\n"
               "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n") % (W, H, int(W * .009), col, 1, int(H * .05))
        ass += "".join(f"Dialogue: 0,{ts_ass(a)},{ts_ass(b)},C,,0,0,0,,{t.upper()}\n" for a, b, t in caps)
        if not pro:  # free version: small watermark for the whole video
            ass = ass.replace("\n[Events]", ("Style: W,Arial,%d,&H99FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,8,40,40,%d,1\n\n[Events]") % (int(W * .04), int(H * .05)))
            ass += f"Dialogue: 1,0:00:00.00,{ts_ass(t0)},W,,0,0,0,,Made with Cutloom (free version)\n"
        open(f"{d}/list.txt", "w").write("\n".join(lst))
        open(f"{d}/captions.ass", "w", encoding="utf-8").write(ass)
        open(f"{d}/captions.srt", "w", encoding="utf-8").write("".join(f"{n}\n{ts_srt(a)} --> {ts_srt(b)}\n{t}\n\n" for n, (a, b, t) in enumerate(caps, 1)))
        open(f"{d}/script.txt", "w", encoding="utf-8").write(f"{r.title}\n\n" + "\n".join(s["text"] for s in r.scenes) + f"\n\n{r.description}\n{' '.join(r.hashtags)}\n")
        J["step"] = "Rendering the final video (the slow part on older PCs)"
        music = bgm_path(getattr(r, "bgm", "none"))
        if music:
            J["step"] = "Mixing in music"
            vol = max(0.0, min(1.0, getattr(r, "music_volume", 0.18)))
            base = ["-f", "concat", "-safe", "0", "-i", "list.txt", "-i", vfile, "-stream_loop", "-1", "-i", os.path.abspath(music)]
            mix = f"[2:a]atrim=0:{D:.2f},volume={vol}[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]"
            enc = ["-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "video.mp4"]
        else:
            base = ["-f", "concat", "-safe", "0", "-i", "list.txt", "-i", vfile]
            enc = ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "video.mp4"]
        if r.caption == "none":
            ff([*base, *enc], d)
        else:
            try: ff([*base, "-vf", "subtitles=captions.ass", *enc], d)
            except RuntimeError:
                warn.append("Captions could not be burned in on this PC. Use the .srt file instead."); ff([*base, *enc], d)
        zp = f"cutloom-{jid}.zip"
        if pro:
            with zipfile.ZipFile(f"{d}/{zp}", "w") as z:
                for n in ("video.mp4", vfile, "script.txt", "captions.srt"): z.write(f"{d}/{n}", n)
        J.update(done=True, step="Done", video=f"/outputs/{jid}/video.mp4", zip=(f"/outputs/{jid}/{zp}" if pro else ""), warn=warn)
    except requests.exceptions.ConnectionError:
        J.update(done=True, error="Could not reach the AI service. Check that this PC has an internet connection (or try again if it just dropped), then click Make video again. If it keeps failing, pick a free voice instead, which needs no internet for the voice step.")
    except Exception as e:
        msg = e.response.text[:300] if isinstance(e, requests.HTTPError) and e.response is not None else str(e)[:400]
        J.update(done=True, error=msg)

def check_free_limits(r, pro):
    if not pro: r.quality = "fast"
    if not pro and sum(len(x.get("text", "").split()) for x in r.scenes) > 50: raise HTTPException(402, "The free version is limited to about 15 seconds (roughly 40 words). Shorten the script or upgrade to Pro.")
    if not pro and r.format != "vertical": raise HTTPException(402, "Horizontal, square and custom sizes are part of Cutloom Pro. Add your license key in Settings, or use the vertical 9:16 format.")
    if not pro and getattr(r, "bgm", "none") != "none": raise HTTPException(402, "Background music is part of Cutloom Pro. Add your license key in Settings, or turn music off.")
    if not pro and any(s.get("clip") for s in r.scenes): raise HTTPException(402, "Using your own clip for a scene is part of Cutloom Pro. Add your license key in Settings, or use matched stock footage.")
    if r.format not in FORMATS and r.format != "custom": raise HTTPException(400, "Unknown format.")

@app.post("/api/render")
def render(r: Render):
    if not r.voice.startswith("sys:") and not keys().get("openai"): raise HTTPException(400, "Pick a free voice, or add your OpenAI key in Settings for the natural AI voices.")
    pro = is_pro()
    check_free_limits(r, pro)
    jid = uuid.uuid4().hex[:8]; JOBS[jid] = {"step": "Starting", "done": False}
    threading.Thread(target=run, args=(jid, r, pro), daemon=True).start()
    return {"id": jid}

@app.get("/api/job/{jid}")
def job(jid: str):
    return JOBS.get(jid) or JSONResponse({"detail": "Unknown job"}, status_code=404)

class BatchReq(BaseModel):
    niche: str = ""
    tone: str = "engaging"
    seconds: int = 30
    language: str = "English"
    notes: str = ""
    script_quality: str = "standard"
    topics: list[str]
    voice: str = "alloy"
    speed: float = 1.0
    quality: str = "fast"
    format: str = "vertical"
    width: int = 0
    height: int = 0
    caption: str = "white"
    bgm: str = "none"
    music_volume: float = 0.18

BATCHES = {}

def safe_name(s, i):
    n = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40]
    return f"{i:02d}-{n or 'video'}"

def run_batch(bid, r):
    B, pro = BATCHES[bid], is_pro()
    for i, topic in enumerate(r.topics):
        item = B["items"][i]; item["step"] = "Writing script"
        try:
            sc = script(ScriptReq(topic=topic, niche=r.niche, seconds=r.seconds, tone=r.tone, notes=r.notes, language=r.language, script_quality=r.script_quality))
            item["title"] = sc.get("title") or topic
            rr = Render(title=item["title"], scenes=sc.get("scenes", []), voice=r.voice, speed=r.speed, quality=r.quality,
                       format=r.format, width=r.width, height=r.height, caption=r.caption, bgm=r.bgm, music_volume=r.music_volume,
                       description=sc.get("description", ""), hashtags=sc.get("hashtags", []))
            check_free_limits(rr, pro)
            if not rr.scenes: raise RuntimeError("The script came back empty.")
            jid = uuid.uuid4().hex[:8]; JOBS[jid] = {"step": "Starting", "done": False}
            item["jid"] = jid
            run(jid, rr, pro)  # sequential on purpose, so one PC isn't asked to render two videos at once
            j = JOBS[jid]
            if j.get("error"): item.update(done=True, error=j["error"], step="Failed")
            else: item.update(done=True, step="Done", video=j.get("video"))
        except Exception as e:
            item.update(done=True, error=str(e)[:300], step="Failed")
        B["progress"] = i + 1
    ok = [x for x in B["items"] if x.get("jid") and not x.get("error")]
    if ok:
        zp = os.path.join(OUT, f"batch-{bid}.zip")
        with zipfile.ZipFile(zp, "w") as z:
            for i, item in enumerate(ok, 1):
                jd = os.path.join(OUT, item["jid"]); name = safe_name(item["title"], i)
                z.write(os.path.join(jd, "video.mp4"), f"{name}.mp4")
                if os.path.exists(os.path.join(jd, "script.txt")): z.write(os.path.join(jd, "script.txt"), f"{name}.txt")
        B["zip"] = f"/outputs/batch-{bid}.zip"
    B["done"] = True

@app.post("/api/batch")
def batch(r: BatchReq):
    if not is_pro(): raise HTTPException(402, "Batch mode is part of Cutloom Pro. Add your license key in Settings, or make videos one at a time for free.")
    topics = [t.strip() for t in r.topics if t.strip()]
    if not topics: raise HTTPException(400, "Add at least one topic, one per line.")
    if len(topics) > 20: raise HTTPException(400, "Batch is limited to 20 topics at a time.")
    if not r.voice.startswith("sys:") and not keys().get("openai"): raise HTTPException(400, "Pick a free voice, or add your OpenAI key in Settings for the natural AI voices.")
    r.topics = topics
    bid = uuid.uuid4().hex[:8]
    BATCHES[bid] = {"items": [{"topic": t, "step": "Waiting", "done": False} for t in topics], "progress": 0, "total": len(topics), "done": False}
    threading.Thread(target=run_batch, args=(bid, r), daemon=True).start()
    return {"id": bid}

@app.get("/api/batch/{bid}")
def batch_status(bid: str):
    return BATCHES.get(bid) or JSONResponse({"detail": "Unknown batch"}, status_code=404)

STOP = set("about after again also because been before being both could does doing done each even from have here into just like made make many more most much must only other over said same should some such than that their them then there these they this those through very want were what when where which while will with would your".split())
VOICES_CACHE = []

def sys_voices():
    if not VOICES_CACHE:
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, "sys_tts.py"), "list"], capture_output=True, text=True, timeout=40)
            VOICES_CACHE.extend(json.loads(p.stdout.strip().splitlines()[-1]))
        except Exception:
            pass
    return VOICES_CACHE

@app.get("/api/voices")
def voices():
    return {"openai": ["alloy", "nova", "onyx", "echo", "fable", "shimmer"], "system": sys_voices()}

def make_scenes(text, context=""):
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if x.strip()]
    scenes, cur = [], []
    for x in sents:  # group sentences into scenes of roughly 10 to 16 words
        cur.append(x)
        if sum(len(y.split()) for y in cur) >= 10: scenes.append(" ".join(cur)); cur = []
    if cur:
        if scenes and sum(len(y.split()) for y in cur) < 6: scenes[-1] += " " + " ".join(cur)
        else: scenes.append(" ".join(cur))
    out = []
    for sc in scenes:
        # keep words in the order they appear, not sorted by length, so the query stays
        # a coherent phrase (e.g. "morning routine coffee") instead of picking odd long words
        ws = [w for w in re.findall(r"\w{4,}", sc.lower()) if w not in STOP]
        kw = list(dict.fromkeys(ws))[:3]
        if context: kw = [context.split()[0].lower()] + kw if context.split() else kw
        out.append({"text": sc, "keywords": " ".join(kw[:4]) or (context or "lifestyle")})
    return out

class SplitReq(BaseModel):
    text: str
    niche: str = ""

@app.post("/api/split")
def split(r: SplitReq):
    sc = make_scenes(r.text, r.niche)
    if not sc: raise HTTPException(400, "Paste your script first.")
    return {"title": sc[0]["text"][:60], "scenes": sc, "description": "", "hashtags": []}

@app.get("/api/3d")
def three_d():
    up = False
    if has3d():
        try: requests.get("http://127.0.0.1:7861/api/status", timeout=1); up = True
        except Exception: pass
    return {"installed": has3d(), "running": up, "url": "http://127.0.0.1:7861"}

app.mount("/outputs", StaticFiles(directory=OUT), name="outputs")

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))

if __name__ == "__main__":
    start3d()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 7862)))
