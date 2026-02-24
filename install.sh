#!/usr/bin/env bash
# install.sh — Install the UART1 DMA-disable overlay on Jetson Orin Nano / AGX Orin
#
# What this script does:
#   1. Compiles disable-uart1-dma.dts → disable-uart1-dma.dtbo
#      (offers to install dtc if missing; falls back to pre-built .dtbo if declined)
#   2. Copies the .dtbo to /boot/
#   3. Detects the board's FDT using find_fdt.py
#   4. Edits /boot/extlinux/extlinux.conf to append the overlay to the OVERLAYS
#      line (or adds one), and ensures an FDT line is present
#
# Usage:
#   sudo bash install.sh
#
# Requires: Python 3

set -euo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DTS_SRC="${SCRIPT_DIR}/disable-uart1-dma.dts"
DTBO_SRC="${SCRIPT_DIR}/disable-uart1-dma.dtbo"   # pre-built fallback
DTBO_DEST="/boot/disable-uart1-dma.dtbo"
EXTLINUX="/boot/extlinux/extlinux.conf"
FIND_FDT="${SCRIPT_DIR}/find_fdt.py"

OVERLAY_PATH="/boot/disable-uart1-dma.dtbo"

# ── helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

require_root() {
    [[ "${EUID}" -eq 0 ]] || die "This script must be run as root (use sudo)."
}

# ── step 1: compile overlay ───────────────────────────────────────────────────
install_dtc() {
    echo ""
    read -r -p "[PROMPT] Install device-tree-compiler now? [Y/n] " yn
    case "${yn}" in
        [nN]*) return 1 ;;
        *)
            info "Running: apt install -y device-tree-compiler"
            apt install -y device-tree-compiler || die "apt install failed."
            return 0
            ;;
    esac
}

compile_overlay() {
    if ! command -v dtc &>/dev/null; then
        warn "dtc (device-tree-compiler) is not installed."
        if install_dtc; then
            info "dtc installed successfully."
        elif [[ -f "${DTBO_SRC}" ]]; then
            warn "Skipping compilation — using pre-built ${DTBO_SRC}"
            cp "${DTBO_SRC}" "${DTBO_DEST}"
            info "Pre-built overlay copied → ${DTBO_DEST}"
            return
        else
            die "dtc is not installed and no pre-built .dtbo was found. " \
                "Install manually with: sudo apt install device-tree-compiler"
        fi
    fi

    info "Compiling ${DTS_SRC} ..."
    dtc -@ -I dts -O dtb -o "${DTBO_DEST}" "${DTS_SRC}"
    info "Overlay compiled → ${DTBO_DEST}"
}

# ── step 2: detect FDT ────────────────────────────────────────────────────────
detect_fdt() {
    if [[ ! -f "${FIND_FDT}" ]]; then
        die "find_fdt.py not found at ${FIND_FDT}"
    fi
    FDT="$(python3 "${FIND_FDT}" --quiet)" || \
        die "find_fdt.py failed. Is this a supported Jetson board running JetPack 6?"
    info "Detected FDT: ${FDT}"
}

# ── step 3: update extlinux.conf ──────────────────────────────────────────────
update_extlinux() {
    [[ -f "${EXTLINUX}" ]] || die "${EXTLINUX} not found."

    # Back up before touching
    local backup="${EXTLINUX}.bak.$(date +%Y%m%d%H%M%S)"
    cp "${EXTLINUX}" "${backup}"
    info "Backed up extlinux.conf → ${backup}"

    # ── FDT line ──────────────────────────────────────────────────────────────
    if grep -qE "^\s*FDT\s" "${EXTLINUX}"; then
        info "FDT line already present — not modified."
    else
        info "Adding FDT line for ${FDT}"
        # Insert after the LABEL line (first occurrence)
        sed -i "/^\s*LABEL\s/a\\      FDT ${FDT}" "${EXTLINUX}"
    fi

    # ── OVERLAYS line ─────────────────────────────────────────────────────────
    if grep -qF "${OVERLAY_PATH}" "${EXTLINUX}"; then
        info "Overlay already present in extlinux.conf — nothing to do."
        return
    fi

    if grep -qE "^\s*OVERLAYS\s" "${EXTLINUX}"; then
        info "Appending overlay to existing OVERLAYS line."
        # Append ,/boot/disable-uart1-dma.dtbo to the OVERLAYS value
        sed -i "s|^\(\s*OVERLAYS\s.*\)$|\1,${OVERLAY_PATH}|" "${EXTLINUX}"
    else
        info "No OVERLAYS line found — adding one."
        sed -i "/^\s*FDT\s/a\\      OVERLAYS ${OVERLAY_PATH}" "${EXTLINUX}"
    fi

    info "extlinux.conf updated."
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
    require_root
    compile_overlay
    detect_fdt
    update_extlinux

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo " Installation complete."
    echo " Reboot to apply the overlay:"
    echo "   sudo reboot"
    echo ""
    echo " After rebooting, verify PIO mode with:"
    echo "   sudo dmesg | grep -i '3100000\|pio\|dma'"
    echo " You should see:"
    echo "   serial-tegra 3100000.serial: RX in PIO mode"
    echo "   serial-tegra 3100000.serial: TX in PIO mode"
    echo ""
    echo " NOTE: If you run jetson-io after this install, it will"
    echo " overwrite the OVERLAYS line. Re-run this script afterwards."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

main "$@"
