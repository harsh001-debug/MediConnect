# MediConnect

MediConnect is a booking demo for clinic and video consultations. Patients can find doctors and book visits, doctors can manage their practice, and admins can review doctor applications and appointments. Guests can book urgent visits with a verified mobile number.

The React and Vite frontend runs locally. The FastAPI backend connects to MongoDB Atlas and the configured email, SMS, payment and transcription providers.

## Run a fresh clone locally

Install **Python 3.13**, **Node.js 22 LTS or newer**, and **pnpm 11**. An internet connection is required for the live integrations.

```sh
git clone "<repository-url>" MediConnect
cd MediConnect
npm install --global pnpm@11.19.0
```

Skip the last line if pnpm 11 is already installed.

### 1. Use the provided environment

The backend loads the provided `backend/.env` automatically. Use that file as supplied to connect to the same live MongoDB Atlas database and configured email, SMS, Razorpay Test Mode and Gemini accounts.

The Atlas project owner may need to allow the tester's public IP in **Network Access**. If the clone is missing `backend/.env`, get the file from the project owner before starting FastAPI. [MongoDB Atlas connection guide](https://www.mongodb.com/docs/atlas/connect-to-database-deployment/)

### 2. Install and start FastAPI

Open a terminal at the project root.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

On macOS or Linux, use `./.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.

Open [API health](http://localhost:8000/api/v1/health). A response of `{"status":"ok"}` confirms FastAPI can reach Atlas. [API docs](http://localhost:8000/docs) list the endpoints.

### 3. Install and start the frontend

Open a second terminal from the project root.

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open [MediConnect](http://localhost:5173). Vite forwards API requests to FastAPI on port 8000.

### 4. Test the live features

Use the existing demo doctors and slots in the supplied database. If the test database needs demo accounts or future slots, follow the [demo account guide](DEMO_ACCOUNTS.md).

Use the [live testing checklist](docs/INTEGRATIONS.md#live-testing-checklist) to confirm email delivery, SMS delivery, booking, checkout, refunds, video, notifications and transcription. Use inboxes and mobile numbers you control. Razorpay checkout uses Test Mode throughout.

## Source layout

| Path | Contents |
| --- | --- |
| `frontend/src/` | React pages, shared components, routing and styles. Start with `App.tsx`. |
| `backend/app/` | FastAPI entry point, MongoDB access, models and integration code. Start with `main.py`. |
| `backend/app/api/` | HTTP endpoints for accounts, doctors, appointments, guests and admins. |
| `backend/tests/` | Automated API, provider and database tests. |
| `backend/seed.py`, `backend/seed_west_bengal.py` | Demo account and slot setup. |
| `backend/manage_database.py`, `docs/DATABASE.md` | MongoDB audit, schema setup and validator tests. |

## Automated checks

From `backend/`, run `.\.venv\Scripts\python.exe -m pytest -q` in PowerShell or `./.venv/bin/python -m pytest -q` on macOS or Linux. The default suite uses an in-memory database and simulated provider responses. The [database guide](docs/DATABASE.md#verification) covers opt-in checks against a dedicated live database.

From `frontend/`, run `pnpm build` to type-check and build the site. The bundle is written to `backend/app/static/`. Restart FastAPI after the first build to open the bundled site at [localhost:8000](http://localhost:8000).

## Troubleshooting

- **The API health check fails.** Check the Atlas access list, database user, cluster connection string and test database name.
- **The doctor directory is empty.** Seed fictional doctors or approve a test doctor with the admin account.
- **The email or SMS code is missing.** Confirm the contact on the verification page, then check the provider setup and delivery status in the [integration guide](docs/INTEGRATIONS.md#email-and-mobile-verification).
- **Checkout is unavailable.** Check both Razorpay Test Mode keys and restart FastAPI.
