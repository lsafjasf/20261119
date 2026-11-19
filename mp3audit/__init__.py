"""MP3 frame, side-information, and bit-reservoir auditing."""

from .header import FrameHeader, HeaderError, parse_header
from .parser import AuditResult, Diagnostic, FrameRecord, audit_bytes, audit_file

__all__ = [
    "AuditResult",
    "Diagnostic",
    "FrameHeader",
    "FrameRecord",
    "HeaderError",
    "audit_bytes",
    "audit_file",
    "parse_header",
]
