#!/usr/bin/env python3
"""Read a file out of a WoW 1.12 client's MPQ archives (format v1), patch chain order.

  tools/mpq.py /path/to/WoW-1.12/Data 'WTF\\DefaultBindings.wtf' > DefaultBindings.wtf

Covers what 1.12's archives use: the encrypted hash and block tables, sectored or single-unit files, zlib
and bzip2 sectors, and encrypted files. PKWARE-imploded and audio-compressed sectors raise an error.
What it reads is Blizzard data from your own install: don't commit it.
"""
import bz2
import struct
import sys
import zlib
from pathlib import Path

# Later archives override earlier ones; the client loads the patches last.
CHAIN = ["patch-2.MPQ", "patch.MPQ", "base.MPQ", "backup.MPQ", "interface.MPQ", "misc.MPQ", "dbc.MPQ", "fonts.MPQ",
         "model.MPQ", "texture.MPQ", "terrain.MPQ", "wmo.MPQ", "sound.MPQ", "speech.MPQ"]


def _crypt_table():
    table, seed = [0] * 0x500, 0x00100001
    for i in range(0x100):
        for j in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            hi = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            table[i + j * 0x100] = hi | (seed & 0xFFFF)
    return table


CRYPT = _crypt_table()


def hash_string(s, kind):
    seed1, seed2 = 0x7FED7FED, 0xEEEEEEEE
    for ch in s.upper().replace("/", "\\").encode("latin-1"):
        value = CRYPT[(kind << 8) + ch]
        seed1 = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
        seed2 = (ch + seed1 + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
    return seed1


def decrypt(data, key):
    out, seed = bytearray(), 0xEEEEEEEE
    for (value,) in struct.iter_unpack("<I", data[:len(data) // 4 * 4]):
        seed = (seed + CRYPT[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        ch = (value ^ (key + seed)) & 0xFFFFFFFF
        key = ((((~key) << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (ch + seed + (seed << 5) + 3) & 0xFFFFFFFF
        out += struct.pack("<I", ch)
    return bytes(out) + data[len(data) // 4 * 4:]


EXISTS, COMPRESS, IMPLODE, ENCRYPTED, FIX_KEY, SINGLE_UNIT = 0x80000000, 0x200, 0x100, 0x10000, 0x20000, 0x1000000


class Archive:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.base = 0
        while True:  # the header sits at a 512-byte boundary
            self.f.seek(self.base)
            head = self.f.read(32)
            if len(head) < 32:
                raise ValueError(f"{path}: no MPQ header")
            if head[:4] == b"MPQ\x1a":
                break
            self.base += 512
        (_, _, _, _, shift, ht_pos, bt_pos, ht_size, bt_size) = struct.unpack("<4sIIHHIIII", head)
        self.sector = 512 << shift
        self.f.seek(self.base + ht_pos)
        ht = decrypt(self.f.read(ht_size * 16), hash_string("(hash table)", 3))
        self.hashes = list(struct.iter_unpack("<IIHHI", ht))
        self.f.seek(self.base + bt_pos)
        bt = decrypt(self.f.read(bt_size * 16), hash_string("(block table)", 3))
        self.blocks = list(struct.iter_unpack("<IIII", bt))

    def find(self, name):
        n = len(self.hashes)
        a, b = hash_string(name, 1), hash_string(name, 2)
        i = start = hash_string(name, 0) % n
        while True:
            h1, h2, _, _, block = self.hashes[i]
            if block == 0xFFFFFFFF:
                return None
            if h1 == a and h2 == b and block != 0xFFFFFFFE:
                return block
            i = (i + 1) % n
            if i == start:
                return None

    def read(self, name):
        index = self.find(name)
        if index is None:
            return None
        offset, csize, fsize, flags = self.blocks[index]
        if not flags & EXISTS:
            return None
        self.f.seek(self.base + offset)
        raw = self.f.read(csize)
        key = None
        if flags & ENCRYPTED:
            key = hash_string(name.replace("/", "\\").rsplit("\\", 1)[-1], 3)
            if flags & FIX_KEY:
                key = ((key + offset) ^ fsize) & 0xFFFFFFFF
        if flags & SINGLE_UNIT:
            data = decrypt(raw, key) if key is not None else raw
            return _decompress(data, fsize) if flags & (COMPRESS | IMPLODE) and csize < fsize else data
        count = (fsize + self.sector - 1) // self.sector
        if not flags & (COMPRESS | IMPLODE):
            sectors = [raw[i * self.sector:(i + 1) * self.sector] for i in range(count)]
            if key is not None:
                sectors = [decrypt(s, (key + i) & 0xFFFFFFFF) for i, s in enumerate(sectors)]
            return b"".join(sectors)[:fsize]
        table = raw[:(count + 1) * 4]
        if key is not None:
            table = decrypt(table, (key - 1) & 0xFFFFFFFF)
        bounds = struct.unpack(f"<{count + 1}I", table)
        out = bytearray()
        for i in range(count):
            chunk = raw[bounds[i]:bounds[i + 1]]
            if key is not None:
                chunk = decrypt(chunk, (key + i) & 0xFFFFFFFF)
            want = min(self.sector, fsize - i * self.sector)
            out += _decompress(chunk, want) if len(chunk) < want else chunk
        return bytes(out)


def _decompress(data, size):
    mask, body = data[0], data[1:]
    if mask == 0x02:
        return zlib.decompress(body)
    if mask == 0x10:
        return bz2.decompress(body)
    raise ValueError(f"MPQ compression {mask:#x} not supported")


def read_from_chain(data_dir, name):
    """The file as the client sees it: from the first archive in patch order that has it, or None."""
    for archive in CHAIN:
        path = Path(data_dir) / archive
        if path.exists():
            content = Archive(path).read(name)
            if content is not None:
                return content
    return None


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    content = read_from_chain(sys.argv[1], sys.argv[2])
    if content is None:
        raise SystemExit(f"{sys.argv[2]} not found in {sys.argv[1]}")
    sys.stdout.buffer.write(content)
