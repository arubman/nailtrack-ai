# NailTrack AI

NailTrack keeps a private photo record of fingernail growth, logs regimens you choose to track, and (in later phases) aligns those photos and sketches how a nail may look over the next few weeks.

NailTrack shares wellness observations only. It is not medical advice and does not diagnose, treat, or predict disease. A treatment's `velocity_modifier` is a number you set for your own forecast. The app does not claim that a supplement, oil, or coating changed your growth rate.

The backend covers accounts, private storage, classical vision, and forecasts. The React PWA is still ahead. Measurements and forecasts are computed, not stubbed.

## Layout

```
backend/
  app/                  FastAPI application
    main.py             App factory, CORS, lifespan
    config.py           Environment settings and quality thresholds
    database.py         SQLAlchemy 2.0 engine (SQLite locally, PostgreSQL in prod)
    models.py           users, finger_baselines, nail_captures, treatments
    schemas.py          Pydantic v2 contracts, including insight disclaimer
    auth.py             Entra External ID JWTs, or the gated dev user
    storage.py          Private Azure Blob SAS URLs, or the local authenticated route
    reference_white.py  Von Kries gains from a card white/gray patch
    constants.py        Horizons, Gabor normalization, wellness copy
    services/           Quality, MediaPipe hands, segmentation, alignment, metrics
    api/                health, session, fingers, calibrate, captures, treatments, media
  models/hand_landmarker.task
  alembic/              PostgreSQL (and SQLite) migrations
  scripts/tune_thresholds.py
  requirements.txt
  startup.sh            App Service boot: libegl1, headless OpenCV, gunicorn
  startup.txt           `bash startup.sh` so Oryx picks the script up
  runtime.txt           python-3.11
```

## Local development

Python 3.11 is the App Service runtime. The code also runs on 3.12.

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install --force-reinstall --no-deps "opencv-python-headless==5.0.0.93"
uvicorn app.main:app --reload --port 8000
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) and [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

With no environment variables the app uses:

| Setting | Local default |
| --- | --- |
| `ENVIRONMENT` | `development` |
| `AUTH_MODE` | `dev` (one local user, no bearer token) |
| `DATABASE_URL` | `sqlite:///./nailtrack.db` (tables created on startup) |
| `STORAGE_BACKEND` | `auto` → local folder `data/uploads` when no Azure connection string is set |

Copy `backend/.env.example` to `backend/.env` to override any of these. `.env` is gitignored.

```bash
cd backend
pytest
```

Alembic is optional locally because SQLite creates tables on startup. To exercise the migration instead:

```bash
cd backend
AUTO_CREATE_TABLES=false alembic upgrade head
```

Do not run `create_all` and Alembic against the same populated database. Use one of the two.

## Authentication

Every `/api/v1` route resolves the caller with `get_current_user`.

`AUTH_MODE=dev` (development and test only) ignores `Authorization` and uses a single user (`DEV_USER_SUBJECT`, default `local-dev-user`). The process logs a warning when this is on. `ENVIRONMENT=production` refuses to start in dev mode, so the bypass cannot be left on in Azure by forgetting a setting.

`AUTH_MODE=entra` validates an **access token** from Microsoft Entra External ID:

- Signature via the tenant JWKS (`RS256` only)
- `iss`, `aud`, `exp` / `nbf`, with a small clock skew
- `tid` must match `ENTRA_TENANT_ID`
- Stable user id is `{tid}:{oid}` (`sub` is the fallback when `oid` is absent)
- Optional `ENTRA_REQUIRED_SCOPE` (must appear in `scp` or `roles`)
- Optional `ENTRA_ALLOWED_AZP` (must match `azp` or `appid`, typically the SPA client id)

Issuer and JWKS default to the External ID host when `ENTRA_TENANT_SUBDOMAIN` and `ENTRA_TENANT_ID` are set:

```text
https://{subdomain}.ciamlogin.com/{tenantId}/v2.0
https://{subdomain}.ciamlogin.com/{tenantId}/discovery/v2.0/keys
```

If the tenant's OpenID metadata uses a different host (some tenants use `{tenantId}.ciamlogin.com`, or `login.microsoftonline.com`), set `ENTRA_ISSUER` and `ENTRA_JWKS_URL` from `/.well-known/openid-configuration`. `ENTRA_AUDIENCE` is a comma-separated list when the API should accept both the client id and `api://{client-id}`.

`GET /health`, `GET /`, and the OpenAPI pages do not require a token. App Service probes cannot send one. They expose no user data.

## Private images

Blob keys are what the database stores (`users/{user_id}/...jpg`). It does not store permanent public URLs.

- **Azure:** the container stays private. The API never sets public access, and it refuses to start if it can see that the container allows anonymous reads. Clients receive a read-only SAS URL that expires after `SAS_URL_TTL_SECONDS` (default 300, allowed range 30–3600). Phase 1 signs that SAS with the account key in the connection string.
- **Local:** `GET /api/v1/media/{key}` serves the file only when the key is under `users/{current_user_id}/` and the caller is authenticated. The dev folder is not mounted as static files.

Allowed objects are JPEG, PNG, and WebP. Masks are PNG (class index 0 background, 1 lunula, 2 nail bed, 3 free margin).

## Data model

| Table | Scope |
| --- | --- |
| `users` | Entra subject, or the single dev user |
| `finger_baselines` | Unique on `(user_id, finger_code)`, not on `finger_code` alone |
| `nail_captures` | `baseline_id` foreign key. `user_id` and `finger_code` are denormalized and constrained to that baseline |
| `treatments` | `user_id`. `target_fingers_json` is finger codes or `["ALL"]` |

Deleting a user deletes their baselines, captures, and treatments.

Baseline columns that later calibration will fill:

- `calibrated_width_mm` — lateral nail-fold width in millimetres
- `scale_reference_type` / `scale_reference_width_mm` — `ID1_CARD` (85.60 mm) or `MANUAL_MM`
- `reference_white_json` — linear sRGB mean of the **white area of the ID-1 card** (or a gray/white card), plus Von Kries gains. Periungual skin is not the reference. See `app/reference_white.py`.
- `baseline_image_key`, `anchor_keypoints_json` — Day-0 image key and fold / DIP-crease points

Capture columns reserved for segmentation and correction (no model is trained yet):

- `segmentation_mask_key` — automatic mask
- `corrected_mask_key` — the user-corrected mask, kept beside the automatic one so a correction can become a training pair
- `mask_source` — `auto_classical`, `user_corrected`, or `model`
- A check constraint ties the source to the key that must be present

`observed_velocity_mm_day` starts at `DEFAULT_VELOCITY_MM_DAY` (default 0.11) when the ORM inserts a baseline. The column's server default stays 0.11 for non-ORM inserts.

## HTTP API (Phase 1)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | no | Liveness, including a database ping |
| `GET` | `/api/v1/me` | yes | Current user, disclaimer, thresholds, forecast horizons, slider range |
| `GET` | `/api/v1/fingers` | yes | All ten fingers, with this user's baseline if one exists |
| `GET` | `/api/v1/fingers/{finger_code}` | yes | One finger |
| `POST` | `/api/v1/treatments` | yes | Log a regimen |
| `GET` | `/api/v1/treatments` | yes | List, filter by category, finger, or `active_only` |
| `GET/PATCH/DELETE` | `/api/v1/treatments/{id}` | yes | Read, update, delete. Another user's id is a 404 |
| `POST` | `/api/v1/calibrate` | yes | ID-1 or manual card, white reference, Day-0 baseline |
| `POST` | `/api/v1/captures/analyze` | yes | Quality gate, align, measure. 422 with reasons unless `force_save` |
| `POST` | `/api/v1/captures/{id}/mask` | yes | Store a user-corrected 4-class mask and remeasure |
| `GET` | `/api/v1/fingers/{code}/timeline` | yes | History (`t<0`), today (`t=0`), forecasts at +7, +14, +30, +60, +90 |
| `GET` | `/api/v1/media/{key}` | yes | Local private-file read |

`GET /api/v1/me` is the source of truth for the client HUD:

- blur Laplacian minimum **110.0**
- glare ratio maximum **0.08** (fraction of nail-plate pixels, not a percent)
- tilt maximum **10** degrees
- ridge-score insight threshold **0.35**
- default velocity **0.11** mm/day, population velocity **0.115** mm/day
- forecast horizons **+7, +14, +30, +60, +90** (not +15)
- timeline slider **−90..+90**, step 1

Any response that carries wellness insights includes `disclaimer`. `WellnessInsight` and `InsightList` in `schemas.py` are the shape later phases should return. The disclaimer text is `NOT_MEDICAL_ADVICE` in `app/constants.py`.

Treatment `velocity_modifier` must be greater than 0 and at most 5. The default is 1. Omitting it does not imply the app measured an effect.

Creating, updating, or deleting a treatment rebuilds that user's forecast cache. `velocity_modifier` is the user's multiplier. Active modifiers for the finger (or `ALL`) are multiplied, then clamped to 0.05–5, and that product is `alpha_tx` in the growth formula.

## Thresholds and the Gabor score

Override any threshold with the environment variables in `.env.example` (`BLUR_LAPLACIAN_MIN`, `GLARE_RATIO_MAX`, `TILT_DEG_MAX`, `RIDGE_SCORE_INSIGHT_THRESHOLD`, `GABOR_ENERGY_FLOOR`, `DEFAULT_VELOCITY_MM_DAY`, `POPULATION_VELOCITY_MM_DAY`).

The longitudinal ridge score is the share of L2-normalized Gabor energy that is longitudinal, gated by `GABOR_ENERGY_FLOOR` so a smooth nail scores 0. The full procedure, kernel constants (`ksize=21`, `sigma=3.0`, `lambda=8`, `gamma=0.5`, `theta=0` and `pi/2`), and the reason a ratio is used instead of a camera-specific gain are in `app/constants.py` (`GABOR_RIDGE_SCORE_NORMALIZATION`). A score above the insight threshold means at least 35% of the oriented energy is longitudinal. That shows a wellness observation, not a diagnosis.

Glare pixels are those with HSV `V > 245` and `S < 25` (`GLARE_VALUE_MIN`, `GLARE_SATURATION_MAX`). The pass/fail cutoff is the configurable ratio.

The Phase 3 length change at horizon `t` is:

```text
delta_mm(t) = (0.7 * v_observed + 0.3 * v_population) * velocity_modifier * t
```

`v_population` and the default `v_observed` are config. The 0.7 / 0.3 weights are `VELOCITY_OBSERVED_WEIGHT` and `VELOCITY_POPULATION_WEIGHT`.

White balance, before CIELAB, is Von Kries in linear sRGB from the stored reference patch:

- ID-1 card white (or another white reference) maps onto D65 `(1, 1, 1)`
- A gray card is neutralized (channels equalized) without being pushed to white, because gray is not D65

## Calibration card

`POST /api/v1/calibrate` with `reference_type=ID1_CARD` looks for ArUco `DICT_4X4_50` markers 0, 1, 2, 3 (clockwise from the top-left of the printed card) before the generic rectangle detector. Marker positions, the white patch, and the 18% gray patch are the printed geometry: white is the Von Kries reference, gray is stored beside it. A card with no markers still uses the quadrilateral fallback.

## Forecasts and wellness notes

`GET /api/v1/fingers/{code}/timeline` uses the latest capture as `t=0`. Older aligned frames are negative days. Forecast frames are `+7, +14, +30, +60, +90` and include control-mesh vertices (`dx`, `dy`, `pinned`) so a client can interpolate. Length change is

```text
delta_mm(t) = (0.7 * v_observed + 0.3 * v_population) * alpha_tx * t
```

Background, lunula, and the proximal 85% of the nail bed stay pinned. The free margin moves along the finger by that many pixels, with an inward lateral taper of `0.02 * delta`. When the free margin is shorter than 1.5 mm, the grown band is filled from the distal edge plus light longitudinal noise. Spots and cracks move at `v_observed * t` and are marked clipped once they pass the forecast tip. Each insight includes `disclaimer`.

A new capture updates `observed_velocity_mm_day` only when the nail is longer than the previous one and at least a day has passed. A shorter nail is treated as a trim, not negative growth.

## Azure App Service

Deploy the **`backend/`** directory as the site root so Oryx finds `requirements.txt`, `runtime.txt`, and `startup.txt`. Stack: Python 3.11, Linux. `startup.txt` runs `bash startup.sh`, which:

1. Installs `libegl1`, `libgl1`, `libgles2`, `libglib2.0-0`, and `libgomp1` when `apt-get` is available. MediaPipe's native library needs both EGL and `libGLESv2.so.2`.
2. Reinstalls `opencv-python-headless==5.0.0.93` with `--no-deps` so MediaPipe's `opencv-contrib-python` dependency does not leave a GUI build on the import path.
3. Starts gunicorn with a uvicorn worker on `${PORT:-8000}`.

`startup.txt` is:

```text
gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --graceful-timeout 30 --keep-alive 5 --forwarded-allow-ips=* --access-logfile - --error-logfile - app.main:app
```

Oryx runs that command in a shell, so `PORT` expands. The fallback is 8000, which matches App Service's default `WEBSITES_PORT`. Two workers are for PostgreSQL. Do not point this command at a SQLite file.

Production startup fails unless all of the following are true:

- `ENVIRONMENT=production`
- `AUTH_MODE=entra` and the Entra settings above
- `DATABASE_URL` is PostgreSQL (`postgresql://` is rewritten to `postgresql+psycopg://`)
- `AZURE_STORAGE_CONN_STRING` (or `AZURE_STORAGE_CONNECTION_STRING`) includes `AccountName` and `AccountKey`
- `AZURE_STORAGE_CONTAINER` (default `nailtrack-private`) is a private container

Run migrations as a deploy step, not via `create_all`:

```bash
alembic upgrade head
```

Set `AUTO_CREATE_TABLES=false` in production (that is already the default when the URL is Postgres).

Other useful settings: `CORS_ORIGINS` (comma-separated; default `http://localhost:5173,http://127.0.0.1:5173`), `SAS_URL_TTL_SECONDS`, `LOG_LEVEL`.

`opencv-python-headless` 5.0.0 is the build the pipeline was checked against (`GUI: NONE`). The hand model is `backend/models/hand_landmarker.task`.

GitHub Actions workflow `.github/workflows/azure-deploy.yml` runs the tests on pull requests and, on `main`, deploys `backend/` with `azure/webapps-deploy`. Set these repository secrets:

| Secret | Purpose |
| --- | --- |
| `AZURE_WEBAPP_NAME` | App Service name |
| `AZURE_WEBAPP_PUBLISH_PROFILE` | Deployment Center → Download publish profile |

An OIDC deploy is the alternative if you would rather not store a publish profile. Create a federated credential for `repo:arubman/nailtrack-ai:ref:refs/heads/main`, then replace the deploy step with `azure/login` using `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. The workflow comments point at that swap. The frontend Static Web App job belongs in Phase 4.

Tune thresholds on a folder of real photos without changing the defaults:

```bash
python scripts/tune_thresholds.py ./sample_nails --csv metrics.csv
```

## Tests

`pytest` from `backend/` covers:

- model round-trips, per-user uniqueness, capture/baseline scoping in the database, mask-source and ridge-score checks, cascade delete
- portable UUID / JSON DDL (no native Postgres enums, no JSONB)
- Alembic upgrade, an ORM insert against the migrated schema, and downgrade
- dev-mode treatments (create, list, patch, delete, another user's id hidden)
- Entra JWT checks (audience, issuer, tenant, scope, authorized party, expiry, `alg=none`) with a locally signed key, and the 401 path when auth mode is `entra`
- finger listing scoped to the caller, the authenticated media route, and a read-only SAS URL
- production settings refusing dev auth, SQLite, and local disk
- synthetic nails and cards: class order, ray-cast lengths, ID-1 scale, Von Kries Lab, Gabor orientation, homography recovery, ECC affine fallback
- a blank frame returns no MediaPipe hand, and analyze then responds 422 `RETAKE_NO_HAND` even with `force_save`
- headless OpenCV still exposes GrabCut, ECC, Gabor, and SIFT

## What Phase 4 still owns

- The React/TypeScript PWA: camera HUD, the −90..+90 scrubber that interpolates `mesh` vertices, and the treatment logger wired to this API.
- Add the Static Web Apps job to `.github/workflows/azure-deploy.yml`.
- Confirm `GABOR_ENERGY_FLOOR` (default `1e-4`) with `scripts/tune_thresholds.py` once 10–20 real nail photos exist. The classical segmenter and spot detector will need that set; corrected masks are already stored beside the automatic ones.
- Account-key SAS can later move to user-delegation SAS and a managed identity.
- If the App Service image cannot run `apt-get` during startup, use a custom container that already has `libegl1` and `libgles2`. `startup.sh` continues when apt fails and still forces the headless OpenCV reinstall.
