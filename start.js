module.exports = {
  daemon: true,
  run: [
    { method: "shell.run", params: { path: "server", venv: "env", message: "python main.py",
      on: [{ event: "/(http:\\/\\/[0-9.:]+)/", done: true }] } },
    { method: "local.set", params: { url: "{{input.event[1]}}" } }
  ]
}
