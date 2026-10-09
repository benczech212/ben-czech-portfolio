#!/usr/bin/env python3
"""Build WordPress draft pages from content files (content/pages/<slug>.yaml).

Each section becomes an Elementor container holding either one Czech Studios widget
(czs-hero, czs-split, czs-card-grid, czs-people, czs-cta-band, czs-quote, ...) or core
Elementor widgets (gallery, columns of heading/text/placeholder). Images and PDFs are
resolved through content/media-manifest.json (run scripts/wp_media.py first).

Idempotent: pages are matched by slug and updated in place, never duplicated. New pages
are created as drafts; an existing page keeps its status (the build never publishes).
Shared bios in content/people.yaml are upserted into "People (bios)" first.

Usage:
  python scripts/wp_build.py                    # people + all pages + flush Elementor CSS
  python scripts/wp_build.py home about         # only these pages
  python scripts/wp_build.py --dry-run home     # print the Elementor JSON, change nothing
  python scripts/wp_build.py --skip-people      # once the site owner edits bios in WordPress
  python scripts/wp_build.py --menu             # also ensure a "Main" menu with the built pages
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

import yaml

from wp_rest import WP, vm_wp

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "content" / "pages"
PEOPLE = ROOT / "content" / "people.yaml"
MANIFEST = ROOT / "content" / "media-manifest.json"
REF_RE = re.compile(r"^(wix|wixdoc):([A-Za-z0-9_~.\-]+)$")
OUR_WIDGETS = {"czs-hero", "czs-split", "czs-card-grid", "czs-offering-card", "czs-people",
               "czs-cta-band", "czs-quote", "czs-latest-posts", "czs-event-list"}
SCHEME_BG = {"light": "czswhite", "tint": "czsmist", "dark": "primary", "navy": "czsnavy", "forest": "czsforest"}
ZERO_BOX = {"unit": "px", "top": "0", "right": "0", "bottom": "0", "left": "0", "isLinked": True}


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------- helpers

class Ids:
    """Deterministic 7-char Elementor ids, so rebuilding yields the same structure."""

    def __init__(self, slug: str):
        self.slug, self.seen = slug, set()

    def __call__(self, path: str) -> str:
        i = hashlib.sha1(f"{self.slug}/{path}".encode()).hexdigest()[:7]
        while i in self.seen:
            i = hashlib.sha1(i.encode()).hexdigest()[:7]
        self.seen.add(i)
        return i


def box(top, right, bottom, left, unit="px"):
    return {"unit": unit, "top": str(top), "right": str(right), "bottom": str(bottom), "left": str(left),
            "isLinked": False}


def gap(size: int):
    return {"size": size, "column": str(size), "row": str(size), "unit": "px", "isLinked": True}


class Media:
    def __init__(self):
        self.m = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}

    def entry(self, ref: str) -> dict:
        if ref not in self.m:
            raise BuildError(f"{ref} is not in content/media-manifest.json - run scripts/wp_media.py first")
        return self.m[ref]

    def image(self, value) -> dict:
        """yaml image (str ref or {src, alt}) -> Elementor MEDIA control value."""
        if not value:
            return {"url": "", "id": ""}
        ref = value["src"] if isinstance(value, dict) else value
        e = self.entry(ref)
        alt = value.get("alt", e.get("alt", "")) if isinstance(value, dict) else e.get("alt", "")
        return {"url": e["url"], "id": e["wp_id"], "alt": alt, "source": "library", "size": ""}

    def href(self, href: str) -> str:
        href = (href or "").strip()
        if REF_RE.match(href):
            return self.entry(href)["url"]
        return href

    def link(self, href: str, new_tab: bool = False) -> dict:
        return {"url": self.href(href), "is_external": "on" if new_tab else "", "nofollow": "",
                "custom_attributes": ""}


# --------------------------------------------------------------------------- section renderers

def outer_container(ids: Ids, path: str, children: list, extra: dict | None = None) -> dict:
    """Full-width, zero-padding wrapper: our widgets draw their own band and inner width."""
    settings = {"content_width": "full", "padding": ZERO_BOX, "margin": ZERO_BOX, "flex_gap": gap(0),
                "_title": path}
    if extra:
        settings.update(extra)
    return {"id": ids(path), "elType": "container", "isInner": False, "settings": settings,
            "elements": children}


def widget(ids: Ids, path: str, widget_type: str, settings: dict) -> dict:
    return {"id": ids(path + "/w"), "elType": "widget", "widgetType": widget_type, "settings": settings,
            "elements": []}


def buttons_to_settings(buttons: list | None, media: Media, prefix: str = "button") -> dict:
    out = {}
    for i, b in enumerate((buttons or [])[:2]):
        key = prefix if i == 0 else prefix + "2"
        out[key + "_text"] = b["label"]
        out[key + "_link"] = media.link(b["href"], bool(b.get("new_tab")))
    return out


def czs_settings(sec: dict, media: Media, ids: Ids, path: str) -> dict:
    """Map a content-file section onto a czs-* widget's control values (1:1 field names)."""
    w = sec["widget"]
    s: dict = {}
    for key in ("eyebrow", "heading", "heading_tag", "intro", "body", "text", "layout", "scheme",
                "image_side", "image_shape", "columns", "card_title_tag", "quote", "name", "role",
                "title", "format", "blurb", "price", "category", "count", "empty_text"):
        if key in sec and sec[key] is not None:
            s[key] = str(sec[key]) if not isinstance(sec[key], (list, dict)) else sec[key]
    if "image" in sec:
        s["image"] = media.image(sec["image"])
    s.update(buttons_to_settings(sec.get("buttons"), media))

    if w == "czs-card-grid":
        cards = []
        for i, c in enumerate(sec.get("cards", [])):
            item = {"_id": ids(f"{path}/card{i}"), "title": c.get("title", ""), "text": c.get("text", ""),
                    "image": media.image(c.get("image")), "link_text": "", "link": media.link("")}
            if c.get("link"):
                item["link_text"] = c["link"]["label"]
                item["link"] = media.link(c["link"]["href"], bool(c["link"].get("new_tab")))
            cards.append(item)
        s["cards"] = cards
    elif w == "czs-people":
        s["people"] = [str(p) for p in sec.get("people", [])]
        s["show_bio"] = "yes" if sec.get("show_bio", True) else ""
        s["show_links"] = "yes" if sec.get("show_links", True) else ""
    elif w == "czs-event-list":
        s["events"] = [{"_id": ids(f"{path}/ev{i}"), "date": e.get("date", ""), "title": e.get("title", ""),
                        "location": e.get("location", ""), "text": e.get("text", ""),
                        "link_text": e.get("link_text", "Details"), "link": media.link(e.get("href", ""))}
                       for i, e in enumerate(sec.get("events", []))]
    return s


def band_container(ids: Ids, path: str, scheme: str, children: list, extra: dict | None = None) -> dict:
    """Boxed core-widget band with the scheme's background (for gallery / columns sections)."""
    settings = {
        "content_width": "boxed",
        "boxed_width": {"unit": "px", "size": 1180, "sizes": []},
        "padding": box(80, 24, 80, 24),
        "padding_mobile": box(48, 16, 48, 16),
        "margin": ZERO_BOX,
        "background_background": "classic",
        "__globals__": {"background_color": f"globals/colors?id={SCHEME_BG.get(scheme, 'czswhite')}"},
        "_title": path,
    }
    if extra:
        settings.update(extra)
    return {"id": ids(path), "elType": "container", "isInner": False, "settings": settings,
            "elements": children}


def core_item(item: dict, ids: Ids, path: str) -> dict:
    kind = item["widget"]
    if kind == "heading":
        return widget(ids, path, "heading", {"title": item["text"], "header_size": item.get("tag", "h2")})
    if kind == "text":
        return widget(ids, path, "text-editor", {"editor": item["html"].strip()})
    if kind == "placeholder":
        inner = [
            widget(ids, path + "/h", "heading", {"title": item.get("heading", "Placeholder"),
                                                 "header_size": item.get("tag", "h2")}),
            widget(ids, path + "/t", "text-editor", {"editor": "<p>" + html.escape(item.get("text", "")) + "</p>"}),
        ]
        return {"id": ids(path), "elType": "container", "isInner": True, "elements": inner, "settings": {
            "content_width": "full", "flex_direction": "column",
            "padding": box(32, 32, 32, 32), "padding_mobile": box(24, 20, 24, 20),
            "border_border": "dashed", "border_width": box(2, 2, 2, 2),
            "border_radius": box(16, 16, 16, 16),
            "background_background": "classic",
            "__globals__": {"border_color": "globals/colors?id=czsgraymid",
                            "background_color": "globals/colors?id=czsmist"},
            "_title": "Placeholder (replace before publishing)"}}
    raise BuildError(f"unknown core item widget '{kind}' at {path}")


def render_section(sec: dict, i: int, media: Media, ids: Ids) -> dict:
    w = sec.get("widget", "")
    path = f"s{i}-{w}"
    if w.startswith("cs-"):  # plan draft used cs-*; the plugin names are czs-*
        w = "czs-" + w[3:]
        sec = dict(sec, widget=w)
    if w in OUR_WIDGETS:
        return outer_container(ids, path, [widget(ids, path, w, czs_settings(sec, media, ids, path))])

    if w == "gallery":
        images = [media.image(im) for im in sec.get("images", [])]
        children = []
        if sec.get("label"):  # visually hidden heading = accessible name for the gallery
            children.append(widget(ids, path + "/label", "heading", {
                "title": sec["label"], "header_size": "h2", "_css_classes": "screen-reader-text"}))
        cols = str(sec.get("columns", 6))
        children.append(widget(ids, path, "image-gallery", {
            "wp_gallery": [{"id": im["id"], "url": im["url"]} for im in images],
            "thumbnail_size": "medium",
            "gallery_columns": cols,
            "gallery_columns_tablet": "5",
            "gallery_columns_mobile": "3",
            "gallery_link": "file",
            "open_lightbox": "yes",
            "gallery_display_caption": "none",
            "image_spacing": "custom",
            "image_spacing_custom": {"unit": "px", "size": 10, "sizes": []},
            "image_border_radius": box(8, 8, 8, 8),
        }))
        return band_container(ids, path, sec.get("scheme", "light"), children,
                              {"flex_direction": "column", "flex_gap": gap(0)})

    if w == "columns":
        cols = sec.get("columns", [])
        n = max(1, len(cols))
        kids = []
        for ci, col in enumerate(cols):
            cpath = f"{path}/c{ci}"
            last = ci == n - 1
            settings = {
                "content_width": "full", "flex_direction": "column",
                "width": {"unit": "%", "size": round(100 / n, 4), "sizes": []},
                "width_mobile": {"unit": "%", "size": 100, "sizes": []},
                "padding": box(0, 0 if last else 48, 0, 0),
                "padding_mobile": ZERO_BOX,
                "flex_gap": gap(16),
                "_title": f"Column {ci + 1}",
            }
            items = [core_item(it, ids, f"{cpath}/i{j}") for j, it in enumerate(col)]
            kids.append({"id": ids(cpath), "elType": "container", "isInner": True, "settings": settings,
                         "elements": items})
        return band_container(ids, path, sec.get("scheme", "light"), kids, {
            "flex_direction": "row", "flex_direction_mobile": "column", "flex_align_items": "flex-start",
            "flex_gap": gap(0), "flex_gap_mobile": gap(32)})

    raise BuildError(f"unknown widget '{w}' in section {i}")


def fallback_html(page: dict) -> str:
    """Plain HTML copy of the page for post_content (search, excerpts, non-Elementor views)."""
    out = []
    for sec in page.get("sections", []):
        for key in ("eyebrow", "heading"):
            if sec.get(key):
                out.append(f"<h2>{html.escape(str(sec[key]))}</h2>" if key == "heading"
                           else f"<p>{html.escape(str(sec[key]))}</p>")
        for key in ("intro", "text"):
            if sec.get(key):
                out += [f"<p>{html.escape(p.strip())}</p>" for p in str(sec[key]).split("\n\n") if p.strip()]
        if sec.get("body"):
            out.append(str(sec["body"]).strip())
        for c in sec.get("cards", []) or []:
            out.append(f"<h3>{html.escape(c.get('title', ''))}</h3><p>{html.escape(c.get('text', ''))}</p>")
    return "\n".join(out)


# --------------------------------------------------------------------------- WordPress

def elementor_version(wp: WP) -> str:
    status, body = wp.get("/plugins?search=elementor&_fields=plugin,version,status")
    if status == 200:
        for p in body or []:
            if p["plugin"].startswith("elementor/") and p["status"] == "active":
                return p["version"]
    raise BuildError(f"could not read the Elementor version (HTTP {status})")


def upsert_people(wp: WP, media: Media) -> None:
    data = yaml.safe_load(PEOPLE.read_text(encoding="utf-8"))
    for p in data.get("people", []):
        links = p.get("links") or {}
        body = {
            "title": p["name"],
            "slug": p["slug"],
            "status": "publish",  # non-public post type: "publish" only makes it selectable in widgets
            "content": "".join(f"<p>{html.escape(x.strip())}</p>" for x in str(p["bio"]).split("\n\n") if x.strip()),
            "menu_order": int(p.get("order", 0)),
            "featured_media": media.entry(p["photo"]["src"])["wp_id"] if p.get("photo") else 0,
            "meta": {
                "_czs_person_role": p.get("role", ""),
                "_czs_person_profile_label": (p.get("profile") or {}).get("label", ""),
                "_czs_person_profile_url": (p.get("profile") or {}).get("href", ""),
                "_czs_person_instagram": links.get("instagram", ""),
                "_czs_person_facebook": links.get("facebook", ""),
                "_czs_person_pinterest": links.get("pinterest", ""),
                "_czs_person_website": links.get("website", ""),
            },
        }
        status, found = wp.get(f"/czs-people?slug={p['slug']}&status=any&context=edit&_fields=id")
        if status != 200:
            raise BuildError(f"People endpoint unavailable (HTTP {status}) - is czs-blocks active?")
        if found:
            status, res = wp.post(f"/czs-people/{found[0]['id']}", body)
            verb = "updated"
        else:
            status, res = wp.post("/czs-people", body)
            verb = "created"
        if status not in (200, 201):
            raise BuildError(f"person {p['slug']}: HTTP {status} {res}")
        print(f"  person {p['slug']:<6} {verb} #{res['id']}")


def find_page(wp: WP, slug: str):
    status, found = wp.get(f"/pages?slug={slug}&status=publish,draft,pending,private,future&context=edit&_fields=id,status,link")
    if status != 200:
        raise BuildError(f"page lookup failed for {slug}: HTTP {status}")
    return found[0] if found else None


def build_page(wp: WP | None, path: Path, media: Media, version: str, dry: bool) -> dict | None:
    page = yaml.safe_load(path.read_text(encoding="utf-8"))
    slug = page["slug"]
    ids = Ids(slug)
    data = [render_section(sec, i, media, ids) for i, sec in enumerate(page.get("sections", []))]
    if dry:
        print(json.dumps(data, indent=1, ensure_ascii=False))
        return None

    body = {
        "title": page["title"],
        "slug": slug,
        "template": page.get("template", "elementor_header_footer"),
        "content": fallback_html(page),
        "meta": {
            "_elementor_data": json.dumps(data, ensure_ascii=False),
            "_elementor_edit_mode": "builder",
            "_elementor_template_type": "wp-page",
            "_elementor_version": version,
            "_elementor_page_settings": {"hide_title": "yes"},
        },
    }
    existing = find_page(wp, slug)
    if existing:
        status, res = wp.post(f"/pages/{existing['id']}", body)  # status untouched
        verb = "updated"
    else:
        body["status"] = "draft"
        status, res = wp.post("/pages", body)
        verb = "created"
    if status not in (200, 201):
        raise BuildError(f"{slug}: HTTP {status} {res}")
    saved = res.get("meta", {}).get("_elementor_data", "")
    if saved and json.loads(saved) != data:
        raise BuildError(f"{slug}: _elementor_data came back different from what was sent (sanitized?)")
    print(f"  page {slug:<8} {verb} #{res['id']} ({res['status']}), {len(data)} sections")
    return {"id": res["id"], "slug": slug, "title": page["title"], "status": res["status"]}


def ensure_menu(wp: WP, pages: list[dict]) -> None:
    status, menus = wp.get("/menus?context=edit&_fields=id,name,locations")
    if status != 200:
        print(f"  menu: skipped (HTTP {status})")
        return
    menu = next((m for m in menus if m["name"] == "Main"), None)
    if not menu:
        status, menu = wp.post("/menus", {"name": "Main", "locations": ["menu-1"]})
        if status not in (200, 201):
            print(f"  menu: could not create (HTTP {status})")
            return
    status, items = wp.get(f"/menu-items?menus={menu['id']}&per_page=100&context=edit&_fields=id,object_id")
    have = {it["object_id"] for it in items or []}
    order = {"home": 1, "about": 2, "contact": 3}
    for p in sorted(pages, key=lambda p: order.get(p["slug"], 99)):
        if p["id"] in have:
            continue
        wp.post("/menu-items", {"title": p["title"], "menus": menu["id"], "type": "post_type", "object": "page",
                                "object_id": p["id"], "status": "publish", "menu_order": order.get(p["slug"], 99)})
    print(f"  menu 'Main' (#{menu['id']}, header location) has {len(pages)} pages")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", nargs="*", help="page slugs (default: every content/pages/*.yaml)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-people", action="store_true")
    ap.add_argument("--no-flush", action="store_true", help="skip wp elementor flush_css on the VM")
    ap.add_argument("--menu", action="store_true", help="ensure a 'Main' header menu with the built pages")
    args = ap.parse_args()

    files = sorted(PAGES.glob("*.yaml"))
    if args.pages:
        files = [f for f in files if f.stem in args.pages]
        missing = set(args.pages) - {f.stem for f in files}
        if missing:
            print(f"no content file for: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    media = Media()
    try:
        if args.dry_run:
            for f in files:
                build_page(None, f, media, "dry-run", True)
            return 0
        wp = WP()
        version = elementor_version(wp)
        print(f"Elementor {version} on {wp.site}")
        if not args.skip_people and PEOPLE.exists():
            upsert_people(wp, media)
        built = [b for f in files if (b := build_page(wp, f, media, version, False))]
        if args.menu:
            ensure_menu(wp, built)
    except BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not args.no_flush:
        print("  " + (vm_wp("elementor", "flush_css").strip().splitlines() or ["(no output)"])[-1])
    for b in built:
        print(f"  preview: {wp.site}/?page_id={b['id']}&preview=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
