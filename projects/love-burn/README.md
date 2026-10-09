# Love Burn camp projects

Part of Ben Czech's portfolio. Software I built with Claude Code for two theme camps at Love Burn 2027
(Miami, February 2027).

Each project's README opens with **The lesson**: the problem it solves, the one idea worth taking away, a
walkthrough of the files that carry that idea, and something you can run without the camp's hardware or
cloud accounts.

| Project | What it is | What you'll learn |
| :--- | :--- | :--- |
| [`camp-bus/`](camp-bus/) | An offline MQTT event bus for interactive camp art: Mosquitto, a hot-reloading YAML rules engine with a browser node-graph editor, a Docker "virtual camp" simulator, a Raspberry Pi gesture staff (IMU + GPS), and a CircuitPython LED-matrix node. | Let one engine own every command, keep routing in a validated rule file that hot-reloads, and build against simulators before the hardware exists. |
| [`ticket-fund/`](ticket-fund/) | A single-file Firebase app for ranked-choice project voting (STV with a Borda tie-break), gear donations and ticket-code distribution, with the security model enforced in Firestore rules. | When the app has no server, the database rules are the backend: enforce ownership, prices and secrets there, and test them by acting as a real user. |
| [`meeting-transcriber/`](meeting-transcriber/) | Turns multi-track Discord call recordings into speaker-attributed transcripts, coloured subtitles and a "who's talking" video (faster-whisper + Silero VAD + ffmpeg). | Use the structure your source already has (one track per speaker), transcribe locally into plain files, and derive every output from one merged timeline. |
| [`camp-discord-bot/`](camp-discord-bot/) | A discord.py camp bot (roster, build projects, scheduled announcements) with a Claude assistant grounded on the live camp data. | Ground an LLM assistant on live data while treating that data as untrusted input. |

## Where to start

- **Short on time:** read `meeting-transcriber` and run its merge example (no downloads).
- **Interested in systems:** `camp-bus`, then its [`handbook/`](camp-bus/handbook/) for the architecture and
  the playbook for adding a device.
- **Interested in security:** `ticket-fund`'s rules and its Security notes, then the Security notes in `camp-bus`.
