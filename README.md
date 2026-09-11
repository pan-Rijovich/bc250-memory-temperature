# ASRock BC-250 GDDR6 Memory Temperature

A small reverse-engineered tool for reading the **GDDR6 memory temperature** on the **ASRock BC-250**.

The tool uses the **DRAM Info mode of Mode Register 3 (MR3)** defined by the JEDEC GDDR6 standard (JESD250D). MR3 `OP[7:6]` selects the DRAM Info function, which allows the DRAM to output device information such as **Vendor ID** or **Temperature Readout** onto the DQ bus.

For temperature monitoring, the tool selects the MR3 **Temperature Readout** mode and reads the temperature reported by each GDDR6 memory chip.

## ⚠️ Warning

> **Use at your own risk.**
>
> This project accesses and modifies low-level SMU/PHY state on the ASRock BC-250 in order to access the GDDR6 DRAM Info interface.
>
> An incorrect SMU configuration, an incorrect firmware address, an incompatible payload, or an incorrect DQ mapping can cause **memory corruption, data corruption, system instability, crashes, or an unbootable system**.
>
> Do not use the SMU patch with a different firmware version unless the relevant addresses and payload have been verified.

## Features

* Reads GDDR6 temperature from all 8 memory chips.
* Uses the JEDEC GDDR6 **MR3 DRAM Info / Temperature Readout** mechanism.
* Displays the raw MR3 response and decoded temperature.
* Calculates:

  * **Average** — average temperature across all memory chips.
  * **Hotspot** — highest reported memory temperature and the corresponding chip.

## JEDEC MR3 DRAM Info

JESD250D defines MR3 `OP[7:6]` as the **DRAM Info** selector:

| MR3 `OP[7:6]` | DRAM Info function  |
| ------------- | ------------------- |
| `00`          | DRAM Info disabled  |
| `01`          | Vendor ID (ID1)     |
| `10`          | Temperature Readout |
| `11`          | Vendor ID (ID2)     |

The temperature is the DRAM device's **junction temperature**. The DRAM internally samples the temperature when the MR3 command enables the temperature readout and continuously drives the corresponding digital value onto the DQ bus.

The current implementation converts the temperature code according to the JEDEC linear encoding:

```text
Temperature (°C) = (Temperature Code × 2) - 40
```

## Reading memory temperature

Run:

```bash
sudo python3 read_temp.py
```

The script reads the temperature from all 8 GDDR6 chips and displays a table containing:

* chip number;
* raw MR3 response;
* temperature code;
* decoded temperature.

It then reports the memory **Average** and **Hotspot**.

Example:

```text
+--------+------------+-------------+---------------+
|   Chip | MR3 raw    | Temp code   | Temperature   |
+========+============+=============+===============+
|      0 | 0x00002626 | 0x26        | 36.0 °C       |
+--------+------------+-------------+---------------+
|      1 | 0x00002525 | 0x25        | 34.0 °C       |
+--------+------------+-------------+---------------+
|      2 | 0x00002A2A | 0x2A        | 44.0 °C       |
+--------+------------+-------------+---------------+
|      3 | 0x00002626 | 0x26        | 36.0 °C       |
+--------+------------+-------------+---------------+
|      4 | 0x00002626 | 0x26        | 36.0 °C       |
+--------+------------+-------------+---------------+
|      5 | 0x00002929 | 0x29        | 42.0 °C       |
+--------+------------+-------------+---------------+
|      6 | 0x00002929 | 0x29        | 42.0 °C       |
+--------+------------+-------------+---------------+
|      7 | 0x00002727 | 0x27        | 38.0 °C       |
+--------+------------+-------------+---------------+

Average : 38.5 °C
Hotspot : 44.0 °C (Chip 2)
```

## How it works

The GDDR6 temperature is not read directly by the Linux-side tool.

The temperature request is sent through the SMU using **Queue 3 / Message 5**. The request is then handled by the UMC firmware.

The relevant UMC firmware flow is:

```text
Linux tool
    ↓
SMU Queue 3 / Message 5
    ↓
umc_read_temp_per_chip
    ↓
UMC mailbox
    ↓
GDDR6 MR3 DRAM Info / Temperature Readout
    ↓
UMC mailbox response
    ↓
SMU Queue response
    ↓
Linux tool
```

`umc_read_temp_per_chip(qid)` is the handler executed when Queue 3 / Message 5 is processed.

For each request, the handler:

1. Selects the corresponding UMC IP.
2. Initiates the GDDR6 MR3 DRAM Info temperature read.
3. Waits for the operation to complete.
4. Waits for the returned temperature value to become valid.
5. Stores the result in the queue response.
6. Marks the queue request as completed.

The Linux-side tool therefore acts as a frontend for the existing SMU/UMC firmware temperature-read path.

## SMU payload

`patch_smu.py` installs a custom payload into the SMU memory and redirects the Queue 3 / Message 5 entry point to the patched umc_read_temp_per_chip() implementation.

The payload replaces the firmware handling of **Queue 3 / Message 5** with a patched implementation of:

The current payload is intended for:

```text
ASRock BC-250 P3.0
```

The payload is written starting at:

```text
0x0003AA9C
```

The current address of `umc_read_temp_per_chip()` is:

```text
0x0003AAC4
```

### Rebuilding the payload

The address of `umc_read_temp_per_chip()` is **payload-dependent** and may change when `SMUPayload.elf` is modified or rebuilt.

After rebuilding `SMUPayload.elf`, verify the function address with:

```bash
./xtensa-esp32-elf-gcc-5.2.0/bin/xtensa-esp32-elf-nm \
    SMUPayload.elf | grep umc_read_temp_per_chip
```

Compare the reported address with:

```text
0x0003AAC4
```

If the address is different, update `QUEUE_3_MSG_5_HANDLER` in `patch_smu.py` with the new address.

### Applying the SMU patch

Make sure `SMUPayload.bin` is present, then run:

```bash
sudo python3 patch_smu.py
```

The patcher unlocks SMU access, writes the payload, and updates SMU register `0x748C` so that Queue 3 / Message 5 is handled by the patched `umc_read_temp_per_chip()` implementation.

## Requirements

* ASRock BC-250
* Linux
* Python 3
* Root privileges
* The Xtensa toolchain used to build the SMU payload

Install the Python dependencies with:

```bash
python3 -m pip install -r requirements.txt
```

### Xtensa toolchain

The Xtensa toolchain is **not included in this repository** and must be downloaded separately.

The SMU payload is built using an Xtensa ESP32 GCC 5.2.0 toolchain. After downloading it, place the toolchain directory in the project root:

```text
xtensa-esp32-elf-gcc-5.2.0/
```

The expected compiler path is:

```text
xtensa-esp32-elf-gcc-5.2.0/bin/xtensa-esp32-elf-gcc
```

## Project structure

```text
bc250-memory-temperature/
├── bc250_smu/
├── read_temp.py
├── patch_smu.py
├── main.c
├── Makefile
├── smu3.ld
├── SMUPayload.bin
├── SMUPayload.elf
├── unlock.py
├── xtensa-esp32-elf-gcc-5.2.0/
├── requirements.txt
├── LICENSE
└── README.md
```

## Disclaimer

This project is intended for research, reverse engineering, and experimentation with the ASRock BC-250.

Low-level access to the SMU, PHY, and DRAM interface can affect system operation. **Memory corruption and data corruption are possible if the wrong firmware, addresses, payload, or DQ mapping are used.**

