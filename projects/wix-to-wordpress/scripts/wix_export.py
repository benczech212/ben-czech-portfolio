"""Read-only export of a business's Wix sites via the Wix REST API.

Usage:  python scripts/wix_export.py [--site live|dev|all]

The live site's output folder is named by WIX_SITE_SLUG (environment; default "live-site").

Reads WIX_API_KEY and WIX_SITE_ID from .secrets/wix.env (never printed or written).
Only GET requests and POST query/list/search/count endpoints are used -- nothing
is created, updated, deleted, published or sent.

Output: data/wix-export/<site-slug>/*.json plus _export-log.json per site.

Privacy: contacts, members, orders (stores/eCom/events/pricing plans), bookings,
event guests/RSVPs, form submissions and person-list CMS collections are reduced
to counts / schema / date ranges / aggregates. Public-profile CMS collections are
saved with email/phone/address-type fields stripped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".secrets" / "wix.env"
OUT_ROOT = ROOT / "data" / "wix-export"
BASE = "https://www.wixapis.com"
DEV_SITE_NAME = "Dev site 1 (Stores V3)"

# POST endpoints must look like read operations.
READ_POST = re.compile(r"/(query|search|count|query-variants)$")


def load_env() -> dict:
    cfg = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


CFG = load_env()
SESSION = requests.Session()


class Api:
    def __init__(self, site_id: str | None):
        self.site_id = site_id

    def call(self, method: str, path: str, body: dict | None = None, params=None):
        if method == "POST":
            if not READ_POST.search(path.split("?")[0]):   # raise, not assert: python -O skips asserts
                raise ValueError(f"refusing non-read POST {path}")
        elif method != "GET":
            raise ValueError("read-only exporter")
        h = {"Authorization": CFG["WIX_API_KEY"], "Content-Type": "application/json"}
        if self.site_id:
            h["wix-site-id"] = self.site_id
        for attempt in range(4):
            r = SESSION.request(method, BASE + path, headers=h, json=body, params=params, timeout=90)
            if r.status_code == 429 or r.status_code >= 500 and r.status_code != 501:
                time.sleep(2 ** attempt)
                continue
            break
        try:
            data = r.json()
        except Exception:
            data = {"_raw": r.text[:300]}
        return r.status_code, data


def err_msg(data) -> str:
    if isinstance(data, dict):
        return str(data.get("message") or data.get("_raw") or "")[:200]
    return ""


class HttpError(Exception):
    def __init__(self, status, msg):
        super().__init__(f"{status} {msg}")
        self.status, self.msg = status, msg


# ---------------------------------------------------------------- paging helpers

def page_offset(api, method, path, key, body_fn=None, params_fn=None, limit=100, max_pages=200):
    """Offset paging. body_fn(limit, offset) -> body; params_fn(limit, offset) -> params."""
    out, offset = [], 0
    for _ in range(max_pages):
        body = body_fn(limit, offset) if body_fn else None
        params = params_fn(limit, offset) if params_fn else None
        st, data = api.call(method, path, body, params)
        if st != 200:
            raise HttpError(st, err_msg(data))
        items = data.get(key) or []
        out.extend(items)
        if len(items) < limit:
            break
        offset += len(items)
    return out, data


def find_next_cursor(data):
    for k in ("pagingMetadata", "metadata", "metaData", "nextCursor", "pagingMetadataV2"):
        m = data.get(k)
        if isinstance(m, dict):
            c = m.get("cursors") or {}
            if c.get("next"):
                return c["next"]
    return None


def page_cursor(api, method, path, key, body_fn=None, params_fn=None, limit=100, max_pages=500):
    """Cursor paging. body_fn(limit, cursor) / params_fn(limit, cursor)."""
    out, cursor = [], None
    for _ in range(max_pages):
        body = body_fn(limit, cursor) if body_fn else None
        params = params_fn(limit, cursor) if params_fn else None
        st, data = api.call(method, path, body, params)
        if st != 200:
            raise HttpError(st, err_msg(data))
        items = data.get(key) or []
        out.extend(items)
        cursor = find_next_cursor(data)
        if not cursor or not items:
            break
    return out, data


def cq(limit, cursor, extra=None):
    q = {"cursorPaging": {"limit": limit}}
    if cursor:
        q["cursorPaging"]["cursor"] = cursor
    body = {"query": q}
    if extra:
        body.update(extra)
    return body


def oq(limit, offset, extra=None):
    body = {"query": {"paging": {"limit": limit, "offset": offset}}}
    if extra:
        body.update(extra)
    return body


# ---------------------------------------------------------------- privacy helpers

PII_KEY = re.compile(
    r"(e-?mail|phone|mobile|address|street|zip|postal|birth|dob|loginemail|contactid|wixmemberid)",
    re.I)


def strip_pii(obj, keep: set[str] = frozenset()):
    """Recursively drop keys that look like email/phone/address fields."""
    if isinstance(obj, dict):
        return {k: strip_pii(v, keep) for k, v in obj.items()
                if k in keep or not PII_KEY.search(k)}
    if isinstance(obj, list):
        return [strip_pii(v, keep) for v in obj]
    return obj


def date_range(values):
    vals = sorted(v for v in values if v)
    return {"earliest": vals[0], "latest": vals[-1]} if vals else None


def get_path(d, dotted, default=None):
    cur = d
    for p in dotted.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def money_by_year(items, date_key, amount_key, currency_key=None):
    agg = defaultdict(lambda: {"count": 0, "total": 0.0})
    currencies = Counter()
    for it in items:
        d = get_path(it, date_key) or ""
        y = d[:4] or "unknown"
        agg[y]["count"] += 1
        agg[y]["total"] = round(agg[y]["total"] + to_float(get_path(it, amount_key)), 2)
        if currency_key:
            currencies[get_path(it, currency_key) or "?"] += 1
    return {"byYear": dict(sorted(agg.items())), "currencies": dict(currencies)}


def schema_of(items, limit_depth=1):
    keys = Counter()
    for it in items:
        if isinstance(it, dict):
            keys.update(it.keys())
    return sorted(keys)


# ---------------------------------------------------------------- exporter

class Exporter:
    def __init__(self, slug: str, site_id: str):
        self.slug = slug
        self.api = Api(site_id)
        self.out = OUT_ROOT / slug
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = []

    def save(self, rel: str, payload):
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return rel

    def run(self, name, endpoint, fn):
        """fn() -> (count, file_or_None, note)"""
        try:
            count, file, note = fn()
            entry = {"resource": name, "endpoint": endpoint, "status": 200, "count": count,
                     "file": file, "note": note}
        except HttpError as e:
            entry = {"resource": name, "endpoint": endpoint, "status": e.status, "count": None,
                     "file": None, "note": e.msg}
        except Exception as e:  # noqa
            entry = {"resource": name, "endpoint": endpoint, "status": "error", "count": None,
                     "file": None, "note": f"{type(e).__name__}: {e}"[:300]}
        self.log.append(entry)
        print(f"  [{entry['status']}] {name}: {entry['count']}  {entry['note'] or ''}"[:200])
        return entry

    # simple helper: fetch list + save whole
    def simple(self, name, method, path, key, file, pager="cursor", body_extra=None,
               params_fn=None, meta_note=None, transform=None):
        def fn():
            if params_fn:
                if pager == "offset":
                    items, _ = page_offset(self.api, method, path, key, params_fn=params_fn)
                elif pager == "cursor":
                    items, _ = page_cursor(self.api, method, path, key, params_fn=params_fn)
                else:
                    st, data = self.api.call(method, path, params=params_fn(100, None))
                    if st != 200:
                        raise HttpError(st, err_msg(data))
                    items = data.get(key) or []
            elif method == "GET":
                st, data = self.api.call(method, path)
                if st != 200:
                    raise HttpError(st, err_msg(data))
                items = data.get(key) if key else data
            elif pager == "offset":
                items, _ = page_offset(self.api, method, path, key,
                                       body_fn=lambda l, o: oq(l, o, body_extra))
            else:
                items, _ = page_cursor(self.api, method, path, key,
                                       body_fn=lambda l, c: cq(l, c, body_extra))
            if transform:
                items = transform(items)
            n = len(items) if isinstance(items, list) else (1 if items else 0)
            return n, self.save(file, items), meta_note
        return self.run(name, f"{method} {path}", fn)

    # ------------------------------------------------------------ resources

    def site_info(self):
        self.simple("site-properties", "GET", "/site-properties/v4/properties", None, "site-properties.json")
        self.simple("locations", "GET", "/locations/v1/locations", "locations", "locations.json")
        self.simple("seo-site-tags", "GET", "/seo-metatags-server/v1/site-seo-tags", "siteSeoTags",
                    "seo-site-tags.json")
        self.simple("seo-redirects", "GET", "/seo-redirects-service/v1/redirects", "redirects",
                    "seo-redirects.json")
        self.simple("marketing-tags", "GET", "/marketing/v1/tags", "tags", "marketing-tags.json")
        self.simple("contact-labels", "GET", "/contacts/v4/labels", "labels", "contact-labels.json",
                    pager="offset", params_fn=lambda l, o: {"paging.limit": l, "paging.offset": o},
                    meta_note="label names only (no contacts)")

    def stores(self):
        st, data = self.api.call("GET", "/stores/v3/provision/version")
        version = data.get("catalogVersion") if st == 200 else None
        self.log.append({"resource": "stores-catalog-version", "endpoint": "GET /stores/v3/provision/version",
                         "status": st, "count": None, "file": None, "note": version})
        print(f"  catalog version: {version}")
        if version == "V3_CATALOG":
            fields = ["URL", "CURRENCY", "INFO_SECTION", "PLAIN_DESCRIPTION", "INFO_SECTION_PLAIN_DESCRIPTION",
                      "SUBSCRIPTION_PRICES_INFO", "BREADCRUMBS_INFO", "WEIGHT_MEASUREMENT_UNIT_INFO",
                      "VARIANT_OPTION_CHOICE_NAMES", "MEDIA_ITEMS_INFO", "DESCRIPTION",
                      "DIRECT_CATEGORIES_INFO", "ALL_CATEGORIES_INFO", "INFO_SECTION_DESCRIPTION", "THUMBNAIL"]
            self.simple("products (v3)", "POST", "/stores/v3/products/query", "products", "products.json",
                        body_extra={"fields": fields})
            self.simple("product-variants (v3)", "POST", "/stores/v3/products/query-variants", "variants",
                        "product-variants.json")
            self.simple("inventory-items (v3)", "POST", "/stores/v3/inventory-items/query", "inventoryItems",
                        "inventory-items.json")
            self.simple("store-categories (v3)", "POST", "/categories/v1/categories/query", "categories",
                        "store-categories.json",
                        body_extra={"treeReference": {"appNamespace": "@wix/stores"}})
            for res, key in (("ribbons", "ribbons"), ("brands", "brands"),
                             ("info-sections", "infoSections"), ("customizations", "customizations")):
                self.simple(f"{res} (v3)", "POST", f"/stores/v3/{res}/query", key, f"store-{res}.json")
        else:
            self.simple("products (v1)", "POST", "/stores/v1/products/query", "products", "products.json",
                        pager="offset", body_extra={"includeVariants": True})
            self.simple("store-collections (v1)", "POST", "/stores/v1/collections/query", "collections",
                        "store-collections.json", pager="offset")
            self.simple("inventory-items (v2)", "POST", "/stores/v2/inventoryItems/query", "inventoryItems",
                        "inventory-items.json", pager="offset")
        self.simple("coupons", "POST", "/stores/v2/coupons/query", "coupons", "coupons.json", pager="offset")
        self.simple("discount-rules", "POST", "/ecom/v1/discount-rules/query", "discountRules",
                    "discount-rules.json")
        self.store_orders_summary()

    def store_orders_summary(self):
        summary = {"_privacy": "aggregates only; no buyer data saved"}

        def v2():
            items, _ = page_offset(self.api, "POST", "/stores/v2/orders/query", "orders",
                                   body_fn=lambda l, o: oq(l, o))
            summary["storesV2Orders"] = {
                "count": len(items),
                "dateRange": date_range([o.get("dateCreated") for o in items]),
                "revenue": money_by_year(items, "dateCreated", "totals.total", "currency"),
                "paymentStatus": dict(Counter(o.get("paymentStatus") for o in items)),
                "fulfillmentStatus": dict(Counter(o.get("fulfillmentStatus") for o in items)),
                "productsSold": dict(Counter(li.get("name") for o in items for li in o.get("lineItems", []))),
                "orderFieldNames": schema_of(items),
            }
            return len(items), None, "aggregated into orders-summary.json"

        def ecom():
            items, _ = page_cursor(self.api, "POST", "/ecom/v1/orders/search", "orders",
                                   body_fn=lambda l, c: {"search": {"cursorPaging": {"limit": l, **({"cursor": c} if c else {})}}})
            summary["ecomOrders"] = {
                "count": len(items),
                "dateRange": date_range([o.get("createdDate") for o in items]),
                "revenue": money_by_year(items, "createdDate", "priceSummary.total.amount", "currency"),
                "paymentStatus": dict(Counter(o.get("paymentStatus") for o in items)),
                "channel": dict(Counter(get_path(o, "channelInfo.type") for o in items)),
                "itemsSold": dict(Counter(get_path(li, "productName.original")
                                          for o in items for li in o.get("lineItems", []))),
                "orderFieldNames": schema_of(items),
            }
            return len(items), None, "aggregated into orders-summary.json"

        self.run("stores-orders (v2) [private]", "POST /stores/v2/orders/query", v2)
        self.run("ecom-orders [private]", "POST /ecom/v1/orders/search", ecom)
        self.save("orders-summary.json", summary)

    def blog(self):
        fs = [("fieldsets", f) for f in ("RICH_CONTENT", "CONTENT_TEXT", "SEO", "URL", "METRICS")]
        self.simple("blog-posts", "GET", "/blog/v3/posts", "posts", "blog-posts.json", pager="offset",
                    params_fn=lambda l, o: [("paging.limit", l), ("paging.offset", o)] + fs)
        self.simple("blog-draft-posts", "GET", "/blog/v3/draft-posts", "draftPosts", "blog-draft-posts.json",
                    pager="offset",
                    params_fn=lambda l, o: [("paging.limit", l), ("paging.offset", o),
                                            ("fieldsets", "RICH_CONTENT"), ("fieldsets", "URL")])
        self.simple("blog-categories", "GET", "/blog/v3/categories", "categories", "blog-categories.json",
                    pager="offset",
                    params_fn=lambda l, o: [("paging.limit", l), ("paging.offset", o), ("fieldsets", "URL")])
        self.simple("blog-tags", "POST", "/blog/v3/tags/query", "tags", "blog-tags.json", pager="offset")
        self.blog_authors()

    def blog_authors(self):
        """Resolve author member IDs to public nickname/slug only (site authors = public bylines)."""
        def fn():
            ids = set()
            for f in ("blog-posts.json", "blog-draft-posts.json"):
                p = self.out / f
                if p.exists():
                    for post in json.loads(p.read_text(encoding="utf-8")):
                        for k in ("memberId", "ownerId"):
                            if post.get(k):
                                ids.add(post[k])
            authors = []
            for mid in sorted(ids):
                st, data = self.api.call("GET", f"/members/v1/members/{mid}", params={"fieldsets": "PUBLIC"})
                m = data.get("member", {}) if st == 200 else {}
                prof = m.get("profile", {})
                authors.append({"memberId": mid, "status": st, "nickname": prof.get("nickname"),
                                "slug": prof.get("slug") or prof.get("slug"), "title": prof.get("title")})
            return len(authors), self.save("blog-authors.json", authors), "public nickname/slug only"
        self.run("blog-authors", "GET /members/v1/members/{id}?fieldsets=PUBLIC", fn)

    def cms(self):
        cols_holder = {}

        def fn():
            items, _ = page_offset(self.api, "GET", "/wix-data/v2/collections", "collections",
                                   params_fn=lambda l, o: {"paging.limit": l, "paging.offset": o})
            cols_holder["cols"] = items
            return len(items), self.save("cms/_collections.json", items), "schemas for all collections"
        e = self.run("cms-collections", "GET /wix-data/v2/collections", fn)
        if e["status"] != 200:
            return
        for c in cols_holder["cols"]:
            self.cms_collection(c)

    # collections that are person lists -> schema/count only
    CMS_PRIVATE = {"atcMember", "EvVolunteer"}
    CMS_PRIVATE_PREFIX = ("Members/FullData", "Members/PrivateMembersData", "Members/PublicData",
                          "Forms/", "Stores/Orders")
    # keep address-ish fields for these (venue = public place, not a person)
    CMS_KEEP_FIELDS = {"EvVenue": {"address"}, "VenueTest": set()}

    def cms_collection(self, c):
        cid = c["id"]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cid).strip("_")
        path = "POST /wix-data/v2/items/query"

        def fetch():
            return page_offset(self.api, "POST", "/wix-data/v2/items/query", "dataItems",
                               body_fn=lambda l, o: {"dataCollectionId": cid,
                                                     "query": {"paging": {"limit": l, "offset": o}},
                                                     "returnTotalCount": True},
                               limit=1000 if c.get("collectionType") != "WIX_APP" else 100)[0]

        def fn():
            items = fetch()
            fields = [{"key": f["key"], "type": f.get("type"), "displayName": f.get("displayName")}
                      for f in c.get("fields", [])]
            private = cid in self.CMS_PRIVATE or cid.startswith(self.CMS_PRIVATE_PREFIX)
            if private:
                created = [get_path(i, "data._createdDate.$date") or get_path(i, "data._createdDate")
                           for i in items]
                payload = {"collection": cid, "_privacy": "person data: count + schema only",
                           "count": len(items), "fields": fields,
                           "createdRange": date_range([str(x) for x in created if x])}
                return len(items), self.save(f"cms/{safe}.json", payload), "PRIVATE: counts/schema only"
            keep = self.CMS_KEEP_FIELDS.get(cid, set())
            data = [strip_pii(i.get("data", i), keep) for i in items]
            if cid == "Members/Badges":  # badge definitions are fine; drop member id lists
                data = [{k: v for k, v in d.items() if k != "members"} for d in data]
            stripped = sorted({f["key"] for f in fields if PII_KEY.search(f["key"]) and f["key"] not in keep})
            payload = {"collection": cid, "collectionType": c.get("collectionType"),
                       "displayName": c.get("displayName"), "count": len(data), "fields": fields,
                       "strippedFields": stripped, "items": data}
            note = f"stripped {stripped}" if stripped else None
            return len(data), self.save(f"cms/{safe}.json", payload), note
        self.run(f"cms:{cid}", path, fn)

    def bookings(self):
        self.simple("bookings-services", "POST", "/bookings/v2/services/query", "services",
                    "bookings-services.json", pager="offset")
        self.simple("bookings-staff", "POST", "/bookings/v1/staff-members/query", "staffMembers",
                    "bookings-staff.json", transform=lambda xs: strip_pii(xs))
        self.simple("bookings-resources", "POST", "/bookings/v2/resources/query", "resources",
                    "bookings-resources.json")

        def bk():
            items, _ = page_offset(self.api, "POST", "/_api/bookings-reader/v2/extended-bookings/query",
                                   "extendedBookings", body_fn=lambda l, o: oq(l, o))
            bs = [i.get("booking", {}) for i in items]
            payload = {"_privacy": "aggregates only", "count": len(bs),
                       "dateRange": date_range([b.get("createdDate") for b in bs]),
                       "status": dict(Counter(b.get("status") for b in bs)),
                       "byService": dict(Counter(get_path(b, "bookedEntity.title") for b in bs))}
            return len(bs), self.save("bookings-summary.json", payload), "aggregates only"
        self.run("bookings [private]", "POST /_api/bookings-reader/v2/extended-bookings/query", bk)

    def events(self):
        self.simple("events (v1)", "GET", "/events/v1/events", "events", "events-v1.json", pager="offset",
                    params_fn=lambda l, o: [("limit", l), ("offset", o), ("fieldset", "FULL"),
                                            ("fieldset", "DETAILS"), ("fieldset", "TEXTS"),
                                            ("fieldset", "REGISTRATION"), ("fieldset", "URLS"),
                                            ("fieldset", "FORM"), ("fieldset", "SEO_SETTINGS"),
                                            ("fieldset", "CATEGORIES")],
                    transform=lambda xs: xs)
        self.simple("events (v3)", "POST", "/events/v3/events/query", "events", "events-v3.json",
                    pager="offset",
                    body_extra={"fields": ["DETAILS", "TEXTS", "REGISTRATION", "URLS", "FORM",
                                           "SEO_SETTINGS", "CATEGORIES", "AGENDA", "DASHBOARD"]})
        self.simple("event-categories", "POST", "/events/v1/categories/query", "categories",
                    "event-categories.json", pager="offset")
        self.simple("event-ticket-definitions", "POST", "/events/v3/ticket-definitions/query",
                    "ticketDefinitions", "event-ticket-definitions.json")
        summary = {"_privacy": "aggregates only; no guest/buyer data saved"}

        def orders():
            items, _ = page_offset(self.api, "GET", "/events/v1/orders", "orders",
                                   params_fn=lambda l, o: {"limit": l, "offset": o})
            summary["ticketOrders"] = {
                "count": len(items),
                "dateRange": date_range([o.get("created") for o in items]),
                "revenue": money_by_year(items, "created", "totalPrice.amount", "totalPrice.currency"),
                "status": dict(Counter(o.get("status") for o in items)),
                "byEventId": dict(Counter(o.get("eventId") for o in items)),
                "ticketsSold": sum(int(o.get("ticketsQuantity") or 0) for o in items),
            }
            return len(items), None, "aggregated into events-registration-summary.json"

        def rsvps():
            items, _ = page_cursor(self.api, "POST", "/events/v2/rsvps/query", "rsvps",
                                   body_fn=lambda l, c: cq(l, c))
            summary["rsvps"] = {"count": len(items),
                                "byEventId": dict(Counter(r.get("eventId") for r in items)),
                                "status": dict(Counter(r.get("status") for r in items))}
            return len(items), None, "aggregated"

        def guests():
            items, _ = page_cursor(self.api, "POST", "/events/v2/guests/query", "guests",
                                   body_fn=lambda l, c: cq(l, c))
            summary["guests"] = {"count": len(items),
                                 "byEventId": dict(Counter(g.get("eventId") for g in items))}
            return len(items), None, "aggregated"
        self.run("event-orders [private]", "GET /events/v1/orders", orders)
        self.run("event-rsvps [private]", "POST /events/v2/rsvps/query", rsvps)
        self.run("event-guests [private]", "POST /events/v2/guests/query", guests)
        self.save("events-registration-summary.json", summary)

    def pricing_plans(self):
        self.simple("pricing-plans (v2 list)", "GET", "/pricing-plans/v2/plans", "plans",
                    "pricing-plans-v2.json", pager="offset",
                    params_fn=lambda l, o: {"limit": l, "offset": o})
        self.simple("pricing-plans (v3 query)", "POST", "/pricing-plans/v3/plans/query", "plans",
                    "pricing-plans-v3.json")

        def orders():
            items, _ = page_offset(self.api, "GET", "/pricing-plans/v2/orders", "orders",
                                   params_fn=lambda l, o: {"limit": l, "offset": o}, limit=50)
            payload = {"_privacy": "aggregates only", "count": len(items),
                       "dateRange": date_range([o.get("createdDate") for o in items]),
                       "revenue": money_by_year(items, "createdDate", "priceDetails.total",
                                                "priceDetails.currency"),
                       "byPlan": dict(Counter(o.get("planName") for o in items)),
                       "status": dict(Counter(o.get("status") for o in items))}
            return len(items), self.save("pricing-plan-orders-summary.json", payload), "aggregates only"
        self.run("pricing-plan-orders [private]", "GET /pricing-plans/v2/orders", orders)

    def forms(self):
        all_forms = []
        for ns in ("wix.form_app.form", "wix.bookings.form", "wix.events.form",
                   "wix.pricing_plans.form", "wix.site_members.form"):
            try:
                items, _ = page_cursor(self.api, "POST", "/form-schema-service/v4/forms/query", "forms",
                                       body_fn=lambda l, c: cq(l, c, {"namespace": ns}))
            except HttpError as e:
                self.log.append({"resource": f"forms[{ns}]", "endpoint": "POST /form-schema-service/v4/forms/query",
                                 "status": e.status, "count": None, "file": None, "note": e.msg})
                continue
            self.log.append({"resource": f"forms[{ns}]", "endpoint": "POST /form-schema-service/v4/forms/query",
                             "status": 200, "count": len(items), "file": "forms.json" if items else None,
                             "note": None})
            all_forms += items
        self.save("forms.json", all_forms)

        def counts():
            by_ns = defaultdict(list)
            for f in all_forms:
                by_ns[f.get("namespace", "wix.form_app.form")].append(f["id"])
            out = []
            for ns, ids in by_ns.items():
                st, data = self.api.call("POST", "/form-submission-service/v4/submissions/count",
                                         {"formIds": ids, "namespace": ns})
                if st != 200:
                    raise HttpError(st, err_msg(data))
                names = {f["id"]: f.get("name") for f in all_forms}
                for fc in data.get("formsSubmissionsCount", []):
                    out.append({"namespace": ns, "formId": fc.get("formId"),
                                "formName": names.get(fc.get("formId")),
                                "submissions": fc.get("totalCount")})
            return (sum(int(x["submissions"] or 0) for x in out),
                    self.save("form-submission-counts.json", out), "counts only")
        self.run("form-submissions [private]", "POST /form-submission-service/v4/submissions/count", counts)

    def misc_apps(self):
        self.simple("faq-categories", "POST", "/faq/v2/categories/query", "categories", "faq-categories.json")
        self.simple("faq-questions", "POST", "/faq/v2/question-entries/query", "questionEntries",
                    "faq-questions.json")
        self.simple("portfolio-collections", "POST", "/portfolio/v1/collections/query", "collections",
                    "portfolio-collections.json", body_extra={"includePageUrl": True})
        e = self.simple("portfolio-projects", "POST", "/portfolio/v1/projects/query", "projects",
                        "portfolio-projects.json", body_extra={"includePageUrl": True})
        if e["status"] == 200:
            def items():
                projects = json.loads((self.out / "portfolio-projects.json").read_text(encoding="utf-8"))
                out = {}
                for p in projects:
                    its, _ = page_offset(self.api, "GET", f"/portfolio/v1/projectItems/{p['id']}/items",
                                         "items", params_fn=lambda l, o: {"paging.limit": l, "paging.offset": o})
                    out[p["id"]] = {"title": p.get("title"), "items": its}
                return sum(len(v["items"]) for v in out.values()), self.save("portfolio-items.json", out), None
            self.run("portfolio-items", "GET /portfolio/v1/projectItems/{projectId}/items", items)
        self.simple("online-programs", "POST", "/online-programs/v3/programs/query", "programs",
                    "online-programs.json")
        self.simple("groups", "POST", "/social-groups/v2/groups/query", "groups", "groups.json",
                    pager="offset")
        self.simple("reviews", "POST", "/reviews/v1/reviews/query", "reviews", "reviews.json")

    def media(self):
        self.simple("media-folders", "GET", "/site-media/v1/folders", "folders", "media-folders.json",
                    params_fn=lambda l, c: {"paging.limit": l, **({"paging.cursor": c} if c else {})})

        def fn():
            items, _ = page_cursor(self.api, "POST", "/site-media/v1/files/search", "files",
                                   body_fn=lambda l, c: {"paging": {"limit": l, **({"cursor": c} if c else {})}})
            slim = []
            for f in items:
                slim.append({k: f.get(k) for k in ("id", "displayName", "url", "parentFolderId", "hash",
                                                    "sizeInBytes", "private", "mediaType", "mimeType",
                                                    "operationStatus", "sourceUrl", "thumbnailUrl", "labels",
                                                    "createdDate", "updatedDate", "state", "siteId")}
                            | {"media": _media_meta(f.get("media"))})
            types = Counter(f.get("mediaType") for f in items)
            return len(slim), self.save("media-files.json", slim), f"by type {dict(types)}"
        self.run("media-files", "POST /site-media/v1/files/search", fn)

    def people_counts(self):
        """Contacts / members: totals + date ranges only."""
        summary = {"_privacy": "counts/date ranges only; no names/emails saved"}

        def contacts():
            st, data = self.api.call("POST", "/contacts/v4/contacts/query",
                                     {"query": {"paging": {"limit": 1}}})
            if st != 200:
                raise HttpError(st, err_msg(data))
            total = get_path(data, "pagingMetadata.total")
            rng = {}
            for order, label in (("ASC", "earliest"), ("DESC", "latest")):
                st2, d2 = self.api.call("POST", "/contacts/v4/contacts/query",
                                        {"query": {"paging": {"limit": 1},
                                                   "sort": [{"fieldName": "createdDate", "order": order}]}})
                cs = d2.get("contacts") or []
                rng[label] = cs[0].get("createdDate") if cs else None
            summary["contacts"] = {"total": total, "createdRange": rng}
            return total, None, None

        def members():
            items, _ = page_offset(self.api, "POST", "/members/v1/members/query", "members",
                                   body_fn=lambda l, o: oq(l, o))
            summary["members"] = {"total": len(items),
                                  "createdRange": date_range([m.get("createdDate") for m in items]),
                                  "status": dict(Counter(m.get("status") for m in items)),
                                  "privacyStatus": dict(Counter(m.get("privacyStatus") for m in items)),
                                  "fieldNames": schema_of(items)}
            return len(items), None, None
        self.run("contacts [private]", "POST /contacts/v4/contacts/query", contacts)
        self.run("members [private]", "POST /members/v1/members/query", members)
        self.save("people-summary.json", summary)

    def run_all(self):
        print(f"== {self.slug}")
        for step in (self.site_info, self.stores, self.blog, self.cms, self.bookings, self.events,
                     self.pricing_plans, self.forms, self.misc_apps, self.media, self.people_counts):
            step()
        self.save("_export-log.json", {"site": self.slug, "siteId": self.api.site_id,
                                       "exportedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                                       "resources": self.log})


def _media_meta(m):
    """Keep only dimensions/format info from media blob."""
    if not isinstance(m, dict):
        return None
    out = {}
    for kind in ("image", "video", "audio", "document", "vector", "archive", "model3d"):
        v = m.get(kind)
        if isinstance(v, dict):
            inner = v.get("image") if kind == "image" and isinstance(v.get("image"), dict) else v
            out[kind] = {k: inner.get(k) for k in ("width", "height", "format", "altText", "duration",
                                                    "filename", "url") if k in inner}
    return out or None


def list_sites():
    st, data = Api(None).call("POST", "/site-list/v2/sites/query", {"query": {"cursorPaging": {"limit": 50}}})
    if st != 200:
        raise SystemExit(f"site list failed: {st} {err_msg(data)}")
    return data.get("sites", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=["live", "dev", "all"], default="all")
    args = ap.parse_args()
    sites = list_sites()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "_sites.json").write_text(json.dumps(
        [{k: s.get(k) for k in ("id", "name", "displayName", "createdDate", "updatedDate", "published",
                                "premium", "viewUrl", "editorType", "ownerAccountId", "contributorAccountIds")}
         for s in sites], indent=2), encoding="utf-8")
    live_id = CFG["WIX_SITE_ID"]
    targets = []
    if args.site in ("live", "all"):
        targets.append((os.environ.get("WIX_SITE_SLUG", "live-site"), live_id))
    if args.site in ("dev", "all"):
        dev = next((s for s in sites if s.get("displayName") == DEV_SITE_NAME), None)
        if dev:
            targets.append(("dev-site-1-stores-v3", dev["id"]))
        else:
            print("dev site not found in site list", file=sys.stderr)
    for slug, sid in targets:
        Exporter(slug, sid).run_all()


if __name__ == "__main__":
    main()
