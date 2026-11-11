import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, LargeBinary, and_
from sqlalchemy.orm import sessionmaker, declarative_base, Session

import google_cal

# All paths are resolved relative to this file, so the app works no matter
# which folder you start uvicorn from (locally or on Render).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# --- DATABASE SETUP ---
# Vercel/Render: set DATABASE_URL to a Postgres URL. Locally: falls back to SQLite.
# (On Vercel without Postgres we fall back to /tmp, which is wiped often - demo only.)
_default_sqlite = "/tmp/barbershop.db" if os.getenv("VERCEL") else os.path.join(BASE_DIR, "barbershop.db")
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or f"sqlite:///{_default_sqlite}"

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Always use the psycopg (v3) driver, whatever prefix the host gives us.
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://"):
        if DATABASE_URL.startswith(prefix):
            DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(prefix):]
            break
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

MAX_PHOTO_BYTES = 3 * 1024 * 1024

STANDARD_SLOTS = [
    "11:00 AM", "11:30 AM", "12:00 PM", "12:30 PM",
    "1:00 PM", "1:30 PM", "2:00 PM", "2:30 PM",
    "3:00 PM", "3:30 PM", "4:00 PM", "4:30 PM",
    "5:00 PM", "5:30 PM", "6:00 PM", "6:30 PM",
    "7:00 PM", "7:30 PM", "8:00 PM", "8:30 PM",
    "9:00 PM", "9:30 PM",
]


# --- DATABASE MODELS ---
class Barber(Base):
    __tablename__ = "barbers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    is_on_shift = Column(Boolean, default=True)
    profile_picture = Column(String, nullable=True)


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)
    barber_id = Column(Integer, index=True)
    customer_name = Column(String)
    phone_number = Column(String)
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime)


class BarberPhoto(Base):
    """Profile photos live in the database so they survive redeploys (Vercel/Render disks are not permanent)."""
    __tablename__ = "barber_photos"
    barber_id = Column(Integer, primary_key=True)
    content_type = Column(String)
    data = Column(LargeBinary)


class BarberSchedule(Base):
    __tablename__ = "barber_schedules"
    id = Column(Integer, primary_key=True, index=True)
    barber_id = Column(Integer, index=True)
    date = Column(String, index=True)       # Format: "YYYY-MM-DD"
    time_slot = Column(String, index=True)  # Format: "1:00 PM"


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- STARTUP SEED ---
@asynccontextmanager
async def lifespan(_app: FastAPI):
    db = SessionLocal()
    try:
        if db.query(Barber).count() == 0:
            db.add_all([Barber(name="Hazim"), Barber(name="Arep")])
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title="Hairlab API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- PAGES (so one `uvicorn main:app` serves both the booking page and admin) ---
@app.get("/", include_in_schema=False)
def booking_page():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/admin", include_in_schema=False)
@app.get("/admin.html", include_in_schema=False)
def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "admin.html"))


@app.get("/photos/{barber_id}", include_in_schema=False)
def barber_photo(barber_id: int, db: Session = Depends(get_db)):
    photo = db.query(BarberPhoto).filter(BarberPhoto.barber_id == barber_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="No photo.")
    return Response(content=photo.data, media_type=photo.content_type,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health")
def health():
    return {"status": "ok"}


# --- WHATSAPP BACKGROUND TASK ---
def send_whatsapp_notifications(customer_name: str, customer_phone: str, barber_name: str, time_str: str):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        print("WhatsApp skipped: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set.")
        return

    whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    admin_whatsapp_to = os.getenv("ADMIN_WHATSAPP_TO")

    try:
        from twilio.rest import Client  # imported lazily so the app runs without Twilio set up

        client = Client(account_sid, auth_token)

        customer_msg = f"Hi {customer_name}! ✂️ Your Hairlab booking with *{barber_name}* for *{time_str}* is confirmed. See you soon!"
        client.messages.create(body=customer_msg, from_=whatsapp_from, to=f"whatsapp:{customer_phone}")

        if admin_whatsapp_to:
            admin_msg = f"🚨 *New Booking!*\nCustomer: {customer_name}\nPhone: {customer_phone}\nBarber: {barber_name}\nTime: {time_str}"
            client.messages.create(body=admin_msg, from_=whatsapp_from, to=admin_whatsapp_to)
    except Exception as e:
        print(f"WhatsApp Notification Error: {e}")


def get_barber_or_404(db: Session, barber_id: int) -> Barber:
    barber = db.query(Barber).filter(Barber.id == barber_id).first()
    if not barber:
        raise HTTPException(status_code=404, detail="Barber not found.")
    return barber


def parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must be in YYYY-MM-DD format.")


# --- CUSTOMER ENDPOINTS ---

@app.get("/customer/barbers-for-slot/")
def get_barbers_for_slot(date: str, time_slot: str, db: Session = Depends(get_db)):
    parse_date(date)
    assigned_records = db.query(BarberSchedule).filter(
        and_(BarberSchedule.date == date, BarberSchedule.time_slot == time_slot)
    ).all()

    barber_ids = [r.barber_id for r in assigned_records]
    if not barber_ids:
        return []

    # Hide barbers who already have a booking at this exact time.
    slot_dt = datetime.strptime(f"{date} {time_slot}", "%Y-%m-%d %I:%M %p")
    booked_ids = {
        a.barber_id for a in db.query(Appointment).filter(
            Appointment.barber_id.in_(barber_ids), Appointment.start_time == slot_dt
        ).all()
    }

    barbers = db.query(Barber).filter(and_(Barber.id.in_(barber_ids), Barber.is_on_shift == True)).all()  # noqa: E712
    return [b for b in barbers if b.id not in booked_ids]


@app.get("/customer/active-barbers/")
def get_active_barbers(db: Session = Depends(get_db)):
    return db.query(Barber).filter(Barber.is_on_shift == True).all()  # noqa: E712


@app.get("/available-slots/")
def get_available_slots(barber_id: int, date_iso: str, db: Session = Depends(get_db)):
    parse_date(date_iso)
    available_slots = []

    for slot_name in STANDARD_SLOTS:
        is_assigned = db.query(BarberSchedule).filter(
            and_(BarberSchedule.barber_id == barber_id, BarberSchedule.date == date_iso, BarberSchedule.time_slot == slot_name)
        ).first()
        if not is_assigned:
            continue

        slot_dt = datetime.strptime(f"{date_iso} {slot_name}", "%Y-%m-%d %I:%M %p")
        count = db.query(Appointment).filter(
            Appointment.barber_id == barber_id,
            Appointment.start_time == slot_dt
        ).count()

        # Strict 1 reservation per slot limit
        if count < 1:
            available_slots.append(slot_name)

    return available_slots


@app.post("/book-slot/")
def book_appointment(
    barber_id: int, customer_name: str, phone_number: str,
    start_time_iso: str, background_tasks: BackgroundTasks, duration_minutes: int = 30, db: Session = Depends(get_db)
):
    customer_name = customer_name.strip()
    phone_number = phone_number.strip()
    if not customer_name or not phone_number:
        raise HTTPException(status_code=400, detail="Name and phone number are required.")

    try:
        start_time = datetime.fromisoformat(start_time_iso)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start time.")
    end_time = start_time + timedelta(minutes=duration_minutes)

    barber = get_barber_or_404(db, barber_id)
    if not barber.is_on_shift:
        raise HTTPException(status_code=400, detail="This barber is not taking bookings right now.")

    slot_label = start_time.strftime("%I:%M %p").lstrip("0")
    scheduled = db.query(BarberSchedule).filter(
        and_(BarberSchedule.barber_id == barber_id,
             BarberSchedule.date == start_time.strftime("%Y-%m-%d"),
             BarberSchedule.time_slot == slot_label)
    ).first()
    if not scheduled:
        raise HTTPException(status_code=400, detail="This barber is not scheduled for that slot.")

    try:
        overlapping = db.query(Appointment).filter(
            Appointment.barber_id == barber_id,
            and_(Appointment.start_time < end_time, Appointment.end_time > start_time)
        ).with_for_update().all()

        if overlapping:
            raise HTTPException(status_code=400, detail="Barber is already booked for this slot.")

        db.add(Appointment(
            barber_id=barber_id, customer_name=customer_name,
            phone_number=phone_number, start_time=start_time, end_time=end_time
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise

    tz_start = start_time.isoformat() + "+08:00"
    tz_end = end_time.isoformat() + "+08:00"
    background_tasks.add_task(google_cal.create_google_event, customer_name, phone_number, barber.name, tz_start, tz_end)
    background_tasks.add_task(send_whatsapp_notifications, customer_name, phone_number, barber.name, slot_label)

    return {"status": "success", "message": "Successfully locked in!"}


# --- ADMIN ENDPOINTS ---

@app.get("/admin/barbers/")
def get_all_barbers(db: Session = Depends(get_db)):
    return db.query(Barber).order_by(Barber.id.asc()).all()


@app.post("/admin/update-shift/")
def update_barber_shift(barber_id: int, is_on_shift: bool, db: Session = Depends(get_db)):
    barber = get_barber_or_404(db, barber_id)
    barber.is_on_shift = is_on_shift
    db.commit()
    return {"message": "Shift updated."}


@app.post("/admin/add-barber/")
async def add_barber(name: str = Form(...), file: UploadFile = File(None), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if db.query(Barber).filter(Barber.name == name).first():
        raise HTTPException(status_code=400, detail="Barber already exists.")

    photo_bytes, content_type = None, None
    if file and file.filename:
        content_type = file.content_type or ""
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise HTTPException(status_code=400, detail="Photo must be a JPG, PNG, WEBP or GIF.")
        photo_bytes = await file.read(MAX_PHOTO_BYTES + 1)
        if len(photo_bytes) > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=400, detail="Photo must be under 3 MB.")

    barber = Barber(name=name, is_on_shift=True)
    db.add(barber)
    db.flush()  # assigns barber.id
    if photo_bytes:
        db.add(BarberPhoto(barber_id=barber.id, content_type=content_type, data=photo_bytes))
        barber.profile_picture = f"photos/{barber.id}?v={int(datetime.now().timestamp())}"
    db.commit()
    return {"message": f"Added {name}"}


@app.put("/admin/edit-barber/")
def edit_barber(barber_id: int, new_name: str, db: Session = Depends(get_db)):
    new_name = new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name is required.")
    barber = get_barber_or_404(db, barber_id)
    clash = db.query(Barber).filter(Barber.name == new_name, Barber.id != barber_id).first()
    if clash:
        raise HTTPException(status_code=400, detail="Another barber already has that name.")
    barber.name = new_name
    db.commit()
    return {"message": "Barber name updated."}


@app.delete("/admin/barbers/{barber_id}")
def delete_barber(barber_id: int, db: Session = Depends(get_db)):
    barber = get_barber_or_404(db, barber_id)

    # Cascade deletion for related rows
    db.query(Appointment).filter(Appointment.barber_id == barber_id).delete()
    db.query(BarberSchedule).filter(BarberSchedule.barber_id == barber_id).delete()
    db.query(BarberPhoto).filter(BarberPhoto.barber_id == barber_id).delete()

    db.delete(barber)
    db.commit()
    return {"message": "Barber deleted successfully."}


@app.post("/admin/assign-schedule/")
def assign_schedule(barber_id: int, date: str, time_slot: str, assign: bool, db: Session = Depends(get_db)):
    parse_date(date)
    if time_slot not in STANDARD_SLOTS:
        raise HTTPException(status_code=400, detail="Unknown time slot.")
    get_barber_or_404(db, barber_id)

    existing = db.query(BarberSchedule).filter(
        and_(BarberSchedule.barber_id == barber_id, BarberSchedule.date == date, BarberSchedule.time_slot == time_slot)
    ).first()

    if assign and not existing:
        db.add(BarberSchedule(barber_id=barber_id, date=date, time_slot=time_slot))
        db.commit()
    elif not assign and existing:
        db.delete(existing)
        db.commit()

    return {"message": "Schedule updated."}


@app.get("/admin/schedules/")
def get_schedules(date: str, db: Session = Depends(get_db)):
    parse_date(date)
    return db.query(BarberSchedule).filter(BarberSchedule.date == date).all()


@app.get("/appointments/")
def get_all_appointments(date: str = None, db: Session = Depends(get_db)):
    query = db.query(Appointment)

    if date:
        start_dt = parse_date(date)
        end_dt = start_dt + timedelta(days=1)
        query = query.filter(and_(Appointment.start_time >= start_dt, Appointment.start_time < end_dt))

    appointments = query.order_by(Appointment.start_time.asc()).all()
    barbers = {b.id: b.name for b in db.query(Barber).all()}

    return [
        {
            "id": appt.id,
            "barber_id": appt.barber_id,
            "customer_name": appt.customer_name,
            "phone_number": appt.phone_number,
            "barber_name": barbers.get(appt.barber_id, "Unknown"),
            "time": appt.start_time.strftime("%I:%M %p").lstrip("0"),
            "date": appt.start_time.strftime("%Y-%m-%d"),
        }
        for appt in appointments
    ]
