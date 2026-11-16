# NailTrack AI

NailTrack keeps a private photo record of fingernail growth, logs regimens you choose to track, and (in later phases) aligns those photos and sketches how a nail may look over the next few weeks.

NailTrack shares wellness observations only. It is not medical advice and does not diagnose, treat, or predict disease. A treatment's `velocity_modifier` is a number you set for your own forecast. The app does not claim that a supplement, oil, or coating changed your growth rate.

The backend covers accounts, private storage, classical vision, and forecasts. The React PWA in `frontend/` is the installable client. Measurements and forecasts are computed, not stubbed.

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
  startup.sh            Zip-deploy fallback: apt libraries, headless OpenCV, gunicorn
  startup.txt           `bash startup.sh` so Oryx picks the script up
  Dockerfile            Recommended App Service image (libraries already installed)
  runtime.txt           python-3.11
frontend/               Vite + React + TypeScript PWA
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

The PWA talks to that API. `VITE_AUTH_MODE=dev` matches the backend's dev user and sends no bearer token.

```bash
cd frontend
npm ci
npm test
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The dev server proxies `/api` to port 8000. Copy `frontend/.env.example` when you point the UI at another origin (`VITE_API_BASE_URL`). Production builds bake that URL in, so change it and rebuild.

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

**Recommended:** build `backend/Dockerfile` and run that image. It is Python 3.11 slim with `libegl1`, `libgl1`, `libgles2`, `libglib2.0-0`, and `libgomp1` already installed, then `opencv-python-headless==5.0.0.93` force-reinstalled after MediaPipe so the GUI OpenCV build is not what `import cv2` loads. The container starts gunicorn directly. Nothing is apt-installed at boot.

```bash
docker build -t nailtrack-api backend
docker run --rm -p 8000:8000 --env-file backend/.env nailtrack-api
```

On App Service, publish the image to a registry the app can pull, set `WEBSITES_PORT=8000`, and do not set a startup command that calls `startup.sh`.

**Zip-deploy fallback:** deploy the `backend/` directory so Oryx finds `requirements.txt`, `runtime.txt`, and `startup.txt`. `startup.txt` is `bash startup.sh`. That script apt-installs the same libraries when the sandbox allows it, force-reinstalls headless OpenCV, then execs gunicorn on `${PORT:-8000}`. If apt is not permitted, the script continues and the process will fail until you switch to the container image. The GitHub Action `azure/webapps-deploy` publishes this zip. Two workers are for PostgreSQL. Do not point this command at a SQLite file.

Production startup fails unless all of the following are true:

- `ENVIRONMENT=production`
- `AUTH_MODE=entra` and the Entra settings above
- `DATABASE_URL` is PostgreSQL (`postgresql://` is rewritten to `postgresql+psycopg://`)
- Blob storage is configured (see below)
- `AZURE_STORAGE_CONTAINER` (default `nailtrack-private`) is a private container

Blob reads are short-lived read-only SAS URLs. Set `AZURE_STORAGE_USE_MANAGED_IDENTITY=true` and `AZURE_STORAGE_ACCOUNT_NAME` so the API signs **user-delegation SAS** with `DefaultAzureCredential` (the App Service managed identity). Grant that identity **Storage Blob Data Reader** and **Storage Blob Delegator** on the account. The browser fetches those SAS URLs, so the storage account CORS list must allow the Static Web Apps origin for `GET` and `HEAD`. A connection string with `AccountName` and `AccountKey` remains the local and dev fallback, used only when the managed-identity flag is off. If both are set, user delegation wins.

Run migrations as a deploy step, not via `create_all`:

```bash
alembic upgrade head
```

Set `AUTO_CREATE_TABLES=false` in production (that is already the default when the URL is Postgres).

Other useful settings: `CORS_ORIGINS` (comma-separated; default `http://localhost:5173,http://127.0.0.1:5173`), `SAS_URL_TTL_SECONDS`, `LOG_LEVEL`. Production `CORS_ORIGINS` must include the Static Web Apps origin (`https://<name>.azurestaticapps.net`, plus any custom domain). The browser calls the App Service directly. The PWA does not proxy `/api` in production.

`opencv-python-headless` 5.0.0 is the build the pipeline was checked against (`GUI: NONE`). The hand model is `backend/models/hand_landmarker.task` (copied to `frontend/public/models/` for the live HUD).

## Static Web Apps

GitHub Actions (`.github/workflows/azure-deploy.yml`) runs backend pytest and frontend Vitest plus `npm run build` on pull requests. On `main` it zip-deploys `backend/` and uploads `frontend/` to Static Web Apps (`app_location: frontend`, `output_location: dist`).

| Name | Kind | Purpose |
| --- | --- | --- |
| `AZURE_WEBAPP_NAME` | secret | App Service name |
| `AZURE_WEBAPP_PUBLISH_PROFILE` | secret | Deployment Center → Download publish profile |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | secret | Static Web App → Manage deployment token |
| `VITE_API_BASE_URL` | variable | App Service origin, no trailing slash. Baked in at build time |
| `VITE_AUTH_MODE` | variable | `entra` in production |
| `VITE_ENTRA_CLIENT_ID` | variable | SPA app (client) id |
| `VITE_ENTRA_AUTHORITY` | variable | `https://<subdomain>.ciamlogin.com/<tenant-id>` |
| `VITE_API_SCOPE` | variable | `api://<api-app-id>/access_as_user` |

An OIDC deploy is the alternative to the publish profile. Create a federated credential for `repo:arubman/nailtrack-ai:ref:refs/heads/main`, then replace the API deploy step with `azure/login` using `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`.

The SPA redirect URI is the Static Web Apps origin. The API app registration exposes the scope in `VITE_API_SCOPE` and the SPA is an authorized client. `ENTRA_AUDIENCE` on the API must include that API's client id.

## Deploy end to end

1. Create the Entra External ID tenant apps: an API that exposes a scope, and a SPA whose redirect URI is the Static Web Apps origin.
2. Create PostgreSQL and the private blob container. Run `alembic upgrade head` against that database.
3. Turn on the App Service system-assigned identity. Assign Storage Blob Data Reader and Storage Blob Delegator. Set `AZURE_STORAGE_USE_MANAGED_IDENTITY=true` and `AZURE_STORAGE_ACCOUNT_NAME`. Add the Static Web Apps origin to the storage account CORS rules (`GET`, `HEAD`).
4. Set the API app settings: `ENVIRONMENT=production`, `AUTH_MODE=entra`, the Entra ids, `DATABASE_URL`, and `CORS_ORIGINS` including the Static Web Apps origin.
5. Deploy the API from `backend/Dockerfile` (recommended) or let the GitHub zip deploy use `startup.sh`.
6. Create the Static Web App, store its deployment token as `AZURE_STATIC_WEB_APPS_API_TOKEN`, and set the `VITE_*` repository variables to the API origin and the SPA registration.
7. Push `main` (or run the workflow) so the PWA is rebuilt with that API URL.
8. Open the site, sign in, calibrate a finger with the printed ArUco card, then save a measurement. `/health` stays anonymous.

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
- finger listing scoped to the caller, the authenticated media route, account-key SAS, and user-delegation SAS when a credential is configured

`npm test` from `frontend/` covers the API client (bearer token, dev mode with no token, raw 422 body), mesh interpolation, and slider frame selection from −90 to +90.
- production settings refusing dev auth, SQLite, and local disk
- synthetic nails and cards: class order, ray-cast lengths, ID-1 scale, Von Kries Lab, Gabor orientation, homography recovery, ECC affine fallback
- a blank frame returns no MediaPipe hand, and analyze then responds 422 `RETAKE_NO_HAND` even with `force_save`
- headless OpenCV still exposes GrabCut, ECC, Gabor, and SIFT

## Still open

Confirm `GABOR_ENERGY_FLOOR` (default `1e-4`) with `scripts/tune_thresholds.py` once 10–20 real nail photos exist. The classical segmenter and spot detector may need that set. Corrected masks are already stored beside the automatic ones.
