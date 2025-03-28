#!/usr/bin/env python3
"""
test_cdc.py - Robust CDC discovery using a PING command.
This script cycles through all serial ports, sends a PING packet,
and selects the port that responds with the expected identifier.
"""

import serial
import serial.tools.list_ports
import struct
import time

EXPECTED_VID = 0x1067  # You can still use these if available
EXPECTED_PID = 0x626D
EXPECTED_RESPONSE = b"TS_Module_v1"
BAUD_RATE = 115200
PING_MAGIC = 0x09
# New command: id_module_cmd_ping = 0x5F
PING_COMMAND_ID = 0x5F

def send_ping(ser):
    packet_id = 0
    header = struct.pack("<BBI", PING_MAGIC, PING_COMMAND_ID, packet_id)
    ser.reset_input_buffer()
    ser.write(header)
    ser.flush()
    time.sleep(1.0)  # Increased delay for CDC initialization and processing
    response = ser.read_all()
    return response

def find_module_port():
    ports = list(serial.tools.list_ports.comports())
    print("Scanning available ports:")
    for port in ports:
        print(f"Port: {port.device} - hwid: {port.hwid}")
        # Optionally, check if the hwid contains expected VID/PID
        expected_hwid = f"VID:PID={EXPECTED_VID:04X}:{EXPECTED_PID:04X}"
        if expected_hwid not in port.hwid:
            continue  # Skip ports that don't match VID/PID
        try:
            ser = serial.Serial(port.device, BAUD_RATE, timeout=0.5)
            # Give the device a moment to initialize
            time.sleep(2)
            response = send_ping(ser)
            ser.close()
            if EXPECTED_RESPONSE in response:
                print(f"Found target device on port: {port.device}")
                return port.device
            else:
                print(f"Port {port.device} did not respond correctly: {response}")
        except Exception as e:
            print(f"Error opening port {port.device}: {e}")
    return None

def main():
    port = find_module_port()
    if not port:
        print("Device not found. Please check your connection and try again.")
        return

    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
    except Exception as e:
        print(f"Error opening serial port: {e}")
        return

    # Test communication with a PING command
    time.sleep(2)  # Allow device to settle
    response = send_ping(ser)
    print(f"Received response: {response}")
    ser.close()

if __name__ == "__main__":
    main()

