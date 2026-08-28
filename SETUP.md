# Setup — Lebanon product scanner

This is an n8n workflow. Once a day at 09:00 Beirut it searches for products
trending in the US, has Claude score each one for the Lebanese market, sends you
the shortlist on Telegram, and logs every score to a Google Sheet.

Setup is about 25 minutes, most of it waiting on Google's OAuth screen.

If you have never used n8n: a workflow is a chain of **nodes**. Each node gets
the previous node's output as JSON and passes its own JSON on. Anywhere you see
`{{ ... }}` in a field, that is an expression — n8n evaluates the JavaScript
inside it at runtime. A field that contains an expression shows a small `fx`
toggle in the UI and its stored value begins with `=`. You do not need to write
any of these; they are already in the file.

---

## 1. Import the workflow

1. Open n8n → **Workflows** → **Import from File** (top-right `...` menu).
2. Select `workflow.json`.
3. It imports **inactive**, with red warning triangles on four nodes. That is
   expected — those nodes point at credentials that do not exist yet. Sections
   2–5 fix that.

Do not activate it yet.

---

## 2. Serper (Google search results as JSON)

Serper gives you Google results as clean JSON instead of scraping. Four searches
a day at ~$0.30 per thousand searches is roughly **$0.004/month**. The free tier
(2,500 searches) covers about 20 months on its own.

1. Sign up at <https://serper.dev>, copy the API key from the dashboard.
2. In n8n: **Credentials** → **Add credential** → search for
   **Header Auth** (its internal name is `httpHeaderAuth`).
3. Fill in:
   - **Name** (of the credential itself): `SERPER_API_KEY`
   - **Header Name**: `X-API-KEY`
   - **Header Value**: your key
4. Save.
5. Open the **Serper Search** node → **Credential for Header Auth** → pick
   `SERPER_API_KEY`.

Header Auth is n8n's generic "send this header on every request" credential. It
exists so the key lives in n8n's encrypted credential store instead of sitting in
plain text inside the workflow, which is what you want when you export or share
the JSON.

---

## 3. Anthropic

1. Get a key at <https://console.anthropic.com> → **API Keys**. Put some credit
   on the account; this workflow does not run on the free trial indefinitely.
2. Add a **second** Header Auth credential (same type as Serper, different
   values):
   - **Name**: `ANTHROPIC_API_KEY`
   - **Header Name**: `x-api-key`
   - **Header Value**: your key
3. Open the **Claude Score Products** node → set its credential to
   `ANTHROPIC_API_KEY`.

The other two headers this API needs (`anthropic-version` and `content-type`)
are already set as plain header parameters in the node — leave them alone.

**Cost per run:** the input blob is capped at 12,000 characters (~3,000 tokens)
and the reply is capped at 4,000 tokens. At Sonnet pricing that is well under a
cent per run, so under $0.30/month. The cap on `max_tokens` is a ceiling, not a
target; a normal reply is 10–20 products and nowhere near it.

---

## 4. Telegram

### 4a. The bot

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts.
2. He replies with a token that looks like
   `8123456789:AAH_ExampleTokenGoesHere_1234567`.
3. In n8n: **Add credential** → **Telegram API** → paste the token → name it
   `Telegram Bot` → Save.
4. Set that credential on **both** Telegram nodes: `Telegram: Shortlist` and
   `Telegram: Nothing Found`.

### 4b. The chat ID — this is the step people get stuck on

A bot cannot start a conversation with you. **You must message it first**, or
every send fails with `403: bot can't initiate conversation with a user`.

1. Open your bot in Telegram (BotFather's message contains a `t.me/...` link)
   and press **Start**, or just send it `hi`.
2. Now fetch the chat ID. Substitute your token:

   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | python3 -m json.tool
   ```

   Note the URL is `/bot<TOKEN>/` — the literal word `bot` immediately followed
   by the token, no slash between them. This trips people up.

3. Find `"chat": {"id": 123456789, ...}` in the output. That number is your chat
   ID. For a personal chat it is positive; for a group it is negative and starts
   with `-100`.

4. If `result` is an empty array `[]`, one of these is true: you never pressed
   Start, or the workflow/another poller already consumed the update. Send the
   bot another message and re-run the curl.

5. Paste the ID into the **Chat ID** field of **both** Telegram nodes, replacing
   `REPLACE_WITH_YOUR_TELEGRAM_CHAT_ID`.

To send to a group instead: add the bot to the group, send a message there, and
use the negative ID from the same `getUpdates` call.

---

## 5. Google Sheets

1. Create a spreadsheet. In the **first row** of `Sheet1`, type these seven
   headers, in this order, spelled exactly like this:

   ```
   date	product	score	est_retail_usd	est_landed_usd	reason	risk
   ```

   The append node matches on header text. A typo here means a silently blank
   column, so copy-paste rather than typing.

2. Grab the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_PART`**`/edit`

3. In n8n: **Add credential** → **Google Sheets OAuth2 API**. n8n's own guide
   for creating the Google Cloud OAuth client is at
   <https://docs.n8n.io/integrations/builtin/credentials/google/oauth-generic/> —
   follow it, it is the fiddliest part of this setup. Short version: create a
   Google Cloud project, enable the **Google Sheets API**, create an **OAuth
   client ID** of type *Web application*, and paste n8n's **OAuth Redirect URL**
   (shown in the credential screen) into the client's *Authorized redirect URIs*.
   Then click **Connect my account** in n8n.

4. Open the **Log All Scores** node:
   - **Document**: switch the selector to **By ID** and paste your spreadsheet
     ID (or leave it on **From list** and pick it, now that OAuth is connected).
   - **Sheet**: `Sheet1`, or whatever you named the tab.
   - Leave the seven column mappings as they are.

---

## 6. Test it without spending anything

Section 8 of the spec has real scan output. Use it to exercise nodes 6–9 without
calling Serper or Anthropic.

1. Click the **Claude Score Products** node.
2. In the **OUTPUT** panel, click **Edit Output** (the pencil). This *pins* data
   to the node.
3. Paste this — it is the sample data wrapped in the shape the Anthropic API
   actually returns, which is what the next node parses:

```json
[
  {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [
      {
        "type": "text",
        "text": "[{\"product\":\"Electric baby nail trimmer\",\"score\":8,\"reason\":\"Battery powered, tiny, solves real parent fear, silent demo works\",\"est_retail_usd\":16,\"est_landed_usd\":6,\"risk\":\"Parents hesitate on electric devices near infants\"},{\"product\":\"Car multi-function storage bag\",\"score\":8,\"reason\":\"No power needed, near-zero shipping weight, car-heavy country\",\"est_retail_usd\":13,\"est_landed_usd\":4,\"risk\":\"Low novelty, needs visual differentiation\"},{\"product\":\"Mini electric garlic chopper\",\"score\":7,\"reason\":\"Lebanese cooking runs on garlic, demo is instantly satisfying\",\"est_retail_usd\":17,\"est_landed_usd\":6,\"risk\":\"Must be USB rechargeable, not mains powered\"},{\"product\":\"Cooling neck fan\",\"score\":7,\"reason\":\"Battery power works during outages, heat runs through September\",\"est_retail_usd\":20,\"est_landed_usd\":7,\"risk\":\"Four week runway left, lithium customs friction\"},{\"product\":\"Pimple patches\",\"score\":7,\"reason\":\"Weightless shipping, no power, visible before-after, repeat purchase\",\"est_retail_usd\":10,\"est_landed_usd\":2,\"risk\":\"Counterfeits everywhere, one bad batch ends reputation\"},{\"product\":\"Magnetic phone charger\",\"score\":6,\"reason\":\"Power cuts make portable charging genuinely useful\",\"est_retail_usd\":22,\"est_landed_usd\":9,\"risk\":\"Saturated market, lithium cells expensive to air freight\"}]"
      }
    ],
    "stop_reason": "end_turn",
    "usage": { "input_tokens": 3000, "output_tokens": 800 }
  }
]
```

4. Click **Test workflow**. n8n uses the pinned data instead of calling
   Anthropic, so nodes 6, 7, 8a and 9 run for free.

You should get a Telegram message listing the top 5 (the magnetic phone charger
scores 6, survives the filter, but is cut by the top-5 cap) and six new rows in
the sheet.

**Pinned data only applies to manual test runs.** Scheduled production runs
ignore it, so you can leave it pinned. To clear it anyway, click the pin icon on
the node.

To test the other branch, pin `"text": "[]"` instead and re-run — you should get
`Scan complete. Nothing cleared the bar today.` and no shortlist.

When you are ready to test for real, unpin and click **Test workflow** again.
That run costs about one cent.

---

## 7. Activate

Toggle **Active** on, top-right. It now runs at 06:00 UTC daily.

06:00 UTC is 09:00 Beirut in summer (UTC+3) and **08:00 in winter** (UTC+2),
because the cron is evaluated in UTC and Lebanon observes DST. If you want a
fixed 09:00 local year-round, either set the workflow's timezone under
**Settings → Timezone** to `Asia/Beirut` and change the cron to `0 9 * * *`, or
edit the cron twice a year. The spec asked for `0 6 * * *`, so that is what
ships.

---

## 8. How the nodes fit together

```
Daily 06:00 UTC
      ↓
Search Queries          4 items out — one per query, month/year from $now
      ↓
Serper Search           runs 4×, once per query. Retry 3× / 5s
      ↓
Flatten Results         4 responses → 1 text blob, deduped, ≤12,000 chars
      ↓
Claude Score Products   one call: extraction + scoring. Retry 2×
      ↓
Parse and Filter        strip fences → JSON → drop score<6 → sort desc
      ↓                        ↓
Anything Survive?        Log All Scores   ← every product, filtered ones too
   ↓ true    ↓ false
Shortlist   Nothing Found
```

Three things worth knowing if you go editing:

**`Search Queries` emits four separate items, not one array.** In n8n a node
runs once per input item, so four items is what makes `Serper Search` fire four
times. Had it emitted one item holding an array, you would need an extra Split
Out node to fan it back out.

**`Parse and Filter` emits one item per product, and copies the same
`hasProducts` flag and prebuilt `message` onto every one.** That is what lets a
single Code node feed both a per-row sheet append and a single summary message.
Both Telegram nodes have **Execute Once** enabled, so they send one message from
the first item rather than one per product.

**`Log All Scores` hangs off `Parse and Filter`, not off the IF.** That is
deliberate — the sheet gets the 3/10 rejects too. Those rejects are the point:
they are the negative examples that make section 7 of the spec worth filling in.

**The Anthropic body is plain JSON with one expression in it.** The system
prompt sits in the node as a literal string, so you can read and edit it in the
n8n UI without touching code. Only `"content"` is
`{{ JSON.stringify($json.blob) }}` — `JSON.stringify` is what escapes the quotes
and newlines in the search text so the body stays valid JSON.

---

## 9. Two places the build departs from the spec

Both are noted so you can revert either in under a minute.

**Result URLs go into the blob.** The spec says to extract `title` and `snippet`.
The system prompt (used verbatim) asks Claude for a `source_url` field, which it
cannot fill from title and snippet alone — every value would be `null`. So
`Flatten Results` appends `[url]` to each line. To revert: delete the `link`
variable and the `lines.push` ternary in that node. The 12,000-char cap holds
either way; you get roughly 15% fewer results with URLs included.

**The "nothing found" message reports parse failures.** The spec fixes node 8b's
text as `Scan complete. Nothing cleared the bar today.` A failed parse also lands
on that branch, and sending "nothing cleared the bar" when Claude actually
returned something unreadable would hide a real bug. So the text is an
expression: the spec string on the normal path, and
`Scan complete. Claude response could not be parsed. Raw: ...` when
`Parse and Filter` sets `error: true`. To revert, replace the whole Text field
with the plain sentence.

One further judgement call, not a deviation: **product names are stripped of
`_ * ` [ ]` rather than backslash-escaped** before going into Telegram. Telegram's
legacy Markdown does not honour backslash escapes reliably — only MarkdownV2
does, and MarkdownV2 would also require escaping the `-`, `.`, `|` and `~`
already in the message format. Stripping five characters from a product name
guarantees the message sends. The **sheet keeps the original unmodified name**;
only the Telegram copy is sanitised.

---

## 10. Troubleshooting

| Symptom | Cause |
|---|---|
| `403 bot can't initiate conversation` | You never messaged the bot. Section 4b, step 1. |
| `400 chat not found` | Wrong chat ID, or you used the bot's own ID instead of yours. |
| `getUpdates` returns `"result": []` | No unconsumed messages. Send the bot another message. |
| Telegram sends nothing, no error | Zero items reached the node. Check the IF node's branches in the execution view. |
| `401` from Serper or Anthropic | Credential not attached to the node, or the header **name** is wrong — `X-API-KEY` for Serper, `x-api-key` for Anthropic. They are different. |
| Sheet rows land in the wrong columns | Header row text does not match the seven column names exactly. |
| A row with `(parse error)` | Claude returned non-JSON. The `raw` field in that execution holds the first 500 characters. |
| A row with `(no products extracted)` | Claude returned a valid but empty array — usually a bad search day, occasionally a blob of only listicles. |
| Every score is 7+ | The prompt anticipates this and tells Claude to re-score harder. If it persists, section 7 real data is what actually fixes it. |
| Workflow runs but nothing arrives on Telegram | Check the workflow is **Active**, not just saved. |

To read a past run: **Executions** in the left sidebar → pick one → click any
node to see exactly what went in and came out. This is the fastest way to debug
anything here.

---

## 11. Making the scoring actually good

Right now the scoring is generic reasoning about Lebanon. It is a plausible
prior, not knowledge. Section 7 of the spec — `SOLD WELL` / `FLOPPED` — is empty,
and until it isn't, treat every score as a guess with a confident tone.

The loop that fixes it:

1. Let the sheet fill for a few weeks. Every scored product lands there, rejects
   included.
2. When you actually test a product, record the outcome next to its row.
3. Once you have ~10 real outcomes, append them to the end of the system prompt
   in the `Claude Score Products` node, before the `Return ONLY a JSON array`
   paragraph:

   ```
   KNOWN RESULTS from this market. Score consistently with these:
   SOLD WELL: <product> — <why it worked>
   FLOPPED: <product> — <why it failed>
   ```

Ten real outcomes will move the scoring more than any amount of prompt
rewriting. The flops matter more than the wins — they are the ones the generic
prior gets wrong.
