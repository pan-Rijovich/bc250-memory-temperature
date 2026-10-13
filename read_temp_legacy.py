#!/usr/bin/env python3
"""
ASRock BC-250 GDDR6 temperature monitor.

SMU message 3, command 0x5:
    Input:  UMC ID (0-7)
    Output: MR3 value (bits 0-15), or an error code.

C implementation error responses:
    0xFFFFFFFF: Stage 1 timeout
    0xFFFFFFFE: Stage 2 timeout

Temperature conversion:
    temperature_C = (MR3 & 0xFF) * 2 - 40

SMU message 3, command 0x7:
    Input:  (UMC ID << 8) | register index
    Output: captured register value (0-3)

Usage:
    sudo python3 read_temp.py
"""

import os
import sys
import time
import argparse
from datetime import datetime

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from bc250_smu import Bc250Smu


SMU_MSG_READ_TEMP = 0x5
SMU_MSG_READ_REGDUMP = 0x7

ERR_STAGE1_TIMEOUT = 0xFFFFFFFF
ERR_STAGE2_TIMEOUT = 0xFFFFFFFE


def decode_response(value):
    """Decode the response from umc_read_temp_per_chip()."""
    value &= 0xFFFFFFFF

    if value == ERR_STAGE1_TIMEOUT:
        return {
            "temp_code": None,
            "error": "Stage 1 timeout",
        }

    if value == ERR_STAGE2_TIMEOUT:
        return {
            "temp_code": None,
            "error": "Stage 2 timeout",
        }

    temp_code = value & 0xFF

    return {
        "temp_code": temp_code,
        "error": None,
    }


def format_table(rows):
    """Format a table using only the Python standard library."""
    headers = [
        "Chip (UMC)",
        "Response raw",
        "Temp code",
        "Temperature",
        "Status",
    ]

    all_rows = [headers] + rows
    widths = [
        max(len(str(row[i])) for row in all_rows)
        for i in range(len(headers))
    ]

    def format_row(row):
        return "| " + " | ".join(
            str(value).ljust(widths[i])
            for i, value in enumerate(row)
        ) + " |"

    separator = "+-" + "-+-".join(
        "-" * width for width in widths
    ) + "-+"

    result = [separator, format_row(headers), separator]

    for row in rows:
        result.append(format_row(row))

    result.append(separator)
    return "\n".join(result)


def read_regdump(smu, chip):
    """Read and clear the four captured registers for one UMC."""
    values = []

    for reg in range(4):
        payload = (chip << 8) | reg

        try:
            value = smu.send_message(
                3, SMU_MSG_READ_REGDUMP, [payload]
            )[1]
            values.append(f"0x{value & 0xFFFFFFFF:08X}")
        except Exception:
            values.append("ERR")

    return values


def main():
    if os.geteuid() != 0:
        sys.exit("This script needs root privileges.")

    parser = argparse.ArgumentParser(
        description="BC-250 GDDR6 temperature monitor"
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "-c", "--counter",
        type=str,
        help="Path to the successful-cycle counter file",
    )
    parser.add_argument(
        "-l", "--log",
        type=str,
        default="smu_error.log",
        help="Error log file (default: smu_error.log)",
    )

    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("interval must be greater than zero")

    success_cycles = 0

    if args.counter and os.path.exists(args.counter):
        try:
            with open(args.counter, "r", encoding="utf-8") as f:
                success_cycles = int(f.read().strip())
        except (ValueError, OSError):
            pass

    smu = Bc250Smu()
    use_alternate_screen = sys.stdout.isatty()

    try:
        if use_alternate_screen:
            sys.stdout.write("\033[?1049h")  # Save screen and enter alternate buffer
            sys.stdout.write("\033[?25l")    # Hide cursor
            sys.stdout.flush()
        while True:
            rows = []
            valid_temps = []
            cycle_errors = []
            cycle_has_errors = False

            for chip in range(8):
                try:
                    response = smu.send_message(
                        3, SMU_MSG_READ_TEMP, [chip]
                    )[1]
                    response &= 0xFFFFFFFF

                except Exception as exc:
                    cycle_has_errors = True
                    cycle_errors.append(
                        f"UMC {chip}: SMU communication error: {exc}"
                    )

                    rows.append([
                        chip, "ERR", "-", "-", "-", "Communication error"
                    ])
                    continue

                fields = decode_response(response)

                if fields["error"] is not None:
                    cycle_has_errors = True

                    rows.append([
                        chip,
                        f"0x{response:08X}",
                        "-",
                        "-",
                        "-",
                        fields["error"],
                    ])

                    timestamp = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    reg_values = read_regdump(smu, chip)

                    log_msg = (
                        f"[{timestamp}] {fields['error']}: "
                        f"UMC={chip} | "
                        f"Response=0x{response:08X} | "
                        f"Regs(0-3)=[{', '.join(reg_values)}]\n"
                    )

                    try:
                        with open(
                            args.log, "a", encoding="utf-8"
                        ) as log_file:
                            log_file.write(log_msg)
                    except OSError as exc:
                        cycle_errors.append(
                            f"Cannot write log file: {exc}"
                        )

                    continue

                temp_code = fields["temp_code"]
                temp_c = temp_code * 2 - 40

                valid_temps.append((chip, temp_c))

                rows.append([
                    chip,
                    f"0x{response:08X}",
                    f"0x{temp_code:02X}",
                    f"{temp_c:.1f} °C",
                    "OK",
                ])

            output = [
                "=== BC-250 SMU Temperature Monitor ===",
                "",
                format_table(rows),
            ]

            if valid_temps:
                temps = [temp for _, temp in valid_temps]
                average = sum(temps) / len(temps)

                hotspot_chip, hotspot = max(
                    valid_temps,
                    key=lambda item: item[1],
                )

                output.append(f"\nAverage : {average:.1f} °C")
                output.append(
                    f"Hotspot : {hotspot:.1f} °C "
                    f"(Chip {hotspot_chip})"
                )
                output.append(
                    f"Valid readings: {len(valid_temps)}/8"
                )
            else:
                output.append(
                    "\nNo valid temperature readings in this cycle."
                )

            if not cycle_has_errors:
                success_cycles += 1

                if args.counter:
                    try:
                        with open(
                            args.counter, "w", encoding="utf-8"
                        ) as counter_file:
                            counter_file.write(str(success_cycles))
                    except OSError as exc:
                        output.append(
                            f"Counter file error: {exc}"
                        )

                output.append(
                    f"Successful cycles: {success_cycles}"
                )
            else:
                output.append(
                    f"Successful cycles: {success_cycles} "
                    "(errors detected)"
                )

                if cycle_errors:
                    output.append("\nCommunication / logging errors:")
                    output.extend(cycle_errors)

            output.append(
                "\nError codes: 0xFFFFFFFF = Stage 1 timeout; "
                "0xFFFFFFFE = Stage 2 timeout."
            )

            if use_alternate_screen:
                sys.stdout.write("\033[H\033[J")

            sys.stdout.write("\n".join(output) + "\n")
            sys.stdout.flush()

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopping monitor...")

    finally:
        try:
            smu.close()
        finally:
            if use_alternate_screen:
                sys.stdout.write("\033[?25h")   # Restore cursor visibility
                sys.stdout.write("\033[?1049l") # Restore previous screen
                sys.stdout.flush()

if __name__ == "__main__":
    main()
