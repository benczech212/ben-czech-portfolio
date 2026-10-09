# Ben Czech — projects

Things I've built with Claude Code: agent systems with real guardrails, interactive installations, and tools
for live visuals and a festival art camp. Each folder is a cleaned, standalone copy of a working project, with
personal data, home-network details and credentials removed.

Each project README opens with **The lesson**: the problem, the one idea worth stealing, a walkthrough of the
files that matter, a way to try it without the original hardware or accounts, and what to take to your own
project. Start with [agent-harness](agent-harness/) if you're here for Claude Code.

| Project | What it is | Built with |
| :--- | :--- | :--- |
| [agent-harness](agent-harness/) | Template of my daily personal-agent setup: a coordinator session, scoped subagents, permission deny-rules for secrets, scoped writes and an audit log | Claude Code (CLAUDE.md, subagents, slash commands, settings) |
| [lrma-exhibit](projects/lrma-exhibit/) | Control system for an interactive projection exhibit at the Leepa-Rattner Museum of Art: declarative macros, readiness probes, a touch panel, visitor inputs and monitoring. CC BY 4.0 | Node.js, Resolume Arena, Synesthesia, OSC, Prometheus/Loki/Grafana |
| [dj-facade](projects/dj-facade/) | A 1,215-LED sculptural DJ facade: camera-based pixel mapping, a custom Pixelblaze pattern with 22 vibes and 16 effects, audio-reactive cues and showreel recording | Python, OpenCV, Pixelblaze, ffmpeg |
| [agent-media-pipeline](projects/agent-media-pipeline/) | Agent-directed cleanup of a multi-terabyte media library with approval gates: dry-run deletes, allow-lists, VMAF-verified GPU re-encodes, undoable renames | Python, ffmpeg/VMAF, Plex, Docker |
| [wix-to-wordpress](projects/wix-to-wordpress/) | Tooling for migrating a small business site from Wix to self-hosted WordPress: read-only exporter, YAML-to-Elementor page builder (drafts only), a custom widget plugin, GCP staging | Python, PHP, WordPress/Elementor, GCP |
| [love-burn/camp-bus](projects/love-burn/camp-bus/) | Offline MQTT event bus connecting sensors, props and instruments to lights and sound at a festival art camp, with a live-editable rules engine and Raspberry Pi nodes | Python, Mosquitto, CircuitPython, Docker |
| [love-burn/ticket-fund](projects/love-burn/ticket-fund/) | Ranked-choice project voting, ticket requests and donations for the camp, with access control enforced in Firestore rules | Firebase, JavaScript |
| [love-burn/meeting-transcriber](projects/love-burn/meeting-transcriber/) | Local multi-speaker meeting transcription: per-speaker Whisper + VAD, merged transcripts, subtitles and a "who's talking" video | Python, faster-whisper, ffmpeg |
| [love-burn/camp-discord-bot](projects/love-burn/camp-discord-bot/) | Discord bot for the camp with a Claude assistant grounded on live roster and build data, treating that data as untrusted | Python, discord.py, Claude API |

## How these were prepared

Every project was copied file by file into this repo (no history from the originals), then scanned for
credentials, personal data and network details, and reviewed for security issues. Fixes made along the way —
localhost-by-default servers, origin and content-type checks, allow-lists before deletes, quoting of remote
commands — are described in each project's **Security notes**.

Work I do for my employer and for clients isn't included here.

## License

Code is MIT licensed (see [LICENSE](LICENSE)) unless a project says otherwise. `projects/lrma-exhibit` is
CC BY 4.0, as agreed with St. Petersburg College.
