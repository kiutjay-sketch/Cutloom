import os, sys, time, uuid, threading
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "TripoSR"))

import numpy as np, torch, rembg, trimesh
from PIL import Image
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from tsr.system import TSR
from tsr.utils import remove_background, resize_foreground, to_gradio_3d_orientation

OUT = os.path.join(HERE, "outputs"); os.makedirs(OUT, exist_ok=True)
DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
app = FastAPI(title="Cutloom 3D")

@app.exception_handler(RuntimeError)
def _runtime_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)
_model, _session, _lock = None, None, threading.Lock()

def get_model():
    global _model, _session
    if _model is None:
        m = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
        m.renderer.set_chunk_size(8192); m.to(DEVICE)
        _model, _session = m, rembg.new_session()
    return _model, _session

@app.get("/api/status")
def status():
    return {"device": DEVICE, "gpu": DEVICE.startswith("cuda"), "loaded": _model is not None}

@app.post("/api/generate")
def generate(image: UploadFile = File(...), resolution: int = Form(256), remove_bg: bool = Form(True), mode: str = Form("blockout")):
    try:
        img = Image.open(image.file).convert("RGB")
    except Exception:
        raise HTTPException(400, "That file isn't an image we can read. Try a PNG or JPG.")
    t0 = time.time()
    with _lock:
        model, session = get_model()
        if remove_bg:
            img = remove_background(img, session)
            img = resize_foreground(img, 0.85)
            a = np.array(img).astype(np.float32) / 255.0
            a = a[:, :, :3] * a[:, :, 3:4] + (1 - a[:, :, 3:4]) * 0.5
            img = Image.fromarray((a * 255).astype(np.uint8))
        with torch.no_grad():
            codes = model([img], device=DEVICE)
        blockout = mode == "blockout"
        res = max(32, min(int(resolution * (0.25 if blockout else 1)), 384))
        mesh = model.extract_mesh(codes, not blockout, resolution=res)[0]
        mesh = to_gradio_3d_orientation(mesh)
        if blockout:  # chunky, flat-shaded grey mesh: unmerge vertices so each face keeps its own normal
            mesh = trimesh.Trimesh(vertices=mesh.vertices[mesh.faces].reshape(-1, 3),
                                   faces=np.arange(len(mesh.faces) * 3).reshape(-1, 3), process=False)
            mesh.visual.face_colors = np.tile([205, 210, 222, 255], (len(mesh.faces), 1))
        name = f"{uuid.uuid4().hex[:10]}.glb"
        mesh.export(os.path.join(OUT, name))
    return {"url": f"/outputs/{name}", "seconds": round(time.time() - t0, 1)}

app.mount("/outputs", StaticFiles(directory=OUT), name="outputs")

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7861)
