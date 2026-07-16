#!/usr/bin/env python3
"""Download Instagram post images, compress them, and import them into Eagle."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

try:
    import instaloader
    from instaloader import exceptions as instaloader_exceptions
    from instaloader import Instaloader, Post, Profile
except ImportError as exc:  # pragma: no cover - exercised before runtime setup.
    raise SystemExit(
        "Missing dependency: instaloader. Run `python3 -m pip install -r requirements.txt` first."
    ) from exc


INSTAGRAM_SHORTCODE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv)/([^/?#]+)/?",
    re.IGNORECASE,
)
SHORTCODE_ONLY_RE = re.compile(r"^[A-Za-z0-9_-]{5,}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
IMAGE_CHUNK_SIZE = 1024 * 256
DEFAULT_INSTAGRAM_USERNAME = "xibahbah"
INSTAGRAM_PROFILE_HOSTS = {"instagram.com", "www.instagram.com"}
INSTAGRAM_RESERVED_PATHS = {
    "about",
    "accounts",
    "api",
    "developer",
    "direct",
    "explore",
    "p",
    "privacy",
    "reel",
    "stories",
    "terms",
    "tv",
}


@dataclass(frozen=True)
class MediaEntry:
    index: int
    url: str
    source_name: str


@dataclass(frozen=True)
class PreparedImage:
    source_url: str
    original_path: Path
    compressed_path: Path
    eagle_name: str


@dataclass(frozen=True)
class Target:
    kind: str
    value: str
    url: str


class EagleError(RuntimeError):
    pass


class EagleClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def check_running(self) -> None:
        self._request("GET", "/api/application/info")

    def create_folder(self, folder_name: str, parent: Optional[str] = None) -> str:
        payload = {"folderName": folder_name}
        if parent:
            payload["parent"] = parent
        data = self._request("POST", "/api/folder/create", json_payload=payload)
        folder_id = data.get("data", {}).get("id")
        if not folder_id:
            raise EagleError(f"Eagle did not return a folder id: {json.dumps(data, indent=2)}")
        return folder_id

    def add_from_paths(self, folder_id: str, items: Sequence[dict]) -> None:
        payload = {"folderId": folder_id, "items": list(items)}
        self._request("POST", "/api/item/addFromPaths", json_payload=payload)

    def _request(self, method: str, path: str, json_payload: Optional[dict] = None) -> dict:
        params = {"token": self.token} if self.token else None
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.ConnectionError as exc:
            raise EagleError(
                f"Could not reach Eagle at {self.base_url}. Open Eagle and try again."
            ) from exc
        except requests.RequestException as exc:
            raise EagleError(f"Eagle API request failed: {exc}") from exc
        except ValueError as exc:
            raise EagleError(f"Eagle returned a non-JSON response from {url}") from exc

        if data.get("status") != "success":
            message = data.get("message") or json.dumps(data, indent=2)
            raise EagleError(f"Eagle API error: {message}")
        return data


def parse_shortcode(value: str) -> str:
    value = value.strip()
    match = INSTAGRAM_SHORTCODE_RE.search(value)
    if match:
        return match.group(1)
    if SHORTCODE_ONLY_RE.match(value):
        return value
    raise ValueError(
        "Expected an Instagram post/reel URL like "
        "`https://www.instagram.com/p/SHORTCODE/` or a shortcode."
    )


def parse_profile_username(value: str) -> Optional[str]:
    value = value.strip()
    if value.startswith("@"):
        username = value[1:].strip("/")
        return username if username and "/" not in username else None

    parsed = urlparse(value)
    if parsed.netloc.lower() not in INSTAGRAM_PROFILE_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        return None

    username = parts[0]
    if username.lower() in INSTAGRAM_RESERVED_PATHS:
        return None
    return username


def parse_target(value: str) -> Target:
    value = value.strip()
    match = INSTAGRAM_SHORTCODE_RE.search(value)
    if match:
        shortcode = match.group(1)
        return Target("post", shortcode, post_url_from_shortcode(shortcode))

    username = parse_profile_username(value)
    if username:
        return Target("profile", username, f"https://www.instagram.com/{username}/")

    if SHORTCODE_ONLY_RE.match(value):
        return Target("post", value, post_url_from_shortcode(value))

    raise ValueError(
        "Expected an Instagram post/reel URL, profile URL, @username, or post shortcode."
    )


def safe_name(value: str, fallback: str = "instagram-post") -> str:
    normalized = SAFE_NAME_RE.sub("", value).strip(" ._-")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:120] or fallback


def import_browser_session(
    loader: Instaloader,
    browser: str,
    cookie_file: Optional[str],
    session_file: Optional[str],
) -> None:
    try:
        from instaloader.__main__ import import_session
    except ImportError as exc:
        raise RuntimeError(
            "Browser cookie import needs `browser-cookie3`. Run "
            "`python3 -m pip install -r requirements.txt` and try again."
        ) from exc

    def timeout_handler(_signum, _frame):
        raise TimeoutError

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(45)
    try:
        import_session(browser.lower(), loader, cookie_file)
    except TimeoutError as exc:
        raise RuntimeError(
            f"Timed out while loading cookies from {browser}. If macOS asks for Keychain "
            "access, click Allow, or try `--load-cookies firefox` if you use Firefox."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Could not load Instagram cookies from {browser}. Open Instagram in {browser}, "
            "make sure you are logged in, then rerun the command."
        ) from exc
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    username = loader.test_login()
    if not username:
        raise RuntimeError(f"Loaded cookies from {browser}, but Instagram did not accept them.")

    loader.save_session_to_file(filename=session_file)
    print(f"Saved Instagram session for {username}.")


def build_loader(
    username: Optional[str],
    session_file: Optional[str],
    load_cookies: Optional[str],
    cookie_file: Optional[str],
) -> Instaloader:
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )

    if load_cookies:
        import_browser_session(loader, load_cookies, cookie_file, session_file)
        return loader

    if username:
        if username.upper() in {"YOUR_INSTAGRAM_USERNAME", "USERNAME", "YOUR_USERNAME"}:
            raise RuntimeError("Replace `YOUR_INSTAGRAM_USERNAME` with your real Instagram username.")

        try:
            loader.load_session_from_file(username, filename=session_file)
            print(f"Loaded Instagram session for {username}.")
        except FileNotFoundError:
            password = getpass.getpass(f"Instagram password for {username}: ")
            try:
                loader.login(username, password)
            except instaloader_exceptions.TwoFactorAuthRequiredException:
                code = input("Instagram two-factor code: ").strip()
                loader.two_factor_login(code)
            except instaloader_exceptions.InstaloaderException as exc:
                raise RuntimeError(
                    "Instagram login failed. Check that `--login` is your actual username, "
                    "then try again. If Instagram still rejects it, log into Instagram in "
                    "your browser once and rerun the command."
                ) from exc
            loader.save_session_to_file(filename=session_file)
            print(f"Saved Instagram session for {username}.")

    return loader


def load_post(loader: Instaloader, shortcode: str) -> Post:
    try:
        return Post.from_shortcode(loader.context, shortcode)
    except Exception as exc:
        raise RuntimeError(
            "Could not read that Instagram post. If it is private, age-gated, or Instagram "
            "is rate-limiting you, rerun with `--login YOUR_USERNAME`."
        ) from exc


def load_profile(loader: Instaloader, username: str) -> Profile:
    try:
        return Profile.from_username(loader.context, username)
    except Exception as exc:
        raise RuntimeError(
            "Could not read that Instagram profile. If it is private or Instagram "
            "is rate-limiting you, rerun with `--login YOUR_USERNAME`."
        ) from exc


def extract_images(post: Post, include_video_thumbnails: bool) -> List[MediaEntry]:
    entries: List[MediaEntry] = []

    if post.typename == "GraphSidecar":
        for index, node in enumerate(post.get_sidecar_nodes(), start=1):
            is_video = bool(getattr(node, "is_video", False))
            if is_video and not include_video_thumbnails:
                continue
            url = getattr(node, "display_url", None) or getattr(node, "url", None)
            if url:
                entries.append(MediaEntry(index=index, url=url, source_name=f"image-{index:02d}"))
        return entries

    if post.is_video and not include_video_thumbnails:
        return []

    if post.url:
        entries.append(MediaEntry(index=1, url=post.url, source_name="image-01"))
    return entries


def download_image(session: requests.Session, url: str, destination: Path) -> None:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.instagram.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }
    with session.get(url, headers=headers, stream=True, timeout=45) as response:
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=IMAGE_CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)


def compress_image(source: Path, destination: Path, quality: int, max_edge: Optional[int]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if max_edge:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image.thumbnail((max_edge, max_edge), resampling)

        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha = image.convert("RGBA").split()[-1]
            background.paste(image.convert("RGBA"), mask=alpha)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

        image.save(
            destination,
            "JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )


def prepare_images(
    entries: Iterable[MediaEntry],
    post: Post,
    post_url: str,
    output_dir: Path,
    quality: int,
    max_edge: Optional[int],
    session: requests.Session,
) -> List[PreparedImage]:
    shortcode = post.shortcode
    owner = safe_name(post.owner_username, fallback="instagram")
    post_dir = output_dir / safe_name(f"{owner}_{shortcode}", fallback=shortcode)
    originals_dir = post_dir / "originals"
    compressed_dir = post_dir / "compressed"
    prepared: List[PreparedImage] = []

    for entry in entries:
        base_name = safe_name(f"{owner}_{shortcode}_{entry.source_name}", fallback=f"{shortcode}_{entry.index}")
        original_path = originals_dir / f"{base_name}.download"
        compressed_path = compressed_dir / f"{base_name}.jpg"

        print(f"Downloading {entry.source_name}...")
        download_image(session, entry.url, original_path)
        print(f"Compressing {entry.source_name}...")
        compress_image(original_path, compressed_path, quality=quality, max_edge=max_edge)

        prepared.append(
            PreparedImage(
                source_url=post_url,
                original_path=original_path,
                compressed_path=compressed_path,
                eagle_name=base_name,
            )
        )

    return prepared


def make_folder_name(post: Post, explicit_name: Optional[str]) -> str:
    if explicit_name:
        return safe_name(explicit_name)

    date_part = post.date_utc.strftime("%Y-%m-%d") if isinstance(post.date_utc, datetime) else "instagram"
    return safe_name(f"{post.owner_username} {date_part} {post.shortcode}")


def make_annotation(post: Post, post_url: str) -> str:
    caption = (post.caption or "").strip()
    parts = [f"Imported from {post_url}"]
    if caption:
        parts.append(caption[:3500])
    return "\n\n".join(parts)


def build_eagle_items(prepared_images: Sequence[PreparedImage], post: Post, tags: Sequence[str]) -> List[dict]:
    annotation = make_annotation(post, prepared_images[0].source_url) if prepared_images else ""
    return [
        {
            "path": str(image.compressed_path.resolve()),
            "name": image.eagle_name,
            "website": image.source_url,
            "annotation": annotation,
            "tags": list(tags),
        }
        for image in prepared_images
    ]


def post_url_from_shortcode(shortcode: str) -> str:
    return f"https://www.instagram.com/p/{shortcode}/"


def prepare_post(args: argparse.Namespace, loader: Instaloader, post: Post, post_url: str) -> List[PreparedImage]:
    entries = extract_images(post, include_video_thumbnails=args.video_thumbnails)

    if not entries:
        print(
            "No image media found in this post. "
            "If it is a video/reel and you want the thumbnail, rerun with `--video-thumbnails`."
        )
        return []

    return prepare_images(
        entries=entries,
        post=post,
        post_url=post_url,
        output_dir=args.output_dir,
        quality=args.quality,
        max_edge=args.max_edge,
        session=getattr(loader.context, "_session", None) or requests.Session(),
    )


def process_post(args: argparse.Namespace, loader: Instaloader, target: Target) -> int:
    print(f"Reading Instagram post {target.value}...", flush=True)
    post = load_post(loader, target.value)
    prepared = prepare_post(args, loader, post, target.url)
    if not prepared:
        return 0

    if args.no_eagle:
        print(f"Done. Compressed files are in {prepared[0].compressed_path.parent}")
        return len(prepared)

    eagle = EagleClient(args.eagle_url, token=args.eagle_token)
    eagle.check_running()
    folder_name = make_folder_name(post, args.folder)
    if args.folder_id:
        folder_id = args.folder_id
    else:
        folder_id = eagle.create_folder(folder_name, parent=args.parent_folder_id)
    eagle.add_from_paths(folder_id, build_eagle_items(prepared, post, args.tags))
    target = f"folder id `{folder_id}`" if args.folder_id else f"folder `{folder_name}`"
    print(f"Imported {len(prepared)} image(s) into Eagle {target}.")
    return len(prepared)


def process_profile(args: argparse.Namespace, loader: Instaloader, target: Target) -> int:
    username = target.value
    print(f"Reading Instagram profile @{username}...", flush=True)
    profile = load_profile(loader, username)

    eagle: Optional[EagleClient] = None
    folder_id: Optional[str] = None
    folder_name = safe_name(args.folder or f"Instagram {username}", fallback=username)

    if not args.no_eagle:
        eagle = EagleClient(args.eagle_url, token=args.eagle_token)
        eagle.check_running()
        folder_id = args.folder_id or eagle.create_folder(folder_name, parent=args.parent_folder_id)
        target_label = f"folder id `{folder_id}`" if args.folder_id else f"folder `{folder_name}`"
        print(f"Importing profile images into Eagle {target_label}.")

    total_images = 0
    total_posts = 0

    for post in profile.get_posts():
        if args.max_posts is not None and total_posts >= args.max_posts:
            break

        total_posts += 1
        post_url = post_url_from_shortcode(post.shortcode)
        print(f"Post {total_posts}: {post.shortcode}")
        prepared = prepare_post(args, loader, post, post_url)

        if not prepared:
            continue

        total_images += len(prepared)
        if eagle and folder_id:
            eagle.add_from_paths(folder_id, build_eagle_items(prepared, post, args.tags))
            print(f"Imported {len(prepared)} image(s) from {post.shortcode}.")

    if args.no_eagle:
        print(f"Done. Prepared {total_images} image(s) from {total_posts} post(s).")
    else:
        print(f"Imported {total_images} image(s) from {total_posts} post(s) into Eagle.")

    return total_images


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download images from Instagram posts or profiles, compress them, and import them into Eagle."
    )
    parser.add_argument("targets", nargs="+", help="Instagram post/reel URLs, profile URLs, @usernames, or shortcodes.")
    parser.add_argument("-f", "--folder", help="Eagle folder name. Defaults to owner/date/shortcode.")
    parser.add_argument(
        "--folder-id",
        help="Existing Eagle folder id to import into instead of creating a new folder.",
    )
    parser.add_argument(
        "--parent-folder-id",
        help="Optional Eagle parent folder id. New folders are created inside it.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads"),
        help="Local working folder for originals and compressed images.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=82,
        choices=range(1, 101),
        metavar="1-100",
        help="JPEG quality for compressed imports. Default: 82.",
    )
    parser.add_argument(
        "--max-edge",
        type=int,
        default=2048,
        help="Resize images so the longest edge is at most this many pixels. Use 0 to keep size.",
    )
    parser.add_argument(
        "--video-thumbnails",
        action="store_true",
        help="Import thumbnails for video/reel media instead of skipping them.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        help="For profile URLs, stop after this many posts. Omit to process every available post.",
    )
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=["instagram"],
        help="Tag to add in Eagle. Can be used more than once.",
    )
    parser.add_argument(
        "--login",
        default=DEFAULT_INSTAGRAM_USERNAME,
        help=f"Instagram username for private posts or rate-limit recovery. Default: {DEFAULT_INSTAGRAM_USERNAME}.",
    )
    parser.add_argument(
        "--no-login",
        action="store_true",
        help="Do not log into Instagram; try anonymous access only.",
    )
    parser.add_argument(
        "--load-cookies",
        metavar="BROWSER",
        help="Load your Instagram session from a browser, e.g. chrome, safari, firefox, brave, edge.",
    )
    parser.add_argument(
        "--cookiefile",
        help="Optional browser profile cookie file to use with --load-cookies.",
    )
    parser.add_argument(
        "--session-file",
        help="Optional Instaloader session file path used with --login.",
    )
    parser.add_argument(
        "--eagle-url",
        default="http://localhost:41595",
        help="Eagle API base URL. Default: http://localhost:41595.",
    )
    parser.add_argument(
        "--eagle-token",
        help="Eagle API token, only needed when calling Eagle over LAN.",
    )
    parser.add_argument(
        "--no-eagle",
        action="store_true",
        help="Only download/compress locally; do not import into Eagle.",
    )

    args = parser.parse_args(argv)
    if args.no_login:
        args.login = None
    if args.load_cookies:
        args.login = None
    if args.folder_id and args.parent_folder_id:
        parser.error("--folder-id cannot be combined with --parent-folder-id.")
    if args.max_posts is not None and args.max_posts < 1:
        parser.error("--max-posts must be at least 1.")
    if args.max_edge == 0:
        args.max_edge = None
    elif args.max_edge < 0:
        parser.error("--max-edge must be 0 or greater.")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    total = 0

    try:
        loader = build_loader(args.login, args.session_file, args.load_cookies, args.cookiefile)
        for source in args.targets:
            target = parse_target(source)
            if target.kind == "profile":
                total += process_profile(args, loader, target)
            else:
                total += process_post(args, loader, target)
    except (ValueError, RuntimeError, requests.RequestException, EagleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Finished. Prepared {total} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
