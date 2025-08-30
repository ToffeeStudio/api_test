import time
import hid
import struct
import sys
import serial
import serial.tools.list_ports
from enum import IntEnum

# --- Device Identification ---
# Use the same constants from your original script
VID = 0x1067         # (4199) Vendor ID for your device
PID = 0x626D         # (25197) Product ID for your device
USAGE_PAGE = 0xFF60  # (65376) Raw HID Usage Page
USAGE = 0x61         # (97) Raw HID Usage
CDC_PRODUCT_STRING = "Module CDC Interface" # Or part of the description

# --- Firmware Command ---
class CommandID(IntEnum):
    MODULE_CMD_DUMP_FILES_CDC = 0x61 # The command to trigger the dump

# --- HID Packet Configuration ---
PACKET_SIZE = 32  # Adjust to match RAW_EPSIZE on your device firmware
MAGIC_BYTE = 0x09 # Magic byte expected by firmware

# --- Helper to find CDC Port (Simplified from original) ---
def find_cdc_port(vid=VID, pid=PID, product=CDC_PRODUCT_STRING):
    print(f"Searching for CDC port with VID={vid:04X}, PID={pid:04X}, Product='{product}'...")
    ports = serial.tools.list_ports.comports()
    for p in ports:
        print(f"  Checking port: {p.device}, VID={p.vid}, PID={p.pid}, Product={p.product}, Desc={p.description}")
        # Exact VID/PID Match
        if p.vid == vid and p.pid == pid:
            # Prefer match by product string if available
            if p.product and product in p.product:
                print(f"  Found matching CDC port by VID/PID and Product: {p.device}")
                return p.device
            # Fallback: Check description if product doesn't match/exist
            if "CDC" in (p.description or ""):
                 print(f"  Found matching CDC port by VID/PID and 'CDC' in description: {p.device}")
                 return p.device

        # Fallback for Windows using HWID if VID/PID fields are None (sometimes happens)
        hwid = p.hwid or ""
        if f"VID_{vid:04X}&PID_{pid:04X}" in hwid or f"VID:PID={vid:04X}:{pid:04X}" in hwid:
             print(f"  Found matching CDC port by HWID inspection: {p.device}")
             return p.device

    return None
