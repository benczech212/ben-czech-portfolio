import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

MEMBERS_FILE = DATA_DIR / "members.json"
BUILDS_FILE = DATA_DIR / "builds.json"
ANNOUNCEMENTS_FILE = DATA_DIR / "announcements.json"


def _read(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, data: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# --- Members ---

def get_members() -> list:
    return _read(MEMBERS_FILE)


def save_members(members: list) -> None:
    _write(MEMBERS_FILE, members)


def get_member(discord_id: str) -> dict | None:
    return next((m for m in get_members() if m["discord_id"] == discord_id), None)


def upsert_member(member: dict) -> None:
    members = get_members()
    idx = next((i for i, m in enumerate(members) if m["discord_id"] == member["discord_id"]), None)
    if idx is None:
        members.append(member)
    else:
        members[idx] = member
    save_members(members)


# --- Builds ---

def get_builds() -> list:
    return _read(BUILDS_FILE)


def save_builds(builds: list) -> None:
    _write(BUILDS_FILE, builds)


def get_build(build_id: str) -> dict | None:
    return next((b for b in get_builds() if b["id"] == build_id), None)


def get_build_by_name(name: str) -> dict | None:
    name_lower = name.lower()
    return next((b for b in get_builds() if b["name"].lower() == name_lower), None)


def upsert_build(build: dict) -> None:
    builds = get_builds()
    idx = next((i for i, b in enumerate(builds) if b["id"] == build["id"]), None)
    if idx is None:
        builds.append(build)
    else:
        builds[idx] = build
    save_builds(builds)


# --- Announcements ---

def get_announcements() -> list:
    return _read(ANNOUNCEMENTS_FILE)


def save_announcements(announcements: list) -> None:
    _write(ANNOUNCEMENTS_FILE, announcements)


def get_announcement(ann_id: str) -> dict | None:
    return next((a for a in get_announcements() if a["id"] == ann_id), None)


def upsert_announcement(ann: dict) -> None:
    announcements = get_announcements()
    idx = next((i for i, a in enumerate(announcements) if a["id"] == ann["id"]), None)
    if idx is None:
        announcements.append(ann)
    else:
        announcements[idx] = ann
    save_announcements(announcements)


def delete_announcement(ann_id: str) -> bool:
    announcements = get_announcements()
    new_list = [a for a in announcements if a["id"] != ann_id]
    if len(new_list) == len(announcements):
        return False
    save_announcements(new_list)
    return True
