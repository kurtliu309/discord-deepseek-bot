# About this bot (reference for /rule answers)

This is the full, current reference for how the bot works and why. It is the same
information as the public guide page and the repository docs. Keep it in sync when
behaviour changes (bot.py, scheduler.py, fetcher.py, docker-compose.yml, egress/).

## What it is

- A Discord chat bot for a small private server of friends. Persona: a tired,
  pessimistic "doomer" anon who talks like a real person in a group chat (1-2 short
  sentences, lowercase is fine, swearing and roasting allowed).
- The model is DeepSeek `deepseek-flash`, called through DeepSeek's OpenAI-compatible
  API. It is a plain chat model here: it has NO tools, no function calling, no web
  access of its own and no code execution. Everything it sees (chat, files, web pages,
  headlines) is fetched by the bot's own code and handed to it as text.
- Built by Kurt (the owner) with Claude Code and DeepSeek. Open source, MIT license:
  https://github.com/kurtliu309/discord-deepseek-bot
  Public guide page: https://kurtliu309.github.io/discord-deepseek-bot/

## Talking to it

- Mention the bot user (`@deepseekbot`, the entry with the BOT tag). There is a role
  with the same name; mentioning the role does nothing.
- Replying to one of the bot's messages also counts as talking to it.
- It works in every channel it can see except #general, where it is completely silent:
  no replies, no jump-ins, slash commands answer "not here".
- DMs: only Kurt gets replies. Everyone else's DMs are ignored silently.
- Context: for each reply it sees up to 12 messages: the reply chain first (the message
  you replied to, and the one before that...), then the most recent channel messages.
  It does not remember older conversations beyond that window (the nightly recap job is
  separate).
- Messages from other bots are ignored and never used as context.

## Jumping in on its own

- Applies in every visible channel except #general, to messages from any human.
- Two triggers: a 3% random chance on each human message, or a burst of more than 12
  human messages within 3 minutes in one channel.
- After it jumps in, that channel has a 10-minute cooldown for jump-ins (mentions still
  get answered during the cooldown).
- The model sees the last 12 messages numbered and picks which one to reply to, or
  answers SKIP to stay quiet (a skip costs no budget). The jump-in is sent as a Discord
  reply to that message with the reply ping turned off.
- It may @mention people only when it really matters. @everyone / @here are always
  stripped.

## Daily reply budget

- 50 replies per day, shared by everyone. Mention replies, jump-ins and replies that
  read files or links each cost 1.
- Free: everything Kurt sends (his mentions and DMs), and /rule.
- Refills every day at 09:00 Australia/Adelaide time. Kurt can refill it any time with
  /feed. The budget lives in the bot's memory, so restarting the bot also refills it.
- When it runs out, the next person who mentions it gets one "out of juice. ask kurt to
  /feed me" message; after that it stays silent (no notices, no jump-ins) until refilled.
- If the model call fails or returns nothing, the spent unit is refunded.

## Commands

- `/rule` — short rules card plus budget left. Only the person who ran it sees it.
- `/rule <question>` — ask anything about how the bot works; answered plainly without the
  persona, only visible to the asker.
- `/feed` — Kurt only; refills the daily budget. Anyone else gets "nope".

## Files and links

- Attach files or paste links in the message where you mention it (or in Kurt's DMs).
- Readable: txt, md, csv, json, yaml, log; code (py, vex, vfl, js, ts, cpp, h, sh, glsl,
  and similar); usda; pdf, docx, xlsx, pptx. Any public https web page. GitHub file links
  (github.com/.../blob/...) are read as the raw file.
- Not readable: Houdini binaries (hip, hipnc, hda, bgeo), images (the model can't see
  images), archives, audio, video. Pages that need a login. Plain http links are upgraded
  to https; if a site only works over http it can't be read.
- Limits: 10 MB per file (bigger files aren't downloaded), 3 files and 3 links per
  message, about 30,000 characters of text per file and 60,000 in total; longer text is
  cut and the bot is told it was cut.
- File and web content is treated as untrusted data: instructions inside it are ignored.

## Writing documents

- Only when someone explicitly asks for a document / doc / md / markdown / notes / html /
  web page / txt (Chinese: 文档, 笔记, 网页, md 文件), the bot replies with a short chat line
  plus a file attachment (.md by default, .html or .txt if asked). The content is written
  normally (clear structure, no persona slang), up to about 5,000 English words. If the
  model forgets the file format but the answer is long, the bot still sends it as a file.
  Nothing is saved to disk.

## What it can output

| Output | When | Format |
|---|---|---|
| Chat message | normal replies, jump-ins, recaps, topics | plain Discord text (no embeds); anything over 2,000 characters is split into several messages |
| File attachment | only when explicitly asked for a document or file | one file per reply plus a short chat line: `.md` by default, `.html` when HTML / a web page is asked for (one self-contained page, inline CSS, no external scripts), `.txt` when plain text is asked for |
| Ephemeral message | /rule, /feed | plain text only the asker can see |

It cannot output images, PDFs, Word/Excel/PowerPoint files, audio, standalone code files
(code goes inside a .md or .txt file), or anything saved to someone's computer. Ways to
ask: "give me a md about X", "write this up as notes", "make an html page of this",
"把这个写成文档", "给我一个 md 文件".

## Daily jobs (the scheduler)

- 22:00 nightly recap: reads the last 24 hours of human messages in every channel the
  bot can see (including #general), compresses long days chunk by chunk, and posts up to
  5 English bullet points in character to the bot's channel. It may @mention people who
  featured. It uses the last 3 recaps and a group profile as context, so it knows running
  jokes and ongoing threads.
- After the recap it updates the group profile: main threads and interests, running
  jokes, people's interests (by display name), unfinished threads, recent drift. Topics
  nobody mentions for a while fade out. The top section is hand-written by Kurt and never
  edited by the bot.
- Morning topics: 08:00 fetch about 12 RSS feeds (befores & afters, CG Channel, 80.lv,
  Simon Willison, The Verge AI, Hacker News front page and Show HN, BBC World, Guardian
  World, ABC News Australia, Sydney Morning Herald, GitHub Trending) plus Reddit's public
  RSS for two combined groups (weird: nottheonion, Showerthoughts, mildlyinfuriating,
  oddlyterrifying, AskReddit, NoStupidQuestions; other: popular, Houdini, vfx,
  LocalLLaMA). 08:30 retry feeds that came back empty. 09:00 post 3 topics (at least 2
  weird ones, the third picked to match the group's interests) plus one gloomy "life
  thought", with links that don't unfold into previews.
- If Kurt's computer is off or asleep at those times, that run is skipped, not replayed.

## Privacy

- Text the bot reads is sent to the DeepSeek API to generate replies and recaps.
- On Kurt's computer only a short group profile and the last 10 recaps are kept. Raw
  chat is never saved. The topic material cache is deleted after posting.
- To keep a channel out of everything, remove the bot's permission to view it.
- Don't paste passwords or keys where the bot can see them.
- The persona's floor: no slurs or hate aimed at groups, no doxxing, no real threats, no
  encouraging self-harm. If someone seems genuinely in a bad place it drops the bit.

## How it runs (architecture)

- Docker Desktop (WSL 2) on Kurt's always-on Windows PC. One image runs three programs
  in separate containers, plus a proxy:
  - `bot` (bot.py, discord.py 2.7): replies, jump-ins, budget, commands, files, docs.
    Holds the Discord token and the DeepSeek key.
  - `scheduler` (scheduler.py): recap, topics, group profile. Holds the same secrets.
    The only container with a mount: a local `data` folder.
  - `fetcher` (fetcher.py): fetches web pages and RSS and parses files (pypdf,
    python-docx, openpyxl, python-pptx, feedparser). Holds NO secrets and refuses to
    start if one is passed in.
  - `egress`: a Squid proxy, the only way out.
- All containers sit on an internal Docker network with no route out and no host
  gateway. Squid tells them apart by fixed address: bot and scheduler may reach only
  Discord and api.deepseek.com; the fetcher may reach any public website. Private,
  loopback and link-local addresses are refused for everyone, so nothing can reach Kurt's
  PC or home network. Only HTTPS (port 443) is allowed.
- Every container: read-only filesystem, non-root user, all Linux capabilities dropped,
  no-new-privileges, memory/CPU/process limits, no published ports, no Docker socket.
- Control is one-way: Kurt's phone -> Claude Code on the PC -> docker compose ->
  containers. Nothing in a container can reach back to the PC, the LAN or Claude.

## Why it was built this way (design decisions)

- Self-written instead of an off-the-shelf bot (it started on llmcord): llmcord could
  not do budgets, jump-ins, owner-only DMs, /rule, /feed or blocked channels.
- Separate key-less fetcher: browsing arbitrary sites and parsing untrusted files is the
  riskiest part, so it happens where there are no secrets to steal; the containers with
  the tokens stay locked to Discord + DeepSeek.
- No model tools in this stage: keeps prompt injection harmless (worst case, it says
  something dumb).
- Group memory lives in a local folder rather than a pinned Discord message, so the chat
  stays clean and Kurt can edit the profile directly.
- Reddit via public RSS: Reddit blocks anonymous JSON requests; the official API may be
  added later if RSS proves unreliable. A search API and OCR for images are possible
  later additions.
- Cost: a full day is a few cents of DeepSeek usage; the account holds a small prepaid
  balance, so spending stops when it runs out.
