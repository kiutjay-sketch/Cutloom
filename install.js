module.exports = {
  run: [
    { method: "shell.run", params: { path: "server", venv: "env", message: "uv pip install fastapi uvicorn python-multipart requests imageio-ffmpeg" } },
    { method: "notify", params: { html: "Cutloom is installed. Click Start.", href: "start.js" } }
  ]
}
