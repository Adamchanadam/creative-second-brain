from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PREFERRED_INFOGRAPHIC_ASPECT = 0.8
MAX_IMAGE_EDGE = 3840
MIN_IMAGE_PIXELS = 655_360
MAX_IMAGE_PIXELS = 8_294_400
MAX_LONG_TO_SHORT_RATIO = 3.0


def is_png(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == PNG_MAGIC
    except OSError:
        return False


def png_has_alpha_or_transparency(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA"}:
        return True
    if image.mode == "P" and "transparency" in image.info:
        return True
    return "transparency" in image.info


def evaluate_footer_box(
    *,
    image_width: int,
    image_height: int,
    footer_box: tuple[int, int, int, int],
    min_height_ratio: float = 0.030,
    max_height_ratio: float = 0.045,
) -> list[dict[str, Any]]:
    x, y, width, height = footer_box
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, value: Any, expected: str, severity: str = "error") -> None:
        checks.append({"name": name, "passed": passed, "value": value, "expected": expected, "severity": severity})

    add("footer_inside_image", x >= 0 and y >= 0 and x + width <= image_width and y + height <= image_height, footer_box, "footer box must stay inside image")
    add(
        "footer_bottom_anchored",
        image_height - (y + height) <= 8,
        image_height - (y + height),
        "<= 8px from bottom",
        "warning",
    )
    add(
        "footer_full_width",
        x <= 8 and image_width - (x + width) <= 8,
        (x, width),
        "full-width footer with <= 8px side gap",
        "warning",
    )
    add(
        "footer_height_range",
        int(image_height * min_height_ratio) <= height <= int(image_height * max_height_ratio),
        height,
        f"{int(image_height * min_height_ratio)}px to {int(image_height * max_height_ratio)}px",
        "warning",
    )
    add(
        "footer_not_oversized",
        height <= int(image_height * 0.10),
        height,
        f"<= {int(image_height * 0.10)}px",
        "warning",
    )
    return checks


def dark_footer_pixel(rgb: tuple[int, int, int]) -> bool:
    red, green, blue = rgb
    return red < 70 and green < 95 and blue < 130


def source_line_pixel(rgb: tuple[int, int, int]) -> bool:
    """Detect marker-like ink in the bottom source line without requiring a dark bar."""

    red, green, blue = rgb
    return (
        red < 120 and green < 120 and blue < 120
    ) or (
        green < 115 and red < 130 and blue < 170
    ) or (
        red > 150 and green < 120 and blue < 90
    )


def detect_dark_footer_box(image: Image.Image, *, row_dark_ratio: float = 0.55) -> tuple[int, int, int, int] | None:
    rgb = image.convert("RGB")
    width, height = rgb.size
    search_start = max(0, int(height * 0.82))
    footer_start: int | None = None
    started = False
    for y in range(height - 1, search_start - 1, -1):
        dark_count = sum(1 for x in range(width) if dark_footer_pixel(rgb.getpixel((x, y))))
        is_dark_row = dark_count / max(width, 1) >= row_dark_ratio
        if is_dark_row:
            footer_start = y
            started = True
        elif started:
            break
    if footer_start is None or height - footer_start < 8:
        return None
    return (0, footer_start, width, height - footer_start)


def detect_handdrawn_source_line_box(
    image: Image.Image,
    *,
    min_row_ink_ratio: float = 0.012,
    max_gap: int = 10,
) -> tuple[int, int, int, int] | None:
    rgb = image.convert("RGB")
    width, height = rgb.size
    search_start = max(0, int(height * 0.84))
    ink_rows: list[int] = []
    ink_columns: list[int] = []
    for y in range(search_start, height):
        row_columns = [
            x for x in range(width)
            if source_line_pixel(rgb.getpixel((x, y)))
        ]
        if len(row_columns) / max(width, 1) >= min_row_ink_ratio:
            ink_rows.append(y)
            ink_columns.extend(row_columns)
    if not ink_rows or not ink_columns:
        return None

    groups: list[list[int]] = []
    current: list[int] = []
    previous: int | None = None
    for row in ink_rows:
        if previous is None or row - previous <= max_gap:
            current.append(row)
        else:
            groups.append(current)
            current = [row]
        previous = row
    if current:
        groups.append(current)
    bottom_group = max(groups, key=lambda rows: rows[-1])
    y_min = max(search_start, min(bottom_group) - 4)
    y_max = min(height - 1, max(bottom_group) + 4)
    if y_max - y_min + 1 < 8:
        return None
    x_min = max(0, min(ink_columns) - 8)
    x_max = min(width - 1, max(ink_columns) + 8)
    return (x_min, y_min, x_max - x_min + 1, y_max - y_min + 1)


def detect_footer_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    return detect_dark_footer_box(image) or detect_handdrawn_source_line_box(image)


def footer_box_has_pixel_evidence(image: Image.Image, footer_box: tuple[int, int, int, int]) -> bool:
    rgb = image.convert("RGB")
    x, y, width, height = footer_box
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > rgb.width or y + height > rgb.height:
        return False
    dark = sum(1 for row in range(y, y + height) for column in range(x, x + width) if dark_footer_pixel(rgb.getpixel((column, row))))
    if dark / (width * height) >= 0.55:
        return True
    ink = sum(1 for row in range(y, y + height) for column in range(x, x + width) if source_line_pixel(rgb.getpixel((column, row))))
    return ink / (width * height) >= 0.025


def inspect_png(path: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = [
        {"name": "file_exists", "passed": path.exists(), "value": str(path), "expected": "file exists"},
        {"name": "png_header", "passed": is_png(path), "value": str(path), "expected": "PNG/IHDR file"},
    ]
    if path.exists() and is_png(path):
        with Image.open(path) as image:
            checks.append({"name": "image_dimensions", "passed": image.width > 0 and image.height > 0, "value": [image.width, image.height], "expected": "positive dimensions"})
            has_alpha_or_transparency = png_has_alpha_or_transparency(image)
            checks.append({
                "name": "opaque_background",
                "passed": not has_alpha_or_transparency,
                "value": {"mode": image.mode, "has_alpha_or_transparency": has_alpha_or_transparency},
                "expected": "formal infographic PNG must have an opaque background with no alpha channel or transparent color",
                "severity": "error",
            })
            aspect = image.width / max(image.height, 1)
            total_pixels = image.width * image.height
            short_edge = max(min(image.width, image.height), 1)
            long_to_short_ratio = max(image.width, image.height) / short_edge
            checks.append({
                "name": "portrait_orientation",
                "passed": image.height > image.width,
                "value": {"width": image.width, "height": image.height, "aspect": round(aspect, 4)},
                "expected": "portrait is preferred but not required",
                "severity": "warning",
            })
            checks.append({
                "name": "gpt_image_2_size_constraints",
                "passed": (
                    image.width % 16 == 0
                    and image.height % 16 == 0
                    and max(image.width, image.height) <= MAX_IMAGE_EDGE
                    and MIN_IMAGE_PIXELS <= total_pixels <= MAX_IMAGE_PIXELS
                    and long_to_short_ratio <= MAX_LONG_TO_SHORT_RATIO
                ),
                "value": {
                    "width": image.width,
                    "height": image.height,
                    "total_pixels": total_pixels,
                    "long_to_short_ratio": round(long_to_short_ratio, 4),
                },
                "expected": (
                    "both edges are multiples of 16px; maximum edge <= 3840px; "
                    "long-to-short ratio <= 3:1; total pixels 655360 to 8294400"
                ),
                "severity": "error",
            })
            checks.append({
                "name": "preferred_product_master_ratio",
                "passed": abs(aspect - PREFERRED_INFOGRAPHIC_ASPECT) <= 0.02,
                "value": {"width": image.width, "height": image.height, "aspect": round(aspect, 4)},
                "expected": "4:5 is preferred but not required",
                "severity": "warning",
            })
    return checks


def result(
    status: str,
    *,
    image_path: Path,
    footer_box: tuple[int, int, int, int] | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    failed = [item for item in checks if not item["passed"] and item.get("severity", "error") == "error"]
    warnings = [item for item in checks if not item["passed"] and item.get("severity") == "warning"]
    retryable_visual_failures = {"footer_detected", "footer_inside_image", "opaque_background"}
    regeneration_required = any(
        str(item.get("name") or "") in retryable_visual_failures
        for item in failed
        if isinstance(item, dict)
    )
    return {
        "status": "ready" if not failed and status == "ready" else "needs_review",
        "image": str(image_path),
        "footer_box": list(footer_box) if footer_box else None,
        "checks": checks,
        "failed_checks": failed,
        "warning_checks": warnings,
        "regeneration_required": regeneration_required,
    }


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("box must be x,y,width,height")
    return parts[0], parts[1], parts[2], parts[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated infographic PNG footer safety.")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--footer-box", type=parse_box, help="Optional manual override for the generated footer box as x,y,width,height.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    image_path = args.image.resolve()
    if not image_path.exists() or not is_png(image_path):
        checks = inspect_png(image_path)
        data = result("needs_review", image_path=image_path, footer_box=None, checks=checks)
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else f"status: {data['status']}")
        return 1
    with Image.open(image_path) as image:
        checks = inspect_png(image_path)
        automatic_footer = detect_footer_box(image)
        manual_footer = args.footer_box
        if manual_footer is not None:
            checks.append({
                "name": "manual_footer_fallback_allowed",
                "passed": automatic_footer is None,
                "value": list(manual_footer),
                "expected": "manual footer box is allowed only after automatic detection fails",
                "severity": "warning",
            })
            checks.append({
                "name": "manual_footer_pixel_evidence",
                "passed": footer_box_has_pixel_evidence(image, manual_footer),
                "value": list(manual_footer),
                "expected": "manual footer box contains a bottom dark footer or hand-drawn source line",
                "severity": "error",
            })
        footer_box = automatic_footer or manual_footer
        checks.append({
            "name": "footer_detected",
            "passed": footer_box is not None,
            "value": list(footer_box) if footer_box else None,
            "expected": "bottom source/footer band is detectable",
            "severity": "error",
        })
        if footer_box is not None:
            checks.extend(
                evaluate_footer_box(
                    image_width=image.width,
                    image_height=image.height,
                    footer_box=footer_box,
                )
            )
    data = result(
        "ready",
        image_path=image_path,
        footer_box=footer_box,
        checks=checks,
    )

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"status: {data['status']}")
        for check in data["checks"]:
            marker = "ok" if check["passed"] else "fail"
            print(f"- {marker}: {check['name']} ({check['value']} / {check['expected']})")
    return 0 if data["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
