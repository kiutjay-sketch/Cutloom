"""Free voice: uses Windows' own built-in System.Speech voices (no key, no extra install, no cost)."""
import sys, os, json, subprocess, tempfile

PS = "powershell.exe" if os.name == "nt" else None
LIST_CMD = ("Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name } | ConvertTo-Json -Compress")
SAY_CMD = ("Add-Type -AssemblyName System.Speech; "
           "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
           "if ($env:CL_VOICE) {{ try {{ $s.SelectVoice($env:CL_VOICE) }} catch {{}} }} "
           "$s.Rate=[Math]::Max(-10,[Math]::Min(10,[int]$env:CL_RATE)); "
           "$s.SetOutputToWaveFile('{out}'); "
           "$s.Speak([IO.File]::ReadAllText('{txt}')); $s.Dispose()")

def run_ps(cmd):
    return subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", cmd],
                          capture_output=True, text=True, timeout=120)

def main():
    if os.name != "nt":
        print(json.dumps([]) if sys.argv[1] == "list" else "", file=sys.stderr); return
    if sys.argv[1] == "list":
        try:
            p = run_ps(LIST_CMD)
            names = json.loads(p.stdout.strip() or "[]")
            names = names if isinstance(names, list) else [names]
            print(json.dumps([{"id": n, "name": n} for n in names if n]))
        except Exception:
            print(json.dumps([]))
    else:
        text_file, out, rate, voice = sys.argv[2:6]
        wpm = int(rate) if rate.strip() else 175
        env = dict(os.environ, CL_VOICE=voice or "", CL_RATE=str(round((wpm / 175 - 1) * 10)))
        p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command",
                            SAY_CMD.format(out=out.replace("'", "''"), txt=text_file.replace("'", "''"))],
                           capture_output=True, text=True, timeout=180, env=env)
        if p.returncode or not os.path.exists(out):
            sys.stderr.write(p.stderr or "The Windows voice could not be reached.")
            sys.exit(1)

if __name__ == "__main__":
    main()
