# Instagram to Eagle

Small CLI tool that downloads images from an Instagram post/carousel or profile, compresses the images, creates a folder in Eagle, and imports the compressed files.

Use this only for public posts or content you have permission to archive.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Open Eagle before running the importer. Eagle exposes a local API on `http://localhost:41595` when the app is running.

## Usage

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/p/SHORTCODE/"
```

Profile URLs work too:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/elizavetaporodina/" --max-posts 5
```

Useful options:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/p/SHORTCODE/" \
  --folder "My Eagle Folder" \
  --quality 82 \
  --max-edge 2048
```

To import into an existing Eagle folder instead of creating a new one, pass its folder id:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/p/SHORTCODE/" --folder-id EAGLE_FOLDER_ID
```

The tool logs in as `xibahbah` by default and prompts for the password when a saved session is not available.

If Instagram rejects password login, log into Instagram in your browser and use browser cookies:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/elizavetaporodina/" --max-posts 5 --load-cookies chrome
```

If Chrome stalls on macOS Keychain access, click Allow in the Keychain prompt or try Firefox:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/elizavetaporodina/" --max-posts 5 --load-cookies firefox
```

For a different Instagram account:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/p/SHORTCODE/" --login your_username
```

For anonymous access only:

```bash
python3 instagram_to_eagle.py "https://www.instagram.com/elizavetaporodina/" --max-posts 5 --no-login
```

The tool saves compressed images under `downloads/<owner>_<shortcode>/compressed/` before importing those local files into Eagle.

## Notes

- Carousel posts are supported.
- Profile URLs are supported. Omit `--max-posts` to process every available post, but start small first because large profiles can take a long time or trigger Instagram rate limits.
- Video/reel media is skipped by default. Add `--video-thumbnails` to import thumbnails for video items.
- Use `--no-eagle` to only download and compress locally.
- Use `--parent-folder-id` if you want the created Eagle folder inside an existing Eagle folder.
