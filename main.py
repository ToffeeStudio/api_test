import time
import hid
import struct
import argparse
import sys
import os
from enum import IntEnum
from typing import List, Tuple, Optional
import serial
import serial.tools.list_ports
import struct
import time
from find_cdc import find_cdc_port
import shutil
# random

def convert_raw_to_png(raw_filepath: str, png_filepath: str, width: int = 128, height: int = 128) -> bool:
    print(f"Attempting to convert '{os.path.basename(raw_filepath)}' to PNG...")
    expected_bytes = width * height * 2 # 2 bytes per pixel for RGB565
    try:
        # Read the raw file data
        with open(raw_filepath, 'rb') as f_raw:
            raw_data = f_raw.read()

        # Validate size
        if len(raw_data) != expected_bytes:
            print(f"  Error: File size mismatch for '{os.path.basename(raw_filepath)}'. Expected {expected_bytes}, got {len(raw_data)}.")
            return False

        pixels_rgb888 = []
        # Iterate 2 bytes at a time, unpack as Big-Endian unsigned short (>H)
        for i in range(0, len(raw_data), 2):
            # Unpack Big-Endian short
            rgb565 = struct.unpack('>H', raw_data[i:i+2])[0]

            # Convert RGB565 to RGB888 components
            r5 = (rgb565 >> 11) & 0x1F
            g6 = (rgb565 >> 5)  & 0x3F
            b5 = rgb565         & 0x1F

            # Scale components to 8-bit
            r8 = (r5 << 3) | (r5 >> 2)
            g8 = (g6 << 2) | (g6 >> 4)
            b8 = (b5 << 3) | (b5 >> 2)

            pixels_rgb888.append((r8, g8, b8))

        # Create and save PNG image using Pillow
        img = Image.new('RGB', (width, height))
        img.putdata(pixels_rgb888)
        img.save(png_filepath, "PNG")
        print(f"  Successfully converted and saved to '{os.path.basename(png_filepath)}'")
        return True

    except FileNotFoundError:
        print(f"  Error: Raw file not found: '{raw_filepath}'")
        return False
    except Exception as e:
        print(f"  Error during RAW to PNG conversion: {e}")
        # Optionally re-raise if you want the main script to halt on conversion error
        # raise e
        return False

try:
    from PIL import Image
except ImportError:
    print("Please install the Pillow library to use image processing functionality (pip install Pillow).")
    sys.exit(1)

class CommandID(IntEnum):
    MODULE_CMD_LS = 0x50
    MODULE_CMD_CD = 0x51
    MODULE_CMD_PWD = 0x52
    MODULE_CMD_RM = 0x53
    MODULE_CMD_MKDIR = 0x54
    MODULE_CMD_TOUCH = 0x55
    MODULE_CMD_CAT = 0x56
    MODULE_CMD_OPEN = 0x57
    MODULE_CMD_WRITE = 0x58
    MODULE_CMD_CLOSE = 0x59
    MODULE_CMD_FORMAT_FILESYSTEM = 0x5A
    MODULE_CMD_FLASH_REMAINING = 0x5B
    MODULE_CMD_CHOOSE_IMAGE = 0x5C
    MODULE_CMD_WRITE_DISPLAY = 0x5D
    MODULE_CMD_SET_TIME = 0x5E
    MODULE_CMD_LS_NEXT = 0x60
    MODULE_CMD_LS_ALL = 0x61
    MODULE_CMD_WPM_SET_ANIM = 0x77
    MODULE_CMD_WPM_SET_CONFIG = 0x78

class ReturnCode(IntEnum):
    SUCCESS = 0x00
    IMAGE_ALREADY_EXISTS = 0xE1
    IMAGE_FLASH_FULL = 0xE2
    IMAGE_W_OOB = 0xE3
    IMAGE_H_OOB = 0xE4
    IMAGE_NAME_IN_USE = 0xE5
    IMAGE_NOT_FOUND = 0xE6
    IMAGE_NOT_OPEN = 0xE7
    IMAGE_PACKET_ID_ERR = 0xE8
    FLASH_REMAINING = 0xE9
    INVALID_COMMAND = 0xEF
    MORE_ENTRIES = 0xEA

PACKET_SIZE = 32  # Adjust this to match RAW_EPSIZE on your device
HEADER_SIZE = 6   # Magic number (1 byte) + Command ID (1 byte) + Packet ID (4 bytes)
DATA_SIZE = PACKET_SIZE - HEADER_SIZE


class HIDDevice:
    def __init__(self, vid: int, pid: int, usage_page: int, usage: int):
        self.vid = vid
        self.pid = pid
        self.usage_page = usage_page
        self.usage = usage
        self.device = None
        self.packet_id = 0

    def __enter__(self):
        # Enumerate devices by VID/PID
        all_devices = hid.enumerate(self.vid, self.pid)

        # Print them for debugging
        for dev in all_devices:
            print(dev)

        # Filter by usage_page and usage
        matching_devices = [
            d for d in all_devices
            if d.get('usage_page') == self.usage_page
            and d.get('usage') == self.usage
        ]

        if not matching_devices:
            print(f"No matching HID device found for "
                  f"VID=0x{self.vid:04X}, PID=0x{self.pid:04X}, "
                  f"UsagePage=0x{self.usage_page:04X}, Usage=0x{self.usage:02X}.")
            sys.exit(1)

        # Take the first matching device
        path = matching_devices[0].get('path')
        if not path:
            print("Selected device does not have a valid path.")
            sys.exit(1)

        print("PATH:", path)
        self.device = hid.Device(path=path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.device:
            self.device.close()

    def send_packet(self, command_id: CommandID, data: bytes = b'') -> None:
        packet_id_bytes = struct.pack('<I', self.packet_id)
        # Include the magic number 0x09 at the front of the packet
        header = struct.pack('<BBI', 0x09, command_id, self.packet_id)
        packet = header + data
        packet = packet.ljust(PACKET_SIZE, b'\x00')
        print(f"Sending packet of length {len(packet)}: {packet.hex()}")
        self.device.write(packet)
        time.sleep(0.001)
        self.packet_id += 1

    def receive_packet(self) -> Tuple[int, bytes]:
        response = self.device.read(PACKET_SIZE, 1500)  # Timeout in ms
        if not response:
            print("No response received.")
            return None, None
        print(f"Received response of length {len(response)}: {bytes(response).hex()}")
        time.sleep(0.001)
        status = response[0]
        data = bytes(response[1:])
        return status, data

    def execute_command(self, command_id: CommandID, data: bytes = b'') -> Tuple[ReturnCode, Optional[bytes]]:
        self.send_packet(command_id, data)
        status, response = self.receive_packet()
        if status is None:
            return ReturnCode.INVALID_COMMAND, None
        # Return the actual status code we received
        return ReturnCode(status), response

class FileSystem:
    def __init__(self, hid_device: HIDDevice):
        self.hid = hid_device

    def ls(self) -> List[str]:
        all_entries = []
        
        # Request first page
        ret_code, response = self.hid.execute_command(CommandID.MODULE_CMD_LS)
        
        if ret_code not in [ReturnCode.SUCCESS, ReturnCode.MORE_ENTRIES]:
            return []
        
        # Parse entries from the first page
        if response:
            entries = response.decode('utf-8', errors='ignore').strip('\x00').split('\x00')
            entries = [e for e in entries if e]  # Filter out empty entries
            all_entries.extend(entries)
        
        # Keep requesting more pages while the firmware indicates there are more entries
        while ret_code == ReturnCode.MORE_ENTRIES:
            ret_code, response = self.hid.execute_command(CommandID.MODULE_CMD_LS_NEXT)
            if ret_code not in [ReturnCode.SUCCESS, ReturnCode.MORE_ENTRIES]:
                break
            
            if response:
                entries = response.decode('utf-8', errors='ignore').strip('\x00').split('\x00')
                entries = [e for e in entries if e]
                all_entries.extend(entries)
        
        return all_entries

    def cd(self, directory: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_CD, directory.encode())
        return ret_code == ReturnCode.SUCCESS

    def pwd(self) -> str:
        ret_code, response = self.hid.execute_command(CommandID.MODULE_CMD_PWD)
        return response.decode('utf-8', errors='ignore').strip('\x00') if ret_code == ReturnCode.SUCCESS else ""

    def rm(self, path: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_RM, path.encode())
        return ret_code == ReturnCode.SUCCESS

    def mkdir(self, directory: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_MKDIR, directory.encode())
        return ret_code == ReturnCode.SUCCESS

    def touch(self, file_path: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_TOUCH, file_path.encode())
        return ret_code == ReturnCode.SUCCESS

    def cat(self, file_path: str) -> str:
        ret_code, response = self.hid.execute_command(CommandID.MODULE_CMD_CAT, file_path.encode())
        return response.decode('utf-8', errors='ignore').strip('\x00') if ret_code == ReturnCode.SUCCESS else ""

    def open(self, file_path: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_OPEN, file_path.encode())
        return ret_code == ReturnCode.SUCCESS

    def write(self, data: bytes) -> bool:
        # The data chunk should not exceed DATA_SIZE
        if len(data) > DATA_SIZE:
            data_chunks = [data[i:i + DATA_SIZE] for i in range(0, len(data), DATA_SIZE)]
        else:
            data_chunks = [data]

        for chunk in data_chunks:
            ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_WRITE, chunk)
            if ret_code != ReturnCode.SUCCESS:
                return False
        return True

    def close(self) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_CLOSE)
        return ret_code == ReturnCode.SUCCESS

    def format_filesystem(self) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_FORMAT_FILESYSTEM)
        return ret_code == ReturnCode.SUCCESS

    def flash_remaining(self) -> int:
        ret_code, response = self.hid.execute_command(CommandID.MODULE_CMD_FLASH_REMAINING)
        return struct.unpack('<I', response[:4])[0] if ret_code == ReturnCode.SUCCESS and response else 0

    def choose_image(self, image_path: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_CHOOSE_IMAGE, image_path.encode())
        return ret_code == ReturnCode.SUCCESS

    def write_display(self, data: bytes) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_WRITE_DISPLAY, data)
        return ret_code == ReturnCode.SUCCESS

    def set_time(self, hour: int, minute: int, second: int) -> bool:
        data = struct.pack('<BBB', hour, minute, second)
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_SET_TIME, data)
        return ret_code == ReturnCode.SUCCESS

    def write_display_image(self, image_data: bytes) -> bool:
        total_bytes = len(image_data)
        bytes_written = 0
        packet_size = DATA_SIZE

        while bytes_written < total_bytes:
            chunk = image_data[bytes_written:bytes_written + packet_size]
            ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_WRITE_DISPLAY, chunk)
            if ret_code != ReturnCode.SUCCESS:
                print(f"Failed to write display data at offset {bytes_written}")
                return False
            bytes_written += packet_size

        return True
    
    def set_wpm_anim(self, filename: str) -> bool:
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_WPM_SET_ANIM, filename.encode())
        return ret_code == ReturnCode.SUCCESS

    def set_wpm_config(self, min_wpm: int, max_wpm: int) -> bool:
        data = struct.pack('<BB', min_wpm, max_wpm)
        ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_WPM_SET_CONFIG, data)
        return ret_code == ReturnCode.SUCCESS

    def ls_all(self, output_dir: str) -> List[str]:
        """
        Triggers firmware dump via HID, then receives all .raw/.araw files
        over CDC using the filename/size/data protocol and saves them.

        Args:
            output_dir: The directory to save the received files.

        Returns:
            A list of file paths that were successfully received and saved.
        """
        saved_files = [] # Keep track of successfully saved files

        # 1. Send the HID command to trigger the CDC dump
        command_id = CommandID.MODULE_CMD_LS_ALL # 0x61
        print(f"[ls_all] Sending command 0x{command_id:02X} via HID...")
        try:
            self.hid.send_packet(command_id)
            print("[ls_all] HID command sent.")
            # Keep a delay allowing firmware/OS to potentially set up CDC
            # The firmware also has a 1000ms delay now. 0.5s here might still be good.
            time.sleep(0.5)
        except Exception as e:
            print(f"[ls_all] Error sending HID command: {e}")
            return saved_files # Return empty list on HID error

        # 2. Find the CDC serial port
        print("[ls_all] Searching for CDC port...")
        port = find_cdc_port(vid=self.hid.vid, pid=self.hid.pid)
        if not port:
            print("[ls_all] Error: CDC serial port not found.")
            print("[ls_all] Please ensure the device is connected and the firmware has initialized the CDC interface.")
            return saved_files # Return empty list if port not found

        print(f"[ls_all] Found CDC port: {port}")

        print(f"[ls_all] Preparing output directory: '{output_dir}'")
        try:
            if os.path.exists(output_dir):
                if os.path.isdir(output_dir):
                    print(f"[ls_all] Clearing existing directory: '{output_dir}'...")
                    shutil.rmtree(output_dir) # Remove the directory and all its contents
                    print(f"[ls_all] Directory cleared.")
                else:
                    # It exists but is a file - remove it
                    print(f"[ls_all] Output path '{output_dir}' exists but is a file. Removing it...")
                    os.remove(output_dir)
                    print(f"[ls_all] File removed.")
            # Recreate the directory after ensuring it's gone (or never existed)
            os.makedirs(output_dir, exist_ok=True)
            print(f"[ls_all] Ensured output directory exists.")
        except OSError as e:
            print(f"\n[ls_all] ERROR: Could not clear or create output directory '{output_dir}': {e}")
            print("[ls_all] Aborting file transfer.")
            return saved_files # Return empty list if directory prep fails

        # --- 3. Receive Files via CDC (Adapted from receive_cdc.py) ---
        print(f"[ls_all] Opening {port} to receive files...")
        print(f"[ls_all] Saving files to: {output_dir}")
        os.makedirs(output_dir, exist_ok=True) # Ensure output directory exists

        files_received_count = 0
        total_bytes_received = 0
        ser = None # Initialize serial object outside try

        # Define timeouts (can be adjusted)
        # Timeout between filename/size/termination signal
        READ_TIMEOUT_INTER_FILE = 5 # Seconds
        # Timeout while receiving data chunks for a single file
        READ_TIMEOUT_DATA = 2      # Seconds

        try:
            # Use a longer timeout initially when waiting for the first filename
            with serial.Serial(port, 115200, timeout=READ_TIMEOUT_INTER_FILE) as ser:
                print(f"[ls_all] Serial port {port} opened. Waiting for first filename...")

                while True:
                    # 3a. Receive Filename (null-terminated)
                    filename_bytes = bytearray()
                    while True:
                        # Read one byte at a time to detect null terminator
                        byte = ser.read(1)
                        if not byte: # Timeout occurred waiting for filename byte
                            print("\n[ls_all] Timeout waiting for filename byte. Assuming transfer complete or stalled.")
                            if files_received_count == 0:
                                print("[ls_all] WARNING: No files received before timeout.")
                                # Don't raise error, just break loop and finish
                            else:
                                print("[ls_all] Treating timeout as end-of-transfer signal.")
                            filename_bytes = b'' # Ensure filename is empty to break outer loop
                            break # Exit filename loop
                        if byte == b'\0':
                            break # End of filename
                        filename_bytes.extend(byte)

                    if not filename_bytes: # Received null byte or timed out after receiving files
                        if files_received_count > 0 and not byte: # Check if it was timeout after success
                             print("[ls_all] Timeout likely indicates completion.")
                        else: # Received explicit null byte
                             print("\n[ls_all] Received termination signal (empty filename).")
                        break # Exit the main receiving loop

                    filename = filename_bytes.decode('utf-8', errors='ignore')
                    print(f"\n[ls_all] Received Filename: '{filename}'")

                    # 3b. Receive Size (4 bytes, Little Endian)
                    ser.timeout = READ_TIMEOUT_INTER_FILE # Reset timeout for reading size
                    size_bytes = ser.read(4)
                    if len(size_bytes) < 4:
                        print(f"\n[ls_all] ERROR: Timeout or short read receiving size for '{filename}'. Expected 4 bytes, got {len(size_bytes)}.")
                        print("[ls_all] Aborting transfer.")
                        break # Exit the main loop

                    expected_size = struct.unpack('<I', size_bytes)[0]
                    print(f"[ls_all] Expecting Size: {expected_size} bytes")

                    # 3c. Receive Data and Save File
                    output_path = os.path.join(output_dir, filename)
                    received_bytes = 0
                    ser.timeout = READ_TIMEOUT_DATA # Switch to data timeout for content
                    file_saved_successfully = False
                    try:
                        with open(output_path, 'wb') as f:
                            start_time = time.time()
                            while received_bytes < expected_size:
                                # Read in chunks for efficiency
                                bytes_to_read = min(4096, expected_size - received_bytes)
                                chunk = ser.read(bytes_to_read)
                                if not chunk: # Timeout occurred during data transfer
                                    print(f"\n[ls_all] ERROR: Timeout receiving data for '{filename}' at {received_bytes}/{expected_size} bytes.")
                                    raise TimeoutError(f"Timeout receiving data for {filename}")

                                f.write(chunk)
                                received_bytes += len(chunk)

                                # Simple progress indicator within a file
                                progress_percent = int((received_bytes / expected_size) * 100) if expected_size > 0 else 100
                                print(f"... {progress_percent}% ({received_bytes}/{expected_size} bytes)", end='\r')

                            # Ensure newline after progress indicator finishes
                            print()
                            end_time = time.time()
                            duration = end_time - start_time
                            rate = (received_bytes / duration / 1024) if duration > 1e-6 else float('inf')
                            print(f"-> Saved '{output_path}' ({received_bytes} bytes) in {duration:.2f}s [{rate:.1f} KB/s]")
                            saved_files.append(output_path) # Add to list of successful saves
                            files_received_count += 1
                            total_bytes_received += received_bytes
                            file_saved_successfully = True

                        # Check if the successfully saved file is a .raw file
                        if file_saved_successfully and output_path.lower().endswith(".raw"):
                            png_filename = os.path.splitext(output_path)[0] + ".png"
                            # Call the helper function (defined earlier in the file)
                            convert_raw_to_png(output_path, png_filename)
                        elif file_saved_successfully:
                             print(f"Skipping PNG conversion for non-.raw file: {os.path.basename(output_path)}")

                    except TimeoutError:
                        # Clean up potentially incomplete file
                        print(f"[ls_all] Attempting to remove incomplete file: {output_path}")
                        try:
                            os.remove(output_path)
                            print(f"[ls_all] Removed incomplete file: {output_path}")
                        except OSError as e:
                            print(f"[ls_all] Warning: Could not remove incomplete file '{output_path}': {e}")
                        break # Abort transfer after timeout during data receive
                    except IOError as e:
                        print(f"\n[ls_all] ERROR: Could not write to file '{output_path}': {e}")
                        break # Abort transfer on file write error
                    finally:
                        # Reset timeout for next filename/termination signal
                         ser.timeout = READ_TIMEOUT_INTER_FILE

        except serial.SerialException as e:
            print(f"\n[ls_all] ERROR: Serial communication error on port {port}: {e}")
            print("[ls_all]       Check device connection, ensure it's not in use elsewhere (QMK Toolbox, screen).")
        except TimeoutError as e: # Should be caught internally now, but keep for safety
            print(f"\n[ls_all] ERROR: Timeout occurred: {e}")
        except Exception as e:
            print(f"\n[ls_all] ERROR: An unexpected error occurred during CDC receive: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # No need to close ser explicitly if using 'with' statement
            print(f"\n[ls_all] CDC Receiver process finished.")
            print(f"[ls_all] Total files successfully received: {files_received_count}")
            print(f"[ls_all] Total bytes received: {total_bytes_received}")

        return saved_files # Return the list of successfully saved file paths


def rgb565_to_rgb(rgb565):
    r5 = (rgb565 >> 11) & 0x1F
    g6 = (rgb565 >> 5) & 0x3F
    b5 = rgb565 & 0x1F
    r8 = (r5 << 3) | (r5 >> 2)
    g8 = (g6 << 2) | (g6 >> 4)
    b8 = (b5 << 3) | (b5 >> 2)
    return (r8, g8, b8)

def color_distance(c1, c2):
    return ((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2 + (c1[2]-c2[2])**2)

def image_to_rgb565(image: Image.Image, background_color: Tuple[int, int, int] = (0, 0, 0)):
    # Handle images with transparency by pasting onto a background
    if image.mode == 'RGBA':
        background = Image.new('RGBA', image.size, background_color)
        image = Image.alpha_composite(background, image.convert('RGBA'))
        image = image.convert('RGB')

    image_data = bytearray()
    pixels = list(image.getdata())
    processed_pixels = []

    for r, g, b in pixels:
        r5 = r >> 3
        g6 = g >> 2
        b5 = b >> 3
        rgb565 = (r5 << 11) | (g6 << 5) | b5
        # Use big-endian packing for RGB565
        image_data.extend(struct.pack('>H', rgb565))
        processed_pixels.append((r, g, b))

    processed_image = Image.new('RGB', image.size)
    processed_image.putdata(processed_pixels)
    return bytes(image_data), processed_image

def image_to_rgb565_quantized(image: Image.Image, background_color: Tuple[int, int, int] = (0, 0, 0)):
    # Handle images with transparency by pasting onto a background
    if image.mode == 'RGBA':
        background = Image.new('RGBA', image.size, background_color)
        image = Image.alpha_composite(background, image.convert('RGBA'))
        image = image.convert('RGB')

    image_data = bytearray()
    pixels = list(image.getdata())
    colors_rgb565 = [
        0xE007,  # Green
        0x00F8,  # Blue
        0x1F00,  # Red
    ]
    colors_rgb = [rgb565_to_rgb(c) for c in colors_rgb565]
    processed_pixels = []

    for r, g, b in pixels:
        min_dist = None
        closest_color = None
        for i, color in enumerate(colors_rgb):
            dist = color_distance((r, g, b), color)
            if (min_dist is None) or (dist < min_dist):
                min_dist = dist
                closest_color = colors_rgb565[i]
        # Use big-endian packing for RGB565
        image_data.extend(struct.pack('>H', closest_color))
        processed_pixels.append(rgb565_to_rgb(closest_color))

    processed_image = Image.new('RGB', image.size)
    processed_image.putdata(processed_pixels)
    return bytes(image_data), processed_image

def create_colored_bars_image(width: int, height: int) -> bytes:
    image_data = bytearray()
    bar_width = width // 8
    colors = [
        0xE007,  # Green
        0x00F8,  # Blue
        0x1F00,  # Red
    ]
    for y in range(height):
        for x in range(width):
            color_index = (x // bar_width) % len(colors)
            # Use big-endian packing for RGB565
            image_data.extend(struct.pack('>H', colors[color_index]))
    return bytes(image_data)

def create_animated_bars(width: int, height: int, num_frames: int) -> bytes:
    image_data = bytearray()
    colors = [
        0xE007,  # Green
        0x00F8,  # Blue
        0x1F00,  # Red
    ]
    for frame in range(num_frames):
        offset = frame * 2
        for y in range(height):
            for x in range(width):
                color_index = ((x + offset) // 16) % len(colors)
                # Use big-endian packing for RGB565
                image_data.extend(struct.pack('>H', colors[color_index]))
    return bytes(image_data)

def write_image_to_file(fs: FileSystem, image_data: bytes) -> bool:
    total_bytes = len(image_data)
    bytes_written = 0
    packet_size = DATA_SIZE

    while bytes_written < total_bytes:
        chunk = image_data[bytes_written:bytes_written + packet_size]
        if not fs.write(chunk):
            print(f"Failed to write chunk at offset {bytes_written}")
            return False
        bytes_written += packet_size

    return True

def find_qmk_device(vid, pid, usage_page, usage):
    # If you no longer need this function, you can leave it here or remove it.
    # It's not strictly used by the HIDDevice code above.
    for d in hid.enumerate():
        if d['vendor_id'] == vid and d['product_id'] == pid:
            if 'usage_page' in d and 'usage' in d:
                if d['usage_page'] == usage_page and d['usage'] == usage:
                    print("Found QMK console device:", d)
                    return d
    return None


def main():
    VID = 0x1067  # (4199) Vendor ID for your device
    PID = 0x626D  # (25197) Product ID for your device
    USAGE_PAGE = 0xFF60  # (65376)
    USAGE = 0x61  # (97)

    parser = argparse.ArgumentParser(description="HID File System Command Line Utility")
    parser.add_argument("--ls", action="store_true", help="List directory contents")
    parser.add_argument("--cd", help="Change directory")
    parser.add_argument("--pwd", action="store_true", help="Print working directory")
    parser.add_argument("--rm", help="Remove file or directory")
    parser.add_argument("--mkdir", help="Create a new directory")
    parser.add_argument("--touch", help="Create a new file")
    parser.add_argument("--cat", help="Display file contents")
    parser.add_argument("--open", help="Open a file")
    parser.add_argument("--write", help="Write to the currently open file")
    parser.add_argument("--close", action="store_true", help="Close the currently open file")
    parser.add_argument("--format", action="store_true", help="Format the filesystem")
    parser.add_argument("--flash-remaining", action="store_true", help="Check remaining flash memory")
    parser.add_argument("--choose-image", help="Choose an image")
    parser.add_argument("--write-display", help="Write data to display")
    parser.add_argument("--set-time", nargs=3, type=int, metavar=('HOUR', 'MINUTE', 'SECOND'), help="Set the time")
    parser.add_argument("--write-test-image", action="store_true", help="Write a 128x128 colored bars test image to the open file")
    parser.add_argument("--write-test-anim", action="store_true", help="Write a 128x128 animated test pattern")
    parser.add_argument("--write-image-immediate", help="Write an image directly to the display")
    parser.add_argument("--write-image-file", help="Write an image to a file on the device")
    parser.add_argument("--ls_all", action="store_true", help="Retrieve all .raw/.araw files via CDC using the default or specified output directory.")
    parser.add_argument("--output-dir",metavar='DIR', default="dumped_files", help="Directory to save files for --ls_all (default: dumped_files)")
    parser.add_argument("--quantize", action="store_true", help="Quantize image colors to specific colors")
    parser.add_argument("--background-color", type=str, default="0,0,0", help="Background color for transparency (format: R,G,B)")
    parser.add_argument("--wpm-gif", help="Set the .araw file to use for the WPM indicator.")
    parser.add_argument("--wpm-range", nargs=2, type=int, metavar=('MIN', 'MAX'), help="Set the min and max WPM range (e.g., 20 150).")

    args = parser.parse_args()
    background_color = tuple(map(int, args.background_color.split(',')))

    with HIDDevice(VID, PID, USAGE_PAGE, USAGE) as hid_device:
        fs = FileSystem(hid_device)

        if args.ls:
            print("Directory contents:", fs.ls())
        elif args.cd:
            print("Changed directory:", fs.cd(args.cd))
        elif args.pwd:
            print("Current directory:", fs.pwd())
        elif args.rm:
            print("Removed:", fs.rm(args.rm))
        elif args.mkdir:
            print("Created directory:", fs.mkdir(args.mkdir))
        elif args.touch:
            print("Created file:", fs.touch(args.touch))
        elif args.cat:
            print("File contents:", fs.cat(args.cat))
        elif args.open:
            print("Opened file:", fs.open(args.open))
        elif args.write:
            print("Wrote to file:", fs.write(args.write.encode()))
        elif args.close:
            print("Closed file:", fs.close())
        elif args.format:
            print("Formatted filesystem:", fs.format_filesystem())
        elif args.flash_remaining:
            print("Flash remaining:", fs.flash_remaining())
        elif args.choose_image:
            print("Chose image:", fs.choose_image(args.choose_image))
        elif args.write_display:
            print("Wrote to display:", fs.write_display(args.write_display.encode()))
        elif args.set_time:
            hour, minute, second = args.set_time
            print("Set time:", fs.set_time(hour, minute, second))
        elif args.write_test_image:
            image_data = create_colored_bars_image(128, 128)
            if fs.open("test_image.raw"):
                time.sleep(1)
                success = write_image_to_file(fs, image_data)
                fs.close()
                print(f"Wrote test image: {'Success' if success else 'Failed'}")
            else:
                print("Failed to open file for writing test image")

        elif args.write_test_anim:
            image_data = create_animated_bars(128, 128, 12)  # e.g. 12 frames
            if fs.open("test_anim.araw"):
                time.sleep(1)
                success = write_image_to_file(fs, image_data)
                fs.close()
                print(f"Wrote test animation: {'Success' if success else 'Failed'}")
            else:
                print("Failed to open file for writing test animation")
        elif args.write_image_immediate:
            image = Image.open(args.write_image_immediate)
            image = image.resize((128, 128), Image.LANCZOS)
            image = image.convert('RGBA')  # Ensure the image has an alpha channel
            if args.quantize:
                image_data, processed_image = image_to_rgb565_quantized(image, background_color=background_color)
            else:
                image_data, processed_image = image_to_rgb565(image, background_color=background_color)
            processed_image.show()
            success = fs.write_display_image(image_data)
            print(f"Wrote image directly to display: {'Success' if success else 'Failed'}")
        elif args.write_image_file:
            image = Image.open(args.write_image_file)
            image = image.resize((128, 128), Image.LANCZOS)
            image = image.convert('RGBA')
            if args.quantize:
                image_data, processed_image = image_to_rgb565_quantized(image, background_color=background_color)
            else:
                image_data, processed_image = image_to_rgb565(image, background_color=background_color)
            processed_image.show()
            output_filename = os.path.splitext(os.path.basename(args.write_image_file))[0] + ".raw"
            if fs.open(output_filename):
                time.sleep(1)
                success = write_image_to_file(fs, image_data)
                fs.close()
                print(f"Wrote image to {output_filename}: {'Success' if success else 'Failed'}")
            else:
                print("Failed to open file for writing image")

        elif args.wpm_gif:
            print(f"Setting WPM indicator animation to: {args.wpm_gif}")
            success = fs.set_wpm_anim(args.wpm_gif)
            print(f"Set WPM animation: {'Success' if success else 'Failed'}")

        elif args.wpm_range:
            try:
                min_wpm, max_wpm = args.wpm_range
                if not (0 <= min_wpm <= 255 and 0 <= max_wpm <= 255 and min_wpm < max_wpm):
                    raise ValueError("WPM values must be between 0-255, and min must be less than max.")
                
                print(f"Setting WPM range to {min_wpm}-{max_wpm}")
                success = fs.set_wpm_config(min_wpm, max_wpm)
                print(f"Set WPM range: {'Success' if success else 'Failed'}")

            except ValueError as e:
                print(f"Error: Invalid WPM range. {e}", file=sys.stderr)
        elif args.ls_all:
            output_directory = args.output_dir # Get the directory from the argument value
            print(f"Attempting to retrieve all files to directory: '{output_directory}'")
            saved_file_list = fs.ls_all(output_directory)
            if saved_file_list:
                print("\nSuccessfully saved files:")
                for f_path in saved_file_list:
                    print(f"- {f_path}")
            else:
                 print("\nNo files were saved successfully.")
        else:
            parser.print_help()

if __name__ == "__main__":
    main()

