# jetson-orin-uart

Fix for the UART DMA initialization bug on Jetson Orin Nano (and likely AGX Orin) under JetPack 6.2.2 (L4T 36.x).

---

## The Problem

On the Jetson Orin Nano expansion header UART (`ttyTHS1`), the **first 96 bytes of every received transmission read as `0x00`** regardless of what was actually sent.

The root cause is a bug in the `serial-tegra` driver when it operates in DMA mode. Under JetPack 6.2.2, UEFI injects `dmas` and `dma-names` properties into the `serial@3100000` device tree node at boot, enabling DMA mode. The driver then fails to initialize the beginning of its DMA RX buffer, silently zeroing those bytes.

You can confirm the affected driver with:

```bash
sudo dmesg | grep 3100000
```

You will see something like `serial-tegra 3100000.serial: ...`.

---

## The Fix

A device tree overlay removes the `dmas` and `dma-names` properties before the kernel boots, forcing `serial-tegra` to fall back to PIO (interrupt-driven) mode. In PIO mode the bug does not occur.

The overlay uses `delete_prop`, a **Tegra/NVIDIA-specific extension** to the standard DTS overlay format. This is necessary because UEFI injects those properties during its own pass — a normal kernel-level overlay deletion would run too late and be overridden. `delete_prop` is processed by UEFI's plugin manager before the kernel starts. It is documented in the [NVIDIA Jetson Linux Developer Guide](https://docs.nvidia.com/jetson/archives/r36.4/DeveloperGuide/index.html) under the UEFI Adaptation section.

After applying the fix, `dmesg` will confirm PIO mode:

```
serial-tegra 3100000.serial: RX in PIO mode
serial-tegra 3100000.serial: TX in PIO mode
```

---

## Affected Hardware

| Board | Status |
|---|---|
| Jetson Orin Nano Dev Kit | ✅ Tested and confirmed |
| Jetson Orin Nano Super Dev Kit | ✅ Tested and confirmed |
| Jetson AGX Orin | ⚠️ Same driver and UART address — likely affected, not yet tested |

---

## Installation

### Prerequisites

```bash
sudo apt install device-tree-compiler python3
```

### Steps

Clone the repo and run the install script:

```bash
git clone https://github.com/jetsonhacks/jetson-orin-uart.git
cd jetson-orin-uart
sudo bash install.sh
sudo reboot
```

The script will:
1. Compile `disable-uart1-dma.dts` into `disable-uart1-dma.dtbo` and copy it to `/boot/` (falls back to the pre-built `.dtbo` if `dtc` is unavailable)
2. Auto-detect your board's FDT using `find_fdt.py`
3. Add a new `UARTFix` boot entry to `/boot/extlinux/extlinux.conf` based on the current default configuration, set it as the new default, and leave the previous entry intact as a fallback in the boot menu

A timestamped backup of `extlinux.conf` is created before any changes are made.

---

## Verification

After rebooting, check that PIO mode is active:

```bash
sudo dmesg | grep -i '3100000\|pio\|dma'
```

Expected output:

```
serial-tegra 3100000.serial: RX in PIO mode
serial-tegra 3100000.serial: TX in PIO mode
```

Then test your UART. The first bytes of received data should now be correct rather than zero.

---

## Loopback Test

A loopback test script is included to verify the fix end-to-end. It sends 128 non-zero bytes over the UART and checks that they are received correctly from the first byte.

**Prerequisites:** pyserial is required:

```bash
pip3 install pyserial
```

**Hardware setup:** Connect a jumper wire between **TX (pin 8)** and **RX (pin 10)** on the 40-pin expansion header before running the test. Remove it afterwards.

**Permissions:** Add your user to the `dialout` group to access the UART port without sudo:

```bash
sudo usermod -aG dialout $USER
newgrp dialout
```

`newgrp dialout` activates the group membership in your current shell immediately, without needing to log out and back in.

**Run the test:**

```bash
python3 loopback_test.py
```

On a fixed board you will see:

```
PASS: All bytes received correctly — UART is operating in PIO mode.
```

On an unfixed board, the first 96 bytes will read as `0x00` and the test will report:

```
FAIL: 96 zero byte(s) found at positions: [0, 1, 2, ...]
This is the DMA initialization bug. Is the overlay installed and the board rebooted?
```

Options:

```
--port  Serial port to use (default: /dev/ttyTHS1)
--baud  Baud rate (default: 115200)
```

---

## ⚠️ Warning: jetson-io Compatibility

The `jetson-io` tool **adds a new entry and updates the `DEFAULT` line** in `extlinux.conf` when you use it to configure the expansion header. If you run `jetson-io` after installing this fix, its new entry will become the default and the UART fix will no longer be active. Re-run the install script to add a new `UARTFix` entry on top of jetson-io's configuration:

```bash
sudo bash install.sh
sudo reboot
```

---

## Uninstallation

Open `/boot/extlinux/extlinux.conf` and either revert the `DEFAULT` line to your previous label, or remove the `UARTFix` stanza entirely.

```bash
sudo nano /boot/extlinux/extlinux.conf
sudo reboot
```

To revert to the previous default, change the `DEFAULT` line at the top of the file back to its original label (e.g. `primary` or `JetsonIO`). The `UARTFix` entry can then be left in place or deleted — it will no longer be the default.

The DMA bug will return after rebooting without the overlay active.

---

## Repository Contents

```
jetson-orin-uart/
├── README.md                   # this file
├── disable-uart1-dma.dts       # overlay source
├── disable-uart1-dma.dtbo      # pre-compiled convenience binary
├── find_fdt.py                 # auto-detects the board's FDT/DTB path
├── install.sh                  # install script
└── loopback_test.py            # verifies the fix with a hardware loopback test
```

### find_fdt.py

`find_fdt.py` reads `/proc/device-tree/compatible`, extracts the board ID pair, and matches it against DTB filenames in `/boot/dtb/`. It correctly handles the fact that UEFI modifies the device tree before Linux boots, which makes comparing live properties against DTB file contents unreliable — so it matches on filenames only. It filters out jetson-io merged DTBs, preferring base BSP DTBs.

```bash
# Verbose (shows board info and detected DTB path):
python3 find_fdt.py

# Quiet (prints only the DTB path, useful in scripts):
FDT=$(python3 find_fdt.py --quiet)
```

---

## Background: What is a Device Tree Overlay?

The Linux kernel uses a Device Tree to describe hardware — which peripherals exist, where they are in memory, and how they are configured. On Jetson, UEFI loads the base device tree and applies overlays before handing off to the kernel. An overlay is a small patch to the tree, expressed in DTS (Device Tree Source) format and compiled to a binary `.dtbo` file.

This repo's overlay targets the node at `/bus@0/serial@3100000`, which represents the expansion header UART, and removes the two properties that enable DMA mode — leaving the driver to operate in the simpler, bug-free PIO mode.

---

## License

MIT License
Copyright (c) 2026 JetsonHacks

## Release

### Initial Release
* February, 2026
* JetPack 6.2.2
* Tested on Jetson Orin Nano Developer Kit