# Lebanon product scanner

Once a day it searches for products trending in the US, has Gemini score each
one against the realities of selling in Lebanon, sends the shortlist to
Telegram, and logs every new product to a Google Sheet.

It is a filter, not a feed. A run that sends nothing is a valid run.

**It runs on GitHub Actions and costs nothing.** No server, no VPS, no machine
that has to stay on through a power cut.

```
schedule (06:00 UTC)
   └─ 4 rotating Google searches   (Serper, free tier)
      └─ merged into one ≤12k blob
         └─ scored 1-10 for Lebanon (Gemini, free tier)
            └─ drop score < 6, drop anything already reported
               ├─ Telegram: top 5
               └─ Sheet: every new product, rejects included
                  └─ commit state/seen.json
```

---

## Setup

About 20 minutes. Four secrets to collect, then one button to press.

### 1. Fork or use this repo

The code lives in `scanner/`, the schedule in `.github/workflows/scan.yml`.

> **Scheduled workflows only run on the repository's default branch.** If this
> code is sitting on a feature branch, the cron will never fire. Merge to your
> default branch (or make this branch the default) before expecting daily runs.

### 2. Serper — Google results as JSON

Free tier is 2,500 searches. At 4 a day that is about 20 months.

1. Sign up at <https://serper.dev> and copy the API key.
2. Save it as a repo secret named `SERPER_API_KEY` (see step 6).

### 3. Gemini — the scoring model

**A Gemini Advanced / Google One AI Premium subscription is not API access.**
They are separate products. Paying for the chatbot does not give you an API key
and does not raise your API limits. What you need is free.

1. Go to <https://aistudio.google.com/apikey>, sign in, click **Create API key**.
2. Accept the free tier. Do not attach a billing account unless you want paid
   limits.
3. Save it as `GEMINI_API_KEY`.

One run makes one API call. You will not come close to any free-tier limit.

**Model choice.** Defaults to `gemini-2.0-flash`, the safest free-tier bet. To
try a better one, set a repository *variable* (not a secret) named
`GEMINI_MODEL` to e.g. `gemini-2.5-pro`. Pro reasons better about the judgement
this prompt asks for — weighing saturation against novelty, being genuinely
skeptical. But Pro has much tighter free limits and is sometimes not free-tier
at all; if you see `429 RESOURCE_EXHAUSTED` or `404 NOT_FOUND`, remove the
variable. To see what your key can reach:

```bash
curl -s -H "x-goog-api-key: YOUR_KEY" \
  "https://generativelanguage.googleapis.com/v1beta/models" \
  | python3 -c "import json,sys; [print(m['name']) for m in json.load(sys.stdin)['models']]"
```

### 4. Telegram

**4a. The bot.** Message **@BotFather** → `/newbot` → follow the prompts. Save
the token he gives you as `TELEGRAM_BOT_TOKEN`.

**4b. The chat ID — the step people get stuck on.** A bot cannot start a
conversation with you. **Message it first** or every send fails with
`403: bot can't initiate conversation with a user`.

1. Open your bot (BotFather's message has a `t.me/...` link), press **Start**.
2. Then:

   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | python3 -m json.tool
   ```

   The URL is `/bot<TOKEN>/` — the literal word `bot` immediately followed by
   the token, no slash between. This trips people up.

3. Find `"chat": {"id": 123456789}`. Save that number as `TELEGRAM_CHAT_ID`.
   Personal chats are positive; groups are negative and start with `-100`.

If `result` is `[]`, you did not press Start, or something already consumed the
update. Send another message and retry.

### 5. Google Sheets — service account, not OAuth

OAuth needs a browser redirect, which does not exist on a CI runner. A service
account is a key file that works headlessly. One extra step: you share the sheet
with the service account like you would with a colleague.

1. Create a spreadsheet. **You do not need to add headers** — the scanner writes
   them on first run if row 1 is empty.
2. Copy the id from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit` → `SHEET_ID`.
3. At <https://console.cloud.google.com>: create a project → **APIs & Services →
   Library** → enable **Google Sheets API**.
4. **IAM & Admin → Service Accounts → Create service account**. No roles needed.
5. Open it → **Keys → Add key → Create new key → JSON**. A file downloads.
6. Open that file, copy its **entire contents**, save as
   `GOOGLE_SERVICE_ACCOUNT_JSON`.
7. Open the file and find `"client_email"` — something like
   `scanner@your-project.iam.gserviceaccount.com`. **Share your spreadsheet with
   that address, as Editor.** Skipping this is the single most common failure;
   it produces a `403`.

### 6. Add the secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | From |
|---|---|
| `SERPER_API_KEY` | step 2 |
| `GEMINI_API_KEY` | step 3 |
| `TELEGRAM_BOT_TOKEN` | step 4a |
| `TELEGRAM_CHAT_ID` | step 4b |
| `SHEET_ID` | step 5 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | step 5, the whole file |

Optional **variables** (the tab next to Secrets): `GEMINI_MODEL`, `SHEET_TAB`.

### 7. Test it, then let it run

Repo → **Actions → Daily product scan → Run workflow**. Two checkboxes:

- **Use built-in sample data** — runs the real Telegram and Sheets steps against
  the spec's sample products. Costs no API calls. Start here.
- **Print output only** — prints what it would send and touches nothing.

Both together is a completely inert run that proves the code works.

Once a sample run lands in Telegram and the sheet, run it with neither box
ticked. That run costs about one Serper credit and one Gemini call. After that
it runs itself daily.

---

## Running locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# no network, no config needed
.venv/bin/python -m scanner --sample --dry-run

# the tests
.venv/bin/python -m pytest
```

For a real local run, export the same six variables and drop `--dry-run`.
`--date 2026-09-14` overrides today, which is how you check query rotation.

---

## How it works

| Module | Job |
|---|---|
| `queries.py` | 4 queries per day from a rotating pool of 12 |
| `search.py` | Serper calls, then merge + dedupe + cap at 12,000 chars |
| `scoring.py` | one Gemini call; the system prompt and the response schema |
| `products.py` | parse the reply, score threshold, sheet row shape |
| `memory.py` | which products have already been reported |
| `telegram.py` | message format and delivery |
| `sheets.py` | service-account auth and append |
| `__main__.py` | the order the above run in |

### Query rotation

Three of the original four queries interpolate the month, so for a whole
calendar month they were fixed strings, and Google's top ten for them barely
moves. Every day returned the same products.

Now: a pool of 12, four drawn per day. Pool size and daily step (5) are coprime,
so the offset visits every position before repeating and no two consecutive days
run the same four. The four original queries are still in the pool; the eight
additions are different sources and four category sweeps.

To change it, edit `POOL_TEMPLATES`. Keep the pool size coprime with
`ROTATION_STEP` — a test enforces this.

### Cross-run memory

`state/seen.json` holds every product already reported. The job commits it after
each run, so you get git history of the whole catalogue, and the commit doubles
as the repo activity that stops GitHub disabling the schedule after 60 idle
days.

Products are matched on their **set of words**, not the raw string, so
`LED Strip Lights`, `led strip lights` and `Strip Lights LED` are one product.
Containment also matches — `Portable cooling neck fan` against a seen
`Neck fan` — but only when both names have two or more words, otherwise a
one-word `Fan` would swallow everything fan-adjacent.

It is deliberately blunt. `Pimple patches` and `Acne patches` are the same
product to you and different products to the matcher. Showing you a near
duplicate beats silently hiding something real.

Entries expire after 90 days and are capped at 500, so seasonal products can
resurface. **To reset**, commit `{"seen": []}` to `state/seen.json`.

### What reaches the sheet

Every **new** product, rejects included — a 3/10 is logged and simply left out
of the Telegram message. Those rejects are the point: they are the negative
examples that make the scoring worth tuning later.

Repeats are not re-logged. The sheet is a list of distinct products, not a daily
dump. The cost is recurrence data: you cannot see from the sheet that something
reappeared for three weeks. `times_seen` tracks that inside `state/seen.json` if
you want it.

### Failure behaviour

State is saved **last**, only after Telegram and Sheets both succeed. A failed
send leaves products unseen and they are reported again next run. You may see a
repeat; you never lose a product.

The model returning nothing usable — a safety block, a `MAX_TOKENS` cut-off, an
unparseable reply — still sends you a Telegram message naming the cause. Silence
is indistinguishable from a broken workflow.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `403 bot can't initiate conversation` | You never messaged the bot. Step 4b. |
| `400 chat not found` | Wrong chat ID, or you used the bot's id instead of yours. |
| `getUpdates` returns `[]` | No unconsumed messages. Message the bot again. |
| `sheets 403` | The spreadsheet is not shared with the service account's `client_email`. Step 5.7. |
| `sheets 404` | `SHEET_ID` is the whole URL instead of just the id. |
| `404 NOT_FOUND` from Gemini | That model id is not available to your key. Run the list-models curl in step 3. |
| `429 RESOURCE_EXHAUSTED` | Free-tier limit — almost always from switching to a Pro model. Remove the `GEMINI_MODEL` variable. |
| `401` from Serper | Key wrong or secret not set. |
| Workflow never runs on schedule | It is not on the default branch, or the repo was idle 60 days. |
| `Nothing new today` every day | De-duplication working as intended; the searches genuinely return the same products. |
| Scan ran but no sheet rows | Everything was a repeat. Expected. |
| `... could not be parsed` | Gemini ignored the schema. The message carries the first 400 characters. |

Every run's full log is under **Actions**. That is the fastest way to debug
anything here.

---

## Two honest limitations

**The scoring is an unvalidated prior.** Nothing here knows what actually sells
in Lebanon. It is a capable model reasoning from a well-written prompt, which is
not the same as knowledge. Prices in particular are invented — the prompt says
so itself. Verify on AliExpress before buying anything.

The fix is real outcomes. Once you have tested ~10 products, append them to
`SYSTEM_PROMPT` in `scanner/scoring.py`:

```
KNOWN RESULTS from this market. Score consistently with these:
SOLD WELL: <product> — <why it worked>
FLOPPED: <product> — <why it failed>
```

Ten real outcomes will move the scoring more than any amount of prompt
rewriting. The flops matter more than the wins.

**It reads listicles, not live trends.** Serper returns Google's top ten, which
for these queries means dropshipping SEO content — often recycled, often months
stale, and read by every other dropshipper running the same search. That sits
awkwardly against the prompt's own SATURATION rule: a product that made it into
a ranked "best dropshipping products" article is, by definition, already well
along the saturation curve. Rotation widens the slice; it does not fix the
source.

---

## The n8n version

`n8n/` holds the original build — an importable `workflow.json` and its own
`SETUP.md`. Same logic, same prompt, same behaviour, but it needs an n8n
instance running somewhere 24/7. Kept for reference, or if you would rather edit
the flow visually. You do not need it to run this.
