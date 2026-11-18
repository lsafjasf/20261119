"""Interactive-style demo of slip trimming.

Simulates a user dragging the in point of a clip that contains internal
markers and keyframes. After every interaction it prints the live source
window, the unchanged target interval and the remapped display times, then
undoes everything to prove both endpoints and all mappings are restored.
"""

from timeline import (
    Clip,
    History,
    Keyframe,
    Marker,
    MediaSource,
    Project,
    SlipTrimCommand,
    format_time,
)


def print_view(project: Project, clip_id: str, note: str) -> None:
    view = project.clip_view(clip_id)
    src_in, src_out = view["source_interval"]
    tgt_in, tgt_out = view["target_interval"]
    print(f"\n== {note} ==")
    print(
        f"source window [{format_time(src_in)} -> {format_time(src_out)}]  "
        f"target interval [{format_time(tgt_in)} -> {format_time(tgt_out)}]  "
        f"duration {view['duration']:.3f}s"
    )
    for marker in view["markers"]:
        print(
            f"  marker {marker['label']!r:<8} src={format_time(marker['source_time'])}"
            f" -> display={format_time(marker['display_time'])}"
        )
    for keyframe in view["keyframes"]:
        print(
            f"  keyframe v={keyframe['value']:<4} src={format_time(keyframe['source_time'])}"
            f" -> display={format_time(keyframe['display_time'])}"
        )


def main() -> None:
    source = MediaSource("interview.mp4", length=20.0)
    clip = Clip(
        clip_id="A",
        source=source,
        target_start=30.0,
        source_in=4.0,
        source_out=10.0,
        markers=[Marker(5.0, "hit"), Marker(8.0, "beat"), Marker(16.0, "tail")],
        keyframes=[Keyframe(6.0, 0.25), Keyframe(9.0, 0.75)],
    )
    project = Project("demo", [clip])
    history = History()
    print_view(project, "A", "initial state")

    for requested_in, note in (
        (6.5, "drag in point to 00:00:06.500 (out follows)"),
        (1.0, "drag back toward the head"),
        (-5.0, "attempt to drag past the head (must be blocked at 0)"),
        (18.0, "attempt to drag past the tail (must be blocked at 14)"),
    ):
        command = SlipTrimCommand.drag_in_point(clip, requested_in)
        result = history.execute(command)
        suffix = " [BLOCKED at media boundary]" if result.blocked else ""
        print_view(project, "A", note + suffix)

    while history.can_undo:
        history.undo()
        print_view(project, "A", "undo step")


if __name__ == "__main__":
    main()
