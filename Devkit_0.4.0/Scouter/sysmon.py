"""Once-a-second system telemetry -> JSON lines, fsync'ed on every write so the record
survives a sudden board reset (the point: the DevKit reset 30-40 s into HDMI runs on
2026-10-02 with nothing in the journal). Logs board power (pyneat power module, PMIC
rails over I2C), SoC/board temperatures, fan, load average, CmaFree."""

from __future__ import annotations

import glob
import json
import os
import threading
import time


def _read(path: str):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _hwmon(name: str) -> str | None:
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(os.path.join(d, "name")) == name:
            return d
    return None


class SysMon:
    def __init__(self, path, interval_s: float = 1.0, extra=None) -> None:
        self.path, self.interval_s = str(path), interval_s
        self.extra = extra or (lambda: {})
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sysmon", daemon=True)
        self._soc = _hwmon("simaai_modalix_thermal_sensor")
        self._fan = _hwmon("lm96163")
        self._power_opts = None
        try:
            import pyneat
            opts = pyneat.PowerMonitorOptions()
            opts.enabled = True
            self._power_opts = opts
            self._read_power = pyneat.power.read_power_snapshot
        except Exception as exc:  # noqa: BLE001
            self._power_err = str(exc)

    def start(self) -> "SysMon":
        self._f = open(self.path, "w", encoding="utf-8")
        self._t0 = time.monotonic()
        self._thread.start()
        return self

    def sample(self) -> dict:
        rec = {"t": round(time.monotonic() - self._t0, 1), "wall": time.strftime("%H:%M:%S")}
        if self._soc:
            temps = []
            for f in glob.glob(os.path.join(self._soc, "temp*_input")):
                v = _read(f)
                if v and v.lstrip("-").isdigit() and int(v) < 125000:   # some channels read ~140 C (bogus)
                    temps.append(int(v) / 1000)
            if temps:
                rec["soc_max_c"] = max(temps)
        rec["board_c"] = int(_read("/sys/class/thermal/thermal_zone0/temp") or 0) / 1000
        if self._fan:
            rec["fan_rpm"] = int(_read(os.path.join(self._fan, "fan1_input")) or 0)
        rec["load1"] = float((_read("/proc/loadavg") or "0").split()[0])
        for line in open("/proc/meminfo"):
            if line.startswith("CmaFree"):
                rec["cma_free_mb"] = int(line.split()[1]) // 1024
        if self._power_opts is not None:
            try:
                snap = self._read_power(self._power_opts)
                rec["power_w"] = round(snap.total_watts, 2)
                rails = {}
                for r in snap.rails:
                    name = getattr(r.config, "name", None) or getattr(r.config, "label", "?")
                    w = r.power_w.value if r.power_w.available else None
                    v = r.voltage_v.value if r.voltage_v.available else None
                    rails[name] = [round(w, 2) if w is not None else None,
                                   round(v, 3) if v is not None else None]
                rec["rails"] = rails   # name -> [watts, volts]
            except Exception as exc:  # noqa: BLE001
                rec["power_err"] = str(exc)[:120]
        rec.update(self.extra())
        return rec

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._f.write(json.dumps(self.sample(), ensure_ascii=False) + "\n")
                self._f.flush()
                os.fsync(self._f.fileno())
            except Exception as exc:  # noqa: BLE001
                print(f"sysmon: {exc}", flush=True)
            self._stop.wait(self.interval_s)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._f.close()
