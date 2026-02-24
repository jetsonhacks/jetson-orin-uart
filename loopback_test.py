#!/usr/bin/env python3
"""
loopback_test.py — UART loopback test for the Jetson Orin Nano expansion header.

Verifies that the UART DMA fix is in effect by checking that the first bytes of
a received transmission are not zeroed out.

Hardware setup required:
  Connect a jumper wire between TX (pin 8) and RX (pin 10) on the 40-pin
  expansion header before running this test. Remove the jumper afterwards.

Usage:
  sudo python3 loopback_test.py
  sudo python3 loopback_test.py --port /dev/ttyTHS1 --baud 115200
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("ERROR: pyserial is not installed.")
    print("Install it with:  pip3 install pyserial")
    sys.exit(1)

# ── defaults ──────────────────────────────────────────────────────────────────
DEFAULT_PORT = "/dev/ttyTHS1"
DEFAULT_BAUD = 115200
TIMEOUT      = 2.0   # seconds to wait for RX data

# The test payload must be longer than 96 bytes and contain no zero bytes, so
# that any bytes incorrectly received as 0x00 are immediately obvious.
# We use a repeating sequence of 0x01–0xFF (128 bytes).
PAYLOAD = bytes([(i % 255) + 1 for i in range(128)])


# ── helpers ───────────────────────────────────────────────────────────────────
def print_hex(label: str, data: bytes, width: int = 16) -> None:
    print(f"\n  {label}:")
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        print(f"    {i:4d}: {hex_part}")


def check_port_permissions(port: str) -> None:
    """
    Warn the user if they are unlikely to have permission to open the port.
    Prints actionable guidance rather than letting serial.Serial raise a
    cryptic PermissionError.
    """
    import grp
    import os
    import stat

    if not os.path.exists(port):
        print(f"ERROR: Port {port} does not exist.")
        print("Is the correct JetPack version installed? Expected port: /dev/ttyTHS1")
        sys.exit(1)

    # Check if the port is owned by / group-accessible by the current user
    uid  = os.getuid()
    info = os.stat(port)
    mode = info.st_mode

    # Root always has access
    if uid == 0:
        return

    # Check group membership
    try:
        dialout_gid = grp.getgrnam("dialout").gr_gid
    except KeyError:
        dialout_gid = None

    user_gids = os.getgroups()
    port_gid  = info.st_gid

    in_port_group    = port_gid in user_gids
    in_dialout_group = dialout_gid in user_gids if dialout_gid else False
    group_readable   = bool(mode & stat.S_IRGRP) and bool(mode & stat.S_IWGRP)

    if not (in_port_group or in_dialout_group) or not group_readable:
        print(f"WARNING: You may not have permission to access {port}.")
        print()
        print("To fix this, add your user to the dialout group:")
        print(f"  sudo usermod -aG dialout $USER")
        print()
        print("Then log out and back in (or run: newgrp dialout), and re-run:")
        print(f"  python3 loopback_test.py")
        print()
        print("Alternatively, run the test with sudo:")
        print(f"  sudo python3 loopback_test.py")
        print()
        # Don't exit — let serial.Serial raise PermissionError if it actually fails


def run_test(port: str, baud: int) -> bool:
    check_port_permissions(port)
    print(f"Opening {port} at {baud} baud ...")
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT,
        )
    except serial.SerialException as e:
        print(f"\nERROR: Could not open {port}: {e}")
        print("Is the port in use? Try: sudo fuser /dev/ttyTHS1")
        return False

    with ser:
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print(f"Sending {len(PAYLOAD)} bytes ...")
        ser.write(PAYLOAD)
        ser.flush()

        # Give the data time to loop back
        time.sleep(0.1)

        received = ser.read(len(PAYLOAD))

    print(f"Received {len(received)} bytes.")

    # ── length check ──────────────────────────────────────────────────────────
    if len(received) == 0:
        print("\nFAIL: No data received.")
        print("Check that TX (pin 8) and RX (pin 10) are jumpered together.")
        return False

    if len(received) < len(PAYLOAD):
        print(f"\nWARN: Only {len(received)} of {len(PAYLOAD)} bytes received.")

    # ── zero-byte check (the DMA bug symptom) ─────────────────────────────────
    zero_positions = [i for i, b in enumerate(received) if b == 0x00]
    if zero_positions:
        print(f"\nFAIL: {len(zero_positions)} zero byte(s) found at positions: "
              f"{zero_positions}")
        print("This is the DMA initialization bug. Is the overlay installed and "
              "the board rebooted?")
        print_hex("Sent    ", PAYLOAD[:len(received)])
        print_hex("Received", received)
        return False

    # ── content check ─────────────────────────────────────────────────────────
    if received != PAYLOAD[:len(received)]:
        mismatches = [i for i, (s, r) in enumerate(zip(PAYLOAD, received)) if s != r]
        print(f"\nFAIL: {len(mismatches)} byte(s) did not match at positions: "
              f"{mismatches}")
        print_hex("Sent    ", PAYLOAD[:len(received)])
        print_hex("Received", received)
        return False

    print("\nPASS: All bytes received correctly — UART is operating in PIO mode.")
    return True


# ── entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=DEFAULT_PORT,
                        help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                        help=f"Baud rate (default: {DEFAULT_BAUD})")
    args = parser.parse_args()

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" Jetson Orin UART Loopback Test")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" Jumper required: TX (pin 8) → RX (pin 10) on expansion header")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()

    passed = run_test(args.port, args.baud)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
