#!/usr/bin/env python3
"""Bring the images (and PDFs) used by content files into WordPress, once, as WebP.

Pipeline:  content/pages/*.yaml + content/people.yaml
             -> collect media refs  (wix:<file> = static.wixstatic.com/media/<file>,
                                     wixdoc:<file> = $WIX_SITE_URL/_files/ugd/<file>)
             -> download originals  data/media/originals/   (gitignored archive)
             -> convert             data/media/webp/
             -> upload              WP REST /wp/v2/media  (+ alt text, _czs_wix_id meta)
             -> manifest            content/media-manifest.json  (wix ref -> WP id + URL)

Conversion rules ("Images in WebP"):
  photos (JPEG etc.)   WebP q82, longest edge <= 2560 px
  PNG with alpha       WebP with alpha; lossless when it is a small graphic (<= 1200 px)
  animated GIF         animated WebP
  SVG                  kept as SVG (the czs-blocks plugin sanitizes it on upload)
  EXIF/GPS stripped (orientation applied first); ICC color profile kept.

Idempotent: anything already in the manifest (and still present in WordPress) is skipped.
Secrets come from .secrets/wordpress.env and are never printed.
WIX_SITE_URL (the public Wix site, e.g. https://www.example.com) is read from the environment or
from .secrets/wordpress.env; it is only needed when content refers to wixdoc: files.

Usage:
  python scripts/wp_media.py                 # download + convert + upload what's missing
  python scripts/wp_media.py --dry-run       # download + convert only, report sizes
  python scripts/wp_media.py --pages home about
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml
from PIL import Image, ImageOps, ImageSequence

from wp_rest import SECRETS, UA, WP as BaseWP, load_env

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
PAGES = CONTENT / "pages"
PEOPLE = CONTENT / "people.yaml"
MANIFEST = CONTENT / "media-manifest.json"
ORIG_DIR = ROOT / "data" / "media" / "originals"
OUT_DIR = ROOT / "data" / "media" / "webp"

MAX_EDGE = 2560
QUALITY = 82
SMALL_GRAPHIC_EDGE = 1200
REF_RE = re.compile(r"^(wix|wixdoc):([A-Za-z0-9_~.\-]+)$")


# --------------------------------------------------------------------------- helpers

def source_url(ref: str) -> str:
    kind, name = REF_RE.match(ref).groups()  # type: ignore[union-attr]
    if kind == "wix":
        return "https://static.wixstatic.com/media/" + name
    return wix_site_url() + "/_files/ugd/" + name


def wix_site_url() -> str:
    url = os.environ.get("WIX_SITE_URL")
    if not url and SECRETS.exists():
        url = load_env(SECRETS).get("WIX_SITE_URL")
    if not url:
        sys.exit("Set WIX_SITE_URL (e.g. https://www.example.com) to resolve wixdoc: references")
    return url.rstrip("/")


def media_id(ref: str) -> str:
    """Wix media id without the ~mv2 suffix and extension, e.g. c1a0ba_ad5c...b48."""
    name = ref.split(":", 1)[1]
    return re.sub(r"(~mv2)?(\.[A-Za-z0-9]+)$", "", name)


def collect_refs(pages: list[str] | None) -> dict[str, dict]:
    """ref -> {alt, title, used_on} from every media-looking value in the content files."""
    refs: dict[str, dict] = {}

    def visit(node, where: str, ctx_alt: str | None = None, ctx_title: str | None = None):
        if isinstance(node, dict):
            src = node.get("src")
            if isinstance(src, str) and REF_RE.match(src):
                add(src, where, node.get("alt"), node.get("title"))
            for k, v in node.items():
                if k in ("fixes", "suggestions", "flags", "todo", "not_migrated", "seo"):
                    continue
                if k != "src":
                    visit(v, where)
        elif isinstance(node, list):
            for v in node:
                visit(v, where)
        elif isinstance(node, str) and REF_RE.match(node):
            add(node, where, None, None)

    def add(ref: str, where: str, alt, title):
        entry = refs.setdefault(ref, {"alt": None, "title": None, "used_on": []})
        if alt is not None and entry["alt"] is None:
            entry["alt"] = str(alt)
        if title and not entry["title"]:
            entry["title"] = str(title)
        if where not in entry["used_on"]:
            entry["used_on"].append(where)

    files = sorted(PAGES.glob("*.yaml"))
    if pages:
        files = [f for f in files if f.stem in pages]
    for f in files:
        visit(yaml.safe_load(f.read_text(encoding="utf-8")), f.stem)
    if PEOPLE.exists():  # shared bio photos (used by the People widget)
        visit(yaml.safe_load(PEOPLE.read_text(encoding="utf-8")), "people")
    return refs


def download(ref: str) -> Path:
    name = ref.split(":", 1)[1]
    dest = ORIG_DIR / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(source_url(ref), headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            break
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    dest.write_bytes(data)
    return dest


# --------------------------------------------------------------------------- conversion

def convert(src: Path, ref: str) -> tuple[Path, str, dict]:
    """Returns (output path, mime, info). Non-images (PDF, SVG) pass through unchanged."""
    ext = src.suffix.lower()
    base = media_id(ref)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if ext in (".svg", ".pdf"):
        out = OUT_DIR / (base + ext)
        out.write_bytes(src.read_bytes())
        mime = "image/svg+xml" if ext == ".svg" else "application/pdf"
        return out, mime, {"mode": "passthrough"}

    out = OUT_DIR / (base + ".webp")
    with Image.open(src) as im:
        icc = im.info.get("icc_profile")
        fmt = (im.format or "").upper()
        # Only GIF/APNG are real animations. Phone JPEGs saved as MPO also report
        # is_animated (main photo + a small preview frame): use the first frame.
        animated = fmt in ("GIF", "PNG") and getattr(im, "is_animated", False) and im.n_frames > 1
        if fmt == "MPO":
            im.seek(0)

        if animated:  # GIF (or APNG) -> animated WebP
            frames, durations = [], []
            for frame in ImageSequence.Iterator(im):
                f = frame.convert("RGBA")
                f = shrink(f)
                frames.append(f)
                durations.append(frame.info.get("duration", im.info.get("duration", 100)))
            frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:], duration=durations,
                           loop=im.info.get("loop", 0), quality=QUALITY, method=6)
            return out, "image/webp", {"mode": f"animated {len(frames)} frames", "size": frames[0].size}

        im = ImageOps.exif_transpose(im)  # apply camera rotation, then drop EXIF entirely
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        if has_alpha:
            im = im.convert("RGBA")
            if im.getextrema()[3][0] == 255:  # alpha channel present but fully opaque
                has_alpha = False
                im = im.convert("RGB")
        else:
            im = im.convert("RGB")
        im = shrink(im)

        kwargs: dict = {"method": 6}
        if icc:
            kwargs["icc_profile"] = icc  # keep color profile so paint colors stay true
        small_graphic = fmt == "PNG" and max(im.size) <= SMALL_GRAPHIC_EDGE
        if small_graphic:
            kwargs["lossless"] = True
            kwargs["quality"] = 100
            mode = "lossless (small graphic)"
        else:
            kwargs["quality"] = QUALITY
            if has_alpha:
                kwargs["alpha_quality"] = 100
            mode = f"lossy q{QUALITY}" + (" +alpha" if has_alpha else "")
        # No exif= argument: EXIF/GPS/XMP are not written.
        im.save(out, "WEBP", **kwargs)
        return out, "image/webp", {"mode": mode, "size": im.size}


def shrink(im: Image.Image) -> Image.Image:
    if max(im.size) <= MAX_EDGE:
        return im
    im = im.copy()
    im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    return im


# --------------------------------------------------------------------------- WordPress REST

class WP(BaseWP):
    """REST client plus the media-specific calls."""

    def exists(self, att_id: int) -> bool:
        status, _ = self.request("GET", f"/media/{att_id}?context=edit&_fields=id")
        return status == 200

    def find_by_slug(self, slug: str):
        status, body = self.request("GET", f"/media?slug={urllib.parse.quote(slug)}&context=edit&_fields=id,source_url,slug")
        if status == 200 and body:
            return body[0]
        return None

    def upload(self, path: Path, mime: str, slug: str, alt: str, title: str, wix_id: str) -> dict:
        status, body = self.request(
            "POST", "/media", data=path.read_bytes(),
            headers={"Content-Type": mime, "Content-Disposition": f'attachment; filename="{path.name}"'},
            timeout=300)
        if status not in (200, 201):
            raise RuntimeError(f"upload failed for {path.name}: HTTP {status} {body}")
        att_id = body["id"]
        meta_status, meta = self.request("POST", f"/media/{att_id}", json_body={
            "slug": slug, "alt_text": alt, "title": title, "meta": {"_czs_wix_id": wix_id}})
        if meta_status != 200:
            raise RuntimeError(f"metadata update failed for {att_id}: HTTP {meta_status} {meta}")
        return meta


def slug_for(ref: str) -> str:
    return "wix-" + re.sub(r"[^a-z0-9]+", "-", media_id(ref).lower()).strip("-")


def human_title(ref: str, info: dict) -> str:
    return info.get("title") or info.get("alt") or media_id(ref)


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", nargs="*", help="only these page slugs (default: all content files)")
    ap.add_argument("--dry-run", action="store_true", help="download and convert, but do not upload")
    ap.add_argument("--no-verify", action="store_true", help="trust the manifest without checking WordPress")
    args = ap.parse_args()

    refs = collect_refs(args.pages)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    wp = None if args.dry_run else WP()

    tot_orig = tot_out = 0
    counts = {"uploaded": 0, "skipped": 0, "converted": 0}
    print(f"{len(refs)} media refs in content files")
    for ref, info in sorted(refs.items()):
        src = download(ref)
        out, mime, conv = convert(src, ref)
        o_size, n_size = src.stat().st_size, out.stat().st_size
        tot_orig += o_size
        tot_out += n_size
        counts["converted"] += 1
        alt = info["alt"] if info["alt"] is not None else ""
        line = f"  {media_id(ref)[:40]:40} {o_size/1024:8.0f} KB -> {n_size/1024:7.0f} KB  {conv['mode']}"

        entry = manifest.get(ref)
        if entry and wp and (args.no_verify or wp.exists(entry["wp_id"])):
            # Keep alt text in sync with the content file (cheap, idempotent).
            if entry.get("alt") != alt:
                wp.request("POST", f"/media/{entry['wp_id']}", json_body={"alt_text": alt})
                entry["alt"] = alt
            entry["used_on"] = info["used_on"]
            counts["skipped"] += 1
            print(line + "  [already uploaded]")
            continue
        if not wp:
            print(line + "  [dry run]")
            continue

        slug = slug_for(ref)
        found = wp.find_by_slug(slug)  # manifest lost but the file is in WordPress
        if found:
            att = {"id": found["id"], "source_url": found["source_url"]}
            wp.request("POST", f"/media/{found['id']}", json_body={"alt_text": alt})
            note = "[re-linked existing]"
        else:
            att = wp.upload(out, mime, slug, alt, human_title(ref, info), media_id(ref))
            counts["uploaded"] += 1
            note = f"[uploaded #{att['id']}]"
        manifest[ref] = {
            "wix_id": media_id(ref),
            "source": source_url(ref),
            "wp_id": att["id"],
            "url": att["source_url"],
            "mime": mime,
            "alt": alt,
            "original_bytes": o_size,
            "converted_bytes": n_size,
            "conversion": conv["mode"],
            "used_on": info["used_on"],
        }
        MANIFEST.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n", encoding="utf-8")
        print(line + "  " + note)

    if wp:
        MANIFEST.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n", encoding="utf-8")
    saved = (1 - tot_out / tot_orig) * 100 if tot_orig else 0
    print(f"\n{counts['converted']} files: {tot_orig/1024:,.0f} KB original -> {tot_out/1024:,.0f} KB converted "
          f"({saved:.0f}% smaller). uploaded {counts['uploaded']}, already there {counts['skipped']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
