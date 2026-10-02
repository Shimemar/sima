#!/usr/bin/env python3
"""HDMI切替時のコンソールメッセージ調査: journal中の該当メッセージ、getty/lightdmの状態"""
import subprocess
def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
print("== journal (this boot) matching messages")
print(sh("sudo -n journalctl -b --no-pager -o short-precise | grep -E -i 'read zero bytes|term_exitfunc|agetty|getty@|lightdm' | tail -40"))
print("== active gettys"); print(sh("systemctl list-units --no-pager --type=service | grep -i -E 'getty|lightdm'"))
print("== lightdm:", sh("systemctl is-active lightdm"))
print("== current VT:", sh("cat /sys/class/tty/tty0/active"))
print("== logind NAutoVTs/ReserveVT:", sh("grep -E '^(#)?(NAutoVTs|ReserveVT)' /etc/systemd/logind.conf"))
