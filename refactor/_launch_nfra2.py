"""一次性启动器：延迟 600s（等 IP 冷却）后以温和节流重跑 nfra 增量采集。"""
import os
import subprocess
import sys

cwd = "/Users/lmc/code/orchestrator/refactor"
env = dict(os.environ,
           REFACTOR_DB_URL="postgresql+psycopg://root:5462@localhost:5432/test",
           REFACTOR_STORAGE="postgres")
log = open("/tmp/nfra_run2.log", "w")
cmd = (
    "sleep 600 && "
    "cd /Users/lmc/code/orchestrator/refactor && "
    "REFACTOR_DB_URL='postgresql+psycopg://root:5462@localhost:5432/test' "
    "REFACTOR_STORAGE=postgres "
    "python -m collectors.nfra.collector --out-dir data/raw --delay 2.0"
)
p = subprocess.Popen(
    ["bash", "-c", cmd],
    cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
    start_new_session=True,
)
print("LAUNCHED_PID", p.pid, "(will start crawl after ~600s cooldown)")
