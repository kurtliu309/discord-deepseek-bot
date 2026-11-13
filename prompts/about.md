# About this bot (reference for /rule answers)

This is the full, current reference for how the bot works and why. It is the same
information as the public guide page and the repository docs. Keep it in sync when
behaviour changes (bot.py, scheduler.py, fetcher.py, setup_ui.py, tools/,
docker-compose.yml, egress/).

## Changelog (what's new)

Newest first. Same as the "What's new" box on the guide page. When asked what changed or
what's new, answer from this list.

- **v3.2 · 2026-11-14**
  - New `news` feature: every day at the server's own time it posts the day's news in a
    channel: one opening line in character, then one short message per story (title, a
    two- or three-sentence summary, one line of comment, the link).
  - Stories are picked for each server from that server's own interests (a hand-written
    `interests.md`, or the main threads of its profile when there is none), and each
    server remembers what it was already sent for 30 days, so the same story doesn't come
    twice. The full version of each day's digest is saved on Kurt's computer.
- **v3.1 · 2026-11-14**
  - Servers with trust level owner can turn on `tools`: the model may call three read-only
    tools of its own — search the web, read a web page, read a video's public subtitles.
    Public servers never have tools: their model requests carry no tool list at all, so
    the model cannot call anything.
  - Every tool call costs one lookup from the daily budget; one question is capped at 4
    calls and about 40,000 characters of tool output in total.
  - The tools run in their own container with no Discord or DeepSeek secret, and search
    runs on a separate SearXNG container; both reach the internet only through the
    allowlist proxy.
  - Search quality is still mediocre: SearXNG uses free search engines, which rate-limit
    and return junk. Reading pages and video subtitles works well. A paid search API may
    replace it later.
- **v3.0 · 2026-11-14**
  - Every setting is now per server, in `data/guilds/<server id>/config.yaml`: persona,
    style, language, feature switches, channels, times, feeds, budgets, blocked people,
    admins. A server without a valid config gets no replies at all; the owner is told
    once to run `/setup`.
  - New `/setup` (the owner and the server's admins, private replies): trust level,
    persona, style, language, budgets, jump-in settings, quiet channels, recap and topics
    settings, consent, blocked people, admins. Three pages, with a 中文 / English button
    that changes only the menu language and is remembered per server. Only the owner can
    change the trust level or the admin list; the check is enforced on the server, so an
    admin's attempt is refused even if the menu is bypassed.
  - Two layers of budget, one for replies and one for tool lookups: each has a server
    total and a per-person limit, and whichever runs out first stops it. The owner gets
    one private message when a limit hits zero. `/feed` refills the whole server;
    `/feed @someone` refills just that person.
  - Blocked people: their mentions are ignored, they never get a jump-in and their
    messages don't count towards bursts, but the nightly recap still reads the channel as
    before. The owner can never be blocked.
  - Admins: the people the owner lists can run `/setup` and `/feed` (they also need
    Discord's "Manage Server" permission to see `/setup`). The owner is always an admin.
  - Every saved `/setup` that changed something sends the owner a private message listing
    only the changes (people by name, never IDs).
  - Persona library in `data/shared/personas/`: shared floor + character + typing style,
    hot reloaded without a restart. Ships a second persona (turtle) and a chatty style.
  - Per-server memory: the profile is split into blocks the bot rewrites one at a time
    each night (the hand-written Kurt block is never touched); recaps, budgets, the news
    dedupe list and the daily material are per server too.
  - The scheduler works per server: each one gets its recap, topics and news at its own
    time, in its own channel, from its own feed list. A server that hasn't consented is
    never read or summarised.
- **v2.2 · 2026-11-13**
  - Persona: same doomer, now a senior film VFX artist of many years: strong on tech,
    loves movies, worn out by overtime.
  - Messages about Houdini/FX now get a higher jump-in chance: 5% instead of 3%.
  - The nightly recap no longer @mentions anyone; it uses names only.
- **v2.1 · 2026-11-13**
  - New `/dev` ("developer mode"): same as /rule, but the card or answer is posted
    publicly in the channel. Costs 1 reply from the daily budget (free for Kurt).
  - Smarter `/rule`: it now reads this full reference (rules, mechanics, architecture,
    design reasons) instead of only the short rules card.
  - File replies fixed: asking for a document now reliably returns an attachment —
    `.md` by default, `.html` for "give me HTML" / a web page, `.txt` for plain text.
    "give me a md", "give us md" and Chinese requests (文档, 笔记, 网页) are recognised;
    if the model forgets the file format but the answer is long, it is still sent as a
    file.
- **v2.0 · 2026-11-13** — Self-written bot replaces llmcord: jump-ins (3% random or a
  burst of 12 messages in 3 minutes, 10-minute cooldown), shared daily budget of 50 with
  `/feed`, `/rule`, owner-only DMs, silence in #general, file and link reading through a
  key-less fetcher container, nightly recap at 22:00 and morning topics at 09:00 with a
  local group profile. Reddit is read through its public RSS.
- **v1.0 · 2026-11-13** — First deploy: a chat-only bot built on llmcord behind an
  allowlist proxy, replying only when mentioned.

## What it is

- A Discord chat bot for a small private server of friends, now able to live in several
  servers at once, each with its own settings and its own memory. Persona: a tired,
  pessimistic "doomer" anon who talks like a real person in a group chat (1-2 short
  sentences, lowercase is fine, swearing and roasting allowed). It's a senior film
  VFX artist of many years: strong on tech, loves movies, worn out by overtime.
- The model is DeepSeek `deepseek-flash`, called through DeepSeek's OpenAI-compatible
  API. It has no code execution and cannot see images. In the owner's own servers it may
  call three read-only tools (search, read a page, read a video's public subtitles); in
  public servers its requests carry no tool list at all. Everything else it sees (chat,
  files, web pages, headlines) is fetched by the bot's own code and handed to it as text.
- Built by Kurt (the owner) with Claude Code and DeepSeek. Open source, MIT license:
  https://github.com/kurtliu309/discord-deepseek-bot
  Public guide page: https://kurtliu309.github.io/discord-deepseek-bot/

## Talking to it

- A server has to be set up first: the owner or one of its admins runs `/setup` there. In
  a server without a valid config the bot says nothing, and the owner is told once to run
  `/setup`.
- Mention the bot user (`@deepseekbot`, the entry with the BOT tag). There is a role
  with the same name; mentioning the role does nothing.
- Replying to one of the bot's messages also counts as talking to it.
- It works in every channel it can see except the server's quiet channels. In a quiet
  channel it is completely silent: no replies, no jump-ins, slash commands answer "not
  here". Kurt set his own server's quiet list to #general; other servers pick their own.
- Blocked people are invisible to it: no reply to their mentions, no jump-ins on their
  messages, and their messages never count towards a burst.
- DMs: only Kurt gets replies. Everyone else's DMs are ignored silently.
- Context: for each reply it sees up to 12 messages: the reply chain first (the message
  you replied to, and the one before that...), then the most recent channel messages.
  It does not remember older conversations beyond that window (the nightly recap job is
  separate).
- Messages from other bots are ignored and never used as context.

## Jumping in on its own

- Applies in every channel that isn't quiet, to messages from any human.
- Two triggers: a 3% random chance on each human message (5% when the message is about
  Houdini/FX), or a burst of more than 12 human messages within 3 minutes in one
  channel. The numbers are per server; the ones here are the defaults.
- After it jumps in, that channel has a 10-minute cooldown for jump-ins (mentions still
  get answered during the cooldown).
- The model sees the last 12 messages numbered and picks which one to reply to, or
  answers SKIP to stay quiet (a skip costs no budget). The jump-in is sent as a Discord
  reply to that message with the reply ping turned off.
- It may @mention people only when it really matters. @everyone / @here are always
  stripped.

## Daily budgets

- Each server has two daily budgets: replies and tool lookups.
- Replies: 50 per day for the server and 30 per person by default. Mention replies,
  jump-ins, /dev and replies that read files or links each cost 1. The per-person limit
  is on top of the server total; whichever runs out first stops it.
- Lookups: spent one per tool call, and only when `tools` is on. A server with tools on
  sets its own server total (it defaults to 0, which means no lookups) and a per-person
  limit (20 by default). A question stops after 4 calls or about 40,000 characters of
  tool output.
- Free: everything Kurt sends (his mentions, DMs and /dev), all of Kurt's own tool
  calls, and /rule. /dev costs 1 for everyone else.
- Refills every day at the server's reset time, 09:00 Australia/Adelaide by default. Kurt
  and the server's admins can refill early with /feed. The counters live in the bot's
  memory, so restarting the bot also refills them.
- When the server total runs out, the next person who mentions it gets one "out of
  juice. ask kurt to /feed me" message; after that it stays silent (no notices, no
  jump-ins) until refilled. When one person's own limit runs out, only that person gets
  the notice. In both cases Kurt gets a private message, once per limit per cycle.
- If the model call fails or returns nothing, the spent unit is refunded.

## Commands

- `/rule` — short rules card plus budget left. Only the person who ran it sees it.
- `/rule <question>` — ask anything about how the bot works; answered plainly without the
  persona, only visible to the asker.
- `/dev` and `/dev <question>` — "developer mode": exactly like /rule, but the card or
  answer is posted publicly in the channel (with the question shown on top) so everyone
  can read it. Costs 1 unit of the daily budget for everyone except Kurt; if the budget
  is empty, only the asker is told. Not available in quiet channels.
- `/setup` — the owner and the server's admins; only the person who types it sees the
  menus. Three pages: trust level, persona, style, language, budgets and jump-in
  settings; quiet channels, recap and topics channels/times, consent, blocked people and
  admins; and a member page. One button switches the whole menu between Chinese and
  English (it only changes the menu, not the bot's replies, and is remembered per
  server). Only the owner can change the trust level or the admin list. Saving with real
  changes sends the owner a private message that lists only what changed.
- `/feed` — the owner or a server admin; refills this server's budgets. `/feed @someone`
  refills only that person's limits. Anyone else gets "nope".

## Servers and trust levels

- Every server has a trust level: owner (Kurt's own servers, only people he trusts) or
  public (strangers are in it). The level is a ceiling: a feature that needs owner does
  nothing in a public server even if its config says on.
- The one difference today is `tools`: owner servers may switch them on. Public servers
  never have tools.
- Each server also has its own admins (who can run `/setup` and `/feed`) and blocked
  people. Only the owner can change the trust level or the admin list.

## Files and links

- Attach files or paste links in the message where you mention it (or in Kurt's DMs).
  A server can switch this off; then the bot ignores attachments and links.
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

## The tools (owner servers only)

- Three tools, all read-only: `search` (web search, returns titles, links and snippets),
  `read_page` (opens one public https page and returns its text) and `read_video` (reads
  a video's public subtitles, timestamped; no audio download and no transcription).
- The bot decides whether a request gets tools at all: public servers, unconfigured
  servers and its own jump-ins never send a tool list, so the model cannot call anything.
- The model only chooses the query or the link. The bot fills in the server ID and the
  site allow-list, charges one lookup before each call, and stops the question after 4
  calls or about 40,000 characters. The tools container checks the server again before it
  does anything.
- Everything the tools return is wrapped as untrusted data, exactly like files and pages
  the bot reads itself.
- They can search and read, nothing else: no writing files, no running commands, no
  access to anyone's computer.

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
| Chat message | normal replies, jump-ins, recaps, topics, news | plain Discord text (no embeds); anything over 2,000 characters is split into several messages |
| File attachment | only when explicitly asked for a document or file | one file per reply plus a short chat line: `.md` by default, `.html` when HTML / a web page is asked for (one self-contained page, inline CSS, no external scripts), `.txt` when plain text is asked for |
| Ephemeral message | /rule, /feed, /setup | plain text or menus only the person who ran it can see |
| Public explanation | /dev | the same card or answer as /rule, posted to the channel |

It cannot output images, PDFs, Word/Excel/PowerPoint files, audio, standalone code files
(code goes inside a .md or .txt file), or anything saved to someone's computer. Ways to
ask: "give me a md about X", "write this up as notes", "make an html page of this",
"把这个写成文档", "给我一个 md 文件".

## Daily jobs (the scheduler)

- Every job is per server: its own switch, channel and time. Times are each server's
  choice; the defaults are below.
- 22:00 nightly recap: reads the last 24 hours of human messages in every channel the
  bot can see (including quiet channels), compresses long days chunk by chunk, and posts
  up to 5 English bullet points in character to the server's recap channel. It uses names
  only and never @mentions anyone. It uses the last 3 recaps and a group profile as
  context, so it knows running jokes and ongoing threads.
- After the recap it updates the group profile: main threads and interests, running
  jokes, people's interests (by display name), unfinished threads, recent drift. Topics
  nobody mentions for a while fade out. The top section is hand-written by Kurt and never
  edited by the bot.
- News (v3.2): at the server's news time the scheduler reads the day's candidate stories
  (gathered once a day on Kurt's PC by a separate local job and shared by every server),
  drops anything that server was sent in the last 30 days, and has the model pick the
  stories that fit the server's interests. It posts one opening line in character, then
  one short message per story: title, a two- or three-sentence summary, one line of
  comment and the link. The full digest is saved on Kurt's computer.
- Morning topics: 08:00 the fetcher pulls about 12 RSS feeds (befores & afters, CG
  Channel, 80.lv, Simon Willison, The Verge AI, Hacker News front page and Show HN, BBC
  World, Guardian World, ABC News Australia, Sydney Morning Herald, GitHub Trending) plus
  Reddit's public RSS for two combined groups (weird: nottheonion, Showerthoughts,
  mildlyinfuriating, oddlyterrifying, AskReddit, NoStupidQuestions; other: popular,
  Houdini, vfx, LocalLLaMA). 08:30 retry feeds that came back empty. At the server's
  topics time (09:00 by default) post 3 topics (at least 2 weird ones, the third picked
  to match the server's interests) plus one gloomy "life thought", with links that don't
  unfold into previews. Each server has its own feed list.
- If Kurt's computer is off or asleep at those times, that run is skipped, not replayed.

## Privacy

- Text the bot reads is sent to the DeepSeek API to generate replies, recaps and news.
- On Kurt's computer, per server, it keeps only a short group profile, the last 10
  recaps, the news dedupe list (30 days of story IDs) and the news digests. Raw chat is
  never saved. The topic material cache is deleted after posting.
- To keep a channel out of everything, remove the bot's permission to view it.
- Don't paste passwords or keys where the bot can see them.
- The persona's floor: no slurs or hate aimed at groups, no doxxing, no real threats, no
  encouraging self-harm. If someone seems genuinely in a bad place it drops the bit.

## How it runs (architecture)

- Docker Desktop (WSL 2) on Kurt's always-on Windows PC. One image runs three programs
  in separate containers, plus a tool server, a search server and a proxy — six
  containers:
  - `bot` (bot.py, discord.py 2.7): replies, jump-ins, budgets, commands, /setup, files,
    docs, the tool loop. Holds the Discord token and the DeepSeek key. Mounts
    `data/guilds` read-write (`/setup` writes configs there) and `data/shared` read-only
    (personas and news material).
  - `scheduler` (scheduler.py): recap, topics, news, group profile. Holds the same
    secrets. Mounts the whole local `data` folder read-write.
  - `fetcher` (fetcher.py): fetches web pages and RSS and parses files (pypdf,
    python-docx, openpyxl, python-pptx, feedparser). Holds NO secrets and refuses to
    start if one is passed in.
  - `tools`: an MCP server the bot calls over the internal network. It runs the three
    tools (search, read_page, read_video), reusing the fetcher's code. No Discord or
    DeepSeek secret; refuses to start if one is passed in.
  - `searxng`: the self-hosted search engine `tools` queries. Needs no API key; its only
    mounted file is its own read-only settings file.
  - `egress`: a Squid proxy, the only way out.
- All containers sit on an internal Docker network with no route out and no host
  gateway. Squid tells them apart by fixed address: bot and scheduler may reach only
  Discord and api.deepseek.com; the fetcher, tools and searxng may reach any public
  website. Private, loopback and link-local addresses are refused for everyone, so
  nothing can reach Kurt's PC or home network. Only HTTPS (port 443) is allowed.
- Every container: read-only filesystem, non-root user, all Linux capabilities dropped,
  no-new-privileges, memory/CPU/process limits, no published ports, no Docker socket.
- The model's tool list is built by code: only owner-trust servers get one, the server ID
  and site allow-list are filled in by the bot and never by the model, each call is paid
  for before it runs, and the tools container checks the server again.
- Control is one-way: Kurt's phone -> Claude Code on the PC -> docker compose ->
  containers. Nothing in a container can reach back to the PC, the LAN or Claude.

## Why it was built this way (design decisions)

- Self-written instead of an off-the-shelf bot (it started on llmcord): llmcord could
  not do budgets, jump-ins, owner-only DMs, /rule, /feed or blocked channels.
- Separate key-less fetcher: browsing arbitrary sites and parsing untrusted files is the
  riskiest part, so it happens where there are no secrets to steal; the containers with
  the tokens stay locked to Discord + DeepSeek.
- Tools only for owner servers: a server with strangers could be used to make the model
  fetch a page full of instructions, or just to burn the budget, so public servers'
  requests never contain a tool list. The tools that do exist are read-only, capped and
  run in a key-less container, so a prompt injection can at worst waste a few lookups.
- Search runs on a self-hosted SearXNG: free and needs no key, at the cost of quality
  (the free engines rate-limit and return junk). A paid search API is the likely fix.
- Group memory lives in a local folder rather than a pinned Discord message, so the chat
  stays clean and Kurt can edit the profile directly.
- Reddit via public RSS: Reddit blocks anonymous JSON requests; the official API may be
  added later if RSS proves unreliable. OCR for images is a possible later addition.
- Cost: a full day is a few cents of DeepSeek usage; the account holds a small prepaid
  balance, so spending stops when it runs out.
