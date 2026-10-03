module.exports = {
  run: [
    { method: "shell.run", params: { path: "server/3d", message: "git clone https://github.com/VAST-AI-Research/TripoSR TripoSR || echo TripoSR already downloaded" } },
    { when: "{{gpu === 'nvidia'}}", method: "shell.run", params: { path: "server/3d", venv: "env", message: "uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121" } },
    { when: "{{gpu !== 'nvidia'}}", method: "shell.run", params: { path: "server/3d", venv: "env", message: "uv pip install torch torchvision" } },
    { method: "shell.run", params: { path: "server/3d", venv: "env", message: "uv pip install \"numpy<2\" omegaconf==2.3.0 einops transformers==4.35.0 trimesh rembg onnxruntime huggingface-hub imageio xatlas moderngl pillow fastapi uvicorn python-multipart scikit-image" } },
    { method: "notify", params: { html: "3D module installed. Stop and Start Cutloom to use it.", href: "start.js" } }
  ]
}
