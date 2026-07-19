#!/usr/bin/env python3
"""
reset_epson.py - Read and reset Epson waste-ink counters for 100+ models.

Pure Python standard library. No pip installs, no net-snmp, no Docker.

  Network (recommended; model auto-detected if you omit --model):
      python reset_epson.py --host 192.168.1.50 read
      python reset_epson.py --host 192.168.1.50 --model L3151 reset

  USB (Windows; --model is required, no auto-detect):
      python reset_epson.py --usb "EPSON L3150 Series" --model L3151 read

  Utilities:
      python reset_epson.py --list-models
      python reset_epson.py --self-test        # verify encoder offline

Model constants come from the open-source Ircama/epson_print_conf database
(see models.py). Talks directly to hardware you own via the printer's own
maintenance protocol - nothing here is derived from any commercial binary.
"""

import argparse
import re
import socket
import struct
import sys
import time

try:
    from models import MODELS
except ImportError:
    sys.stderr.write("ERROR: models.py not found next to this script.\n")
    raise

OID_PREFIX = "1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1"
MODEL_NAME_OID = "1.3.6.1.4.1.1248.1.1.3.1.3.8.0"


# ==========================================================================
# Minimal SNMPv1 over UDP (hand-rolled BER) - standard library only
# ==========================================================================
def _ber_len(n):
    if n < 0x80:
        return bytes([n])
    out = b""
    while n:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    return bytes([0x80 | len(out)]) + out


def _tlv(tag, value):
    return bytes([tag]) + _ber_len(len(value)) + value


def _ber_int(n):
    out, v = b"", n
    while True:
        out = bytes([v & 0xFF]) + out
        v >>= 8
        if (v == 0 and not (out[0] & 0x80)) or (v == -1 and (out[0] & 0x80)):
            break
    return _tlv(0x02, out)


def _ber_oid(oid_str):
    parts = [int(x) for x in oid_str.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        if p < 0x80:
            body += bytes([p])
            continue
        stack = [p & 0x7F]
        p >>= 7
        while p:
            stack.append((p & 0x7F) | 0x80)
            p >>= 7
        body += bytes(reversed(stack))
    return _tlv(0x06, body)


def _parse_tlv(data, i):
    tag, length = data[i], data[i + 1]
    i += 2
    if length & 0x80:
        num = length & 0x7F
        length = int.from_bytes(data[i:i + num], "big")
        i += num
    return tag, data[i:i + length], i + length


class SnmpTransport:
    def __init__(self, host, community="public", port=161, timeout=5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.community = community.encode()
        self._rid = 0x4552

    def _get(self, oid_str):
        self._rid = (self._rid + 1) & 0x7FFFFFFF
        varbind = _tlv(0x30, _ber_oid(oid_str) + _tlv(0x05, b""))
        pdu = _tlv(0xA0, _ber_int(self._rid) + _ber_int(0) + _ber_int(0)
                   + _tlv(0x30, varbind))
        msg = _tlv(0x30, _ber_int(0) + _tlv(0x04, self.community) + pdu)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            sock.sendto(msg, (self.host, self.port))
            data, _ = sock.recvfrom(4096)
        finally:
            sock.close()
        return self._extract_value(data)

    @staticmethod
    def _extract_value(data):
        _, msg, _ = _parse_tlv(data, 0)
        i = 0
        _, _, i = _parse_tlv(msg, i)
        _, _, i = _parse_tlv(msg, i)
        _, pdu, _ = _parse_tlv(msg, i)
        j = 0
        _, _, j = _parse_tlv(pdu, j)
        _, es, j = _parse_tlv(pdu, j)
        _, _, j = _parse_tlv(pdu, j)
        _, vbs, _ = _parse_tlv(pdu, j)
        _, vb, _ = _parse_tlv(vbs, 0)
        k = 0
        _, _, k = _parse_tlv(vb, k)
        _, val, _ = _parse_tlv(vb, k)
        if int.from_bytes(es, "big"):
            raise IOError("SNMP error-status %d" % int.from_bytes(es, "big"))
        return val

    def get_model_name(self):
        return self._get(MODEL_NAME_OID).decode("latin-1", "replace").strip()

    def epctrl(self, command, payload):
        return self._get(epctrl_oid(command, payload))


# ==========================================================================
# USB transport (Windows) - ctypes + winspool, uses the installed Epson driver
# ==========================================================================
class UsbTransport:
    _INIT = b"\x1b@"
    _ENTER_REMOTE = b"\x1b(R\x08\x00\x00REMOTE1"
    _EXIT_REMOTE = b"\x1b\x00\x00\x00"

    def __init__(self, printer_name):
        if not sys.platform.startswith("win"):
            raise RuntimeError("USB transport is Windows-only; use --host elsewhere.")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.spool = ctypes.WinDLL("winspool.drv")
        self.printer_name = printer_name

    def _send_and_read(self, message):
        ctypes, wt = self.ctypes, self.wintypes

        class DOC_INFO_1(ctypes.Structure):
            _fields_ = [("pDocName", wt.LPWSTR), ("pOutputFile", wt.LPWSTR),
                        ("pDatatype", wt.LPWSTR)]

        hPrinter = wt.HANDLE()
        if not self.spool.OpenPrinterW(self.printer_name, ctypes.byref(hPrinter), None):
            raise IOError("OpenPrinter failed for %r (check the exact name in "
                          "Settings > Printers)" % self.printer_name)
        try:
            doc = DOC_INFO_1("epson reset", None, "RAW")
            if not self.spool.StartDocPrinterW(hPrinter, 1, ctypes.byref(doc)):
                raise IOError("StartDocPrinter failed")
            self.spool.StartPagePrinter(hPrinter)
            written = wt.DWORD(0)
            buf = ctypes.create_string_buffer(message, len(message))
            self.spool.WritePrinter(hPrinter, buf, len(message), ctypes.byref(written))
            self.spool.EndPagePrinter(hPrinter)
            self.spool.EndDocPrinter(hPrinter)
            time.sleep(0.3)
            out, read, rbuf = b"", wt.DWORD(0), ctypes.create_string_buffer(4096)
            for _ in range(20):
                if not self.spool.ReadPrinter(hPrinter, rbuf, 4096, ctypes.byref(read)):
                    break
                if read.value == 0:
                    if out:
                        break
                    time.sleep(0.1)
                    continue
                out += rbuf.raw[:read.value]
                if b"\x0c" in out or b";" in out:
                    break
            return out
        finally:
            self.spool.ClosePrinter(hPrinter)

    def epctrl(self, command, payload):
        frame = command + struct.pack("<H", len(payload)) + bytes(payload)
        return self._send_and_read(self._INIT + self._ENTER_REMOTE + frame + self._EXIT_REMOTE)


# ==========================================================================
# EPSON-CTRL message construction
# ==========================================================================
def epctrl_oid(command, payload):
    frame = command + struct.pack("<H", len(payload)) + bytes(payload)
    return OID_PREFIX + "." + ".".join(str(b) for b in frame)


def _caesar(key):
    return [0 if b == 0 else b + 1 for b in key]


def read_payload(read_key, addr):
    return [read_key[0], read_key[1], 65, 190, 160, addr % 256, addr // 256]


def write_payload(read_key, write_key, addr, value):
    return ([read_key[0], read_key[1], 66, 189, 33, addr % 256, addr // 256, value]
            + _caesar(write_key))


# ==========================================================================
# EEPROM read/write on top of a transport + model profile
# ==========================================================================
class EpsonEeprom:
    def __init__(self, transport, profile):
        self.t = transport
        self.read_key = profile["read_key"]
        self.write_key = profile["write_key"]

    def read_byte(self, addr):
        resp = self.t.epctrl(b"||", read_payload(self.read_key, addr))
        text = resp.decode("latin-1", "replace") if isinstance(resp, (bytes, bytearray)) else resp
        m = re.search(r"EE:([0-9A-Fa-f]{6})", text)
        if not m:
            raise IOError("No EE: payload for addr %d. Raw=%r" % (addr, resp))
        addr_hex, val_hex = m.group(1)[:4], m.group(1)[4:]
        if int(addr_hex, 16) != addr:
            raise IOError("EEPROM address mismatch: asked %d got %d" % (addr, int(addr_hex, 16)))
        return int(val_hex, 16)

    def write_byte(self, addr, value):
        resp = self.t.epctrl(b"||", write_payload(self.read_key, self.write_key, addr, value))
        text = resp.decode("latin-1", "replace") if isinstance(resp, (bytes, bytearray)) else resp
        if ":OK;" not in text:
            raise IOError("Write not confirmed (addr %d=%d). Raw=%r" % (addr, value, resp))
        return True


# ==========================================================================
# High-level operations (profile-driven)
# ==========================================================================
def read_counters(eeprom, profile):
    """Return [(name, value, unit)] where unit is '%' or 'raw'."""
    out = []
    for name, oids, divider in profile.get("counters", []):
        hexvals = ["%02X" % eeprom.read_byte(o) for o in oids]
        raw = int("".join(reversed(hexvals)), 16)
        if divider:
            out.append((name, round(raw / divider, 2), "%"))
        else:
            out.append((name, raw, "raw"))
    return out


def reset_addresses(profile):
    """Return the {addr: value} map a reset writes."""
    return dict(profile["reset"])


def dump_backup(eeprom, profile, path):
    values = {a: eeprom.read_byte(a) for a in sorted(reset_addresses(profile))}
    lines = ["# Epson EEPROM backup - %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "# address(dec) = value(dec)  [hex]"]
    lines += ["%d = %d  [0x%02X]" % (a, v, v) for a, v in values.items()]
    open(path, "w").write("\n".join(lines) + "\n")
    return values


def do_reset(eeprom, profile):
    for addr, value in reset_addresses(profile).items():
        eeprom.write_byte(addr, value)


def _fmt_counter(name, value, unit):
    return ("  %-18s %8.2f %%" % (name, value) if unit == "%"
            else "  %-18s %8d (raw)" % (name, value))


# ==========================================================================
# Model selection / detection
# ==========================================================================
def _norm(name):
    return re.sub(r"\b(epson|series)\b", "", name, flags=re.I).replace(" ", "").upper()


def resolve_model(name):
    """Match a user- or printer-supplied name to a MODELS key. Returns key or None."""
    if name in MODELS:
        return name
    target = _norm(name)
    norm_map = {_norm(k): k for k in MODELS}
    if target in norm_map:
        return norm_map[target]
    # prefix match (e.g. detected 'L3150' vs key 'L3150')
    for nk, k in norm_map.items():
        if target and (target.startswith(nk) or nk.startswith(target)):
            return k
    return None


# ==========================================================================
# Offline self-test
# ==========================================================================
def self_test():
    ok = True

    def check(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
        print("[%s] %s" % ("PASS" if got == want else "FAIL", name))
        if got != want:
            print("       got:  %s\n       want: %s" % (got, want))

    rk = MODELS["L3151"]["read_key"]
    wk = MODELS["L3151"]["write_key"]
    check("read OID (L3151 addr 47)",
          epctrl_oid(b"||", read_payload(rk, 47)),
          OID_PREFIX + ".124.124.7.0.151.7.65.190.160.47.0")
    check("write OID (L3151 addr 47=0)",
          epctrl_oid(b"||", write_payload(rk, wk, 47, 0)),
          OID_PREFIX + ".124.124.16.0.151.7.66.189.33.47.0.0.78.98.115.106.99.98.122.98")
    check("high address split (258)",
          epctrl_oid(b"||", read_payload(rk, 258)),
          OID_PREFIX + ".124.124.7.0.151.7.65.190.160.2.1")
    check("model resolve 'EPSON L3150 Series' -> L3150",
          resolve_model("EPSON L3150 Series"), "L3150")
    check("model resolve 'l3151' -> L3151", resolve_model("l3151"), "L3151")
    check("L3151 reset map",
          MODELS["L3151"]["reset"],
          {28: 0, 47: 0, 48: 0, 49: 0, 50: 0, 51: 0, 52: 0, 53: 0, 54: 94, 55: 94})
    check("every model has keys + reset + 2-byte read_key",
          all("read_key" in v and len(v["read_key"]) == 2 and "write_key" in v
              and v.get("reset") for v in MODELS.values()), True)

    # SNMP parse round-trip
    payload = b"\x00@BDC PS\r\nEE:002F00;\r\n\x0c"
    vb = _tlv(0x30, _ber_oid("1.3.6.1.4.1.1248") + _tlv(0x04, payload))
    pdu = _tlv(0xA2, _ber_int(1) + _ber_int(0) + _ber_int(0) + _tlv(0x30, vb))
    msg = _tlv(0x30, _ber_int(0) + _tlv(0x04, b"public") + pdu)
    m = re.search(r"EE:([0-9A-Fa-f]{6})",
                  SnmpTransport._extract_value(msg).decode("latin-1"))
    check("SNMP parse -> EE payload", m.group(1) if m else None, "002F00")

    print("\n%s (%d models loaded)" % ("ALL PASSED" if ok else "SOME FAILED", len(MODELS)))
    return 0 if ok else 1


# ==========================================================================
# CLI
# ==========================================================================
def main(argv=None):
    p = argparse.ArgumentParser(
        description="Read/reset Epson waste-ink counters (100+ models, no per-reset fee).",
        epilog="Network (SNMP) is the verified path and auto-detects the model. "
               "USB is best-effort on Windows and needs --model.")
    p.add_argument("action", nargs="?", choices=["read", "backup", "reset"])
    p.add_argument("--host", help="printer IP address (network/SNMP)")
    p.add_argument("--usb", metavar="PRINTER_NAME", help="exact Windows printer name (USB)")
    p.add_argument("--model", help="printer model (e.g. L3151); auto-detected over network")
    p.add_argument("--backup-file", default=None)
    p.add_argument("--yes", action="store_true", help="skip the reset confirmation")
    p.add_argument("--list-models", action="store_true", help="list supported models and exit")
    p.add_argument("--self-test", action="store_true", help="verify the encoder offline")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.list_models:
        names = sorted(MODELS)
        print("%d supported models:\n" % len(names))
        for i in range(0, len(names), 6):
            print("  " + ", ".join(names[i:i + 6]))
        return 0
    if not args.action:
        p.error("choose an action (read/backup/reset), or use --list-models / --self-test")

    if args.usb:
        transport = UsbTransport(args.usb)
    elif args.host:
        transport = SnmpTransport(args.host)
    else:
        p.error("specify --host <ip> (network) or --usb \"<name>\" (USB)")

    # Resolve the model.
    model_key = None
    if args.model:
        model_key = resolve_model(args.model)
        if not model_key:
            p.error("unknown model %r. Try --list-models." % args.model)
    elif args.host:
        try:
            detected = transport.get_model_name()
        except (OSError, IOError) as e:
            print("ERROR: could not query the printer to auto-detect the model: %s\n"
                  "  Pass --model explicitly (see --list-models)." % e, file=sys.stderr)
            return 2
        model_key = resolve_model(detected)
        if not model_key:
            print("Printer reports model %r, which has no reset data in models.py.\n"
                  "  Supported models: --list-models. It may still be an alias; try --model."
                  % detected, file=sys.stderr)
            return 2
        print("Auto-detected model: %s (reported %r)" % (model_key, detected))
    else:
        p.error("USB needs --model (auto-detect is network-only). See --list-models.")

    profile = MODELS[model_key]
    eeprom = EpsonEeprom(transport, profile)
    try:
        return _run(args, eeprom, profile, model_key)
    except socket.timeout:
        print("ERROR: no response from the printer (timed out). Check IP/network and "
              "that SNMP is enabled.", file=sys.stderr)
        return 2
    except (OSError, IOError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2


def _run(args, eeprom, profile, model_key):
    if args.action == "read":
        print("Waste-ink counters for %s:" % model_key)
        for name, value, unit in read_counters(eeprom, profile):
            print(_fmt_counter(name, value, unit))
        return 0

    backup_path = args.backup_file or ("backup-%s-%s.txt"
                                       % (model_key.replace(" ", "_"),
                                          time.strftime("%Y%m%d-%H%M%S")))
    print("Reading counters and backing up affected EEPROM bytes...")
    before = read_counters(eeprom, profile)
    values = dump_backup(eeprom, profile, backup_path)
    for name, value, unit in before:
        print(_fmt_counter(name, value, unit))
    print("  backup -> %s" % backup_path)
    print("  values: " + ", ".join("%d=%d" % (a, values[a]) for a in sorted(values)))
    if args.action == "backup":
        return 0

    if not args.yes:
        if input("\nWrite reset values now? This clears the counter. [y/N] ").strip().lower() != "y":
            print("Aborted. Nothing was written.")
            return 1
    print("Writing reset values...")
    do_reset(eeprom, profile)
    after = {name: (value, unit) for name, value, unit in read_counters(eeprom, profile)}
    print("Done. Power the printer off for 10 seconds, then back on.")
    for name, value, unit in before:
        nv, nu = after.get(name, (0.0, unit))
        suffix = " %%" if unit == "%" else ""
        print("  %-18s %8.2f%s  ->  %8.2f%s" % (name, value, suffix, nv, suffix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
