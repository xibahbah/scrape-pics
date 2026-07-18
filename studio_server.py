#!/usr/bin/env python3
"""Local image library and swipe-review app."""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import requests
from PIL import Image, ImageFilter, ImageOps

from instagram_web_archive import (
    best_image,
    download_image,
    iter_photo_media,
    make_session,
    parse_username,
    profile_user,
    request_json,
    safe,
    taken_date,
)


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "app_data"
IMPORT_DIR = DATA_DIR / "imports"
THUMB_DIR = DATA_DIR / "thumbs"
LIBRARY_PATH = DATA_DIR / "library.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SHOOT_COLLECTIONS = ("makeup", "color", "lighting", "art_design", "pose", "reference")
ARCHIVE_NAME_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<code>[A-Za-z0-9_-]{5,})_(?P<index>\d{2})_(?P<size>\d+x\d+)",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def item_id_for(source_key: str) -> str:
    return hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:18]


def hex_color(rgb: Sequence[int]) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def luminance(rgb: Sequence[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def rgb_distance(first: Sequence[float], second: Sequence[float]) -> float:
    return (
        ((first[0] - second[0]) * 0.30) ** 2
        + ((first[1] - second[1]) * 0.59) ** 2
        + ((first[2] - second[2]) * 0.11) ** 2
    ) ** 0.5


def image_to_rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (22, 24, 27))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def extract_palette(image: Image.Image, colors: int = 5) -> List[str]:
    sample = image.copy()
    sample.thumbnail((220, 220))
    pixels = list(sample.getdata())
    if not pixels:
        return []

    grayscale = ImageOps.grayscale(sample)
    edges = list(grayscale.filter(ImageFilter.FIND_EDGES).getdata())
    average_luma = sum(luminance(pixel) for pixel in pixels) / len(pixels)
    total = len(pixels)
    buckets: Dict[Tuple[int, int, int], Dict[str, float]] = {}

    for pixel, edge in zip(pixels, edges):
        r, g, b = pixel
        key = (r // 16, g // 16, b // 16)
        h, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        luma = luminance(pixel)
        contrast = abs(luma - average_luma) / 255
        edge_weight = edge / 255
        red_focus = 1.0 if (h <= 0.045 or h >= 0.94) and saturation > 0.22 else 0.0
        warm_focus = 1.0 if 0.055 <= h <= 0.20 and saturation > 0.20 else 0.0
        magenta_focus = 1.0 if 0.78 <= h <= 0.93 and saturation > 0.18 else 0.0
        cool_focus = 1.0 if 0.52 <= h <= 0.68 and saturation > 0.18 else 0.0
        attention = (
            0.22
            + saturation * 1.35
            + contrast * 0.95
            + edge_weight * 0.55
            + value * 0.18
            + red_focus * 0.55
            + warm_focus * 0.35
            + magenta_focus * 0.25
            + cool_focus * 0.18
        )
        bucket = buckets.setdefault(
            key,
            {"count": 0.0, "r": 0.0, "g": 0.0, "b": 0.0, "attention": 0.0, "edge": 0.0},
        )
        bucket["count"] += 1
        bucket["r"] += r
        bucket["g"] += g
        bucket["b"] += b
        bucket["attention"] += attention
        bucket["edge"] += edge_weight

    candidates: List[Dict[str, Any]] = []
    min_count = max(8, total * 0.0007)
    for bucket in buckets.values():
        count = bucket["count"]
        if count < min_count:
            continue
        rgb = (bucket["r"] / count, bucket["g"] / count, bucket["b"] / count)
        h, saturation, value = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        contrast = abs(luminance(rgb) - average_luma) / 255
        edge_average = bucket["edge"] / count
        red_band = 1.0 if h <= 0.045 or h >= 0.94 else 0.0
        yellow_band = 1.0 if 0.055 <= h <= 0.20 else 0.0
        magenta_band = 1.0 if 0.78 <= h <= 0.93 else 0.0
        cyan_band = 1.0 if 0.50 <= h <= 0.70 else 0.0
        area = count / total
        presence_score = (area**0.38) * (bucket["attention"] / count) * (0.72 + saturation * 0.55)
        accent_focus = 1.0 + red_band * 1.05 + yellow_band * 0.55 + magenta_band * 0.35 + cyan_band * 0.25
        accent_score = (
            (area**0.16)
            * (saturation**1.35)
            * (0.62 + contrast * 1.35 + edge_average * 0.85 + value * 0.40)
            * accent_focus
        )
        score = max(presence_score, accent_score * 0.95)
        candidates.append({"rgb": rgb, "score": score, "area": area})

    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    selected: List[Tuple[float, float, float]] = []
    for threshold in (42, 32, 22, 12):
        for candidate in candidates:
            rgb = candidate["rgb"]
            if any(rgb_distance(rgb, existing) < threshold for existing in selected):
                continue
            selected.append(rgb)
            if len(selected) == colors:
                return [hex_color(rgb) for rgb in selected]

    return [hex_color(rgb) for rgb in selected[:colors]]


def extract_dominant_color(image: Image.Image) -> str:
    """Return the quantized color that covers the largest pixel area."""
    sample = image.copy()
    sample.thumbnail((96, 96))
    buckets: Dict[Tuple[int, int, int], Dict[str, float]] = {}
    for r, g, b in sample.getdata():
        key = (r // 16, g // 16, b // 16)
        bucket = buckets.setdefault(key, {"count": 0.0, "r": 0.0, "g": 0.0, "b": 0.0})
        bucket["count"] += 1
        bucket["r"] += r
        bucket["g"] += g
        bucket["b"] += b
    if not buckets:
        return ""
    bucket = max(buckets.values(), key=lambda entry: entry["count"])
    return hex_color((bucket["r"] / bucket["count"], bucket["g"] / bucket["count"], bucket["b"] / bucket["count"]))


def extract_tone_colors(image: Image.Image, colors: int = 3) -> Dict[str, List[str]]:
    sample = image.copy()
    sample.thumbnail((220, 220))
    pixels = list(sample.getdata())
    if not pixels:
        return {"highlights": [], "midtones": [], "shadows": []}

    grayscale = ImageOps.grayscale(sample)
    edges = list(grayscale.filter(ImageFilter.FIND_EDGES).getdata())
    lumas = [luminance(pixel) for pixel in pixels]
    sorted_lumas = sorted(lumas)
    shadow_cut = sorted_lumas[int((len(sorted_lumas) - 1) * 0.34)]
    highlight_cut = sorted_lumas[int((len(sorted_lumas) - 1) * 0.68)]

    def band_palette(low: float, high: float, include_low: bool, include_high: bool) -> List[str]:
        band_pixels: List[Tuple[int, int, int]] = []
        band_edges: List[int] = []
        for pixel, edge, luma in zip(pixels, edges, lumas):
            above_low = luma >= low if include_low else luma > low
            below_high = luma <= high if include_high else luma < high
            if above_low and below_high:
                band_pixels.append(pixel)
                band_edges.append(edge)
        return extract_salient_colors(band_pixels, band_edges, colors=colors)

    return {
        "highlights": band_palette(highlight_cut, 255, include_low=True, include_high=True),
        "midtones": band_palette(shadow_cut, highlight_cut, include_low=False, include_high=False),
        "shadows": band_palette(0, shadow_cut, include_low=True, include_high=True),
    }


def extract_salient_colors(
    pixels: Sequence[Tuple[int, int, int]],
    edges: Sequence[int],
    colors: int,
) -> List[str]:
    if not pixels:
        return []

    average_luma = sum(luminance(pixel) for pixel in pixels) / len(pixels)
    total = len(pixels)
    buckets: Dict[Tuple[int, int, int], Dict[str, float]] = {}
    for pixel, edge in zip(pixels, edges):
        r, g, b = pixel
        key = (r // 16, g // 16, b // 16)
        h, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        luma = luminance(pixel)
        contrast = abs(luma - average_luma) / 255
        edge_weight = edge / 255
        red_focus = 1.0 if (h <= 0.045 or h >= 0.94) and saturation > 0.22 else 0.0
        warm_focus = 1.0 if 0.055 <= h <= 0.20 and saturation > 0.20 else 0.0
        magenta_focus = 1.0 if 0.78 <= h <= 0.93 and saturation > 0.18 else 0.0
        cool_focus = 1.0 if 0.52 <= h <= 0.68 and saturation > 0.18 else 0.0
        attention = (
            0.22
            + saturation * 1.35
            + contrast * 0.95
            + edge_weight * 0.55
            + value * 0.18
            + red_focus * 0.55
            + warm_focus * 0.35
            + magenta_focus * 0.25
            + cool_focus * 0.18
        )
        bucket = buckets.setdefault(
            key,
            {"count": 0.0, "r": 0.0, "g": 0.0, "b": 0.0, "attention": 0.0, "edge": 0.0},
        )
        bucket["count"] += 1
        bucket["r"] += r
        bucket["g"] += g
        bucket["b"] += b
        bucket["attention"] += attention
        bucket["edge"] += edge_weight

    candidates: List[Dict[str, Any]] = []
    min_count = max(4, total * 0.001)
    for bucket in buckets.values():
        count = bucket["count"]
        if count < min_count:
            continue
        rgb = (bucket["r"] / count, bucket["g"] / count, bucket["b"] / count)
        h, saturation, value = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        contrast = abs(luminance(rgb) - average_luma) / 255
        edge_average = bucket["edge"] / count
        red_band = 1.0 if h <= 0.045 or h >= 0.94 else 0.0
        yellow_band = 1.0 if 0.055 <= h <= 0.20 else 0.0
        magenta_band = 1.0 if 0.78 <= h <= 0.93 else 0.0
        cyan_band = 1.0 if 0.50 <= h <= 0.70 else 0.0
        area = count / total
        presence_score = (area**0.38) * (bucket["attention"] / count) * (0.72 + saturation * 0.55)
        accent_focus = 1.0 + red_band * 1.05 + yellow_band * 0.55 + magenta_band * 0.35 + cyan_band * 0.25
        accent_score = (
            (area**0.16)
            * (saturation**1.35)
            * (0.62 + contrast * 1.35 + edge_average * 0.85 + value * 0.40)
            * accent_focus
        )
        candidates.append({"rgb": rgb, "score": max(presence_score, accent_score * 0.95)})

    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    selected: List[Tuple[float, float, float]] = []
    for threshold in (42, 32, 22, 12):
        for candidate in candidates:
            rgb = candidate["rgb"]
            if any(rgb_distance(rgb, existing) < threshold for existing in selected):
                continue
            selected.append(rgb)
            if len(selected) == colors:
                return [hex_color(rgb) for rgb in selected]
    return [hex_color(rgb) for rgb in selected[:colors]]


def extract_context_colors(image: Image.Image) -> Dict[str, List[str]]:
    """Suggest skin and background colors without treating either as a hard fact."""
    sample = image.copy()
    sample.thumbnail((260, 260))
    width, height = sample.size
    pixels = list(sample.getdata())
    if not pixels or not width or not height:
        return {"skin": [], "background": []}

    grayscale = ImageOps.grayscale(sample)
    edges = list(grayscale.filter(ImageFilter.FIND_EDGES).getdata())
    skin_pixels: List[Tuple[int, int, int]] = []
    skin_edges: List[int] = []
    background_pixels: List[Tuple[int, int, int]] = []
    background_edges: List[int] = []
    border_x = max(1, int(width * 0.12))
    border_y = max(1, int(height * 0.12))

    for index, (pixel, edge) in enumerate(zip(pixels, edges)):
        r, g, b = pixel
        x = index % width
        y = index // width
        maximum = max(pixel)
        minimum = min(pixel)
        hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

        # Broad RGB/HSV skin range: it deliberately returns a suggestion only,
        # so photographs with makeup, colored lighting, or no person are safe.
        is_skin = (
            r > 45
            and g > 25
            and b > 15
            and maximum - minimum > 15
            and abs(r - g) > 10
            and r >= g * 0.92
            and r >= b * 1.05
            and (hue <= 0.15 or hue >= 0.96)
            and 0.08 <= saturation <= 0.82
            and value >= 0.18
        )
        if is_skin:
            skin_pixels.append(pixel)
            skin_edges.append(edge)

        if x < border_x or x >= width - border_x or y < border_y or y >= height - border_y:
            background_pixels.append(pixel)
            background_edges.append(edge)

    minimum_skin_pixels = max(20, int(len(pixels) * 0.007))
    return {
        "skin": extract_salient_colors(skin_pixels, skin_edges, colors=2)
        if len(skin_pixels) >= minimum_skin_pixels
        else [],
        "background": extract_salient_colors(background_pixels, background_edges, colors=3),
    }


def make_thumbnail(image: Image.Image, item_id: str) -> Path:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMB_DIR / f"{item_id}.jpg"
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return thumb_path

    thumbnail = image.copy()
    thumbnail.thumbnail((720, 720))
    thumbnail.save(thumb_path, "JPEG", quality=86, optimize=True, progressive=True)
    return thumb_path


def analyze_file(path: Path, item_id: str) -> Dict[str, Any]:
    with Image.open(path) as opened:
        width, height = opened.size
        thumb_path = THUMB_DIR / f"{item_id}.jpg"
        if not thumb_path.exists() or not thumb_path.stat().st_size:
            full_image = image_to_rgb(opened)
            thumb_path = make_thumbnail(full_image, item_id)

    # Existing thumbnails are sufficient for color analysis and avoid repeatedly
    # decoding full-resolution originals when a library is refreshed.
    with Image.open(thumb_path) as opened_thumb:
        image = image_to_rgb(opened_thumb)
        image.thumbnail((280, 280))
        colors = extract_palette(image)
        dominant_color = extract_dominant_color(image)
        tone_colors = extract_tone_colors(image)
        context_colors = extract_context_colors(image)

    stat = path.stat()
    return {
        "width": width,
        "height": height,
        "bytes": stat.st_size,
        "type": path.suffix.lstrip(".").upper() or "IMAGE",
        "colors": colors,
        "dominant_color": dominant_color,
        "tone_colors": tone_colors,
        "context_colors": context_colors,
        "thumb_path": str(thumb_path),
        "date_modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def archive_metadata(path: Path, handle: str) -> Dict[str, Any]:
    match = ARCHIVE_NAME_RE.search(path.stem)
    if not match:
        source_key = f"file:{path.resolve()}"
        return {
            "source_key": source_key,
            "source_url": "",
            "post_code": "",
            "media_index": 1,
            "taken_date_utc": "",
        }

    code = match.group("code")
    media_index = int(match.group("index"))
    return {
        "source_key": f"instagram:{handle}:{code}:{media_index:02d}",
        "source_url": f"https://www.instagram.com/p/{code}/",
        "post_code": code,
        "media_index": media_index,
        "taken_date_utc": match.group("date"),
    }


class LibraryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data.get("items"), dict):
                    data.setdefault("version", 1)
                    data.setdefault("order", list(data["items"].keys()))
                    data.setdefault("shoots", {})
                    data.setdefault("current_shoot_id", "")
                    data.setdefault("imports", {})
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return {"version": 2, "items": {}, "order": [], "shoots": {}, "current_shoot_id": "", "imports": {}}

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    def source_index(self) -> Dict[str, str]:
        return {
            item.get("source_key"): item_id
            for item_id, item in self.data["items"].items()
            if item.get("source_key")
        }

    def upsert(self, item: Dict[str, Any], save: bool = True) -> Tuple[Dict[str, Any], bool]:
        with self.lock:
            existing_id = self.source_index().get(item["source_key"])
            if existing_id:
                existing = self.data["items"][existing_id]
                preserved = {
                    key: existing.get(key)
                    for key in ("title", "notes", "status", "rating", "tags", "folders", "shoot_assignments")
                    if key in existing
                }
                existing.update(item)
                existing.update({key: value for key, value in preserved.items() if value not in (None, "")})
                existing["updated_at"] = utc_now()
                if save:
                    self.save()
                return existing, False

            item.setdefault("id", item_id_for(item["source_key"]))
            item.setdefault("title", Path(item.get("path", "")).stem or item["id"])
            item.setdefault("notes", "")
            item.setdefault("status", "unreviewed")
            item.setdefault("rating", 0)
            item.setdefault("tags", [])
            item.setdefault("folders", [])
            item.setdefault("shoot_assignments", {})
            item.setdefault("created_at", utc_now())
            item["updated_at"] = utc_now()
            self.data["items"][item["id"]] = item
            self.data["order"].insert(0, item["id"])
            if save:
                self.save()
            return item, True

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            item = self.data["items"].get(item_id)
            return dict(item) if item else None

    def has_source(self, source_key: str) -> bool:
        with self.lock:
            return source_key in self.source_index()

    def update(self, item_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        allowed = {"title", "notes", "status", "rating", "tags", "folders"}
        with self.lock:
            item = self.data["items"].get(item_id)
            if not item:
                return None
            for key, value in patch.items():
                if key not in allowed:
                    continue
                if key in {"tags", "folders"} and not isinstance(value, list):
                    continue
                if key == "rating":
                    try:
                        value = max(0, min(5, int(value)))
                    except (TypeError, ValueError):
                        continue
                if key == "status" and value not in {"unreviewed", "keep", "reject", "trash"}:
                    continue
                item[key] = value
            item["updated_at"] = utc_now()
            self.save()
            return dict(item)

    def list_shoots(self) -> Dict[str, Any]:
        with self.lock:
            shoots = [dict(shoot) for shoot in self.data["shoots"].values()]
            current_shoot_id = self.data.get("current_shoot_id", "")
            items = list(self.data["items"].values())
        for shoot in shoots:
            legacy_default = re.fullmatch(r"Shoot (\d+)", str(shoot.get("name") or ""))
            if legacy_default:
                shoot["name"] = f"Board {legacy_default.group(1)}"
        counts = {shoot["id"]: {name: 0 for name in SHOOT_COLLECTIONS} for shoot in shoots}
        for item in items:
            for shoot_id, collection in (item.get("shoot_assignments") or {}).items():
                if shoot_id in counts and collection in counts[shoot_id]:
                    counts[shoot_id][collection] += 1
        for shoot in shoots:
            shoot["counts"] = counts[shoot["id"]]
            shoot["total"] = sum(shoot["counts"].values())
        shoots.sort(key=lambda shoot: shoot.get("created_at", ""))
        return {"shoots": shoots, "current_shoot_id": current_shoot_id}

    def create_shoot(self, name: str) -> Dict[str, Any]:
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            raise ValueError("Shoot name is required.")
        with self.lock:
            shoot_id = uuid.uuid4().hex[:12]
            shoot = {"id": shoot_id, "name": clean_name, "created_at": utc_now()}
            self.data["shoots"][shoot_id] = shoot
            self.data["current_shoot_id"] = shoot_id
            self.save()
            return dict(shoot)

    def update_shoot(self, shoot_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self.lock:
            shoot = self.data["shoots"].get(shoot_id)
            if not shoot:
                return None
            if patch.get("current") is True:
                self.data["current_shoot_id"] = shoot_id
            if "name" in patch:
                name = str(patch["name"] or "").strip()[:80]
                if name:
                    shoot["name"] = name
            self.save()
            return dict(shoot)

    def delete_shoot(self, shoot_id: str) -> bool:
        with self.lock:
            if shoot_id not in self.data["shoots"]:
                return False
            self.data["shoots"].pop(shoot_id, None)
            for item in self.data["items"].values():
                assignments = dict(item.get("shoot_assignments") or {})
                if shoot_id in assignments:
                    assignments.pop(shoot_id, None)
                    item["shoot_assignments"] = assignments
                    item["updated_at"] = utc_now()
            if self.data.get("current_shoot_id") == shoot_id:
                self.data["current_shoot_id"] = next(iter(self.data["shoots"]), "")
            self.save()
            return True

    def assign_to_shoot(self, item_id: str, shoot_id: str, collection: str) -> Optional[Dict[str, Any]]:
        if collection not in SHOOT_COLLECTIONS:
            raise ValueError("Unknown shoot collection.")
        with self.lock:
            item = self.data["items"].get(item_id)
            if not item:
                return None
            if shoot_id not in self.data["shoots"]:
                raise ValueError("Shoot not found.")
            assignments = dict(item.get("shoot_assignments") or {})
            # One item can belong to one collection per shoot, never several.
            assignments[shoot_id] = collection
            item["shoot_assignments"] = assignments
            item["updated_at"] = utc_now()
            self.save()
            return dict(item)

    def remove_from_shoot(self, item_id: str, shoot_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            item = self.data["items"].get(item_id)
            if not item:
                return None
            assignments = dict(item.get("shoot_assignments") or {})
            assignments.pop(shoot_id, None)
            item["shoot_assignments"] = assignments
            item["updated_at"] = utc_now()
            self.save()
            return dict(item)

    def delete_handle(self, handle: str) -> Dict[str, int]:
        clean_handle = str(handle or "").strip()
        if not clean_handle:
            raise ValueError("Handle is required.")
        with self.lock:
            items = [dict(item) for item in self.data["items"].values() if item.get("handle") == clean_handle]

        files_deleted = failed_files = 0
        directories: set[Path] = set()
        paths = {Path(str(item.get(field) or "")) for item in items for field in ("path", "thumb_path")}
        for path in paths:
            if not path or str(path) == ".":
                continue
            try:
                resolved = path.expanduser().resolve()
                if resolved.is_file():
                    directories.add(resolved.parent)
                    resolved.unlink()
                    files_deleted += 1
            except OSError:
                failed_files += 1

        with self.lock:
            item_ids = {item["id"] for item in items}
            for item_id in item_ids:
                self.data["items"].pop(item_id, None)
            self.data["order"] = [item_id for item_id in self.data["order"] if item_id not in item_ids]
            self.save()

        import_root = IMPORT_DIR.resolve()
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            current = directory
            while current != import_root:
                try:
                    current.relative_to(import_root)
                    current.rmdir()
                except (OSError, ValueError):
                    break
                current = current.parent
        return {"deleted": len(items), "files_deleted": files_deleted, "failed_files": failed_files}

    def create_import(self, kind: str, handle: str, source: str) -> Dict[str, Any]:
        import_id = uuid.uuid4().hex[:12]
        record = {
            "id": import_id,
            "kind": kind,
            "handle": handle,
            "source": source,
            "state": "running",
            "created_at": utc_now(),
            "finished_at": "",
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "total": 0,
            "error": "",
        }
        with self.lock:
            self.data.setdefault("imports", {})[import_id] = record
            self.save()
        return dict(record)

    def finish_import(self, import_id: str, job: Dict[str, Any]) -> None:
        if not import_id:
            return
        with self.lock:
            record = self.data.setdefault("imports", {}).get(import_id)
            if not record:
                return
            for key in ("state", "created", "updated", "skipped", "total", "error"):
                if key in job:
                    record[key] = job[key]
            record["finished_at"] = utc_now()
            self.save()

    def list_imports(self) -> List[Dict[str, Any]]:
        with self.lock:
            records = [dict(record) for record in self.data.setdefault("imports", {}).values()]
            asset_counts: Dict[str, int] = {}
            for item in self.data["items"].values():
                import_id = str(item.get("import_id") or "")
                if import_id:
                    asset_counts[import_id] = asset_counts.get(import_id, 0) + 1
        for record in records:
            record["asset_count"] = asset_counts.get(record["id"], 0)
        return sorted(records, key=lambda record: record.get("created_at", ""), reverse=True)

    def delete_import(self, import_id: str) -> Dict[str, int]:
        clean_id = str(import_id or "").strip()
        if not clean_id:
            raise ValueError("Import is required.")
        with self.lock:
            records = self.data.setdefault("imports", {})
            if clean_id not in records:
                raise ValueError("Import not found.")
            items = [dict(item) for item in self.data["items"].values() if item.get("import_id") == clean_id]

        files_deleted = failed_files = 0
        directories: set[Path] = set()
        paths = {Path(str(item.get(field) or "")) for item in items for field in ("path", "thumb_path")}
        for path in paths:
            if not path or str(path) == ".":
                continue
            try:
                resolved = path.expanduser().resolve()
                if resolved.is_file():
                    directories.add(resolved.parent)
                    resolved.unlink()
                    files_deleted += 1
            except OSError:
                failed_files += 1

        with self.lock:
            item_ids = {item["id"] for item in items}
            for item_id in item_ids:
                self.data["items"].pop(item_id, None)
            self.data["order"] = [item_id for item_id in self.data["order"] if item_id not in item_ids]
            self.data.setdefault("imports", {}).pop(clean_id, None)
            self.save()

        import_root = IMPORT_DIR.resolve()
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            current = directory
            while current != import_root:
                try:
                    current.relative_to(import_root)
                    current.rmdir()
                except (OSError, ValueError):
                    break
                current = current.parent
        return {"deleted": len(items), "files_deleted": files_deleted, "failed_files": failed_files}

    def list_items(self, filter_name: str = "all", query: str = "", handle: str = "") -> List[Dict[str, Any]]:
        with self.lock:
            items = [self.data["items"][item_id] for item_id in self.data["order"] if item_id in self.data["items"]]

        query = query.lower().strip()
        results: List[Dict[str, Any]] = []
        for item in items:
            if filter_name == "all" and item.get("status") == "reject":
                continue
            if filter_name != "all":
                if filter_name == "reviewed" and item.get("status") == "unreviewed":
                    continue
                elif filter_name != "reviewed" and item.get("status") != filter_name:
                    continue
            if handle and item.get("handle") != handle:
                continue
            if query:
                haystack = " ".join(
                    [
                        item.get("title", ""),
                        item.get("notes", ""),
                        item.get("handle", ""),
                        item.get("post_code", ""),
                        " ".join(item.get("tags", [])),
                    ]
                ).lower()
                if query not in haystack:
                    continue
            results.append(dict(item))
        return results

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            items = list(self.data["items"].values())
        visible_items = [item for item in items if item.get("status") != "reject"]
        counts = {"all": len(visible_items), "unreviewed": 0, "keep": 0, "reject": 0, "trash": 0, "reviewed": 0}
        handles: Dict[str, int] = {}
        tags: Dict[str, int] = {}
        for item in items:
            status = item.get("status", "unreviewed")
            counts[status] = counts.get(status, 0) + 1
            if status == "reject":
                continue
            if status != "unreviewed":
                counts["reviewed"] += 1
            handle = item.get("handle")
            if handle:
                handles[handle] = handles.get(handle, 0) + 1
            for tag in item.get("tags", []):
                tags[tag] = tags.get(tag, 0) + 1
        return {
            "counts": counts,
            "handles": sorted(handles.items(), key=lambda item: (-item[1], item[0])),
            "tags": sorted(tags.items(), key=lambda item: (-item[1], item[0])),
        }


STORE = LibraryStore(LIBRARY_PATH)


def ensure_default_shoot() -> None:
    with STORE.lock:
        if STORE.data.get("shoots"):
            # Keep user-created board names intact; only update the app's old
            # first default name to match the new UI vocabulary.
            changed = False
            for board in STORE.data["shoots"].values():
                if board.get("name") == "Shoot 1":
                    board["name"] = "Board 1"
                    changed = True
            if changed:
                STORE.save()
            return
        shoot_id = uuid.uuid4().hex[:12]
        STORE.data["shoots"][shoot_id] = {"id": shoot_id, "name": "Board 1", "created_at": utc_now()}
        STORE.data["current_shoot_id"] = shoot_id
        STORE.save()


ensure_default_shoot()
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()


class JobCancelled(RuntimeError):
    """Raised inside a worker after the user cancels an import."""


def make_job(kind: str, target: str) -> Dict[str, Any]:
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "target": target,
        "state": "running",
        "message": "",
        "done": 0,
        "total": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "error": "",
        "pause_requested": False,
        "cancel_requested": False,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    return job


def update_job(job: Dict[str, Any], **changes: Any) -> None:
    with JOBS_LOCK:
        job.update(changes)
        job["updated_at"] = utc_now()


def finish_job(job: Dict[str, Any], state: str = "done", error: str = "") -> None:
    update_job(job, state=state, error=error)


def checkpoint_job(job: Optional[Dict[str, Any]]) -> None:
    """Cooperatively pause or cancel an importer between network/file operations."""
    if not job:
        return
    while True:
        with JOBS_LOCK:
            if job.get("cancel_requested"):
                raise JobCancelled("Import cancelled.")
            if not job.get("pause_requested"):
                if job.get("state") == "paused":
                    job["state"] = "running"
                    job["message"] = "Importing"
                    job["updated_at"] = utc_now()
                return
        time.sleep(0.2)


def job_sleep(job: Optional[Dict[str, Any]], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        checkpoint_job(job)
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def control_job(job_id: str, action: str) -> Optional[Dict[str, Any]]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        if job.get("state") in {"done", "error", "cancelled"}:
            return dict(job)
        if action == "pause":
            job["pause_requested"] = True
            job["state"] = "paused"
            job["message"] = "Paused"
        elif action == "resume":
            job["pause_requested"] = False
            job["state"] = "running"
            job["message"] = "Importing"
        elif action == "cancel":
            job["cancel_requested"] = True
            job["pause_requested"] = False
            job["state"] = "cancelling"
            job["message"] = "Stopping"
        else:
            raise ValueError("Unknown job action.")
        job["updated_at"] = utc_now()
        return dict(job)


def visible_item(item: Dict[str, Any]) -> Dict[str, Any]:
    copy = dict(item)
    item_id = copy["id"]
    copy["thumb_url"] = f"/thumb/{item_id}"
    copy["media_url"] = f"/media/{item_id}"
    copy.pop("thumb_path", None)
    copy.pop("path", None)
    return copy


def ingest_image(path: Path, handle: str, save: bool = True, import_id: str = "") -> Tuple[Dict[str, Any], bool]:
    metadata = archive_metadata(path, handle)
    item_id = item_id_for(metadata["source_key"])
    existing = STORE.get(item_id)
    if existing and existing.get("tone_colors") and existing.get("context_colors") and existing.get("dominant_color"):
        return existing, False
    analysis = analyze_file(path, item_id)
    item = {
        "id": item_id,
        "source_key": metadata["source_key"],
        "handle": handle,
        "path": str(path.resolve()),
        "filename": path.name,
        "title": path.stem,
        "tags": [tag for tag in {handle, "instagram"} if tag],
        "folders": [handle] if handle else [],
        "import_id": import_id,
        **metadata,
        **analysis,
    }
    return STORE.upsert(item, save=save)


def ingest_folder(
    path: Path,
    handle: str,
    job: Optional[Dict[str, Any]] = None,
    import_id: str = "",
) -> Dict[str, int]:
    files = sorted(
        file
        for file in path.expanduser().resolve().iterdir()
        if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS
    )
    if job:
        update_job(job, total=len(files), message="Scanning")

    created = updated = skipped = 0
    for index, file in enumerate(files, start=1):
        checkpoint_job(job)
        try:
            _item, is_new = ingest_image(file, handle, save=False, import_id=import_id)
            if is_new:
                created += 1
            else:
                updated += 1
        except Exception:
            skipped += 1
        if job and (index == len(files) or index % 10 == 0):
            update_job(
                job,
                done=index,
                created=created,
                updated=updated,
                skipped=skipped,
                message=f"Indexed {index}/{len(files)}",
            )
        if index % 25 == 0:
            STORE.save()
    STORE.save()
    return {"created": created, "updated": updated, "skipped": skipped, "total": len(files)}


def backfill_dominant_colors() -> Dict[str, int]:
    with STORE.lock:
        items = [dict(item) for item in STORE.data["items"].values() if not item.get("dominant_color")]

    updated = skipped = 0
    for index, item in enumerate(items, start=1):
        image_path = Path(str(item.get("thumb_path") or item.get("path") or ""))
        try:
            with Image.open(image_path) as opened:
                dominant_color = extract_dominant_color(image_to_rgb(opened))
            if not dominant_color:
                skipped += 1
                continue
            with STORE.lock:
                stored = STORE.data["items"].get(item["id"])
                if stored:
                    stored["dominant_color"] = dominant_color
                    stored["updated_at"] = utc_now()
                    updated += 1
        except Exception:
            skipped += 1
        if index % 25 == 0:
            STORE.save()
    if items:
        STORE.save()
    return {"updated": updated, "skipped": skipped, "total": len(items)}


def guess_chrome_cookiefile() -> Optional[str]:
    base = Path.home() / "Library/Application Support/Google/Chrome"
    if not base.exists():
        return None
    for cookie_file in base.glob("*/Cookies"):
        try:
            with sqlite3.connect(f"file:{cookie_file}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "select 1 from cookies where host_key like ? and name = ? limit 1",
                    ("%instagram.com", "sessionid"),
                ).fetchone()
            if row:
                return str(cookie_file)
        except sqlite3.Error:
            continue
    return None


def run_in_thread(job: Dict[str, Any], target) -> None:
    def runner() -> None:
        try:
            target(job)
            finish_job(job)
        except JobCancelled:
            finish_job(job, state="cancelled", error="")
        except Exception as exc:  # noqa: BLE001 - job boundary should capture failures.
            finish_job(job, state="error", error=str(exc))
        finally:
            STORE.finish_import(str(job.get("import_id") or ""), job)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


def start_folder_job(path: Path, handle: str) -> Dict[str, Any]:
    record = STORE.create_import("folder", handle, str(path.expanduser().resolve()))
    job = make_job("folder", str(path))
    job["import_id"] = record["id"]

    def work(current: Dict[str, Any]) -> None:
        ingest_folder(path, handle, current, import_id=record["id"])

    run_in_thread(job, work)
    return job


def start_instagram_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    profile = str(payload.get("profile") or payload.get("handle") or "").strip()
    username = parse_username(profile)
    if not username:
        raise RuntimeError("Instagram handle is required.")

    record = STORE.create_import("instagram", username, profile)
    job = make_job("instagram", username)
    job["import_id"] = record["id"]

    def work(current: Dict[str, Any]) -> None:
        browser = str(payload.get("browser") or "chrome")
        cookiefile = str(payload.get("cookiefile") or "").strip() or None
        if not cookiefile and browser.lower() == "chrome":
            cookiefile = guess_chrome_cookiefile()

        max_pages = payload.get("max_pages")
        max_pages = int(max_pages) if str(max_pages or "").strip() else None
        count_per_page = max(1, int(payload.get("count_per_page") or 12))
        page_delay = max(0.0, float(payload.get("page_delay") or 1.0))
        image_delay = max(0.0, float(payload.get("image_delay") or 0.15))
        video_thumbnails = bool(payload.get("video_thumbnails"))

        args = argparse.Namespace(browser=browser, cookiefile=cookiefile)
        checkpoint_job(current)
        session = make_session(args, username)
        checkpoint_job(current)
        profile_data = profile_user(session, username)
        user_id = profile_data["id"]
        expected_posts = (profile_data.get("edge_owner_to_timeline_media") or {}).get("count") or 0
        update_job(current, total=expected_posts, message="Importing")

        image_dir = IMPORT_DIR / username / "originals"
        image_dir.mkdir(parents=True, exist_ok=True)
        max_id: Optional[str] = None
        page = 0
        seen_post_ids = set()

        while True:
            checkpoint_job(current)
            page += 1
            if max_pages is not None and page > max_pages:
                break
            params = {"count": str(count_per_page)}
            if max_id:
                params["max_id"] = max_id
            data = request_json(session, f"https://www.instagram.com/api/v1/feed/user/{user_id}/", params=params)
            items = data.get("items") or []
            if not items:
                break

            for item in items:
                checkpoint_job(current)
                post_id = str(item.get("id") or item.get("pk") or "")
                if post_id and post_id in seen_post_ids:
                    continue
                if post_id:
                    seen_post_ids.add(post_id)

                code = safe(str(item.get("code") or item.get("pk") or post_id), "post")
                post_url = f"https://www.instagram.com/p/{code}/"
                date_part = taken_date(item)
                for media_index, media in iter_photo_media(item, video_thumbnails):
                    checkpoint_job(current)
                    image = best_image(media)
                    if not image:
                        continue
                    image_url, width, height = image
                    source_key = f"instagram:{username}:{code}:{media_index:02d}"
                    if STORE.has_source(source_key):
                        update_job(current, skipped=current["skipped"] + 1)
                        continue

                    filename = safe(
                        f"{date_part}_{code}_{media_index:02d}_{width}x{height}.jpg",
                        f"{code}_{media_index:02d}.jpg",
                    )
                    path = image_dir / filename
                    download_image(session, image_url, path, post_url)
                    item_id = item_id_for(source_key)
                    analysis = analyze_file(path, item_id)
                    library_item = {
                        "id": item_id,
                        "source_key": source_key,
                        "handle": username,
                        "path": str(path.resolve()),
                        "filename": filename,
                        "title": Path(filename).stem,
                        "source_url": post_url,
                        "post_code": code,
                        "media_index": media_index,
                        "taken_date_utc": date_part,
                        "tags": [username, "instagram"],
                        "folders": [username],
                        "import_id": record["id"],
                        **analysis,
                    }
                    _stored, is_new = STORE.upsert(library_item)
                    update_job(
                        current,
                        created=current["created"] + (1 if is_new else 0),
                        updated=current["updated"] + (0 if is_new else 1),
                    )
                    job_sleep(current, image_delay)

                update_job(current, done=current["done"] + 1, message=f"Page {page}")

            if not data.get("more_available"):
                break
            max_id = data.get("next_max_id")
            if not max_id:
                break
            job_sleep(current, page_delay)

    run_in_thread(job, work)
    return job


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "Jade/0.1"

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/media/"):
            self.send_item_file(parsed.path.removeprefix("/media/"), "path", head_only=True)
            return
        if parsed.path.startswith("/thumb/"):
            self.send_item_file(parsed.path.removeprefix("/thumb/"), "thumb_path", head_only=True)
            return
        self.send_static(parsed.path, head_only=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/library":
            query = parse_qs(parsed.query)
            items = STORE.list_items(
                filter_name=query.get("filter", ["all"])[0],
                query=query.get("q", [""])[0],
                handle=query.get("handle", [""])[0],
            )
            self.send_json({"items": [visible_item(item) for item in items], "stats": STORE.stats()})
            return
        if parsed.path == "/api/shoots":
            self.send_json(STORE.list_shoots())
            return
        if parsed.path == "/api/jobs":
            with JOBS_LOCK:
                jobs = sorted(JOBS.values(), key=lambda job: job["created_at"], reverse=True)
            self.send_json({"jobs": jobs[:20]})
            return
        if parsed.path == "/api/imports":
            self.send_json({"imports": STORE.list_imports()})
            return
        if parsed.path == "/api/config":
            self.send_json({"chrome_cookiefile": guess_chrome_cookiefile() or ""})
            return
        if parsed.path.startswith("/media/"):
            self.send_item_file(parsed.path.removeprefix("/media/"), "path")
            return
        if parsed.path.startswith("/thumb/"):
            self.send_item_file(parsed.path.removeprefix("/thumb/"), "thumb_path")
            return
        self.send_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/control"):
            job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/control").strip("/")
            try:
                job = control_job(job_id, str(self.read_json().get("action") or ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            if not job:
                self.send_json({"error": "Import job not found."}, status=404)
                return
            self.send_json({"job": job})
            return
        if parsed.path == "/api/shoots":
            try:
                shoot = STORE.create_shoot(self.read_json().get("name", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"shoot": shoot, **STORE.list_shoots()}, status=201)
            return
        if parsed.path.startswith("/api/items/") and parsed.path.endswith("/shoot-assignment"):
            item_id = parsed.path.removeprefix("/api/items/").removesuffix("/shoot-assignment").strip("/")
            payload = self.read_json()
            try:
                item = STORE.assign_to_shoot(item_id, str(payload.get("shoot_id") or ""), str(payload.get("collection") or ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            if not item:
                self.send_json({"error": "Item not found."}, status=404)
                return
            self.send_json({"item": visible_item(item), **STORE.list_shoots()})
            return
        if parsed.path == "/api/import-folder":
            payload = self.read_json()
            folder = Path(str(payload.get("path") or ""))
            handle = parse_username(str(payload.get("handle") or folder.parent.name or folder.name))
            if not folder.expanduser().exists():
                self.send_json({"error": "Folder not found."}, status=400)
                return
            job = start_folder_job(folder, handle)
            self.send_json({"job": job}, status=202)
            return
        if parsed.path == "/api/import-instagram":
            try:
                job = start_instagram_job(self.read_json())
            except RuntimeError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"job": job}, status=202)
            return
        self.send_json({"error": "Not found."}, status=404)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/shoots/"):
            shoot_id = parsed.path.removeprefix("/api/shoots/").strip("/")
            shoot = STORE.update_shoot(shoot_id, self.read_json())
            if not shoot:
                self.send_json({"error": "Shoot not found."}, status=404)
                return
            self.send_json({"shoot": shoot, **STORE.list_shoots()})
            return
        if parsed.path.startswith("/api/items/"):
            item_id = parsed.path.removeprefix("/api/items/").strip("/")
            item = STORE.update(item_id, self.read_json())
            if not item:
                self.send_json({"error": "Item not found."}, status=404)
                return
            self.send_json({"item": visible_item(item)})
            return
        self.send_json({"error": "Not found."}, status=404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/shoots/"):
            shoot_id = parsed.path.removeprefix("/api/shoots/").strip("/")
            if not STORE.delete_shoot(shoot_id):
                self.send_json({"error": "Shoot not found."}, status=404)
                return
            self.send_json({"shoot_id": shoot_id, **STORE.list_shoots()})
            return
        if parsed.path.startswith("/api/imports/"):
            import_id = parsed.path.removeprefix("/api/imports/").strip("/")
            try:
                result = STORE.delete_import(import_id)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=404)
                return
            self.send_json({"import_id": import_id, **result, "stats": STORE.stats(), **STORE.list_shoots()})
            return
        if parsed.path.startswith("/api/handles/"):
            handle = unquote(parsed.path.removeprefix("/api/handles/")).strip("/")
            try:
                result = STORE.delete_handle(handle)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"handle": handle, **result, "stats": STORE.stats(), **STORE.list_shoots()})
            return
        if parsed.path.startswith("/api/items/") and parsed.path.endswith("/shoot-assignment"):
            item_id = parsed.path.removeprefix("/api/items/").removesuffix("/shoot-assignment").strip("/")
            shoot_id = parse_qs(parsed.query).get("shoot_id", [""])[0]
            item = STORE.remove_from_shoot(item_id, shoot_id)
            if not item:
                self.send_json({"error": "Item not found."}, status=404)
                return
            self.send_json({"item": visible_item(item), **STORE.list_shoots()})
            return
        self.send_json({"error": "Not found."}, status=404)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_item_file(self, item_id: str, field: str, head_only: bool = False) -> None:
        item = STORE.get(unquote(item_id))
        if not item:
            self.send_error(404)
            return
        path = Path(str(item.get(field) or ""))
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        self.send_file(path, head_only=head_only)

    def send_static(self, request_path: str, head_only: bool = False) -> None:
        clean = unquote(request_path).strip("/")
        if not clean:
            clean = "index.html"
        path = (WEB_DIR / clean).resolve()
        try:
            path.relative_to(WEB_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not path.exists() or not path.is_file():
            path = WEB_DIR / "index.html"
        self.send_file(path, head_only=head_only)

    def send_file(self, path: Path, head_only: bool = False) -> None:
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if head_only:
            return
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def serve(host: str, port: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), StudioHandler)
    print(f"Jade running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Eagle-style image curation app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--ingest-folder", type=Path, help="Index an existing image folder before serving.")
    parser.add_argument("--handle", help="Instagram handle to assign when indexing a folder.")
    parser.add_argument("--backfill-dominant-colors", action="store_true", help="Calculate dominant colors for existing assets.")
    parser.add_argument("--no-serve", action="store_true", help="Run the ingest command and exit.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.ingest_folder:
        handle = parse_username(args.handle or args.ingest_folder.parent.name)
        print(f"Indexing {args.ingest_folder} as @{handle}...")
        result = ingest_folder(args.ingest_folder, handle)
        print(
            "Indexed {total} files: {created} new, {updated} updated, {skipped} skipped.".format(
                **result
            )
        )
    if args.backfill_dominant_colors:
        result = backfill_dominant_colors()
        print("Calculated dominant colors for {updated}/{total} assets; {skipped} skipped.".format(**result))
    if args.no_serve:
        return 0
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
