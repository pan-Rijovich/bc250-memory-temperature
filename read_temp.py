#!/usr/bin/env python3
#!/usr/bin/env python3
"""
ASRock BC-250 GDDR6 temperature monitor.

Reads the JEDEC MR3 temperature readout from all 8 GDDR6 memory chips
on the ASRock BC-250 and displays the temperature of each chip.

Temperature conversion follows the JEDEC MR3 encoding:
    temperature (°C) = (temperature_code * 2) - 40

The script also calculates:
    Average  - average temperature across all 8 GDDR6 chips
    Hotspot  - highest temperature and the corresponding chip

Usage:
    sudo python3 read_temp.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bc250_smu import Bc250Smu, SmuError
from unlock import unlock
from tabulate import tabulate

def main():
    if os.geteuid() != 0:
        sys.exit("needs root")

    smu = Bc250Smu()
    try:
        rows = []
        temperatures = []

        for chip in range(8):
            temp_readout = smu.send_message(3, 0x5, [chip])[1]
            temp_c = ((temp_readout & 0xff) * 2) - 40

            temperatures.append(temp_c)

            rows.append([
                chip,
                f"0x{temp_readout:08X}",
                f"0x{temp_readout & 0xFF:02X}",
                f"{temp_c:.1f} °C"
            ])

        print(tabulate(
            rows,
            headers=["Chip", "MR3 raw", "Temp code", "Temperature"],
            tablefmt="grid"
        ))

        average = sum(temperatures) / len(temperatures)
        hotspot = max(temperatures)
        hotspot_chip = temperatures.index(hotspot)

        print()
        print(f"Average : {average:.1f} °C")
        print(f"Hotspot : {hotspot:.1f} °C (Chip {hotspot_chip})")
    finally:
        smu.close()

if __name__ == "__main__":
    main()
