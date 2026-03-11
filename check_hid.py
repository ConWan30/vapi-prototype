import hid
devs = hid.enumerate(0x054C, 0x0DF2)
print(f"{len(devs)} interfaces found")
for d in devs:
    print(f"  iface {d['interface_number']}: usage_page={d['usage_page']} usage={d['usage']}")
