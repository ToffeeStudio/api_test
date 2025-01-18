#!/usr/bin/env python3

import time
import hid
import struct
import argparse
import sys
import os
from enum import IntEnum
from typing import List, Tuple, Optional

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

PACKET_SIZE = 32  # Adjust this to match RAW_EPSIZE on your device
HEADER_SIZE = 6   # Magic number (1 byte) + Command ID (1 byte) + Packet ID (4 bytes)
DATA_SIZE = PACKET_SIZE - HEADER_SIZE

class HIDDevice:
    def __init__(self, vid: int, pid: int):
        self.vid = vid
        self.pid = pid
        self.device = None
        self.packet_id = 0

    def __enter__(self):
        devices = hid.enumerate(self.vid, self.pid)
        if len(devices) < 2:
            print("HID device not found at index 1.")
            sys.exit(1)
        path = devices[1]['path']  # Use index 1 as per your requirement
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
        response = self.device.read(PACKET_SIZE, 1000)  # Timeout in milliseconds
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
        return ReturnCode.SUCCESS, response

class FileSystem:
    def __init__(self, hid_device: HIDDevice):
        self.hid = hid_device

    def ls(self) -> List[str]:
        ret_code, response = self.hid.execute_command(CommandID.MODULE_CMD_LS)
        if ret_code == ReturnCode.SUCCESS and response:
            return response.decode('utf-8', errors='ignore').strip('\x00').split('\x00')
        else:
            return []

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
            data_chunks = [data[i:i+DATA_SIZE] for i in range(0, len(data), DATA_SIZE)]
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

        packet_size = DATA_SIZE  # Number of data bytes per packet

        while bytes_written < total_bytes:
            chunk = image_data[bytes_written:bytes_written+packet_size]
            ret_code, _ = self.hid.execute_command(CommandID.MODULE_CMD_WRITE_DISPLAY, chunk)
            if ret_code != ReturnCode.SUCCESS:
                print(f"Failed to write display data at offset {bytes_written}")
                return False
            bytes_written += packet_size

        return True

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
        image_data.extend(struct.pack('<H', rgb565))
        # Reconstruct the RGB888 values for display
        processed_pixels.append((r, g, b))
    # Create a new image from the processed pixels
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
        min_distance = None
        closest_color = None
        for i, color in enumerate(colors_rgb):
            dist = color_distance((r, g, b), color)
            if (min_distance is None) or (dist < min_distance):
                min_distance = dist
                closest_color = colors_rgb565[i]
        image_data.extend(struct.pack('<H', closest_color))
        processed_pixels.append(rgb565_to_rgb(closest_color))
    # Create a new image from the processed pixels
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
            image_data.extend(struct.pack('<H', colors[color_index]))

    return bytes(image_data)

def create_animated_bars(width: int, height: int, num_frames: int) -> bytes:
    image_data = bytearray()
    colors = [
        0xE007,  # Green
        0x00F8,  # Blue
        0x1F00,  # Red
    ]

    # create animation frames
    for frame in range(num_frames):
        offset = frame * 2  # shift pattern by 2 pixels each frame
        for y in range(height):
            for x in range(width):
                color_index = ((x + offset) // 16) % len(colors)  # wider bars (16px) that move
                image_data.extend(struct.pack('<H', colors[color_index]))

    return bytes(image_data)

def write_image_to_file(fs: FileSystem, image_data: bytes) -> bool:
    total_bytes = len(image_data)
    bytes_written = 0

    packet_size = DATA_SIZE  # Number of data bytes per packet

    while bytes_written < total_bytes:
        chunk = image_data[bytes_written:bytes_written + packet_size]
        if not fs.write(chunk):
            print(f"Failed to write chunk at offset {bytes_written}")
            return False

        bytes_written += packet_size

    return True

def main():
    VID = 0x1067  # Vendor ID for your device
    PID = 0x626D  # Product ID for your device

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
    parser.add_argument("--quantize", action="store_true", help="Quantize image colors to specific colors")
    parser.add_argument("--background-color", type=str, default="0,0,0", help="Background color for transparency (format: R,G,B)")

    args = parser.parse_args()

    background_color = tuple(map(int, args.background_color.split(',')))

    with HIDDevice(VID, PID) as hid_device:
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
            image_data = create_animated_bars(128, 128, 12)  # 1 second at 24fps
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
            # Display the processed image
            processed_image.show()
            # Send the image data directly to the display
            success = fs.write_display_image(image_data)
            print(f"Wrote image directly to display: {'Success' if success else 'Failed'}")

        elif args.write_image_file:
            image = Image.open(args.write_image_file)
            image = image.resize((128, 128), Image.LANCZOS)
            image = image.convert('RGBA')  # Ensure the image has an alpha channel
            if args.quantize:
                image_data, processed_image = image_to_rgb565_quantized(image, background_color=background_color)
            else:
                image_data, processed_image = image_to_rgb565(image, background_color=background_color)
            # Display the processed image
            processed_image.show()
            output_filename = os.path.splitext(os.path.basename(args.write_image_file))[0] + ".raw"
            if fs.open(output_filename):
                time.sleep(1)
                success = write_image_to_file(fs, image_data)
                fs.close()
                print(f"Wrote image to {output_filename}: {'Success' if success else 'Failed'}")
            else:
                print("Failed to open file for writing image")
        else:
            parser.print_help()

if __name__ == "__main__":
    main()
