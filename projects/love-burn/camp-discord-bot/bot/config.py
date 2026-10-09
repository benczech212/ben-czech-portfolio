import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GUILD_ID = int(os.environ["GUILD_ID"])
ANNOUNCE_CHANNEL_ID = int(os.environ["ANNOUNCE_CHANNEL_ID"])
AI_CHANNEL_ID = int(os.getenv("AI_CHANNEL_ID") or 0)          # 0 = /ask and @mentions work anywhere

# AI assistant
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5-5"
AI_COOLDOWN_SECONDS = float(os.getenv("AI_COOLDOWN_SECONDS") or 30)   # per user

CAMP_NAME = "Lunarcrats"
EVENT_NAME = "Love Burn 2027"
EVENT_DATE = "February 2027"

# Members with this Discord role (or Manage Server) can update/assign any build project and
# manage announcements; see bot/access.py.
LEAD_ROLE_NAME = os.getenv("LEAD_ROLE_NAME") or "Camp Lead"

VALID_ROLES = ["DJ", "Builder", "Cook", "Volunteer", "Art", "LED", "Sound", "Camp Lead", "Member"]

BUILD_STATUSES = ["planning", "in-progress", "complete", "on-hold"]
