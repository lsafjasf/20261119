"""Partitioned fast convolution for long FIR filters (pure-stdlib Python).

Modules:
    fft      - radix-2 FFT / inverse FFT / complex helpers
    direct   - O(n*m) time-domain reference convolution
    ols      - uniform and non-uniform (short-front + long-tail) overlap-save
    crossfade - dual-instance crossfaded filter switching
    metrics  - MAC and memory accounting
"""
