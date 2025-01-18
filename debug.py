from pprint import pprint

import hid
for device in hid.enumerate():
    print(device)
    # print(f"VID: {device['vendor_id']}, PID: {device['product_id']}, Path: {device['path']}")
