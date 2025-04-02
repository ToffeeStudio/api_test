#!/usr/bin/env python3
"""
test_cdc.py - Robust CDC discovery using a PING command.
This script scans available serial ports, sends a 32-byte PING packet (with magic 0x09 and command ID 0x5F),
and looks for the expected "OK" response from the device.
"""

import serial
import serial.tools.list_ports
import struct
import time

# Expected identifiers (match the firmware USB VID/PID)
EXPECTED_VID = 0x1067
EXPECTED_PID = 0x626D

# For CDC, our firmware sends "OK" on successful packet processing.
EXPECTED_RESPONSE = b"OK"

BAUD_RATE = 115200
PING_MAGIC = 0x09
PING_COMMAND_ID = 0x5F
PACKET_SIZE = 32  # Must match RAW_EPSIZE in firmware

def send_ping(ser):
    """
    Build and send a 32-byte PING packet.
    The header is 6 bytes (magic, command, packet_id) and the rest is zero-padded.
    """
    packet_id = 0  # For this test, we use packet_id 0
    header = struct.pack("<BBI", PING_MAGIC, PING_COMMAND_ID, packet_id)
    packet = header.ljust(PACKET_SIZE, b'\x00')
    print(f"Sending packet: {packet.hex()}")
    ser.reset_input_buffer()  # Clear any stale input
    ser.write(packet)
    ser.flush()

def get_response(ser, timeout=2.0):
    """
    Poll for available data for up to `timeout` seconds.
    Returns any received data.
    """
    end_time = time.time() + timeout
    received = b""
    while time.time() < end_time:
        # Read available bytes (or wait for 1 byte if nothing is there)
        data = ser.read(ser.in_waiting or 1)
        if data:
            received += data
            # If we find the expected response, we break early.
            if EXPECTED_RESPONSE in received:
                break
        time.sleep(0.1)
    return received

def find_module_port():
    """
    Scan available serial ports for one matching the expected VID/PID.
    For each candidate, send a PING and check for the expected response.
    """
    ports = list(serial.tools.list_ports.comports())
    print("Scanning available ports:")
    for port in ports:
        print(f"Port: {port.device} - hwid: {port.hwid}")
        expected_hwid = f"VID:PID={EXPECTED_VID:04X}:{EXPECTED_PID:04X}"
        if expected_hwid not in port.hwid:
            continue  # Skip ports that don't match
        try:
            ser = serial.Serial(port.device, BAUD_RATE, timeout=1)
            # Assert DTR and RTS (often required for CDC devices)
            ser.setDTR(True)
            ser.setRTS(True)
            time.sleep(2)  # Allow the CDC interface to initialize
            send_ping(ser)
            response = get_response(ser, timeout=2.0)
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
        ser.setDTR(True)
        ser.setRTS(True)
    except Exception as e:
        print(f"Error opening serial port: {e}")
        return

    time.sleep(2)  # Allow the device to settle after port open
    send_ping(ser)
    response = get_response(ser, timeout=2.0)
    print(f"Received response: {response}")
    ser.close()

if __name__ == "__main__":
    main()

