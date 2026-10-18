"""
Cutloom Ultimate: the paid AI backend (v2: points, images, animation, video).

This is a SEPARATE server from the Cutloom app. It must stay online on a host
you control, because it holds your fal.ai key. That key must never be shipped
inside the Cutloom app: anything inside a downloaded app can be extracted.

How it works
- A customer buys a points pack on Lemon Squeezy. Lemon Squeezy sends a signed
  webhook and points are added to that customer's EMAIL balance. Duplicate
  deliveries are ignored, and a refunded order takes its points back.
- In Cutloom the customer pastes their Ultimate license key. We ask Lemon
  Squeezy who owns the key, and use that email's balance.
- Four actions, each with a point cost you can change (POINT_COSTS):
    image         text -> still image          (default 1 point)
    animate_fast  image -> 5s clip, turbo      (default 4 points)
    animate       image -> 5s clip, standard   (default 16 points)
    video         text -> 5s clip              (default 20 points)
- A failed generation refunds exactly what it cost.

See README.md for setup. Configuration comes from environment variables.
"""
import os, re, json, time, hmac, hashlib, uuid, base64, threading, requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))


def _local():
    try: return json.load(open(os.path.join(HERE, "config.json")))
    except Exception: return {}


_LOCAL = _local()


def setting(name, default=None):
    """Environment variable first (the right way on a real host), then an
    optional local config.json (lower-case keys) for running on your own PC."""
    v = os.environ.get(name.upper())
    if v is not None and v.strip() != "": return v.strip()
    v = _LOCAL.get(name.lower())
    return default if v in (None, "") else v


def _json_setting(name):
    raw = setting(name)
    if not raw: return None
    try: return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        print(f"WARNING: {name.upper()} is not valid JSON; using the defaults.")
        return None


# Packs. The old "N clips" names still work so nothing breaks if a variant isn't renamed yet.
DEFAULT_VARIANTS = {"200 points": 200, "600 points": 600, "2000 points": 2000,
                    "10 clips": 200, "30 clips": 600, "100 clips": 2000}
DEFAULT_COSTS = {"image": 1, "animate_fast": 4, "animate": 16, "video": 20}


def _variants():
    d = _json_setting("variant_credits")
    if not d: return dict(DEFAULT_VARIANTS)
    try: return {str(k).strip().lower(): int(v) for k, v in d.items()}
    except Exception: return dict(DEFAULT_VARIANTS)


def _costs():
    costs = dict(DEFAULT_COSTS)
    d = _json_setting("point_costs")
    if d:
        for k, v in d.items():
            if k in costs:
                try: costs[k] = max(1, int(v))
                except Exception: pass
    return costs


CFG = {
    "fal_key": setting("fal_key"),
    "product_id": setting("lemonsqueezy_ultimate_product_id"),
    "webhook_secret": setting("lemonsqueezy_webhook_secret"),
    "admin_token": setting("admin_token"),
    "models": {
        "video": setting("fal_model_endpoint", "fal-ai/wan/v2.2-a14b/text-to-video"),
        "image": setting("fal_model_image", "fal-ai/wan/v2.2-a14b/text-to-image"),
        "animate": setting("fal_model_animate", "fal-ai/wan/v2.2-a14b/image-to-video"),
        "animate_fast": setting("fal_model_animate_fast", "fal-ai/wan/v2.2-a14b/image-to-video/turbo"),
    },
    "public_base_url": setting("public_base_url", "https://cutloom-ultimate.onrender.com").rstrip("/"),
    "data_dir": setting("data_dir", HERE),
    "variants": _variants(),
    "costs": _costs(),
    "max_pending": int(setting("max_pending_jobs", 3)),
    "max_per_hour": int(setting("max_jobs_per_hour", 60)),
}
os.makedirs(CFG["data_dir"], exist_ok=True)
DB_PATH = os.path.join(CFG["data_dir"], "ultimate_db.json")
TMP_DIR = os.path.join(CFG["data_dir"], "tmp")
os.makedirs(TMP_DIR, exist_ok=True)

ASPECTS = ("9:16", "16:9", "1:1")
IMAGE_SIZE = {"9:16": "portrait_16_9", "16:9": "landscape_16_9", "1:1": "square_hd"}
ANIMATE_DEFAULT_PROMPT = "gentle natural motion, subtle camera movement"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
JOBS = {}          # live job status for polling (in memory)
_lock = threading.RLock()
app = FastAPI(title="Cutloom Ultimate")


def log(*a): print("[ultimate]", *a, flush=True)


# ---------- storage (one JSON file, written atomically) ----------
def db_load():
    try: db = json.load(open(DB_PATH))
    except Exception: db = {}
    for k in ("customers", "orders", "jobs"): db.setdefault(k, {})
    return db


def db_save(db):
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w") as f: json.dump(db, f)
    os.replace(tmp, DB_PATH)


def credit(db, email, delta, reason, ref=""):
    c = db["customers"].setdefault(email, {"credits": 0, "history": []})
    c["credits"] = max(0, c["credits"] + delta)
    c["history"].append({"t": int(time.time()), "change": delta, "reason": reason, "ref": ref})
    c["history"] = c["history"][-200:]
    return c["credits"]


def balance(email):
    with _lock: return db_load()["customers"].get(email, {}).get("credits", 0)


# ---------- who owns a license key ----------
_KEY_CACHE = {}


def ls_validate(license_key):
    r = requests.post("https://api.lemonsqueezy.com/v1/licenses/validate", data={"license_key": license_key},
                      headers={"Accept": "application/json"}, timeout=20)
    return r.json()


def email_for_key(license_key):
    license_key = (license_key or "").strip()
    if not license_key: raise HTTPException(400, "Add your Cutloom Ultimate license key first.")
    hit = _KEY_CACHE.get(license_key)
    if hit and time.time() - hit[1] < 600: return hit[0]
    try: d = ls_validate(license_key)
    except Exception: raise HTTPException(503, "Could not check your license right now. Please try again in a moment.")
    meta = d.get("meta") or {}
    if not d.get("valid") or not CFG["product_id"] or str(meta.get("product_id")) != str(CFG["product_id"]):
        raise HTTPException(403, "That key is not a valid Cutloom Ultimate license.")
    email = (meta.get("customer_email") or "").strip().lower()
    if not email: raise HTTPException(403, "That license has no email on record. Please contact support.")
    _KEY_CACHE[license_key] = (email, time.time())
    return email


class KeyReq(BaseModel):
    license_key: str


@app.post("/api/credits")
def get_credits(r: KeyReq):
    pts = balance(email_for_key(r.license_key))
    return {"points": pts, "credits": pts, "costs": CFG["costs"]}


# ---------- temporary hosting of a customer's image (fal needs a URL) ----------
def _sniff(data: bytes):
    if data[:3] == b"\xff\xd8\xff": return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n": return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return ".webp"
    return None


def sweep_tmp(max_age=3600):
    now = time.time()
    try:
        for n in os.listdir(TMP_DIR):
            p = os.path.join(TMP_DIR, n)
            if now - os.path.getmtime(p) > max_age: os.remove(p)
    except Exception: pass


def save_tmp_image(b64):
    try: data = base64.b64decode(b64 or "", validate=True)
    except Exception: raise HTTPException(400, "That image could not be read. Try a JPG, PNG or WEBP file.")
    if not data: raise HTTPException(400, "Choose an image to animate.")
    if len(data) > MAX_IMAGE_BYTES: raise HTTPException(400, "That image is over 5MB. Please use a smaller one.")
    ext = _sniff(data)
    if not ext: raise HTTPException(400, "Only JPG, PNG and WEBP images are supported.")
    sweep_tmp()
    name = uuid.uuid4().hex + ext
    with open(os.path.join(TMP_DIR, name), "wb") as f: f.write(data)
    return name


def drop_tmp(name):
    if name:
        try: os.remove(os.path.join(TMP_DIR, name))
        except Exception: pass


@app.get("/api/tmp/{name}")
def tmp_image(name: str):
    p = os.path.join(TMP_DIR, name)
    if not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|webp)", name) or not os.path.exists(p):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(p, media_type={"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[name.rsplit(".", 1)[1]])


# ---------- generation ----------
class GenerateReq(BaseModel):
    license_key: str
    prompt: str = ""
    aspect_ratio: str = "9:16"
    kind: str = "video"      # image | animate_fast | animate | video
    image_b64: str = ""      # only for the two animate kinds
    seconds: int = 5         # ignored; kept so older apps still work


def build_input(kind, prompt, aspect, image_url=None):
    if kind == "image":
        return {"prompt": prompt, "image_size": IMAGE_SIZE[aspect], "enable_safety_checker": True}
    if kind == "video":
        return {"prompt": prompt, "aspect_ratio": aspect, "resolution": "720p", "num_frames": 81,
                "frames_per_second": 16, "enable_safety_checker": True}
    if kind == "animate":
        return {"image_url": image_url, "prompt": prompt, "resolution": "720p", "num_frames": 81,
                "frames_per_second": 16, "enable_safety_checker": True}
    return {"image_url": image_url, "prompt": prompt, "resolution": "720p", "enable_safety_checker": True}  # animate_fast


def extract_url(kind, out):
    if kind == "image":
        im = (out.get("images") or [None])[0] or out.get("image") or {}
        return (im or {}).get("url")
    return (out.get("video") or {}).get("url") or out.get("video_url")


def _finish(jid, email, ok, url="", kind=""):
    """Record the outcome exactly once. A failed job refunds exactly what it cost."""
    with _lock:
        db = db_load()
        job = db["jobs"].get(jid)
        if not job or job["state"] != "pending": return
        job["state"] = "done" if ok else "failed"
        if ok: job["url"] = url; job["kind"] = kind
        else: credit(db, email, +job.get("cost", 1), "refund_failed_generation", jid)
        db_save(db)


def _fal_error(e):
    resp = getattr(e, "response", None)
    code = getattr(resp, "status_code", None)
    if code in (401, 403): return "The AI service rejected our request. Please contact support."
    if code == 422: return "The AI service could not use that. Try a different description or image."
    if code == 429: return "The AI service is busy. Please try again in a minute."
    return str(e)[:200]


def run_job(jid, email, kind, prompt, aspect, tmpname):
    try:
        H = {"Authorization": f"Key {CFG['fal_key']}"}
        image_url = f"{CFG['public_base_url']}/api/tmp/{tmpname}" if tmpname else None
        JOBS[jid]["step"] = "Sending to the AI model"
        sub = requests.post(f"https://queue.fal.run/{CFG['models'][kind]}", headers=H, timeout=30,
                            json=build_input(kind, prompt, aspect, image_url))
        sub.raise_for_status()
        d = sub.json()
        status_url, response_url = d["status_url"], d["response_url"]
        JOBS[jid]["step"] = "Working on it (this can take a minute or two)" if kind != "image" else "Making your image"
        for _ in range(180):   # about 6 minutes
            time.sleep(2)
            s = requests.get(status_url, headers=H, timeout=20).json()
            if s.get("status") == "COMPLETED": break
            if s.get("status") in ("ERROR", "FAILED"): raise RuntimeError(s.get("error") or "The AI model reported an error.")
        else:
            raise RuntimeError("The AI model took too long.")
        res = requests.get(response_url, headers=H, timeout=30)
        res.raise_for_status()
        url = extract_url(kind, res.json())
        if not url: raise RuntimeError("The AI model did not return a result.")
        field = "image_url" if kind == "image" else "video_url"
        JOBS[jid] = {"done": True, "kind": kind, field: url}
        _finish(jid, email, True, url, kind)
    except Exception as e:
        log("generation failed:", jid, kind, repr(e)[:300])
        JOBS[jid] = {"done": True, "kind": kind, "error": _fal_error(e) + " Your points have been refunded."}
        _finish(jid, email, False)
    finally:
        drop_tmp(tmpname)


@app.post("/api/generate")
def generate(r: GenerateReq):
    kind = r.kind if r.kind in CFG["costs"] else None
    if not kind: raise HTTPException(400, "Unknown kind of generation.")
    prompt = (r.prompt or "").strip()[:600]
    animate = kind in ("animate", "animate_fast")
    if not prompt:
        if animate: prompt = ANIMATE_DEFAULT_PROMPT
        else: raise HTTPException(400, "Describe what it should show.")
    if not CFG["fal_key"]: raise HTTPException(503, "AI generation is not available right now.")
    aspect = r.aspect_ratio if r.aspect_ratio in ASPECTS else "9:16"
    email = email_for_key(r.license_key)
    tmpname = save_tmp_image(r.image_b64) if animate else None   # validated BEFORE anything is charged
    cost = CFG["costs"][kind]
    jid = uuid.uuid4().hex[:10]
    try:
        with _lock:
            db = db_load()
            have = db["customers"].get(email, {}).get("credits", 0)
            if have < cost:
                raise HTTPException(402, f"That needs {cost} point(s) and you have {have}. Buy a points pack to continue.")
            now = int(time.time())
            mine = [j for j in db["jobs"].values() if j["email"] == email]
            if sum(1 for j in mine if j["state"] == "pending") >= CFG["max_pending"]:
                raise HTTPException(429, "Please wait for your current generations to finish first.")
            if sum(1 for j in mine if now - j["t"] < 3600) >= CFG["max_per_hour"]:
                raise HTTPException(429, "That is a lot of generations in an hour. Please try again a little later.")
            for k in [k for k, j in db["jobs"].items() if now - j["t"] > 30 * 86400]: del db["jobs"][k]
            remaining = credit(db, email, -cost, "generate " + kind, jid)
            db["jobs"][jid] = {"email": email, "state": "pending", "t": now, "kind": kind, "cost": cost}
            db_save(db)
    except Exception:
        drop_tmp(tmpname); raise
    JOBS[jid] = {"done": False, "kind": kind, "step": "Starting"}
    threading.Thread(target=run_job, args=(jid, email, kind, prompt, aspect, tmpname), daemon=True).start()
    return {"job_id": jid, "points_remaining": remaining, "credits_remaining": remaining, "cost": cost}


@app.get("/api/generate/{job_id}")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if j: return j
    with _lock: job = db_load()["jobs"].get(job_id)
    if job and job["state"] == "done" and job.get("url"):
        field = "image_url" if job.get("kind") == "image" else "video_url"
        return {"done": True, "kind": job.get("kind"), field: job["url"]}
    if job and job["state"] == "failed":
        return {"done": True, "error": "That generation was interrupted. Your points have been refunded. Please try again."}
    return JSONResponse({"detail": "Unknown job"}, status_code=404)


def reconcile_orphans():
    """After a restart, any job still marked pending was cut off: refund it once."""
    with _lock:
        db = db_load(); n = 0
        for jid, job in db["jobs"].items():
            if job["state"] == "pending":
                job["state"] = "failed"; credit(db, job["email"], +job.get("cost", 1), "refund_interrupted_generation", jid); n += 1
        if n: db_save(db); log(f"refunded {n} interrupted generation(s) after restart")


reconcile_orphans()


# ---------- Lemon Squeezy webhook: points arrive when someone pays ----------
def signature_ok(body: bytes, header: str):
    secret = CFG["webhook_secret"]
    if not secret: return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest((header or "").encode(), expected.encode())


def handle_ls_event(p):
    event = (p.get("meta") or {}).get("event_name")
    data = p.get("data") or {}
    attrs = data.get("attributes") or {}
    oid = str(data.get("id"))
    item = attrs.get("first_order_item") or {}
    if event not in ("order_created", "order_refunded"): return {"ignored": "event"}
    if not CFG["product_id"] or str(item.get("product_id")) != str(CFG["product_id"]): return {"ignored": "other product"}
    email = (attrs.get("user_email") or "").strip().lower()
    if event == "order_created":
        if attrs.get("status") != "paid": return {"ignored": "not paid"}
        vname = (item.get("variant_name") or "").strip().lower()
        per = CFG["variants"].get(vname)
        if not per or not email:
            log(f"order {oid}: variant {item.get('variant_name')!r} / email missing. NO points granted. Grant manually if this was a real sale.")
            return {"ignored": "unknown variant"}
        try: qty = max(1, int(item.get("quantity") or 1))
        except Exception: qty = 1
        total = per * qty
        with _lock:
            db = db_load()
            if oid in db["orders"]: return {"duplicate": True}
            db["orders"][oid] = {"email": email, "credits": total, "state": "granted", "t": int(time.time())}
            credit(db, email, total, f"purchase {vname} x{qty}", oid)
            db_save(db)
        log(f"order {oid}: granted {total} points to {email}")
        return {"granted": total}
    with _lock:   # order_refunded
        db = db_load(); o = db["orders"].get(oid)
        if not o or o["state"] != "granted": return {"ignored": "unknown or already refunded"}
        o["state"] = "refunded"
        credit(db, o["email"], -o["credits"], "order_refunded", oid)
        db_save(db)
    log(f"order {oid}: refunded, reclaimed up to {o['credits']} points from {o['email']}")
    return {"reclaimed": o["credits"]}


@app.post("/api/webhooks/lemonsqueezy")
async def ls_webhook(request: Request):
    body = await request.body()
    if not CFG["webhook_secret"]: raise HTTPException(503, "Webhook secret is not configured.")
    if not signature_ok(body, request.headers.get("x-signature", "")): raise HTTPException(401, "Bad signature.")
    try: payload = json.loads(body)
    except Exception: raise HTTPException(400, "Bad payload.")
    return handle_ls_event(payload)


# ---------- manual points (support, and testing before the webhook is set up) ----------
class AdminGrant(BaseModel):
    token: str
    email: str
    credits: int
    reason: str = "manual"


@app.post("/api/admin/grant")
def admin_grant(r: AdminGrant):
    tok = CFG["admin_token"]
    if not tok or not hmac.compare_digest((r.token or "").encode(), tok.encode()): raise HTTPException(403, "Not allowed.")
    email = r.email.strip().lower()
    if not email or not (-5000 <= r.credits <= 5000): raise HTTPException(400, "Bad email or amount.")
    with _lock:
        db = db_load(); bal = credit(db, email, r.credits, "admin: " + r.reason[:60]); db_save(db)
    return {"email": email, "credits": bal, "points": bal}


@app.get("/api/health")
def health():
    return {"ok": True, "version": "2.0-points", "models": CFG["models"], "costs": CFG["costs"], "configured": {
        "fal_key": bool(CFG["fal_key"]), "product_id": bool(CFG["product_id"]),
        "webhook_secret": bool(CFG["webhook_secret"]), "admin_token": bool(CFG["admin_token"]),
        "persistent_data_dir": CFG["data_dir"] != HERE}}
