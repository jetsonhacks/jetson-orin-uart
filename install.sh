#!/usr/bin/env bash
# install.sh — Install the UART1 DMA-disable overlay on Jetson Orin Nano / AGX Orin
#
# What this script does:
#   1. Compiles disable-uart1-dma.dts → disable-uart1-dma.dtbo
#      (offers to install dtc if missing; falls back to pre-built .dtbo if declined)
#   2. Copies the .dtbo to /boot/
#   3. Detects the board's FDT using find_fdt.py
#   4. Adds a new 'UARTFix' boot entry to /boot/extlinux/extlinux.conf based
#      on the current default, sets it as the new DEFAULT, and leaves the
#      previous entry intact as a fallback in the boot menu
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

# Extract the full stanza for a given LABEL from extlinux.conf.
# A stanza runs from its LABEL line to the line before the next LABEL (or EOF).
extract_stanza() {
    local label="$1" file="$2"
    awk "/^[[:space:]]*LABEL[[:space:]]+${label}([[:space:]]|$)/{found=1}
         found && /^[[:space:]]*LABEL[[:space:]]/ && !/^[[:space:]]*LABEL[[:space:]]+${label}([[:space:]]|$)/{found=0}
         found{print}" "${file}"
}

update_extlinux() {
    [[ -f "${EXTLINUX}" ]] || die "${EXTLINUX} not found."

    # ── idempotency check ─────────────────────────────────────────────────────
    if grep -qF "${OVERLAY_PATH}" "${EXTLINUX}"; then
        info "Overlay already present in extlinux.conf — nothing to do."
        return
    fi

    # Back up before touching
    local backup="${EXTLINUX}.bak.$(date +%Y%m%d%H%M%S)"
    cp "${EXTLINUX}" "${backup}"
    info "Backed up extlinux.conf → ${backup}"

    # ── find the current default label ────────────────────────────────────────
    local current_default
    current_default="$(grep -E "^\s*DEFAULT\s" "${EXTLINUX}" | awk '{print $2}' | head -1)"
    [[ -n "${current_default}" ]] || die "Could not find DEFAULT label in ${EXTLINUX}"
    info "Current default label: ${current_default}"

    # ── extract the current default stanza ───────────────────────────────────
    local stanza
    stanza="$(extract_stanza "${current_default}" "${EXTLINUX}")"
    [[ -n "${stanza}" ]] || die "Could not extract stanza for label '${current_default}'"

    # ── build the new stanza ──────────────────────────────────────────────────
    local new_label="UARTFix"
    local timestamp
    timestamp="$(date +%Y-%m-%d-%H%M%S)"

    # Replace the LABEL and MENU LABEL lines; ensure FDT and OVERLAYS are set
    local new_stanza
    new_stanza="$(echo "${stanza}" \
        | sed "s|^[[:space:]]*LABEL[[:space:]].*|LABEL ${new_label}|" \
        | sed "s|MENU LABEL.*|MENU LABEL UART DMA Fix [${timestamp}]|")"

    # Add or update FDT line
    if echo "${new_stanza}" | grep -qE "^\s*FDT\s"; then
        : # already present — leave it
    else
        new_stanza="$(echo "${new_stanza}" \
            | sed "/^\s*LINUX\s/a\\      FDT ${FDT}")"
    fi

    # Add or append OVERLAYS line
    if echo "${new_stanza}" | grep -qE "^\s*OVERLAYS\s"; then
        new_stanza="$(echo "${new_stanza}" \
            | sed "s|^\(\s*OVERLAYS\s.*\)$|\1,${OVERLAY_PATH}|")"
    else
        new_stanza="$(echo "${new_stanza}" \
            | sed "/^\s*FDT\s/a\\      OVERLAYS ${OVERLAY_PATH}")"
    fi

    # ── write new stanza and update DEFAULT ───────────────────────────────────
    printf "\n%s\n" "${new_stanza}" >> "${EXTLINUX}"
    sed -i "s|^\(\s*DEFAULT\s\).*|\1${new_label}|" "${EXTLINUX}"

    info "Added new boot entry '${new_label}' and set it as DEFAULT."
    info "Previous entry '${current_default}' remains as a fallback in the boot menu."
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
    echo " The boot menu now has a new 'UARTFix' entry set as default."
    echo " Your previous boot entry remains in the menu as a fallback."
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