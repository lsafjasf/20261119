"""Command line interface for the audio-project three-way merger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .normalize import ProjectFormatError, canonical_json, load_project
from .session import Session, SessionError, UnresolvedConflictsError


def _write(path: str | None, text: str) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _load_session(args: argparse.Namespace) -> Session:
    return Session(
        load_project(args.base),
        load_project(args.left),
        load_project(args.right),
    )


def _print_conflicts(session: Session) -> None:
    if not session.conflicts:
        print("No conflicts: all edits merged automatically.")
        return
    print(f"{len(session.conflicts)} conflict(s):")
    for conflict in session.conflicts:
        status = conflict.resolution or "UNRESOLVED"
        print(f"  [{conflict.id}] {status:10s} {conflict.kind:16s} "
              f"{conflict.label}")


def cmd_merge(args: argparse.Namespace) -> int:
    session = _load_session(args)
    _print_conflicts(session)
    _write(args.output, canonical_json(session.snapshot()))
    if args.log:
        _write(args.log, canonical_json(session.result.log))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    session = _load_session(args)
    try:
        for assignment in args.resolutions:
            conflict_id, choice = assignment.split("=", 1)
            session.resolve(conflict_id.strip(), choice.strip())
    except (SessionError, KeyError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    _print_conflicts(session)
    _write(args.output, canonical_json(session.snapshot()))
    if args.log:
        _write(args.log, canonical_json(session.result.log))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    session = _load_session(args)
    resolutions: dict[str, str] = {}
    if args.resolutions:
        for assignment in args.resolutions:
            conflict_id, choice = assignment.split("=", 1)
            resolutions[conflict_id.strip()] = choice.strip()
    try:
        for conflict_id, choice in resolutions.items():
            session.resolve(conflict_id, choice)
        project = session.export()
    except UnresolvedConflictsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        _print_conflicts(session)
        return 2
    except SessionError as exc:
        raise SystemExit(f"error: {exc}") from exc
    _write(args.output, canonical_json(project))
    if args.log:
        _write(args.log, canonical_json(session.result.log))
    print("Exported normalized playable project.")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web import serve

    serve(
        base=load_project(args.base),
        left=load_project(args.left),
        right=load_project(args.right),
        host=args.host,
        port=args.port,
        output_dir=Path(args.output_dir),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio-merge",
        description="Three-way merger for simplified audio projects.",
    )
    parser.add_argument("--version", action="version",
                        version=f"audio-merge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_project_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("base", help="common ancestor project JSON")
        p.add_argument("left", help="left branch project JSON")
        p.add_argument("right", help="right branch project JSON")
        p.add_argument("-o", "--output", help="write JSON result here")
        p.add_argument("--log", help="write the merge log JSON here")

    merge_p = sub.add_parser("merge", help="merge and report conflicts")
    add_project_args(merge_p)
    merge_p.set_defaults(func=cmd_merge)

    resolve_p = sub.add_parser("resolve", help="apply C1=left style resolutions")
    add_project_args(resolve_p)
    resolve_p.add_argument("resolutions", nargs="+",
                           help="conflict assignments like C1=right C2=delete")
    resolve_p.set_defaults(func=cmd_resolve)

    export_p = sub.add_parser("export", help="export a playable project")
    add_project_args(export_p)
    export_p.add_argument("resolutions", nargs="*",
                          help="optional C1=left style resolutions")
    export_p.set_defaults(func=cmd_export)

    web_p = sub.add_parser("web", help="open the interactive conflict UI")
    add_project_args(web_p)
    web_p.add_argument("--host", default="127.0.0.1")
    web_p.add_argument("--port", type=int, default=8765)
    web_p.add_argument("--output-dir", default="artifacts")
    web_p.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ProjectFormatError, FileNotFoundError) as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
