#!/usr/bin/env python3
"""Generate the deterministic BINGIE preview-card anchor animations.

Kodi cannot position the preview card directly from a window-property string.
The fork therefore publishes an integer ``previewanchor`` in the inclusive
range 0..100 and lets the parameterized presentation-slot template select one
of these zero-duration animations. Keeping the arithmetic here makes the
generated XML reviewable and prevents the card, target marker, and timeline
geometry from acquiring independent hand-rounded positions.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Sequence


ANCHOR_MIN = 0
ANCHOR_MAX = 100
TIMELINE_WIDTH = 1152
PREVIEW_DESCRIPTION = "HTPC cursor-following preview"
SLOT_DESCRIPTION = "HTPC playback presentation slot $PARAM[slot]"
GENERATED_BEGIN = "\t\t\t\t<!-- BEGIN GENERATED PREVIEW ANCHORS -->\n"
GENERATED_END = "\t\t\t\t<!-- END GENERATED PREVIEW ANCHORS -->"

_SLIDE_END = re.compile(r"^([0-9]+),0$")


def anchor_property() -> str:
    return (
        "Window($PARAM[property_window]).Property("
        "$PARAM[property_prefix].$PARAM[slot].previewanchor)"
    )


def _anchor_condition() -> re.Pattern[str]:
    return re.compile(
        r"^String\.IsEqual\("
        + re.escape(anchor_property())
        + r",([0-9]+)\)$"
    )


def anchor_offset(anchor: int, width: int = TIMELINE_WIDTH) -> int:
    """Return the nearest pixel, using deterministic round-half-up."""
    if not ANCHOR_MIN <= anchor <= ANCHOR_MAX:
        raise ValueError("anchor must be between 0 and 100")
    if width <= 0:
        raise ValueError("timeline width must be positive")
    return (width * anchor + 50) // 100


def anchor_rows(width: int = TIMELINE_WIDTH) -> tuple[tuple[int, int], ...]:
    """Return every anchor and offset in canonical order."""
    return tuple(
        (anchor, anchor_offset(anchor, width))
        for anchor in range(ANCHOR_MIN, ANCHOR_MAX + 1)
    )


def render_animations(
    width: int = TIMELINE_WIDTH,
    indent: str = "\t\t\t\t",
) -> str:
    """Render the canonical XML fragment, including a final newline."""
    property_name = anchor_property()
    return "".join(
        (
            f'{indent}<animation effect="slide" end="{offset},0" time="0" '
            f'condition="String.IsEqual({property_name},{anchor})">'
            "Conditional</animation>\n"
        )
        for anchor, offset in anchor_rows(width)
    )


def _slot_group(root: ET.Element) -> ET.Element:
    matches = []
    for control in root.iter("control"):
        candidate = control.findtext("description", default="").strip()
        if candidate == SLOT_DESCRIPTION:
            matches.append(control)
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one control described as "
            f"{SLOT_DESCRIPTION!r}, found {len(matches)}"
        )
    return matches[0]


def _preview_control(root: ET.Element) -> ET.Element:
    matches = []
    for control in _slot_group(root).iter("control"):
        description = control.findtext("description", default="").strip()
        if description == PREVIEW_DESCRIPTION:
            matches.append(control)
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one control described as "
            f"{PREVIEW_DESCRIPTION!r}, found {len(matches)}"
        )
    return matches[0]


def extract_anchor_rows(
    xml_path: Path,
) -> tuple[tuple[int, int], ...]:
    """Extract canonical ``(anchor, offset)`` rows from the preview control."""
    root = ET.parse(xml_path).getroot()
    rows = []
    condition_pattern = _anchor_condition()
    for animation in _preview_control(root).findall("animation"):
        condition = animation.get("condition", "").strip()
        condition_match = condition_pattern.fullmatch(condition)
        if condition_match is None:
            continue
        if animation.get("effect") != "slide" or animation.get("time") != "0":
            raise ValueError(
                f"anchor animation has non-canonical attributes: {condition}"
            )
        end_match = _SLIDE_END.fullmatch(animation.get("end", "").strip())
        if end_match is None:
            raise ValueError(f"anchor animation has invalid end: {condition}")
        rows.append((int(condition_match.group(1)), int(end_match.group(1))))
    return tuple(rows)


def _generated_section_bounds(source: str) -> tuple[int, int]:
    """Return the generated content bounds after validating its markers."""
    begin_count = source.count(GENERATED_BEGIN)
    end_count = source.count(GENERATED_END)
    if begin_count != 1 or end_count != 1:
        raise ValueError(
            "expected exactly one generated preview-anchor section, "
            f"found {begin_count} begin and {end_count} end markers"
        )
    content_start = source.index(GENERATED_BEGIN) + len(GENERATED_BEGIN)
    content_end = source.index(GENERATED_END)
    if content_end < content_start:
        raise ValueError("generated preview-anchor markers are out of order")
    return content_start, content_end


def check_file(
    xml_path: Path,
    width: int = TIMELINE_WIDTH,
) -> Sequence[str]:
    """Return human-readable contract violations for a generated source file."""
    try:
        _generated_section_bounds(xml_path.read_text(encoding="utf-8"))
        actual = extract_anchor_rows(xml_path)
    except (ET.ParseError, OSError, ValueError) as error:
        return (str(error),)
    expected = anchor_rows(width)
    if actual == expected:
        return ()

    errors = [
        "preview anchors differ: "
        f"expected {len(expected)}, found {len(actual)}"
    ]
    actual_by_anchor = dict(actual)
    for anchor, expected_offset in expected:
        actual_offset = actual_by_anchor.get(anchor)
        if actual_offset != expected_offset:
            errors.append(
                f"anchor {anchor}: expected {expected_offset}, "
                f"found {actual_offset}"
            )
            if len(errors) == 12:
                errors.append("additional differences omitted")
                break
    return tuple(errors)


def update_file(
    xml_path: Path,
    width: int = TIMELINE_WIDTH,
) -> None:
    """Replace the one explicitly marked generated section in-place."""
    source = xml_path.read_text(encoding="utf-8")
    content_start, content_end = _generated_section_bounds(source)
    source = (
        source[:content_start]
        + render_animations(width)
        + source[content_end:]
    )
    xml_path.write_text(source, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check",
        metavar="XML",
        type=Path,
        help="validate the generated anchors embedded in an XML file",
    )
    action.add_argument(
        "--update",
        metavar="XML",
        type=Path,
        help="replace marked generated sections in an XML file",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=TIMELINE_WIDTH,
        help=f"timeline width in skin pixels (default: {TIMELINE_WIDTH})",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.check is None and args.update is None:
        sys.stdout.write(render_animations(args.width))
        return 0
    if args.update is not None:
        try:
            update_file(args.update, args.width)
        except (OSError, ValueError) as error:
            print(error, file=sys.stderr)
            return 1
        return 0
    errors = check_file(args.check, args.width)
    if not errors:
        return 0
    for error in errors:
        print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
