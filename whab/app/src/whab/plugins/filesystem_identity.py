"""Stable filesystem identity for private native journals on macOS.

Darwin's device number identifies the current mount, not a durable volume. Query
the UUID from the already opened descriptor; never resolve another pathname or
silently substitute a weaker identity when the filesystem cannot provide one.
Other platforms retain the existing device/inode journal scheme.
"""

import ctypes
import sys


class _Attributes(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    ]


def filesystem_identity(descriptor):
    """Return the durable volume identity, or None for the legacy platform path."""
    if sys.platform != "darwin":
        return None
    # sys/attr.h: ATTR_VOL_INFO is required with ATTR_VOL_UUID. The return buffer
    # is a uint32 length followed by uuid_t (16 bytes), with four-byte alignment.
    attributes = _Attributes(5, 0, 0, 0x80040000, 0, 0, 0)
    function = ctypes.CDLL(None, use_errno=True).fgetattrlist
    function.argtypes = [ctypes.c_int, ctypes.POINTER(_Attributes), ctypes.c_void_p,
                         ctypes.c_size_t, ctypes.c_ulong]
    function.restype = ctypes.c_int
    result = ctypes.create_string_buffer(20)
    if function(descriptor, ctypes.byref(attributes), result, len(result), 0) != 0:
        raise OSError(ctypes.get_errno(), "The filesystem identity is unavailable.")
    if int.from_bytes(result.raw[:4], sys.byteorder) != 20 or not any(result.raw[4:]):
        raise OSError("The filesystem did not return a durable volume identity.")
    return "darwin-volume-uuid:" + result.raw[4:].hex()
