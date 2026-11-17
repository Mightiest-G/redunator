import json, subprocess, sys, time, os
W = os.path.dirname(os.path.abspath(__file__))
VENV = "/home/hatch/workspace/redunator-venv/bin/python"
ONE = os.path.join(W, "speed_pull_one.py")
jobs = json.load(open(sys.argv[1]))
for j in jobs:
    cmd = [VENV, ONE, j["kind"], j["did"], j["label"], j["start"], j["end"]]
    if "holidays" in j: cmd += ["--holidays", j["holidays"]]
    if "class" in j: cmd += ["--class", j["class"]]
    print(f"=== {j['kind']} {j['did']} {j['label']} ===", flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"FAILED {j} rc={r.returncode} — stopping stream", flush=True)
        sys.exit(1)
    time.sleep(20)
print("STREAM DONE", flush=True)
