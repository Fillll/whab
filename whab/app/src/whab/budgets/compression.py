"""Reuse independent DEFLATE blocks in ordinary, fully readable ZIP entries."""

import zlib

BLOCK_BYTES = 64 * 1024


class _BlockCompressor:
    def __init__(self, previous):
        self.previous = previous
        self.blocks = []

    def compress(self, raw):
        for index, offset in enumerate(range(0, len(raw), BLOCK_BYTES)):
            block = raw[offset:offset + BLOCK_BYTES]
            if index < len(self.previous) and self.previous[index][0] == block:
                encoded = self.previous[index][1]
            else:
                compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
                encoded = compressor.compress(block) + compressor.flush(zlib.Z_FULL_FLUSH)
            self.blocks.append((block, encoded))
        return b''.join(encoded for _, encoded in self.blocks)

    def flush(self):
        # FULL_FLUSH resets the dictionary and ends each block on a byte
        # boundary. A final empty block closes their one normal DEFLATE stream.
        return zlib.compressobj(wbits=-zlib.MAX_WBITS).flush()


class DeflateCache:
    """Bounded by the current container's expanded payload size, per session."""

    def __init__(self):
        self.files = {}

    def retain(self, names):
        self.files = {name: blocks for name, blocks in self.files.items() if name in names}

    def write(self, archive, name, raw):
        if len(raw) < BLOCK_BYTES:
            self.files.pop(name, None)
            archive.writestr(name, raw)
            return
        compressor = _BlockCompressor(self.files.get(name, ()))
        with archive.open(name, 'w') as output:
            # Python 3.15's ZIP writer retains responsibility for headers, CRC,
            # sizes and finalization. Only its compression strategy changes.
            output._compressor = compressor
            output.write(raw)
        self.files[name] = compressor.blocks
