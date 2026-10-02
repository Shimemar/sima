#!/usr/bin/env python3
"""hwmon / thermal / power センサーの一覧"""
import glob, os
for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
    name = open(os.path.join(d, "name")).read().strip() if os.path.exists(os.path.join(d, "name")) else "?"
    vals = []
    for f in sorted(glob.glob(os.path.join(d, "*_input"))):
        try:
            vals.append(f"{os.path.basename(f)}={open(f).read().strip()}")
        except Exception as e:
            vals.append(f"{os.path.basename(f)}=ERR")
    print(d, name, " ".join(vals)[:300])
for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
    print(z, open(z + "/type").read().strip(), open(z + "/temp").read().strip())
    for t in sorted(glob.glob(z + "/trip_point_*_temp")):
        ty = open(t.replace("_temp", "_type")).read().strip()
        print("   ", os.path.basename(t), open(t).read().strip(), ty)
print(glob.glob("/sys/bus/i2c/drivers/ina*"), glob.glob("/sys/class/power_supply/*"))
