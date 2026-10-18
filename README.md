# Cutloom Ultimate: backend server (v2, points)

A SEPARATE service from the Cutloom app. It must stay online on a host you
control because it holds your fal.ai key. Never put that key inside the Cutloom
app: anything in a downloaded app can be extracted.

## How it works
- A customer buys a points pack on Lemon Squeezy. Lemon Squeezy sends a signed
  webhook and the points go to that customer's **email** balance. Duplicate
  deliveries are ignored, and a refunded order takes its points back.
- In Cutloom, the customer pastes their Ultimate license key. The server asks
  Lemon Squeezy who owns the key and uses that email's balance, so two keys
  from the same email share one balance.
- Four actions, each with a point cost you can change:

| Action | What it does | Default points | fal.ai model (change with an env var) |
|---|---|---|---|
| `image` | text to still image | 1 | `fal-ai/wan/v2.2-a14b/text-to-image` |
| `animate_fast` | image to 5s clip (turbo) | 4 | `fal-ai/wan/v2.2-a14b/image-to-video/turbo` |
| `animate` | image to 5s clip (standard) | 16 | `fal-ai/wan/v2.2-a14b/image-to-video` |
| `video` | text to 5s clip | 20 | `fal-ai/wan/v2.2-a14b/text-to-video` |

- A failed generation refunds exactly what it cost. So does a job cut off by a
  server restart. A finished job is never refunded twice.
- For the two animate actions, the customer's image is saved briefly on this
  server under a random unguessable name so fal.ai can fetch it, and deleted
  as soon as the job ends. Leftovers older than an hour are swept.

## Environment variables (Render: your service > Environment)
| Name | Value |
|---|---|
| `FAL_KEY` | your fal.ai API key |
| `LEMONSQUEEZY_ULTIMATE_PRODUCT_ID` | `1399775` (live). Use the test-mode ID only while testing |
| `LEMONSQUEEZY_WEBHOOK_SECRET` | the same long random string as in Lemon Squeezy's webhook |
| `ADMIN_TOKEN` | another long random string (manual points, testing) |
| `DATA_DIR` | folder for the database. On Render, a persistent disk path, e.g. `/var/data` |
| `PUBLIC_BASE_URL` | optional. Default `https://cutloom-ultimate.onrender.com` (used to give fal.ai a link to the temporary image) |
| `POINT_COSTS` | optional JSON, e.g. `{"image":1,"animate_fast":4,"animate":16,"video":20}` |
| `VARIANT_CREDITS` | optional JSON mapping variant names to points. Defaults: `200 points`, `600 points`, `2000 points` (the old `10/30/100 clips` names also work) |
| `FAL_MODEL_ENDPOINT`, `FAL_MODEL_IMAGE`, `FAL_MODEL_ANIMATE`, `FAL_MODEL_ANIMATE_FAST` | optional model overrides |
| `MAX_PENDING_JOBS`, `MAX_JOBS_PER_HOUR` | optional. Defaults 3 and 60 per customer |

`/api/health` shows which of these are set (yes/no, never the values) plus the current costs and models.

## Before selling to real customers
1. **Durable storage.** Render's free plan erases files on every restart, which would erase paid points. Use a paid always-on instance with a persistent disk and set `DATA_DIR` to it.
2. **Run one worker.** The restart-recovery step assumes one server process.
3. **Run one real generation of each action** (see below) and compare the amounts billed in fal.ai's dashboard with the point costs.
4. **Test a live purchase** (a 100%-off discount code works) and confirm points appear by themselves.
5. **Update the Privacy page, Terms and README.** Prompts, and customers' uploaded photos, pass through this server and fal.ai.
6. Set a **spend alert** in your fal.ai account.

## Testing with real fal.ai credit
These are the parts nobody has been able to test yet. The request fields below follow fal's documentation and third-party examples, not a live run:
- text-to-image: `prompt`, `image_size` (`portrait_16_9` / `landscape_16_9` / `square_hd`)
- image-to-video: `image_url`, `prompt`, `resolution`, `num_frames`, `frames_per_second` (the turbo variant is sent without frame fields)
- the image reaches fal as a link to `PUBLIC_BASE_URL/api/tmp/<random name>`

If fal rejects a request, the customer sees a friendly message and is refunded, and the server log shows the details. Fix the field in `build_input` in `main.py`.

Steps: set `LEMONSQUEEZY_ULTIMATE_PRODUCT_ID` to the TEST product ID (`1393591`) for the test session, use the Ultimate test-mode license key, give yourself points at `/docs` > `POST /api/admin/grant`, switch `"enabled"` to `true` in your own copy of `ultimate_config.json` (with the test product ID), then run each action once. Put the live ID back afterwards.

## Known limits
- Customer data is one JSON file. Fine to start; move to a real database if this grows.
- If a clip is generated but cannot be downloaded to the customer's PC after 3 tries, points are not restored automatically. Use the admin endpoint.
- Test mode and live mode are separate in Lemon Squeezy: separate products, keys and webhooks.
