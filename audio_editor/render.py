"""Sample-accurate rendering shared by audition and WAV export.

``render_range`` is the only mixing routine in the package.  ``audition``
returns its float output in memory for preview playback; ``export_wav``
runs the same function and only then quantizes to int16 PCM.  Both paths
therefore place every crossfade seam at the identical timeline sample and
apply the identical per-sample gain curve.
"""

from .wavfile import write_wav


def render_range(project, start, end):
    """Render timeline samples ``[start, end)`` and return float samples."""
    if end < start:
        raise ValueError("render end precedes start")
    active = [clip for clip in project.clips.values()
              if clip.end > start and clip.start < end]
    length = end - start
    mix = [0.0] * length
    for clip in active:
        source = clip.source.samples
        first = max(start, clip.start)
        last = min(end, clip.end)
        for timeline_sample in range(first, last):
            source_index = clip.source_sample_at(timeline_sample)
            gain = project.clip_gain_at(clip.id, timeline_sample)
            mix[timeline_sample - start] += source[source_index] * gain
    return mix


def render_full(project):
    """Render every sample covered by the timeline's clips."""
    if not project.clips:
        return []
    start = min(clip.start for clip in project.clips.values())
    end = max(clip.end for clip in project.clips.values())
    return render_range(project, start, end)


def audition(project, start=None, end=None):
    """In-memory preview render: identical math to offline export."""
    if start is None or end is None:
        return render_full(project)
    return render_range(project, start, end)


def export_wav(project, path, start=None, end=None):
    """Export a range (default: full timeline) to a 16-bit PCM WAV file.

    Returns ``(path, start, end, float_samples)`` so callers and tests can
    compare the pre-quantization mix against an audition render directly.
    """
    if start is None or end is None:
        if not project.clips:
            start, end = 0, 0
        else:
            start = min(c.start for c in project.clips.values())
            end = max(c.end for c in project.clips.values())
    samples = render_range(project, start, end)
    write_wav(path, samples, project.sample_rate)
    return path, start, end, samples
