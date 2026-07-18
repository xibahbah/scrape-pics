#!/usr/bin/env python3
"""Archive Instagram profile photo media using an authenticated browser session."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple
from urllib.parse import urlparse

import browser_cookie3
import requests


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def parse_username(value: str) -> str:
    value = value.strip()
    if value.startswith("@"):
        return value[1:].strip("/")

    parsed = urlparse(value)
    if parsed.netloc and "instagram.com" in parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 1:
            return parts[0]

    return value.strip("/")


def safe(value: str, fallback: str = "instagram") -> str:
    value = SAFE_RE.sub("-", value).strip(".-_")
    return value[:120] or fallback


def load_cookie_jar(browser: str, cookie_file: Optional[str]):
    loaders = {
        "chrome": browser_cookie3.chrome,
        "chromium": browser_cookie3.chromium,
        "firefox": browser_cookie3.firefox,
        "safari": browser_cookie3.safari,
        "edge": browser_cookie3.edge,
        "brave": browser_cookie3.brave,
    }
    try:
        loader = loaders[browser.lower()]
    except KeyError as exc:
        names = ", ".join(sorted(loaders))
        raise RuntimeError(f"Unsupported browser {browser!r}. Choose one of: {names}.") from exc
    return loader(domain_name="instagram.com", cookie_file=cookie_file)


def request_json(session: requests.Session, url: str, *, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    last_error: Optional[BaseException] = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=45)
            if response.status_code == 400:
                try:
                    detail = response.json().get("message") or response.text
                except ValueError:
                    detail = response.text
                raise RuntimeError(f"Instagram replied 400: {str(detail).strip()[:300]}")
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"{response.status_code} {response.reason}", response=response)
            response.raise_for_status()
            data = response.json()
            if data.get("status") not in {None, "ok"}:
                raise RuntimeError(f"Instagram returned status={data.get('status')!r}")
            return data
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - command-line archiver needs retry context.
            last_error = exc
            if attempt == 4:
                break
            wait = 3 * attempt
            print(f"Request failed ({exc}); retrying in {wait}s...", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Could not fetch JSON from {url}: {last_error}")


def profile_user(session: requests.Session, username: str) -> Dict[str, Any]:
    """Fetch a profile and translate Instagram's current schema outage into a useful local error."""
    try:
        payload = request_json(
            session,
            "https://www.instagram.com/api/v1/users/web_profile_info/",
            params={"username": username},
        )
        return payload["data"]["user"]
    except Exception as exc:  # noqa: BLE001 - preserves the exact API failure for non-schema cases.
        message = str(exc)
        if "ig_business_category_subvertical" in message:
            raise RuntimeError(
                f"Instagram is currently refusing @{username}'s profile data because its business category points to a deleted Instagram field. "
                "Your browser login is working; try this profile again later or import a different profile."
            ) from exc
        raise


def best_image(media: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    candidates = ((media.get("image_versions2") or {}).get("candidates") or [])
    if not candidates:
        return None

    candidate = max(candidates, key=lambda c: int(c.get("width") or 0) * int(c.get("height") or 0))
    url = candidate.get("url")
    if not url:
        return None
    return str(url), int(candidate.get("width") or 0), int(candidate.get("height") or 0)


def iter_photo_media(item: Dict[str, Any], include_video_thumbnails: bool) -> Iterable[Tuple[int, Dict[str, Any]]]:
    if item.get("media_type") == 8 and item.get("carousel_media"):
        for index, child in enumerate(item.get("carousel_media") or [], start=1):
            is_video = child.get("media_type") == 2
            if is_video and not include_video_thumbnails:
                continue
            if best_image(child):
                yield index, child
        return

    is_video = item.get("media_type") == 2
    if is_video and not include_video_thumbnails:
        return

    if best_image(item):
        yield 1, item


def taken_date(item: Dict[str, Any]) -> str:
    taken_at = item.get("taken_at") or item.get("device_timestamp")
    if isinstance(taken_at, (int, float)) and taken_at > 0:
        if taken_at > 10_000_000_000:
            taken_at = taken_at / 1_000_000
        return datetime.fromtimestamp(taken_at, timezone.utc).strftime("%Y-%m-%d")
    return "unknown-date"


def download_image(session: requests.Session, url: str, path: Path, referer: str) -> int:
    if path.exists() and path.stat().st_size > 0:
        return path.stat().st_size

    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": referer,
        "User-Agent": USER_AGENT,
    }
    tmp_path = path.with_suffix(path.suffix + ".part")
    last_error: Optional[BaseException] = None
    for attempt in range(1, 5):
        try:
            with session.get(url, headers=headers, stream=True, timeout=60) as response:
                if response.status_code in {403, 429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"{response.status_code} {response.reason}", response=response)
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                with tmp_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
            tmp_path.replace(path)
            return path.stat().st_size
        except Exception as exc:  # noqa: BLE001 - command-line archiver needs retry context.
            last_error = exc
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            if attempt == 4:
                break
            wait = 2 * attempt
            print(f"  image retry {attempt} after {exc}; waiting {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Could not download image to {path}: {last_error}")


def make_session(args: argparse.Namespace, username: str) -> requests.Session:
    jar = load_cookie_jar(args.browser, args.cookiefile)
    csrf = next((c.value for c in jar if c.name == "csrftoken"), "")
    if not any(c.name == "sessionid" for c in jar):
        raise RuntimeError("No Instagram sessionid cookie found in the selected browser profile.")

    session = requests.Session()
    session.cookies = jar
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Referer": f"https://www.instagram.com/{username}/",
            "X-IG-App-ID": "936619743392459",
            "X-CSRFToken": csrf,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return session


def archive_profile(args: argparse.Namespace) -> int:
    username = parse_username(args.profile)
    out_dir = args.output_dir / username
    image_dir = out_dir / "originals"
    manifest_path = out_dir / "manifest.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    session = make_session(args, username)
    profile_data = profile_user(session, username)
    user_id = profile_data["id"]
    expected_posts = ((profile_data.get("edge_owner_to_timeline_media") or {}).get("count"))
    print(f"Authenticated. @{username} user_id={user_id}; Instagram reports {expected_posts} posts.", flush=True)

    manifest: Dict[str, Any] = {
        "profile": username,
        "user_id": user_id,
        "source": f"https://www.instagram.com/{username}/",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_posts": expected_posts,
        "video_media_skipped": 0,
        "posts_seen": 0,
        "images_downloaded": 0,
        "items": [],
    }

    max_id: Optional[str] = None
    page = 0
    seen_post_ids = set()
    seen_files = set()

    while True:
        page += 1
        if args.max_pages is not None and page > args.max_pages:
            break

        params = {"count": str(args.count_per_page)}
        if max_id:
            params["max_id"] = max_id
        data = request_json(session, f"https://www.instagram.com/api/v1/feed/user/{user_id}/", params=params)
        items = data.get("items") or []
        print(f"Page {page}: {len(items)} posts; more={bool(data.get('more_available'))}", flush=True)
        if not items:
            break

        for item in items:
            post_id = str(item.get("id") or item.get("pk") or "")
            if post_id and post_id in seen_post_ids:
                continue
            if post_id:
                seen_post_ids.add(post_id)
            manifest["posts_seen"] += 1

            code = safe(str(item.get("code") or item.get("pk") or post_id), "post")
            post_url = f"https://www.instagram.com/p/{code}/"
            date_part = taken_date(item)
            photo_entries = list(iter_photo_media(item, args.video_thumbnails))
            if not photo_entries and item.get("media_type") in {2, 8}:
                manifest["video_media_skipped"] += 1

            for media_index, media in photo_entries:
                best = best_image(media)
                if not best:
                    continue
                image_url, width, height = best
                filename = safe(
                    f"{date_part}_{code}_{media_index:02d}_{width}x{height}.jpg",
                    f"{code}_{media_index:02d}.jpg",
                )
                if filename in seen_files:
                    continue
                seen_files.add(filename)

                path = image_dir / filename
                size = download_image(session, image_url, path, post_url)
                manifest["images_downloaded"] += 1
                manifest["items"].append(
                    {
                        "post_code": code,
                        "post_url": post_url,
                        "media_index": media_index,
                        "width": width,
                        "height": height,
                        "file": str(path),
                        "bytes": size,
                        "taken_date_utc": date_part,
                    }
                )
                if manifest["images_downloaded"] % 25 == 0:
                    print(
                        f"  images saved: {manifest['images_downloaded']} "
                        f"from {manifest['posts_seen']} posts",
                        flush=True,
                    )
                time.sleep(args.image_delay)

        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        if not data.get("more_available"):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(args.page_delay)

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. Saved {manifest['images_downloaded']} images from {manifest['posts_seen']} posts to {image_dir}.")
    print(f"Manifest: {manifest_path}")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive Instagram profile photos using browser cookies.")
    parser.add_argument("profile", help="Instagram profile URL, @username, or username.")
    parser.add_argument("--browser", default="chrome", help="Browser to read cookies from. Default: chrome.")
    parser.add_argument("--cookiefile", help="Optional browser cookie database path.")
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"), help="Base output folder.")
    parser.add_argument("--count-per-page", type=int, default=12, help="Instagram feed page size. Default: 12.")
    parser.add_argument("--page-delay", type=float, default=1.0, help="Delay between profile pages. Default: 1s.")
    parser.add_argument("--image-delay", type=float, default=0.15, help="Delay between images. Default: 0.15s.")
    parser.add_argument("--max-pages", type=int, help="Optional limit for test runs.")
    parser.add_argument("--video-thumbnails", action="store_true", help="Also save thumbnails for video media.")
    args = parser.parse_args(argv)
    if args.count_per_page < 1:
        parser.error("--count-per-page must be at least 1.")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1.")
    if args.page_delay < 0 or args.image_delay < 0:
        parser.error("Delays cannot be negative.")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return archive_profile(parse_args(argv))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
