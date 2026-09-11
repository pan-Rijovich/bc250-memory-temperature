#!/usr/bin/env python3
"""
ASRock BC-250 P3.0 SMU payload patcher.

This script writes a custom SMU payload into SMU memory and redirects
Queue 3 Message 5 to the patched function.

This patch is intended for the ASRock BC-250 P3.0 firmware.

IMPORTANT:
The address of umc_read_temp_per_chip is payload-dependent.

If SMUPayload.elf is rebuilt or a different SMU firmware version is
used, the address of umc_read_temp_per_chip may change.

Before running the patcher with a rebuilt or different payload, verify
the function address with nm.

Example:

    ./xtensa-esp32-elf-gcc-5.2.0/bin/xtensa-esp32-elf-nm \
        SMUPayload.elf | grep umc_read_temp_per_chip

Compare the address reported by nm with:

    QUEUE_3_MSG_5_HANDLER = 0x0003AAC4

If the address is different, update QUEUE_3_MSG_5_HANDLER below.

The payload itself is written starting at:

    PAYLOAD_START = 0x0003AA9C

Usage:
    sudo python3 patch_smu.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bc250_smu import Bc250Smu
from unlock import unlock

PAYLOAD_START = 0x0003AA9C
QUEUE_3_MSG_5_HANDLER = 0x0003AAC4
PAYLOAD_FILE = "SMUPayload.bin"

QUEUE_3_MSG_5_HANDLER_REG = 0x0000748C

def patch_smu_block(smu, start_addr, path):
    """Write a binary payload to SMU memory as 32-bit little-endian words."""
    addr = start_addr

    with open(path, "rb") as f:
        while True:
            data = f.read(4)

            if not data:
                break

            value = int.from_bytes(
                data.ljust(4, b"\x00"),
                "little",
            )

            smu.smu_write32(addr, value)
            addr += 4


def main():
    if os.geteuid() != 0:
        sys.exit("needs root")

    if not os.path.isfile(PAYLOAD_FILE):
        sys.exit(f"payload not found: {PAYLOAD_FILE}")

    smu = Bc250Smu()

    try:
        unlock(smu)

        patch_smu_block(
            smu,
            PAYLOAD_START,
            PAYLOAD_FILE,
        )

        smu.smu_write32(
            QUEUE_3_MSG_5_HANDLER_REG,
            QUEUE_3_MSG_5_HANDLER,
        )

        print(
            "Successfully patched queue_3_msg_5_handler "
            f"-> 0x{QUEUE_3_MSG_5_HANDLER:08X}"
        )

    finally:
        smu.close()


if __name__ == "__main__":
    main()
