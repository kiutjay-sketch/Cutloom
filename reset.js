module.exports = { run: [
  { method: "fs.rm", params: { path: "server/env" } },
  { method: "fs.rm", params: { path: "server/3d/env" } },
  { method: "fs.rm", params: { path: "server/3d/TripoSR" } }
] }
