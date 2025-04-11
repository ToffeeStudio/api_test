#!/usr/bin/env python3

import serial
import serial.tools.list_ports
import struct
import time
import argparse
import os
import sys
from typing import Tuple, Optional

# --- Dependency Check ---
try:
    from PIL import Image
except ImportError:
    print("Error: Pillow library not found.")
    print("Please install it: pip install Pillow")
    sys.exit(1)

# --- Constants ---
# Device Identification
EXPECTED_VID = 0x1067
EXPECTED_PID = 0x626D

# Serial Communication Parameters
BAUD_RATE = 115200         # Often ignored for USB CDC, but set anyway
WRITE_TIMEOUT = 10         # Seconds (timeout for write operations)
PRE_SEND_DELAY = 0.1       # Seconds to wait after opening port before sending anything
POST_HEADER_DELAY = 0.1    # Seconds to wait after sending size header, before sending data

# Image Processing Defaults
DEFAULT_RESIZE_W = 128
DEFAULT_RESIZE_H = 128
DEFAULT_BG_COLOR = (0, 0, 0) # Black

# --- Image Processing Functions ---
def rgb565_to_rgb(rgb565: int) -> tuple[int, int, int]:
    """Converts a 16-bit RGB565 value to an 8-bit RGB tuple."""
    r5 = (rgb565 >> 11) & 0x1F
    g6 = (rgb565 >> 5) & 0x3F
    b5 = rgb565 & 0x1F
    r8 = (r5 * 255 + 15) // 31
    g8 = (g6 * 255 + 31) // 63
    b8 = (b5 * 255 + 15) // 31
    return (r8, g8, b8)

def image_to_rgb565(image: Image.Image, background_color: tuple[int, int, int]) -> bytes:
    """
    Converts a PIL Image to raw RGB565 bytes (big-endian).
    Handles transparency by compositing onto the specified background color.
    Returns the byte data.
    """
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        print("Handling transparency...")
        try:
            alpha = image.convert('RGBA').split()[-1]
            bg = Image.new("RGB", image.size, background_color)
            img_rgb = image.convert("RGB")
            bg.paste(img_rgb, mask=alpha)
            image = bg
        except Exception as e:
            print(f"Warning: Error handling transparency: {e}. Trying simple convert.")
            image = image.convert('RGB')
    elif image.mode != 'RGB':
        image = image.convert('RGB')

    print("Converting pixels to RGB565 (Big Endian format)...")
    image_data = bytearray()
    try:
        pixels = list(image.getdata())
        for r, g, b in pixels:
            r5 = r >> 3
            g6 = g >> 2
            b5 = b >> 3
            rgb565 = (r5 << 11) | (g6 << 5) | b5
            # Outputting Big Endian (>H) as that's common for displays,
            # but firmware reconstructs Little Endian for the *size*. Size is separate.
            image_data.extend(struct.pack('>H', rgb565))
    except Exception as e:
        print(f"Error during pixel conversion: {e}")
        return b'' # Return empty bytes on error

    return bytes(image_data)

# --- Serial Port Function ---
def find_port(vid: int, pid: int) -> Optional[str]:
    """Return the device name for the first port matching VID/PID."""
    print(f"Searching for CDC serial port with VID={vid:#06x}, PID={pid:#06x}...")
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return None

    matching_ports = []
    print("Available Serial Ports:")
    for p in ports:
        print(f"  {p.device}: VID={p.vid} PID={p.pid} (Description: {p.description}, HWID: {p.hwid})")
        # Check explicit VID/PID first
        if p.vid == vid and p.pid == pid:
            print(f"    -> Matched VID/PID directly.")
            matching_ports.append(p.device)
            continue # Prioritize direct match
        # Fallback check in HWID string
        vid_pid_str1 = f"VID:PID={vid:04X}:{pid:04X}"
        vid_pid_str2 = f"VID_{vid:04X}&PID_{pid:04X}"
        if vid_pid_str1 in p.hwid or vid_pid_str2 in p.hwid:
            print(f"    -> Matched VID/PID in HWID string.")
            if p.device not in matching_ports: # Avoid duplicates if direct match already found
                 matching_ports.append(p.device)

    if not matching_ports:
        print("-> No matching CDC port found.")
        return None
    elif len(matching_ports) > 1:
         print(f"-> Warning: Found multiple matching ports: {matching_ports}. Using the first one: {matching_ports[0]}")

    print(f"-> Using port: {matching_ports[0]}")
    return matching_ports[0]

# --- Main Execution Logic ---
def main():
    parser = argparse.ArgumentParser(description="Process an image and send its raw RGB565 data over CDC serial using size-header protocol.")
    parser.add_argument("image_path", help="Path to the image file to process and send.")
    # Add optional arguments if needed later (e.g., --port, --resize, --bg-color)

    args = parser.parse_args()

    # 1. Validate and Process Image
    if not os.path.exists(args.image_path):
        print(f"Error: Image file not found: '{args.image_path}'")
        sys.exit(1)

    print(f"\n--- Processing Image: {args.image_path} ---")
    try:
        img = Image.open(args.image_path)
        print(f"Original size: {img.size}")
        # Use default resize for now, could add args later
        target_size = (DEFAULT_RESIZE_W, DEFAULT_RESIZE_H)
        print(f"Resizing to: {target_size}")
        img_resized = img.resize(target_size, Image.Resampling.LANCZOS)

        image_data = image_to_rgb565(img_resized, DEFAULT_BG_COLOR)

        if not image_data:
            print("Error: Failed to convert image to RGB565 data.")
            sys.exit(1)
        print(f"-> Processed data size: {len(image_data)} bytes.")

    except Exception as e:
        print(f"Error processing image: {e}")
        sys.exit(1)

    # 2. Find CDC Port
    print(f"\n--- Locating Device ---")
    port = find_port(EXPECTED_VID, EXPECTED_PID)
    if not port:
        print("Failed to find device port. Aborting.")
        sys.exit(1)

    # 3. Send Data over CDC
    print(f"\n--- Sending Data to {port} ---")
    success = False
    try:
        # Note: Using a 'with' block ensures the port is closed even if errors occur
        with serial.Serial(port, BAUD_RATE, timeout=1, write_timeout=WRITE_TIMEOUT) as ser:
            print(f"Serial port opened (Baud: {BAUD_RATE}, Write Timeout: {WRITE_TIMEOUT}s).")

            # --- Optional: Pre-Send Delay ---
            print(f"Waiting {PRE_SEND_DELAY:.2f}s before sending...")
            time.sleep(PRE_SEND_DELAY)

            # --- Prepare and Send Size Header ---
            data_size = len(image_data)
            # CRITICAL: Pack size as 4-byte unsigned int, Little Endian ('<I')
            size_header = struct.pack('<I', data_size)
            print(f"Data size: {data_size} bytes")
            print(f"Packed size header (Little Endian '<I'): {size_header.hex()}") # Verify this output!

            print(f"Sending size header ({len(size_header)} bytes)...")
            bytes_written_h = ser.write(size_header)
            ser.flush() # Attempt to push buffer immediately
            if bytes_written_h != 4:
                 raise IOError(f"Failed to write full size header (wrote {bytes_written_h})")
            print("-> Header sent and flushed.")

            # --- Post-Header Delay ---
            print(f"Waiting {POST_HEADER_DELAY:.2f}s after header...")
            time.sleep(POST_HEADER_DELAY)

            # --- Send Actual Image Data ---
            print(f"Sending image data block ({data_size} bytes)...")
            start_time = time.time()
            bytes_written_d = ser.write(image_data)
            ser.flush() # Attempt to push buffer immediately
            end_time = time.time()
            print("-> Data sent and flushed.")

            # --- Verify Write Success ---
            if bytes_written_d == data_size:
                duration = end_time - start_time
                rate = (bytes_written_d / duration / 1024) if duration > 1e-6 else float('inf')
                print(f"\nSUCCESS: Wrote {bytes_written_d} bytes in {duration:.3f} seconds ({rate:.2f} KB/s).")
                success = True
            else:
                # This case indicates an issue with pyserial/OS reporting write status
                print(f"\nERROR: Write count mismatch! Tried to write {data_size}, but ser.write() returned {bytes_written_d}.")

    except serial.SerialTimeoutException:
        print(f"\nERROR: Serial write timeout after {WRITE_TIMEOUT}s on port {port}.")
        print("       Check device connection and firmware readiness.")
    except serial.SerialException as e:
        print(f"\nERROR: Serial communication error on port {port}: {e}")
        print("       Check if port is correct and not in use by another program (like 'screen').")
    except IOError as e:
        print(f"\nERROR: I/O error during send: {e}")
    except Exception as e:
        print(f"\nERROR: An unexpected error occurred: {e}")

    print(f"\n--- Operation {'Completed Successfully' if success else 'Failed'} ---")
    sys.exit(0 if success else 1)

# --- Script Entry Point ---
if __name__ == "__main__":
    main()
