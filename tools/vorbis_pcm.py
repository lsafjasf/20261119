#!/usr/bin/env python3
"""Thin ctypes binding to libvorbis (synthesis side only).

Used to recover the true per-packet granule position of a Vorbis packet
stream and to decode PCM independently of any Ogg container code.  Only
public, ABI-stable structs and functions are used.
"""

import ctypes
import ctypes.util


class OggPacket(ctypes.Structure):
    _fields_ = [
        ("packet", ctypes.POINTER(ctypes.c_ubyte)),
        ("bytes", ctypes.c_long),
        ("b_o_s", ctypes.c_long),
        ("e_o_s", ctypes.c_long),
        ("granulepos", ctypes.c_int64),
        ("packetno", ctypes.c_int64),
    ]


class VorbisInfo(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_int),
        ("channels", ctypes.c_int),
        ("rate", ctypes.c_long),
        ("bitrate_upper", ctypes.c_long),
        ("bitrate_nominal", ctypes.c_long),
        ("bitrate_lower", ctypes.c_long),
        ("bitrate_window", ctypes.c_long),
        ("codec_setup", ctypes.c_void_p),
    ]


class VorbisComment(ctypes.Structure):
    _fields_ = [
        ("user_comments", ctypes.POINTER(ctypes.c_char_p)),
        ("comment_lengths", ctypes.POINTER(ctypes.c_int)),
        ("comments", ctypes.c_int),
        ("vendor", ctypes.c_char_p),
    ]


class OggPackBuffer(ctypes.Structure):
    _fields_ = [
        ("endbyte", ctypes.c_long),
        ("endbit", ctypes.c_int),
        ("buffer", ctypes.POINTER(ctypes.c_ubyte)),
        ("ptr", ctypes.POINTER(ctypes.c_ubyte)),
        ("storage", ctypes.c_long),
    ]


class VorbisDspState(ctypes.Structure):
    _fields_ = [
        ("analysisp", ctypes.c_int),
        ("vi", ctypes.POINTER(VorbisInfo)),
        ("pcm", ctypes.POINTER(ctypes.POINTER(ctypes.c_float))),
        ("pcmret", ctypes.POINTER(ctypes.POINTER(ctypes.c_float))),
        ("pcm_storage", ctypes.c_int),
        ("pcm_current", ctypes.c_int),
        ("pcm_returned", ctypes.c_int),
        ("preextrapolate", ctypes.c_int),
        ("eofflag", ctypes.c_int),
        ("lW", ctypes.c_long),
        ("W", ctypes.c_long),
        ("nW", ctypes.c_long),
        ("centerW", ctypes.c_long),
        ("granulepos", ctypes.c_int64),
        ("sequence", ctypes.c_int64),
        ("glue_bits", ctypes.c_int64),
        ("time_bits", ctypes.c_int64),
        ("floor_bits", ctypes.c_int64),
        ("res_bits", ctypes.c_int64),
        ("backend_state", ctypes.c_void_p),
    ]


class VorbisBlock(ctypes.Structure):
    _fields_ = [
        ("pcm", ctypes.POINTER(ctypes.POINTER(ctypes.c_float))),
        ("opb", OggPackBuffer),
        ("lW", ctypes.c_long),
        ("W", ctypes.c_long),
        ("nW", ctypes.c_long),
        ("pcmend", ctypes.c_int),
        ("mode", ctypes.c_int),
        ("eofflag", ctypes.c_int),
        ("granulepos", ctypes.c_int64),
        ("sequence", ctypes.c_int64),
        ("vd", ctypes.POINTER(VorbisDspState)),
        ("localstore", ctypes.c_void_p),
        ("localtop", ctypes.c_long),
        ("localalloc", ctypes.c_long),
        ("totaluse", ctypes.c_long),
        ("reap", ctypes.c_void_p),
        ("glue_bits", ctypes.c_long),
        ("time_bits", ctypes.c_long),
        ("floor_bits", ctypes.c_long),
        ("res_bits", ctypes.c_long),
        ("internal", ctypes.c_void_p),
    ]


def _load():
    path = ctypes.util.find_library("vorbis")
    if not path:
        raise RuntimeError("libvorbis not found")
    lib = ctypes.CDLL(path)
    lib.vorbis_synthesis_pcmout.restype = ctypes.c_int
    lib.vorbis_synthesis_pcmout.argtypes = [
        ctypes.POINTER(VorbisDspState),
        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(ctypes.c_float))),
    ]
    return lib


def _make_ogg_packet(data, bos=0, eos=0, granule=-1, packetno=0):
    buf = (ctypes.c_ubyte * max(1, len(data))).from_buffer_copy(
        data if data else b"\x00")
    pkt = OggPacket()
    pkt.packet = buf
    pkt.bytes = len(data)
    pkt.b_o_s = bos
    pkt.e_o_s = eos
    pkt.granulepos = granule
    pkt.packetno = packetno
    return pkt, buf  # keep buf alive


def decode_vorbis_packets(packet_datas):
    """Decode a Vorbis packet stream with libvorbis.

    *packet_datas* is the ordered list of packet payloads (headers first).
    Returns (rate, channels, granules, pcm) where *granules[i]* is the
    cumulative number of decodable samples after packet *i* (None for
    header packets) and *pcm* is a list of per-channel sample lists.
    """
    lib = _load()
    vi = VorbisInfo()
    vc = VorbisComment()
    lib.vorbis_info_init(ctypes.byref(vi))
    lib.vorbis_comment_init(ctypes.byref(vc))
    keepalive = []
    try:
        for idx in range(3):
            pkt, buf = _make_ogg_packet(packet_datas[idx],
                                        bos=1 if idx == 0 else 0,
                                        packetno=idx)
            keepalive.append(buf)
            ret = lib.vorbis_synthesis_headerin(
                ctypes.byref(vi), ctypes.byref(vc), ctypes.byref(pkt))
            if ret != 0:
                raise RuntimeError(f"vorbis_synthesis_headerin failed "
                                   f"(packet {idx}, ret={ret})")
        vd = VorbisDspState()
        vb = VorbisBlock()
        if lib.vorbis_synthesis_init(ctypes.byref(vd), ctypes.byref(vi)) != 0:
            raise RuntimeError("vorbis_synthesis_init failed")
        if lib.vorbis_block_init(ctypes.byref(vd), ctypes.byref(vb)) != 0:
            raise RuntimeError("vorbis_block_init failed")
        channels = vi.channels
        pcm = [[] for _ in range(channels)]
        granules = [None, None, None]
        total = 0
        pcm_ptr = ctypes.POINTER(ctypes.POINTER(ctypes.c_float))()
        for idx in range(3, len(packet_datas)):
            pkt, buf = _make_ogg_packet(packet_datas[idx], packetno=idx)
            keepalive.append(buf)
            if lib.vorbis_synthesis(ctypes.byref(vb),
                                    ctypes.byref(pkt)) != 0:
                raise RuntimeError(f"vorbis_synthesis failed on "
                                   f"packet {idx}")
            if lib.vorbis_synthesis_blockin(ctypes.byref(vd),
                                            ctypes.byref(vb)) != 0:
                raise RuntimeError(f"vorbis_synthesis_blockin failed on "
                                   f"packet {idx}")
            while True:
                n = lib.vorbis_synthesis_pcmout(ctypes.byref(vd),
                                                ctypes.byref(pcm_ptr))
                if n <= 0:
                    break
                for ch in range(channels):
                    arr = pcm_ptr[ch]
                    pcm[ch].extend(arr[i] for i in range(n))
                lib.vorbis_synthesis_read(ctypes.byref(vd), n)
                total += n
            granules.append(total)
        lib.vorbis_block_clear(ctypes.byref(vb))
        lib.vorbis_dsp_clear(ctypes.byref(vd))
        return vi.rate, channels, granules, pcm
    finally:
        lib.vorbis_info_clear(ctypes.byref(vi))
        lib.vorbis_comment_clear(ctypes.byref(vc))


def is_vorbis(packet_datas):
    return (len(packet_datas) >= 3
            and packet_datas[0].startswith(b"\x01vorbis")
            and packet_datas[1].startswith(b"\x03vorbis")
            and packet_datas[2].startswith(b"\x05vorbis"))
