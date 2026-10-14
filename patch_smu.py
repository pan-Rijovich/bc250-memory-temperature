#!/usr/bin/env python3
"""Install the BC-250 P3.0 SMU payload and Queue 3 message handlers.

Build artifacts required by this script:
    SMUPayload.bin
    SMUPayload.symbols  (GNU nm --format=posix)

Use --force / -f to overwrite an already-installed payload.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bc250_smu import Bc250Smu
from unlock import unlock

# BC-250 P3.0 SMU firmware constants.
SMU_VERSION_ADDR = 0x00000000
EXPECTED_SMU_VERSION = 0x00580600

PAYLOAD_START = 0x0003AA9C
# EXTRA_SRAM from smu3.ld: ORIGIN=0x3AA9C, LENGTH=0x3564.
PAYLOAD_END = 0x0003E000  # exclusive
PAYLOAD_MAX_SIZE = PAYLOAD_END - PAYLOAD_START

PAYLOAD_FILE = ROOT / "SMUPayload.bin"
SYMBOLS_FILE = ROOT / "SMUPayload.symbols"

# Queue 3 table is an array of 0xA9 entries, each {handler:u32, flags:u32}.
QUEUE3_TABLE_BASE = 0x7464
QUEUE3_ENTRY_COUNT = 0xA9
QUEUE3_ENTRY_SIZE = 8
QUEUE3_HANDLER_OFFSET = 0

MESSAGE_HANDLERS = {
    5: "umc_read_temp_per_chip",
    6: "umc_read_temp_packed",
    7: "regdump_read",
}


@dataclass(frozen=True)
class Symbol:
    kind: str
    address: int
    size: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write SMUPayload.bin and register handlers in the Queue 3 table."
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="overwrite the payload even if Queue 3 already points into its memory range",
    )
    return parser.parse_args()


def handler_slot(message_id: int) -> int:
    """Return address of the handler field (not the flags field)."""
    if not 0 <= message_id < QUEUE3_ENTRY_COUNT:
        raise ValueError(
            f"invalid Queue 3 message ID 0x{message_id:X}; "
            f"table contains 0x{QUEUE3_ENTRY_COUNT:X} entries (0..0x{QUEUE3_ENTRY_COUNT - 1:X})"
        )
    return QUEUE3_TABLE_BASE + message_id * QUEUE3_ENTRY_SIZE + QUEUE3_HANDLER_OFFSET


def load_symbols() -> dict[str, Symbol]:
    """Parse `nm --defined-only --numeric-sort --format=posix` output."""
    symbols: dict[str, Symbol] = {}
    try:
        lines = SYMBOLS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {SYMBOLS_FILE.name}: {exc}") from exc

    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        name, kind, value = fields[:3]
        try:
            address = int(value, 16)
            # GNU nm POSIX format writes symbol size as the fourth field, in hex.
            size = int(fields[3], 16) if len(fields) >= 4 else None
        except ValueError:
            continue
        symbols[name] = Symbol(kind=kind, address=address, size=size)

    if not symbols:
        raise ValueError(f"no symbols parsed from {SYMBOLS_FILE.name}; expected GNU nm POSIX format")
    return symbols


def preflight() -> tuple[bytes, dict[int, dict[str, int | str]]]:
    """Validate only the .bin and .symbols build artifacts before touching hardware."""
    for path in (PAYLOAD_FILE, SYMBOLS_FILE):
        if not path.is_file():
            raise FileNotFoundError(f"required file not found: {path.name}")

    payload = PAYLOAD_FILE.read_bytes()
    if not payload:
        raise ValueError(f"{PAYLOAD_FILE.name} is empty")

    # The write routine emits complete 32-bit words and zero-pads the last word.
    padded_size = (len(payload) + 3) & ~3
    if PAYLOAD_START & 3:
        raise ValueError(f"payload start 0x{PAYLOAD_START:08X} is not 4-byte aligned")
    if padded_size > PAYLOAD_MAX_SIZE:
        raise ValueError(
            f"payload needs 0x{padded_size:X} bytes after 4-byte alignment, but only "
            f"0x{PAYLOAD_MAX_SIZE:X} bytes are available "
            f"(0x{PAYLOAD_START:08X}..0x{PAYLOAD_END:08X})"
        )

    payload_end = PAYLOAD_START + len(payload)
    write_end = PAYLOAD_START + padded_size
    symbols = load_symbols()
    handlers: dict[int, dict[str, int | str]] = {}

    for message_id, name in MESSAGE_HANDLERS.items():
        symbol = symbols.get(name)
        if symbol is None:
            raise ValueError(f"handler symbol {name!r} not found in {SYMBOLS_FILE.name}")
        if symbol.kind not in ("T", "t"):
            raise ValueError(f"{name} has nm type {symbol.kind!r}; expected a text symbol (T/t)")
        if symbol.size is None or symbol.size <= 0:
            raise ValueError(
                f"size for {name!r} is missing/zero in {SYMBOLS_FILE.name}; "
                "generate it using nm --format=posix"
            )

        start = symbol.address
        end = start + symbol.size  # exclusive
        if start < PAYLOAD_START or end > PAYLOAD_END:
            raise ValueError(
                f"{name} range 0x{start:08X}..0x{end:08X} is outside the reserved payload area "
                f"0x{PAYLOAD_START:08X}..0x{PAYLOAD_END:08X}"
            )
        if start < PAYLOAD_START or end > payload_end:
            raise ValueError(
                f"{name} range 0x{start:08X}..0x{end:08X} is not fully present in {PAYLOAD_FILE.name} "
                f"(binary range 0x{PAYLOAD_START:08X}..0x{payload_end:08X})"
            )
        if end > write_end:
            # Defensive consistency check; write_end includes only final-word padding.
            raise ValueError(f"{name} ends beyond the padded binary write range")

        handlers[message_id] = {
            "name": name,
            "address": start,
            "size": symbol.size,
            "slot": handler_slot(message_id),
        }

    print(
        f"Payload:        0x{PAYLOAD_START:08X}..0x{payload_end:08X} "
        f"({len(payload)} bytes; write size 0x{padded_size:X})"
    )
    print(
        f"Payload area:   0x{PAYLOAD_START:08X}..0x{PAYLOAD_END:08X} "
        f"(0x{PAYLOAD_MAX_SIZE:X} bytes; 0x{PAYLOAD_MAX_SIZE - padded_size:X} bytes free)"
    )
    print(
        f"Queue 3 table:  0x{QUEUE3_TABLE_BASE:08X}.."
        f"0x{QUEUE3_TABLE_BASE + QUEUE3_ENTRY_COUNT * QUEUE3_ENTRY_SIZE:08X} "
        f"({QUEUE3_ENTRY_COUNT:#x} entries x {QUEUE3_ENTRY_SIZE} bytes)"
    )
    for message_id, info in handlers.items():
        print(
            f"Message {message_id:#04x}: {info['name']} @ 0x{int(info['address']):08X} "
            f"(size 0x{int(info['size']):X}); handler field 0x{int(info['slot']):08X}"
        )

    return payload, handlers


def read_smu_u32(smu: Bc250Smu, address: int) -> int:
    """Read one 32-bit SMU SRAM word; smu_read returns bytes."""
    data = smu.smu_read(address, 1)
    if not isinstance(data, (bytes, bytearray)) or len(data) != 4:
        raise RuntimeError(
            f"smu_read(0x{address:08X}, 1) returned {type(data).__name__} "
            f"with length {len(data) if hasattr(data, '__len__') else 'n/a'}; expected 4 bytes"
        )
    return int.from_bytes(data, byteorder="little", signed=False)


def write_payload(smu: Bc250Smu, payload: bytes) -> None:
    """Write a binary payload as little-endian 32-bit words."""
    for offset in range(0, len(payload), 4):
        word = payload[offset:offset + 4].ljust(4, b"\x00")
        smu.smu_write32(PAYLOAD_START + offset, int.from_bytes(word, "little"))


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        print("error: needs root; run with sudo", file=sys.stderr)
        return 1

    try:
        payload, handlers = preflight()
    except (OSError, ValueError) as exc:
        print(f"preflight failed: {exc}", file=sys.stderr)
        return 1

    smu = None
    old_slots: dict[int, int] = {}
    attempted_slots: list[int] = []
    try:
        smu = Bc250Smu()

        # This read works before unlock (unlock.py itself uses smu_read before unlocking).
        actual_version = read_smu_u32(smu, SMU_VERSION_ADDR)
        if actual_version != EXPECTED_SMU_VERSION:
            raise RuntimeError(
                f"unsupported SMU version 0x{actual_version:08X}; expected "
                f"0x{EXPECTED_SMU_VERSION:08X}; refusing to patch"
            )
        print(f"SMU version verified: 0x{actual_version:08X}")

        unlock(smu)

        # Keep the old handler words. The adjacent flags words are never written.
        for info in handlers.values():
            slot = int(info["slot"])
            old_slots[slot] = read_smu_u32(smu, slot)

        # A pointer into the reserved payload range indicates an installed/partial patch.
        installed = {
            message_id: (int(info["slot"]), old_slots[int(info["slot"])])
            for message_id, info in handlers.items()
            if PAYLOAD_START <= old_slots[int(info["slot"])] < PAYLOAD_END
        }
        if installed and not args.force:
            details = ", ".join(
                f"msg 0x{message_id:02X}=0x{ptr:08X} (slot 0x{slot:08X})"
                for message_id, (slot, ptr) in installed.items()
            )
            raise RuntimeError(
                "Queue 3 already has handler pointer(s) inside the payload region: " + details + ". "
                "Refusing to overwrite an installed/partial patch; use -f to force overwrite."
            )
        if installed and args.force:
            print("WARNING: -f specified; overwriting payload while Queue 3 has existing payload pointers.")

        write_payload(smu, payload)

        try:
            for message_id, info in handlers.items():
                slot = int(info["slot"])
                address = int(info["address"])
                attempted_slots.append(slot)
                # Write only the first u32 in {handler:u32, flags:u32}; flags remain unchanged.
                smu.smu_write32(slot, address)
                actual_pointer = read_smu_u32(smu, slot)
                if actual_pointer != address:
                    raise RuntimeError(
                        f"readback mismatch for message 0x{message_id:02X}: "
                        f"wrote 0x{address:08X}, read 0x{actual_pointer:08X}"
                    )
        except Exception:
            rollback_errors = []
            for slot in reversed(attempted_slots):
                try:
                    smu.smu_write32(slot, old_slots[slot])
                except Exception as rollback_exc:
                    rollback_errors.append(f"0x{slot:08X}: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    "handler update failed and rollback was incomplete: " + "; ".join(rollback_errors)
                )
            raise

        for message_id, info in handlers.items():
            print(
                f"Patched Queue 3 message 0x{message_id:02X}: "
                f"slot 0x{int(info['slot']):08X} -> 0x{int(info['address']):08X} ({info['name']})"
            )
        print("SMU payload installed successfully.")
        return 0
    except Exception as exc:
        print(f"patch failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if smu is not None:
            smu.close()


if __name__ == "__main__":
    raise SystemExit(main())
