module.exports = {
  version: "2.0",
  title: "Cutloom",
  description: "One-click shorts: niche ideas, script, voiceover, footage, captions and a finished 9:16 video. Optional photo-to-3D module.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    const installed = info.exists("server/env")
    const r = n => info.running(n)
    if (r("install.js")) return [{ icon: "fa-solid fa-plug", text: "Installing", href: "install.js" }]
    if (r("install3d.js")) return [{ icon: "fa-solid fa-cube", text: "Installing 3D module", href: "install3d.js" }]
    if (!installed) return [{ icon: "fa-solid fa-plug", text: "Install", href: "install.js" }]
    if (r("start.js")) {
      const local = info.local("start.js")
      if (local && local.url) return [
        { icon: "fa-solid fa-clapperboard", text: "Open Cutloom", href: local.url, target: "_blank" },
        { icon: "fa-solid fa-terminal", text: "Terminal", href: "start.js" }]
      return [{ icon: "fa-solid fa-terminal", text: "Starting...", href: "start.js" }]
    }
    if (r("update.js")) return [{ icon: "fa-solid fa-rotate", text: "Updating", href: "update.js" }]
    if (r("reset.js")) return [{ icon: "fa-solid fa-broom", text: "Resetting", href: "reset.js" }]
    const menu = [{ icon: "fa-solid fa-power-off", text: "Start", href: "start.js" }]
    if (!info.exists("server/3d/TripoSR")) menu.push({ icon: "fa-solid fa-cube", text: "Add 3D module (optional)", href: "install3d.js" })
    menu.push({ icon: "fa-solid fa-rotate", text: "Update", href: "update.js" }, { icon: "fa-solid fa-broom", text: "Reset", href: "reset.js" })
    return menu
  }
}
