#!/usr/bin/env python3

"""
find_fdt.py - Finds the Flattened Device Tree (FDT/DTB) file for the running machine.

Approach:
  1. Read 'compatible' from the live device tree (/proc/device-tree).
  2. Extract the board ID pair (e.g. 'p3768-0000+p3767-0005') from the compatible string.
  3. Find DTB files in /boot/dtb whose filename contains those board IDs.
  4. Prefer base BSP DTB files over jetson-io merged DTBs when multiple matches exist.

Note: Comparing /proc/device-tree properties directly against DTB file contents
does not work because UEFI applies overlays before the kernel boots, causing the
live compatible/model strings to differ from those stored in the base DTB file.

Usage:
  python3 find_fdt.py                  # verbose output
  python3 find_fdt.py --quiet          # print only the DTB path (for scripting)
  python3 find_fdt.py /custom/dtb/dir  # search a different directory
  python3 find_fdt.py /custom/dtb/dir --quiet
"""

import glob
import os
import re
import subprocess
import sys

# ── tunables ──────────────────────────────────────────────────────────────────
PROC_DT_ROOT    = "/proc/device-tree"   # live device-tree root
DTB_SEARCH_PATH = "/boot/dtb"           # where DTB files live on Jetson

# Matches base BSP DTB filenames, e.g.:
#   kernel_tegra234-p3768-0000+p3767-0005-nv-super.dtb   (Orin Nano Super)
#   kernel_tegra234-p3737-0000+p3701-0000-nv.dtb          (AGX Orin)
# Excludes jetson-io merged DTBs which append extra suffixes, e.g.:
#   kernel_tegra234-p3768-0000+p3767-0005-nv-super-hdr40-user-custom.dtb
_BASE_DTB_RE = re.compile(
    r"kernel_tegra\d+-.+-nv(?:-[a-z]+)?\.dtb$"
)

# Extracts the combined module+carrier board ID pair from a compatible string.
# 'nvidia,p3768-0000+p3767-0005-super' -> 'p3768-0000+p3767-0005'
_BOARD_ID_RE = re.compile(r"p\d+-\d+\+p\d+-\d+")
# ─────────────────────────────────────────────────────────────────────────────


# ── subprocess helpers ────────────────────────────────────────────────────────

def _call(cmd: str) -> int:
    """Run a shell command; return its exit code (stdout/stderr suppressed)."""
    with open(os.devnull, "w") as devnull:
        return subprocess.call(cmd, shell=True, stdout=devnull, stderr=devnull)


# ── live device-tree helpers ──────────────────────────────────────────────────

def _dt_path(prop: str) -> str:
    return os.path.join(PROC_DT_ROOT, prop.lstrip("/"))


def dt_prop_exists(prop: str) -> bool:
    """Return True if the device-tree property exists in /proc/device-tree."""
    return os.path.exists(_dt_path(prop))


def dt_read_prop(prop: str) -> str:
    """
    Read a property from the live device tree; return the first value.
    /proc/device-tree files use null bytes as separators between string-list
    entries, so we split on 0x00 and return the first non-empty token.
    """
    path = _dt_path(prop)
    if not os.path.exists(path):
        raise RuntimeError(f"Device-tree property not found: {prop}")
    with open(path, "rb") as f:
        raw = f.read()
    return raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()


def dt_read_prop_all(prop: str) -> list[str]:
    """Return all null-separated values for a device-tree property."""
    path = _dt_path(prop)
    if not os.path.exists(path):
        raise RuntimeError(f"Device-tree property not found: {prop}")
    with open(path, "rb") as f:
        raw = f.read()
    return [v.decode("utf-8", errors="replace").strip()
            for v in raw.split(b"\x00") if v]


# ── tool check ────────────────────────────────────────────────────────────────

def _check_tools(*tools: str) -> None:
    """
    Verify that each required external tool is on PATH.
    Raises RuntimeError listing any that are missing.
    """
    missing = [t for t in tools if _call(f"which {t}") != 0]
    if missing:
        raise RuntimeError(
            f"Required tool(s) not found: {', '.join(missing)}. "
            "Install with: sudo apt install device-tree-compiler"
        )


# ── DTB matching ──────────────────────────────────────────────────────────────

def _board_ids_from_compat(compat: str) -> str:
    """
    Extract the board ID pair from a compatible string.

    The compatible string from /proc/device-tree takes the form:
      'nvidia,p3768-0000+p3767-0005-super'
    This function returns the pNNNN-NNNN+pNNNN-NNNN portion which appears
    verbatim in the corresponding DTB filename:
      'kernel_tegra234-p3768-0000+p3767-0005-nv-super.dtb'

    Raises RuntimeError if the board ID pattern cannot be found.
    """
    match = _BOARD_ID_RE.search(compat)
    if not match:
        raise RuntimeError(
            f"Could not extract board IDs from compatible string: {compat!r}\n"
            "Expected format: 'nvidia,pNNNN-NNNN+pNNNN-NNNN[-variant]'"
        )
    return match.group(0)


def _is_base_dtb(path: str) -> bool:
    """
    Return True if the filename looks like a base BSP DTB rather than a
    jetson-io merged DTB.
    """
    return bool(_BASE_DTB_RE.search(os.path.basename(path)))


def find_matching_dtb(board_ids: str, dtb_dir: str) -> list[str]:
    """
    Search dtb_dir for DTB filenames containing the given board ID pair.
    Returns base BSP DTBs preferentially; falls back to all matches if none
    match the base pattern (e.g. on a system without the standard BSP naming).
    """
    all_matches = [
        dtb for dtb in sorted(
            glob.glob(os.path.join(dtb_dir, "**", "*.dtb"), recursive=True)
        )
        if board_ids in os.path.basename(dtb)
    ]

    if not all_matches:
        return []

    base_matches = [p for p in all_matches if _is_base_dtb(p)]
    return base_matches if base_matches else all_matches


# ── main logic ────────────────────────────────────────────────────────────────

def find_fdt(dtb_dir: str = DTB_SEARCH_PATH, quiet: bool = False) -> str:
    """
    Return the path to the FDT/DTB file that matches the running board.
    Raises RuntimeError if no match or if ambiguity cannot be resolved.
    """
    _check_tools("dtc")

    compat    = dt_read_prop("compatible")
    model     = dt_read_prop("model")
    board_ids = _board_ids_from_compat(compat)

    if not quiet:
        print(f"Board compatible : {compat!r}")
        print(f"Board model      : {model!r}")
        print(f"Board IDs        : {board_ids!r}")
        print(f"Searching in     : {dtb_dir}")
        print()

    if not os.path.isdir(dtb_dir):
        raise RuntimeError(f"DTB directory not found: {dtb_dir}")

    matches = find_matching_dtb(board_ids, dtb_dir)

    if not matches:
        raise RuntimeError(
            f"No DTB found for board IDs {board_ids!r} in {dtb_dir}\n"
            "Ensure JetPack is correctly installed and /boot/dtb is populated."
        )

    if len(matches) > 1:
        # Should not happen after base-DTB filtering, but handle it explicitly
        raise RuntimeError(
            f"Multiple DTB files matched board IDs {board_ids!r} and could not "
            f"be disambiguated automatically:\n"
            + "\n".join(f"  {p}" for p in matches)
            + "\nSpecify the correct DTB directory or remove ambiguous files."
        )

    return matches[0]


if __name__ == "__main__":
    args    = sys.argv[1:]
    quiet   = "--quiet" in args or "-q" in args
    args    = [a for a in args if a not in ("--quiet", "-q")]
    dtb_dir = args[0] if args else DTB_SEARCH_PATH

    try:
        fdt = find_fdt(dtb_dir=dtb_dir, quiet=quiet)
        if quiet:
            # Machine-readable: just the path, nothing else
            print(fdt)
        else:
            print(f"FDT file: {fdt}")
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
