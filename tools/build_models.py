#!/usr/bin/env python3
"""Regenerate ../models.py by merging two open-source Epson databases:

  A) Ircama/epson_print_conf  (epson_print_conf.py)  - verified, has dividers
  B) atufi/reinkpy            (epson.toml)            - large model coverage

Both source files are VENDORED under ../vendor/, so this builds fully offline
and keeps working even if the upstream URLs disappear:

    python tools/build_models.py            # build from vendored sources
    python tools/build_models.py --refresh  # re-download, update vendor/, build

Canonical per-model entry written to models.py:
  { "read_key":[lo,hi], "write_key":b"...",
    "reset":{addr:val,...},
    "counters":[[name,[oids],divider_or_None],...],
    "source":"epc"|"reinkpy"|"epc+reinkpy" }

reinkpy models are imported only for the validated 2-byte key format
(rlen==2 and wlen==2). Overlap with epson_print_conf is cross-validated;
any conflict is reported and the verified (epc) value is kept.
"""
import os
import sys
import tomllib
import urllib.request
from itertools import chain

EPC_URL = "https://raw.githubusercontent.com/Ircama/epson_print_conf/main/epson_print_conf.py"
REINK_URL = "https://codeberg.org/atufi/reinkpy/raw/branch/main/reinkpy/epson.toml"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "models.py")
VENDOR = os.path.join(HERE, "..", "vendor")
EPC_FILE = os.path.join(VENDOR, "epson_print_conf.py")
REINK_FILE = os.path.join(VENDOR, "epson.toml")

WASTE_TYPES = ("main_waste", "borderless_waste", "first_waste",
               "second_waste", "third_waste")


def load_source(path, url, refresh):
    """Return file bytes from the vendored copy, or re-download and update it."""
    if refresh:
        print("downloading", url)
        data = urllib.request.urlopen(url, timeout=30).read()
        with open(path, "wb") as f:
            f.write(data)
        print("  updated", os.path.relpath(path))
        return data
    with open(path, "rb") as f:
        return f.read()


def reverse_caesar(s):
    return "".join(chr(ord(c) - 1) if c != "\x00" else "\x00" for c in s)


def load_epc(text):
    i = text.index("{", text.index("PRINTER_CONFIG = {"))
    depth, j = 0, i
    while j < len(text):
        depth += 1 if text[j] == "{" else -1 if text[j] == "}" else 0
        if depth == 0:
            break
        j += 1
    conf = eval(text[i:j + 1], {"range": range, "chain": chain})
    for name, d in list(conf.items()):
        for a in d.get("alias", []):
            conf.setdefault(a, d)
    for name, d in list(conf.items()):
        if "same-as" in d and d["same-as"] in conf:
            conf[name] = {**conf[d["same-as"]], **d}

    out = {}
    for name, d in conf.items():
        if "read_key" not in d or "write_key" not in d:
            continue
        if "raw_waste_reset" not in d and "main_waste" not in d:
            continue
        if "raw_waste_reset" in d:
            reset = dict(d["raw_waste_reset"])
        else:
            reset = {}
            for wt in WASTE_TYPES:
                if wt in d:
                    for o in d[wt]["oids"]:
                        reset.setdefault(o, 0)
        counters = [[wt, list(d[wt]["oids"]), d[wt]["divider"]]
                    for wt in WASTE_TYPES if wt in d]
        out[name] = {"read_key": list(d["read_key"]),
                     "write_key": bytes(d["write_key"]),
                     "reset": reset, "counters": counters, "source": "epc"}
    return out


def load_reinkpy(raw):
    epson = tomllib.loads(raw.decode("utf-8"))["EPSON"]
    out, skipped = {}, 0
    for b in epson:
        n = len(b.get("models", []))
        if b.get("rlen") != 2 or b.get("wlen") != 2 or "rkey" not in b:
            skipped += n
            continue
        if "wkey1" in b:
            wkey = b["wkey1"]
        elif "wkey" in b:
            wkey = reverse_caesar(b["wkey"])
        else:
            skipped += n
            continue
        rk = b["rkey"]
        reset, counter_oids = {}, []
        for m in b["mem"]:
            if "reset" in m:
                for a, v in zip(m["addr"], m["reset"]):
                    reset[a] = v
            else:
                for a in m["addr"]:
                    reset.setdefault(a, 0)
                counter_oids.append(list(m["addr"]))
        if not reset:  # keys known but waste addresses not mapped
            skipped += n
            continue
        entry = {"read_key": [rk & 0xFF, (rk >> 8) & 0xFF],
                 "write_key": wkey.encode("latin-1"),
                 "reset": reset,
                 "counters": [["waste%d" % i, o, None]
                              for i, o in enumerate(counter_oids)],
                 "source": "reinkpy"}
        for name in b["models"]:
            out[name] = entry
    return out, skipped


def main():
    refresh = "--refresh" in sys.argv[1:]
    A = load_epc(load_source(EPC_FILE, EPC_URL, refresh).decode("utf-8"))
    B, skipped = load_reinkpy(load_source(REINK_FILE, REINK_URL, refresh))

    merged = dict(A)
    agree = conflict = 0
    conflicts = []
    for name, be in B.items():
        if name in A:
            ae = A[name]
            if (ae["read_key"] == be["read_key"]
                    and ae["write_key"] == be["write_key"]
                    and ae["reset"] == be["reset"]):
                agree += 1
                merged[name]["source"] = "epc+reinkpy"
            else:
                conflict += 1
                conflicts.append(name)
        else:
            merged[name] = be

    lines = ['"""Auto-generated by tools/build_models.py from the open-source',
             'epson_print_conf (EUPL-1.2) and reinkpy databases. Do not hand-edit."""',
             "", "MODELS = {"]
    for name in sorted(merged):
        lines.append("    %r: %r," % (name, merged[name]))
    lines.append("}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\nepson_print_conf models:", len(A))
    print("reinkpy models (2-byte): ", len(B))
    print("reinkpy skipped:         ", skipped)
    print("overlap agree/conflict:   %d / %d" % (agree, conflict))
    print("TOTAL merged models:     ", len(merged))
    if conflicts:
        print("CONFLICTS (kept epc):", ", ".join(conflicts))


if __name__ == "__main__":
    main()
