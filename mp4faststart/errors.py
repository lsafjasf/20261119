"""Errors raised by the MP4 parser, relayout writer, and verifier."""


class MP4Error(ValueError):
    """The input is not a supported or structurally valid MP4 file."""


class FragmentedMP4Error(MP4Error):
    """The input is a fragmented MP4 and cannot be fast-started by relayout."""


class VerificationError(MP4Error):
    """Rebuilt samples do not match the source samples."""
