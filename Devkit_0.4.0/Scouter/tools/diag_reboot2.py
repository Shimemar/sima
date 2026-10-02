#!/usr/bin/env python3
"""直近数回のブートの終わり方と、pstore(カーネルクラッシュ記録)の確認"""
import subprocess, os
def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
for b in ("-1", "-2", "-3"):
    print(f"== boot {b}: first line / Scouter-related / last 3 lines")
    print(sh(f"sudo -n journalctl -b {b} --no-pager -o short-precise | head -1"))
    print(sh(f"sudo -n journalctl -b {b} --no-pager -o short-precise | grep -E 'lightdm|COMMAND=|python3\\[|Stopping|Reached target.*(Shutdown|Power|Reboot)|reboot|poweroff' | grep -v PipelineManager | tail -12"))
    print(sh(f"sudo -n journalctl -b {b} --no-pager -o short-precise | grep -v PipelineManager | tail -3"))
print("== pstore:", sh("ls -la /sys/fs/pstore/ 2>&1; sudo -n ls -la /var/lib/systemd/pstore/ 2>&1 | tail -5"))
print("== last/reboot records:", sh("last -x -n 12 2>&1 | head -14"))
print("== power supply / undervoltage hints:", sh("sudo -n dmesg 2>/dev/null | grep -i -E 'voltage|power|brown|reset reason|watchdog' | head -10"))
