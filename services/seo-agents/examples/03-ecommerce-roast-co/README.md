# 03 — Roast & Co. (e-commerce: guest articles + product highlights)

**The story.** Roast & Co. is an online specialty-coffee roaster. It grows by
publishing genuinely useful brewing guides on **other platforms** (like Medium)
and turning readers into first-time buyers. So it uses the `external_article`
channel, a warm custom voice, and it wants its **bestselling products linked**
inside the draft.

**What this example shows:**

- **Templated analytics whose highlights are product links** — turning your store
  data into "here are products to reference, with URLs."
- **The `external_article` channel** with a `platform_name` ("Medium").
- **The self-review doing its job** — it flags a real issue in the offline draft.

## The files

- `data/analytics.json` — the store's sales export (revenue, orders, bestsellers).
- `tenant.json` — templated analytics + a custom `external_article` prompt.
- `input.json` — an `external_article` run for Medium.

## How the data links to the templates

**Your data** (`data/analytics.json`, trimmed):

```json
{
  "period": "last_28_days",
  "revenue": 42180.0,
  "orders": 712,
  "bestsellers": [
    { "name": "Ethiopia Yirgacheffe", "handle": "ethiopia-yirgacheffe", "units": 210 }
  ]
}
```

**The summary template** formats the headline numbers (`|round|int` drops the
decimals; `|replace` tidies the period label):

```jinja
${{ data.revenue|round|int }} from {{ data.orders }} orders in the {{ data.period|replace('_', ' ') }}. Top seller: {{ data.bestsellers[0].name }}.
```

**The highlights template** turns each bestseller into a linkable product — this
is what lets the writer reference real products with real URLs:

```jinja
[{% for p in data.bestsellers[:limit] %}{"label": {{ (p.name + " — " + p.units|string + " sold")|tojson }}, "url": {{ ("https://roast.example.com/products/" + p.handle)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]
```

## See it flow into the prompt

```bash
python ../../src/main.py preview-prompt
```

Real output (trimmed) — the summary and the product links are yours, and the
`platform_name` from `input.json` becomes "Medium":

```
You are writing a guest article for Medium on behalf of Roast & Co.
Product: Roast & Co. is an online specialty-coffee roaster: ...
Target topic: "..."
...
Context you may weave in only if it fits: $42180 from 712 orders in the last 28 days. Top seller: Ethiopia Yirgacheffe.
Products you may reference naturally (add their link to internal_links):
- Ethiopia Yirgacheffe — 210 sold — https://roast.example.com/products/ethiopia-yirgacheffe
- Colombia Huila — 168 sold — https://roast.example.com/products/colombia-huila
- Espresso Blend No. 5 — 140 sold — https://roast.example.com/products/espresso-blend-5
```

## Run the full draft — and watch the self-review

```bash
python ../../src/main.py run
```

The result includes a self-review note (real offline output):

```json
"qa_notes": ["Target keyword/topic \"anonymous social media app\" not found in title/headings/body."]
```

That's the self-review working correctly: it noticed the target keyword never
made it into the draft. (Offline, the keyword and the body both come from the
mocks, so they don't match — with real tools they line up. See [What's real
offline](../README.md#whats-real-offline-and-what-isnt).) The point is that
`qa_notes` is where you look before publishing anything.

## Go live

Same swaps as the other examples (`llm_provider: "gemini"`, `gsc_provider:
"google"`). If your store analytics is behind an API rather than a file:

```jsonc
{
  "analytics_source": "api",
  "analytics_api_url": "https://shop.roast.example.com/internal/analytics",
  "analytics_api_headers": { "Authorization": "Bearer YOUR_INTERNAL_API_KEY" }
}
```

The two templates don't change — only where the data comes from.
</content>
