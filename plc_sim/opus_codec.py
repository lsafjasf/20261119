"""Minimal ctypes wrapper around libopus for real Opus encode/decode."""
import ctypes
import numpy as np

_lib = ctypes.CDLL("libopus.so.0")

OPUS_APPLICATION_VOIP = 2048
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_VBR_REQUEST = 4006
OPUS_SET_INBAND_FEC_REQUEST = 4012

_lib.opus_encoder_create.restype = ctypes.c_void_p
_lib.opus_encoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int)]
_lib.opus_encoder_ctl.restype = ctypes.c_int
_lib.opus_encode.restype = ctypes.c_int
_lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                             ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte),
                             ctypes.c_int32]
_lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
_lib.opus_decoder_create.restype = ctypes.c_void_p
_lib.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int)]
_lib.opus_decode.restype = ctypes.c_int
_lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte),
                             ctypes.c_int32, ctypes.POINTER(ctypes.c_int16),
                             ctypes.c_int, ctypes.c_int]
_lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]


class OpusEncoder:
    def __init__(self, fs=16000, bitrate=24000):
        err = ctypes.c_int(0)
        self._st = _lib.opus_encoder_create(fs, 1, OPUS_APPLICATION_VOIP,
                                            ctypes.byref(err))
        assert err.value == 0, f"encoder_create err {err.value}"
        _lib.opus_encoder_ctl(self._st, OPUS_SET_BITRATE_REQUEST, bitrate)
        _lib.opus_encoder_ctl(self._st, OPUS_SET_VBR_REQUEST, 0)
        self.fs = fs

    def encode(self, pcm_i16):
        """pcm_i16: int16 array of one frame. Returns bytes."""
        n = len(pcm_i16)
        src = pcm_i16.astype(np.int16)
        buf = (ctypes.c_ubyte * 4000)()
        nb = _lib.opus_encode(self._st,
                              src.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
                              n, buf, 4000)
        assert nb > 0, f"encode err {nb}"
        return bytes(bytearray(buf[:nb]))

    def close(self):
        _lib.opus_encoder_destroy(self._st)


class OpusDecoder:
    def __init__(self, fs=16000):
        err = ctypes.c_int(0)
        self._st = _lib.opus_decoder_create(fs, 1, ctypes.byref(err))
        assert err.value == 0, f"decoder_create err {err.value}"
        self.fs = fs

    def decode(self, packet, frame_samples):
        """packet: bytes or None (None -> opus PLC, keeps decoder state)."""
        out = np.zeros(frame_samples, dtype=np.int16)
        if packet is None:
            n = _lib.opus_decode(self._st, None, 0,
                                 out.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
                                 frame_samples, 0)
        else:
            buf = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
            n = _lib.opus_decode(self._st, buf, len(packet),
                                 out.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
                                 frame_samples, 0)
        assert n == frame_samples, f"decode err {n}"
        return out

    def close(self):
        _lib.opus_decoder_destroy(self._st)
