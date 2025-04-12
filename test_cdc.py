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
    from PIL import Image, ImageSequence
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
WRITE_TIMEOUT = 600        # Seconds (10 minutes - INCREASED TIMEOUT) <--- INCREASED
READ_TIMEOUT = 1           # Seconds for reading confirmations (optional)
PRE_SEND_DELAY = 0.2       # Seconds to wait after opening port before sending anything
POST_HEADER_DELAY = 0.1    # Seconds to wait after sending size header, before sending data
POST_FILENAME_DELAY = 0.1  # Seconds to wait after sending filename
SEND_CHUNK_SIZE = 4096     # Bytes to send per write call for progress reporting

# Image Processing Defaults
DEFAULT_RESIZE_W = 128
DEFAULT_RESIZE_H = 128
DEFAULT_BG_COLOR = (0, 0, 0) # Black

# --- Image Processing Functions ---
def process_image_frame(frame: Image.Image, target_size: tuple[int, int], background_color: tuple[int, int, int]) -> bytes:
    """
    Converts a single PIL Image frame to raw RGB565 bytes (big-endian).
    Handles resizing and transparency.
    """
    try:
        # Resize first
        frame_resized = frame.resize(target_size, Image.Resampling.LANCZOS)

        # Handle transparency by compositing onto the specified background color
        if frame_resized.mode in ('RGBA', 'LA') or (frame_resized.mode == 'P' and 'transparency' in frame_resized.info):
            # print(f"  Handling transparency for frame...") # Verbose
            try:
                alpha = frame_resized.convert('RGBA').split()[-1]
                bg = Image.new("RGB", frame_resized.size, background_color)
                img_rgb = frame_resized.convert("RGB") # Convert potential P mode after handling transparency logic
                bg.paste(img_rgb, mask=alpha)
                image_to_convert = bg
            except Exception as e:
                print(f"  Warning: Error handling transparency: {e}. Trying simple convert.")
                image_to_convert = frame_resized.convert('RGB')
        elif frame_resized.mode == 'P':
             # print(f"  Converting Palette frame to RGB...") # Verbose
             image_to_convert = frame_resized.convert('RGB')
        elif frame_resized.mode != 'RGB':
            # print(f"  Converting frame mode {frame_resized.mode} to RGB...") # Verbose
            image_to_convert = frame_resized.convert('RGB')
        else:
            image_to_convert = frame_resized # Already RGB

        # print("  Converting pixels to RGB565 (Big Endian)...") # Verbose
        frame_data = bytearray()
        pixels = list(image_to_convert.getdata())
        for r, g, b in pixels:
            r5 = r >> 3
            g6 = g >> 2
            b5 = b >> 3
            rgb565 = (r5 << 11) | (g6 << 5) | b5
            # Outputting Big Endian (>H) for pixel data
            frame_data.extend(struct.pack('>H', rgb565))
        return bytes(frame_data)

    except Exception as e:
        print(f"Error processing frame: {e}")
        return b''

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
        print(f"  {p.device}: VID={p.vid} PID={p.pid} (Desc: {p.description}, HWID: {p.hwid})")
        # Check explicit VID/PID first
        if p.vid == vid and p.pid == pid:
            print(f"    -> Matched VID/PID directly.")
            matching_ports.append(p.device)
            continue # Prioritize direct match
        # Fallback check in HWID string (more robust)
        vid_pid_str1 = f"VID:PID={vid:04X}:{pid:04X}"
        vid_pid_str2 = f"VID_{vid:04X}&PID_{pid:04X}"
        product_name = "Module CDC Interface" # Added check for product name
        if (p.hwid and (vid_pid_str1 in p.hwid or vid_pid_str2 in p.hwid)) or \
           (p.product and product_name in p.product):
            print(f"    -> Matched VID/PID/Product in HWID/Desc string.")
            if p.device not in matching_ports: # Avoid duplicates
                 matching_ports.append(p.device)

    if not matching_ports:
        print("-> No matching CDC port found.")
        return None
    elif len(matching_ports) > 1:
         print(f"-> Warning: Found multiple matching ports: {matching_ports}. Using the first one: {matching_ports[0]}")

    print(f"-> Using port: {matching_ports[0]}")
    return matching_ports[0]

# --- Send Data Function (with chunking and progress) ---
def send_file_over_cdc(serial_port: serial.Serial, filename_on_device: str, data: bytes):
    """Sends filename, size, and data over the serial port with progress."""
    print(f"\n--- Sending to Device ({serial_port.port}) ---")
    print(f"Target Filename: {filename_on_device}")
    total_data_size = len(data)
    print(f"Data Size: {total_data_size} bytes")

    # 1. Send Filename (UTF-8 encoded, null-terminated)
    filename_bytes = filename_on_device.encode('utf-8') + b'\0'
    print(f"Sending filename ({len(filename_bytes)} bytes): {filename_bytes.hex()}...")
    bytes_written_fn = serial_port.write(filename_bytes)
    serial_port.flush()
    if bytes_written_fn != len(filename_bytes):
        raise IOError(f"Failed to write full filename (wrote {bytes_written_fn}/{len(filename_bytes)})")
    print("-> Filename sent.")
    time.sleep(POST_FILENAME_DELAY)

    # 2. Send Size Header (4 bytes, Little Endian)
    size_header = struct.pack('<I', total_data_size) # Little Endian for size
    print(f"Sending size header ({len(size_header)} bytes): {size_header.hex()}...")
    bytes_written_h = serial_port.write(size_header)
    serial_port.flush()
    if bytes_written_h != 4:
         raise IOError(f"Failed to write full size header (wrote {bytes_written_h})")
    print("-> Size header sent.")
    time.sleep(POST_HEADER_DELAY)

    # 3. Send Actual Data in Chunks with Progress Reporting
    print(f"Sending data block ({total_data_size} bytes)...")
    start_time = time.time()
    bytes_sent = 0
    last_reported_progress = -1 # Initialize to ensure 0% or first report gets printed

    while bytes_sent < total_data_size:
        chunk = data[bytes_sent : bytes_sent + SEND_CHUNK_SIZE]
        bytes_to_send = len(chunk)
        bytes_written_chunk = serial_port.write(chunk)
        # It's often better to flush *after* writing, but experiment if issues arise
        serial_port.flush()

        # Check write result (optional, timeout is main check)
        if bytes_written_chunk != bytes_to_send:
            print(f"\nWarning: Chunk write mismatch! Target {bytes_to_send}, write() returned {bytes_written_chunk}.")
            # Decide how to handle: maybe retry, maybe just continue and rely on timeout

        bytes_sent += bytes_to_send # Update progress based on intended send size for the chunk

        # Calculate and report progress
        progress_percent = int((bytes_sent / total_data_size) * 100) if total_data_size > 0 else 100

        # Report every 10% milestone
        if progress_percent >= last_reported_progress + 10:
            # Avoid printing 100% here, print completion message later
            if progress_percent < 100:
                print(f"... {progress_percent}% sent ({bytes_sent}/{total_data_size} bytes)")
            last_reported_progress = (progress_percent // 10) * 10 # Set threshold to next multiple of 10

        # Add a tiny sleep if needed to allow device processing, but usually not necessary
        # time.sleep(0.001)

    end_time = time.time()
    print(f"... 100% sent ({bytes_sent}/{total_data_size} bytes)") # Final progress
    print("-> Data sending complete.")

    duration = end_time - start_time
    rate = (bytes_sent / duration / 1024) if duration > 1e-6 else float('inf')
    print(f"\nTransfer took {duration:.3f} seconds.")
    print(f"Average Rate: {rate:.2f} KB/s.")
    # Consider adding a small delay or waiting for an ACK from the device if implemented


# --- Main Execution Logic ---
def main():
    parser = argparse.ArgumentParser(description="Process an image/GIF and send its raw RGB565 data over CDC serial.")
    parser.add_argument("image_path", help="Path to the image or GIF file to process and send.")
    parser.add_argument("--port", help="Specify the serial port manually (e.g., /dev/ttyACM0 or COM3).")
    parser.add_argument("--width", type=int, default=DEFAULT_RESIZE_W, help=f"Target width (default: {DEFAULT_RESIZE_W}).")
    parser.add_argument("--height", type=int, default=DEFAULT_RESIZE_H, help=f"Target height (default: {DEFAULT_RESIZE_H}).")
    parser.add_argument("--bg", type=str, default=",".join(map(str, DEFAULT_BG_COLOR)),
                        help=f"Background color R,G,B for transparency (default: {DEFAULT_BG_COLOR}).")

    args = parser.parse_args()

    # --- 1. Validate and Process Image ---
    if not os.path.exists(args.image_path):
        print(f"Error: Image file not found: '{args.image_path}'")
        sys.exit(1)

    print(f"\n--- Processing File: {args.image_path} ---")
    target_size = (args.width, args.height)
    try:
        bg_color = tuple(map(int, args.bg.split(',')))
        if len(bg_color) != 3: raise ValueError("Background color needs 3 values (R,G,B)")
    except Exception as e:
        print(f"Error parsing background color '{args.bg}': {e}")
        sys.exit(1)

    output_data = bytearray()
    is_animated = False
    target_filename_on_device = ""

    try:
        img = Image.open(args.image_path)
        print(f"Opened file. Format: {img.format}, Mode: {img.mode}, Size: {img.size}")

        # Check if it's an animated format (like GIF)
        if getattr(img, "is_animated", False) or img.format == "GIF":
            is_animated = True
            print("Detected animated format (GIF). Processing frames...")
            frame_count = 0
            # Attempt to get total frames if possible (for better progress estimate during processing)
            total_frames = getattr(img, "n_frames", 0)
            if total_frames > 0:
                print(f"Found {total_frames} frames.")

            for i, frame in enumerate(ImageSequence.Iterator(img)):
                frame_count += 1
                # Print progress during frame processing if total is known
                if total_frames > 0:
                    print(f"Processing frame {frame_count}/{total_frames}...")
                else:
                    print(f"Processing frame {frame_count}...")

                processed_frame_data = process_image_frame(frame, target_size, bg_color)
                if not processed_frame_data:
                    raise ValueError(f"Failed to process frame {frame_count}")
                output_data.extend(processed_frame_data)
            if frame_count == 0:
                 print("Warning: Animated file reported, but no frames found/processed.")
            print(f"Processed {frame_count} frames.")
        else:
            print("Detected static image format. Processing single frame...")
            processed_frame_data = process_image_frame(img, target_size, bg_color)
            if not processed_frame_data:
                 raise ValueError("Failed to process static image")
            output_data = processed_frame_data
            print("Processed 1 frame.")

        # Determine filename extension
        base_name = os.path.splitext(os.path.basename(args.image_path))[0]
        # Sanitize basename: replace non-alphanumeric with underscore, limit length
        sanitized_base_name = "".join(c if c.isalnum() else '_' for c in base_name)
        sanitized_base_name = sanitized_base_name[:50] # Limit length

        output_ext = ".araw" if is_animated and len(output_data) > 0 else ".raw"
        target_filename_on_device = sanitized_base_name + output_ext

        if not output_data:
            print("Error: No data generated after processing.")
            sys.exit(1)
        print(f"-> Final processed data size: {len(output_data)} bytes.")
        print(f"-> Target filename on device: {target_filename_on_device}")


    except Exception as e:
        print(f"Error processing image/GIF: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # --- 2. Find CDC Port ---
    if args.port:
        port = args.port
        print(f"\n--- Using Manual Port ---")
        print(f"Port: {port}")
    else:
        print(f"\n--- Locating Device ---")
        port = find_port(EXPECTED_VID, EXPECTED_PID)
        if not port:
            print("Failed to find device port automatically. Try specifying with --port.")
            print("Ensure the QMK firmware with CDC enabled is flashed and the device is connected.")
            sys.exit(1)

    # --- 3. Send Data over CDC ---
    success = False
    try:
        # Note: Using a 'with' block ensures the port is closed even if errors occur
        with serial.Serial(port, BAUD_RATE, timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT) as ser:
            print(f"Serial port opened (Baud: {BAUD_RATE}, Write Timeout: {WRITE_TIMEOUT}s).")

            # --- Optional: Pre-Send Delay ---
            print(f"Waiting {PRE_SEND_DELAY:.2f}s before sending...")
            time.sleep(PRE_SEND_DELAY)

            # --- Send file using the new protocol ---
            send_file_over_cdc(ser, target_filename_on_device, bytes(output_data))

            # --- Optional: Wait for potential confirmation/response ---
            # print("Waiting briefly for any response from device (optional)...")
            # response = ser.read(100) # Read up to 100 bytes
            # if response:
            #     try:
            #         print(f"Received response: {response.decode('utf-8', errors='ignore')}")
            #     except:
            #         print(f"Received (binary) response: {response.hex()}")
            # else:
            #     print("No immediate response received.")

            print("\nSUCCESS: Data sending process completed.")
            success = True

    except serial.SerialTimeoutException:
        print(f"\nERROR: Serial write timeout after {WRITE_TIMEOUT}s on port {port}.")
        print("       Check device connection, firmware status (is it busy?), and if the timeout is sufficient.")
        print(f"       Consider increasing WRITE_TIMEOUT (currently {WRITE_TIMEOUT}s) or checking device console for errors.")
    except serial.SerialException as e:
        print(f"\nERROR: Serial communication error on port {port}: {e}")
        print("       Check if the port is correct, not in use by another program (like screen, QMK Toolbox console),")
        print("       or if the device was disconnected or reset.")
    except IOError as e:
        print(f"\nERROR: I/O error during send: {e}")
    except Exception as e:
        print(f"\nERROR: An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n--- Operation {'Completed Successfully' if success else 'Failed'} ---")
    sys.exit(0 if success else 1)

# --- Script Entry Point ---
if __name__ == "__main__":
    main()
