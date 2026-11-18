"""Timeline model: sources, clips, automation points and crossfades.

Design rules required by the editor:

* Every structural edit (add / move / trim / delete a clip, or changing a
  fade mode) bumps ``Project.revision`` and invalidates the crossfade cache,
  so gain curves are always recomputed from the current geometry.  The UI
  curve provider and the WAV exporter read the *same* cached arrays.
* Automation points reference a clip by id.  Deleting the referenced clip
  cascade-deletes the points, so no dangling automation survives a delete.
* Overlaps are only considered between adjacent clips on the same track and
  produce one :class:`Crossfade` whose cached per-sample gains come from
  :mod:`audio_editor.fade`.
"""

from .fade import LINEAR, fade_gains


class Source:
    """An audio source: mono float samples plus a sample rate."""

    def __init__(self, name, samples, sample_rate):
        self.name = name
        self.samples = list(samples)
        self.sample_rate = sample_rate

    def __len__(self):
        return len(self.samples)


class Clip:
    """A region of a :class:`Source` placed on a timeline track.

    ``start``  - first timeline sample occupied by the clip.
    ``offset`` - first source sample used by the clip.
    ``duration`` - clip length in timeline samples.
    """

    def __init__(self, clip_id, source, start, offset=0, duration=None,
                 track=0, gain=1.0):
        self.id = clip_id
        self.source = source
        if offset < 0 or offset > len(source.samples):
            raise ValueError("clip offset outside source bounds")
        max_duration = len(source.samples) - offset
        if duration is None:
            duration = max_duration
        if duration < 0 or duration > max_duration:
            raise ValueError("clip duration outside source bounds")
        self.start = int(start)
        self.offset = int(offset)
        self.duration = int(duration)
        self.track = track
        self.gain = float(gain)

    @property
    def end(self):
        """Exclusive timeline end sample."""
        return self.start + self.duration

    def source_sample_at(self, timeline_sample):
        """Source sample index played at a timeline sample, or ``None``."""
        if timeline_sample < self.start or timeline_sample >= self.end:
            return None
        return self.offset + (timeline_sample - self.start)


class AutomationPoint:
    """A gain automation point on the timeline.

    Points are owned by a clip (``clip_id``) and deleted with it.  Their
    ``position`` is an absolute timeline sample so moving a clip does not
    silently move its automation; the editor decides re-anchoring explicitly.
    """

    def __init__(self, point_id, clip_id, position, value):
        self.id = point_id
        self.clip_id = clip_id
        self.position = int(position)
        self.value = float(value)


class Crossfade:
    """Overlap between the outgoing clip ``a`` and incoming clip ``b``."""

    def __init__(self, clip_a, clip_b, start, length, mode):
        self.clip_a_id = clip_a.id
        self.clip_b_id = clip_b.id
        self.start = int(start)
        self.length = int(length)
        self.mode = mode
        out_gains, in_gains = fade_gains(mode, self.length)
        self.out_gains = out_gains
        self.in_gains = in_gains

    @property
    def end(self):
        """Exclusive timeline end sample of the overlap."""
        return self.start + self.length

    def gain_for(self, clip_id, timeline_sample):
        """Fade gain of ``clip_id`` at a timeline sample, or ``None``."""
        if timeline_sample < self.start or timeline_sample >= self.end:
            return None
        index = timeline_sample - self.start
        if clip_id == self.clip_a_id:
            return self.out_gains[index]
        if clip_id == self.clip_b_id:
            return self.in_gains[index]
        return None

    def geometry_signature(self):
        """Identity of the geometry this cache was built from."""
        return (self.clip_a_id, self.clip_b_id, self.start,
                self.length, self.mode)


class Project:
    """A single-track-group timeline holding clips and automation."""

    def __init__(self, sample_rate, default_fade_mode=LINEAR):
        self.sample_rate = sample_rate
        self.default_fade_mode = default_fade_mode
        self.clips = {}
        self.automation = []
        self.crossfades = []
        self.revision = 0
        self._xfade_revision = -1

    def _bump(self):
        self.revision += 1

    # ------------------------------------------------------------------ clips
    def add_clip(self, clip):
        if clip.id in self.clips:
            raise ValueError(f"duplicate clip id: {clip.id}")
        self.clips[clip.id] = clip
        self._bump()
        return clip

    def move_clip(self, clip_id, new_start):
        """Move a clip to ``new_start``; crossfades recompute lazily."""
        clip = self._require_clip(clip_id)
        clip.start = int(new_start)
        self._bump()
        return clip

    def trim_clip(self, clip_id, new_duration):
        """Change a clip duration (trim from the right)."""
        clip = self._require_clip(clip_id)
        max_duration = len(clip.source.samples) - clip.offset
        if new_duration < 0 or new_duration > max_duration:
            raise ValueError("trimmed duration outside source bounds")
        clip.duration = int(new_duration)
        self._bump()
        return clip

    def set_fade_mode(self, clip_a_id, clip_b_id, mode):
        """Override the fade mode used by one adjacent overlap pair."""
        self._ensure_crossfades()
        for xfade in self.crossfades:
            pair = {xfade.clip_a_id, xfade.clip_b_id}
            if pair == {clip_a_id, clip_b_id}:
                rebuilt = Crossfade(
                    self.clips[xfade.clip_a_id],
                    self.clips[xfade.clip_b_id],
                    xfade.start, xfade.length, mode)
                self.crossfades = [
                    rebuilt if x is xfade else x for x in self.crossfades]
                self._bump()
                return rebuilt
        raise ValueError("no overlap between the given clips")

    def delete_clip(self, clip_id):
        """Delete a clip and every automation point referencing it.

        Crossfades touching the clip are dropped and the remaining
        adjacencies are recomputed on the next query.
        """
        self._require_clip(clip_id)
        del self.clips[clip_id]
        removed_points = [p for p in self.automation
                          if p.clip_id == clip_id]
        self.automation = [p for p in self.automation
                           if p.clip_id != clip_id]
        self.crossfades = [
            x for x in self.crossfades
            if clip_id not in (x.clip_a_id, x.clip_b_id)]
        self._bump()
        return removed_points

    def _require_clip(self, clip_id):
        try:
            return self.clips[clip_id]
        except KeyError:
            raise KeyError(f"unknown clip id: {clip_id}") from None

    # ------------------------------------------------------------- automation
    def add_automation(self, point):
        if point.clip_id not in self.clips:
            raise ValueError(
                f"automation references missing clip: {point.clip_id}")
        self.automation.append(point)
        self.automation.sort(key=lambda p: p.position)
        self._bump()
        return point

    def automation_for_clip(self, clip_id):
        return [p for p in self.automation if p.clip_id == clip_id]

    def dangling_automation(self):
        """Automation points whose clip no longer exists (always empty)."""
        return [p for p in self.automation
                if p.clip_id not in self.clips]

    # --------------------------------------------------------------- crossfade
    def _ensure_crossfades(self):
        if self._xfade_revision == self.revision:
            return
        self.crossfades = self._compute_crossfades()
        self._xfade_revision = self.revision

    def _compute_crossfades(self):
        ordered = sorted(self.clips.values(),
                         key=lambda c: (c.track, c.start, c.end))
        result = []
        for index, clip_a in enumerate(ordered[:-1]):
            clip_b = ordered[index + 1]
            if clip_a.track != clip_b.track:
                continue
            overlap_start = max(clip_a.start, clip_b.start)
            overlap_end = min(clip_a.end, clip_b.end)
            if overlap_end <= overlap_start:
                continue
            mode = self._mode_for(clip_a.id, clip_b.id)
            result.append(Crossfade(
                clip_a, clip_b, overlap_start,
                overlap_end - overlap_start, mode))
        return result

    def _mode_for(self, clip_a_id, clip_b_id):
        for xfade in self.crossfades:
            if {xfade.clip_a_id, xfade.clip_b_id} == {clip_a_id, clip_b_id}:
                return xfade.mode
        return self.default_fade_mode

    def get_crossfades(self):
        """Return current crossfades, recomputing cached curves if needed."""
        self._ensure_crossfades()
        return list(self.crossfades)

    def fade_gain_at(self, clip_id, timeline_sample):
        """Crossfade gain applied to a clip at one timeline sample.

        Returns 1.0 outside any overlap, 0.0 where the clip is fully faded,
        and the exact cached curve value inside an overlap.  This is the
        single source of truth shared by the UI curve display and rendering.
        """
        self._ensure_crossfades()
        for xfade in self.crossfades:
            gain = xfade.gain_for(clip_id, timeline_sample)
            if gain is not None:
                return gain
        return 1.0

    # ---------------------------------------------------------------- envelope
    def clip_gain_at(self, clip_id, timeline_sample):
        """Total gain of one clip at one timeline sample.

        Single source of truth used by the UI envelope display, the
        audition renderer and the offline WAV exporter.
        """
        clip = self._require_clip(clip_id)
        fade_gain = self.fade_gain_at(clip_id, timeline_sample)
        auto_gain = self._automation_value_at(
            self.automation_for_clip(clip_id), timeline_sample)
        return clip.gain * fade_gain * auto_gain

    def clip_envelope(self, clip_id):
        """Per-sample ``(timeline_sample, total_gain)`` pairs for one clip.

        Total gain = clip static gain * crossfade curve gain * automation.
        The renderer applies this identical function, guaranteeing the
        on-screen curve and the exported WAV envelope match sample for
        sample.
        """
        clip = self._require_clip(clip_id)
        envelope = []
        for timeline_sample in range(clip.start, clip.end):
            envelope.append(
                (timeline_sample,
                 self.clip_gain_at(clip_id, timeline_sample)))
        return envelope

    @staticmethod
    def _automation_value_at(points, position):
        """Piecewise-linear interpolation of automation points."""
        if not points:
            return 1.0
        if position <= points[0].position:
            return points[0].value
        if position >= points[-1].position:
            return points[-1].value
        for left, right in zip(points, points[1:]):
            if left.position <= position <= right.position:
                span = right.position - left.position
                if span == 0:
                    return right.value
                frac = (position - left.position) / span
                return left.value + (right.value - left.value) * frac
        return 1.0
