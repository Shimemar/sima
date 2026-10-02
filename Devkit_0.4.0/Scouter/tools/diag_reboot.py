#!/usr/bin/env python3
"""直前の再起動理由の調査: ブート一覧、前回ブートの最後のログ、カーネルの異常、温度"""
import subprocess, glob
def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
print("== uptime:", sh("uptime -s; uptime -p"))
print("== boots"); print(sh("sudo -n journalctl --list-boots --no-pager | tail -6"))
print("== previous boot: last 60 lines"); print(sh("sudo -n journalctl -b -1 --no-pager -o short-precise | tail -60"))
print("== previous boot: kernel errors/panic/oom/thermal/watchdog")
print(sh("sudo -n journalctl -b -1 -k --no-pager -o short-precise | grep -i -E 'panic|oops|bug:|oom|thermal|watchdog|hung_task|mla|cma|error' | tail -30"))
print("== previous boot: lightdm / Scouter-related")
print(sh("sudo -n journalctl -b -1 --no-pager -o short-precise | grep -i -E 'lightdm|systemctl|sudo' | tail -20"))
print("== thermal now")
for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
    try: print(z, int(open(z).read()) / 1000, "C")
    except Exception: pass
