#!/usr/bin/env python3

import serial
import serial.tools.list_ports
import struct
import time
import argparse
import os
import sys
from typing import Optional

# --- Constants ---
# Device Identification (Match your device)
EXPECTED_VID = 0x1067
EXPECTED_PID = 0x626D
PRODUCT_NAME_SUBSTRING = "Module CDC Interface" # Important for matching

# Serial Communication Parameters
BAUD_RATE = 115200
# Increase timeout significantly for receiving potentially large files
# Read timeout applies between reads (e.g., between filename and size, or between data chunks)
# Set it long enough to allow the device to prepare the next file, but short enough to detect a total stall.
READ_TIMEOUT_INTER_FILE = 5 # Seconds (timeout between files/parts)
READ_TIMEOUT_DATA = 2      # Seconds (timeout during data stream of a single file)
# Note: pyserial doesn't have a separate 'inter_byte_timeout'. The 'timeout' parameter
# behaves differently based on value:
#   None: Block forever
#   0: Non-blocking
#   >0: Timeout in seconds (float). If read(N) doesn't get N bytes in time, it returns what it has.

# --- Serial Port Function (Adapted from test_cdc.py) ---
def find_cdc_port(vid: int, pid: int, product_substring: Optional[str] = None) -> Optional[str]:
    """Return the device name for the first CDC port matching VID/PID and optionally product string."""
    print(f"Searching for CDC serial port with VID={vid:#06x}, PID={pid:#06x}...")
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return None

    matching_ports = []
    print("Available Serial Ports:")
    for p in ports:
        print(f"  {p.device}: VID={p.vid} PID={p.pid} (Desc: {p.description}, HWID: {p.hwid})")
        # Check explicit VID/PID first
        if p.vid == vid and p.pid == pid:
             # Further check if it's likely the CDC interface (often excludes 'debug' or 'console' if present)
             # This check might need adjustment based on your specific device's interfaces
             if "CDC" in p.description or "Serial" in p.description or (product_substring and product_substring in p.product):
                print(f"    -> Matched VID/PID and likely CDC.")
                matching_ports.append(p.device)
                continue # Prioritize direct match that looks like CDC

        # Fallback check in HWID string (more robust for some OS/drivers)
        vid_pid_str1 = f"VID:PID={vid:04X}:{pid:04X}"
        vid_pid_str2 = f"VID_{vid:04X}&PID_{pid:04X}"
        hwid_match = p.hwid and (vid_pid_str1 in p.hwid or vid_pid_str2 in p.hwid)
        product_match = product_substring and p.product and product_substring in p.product

        if hwid_match or product_match:
            print(f"    -> Matched VID/PID/Product in HWID/Desc string.")
            if p.device not in matching_ports: # Avoid duplicates
                 matching_ports.append(p.device)

    if not matching_ports:
        print("-> No matching CDC port found.")
        return None
    elif len(matching_ports) > 1:
         print(f"-> Warning: Found multiple matching ports: {matching_ports}. Using the first one: {matching_ports[0]}")

    print(f"-> Using CDC port: {matching_ports[0]}")
    return matching_ports[0]

# --- Receive Logic ---
def receive_files_via_cdc(port: str, output_dir: str):
    """Receives files (filename, size, data) over CDC and saves them."""
    print(f"\n--- Starting CDC Receiver on {port} ---")
    print(f"Saving files to: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    files_received_count = 0
    total_bytes_received = 0

    try:
        # Use a longer timeout initially when waiting for the first filename
        with serial.Serial(port, BAUD_RATE, timeout=READ_TIMEOUT_INTER_FILE) as ser:
            print(f"Serial port {port} opened. Waiting for first filename...")

            while True:
                # 1. Receive Filename (null-terminated)
                filename_bytes = bytearray()
                while True:
                    # Read one byte at a time to detect null terminator
                    byte = ser.read(1)
                    if not byte:
                        # Timeout occurred waiting for filename byte
                        print("\nTimeout waiting for filename byte. Assuming transfer complete or stalled.")
                        # If we haven't received any files yet, it's likely an error.
                        if files_received_count == 0:
                             print("ERROR: No files received before timeout.")
                             raise TimeoutError("Timeout waiting for the first filename.")
                        else:
                             # If we received files, timeout might mean completion.
                             print("Treating timeout as end-of-transfer signal.")
                             break # Exit filename loop, will lead to outer loop break
                    if byte == b'\0':
                        break # End of filename
                    filename_bytes.extend(byte)

                if not filename_bytes:
                    # Received null byte immediately - this is our termination signal
                    print("\nReceived termination signal (empty filename).")
                    break # Exit the main receiving loop

                filename = filename_bytes.decode('utf-8', errors='ignore')
                print(f"\nReceived Filename: '{filename}'")

                # 2. Receive Size (4 bytes, Little Endian)
                ser.timeout = READ_TIMEOUT_INTER_FILE # Use inter-file timeout for reading size
                size_bytes = ser.read(4)
                if len(size_bytes) < 4:
                    print(f"\nERROR: Timeout or short read receiving size for '{filename}'. Expected 4 bytes, got {len(size_bytes)}.")
                    # Decide how to handle: Abort? Skip file?
                    print("Aborting transfer.")
                    break # Exit the main loop

                expected_size = struct.unpack('<I', size_bytes)[0]
                print(f"Expecting Size: {expected_size} bytes")

                # 3. Receive Data
                output_path = os.path.join(output_dir, filename)
                received_bytes = 0
                ser.timeout = READ_TIMEOUT_DATA # Switch to data timeout for content
                try:
                    with open(output_path, 'wb') as f:
                        start_time = time.time()
                        while received_bytes < expected_size:
                            # Read in chunks for efficiency
                            bytes_to_read = min(4096, expected_size - received_bytes)
                            chunk = ser.read(bytes_to_read)
                            if not chunk:
                                # Timeout occurred during data transfer
                                print(f"\nERROR: Timeout receiving data for '{filename}' at {received_bytes}/{expected_size} bytes.")
                                raise TimeoutError(f"Timeout receiving data for {filename}")

                            f.write(chunk)
                            received_bytes += len(chunk)

                            # Optional: Progress within a large file
                            # print(f"... {received_bytes}/{expected_size} bytes", end='\r')

                        end_time = time.time()
                        duration = end_time - start_time
                        rate = (received_bytes / duration / 1024) if duration > 1e-6 else float('inf')
                        print(f"-> Saved '{output_path}' ({received_bytes} bytes) in {duration:.2f}s [{rate:.1f} KB/s]")
                        files_received_count += 1
                        total_bytes_received += received_bytes

                except TimeoutError:
                    # Clean up potentially incomplete file?
                    print(f"Attempting to remove incomplete file: {output_path}")
                    try:
                        os.remove(output_path)
                    except OSError as e:
                        print(f"Warning: Could not remove incomplete file: {e}")
                    break # Abort transfer after timeout during data receive
                except IOError as e:
                    print(f"\nERROR: Could not write to file '{output_path}': {e}")
                    break # Abort transfer on file write error
                finally:
                    # Reset timeout for next filename/termination signal
                     ser.timeout = READ_TIMEOUT_INTER_FILE

    except serial.SerialException as e:
        print(f"\nERROR: Serial communication error on port {port}: {e}")
        print("       Check device connection, ensure it's not in use elsewhere (QMK Toolbox, screen).")
        return False # Indicate failure
    except TimeoutError as e:
        print(f"\nERROR: Timeout occurred: {e}")
        return False # Indicate failure
    except Exception as e:
        print(f"\nERROR: An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
        return False # Indicate failure

    print(f"\n--- CDC Receiver Finished ---")
    print(f"Total files received: {files_received_count}")
    print(f"Total bytes received: {total_bytes_received}")
    return True # Indicate success

# --- Main Execution (when run as a script) ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Listen on CDC serial for file transfers from QMK device.")
    parser.add_argument("output_dir", help="Directory to save received files.")
    parser.add_argument("--port", help="Specify the serial port manually (e.g., /dev/ttyACM0 or COM3).")
    parser.add_argument("--vid", type=lambda x: int(x, 0), default=EXPECTED_VID, help="Device Vendor ID (hex or dec).")
    parser.add_argument("--pid", type=lambda x: int(x, 0), default=EXPECTED_PID, help="Device Product ID (hex or dec).")

    args = parser.parse_args()

    if args.port:
        cdc_port = args.port
    else:
        cdc_port = find_cdc_port(args.vid, args.pid, PRODUCT_NAME_SUBSTRING)

    if not cdc_port:
        print("Could not find CDC port. Exiting.")
        sys.exit(1)

    if not os.path.isdir(args.output_dir):
         try:
             print(f"Output directory '{args.output_dir}' does not exist. Creating it...")
             os.makedirs(args.output_dir)
         except OSError as e:
             print(f"Error: Could not create output directory '{args.output_dir}': {e}")
             sys.exit(1)

    success = receive_files_via_cdc(cdc_port, args.output_dir)

    sys.exit(0 if success else 1)
