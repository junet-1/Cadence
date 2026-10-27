# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22"]
# ///
"""BLE <-> Godot bridge. Kickr via FTMS, Zwift Click/Play/Ride via RideOn handshake.

Sends JSON over UDP to the game (127.0.0.1:47800): {"power","cadence","speed","hr"}, {"button":"up|down"}, {"status"}.
Receives {"grade": percent} (SIM, FTMS 0x11) or {"erg": watts} (ERG, FTMS 0x05) on 127.0.0.1:47801. The last message decides
the mode; only that mode's command reaches the trainer, never both.
Run:  uv run bridge.py [--fake] [--test]
"""
import asyncio, json, os, random, socket, struct, sys, time

FTMS = "00001826-0000-1000-8000-00805f9b34fb"
BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
CONTROL = "00002ad9-0000-1000-8000-00805f9b34fb"
ZWIFT = "00000001-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_RIDE = "0000fc82-0000-1000-8000-00805f9b34fb"
Z_ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
Z_RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
Z_TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_MFR = 0x094A
RIDE_ON = b"RideOn"
GAME, LISTEN = ("127.0.0.1", 47800), ("127.0.0.1", 47801)

out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
target = {"mode": "sim", "grade": 0.0, "erg": 0, "seen": 0.0}


def send(**kw):
    out.sendto(json.dumps(kw).encode(), GAME)


def status(msg):
    print(msg, flush=True)
    send(status=msg)


def parse_bike_data(b):
    """FTMS Indoor Bike Data (0x2AD2). Flag bit 0 is inverted: 0 means speed IS present."""
    flags, i, d = int.from_bytes(b[0:2], "little"), 2, {}
    u16 = lambda: int.from_bytes(b[i:i + 2], "little")
    if not flags & 1: d["speed"] = u16() / 100; i += 2
    if flags & 2: i += 2
    if flags & 4: d["cadence"] = u16() / 2; i += 2
    if flags & 8: i += 2
    if flags & 16: i += 3
    if flags & 32: i += 2
    if flags & 64: d["power"] = struct.unpack_from("<h", b, i)[0]; i += 2
    if flags & 128: i += 2
    if flags & 256: i += 5
    if flags & 512: d["hr"] = b[i]
    return d


def sim_command(grade):
    # opcode 0x11: wind 0.001 m/s, grade 0.01 %, crr 0.0001, cw 0.01 kg/m
    return struct.pack("<BhhBB", 0x11, 0, round(max(-40, min(40, grade)) * 100), 40, 51)


def power_command(watts):
    # opcode 0x05 Set Target Power: sint16 watts
    return struct.pack("<Bh", 0x05, round(max(0, min(2000, watts))))


def command(t):
    """The one control command for the current mode (ERG target power or SIM grade)."""
    return power_command(t["erg"]) if t["mode"] == "erg" else sim_command(round(t["grade"], 1))


def on_game(t, msg):
    """Apply a game message: "erg" switches to ERG, "grade" to SIM. A message carries one of them (main.gd::trainer_command)."""
    if "erg" in msg:
        t.update(mode="erg", erg=float(msg["erg"]))
    elif "grade" in msg:
        t.update(mode="sim", grade=float(msg["grade"]))


def varint(b, i):
    v = shift = 0
    while True:
        v |= (b[i] & 0x7F) << shift; shift += 7; i += 1
        if not b[i - 1] & 0x80: return v, i


def pb_fields(b):
    f, i = {}, 0
    while i < len(b):
        key, i = varint(b, i)
        if key & 7 == 0:
            f[key >> 3], i = varint(b, i)
        elif key & 7 == 2:
            n, i = varint(b, i); f.setdefault(key >> 3, []).append(b[i:i + n]); i += n
        else:
            break  # ponytail: only varint/len wire types seen on Zwift controllers
    return f


def zwift_buttons(data):
    """Set of pressed shift buttons. Zwift encodes pressed as 0 (and proto omits 0, so absent = pressed)."""
    t, f = data[0], pb_fields(data[1:])
    on = lambda n: f.get(n, 0) == 0
    if t == 0x37:  # Click v1
        return {k for k, n in (("up", 1), ("down", 2)) if on(n)}
    if t == 0x07:  # Play: field 1 = right pad, 6 = side button, 8 = paddle (sint32)
        lr = f.get(8, 0); lr = (lr >> 1) ^ -(lr & 1)
        return {"up" if on(1) else "down"} if on(6) or abs(lr) == 100 else set()
    if t == 0x23:  # Ride / Click v2: bitmask, bit cleared = pressed
        m, s = f.get(1, 0xFFFFFFFF), set()
        if not m & 0x1000: s.add("up")
        if not m & (0x2000 | 0x100 | 0x200): s.add("down")
        for p in f.get(3, []):  # analog paddles: location 0 = left, 1 = right
            pf = pb_fields(p); v = pf.get(2, 0); v = (v >> 1) ^ -(v & 1)
            if abs(v) >= 25: s.add("up" if pf.get(1, 0) == 1 else "down")
        return s
    return set()


async def run_trainer(dev):
    async with BleakClient(dev) as c:
        await c.start_notify(BIKE_DATA, lambda _, b: send(**parse_bike_data(b)))
        await c.start_notify(CONTROL, lambda *_: None)  # FTMS refuses control writes without indications
        await c.write_gatt_char(CONTROL, b"\x00", response=True)  # request control
        await c.write_gatt_char(CONTROL, b"\x07", response=True)  # start/resume
        status(f"Trainer verbunden: {dev.name}")
        sent = None
        while c.is_connected:
            cmd = command(target)
            if cmd != sent:
                await c.write_gatt_char(CONTROL, cmd, response=True)
                sent = cmd
            await asyncio.sleep(0.5)
    status(f"Trainer getrennt: {dev.name}")


async def run_controller(dev):
    last = set()

    def on_data(_, b):
        nonlocal last
        if not b or b.startswith(RIDE_ON): return
        now = zwift_buttons(b)
        for k in now - last: send(button=k)
        last = now

    async with BleakClient(dev) as c:
        await c.start_notify(Z_ASYNC, on_data)
        await c.start_notify(Z_TX, on_data)
        await c.write_gatt_char(Z_RX, RIDE_ON, response=False)
        status(f"Controller verbunden: {dev.name}")
        while c.is_connected:
            await asyncio.sleep(1)
    status(f"Controller getrennt: {dev.name}")


def classify(adv):
    uuids = {u.lower() for u in adv.service_uuids}
    if FTMS in uuids: return run_trainer
    if ZWIFT_MFR in adv.manufacturer_data or ZWIFT in uuids or ZWIFT_RIDE in uuids: return run_controller


class Commands(asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        on_game(target, json.loads(data)); target["seen"] = time.monotonic()


async def watchdog():
    # Game is gone (closed or crashed) -> exit, so the BLE connections and the port get freed.
    while not target["seen"] or time.monotonic() - target["seen"] < 10:
        await asyncio.sleep(1)
    print("Spiel weg, beende Bridge", flush=True)
    os._exit(0)


async def fake():
    status("Fake-Modus: kein Bluetooth")
    while True:
        p = target["erg"] + random.uniform(-6, 6) if target["mode"] == "erg" else 180 + random.uniform(-20, 20) - target["grade"] * 3
        send(power=round(p), cadence=round(88 + random.uniform(-3, 3)), hr=135)
        await asyncio.sleep(0.25)


async def scan():
    tasks = {}
    while True:
        if not tasks: status("Suche Geräte …")
        for dev, adv in (await BleakScanner.discover(timeout=5, return_adv=True)).values():
            run = classify(adv)
            if run and (dev.address not in tasks or tasks[dev.address].done()):
                tasks[dev.address] = asyncio.create_task(guard(run, dev))
        tasks = {a: t for a, t in tasks.items() if not t.done()}
        await asyncio.sleep(5)


async def guard(run, dev):
    try:
        await run(dev)
    except Exception as e:  # BLE drops are normal; scan() reconnects
        status(f"{dev.name}: {e}")


async def main():
    await asyncio.get_running_loop().create_datagram_endpoint(Commands, local_addr=LISTEN)
    await asyncio.gather(watchdog(), fake() if "--fake" in sys.argv else scan())


def test():
    assert parse_bike_data(bytes.fromhex("4400280aaa00c800")) == {"speed": 26.0, "cadence": 85.0, "power": 200}
    assert parse_bike_data(bytes.fromhex("4402280aaa00c8008c")) == {"speed": 26.0, "cadence": 85.0, "power": 200, "hr": 140}
    assert sim_command(3.5) == bytes.fromhex("1100005e012833")
    assert power_command(200) == bytes.fromhex("05c800") and power_command(-5) == bytes.fromhex("050000")
    t = {"mode": "sim", "grade": 0.0, "erg": 0}
    on_game(t, {"grade": 3.5}); assert command(t) == sim_command(3.5)
    on_game(t, {"erg": 250}); assert command(t) == power_command(250) and command(t)[0] == 0x05  # ERG: kein Steigungsbefehl
    on_game(t, {"grade": -2.0}); assert command(t) == sim_command(-2.0)  # zurück zu SIM: kein Leistungsziel mehr
    assert zwift_buttons(bytes.fromhex("3708001001")) == {"up"}
    assert zwift_buttons(bytes.fromhex("3708011001")) == set()
    assert zwift_buttons(bytes.fromhex("3708011000")) == {"down"}
    released = bytes([0x23, 0x08]) + bytes.fromhex("ffffffff0f")
    assert zwift_buttons(released) == set()
    pressed = bytearray(released); pressed[3] = 0xDF  # bit 12 (0x1000) sits in varint byte 1 as 0x20
    assert zwift_buttons(bytes(pressed)) == {"up"}
    print("ok")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    else:
        from bleak import BleakClient, BleakScanner
        asyncio.run(main())
