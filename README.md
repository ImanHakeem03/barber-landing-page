# Hairlab — Barber Booking App

Online booking for **HAIRLAB. Barber & Salon**. Customers pick a date, a time slot and a barber who is working that slot. Staff manage barbers, daily schedules and appointments from an admin page.

**Live:** https://barber-landing-page-kohl.vercel.app · **Admin:** https://barber-landing-page-kohl.vercel.app/admin

## Features

- **Customer booking page** (`/`): choose a date and a 30-minute slot (11:00 AM – 9:30 PM), then a barber. Only barbers who are scheduled, on shift and not already booked for that slot are shown.
- **Admin dashboard** (`/admin`):
  - add, rename and delete barbers, with photo upload
  - turn shifts on and off
  - **Schedule Calendar**: a month view showing who works each day. Pick a day, then tap or drag a barber to make them bookable for the whole day, or fine-tune single slots.
  - live timetable of bookings that refreshes every 5 seconds
- **Optional integrations**: Google Calendar events and WhatsApp alerts (Twilio) for each booking. Both are skipped when they aren't configured.

## Tech stack

| Part | Choice |
| --- | --- |
| Backend | Python, FastAPI, SQLAlchemy 2 |
| Database | SQLite locally, Postgres (Neon) in production via psycopg 3 |
| Frontend | Plain HTML + Tailwind CSS (CDN), served by FastAPI |
| Hosting | Vercel (region `sin1`) |

## Project layout

```
main.py            FastAPI app: models, API routes, serves the two pages
google_cal.py      Google Calendar helper (service account)
index.html         Customer booking page
admin.html         Admin dashboard
static/            Logo and default avatars
vercel.json        Vercel config (Python service, Singapore region)
render.yaml        Optional Render blueprint (not used by the live site)
requirements.txt   Python dependencies
```

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Then open http://localhost:8000 (booking) and http://localhost:8000/admin (admin).

Without `DATABASE_URL`, the app creates a local `barbershop.db` SQLite file and seeds two barbers. Interactive API docs are at http://localhost:8000/docs.

## Environment variables

All of these are optional.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` / `POSTGRES_URL` | Postgres connection string. Falls back to SQLite. |
| `GOOGLE_CALENDAR_ID` | Calendar to add bookings to. It must be shared with the service account. |
| `GOOGLE_CREDENTIALS_JSON` | Full service-account JSON. Locally, a `credentials.json` file works too; it's git-ignored. |
| `GOOGLE_CALENDAR_DISABLED` | Set to any value to skip Google Calendar, for example during local testing. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Twilio credentials for WhatsApp alerts |
| `TWILIO_WHATSAPP_FROM` | Sender number (defaults to the Twilio sandbox) |
| `ADMIN_WHATSAPP_TO` | WhatsApp number that receives new-booking alerts |

## Deploy

Vercel deploys production automatically from the private mirror `imanhakeem/barber-landing-page`, branch **`live`**. That repo's `main` and `old-main` branches hold the old pre-Vercel version.

To publish, push `main` to both repos:

```bash
git ship
```

`git ship` is a local alias for `git push origin main`, followed by pushing `main` to the mirror's `live` branch, signed in as the `imanhakeem` GitHub account. To set it up on another machine:

```bash
git remote add imanhakeem https://github.com/imanhakeem/barber-landing-page.git
git config remote.imanhakeem.push refs/heads/main:refs/heads/live
git config alias.ship '!git push origin main && GH_TOKEN=$(gh auth token --user imanhakeem) git push imanhakeem'
``` The Neon Postgres database is attached through the Vercel Marketplace, which sets `DATABASE_URL`.

To deploy manually from your machine:

```bash
npx vercel@latest deploy --prod
```

> On Windows PowerShell, use `npx.cmd` if script execution is blocked.

## Using the scheduler

1. Open `/admin` and find **Schedule Calendar**. Each day shows the barbers working it: a black name means the full day, a grey name means part of the day.
2. Click a day, then tap (or drag in) each barber working that day. They become bookable for every slot.
3. To give someone a break, open **Fine-tune individual time slots** and remove the slots they're off.

A barber who isn't scheduled for a date never appears on the booking page for that date.
