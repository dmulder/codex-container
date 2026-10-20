#!/usr/bin/env python3
"""Small process-level Podman double; no real containers or network access."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

root = Path(os.environ["FAKE_PODMAN_STATE"])
root.mkdir(exist_ok=True)
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")

if args[:2] == ["image", "inspect"]:
    if not (root / "image").exists():
        sys.exit(1)
    print((root / "image").read_text())
elif args[0] == "build":
    if os.environ.get("FAKE_FAIL") == "build":
        sys.exit(42)
    content = Path(args[args.index("-f") + 1]).read_bytes()
    (root / "image").write_text(hashlib.sha256(content).hexdigest() + "|sha256:fake")
    print("BUILD DIAGNOSTIC")
elif args[0] == "create":
    name = args[args.index("--name") + 1]
    stage = "update" if "--entrypoint" in args else "exec"
    (root / (name + ".json")).write_text(json.dumps({"args": args, "stage": stage}))
    if os.environ.get("FAKE_FAIL") == "create":
        sys.exit(42)
    print(name)
elif args[0] == "start":
    name = args[-1]
    info = json.loads((root / (name + ".json")).read_text())
    stage = info["stage"]
    if os.environ.get("FAKE_BLOCK") == stage:
        daemon = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        (root / (name + ".pid")).write_text(str(daemon.pid))
        (root / "blocked").write_text(name)
        daemon.wait()
    if os.environ.get("FAKE_FAIL") == stage:
        sys.exit(42)
    if stage == "update":
        print("UPDATE DIAGNOSTIC")
    else:
        info["stdin"] = sys.stdin.readline() if sys.stdin.isatty() else sys.stdin.read()
        info["snapshots"] = {}
        for index, arg in enumerate(info["args"]):
            if arg == "--volume":
                source, target, mode = info["args"][index + 1].split(":")
                if "/input-" in source:
                    info["snapshots"][target] = Path(source).read_text()
                if target == "/codex-exchange":
                    info["output_directory"] = source
        answer = "Complete review: no missing input.\n"
        if os.environ.get("FAKE_VET"):
            if not info["snapshots"]:
                sys.exit("Vet review has no visible input")
            if "Return ONLY a JSON object" in info["stdin"]:
                answer = json.dumps({"complete": True, "risk": "LOW", "summary": "Batch read",
                                     "findings": [], "follow_up": "", "decision": "accept", "reasons": []})
            elif "Draft a short cargo-vet exemption note" in info["stdin"]:
                answer = "Exemption, not certification; review limitations remain."
            elif "Synthesize cargo-vet batch reviews" in info["stdin"]:
                answer = "Synthesis complete; verify interactions before certification."
            with (root / "reviews.jsonl").open("a") as log:
                log.write(json.dumps({"prompt": info["stdin"], "inputs": info["snapshots"]}) + "\n")
        if "output_directory" in info and os.environ.get("FAKE_FAIL") != "missing-output":
            Path(info["output_directory"], "result.txt").write_text(answer)
        (root / "last-exec.json").write_text(json.dumps(info))
        print("CODEX STDOUT")
elif args[0] == "rm":
    if os.environ.get("FAKE_FAIL") == "cleanup":
        sys.exit(42)
    name = args[-1]
    pidfile = root / (name + ".pid")
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass
        pidfile.unlink()
    (root / (name + ".json")).unlink(missing_ok=True)
else:
    sys.exit("Unexpected fake Podman command: " + repr(args))
