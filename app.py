"""Chef website with owner CMS — v2 (production-ready basics).

Environment variables (see .env.example):

  PRODUCTION=1            enables secure cookies, HTTPS-only headers, strict secrets

  SECRET_KEY              session secret (auto-generated and saved in DATA_DIR if absent in dev)

  DATA_DIR                folder for chef.db, uploads/ and backups/ (point at a persistent disk)

  ADMIN_USER/ADMIN_PASSWORD   first-run owner account (then change it inside /admin)

  SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/MAIL_FROM   quote email notifications

  FORCE_HTTPS=1, TRUST_PROXY=1, AUTO_BACKUP_HOURS=24, DEFAULT_COUNTRY_CODE=27

"""

import io, os, re, json, time, shutil, zipfile, sqlite3, secrets, smtplib, tempfile, threading, logging, hmac, hashlib, base64, struct

from pathlib import Path

from functools import wraps

from datetime import datetime

from html import escape

from collections import defaultdict

from contextlib import contextmanager

from email.message import EmailMessage

from urllib.parse import quote_plus

from flask import (Flask, render_template, request, redirect, url_for, session, g, abort,

                   jsonify, send_file, send_from_directory, Response)

from werkzeug.utils import secure_filename

from werkzeug.security import generate_password_hash, check_password_hash

from werkzeug.middleware.proxy_fix import ProxyFix

from PIL import Image, ImageOps, UnidentifiedImageError

import warnings

log = logging.getLogger("chef")

BASE = Path(__file__).resolve().parent

DATA = Path(os.environ.get("DATA_DIR") or BASE).resolve()

DB = DATA / "chef.db"

UPLOAD_DIR = DATA / "uploads"

BACKUP_DIR = DATA / "backups"

for d in (DATA, UPLOAD_DIR, BACKUP_DIR):

    d.mkdir(parents=True, exist_ok=True)

# one-time move of photos from the old static/uploads location

_legacy = BASE / "static" / "uploads"

if _legacy.is_dir() and _legacy.resolve() != UPLOAD_DIR:

    for p in _legacy.iterdir():

        if p.is_file() and p.name != ".gitkeep" and not (UPLOAD_DIR / p.name).exists():

            shutil.copy2(p, UPLOAD_DIR / p.name)

PROD = os.environ.get("PRODUCTION") == "1"

def _secret():

    if os.environ.get("SECRET_KEY"):

        return os.environ["SECRET_KEY"]

    if PROD:

        raise RuntimeError("Set SECRET_KEY when PRODUCTION=1")

    f = DATA / ".secret_key"

    if not f.exists():

        f.write_text(secrets.token_hex(32))

    return f.read_text().strip()

app = Flask(__name__)

app.secret_key = _secret()

app.config.update(

    MAX_CONTENT_LENGTH=12 * 1024 * 1024,

    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=PROD,

    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,

)

if os.environ.get("TRUST_PROXY") == "1":

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

ALLOWED = {"png", "jpg", "jpeg", "webp"}

ASSETS = {"hero": "hero_file", "og": "og_file", "favicon": "favicon_file"}

LOCKED_KEYS = set(ASSETS.values())

DEFAULT_SETTINGS = {

    "site_name": "CHEF Melusi", "chef_name": "Chef Melusi", "location": "Johannesburg, South Africa",

    "email": "hello@chefmelusi.example", "phone": "", "whatsapp": "", "instagram": "", "facebook": "",

    "hero_eyebrow": "", "hero_title": "Food made for your table.",

    "hero_text": "Thoughtful menus, beautiful plates and memorable meals for private dinners, celebrations and special events.",

    "statement_eyebrow": "", "statement_title": "Less ceremony. More memory.",

    "statement_text": "Every menu is shaped around the people, place and occasion — from intimate dinners at home to larger gatherings.",

    "about_title": "Cooking with curiosity, not a formula.",

    "about_text": "Chef Melusi creates food that feels considered without feeling complicated. Seasonal ingredients, confident technique and a strong sense of place sit at the centre of every menu.",

    "about_location": "Based in Johannesburg and available for private bookings and selected events.",

    "quote_title": "Tell me about your table.",

    "quote_text": "Share a few details about your event and the chef will get back to you with a tailored quote.",

    "seo_title": "Chef Melusi | Private Chef & Catering in Johannesburg",

    "seo_description": "Experience exceptional private dining and catering with Chef Melusi in Johannesburg. Discover personalised menus for celebrations, special occasions and events.",

    "hours": "", "availability": "", "notify_email": "", "show_map": "0",

    "hero_file": "", "og_file": "", "favicon_file": "",

}

# ---------- database ----------

@contextmanager

def conn():

    con = sqlite3.connect(DB, timeout=15)

    con.row_factory = sqlite3.Row

    try:

        yield con

        con.commit()

    finally:

        con.close()

def init_db():

    with conn() as c:

        c.executescript("""

        CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, caption TEXT, alt TEXT, layout TEXT DEFAULT 'normal', sort_order INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS services (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, title TEXT NOT NULL, description TEXT, sort_order INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS testimonials (id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT NOT NULL, quote TEXT NOT NULL, role TEXT, sort_order INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL, event_date TEXT, guests TEXT, occasion TEXT, message TEXT, status TEXT DEFAULT 'New', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS occasions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1);

        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

        CREATE TABLE IF NOT EXISTS admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, must_change INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS admin_login_attempts (

            attempt_key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,

            window_started REAL NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0

        );

        CREATE TABLE IF NOT EXISTS admin_audit_log (

            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, event TEXT NOT NULL,

            ip_address TEXT, details TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

        );

        CREATE TABLE IF NOT EXISTS admin_recovery_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            code_hash TEXT NOT NULL,
            used_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS quote_submission_limits (
            ip_hash TEXT PRIMARY KEY,
            window_started REAL NOT NULL,
            submissions INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS testimonial_submission_limits (
            ip_hash TEXT PRIMARY KEY,
            window_started REAL NOT NULL,
            submissions INTEGER NOT NULL DEFAULT 0
        );
        """)
        testimonial_cols = {r["name"] for r in c.execute("PRAGMA table_info(testimonials)")}
        if "status" not in testimonial_cols:
            c.execute("ALTER TABLE testimonials ADD COLUMN status TEXT NOT NULL DEFAULT 'Approved'")
        if "created_at" not in testimonial_cols:
            c.execute("ALTER TABLE testimonials ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        user_cols = {r["name"] for r in c.execute("PRAGMA table_info(admin_users)") }

        if "totp_secret" not in user_cols:

            c.execute("ALTER TABLE admin_users ADD COLUMN totp_secret TEXT")

        if "totp_enabled" not in user_cols:

            c.execute("ALTER TABLE admin_users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")

        if "last_login_at" not in user_cols:

            c.execute("ALTER TABLE admin_users ADD COLUMN last_login_at TEXT")

        if "last_login_ip" not in user_cols:

            c.execute("ALTER TABLE admin_users ADD COLUMN last_login_ip TEXT")

        if "sort_order" not in [r["name"] for r in c.execute("PRAGMA table_info(photos)")]:

            c.execute("ALTER TABLE photos ADD COLUMN sort_order INTEGER DEFAULT 0")

        for k, v in DEFAULT_SETTINGS.items():

            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))

        if c.execute("SELECT COUNT(*) FROM occasions").fetchone()[0] == 0:

            names = ["Private dinner", "Birthday", "Wedding", "Anniversary", "Baby shower", "Engagement",

                     "Corporate event", "Graduation", "Holiday / festive gathering", "Other"]

            c.executemany("INSERT INTO occasions(name,sort_order) VALUES(?,?)", [(n, i + 1) for i, n in enumerate(names)])

        if c.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:

            c.executemany("INSERT INTO services(number,title,description,sort_order) VALUES(?,?,?,?)", [

                ("", "Private dinners", "Restaurant-level cooking in the comfort of your home.", 1),

                ("", "Celebrations", "Menus designed around birthdays, milestones and meaningful gatherings.", 2),

                ("", "Events", "Polished food experiences for corporate and social occasions.", 3),

                ("", "Menu collaborations", "Creative culinary support for brands, launches and hospitality projects.", 4)])

        if c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0] == 0:

            user = os.environ.get("ADMIN_USER", "owner")

            pw = os.environ.get("ADMIN_PASSWORD")

            force = 0

            if not pw:

                pw, force = ("change-me", 1) if not PROD else (secrets.token_urlsafe(12), 1)

                if PROD:

                    print(f"\n*** First-run admin login -> username: {user}  password: {pw}  (you must change it) ***\n", flush=True)

            c.execute("INSERT INTO admin_users(username,password_hash,must_change) VALUES(?,?,?)",

                      (user, generate_password_hash(pw), force))

def get_settings():

    with conn() as c:

        rows = c.execute("SELECT key,value FROM settings").fetchall()

    d = DEFAULT_SETTINGS.copy()

    d.update({r["key"]: r["value"] for r in rows})

    return d

def set_setting(key, value):

    with conn() as c:

        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

# ---------- security ----------

def csrf_token():

    if "csrf" not in session:

        session["csrf"] = secrets.token_hex(16)

    return session["csrf"]

@app.before_request

def guard():

    if os.environ.get("FORCE_HTTPS") == "1" and not request.is_secure and request.host.split(":")[0] not in ("localhost", "127.0.0.1"):

        return redirect(request.url.replace("http://", "https://", 1), 301)

    if request.method == "POST":

        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")

        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):

            abort(400)

@app.after_request

def headers(r):

    r.headers.update({

        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",

        "Referrer-Policy": "strict-origin-when-cross-origin",

        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",

        "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https://images.unsplash.com; "

            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "

            "script-src 'self'; frame-src https://www.google.com; form-action 'self'; base-uri 'self'; frame-ancestors 'none'",

    })

    if PROD:

        r.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return r

@app.errorhandler(400)

def bad_request(_):

    return "Your session expired or the request was invalid. Go back, refresh the page and try again.", 400

@app.errorhandler(413)

def too_large(_):

    return "Image is too large. Maximum upload size is 12 MB.", 413

# ---------- security ----------

IDLE_TIMEOUT_SECONDS = 30 * 60

ABSOLUTE_SESSION_SECONDS = 8 * 60 * 60

LOGIN_WINDOW_SECONDS = 15 * 60

LOGIN_LOCK_SECONDS = 15 * 60

MAX_LOGIN_FAILURES = 8

TOTP_ISSUER = os.environ.get("TOTP_ISSUER", "Chef Portfolio")

def csrf_token():

    if "csrf" not in session:

        session["csrf"] = secrets.token_hex(32)

    return session["csrf"]

def audit_event(event, actor_id=None, details=""):

    """Append a security/admin event without storing secrets or form contents."""

    try:

        with conn() as c:

            c.execute(

                "INSERT INTO admin_audit_log(actor_id,event,ip_address,details) VALUES(?,?,?,?)",

                (actor_id, event[:120], (request.remote_addr or "")[:64], details[:500])

            )

            c.execute("DELETE FROM admin_audit_log WHERE id NOT IN (SELECT id FROM admin_audit_log ORDER BY id DESC LIMIT 5000)")

    except Exception:

        log.exception("Could not write admin audit event")

def _login_key(username):

    raw = f"{request.remote_addr or '?'}|{(username or '').strip().lower()[:120]}"

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def login_lock_remaining(username):

    key = _login_key(username)

    with conn() as c:

        row = c.execute("SELECT locked_until FROM admin_login_attempts WHERE attempt_key=?", (key,)).fetchone()

    if not row:

        return 0

    return max(0, int(float(row["locked_until"] or 0) - time.time()))

def record_login_failure(username):

    key, now = _login_key(username), time.time()

    with conn() as c:

        c.execute("DELETE FROM admin_login_attempts WHERE window_started < ?", (now - 30 * 24 * 60 * 60,))

        row = c.execute("SELECT failures,window_started,locked_until FROM admin_login_attempts WHERE attempt_key=?", (key,)).fetchone()

        if not row or now - float(row["window_started"]) > LOGIN_WINDOW_SECONDS:

            failures, started, locked = 1, now, 0

        else:

            failures, started, locked = int(row["failures"]) + 1, float(row["window_started"]), float(row["locked_until"] or 0)

        if failures >= MAX_LOGIN_FAILURES:

            locked = now + LOGIN_LOCK_SECONDS

        c.execute("""INSERT INTO admin_login_attempts(attempt_key,failures,window_started,locked_until)

                     VALUES(?,?,?,?) ON CONFLICT(attempt_key) DO UPDATE SET

                     failures=excluded.failures,window_started=excluded.window_started,locked_until=excluded.locked_until""",

                  (key, failures, started, locked))

def clear_login_failures(username):

    with conn() as c:

        c.execute("DELETE FROM admin_login_attempts WHERE attempt_key=?", (_login_key(username),))

def _totp_code(secret, for_time=None):

    counter = int((for_time if for_time is not None else time.time()) // 30)

    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))

    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()

    offset = digest[-1] & 0x0F

    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000

    return f"{number:06d}"

def verify_totp(secret, token):

    token = re.sub(r"\s+", "", token or "")

    if not re.fullmatch(r"\d{6}", token):

        return False

    now = time.time()

    return any(hmac.compare_digest(_totp_code(secret, now + drift * 30), token) for drift in (-1, 0, 1))

def new_totp_secret():

    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

def logged_in(f):

    @wraps(f)

    def w(*a, **k):

        uid = session.get("uid")

        if not uid:

            return redirect(url_for("login"))

        now = time.time()

        last_seen = float(session.get("last_seen", now))

        auth_started = float(session.get("auth_started", now))

        if now - last_seen > IDLE_TIMEOUT_SECONDS or now - auth_started > ABSOLUTE_SESSION_SECONDS:

            session.clear()

            return redirect(url_for("login", expired=1))

        with conn() as c:

            u = c.execute("SELECT * FROM admin_users WHERE id=?", (uid,)).fetchone()

        if not u:

            session.clear()

            return redirect(url_for("login"))

        g.user = u

        if u["must_change"] and request.endpoint not in ("password_page", "update_password", "logout"):

            return redirect(url_for("password_page"))

        if not u["totp_enabled"] and request.endpoint not in ("two_factor_setup", "two_factor_enable", "password_page", "update_password", "logout"):

            return redirect(url_for("two_factor_setup"))

        session["last_seen"] = now

        return f(*a, **k)

    return w

@app.before_request

def guard():

    if os.environ.get("FORCE_HTTPS") == "1" and not request.is_secure and request.host.split(":")[0] not in ("localhost", "127.0.0.1"):

        return redirect(request.url.replace("http://", "https://", 1), 301)

    if session.get("preauth_uid") and time.time() - float(session.get("preauth_at", 0)) > 300:

        session.clear()

    if request.method == "POST":

        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")

        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):

            abort(400)

    if session.get("uid"):

        now = time.time()

        last_seen = float(session.get("last_seen", now))

        auth_started = float(session.get("auth_started", now))

        if now - last_seen > IDLE_TIMEOUT_SECONDS or now - auth_started > ABSOLUTE_SESSION_SECONDS:

            session.clear()

            if request.path.startswith("/admin"):

                return redirect(url_for("login", expired=1))

@app.after_request

def headers(r):

    r.headers.update({

        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",

        "Referrer-Policy": "strict-origin-when-cross-origin",

        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",

        "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https://images.unsplash.com; "

            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "

            "script-src 'self'; frame-src https://www.google.com; form-action 'self'; base-uri 'self'; frame-ancestors 'none'",

    })

    if PROD:

        r.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    if request.path.startswith("/admin"):

        r.headers["Cache-Control"] = "no-store, max-age=0"

        r.headers["Pragma"] = "no-cache"

    # Record successful state-changing admin actions, but never record form values.

    if request.method == "POST" and request.path.startswith("/admin") and session.get("uid") and r.status_code < 400:

        if request.endpoint not in ("logout", "two_factor_enable"):

            audit_event("admin:" + str(request.endpoint or "unknown"), session.get("uid"), "Admin action completed")

    return r

@app.errorhandler(400)

def bad_request(_):

    return "Your session expired or the request was invalid. Go back, refresh the page and try again.", 400

@app.errorhandler(413)

def too_large(_):

    return "Image is too large. Maximum upload size is 12 MB.", 413

# ---------- helpers ----------

def quote_submission_limited(ip_address):

    """Allow at most 5 valid quote submissions per IP per 15 minutes."""

    now = time.time()

    window_seconds = 15 * 60

    maximum = 5

    ip_hash = hmac.new(

        app.secret_key.encode("utf-8"),

        (ip_address or "unknown").encode("utf-8"),

        hashlib.sha256

    ).hexdigest()

    with conn() as c:

        c.execute("""

            INSERT INTO quote_submission_limits

                (ip_hash, window_started, submissions)

            VALUES (?, ?, 1)

            ON CONFLICT(ip_hash) DO UPDATE SET

                window_started = CASE

                    WHEN quote_submission_limits.window_started <= ?

                    THEN excluded.window_started

                    ELSE quote_submission_limits.window_started

                END,

                submissions = CASE

                    WHEN quote_submission_limits.window_started <= ?

                    THEN 1

                    ELSE quote_submission_limits.submissions + 1

                END

        """, (ip_hash, now, now - window_seconds, now - window_seconds))

        row = c.execute(

            "SELECT submissions FROM quote_submission_limits WHERE ip_hash=?",

            (ip_hash,)

        ).fetchone()

        return row["submissions"] > maximum


def testimonial_submission_limited(ip_address):
    """Allow at most 5 testimonial submissions per IP per 15 minutes."""
    now = time.time()
    window_seconds = 15 * 60
    maximum = 5
    ip_hash = hmac.new(
        app.secret_key.encode("utf-8"),
        (ip_address or "unknown").encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    with conn() as c:
        c.execute("""
            INSERT INTO testimonial_submission_limits(ip_hash, window_started, submissions)
            VALUES (?, ?, 1)
            ON CONFLICT(ip_hash) DO UPDATE SET
                window_started = CASE
                    WHEN testimonial_submission_limits.window_started <= ?
                    THEN excluded.window_started
                    ELSE testimonial_submission_limits.window_started
                END,
                submissions = CASE
                    WHEN testimonial_submission_limits.window_started <= ?
                    THEN 1
                    ELSE testimonial_submission_limits.submissions + 1
                END
        """, (ip_hash, now, now - window_seconds, now - window_seconds))
        row = c.execute(
            "SELECT submissions FROM testimonial_submission_limits WHERE ip_hash=?",
            (ip_hash,)
        ).fetchone()
        return row["submissions"] > maximum

def to_int(v):

    try:

        return int(v or 0)

    except ValueError:

        return 0

def save_image(file):

    """Validate, decode and re-encode an uploaded image."""

    if not file or not file.filename:

        return None, "Please choose an image."

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""

    if ext not in ALLOWED:

        return None, "Only JPG, PNG and WEBP images are allowed."

    formats = {

        "jpg": "JPEG",

        "jpeg": "JPEG",

        "png": "PNG",

        "webp": "WEBP",

    }

    expected_format = formats[ext]

    try:

        Image.MAX_IMAGE_PIXELS = 40_000_000

        with warnings.catch_warnings():

            warnings.simplefilter("error", Image.DecompressionBombWarning)

            file.stream.seek(0)

            with Image.open(file.stream) as original:

                if original.format != expected_format:

                    return None, "The image format does not match its file extension."

                if getattr(original, "n_frames", 1) != 1:

                    return None, "Animated images are not supported."

                original.verify()

            file.stream.seek(0)

            with Image.open(file.stream) as original:

                if original.format != expected_format:

                    return None, "Invalid image format."

                if original.width * original.height > 40_000_000:

                    return None, "Image dimensions are too large."

                image = ImageOps.exif_transpose(original)

                image.load()

                if expected_format == "JPEG":

                    image = image.convert("RGB")

                elif expected_format == "PNG":

                    if image.mode not in ("RGB", "RGBA", "L", "LA"):

                        image = image.convert("RGBA")

                elif expected_format == "WEBP":

                    if image.mode not in ("RGB", "RGBA"):

                        image = image.convert("RGBA")

                output = io.BytesIO()

                save_options = {"format": expected_format}

                if expected_format == "JPEG":

                    save_options.update(quality=88, optimize=True)

                elif expected_format == "WEBP":

                    save_options.update(quality=88, method=6)

                elif expected_format == "PNG":

                    save_options.update(optimize=True)

                image.save(output, **save_options)

                output.seek(0)

                name = secure_filename(secrets.token_hex(12) + "." + ext)

                with open(UPLOAD_DIR / name, "wb") as destination:

                    destination.write(output.read())

                return name, None

    except (

        UnidentifiedImageError,

        Image.DecompressionBombError,

        Image.DecompressionBombWarning,

        OSError,

        ValueError,

    ):

        return None, "That file is not a valid or supported image."

def remove_upload(name):

    if name:

        p = UPLOAD_DIR / Path(name).name

        if p.is_file():

            p.unlink()

def social(v, base):

    v = (v or "").strip()

    if not v:

        return ""

    return v if v.startswith("http") else base + v.lstrip("@/")

def wa_number(v):

    d = re.sub(r"\D", "", v or "")

    cc = os.environ.get("DEFAULT_COUNTRY_CODE", "27")

    return cc + d[1:] if d.startswith("0") else d

def _send_mail_now(to, subject, body, reply_to=None):

    """Send one message and report the SMTP server's actual result."""

    host = os.environ.get("SMTP_HOST")

    if not host or not to:

        log.warning("Email not sent (SMTP_HOST or recipient missing): %s", subject)

        return False

    try:

        port = int(os.environ.get("SMTP_PORT", "587"))

        user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")

        message = EmailMessage()

        message["Subject"] = re.sub(r"[**\**&#x72;**\**&#x6E;]+", " ", subject)

        message["From"] = os.environ.get("MAIL_FROM") or user or "no-reply@localhost"

        message["To"] = to

        if reply_to:

            message["Reply-To"] = reply_to

        message.set_content(body)

        smtp = smtplib.SMTP_SSL(host, port, timeout=20) if port == 465 else smtplib.SMTP(host, port, timeout=20)

        with smtp:

            if port != 465:

                smtp.starttls()

            if user:

                smtp.login(user, pw or "")

            smtp.send_message(message)

        return True

    except Exception:

        log.exception("Email failed: %s", subject)

        return False

def send_mail(to, subject, body, reply_to=None):

    """Queue a notification email without blocking the public quote form."""

    if not os.environ.get("SMTP_HOST") or not to:

        log.warning("Email not queued (SMTP_HOST or recipient missing): %s", subject)

        return False

    threading.Thread(

        target=_send_mail_now,

        args=(to, subject, body),

        kwargs={"reply_to": reply_to},

        daemon=True,

    ).start()

    return True

def build_backup(fileobj):

    with tempfile.TemporaryDirectory() as td:

        snap = Path(td) / "chef.db"

        src, dst = sqlite3.connect(DB), sqlite3.connect(snap)

        src.backup(dst); dst.close(); src.close()

        with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as z:

            z.write(snap, "chef.db")

            for p in UPLOAD_DIR.iterdir():

                if p.is_file():

                    z.write(p, "uploads/" + p.name)

def _backup_loop(hours):

    while True:

        try:

            with open(BACKUP_DIR / f"auto-{datetime.utcnow():%Y%m%d-%H%M}.zip", "wb") as f:

                build_backup(f)

            for old in sorted(BACKUP_DIR.glob("auto-*.zip"))[:-14]:

                old.unlink()

        except Exception:

            log.exception("Automatic backup failed")

        time.sleep(hours * 3600)

@app.context_processor

def inject():

    s = get_settings()

    root = request.url_root.rstrip("/") if request else ""

    def up(n): return url_for("uploaded", filename=n)

    s["instagram_url"] = social(s["instagram"], "https://instagram.com/")

    s["facebook_url"] = social(s["facebook"], "https://facebook.com/")

    wa = wa_number(s["whatsapp"])

    s["wa_url"] = f"https://wa.me/{wa}" if wa else ""

    q = quote_plus(s["location"])

    s["map_url"] = f"https://www.google.com/maps/search/?api=1&query={q}"

    s["map_embed"] = f"https://www.google.com/maps?q={q}&output=embed"

    s["favicon_url"] = up(s["favicon_file"]) if s["favicon_file"] else url_for("favicon_svg")

    og = s["og_file"] or s["hero_file"]

    if not og:

        with conn() as c:

            row = c.execute("SELECT filename FROM photos ORDER BY sort_order,id DESC LIMIT 1").fetchone()

        og = row["filename"] if row else ""

    s["og_url"] = root + up(og) if og else ""

    ld = {"@context": "https://schema.org", "@type": "ProfessionalService", "name": s["site_name"],

          "description": s["seo_description"], "url": root + "/", "areaServed": s["location"],

          "email": s["email"], "telephone": s["phone"],

          "sameAs": [u for u in (s["instagram_url"], s["facebook_url"]) if u]}

    if s["og_url"]:

        ld["image"] = s["og_url"]

    return {"site": s, "csrf_token": csrf_token, "ld": {k: v for k, v in ld.items() if v}, "canonical": root + "/"}

# ---------- public ----------

@app.route("/")

def index():

    s = get_settings()

    with conn() as c:

        photos = c.execute("SELECT * FROM photos ORDER BY sort_order,id DESC").fetchall()

        services = c.execute("SELECT * FROM services ORDER BY sort_order,id").fetchall()

        testimonials = c.execute("SELECT * FROM testimonials WHERE status = 'Approved' ORDER BY sort_order,id").fetchall()

        occasions = c.execute("SELECT * FROM occasions WHERE active=1 ORDER BY sort_order,id").fetchall()

    hero_alt = s["chef_name"] + " at work"

    if s["hero_file"] and (UPLOAD_DIR / s["hero_file"]).is_file():

        hero_image, gallery = url_for("uploaded", filename=s["hero_file"]), list(photos)

    elif photos:

        hero_image, gallery = url_for("uploaded", filename=photos[0]["filename"]), list(photos[1:])

        hero_alt = photos[0]["alt"] or hero_alt

    else:

        hero_image = "https://images.unsplash.com/photo-1515003197210-e0cd71810b5f?auto=format&fit=crop&w=1400&q=85"

        gallery = []

    return render_template("index.html", gallery=gallery, services=services, testimonials=testimonials,

                           occasions=occasions, hero_image=hero_image, hero_alt=hero_alt)

@app.route("/uploads/<path:filename>")

def uploaded(filename):

    return send_from_directory(UPLOAD_DIR, filename, max_age=60 * 60 * 24 * 30)

@app.route("/favicon.svg")

def favicon_svg():

    ch = escape((get_settings()["site_name"].strip() or "C")[0].upper())

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#5A1D2C"/>'

           f'<text x="32" y="46" text-anchor="middle" font-family="Georgia,serif" font-weight="700" font-size="40" fill="#E8E7E1">{ch}</text></svg>')

    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})

@app.route("/google0f9a3b5ae4cf7087.html")

def google_site_verification():
    # Google Search Console ownership verification file.
    return Response(
        "google-site-verification: google0f9a3b5ae4cf7087.html",
        mimetype="text/html",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.route("/robots.txt")

def robots():

    return Response(f"User-agent: *\nDisallow: /admin\nSitemap: {request.url_root}sitemap.xml\n", mimetype="text/plain")

@app.route("/sitemap.xml")

def sitemap():

    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{request.url_root}</loc></url></urlset>', mimetype="application/xml")

@app.route("/quote", methods=["POST"])
def submit_quote():
    # Honeypot protection against basic bots.
    if request.form.get("company_website"):
        return render_template("quote_received.html")

    f = {
        key: request.form.get(key, "").strip()[:2000]
        for key in ("name", "email", "date", "guests", "occasion", "message")
    }

    if not f["name"] or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", f["email"]):
        return render_template(
            "quote_received.html",
            error="Please enter your name and a valid email address."
        ), 400

    if quote_submission_limited(request.remote_addr):
        return render_template(
            "quote_received.html",
            error="Too many quote requests. Please try again in 15 minutes."
        ), 429

    with conn() as c:
        c.execute(
            """INSERT INTO quotes(name,email,event_date,guests,occasion,message)
               VALUES(?,?,?,?,?,?)""",
            (f["name"], f["email"], f["date"], f["guests"], f["occasion"], f["message"])
        )

    s = get_settings()
    send_mail(
        s["notify_email"] or s["email"],
        f"New quote request from {f['name']}",
        (
            f"Name: {f['name']}\nEmail: {f['email']}\n"
            f"Occasion: {f['occasion']}\nDate: {f['date']}\n"
            f"Guests: {f['guests']}\n\n{f['message']}\n\n"
            f"Manage it in your dashboard: {request.url_root}admin"
        ),
        reply_to=f["email"]
    )

    if os.environ.get("AUTO_REPLY", "1") == "1":
        send_mail(
            f["email"],
            f"We received your request — {s['chef_name']}",
            (
                f"Hi {f['name']},\n\nThank you for your enquiry. "
                f"{s['chef_name']} has received your details and will reply "
                f"with a tailored quote soon.\n\n"
                f"{s['chef_name']}\n{s['location']}"
            )
        )

    return render_template("quote_received.html")


@app.route("/testimonials/submit", methods=["POST"])
def submit_testimonial():
    if request.form.get("website"):
        return redirect(url_for("index") + "#testimonial-form")

    client = request.form.get("client", "").strip()[:100]
    occasion = request.form.get("occasion", "").strip()[:100]
    testimonial_text = request.form.get("testimonial", "").strip()[:1500]

    if len(client) < 2 or len(testimonial_text) < 10:
        return redirect(url_for(
            "index",
            testimonial_message="Please enter your name and a testimonial of at least 10 characters."
        ) + "#testimonial-form")

    if testimonial_submission_limited(request.remote_addr):
        return redirect(url_for(
            "index",
            testimonial_message="Too many submissions. Please try again in 15 minutes."
        ) + "#testimonial-form")

    with conn() as c:
        c.execute(
            """INSERT INTO testimonials(client, quote, role, sort_order, status)
               VALUES(?, ?, ?, 0, 'Pending')""",
            (client, testimonial_text, occasion)
        )

    settings = get_settings()
    recipient = settings["notify_email"] or settings["email"]
    send_mail(
        recipient,
        f"New client testimonial awaiting approval — {client}",
        (
            f"A new testimonial has been submitted for {settings['chef_name']}.\n\n"
            f"Client: {client}\nOccasion: {occasion or 'Not provided'}\n\n"
            f"Testimonial:\n{testimonial_text}\n\n"
            f"Review it in the admin dashboard: {request.url_root}admin#testimonial-reviews"
        )
    )
    return redirect(url_for(
        "index",
        testimonial_message="Thank you. Your testimonial has been sent to Chef Melusi for review."
    ) + "#testimonial-form")

# ---------- owner ----------

@app.route("/admin/login", methods=["GET", "POST"])

def login():

    error = "Your session expired. Please log in again." if request.args.get("expired") else None

    if request.method == "POST":

        username = request.form.get("username", "").strip()

        remaining = login_lock_remaining(username)

        if remaining:

            return render_template("login.html", error=f"Too many attempts. Try again in {max(1, (remaining + 59)//60)} minute(s)."), 429

        with conn() as c:

            u = c.execute("SELECT * FROM admin_users WHERE username=?", (username,)).fetchone()

        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):

            clear_login_failures(username)

            audit_event("login_password_accepted", u["id"], "Password factor accepted; second factor pending")

            session.clear()

            session.permanent = True

            if u["totp_enabled"] and u["totp_secret"]:

                session["preauth_uid"] = u["id"]

                session["preauth_at"] = time.time()

                return redirect(url_for("two_factor_verify"))

            session["uid"] = u["id"]

            session["last_seen"] = time.time()

            session["auth_started"] = time.time()

            return redirect(url_for("password_page") if u["must_change"] else url_for("two_factor_setup"))

        record_login_failure(username)

        audit_event("login_failed", None, "Invalid credentials")

        error = "Incorrect username or password."

    return render_template("login.html", error=error)

@app.route("/admin/2fa/verify", methods=["GET", "POST"])

def two_factor_verify():

    uid = session.get("preauth_uid")

    if not uid or time.time() - float(session.get("preauth_at", 0)) > 300:

        session.clear()

        return redirect(url_for("login", expired=1))

    with conn() as c:

        u = c.execute("SELECT * FROM admin_users WHERE id=?", (uid,)).fetchone()

    if not u or not u["totp_enabled"] or not u["totp_secret"]:

        session.clear()

        return redirect(url_for("login"))

    error = None

    if request.method == "POST":

        throttle_key = f"2fa:{uid}"

        remaining = login_lock_remaining(throttle_key)

        if remaining:

            return render_template("two_factor_verify.html",

                                   error=f"Too many code attempts. Try again in {max(1, (remaining + 59)//60)} minute(s)."), 429

        token = request.form.get("code", "")

        valid = verify_totp(u["totp_secret"], token)

        if not valid:

            # A one-time recovery code can be used instead of the authenticator code.

            candidate = re.sub(r"[^A-Za-z0-9]", "", token).upper()

            if candidate:

                digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()

                with conn() as c:

                    recovery = c.execute("SELECT id FROM admin_recovery_codes WHERE admin_id=? AND code_hash=? AND used_at IS NULL",

                                          (uid, digest)).fetchone()

                    if recovery:

                        c.execute("UPDATE admin_recovery_codes SET used_at=CURRENT_TIMESTAMP WHERE id=?", (recovery["id"],))

                        valid = True

        if valid:

            clear_login_failures(throttle_key)

            session.clear()

            session.permanent = True

            session["uid"] = uid

            session["last_seen"] = time.time()

            session["auth_started"] = time.time()

            with conn() as c:

                c.execute("UPDATE admin_users SET last_login_at=CURRENT_TIMESTAMP,last_login_ip=? WHERE id=?",

                          ((request.remote_addr or "")[:64], uid))

            audit_event("login_success", uid, "Two-factor authentication completed")

            return redirect(url_for("password_page") if u["must_change"] else url_for("admin"))

        record_login_failure(throttle_key)

        audit_event("two_factor_failed", uid, "Invalid authenticator or recovery code")

        error = "That code was not accepted. Try again."

    return render_template("two_factor_verify.html", error=error)

@app.route("/admin/2fa/setup", methods=["GET"])

@logged_in

def two_factor_setup():

    if g.user["totp_enabled"]:

        return redirect(url_for("admin"))

    secret = session.get("pending_totp_secret")

    if not secret:

        secret = new_totp_secret()

        session["pending_totp_secret"] = secret

    label = f"{TOTP_ISSUER}:{g.user['username']}"

    uri = "otpauth://totp/" + quote_plus(label) + "?secret=" + secret + "&issuer=" + quote_plus(TOTP_ISSUER) + "&algorithm=SHA1&digits=6&period=30"

    return render_template("two_factor_setup.html", secret=secret, provisioning_uri=uri, error=request.args.get("error"))

@app.route("/admin/2fa/enable", methods=["POST"])

@logged_in

def two_factor_enable():

    if g.user["totp_enabled"]:

        return redirect(url_for("admin"))

    secret = session.get("pending_totp_secret")

    if not secret or not verify_totp(secret, request.form.get("code", "")):

        return redirect(url_for("two_factor_setup", error="The code did not match. Check your authenticator app and try again."))

    recovery_codes = [secrets.token_hex(12).upper() for _ in range(8)]

    with conn() as c:

        c.execute("UPDATE admin_users SET totp_secret=?,totp_enabled=1 WHERE id=?", (secret, g.user["id"]))

        c.execute("DELETE FROM admin_recovery_codes WHERE admin_id=?", (g.user["id"],))

        c.executemany("INSERT INTO admin_recovery_codes(admin_id,code_hash) VALUES(?,?)",

                      [(g.user["id"], hashlib.sha256(v.encode("utf-8")).hexdigest()) for v in recovery_codes])

    session.pop("pending_totp_secret", None)

    audit_event("two_factor_enabled", g.user["id"], "Authenticator-based 2FA enabled")

    return render_template("two_factor_recovery.html", recovery_codes=recovery_codes)

@app.route("/admin/logout", methods=["POST"])

def logout():

    audit_event("logout", session.get("uid"), "Owner logged out")

    session.clear()

    return redirect(url_for("index"))

@app.route("/admin/password", methods=["GET"])

@logged_in

def password_page():

    return render_template("password.html", user=g.user, error=request.args.get("error"))

@app.route("/admin/password", methods=["POST"])

@logged_in

def update_password():

    cur, new, again = (request.form.get(k, "") for k in ("current", "new", "confirm"))

    username = request.form.get("username", "").strip() or g.user["username"]

    err = None

    if not check_password_hash(g.user["password_hash"], cur):

        err = "Your current password is not correct."

    elif len(new) < 10:

        err = "Use at least 10 characters for the new password."

    elif new != again:

        err = "The two new passwords do not match."

    if err:

        return redirect(url_for("password_page", error=err))

    try:

        with conn() as c:

            c.execute("UPDATE admin_users SET username=?,password_hash=?,must_change=0 WHERE id=?",

                      (username, generate_password_hash(new), g.user["id"]))

    except sqlite3.IntegrityError:

        return redirect(url_for("password_page", error="That username is taken."))

    audit_event("credentials_changed", g.user["id"], "Owner username/password changed")

    if not g.user["totp_enabled"]:

        return redirect(url_for("two_factor_setup"))

    return redirect(url_for("admin", message="Login details updated."))

@app.route("/admin/security")

@logged_in

def security_audit():

    with conn() as c:

        rows = c.execute("""SELECT id,actor_id,event,ip_address,details,created_at

                            FROM admin_audit_log ORDER BY id DESC LIMIT 200""").fetchall()

        recovery_left = c.execute("SELECT COUNT(*) FROM admin_recovery_codes WHERE admin_id=? AND used_at IS NULL",

                                  (g.user["id"],)).fetchone()[0]

    return render_template("security.html", logs=rows, recovery_left=recovery_left, totp_enabled=bool(g.user["totp_enabled"]))

@app.route("/admin/security/recovery-codes", methods=["POST"])

@logged_in

def regenerate_recovery_codes():

    if not g.user["totp_enabled"]:

        abort(403)

    current = request.form.get("current_password", "")

    if not check_password_hash(g.user["password_hash"], current):

        return redirect(url_for("security_audit", error="Current password was incorrect."))

    recovery_codes = [secrets.token_hex(12).upper() for _ in range(8)]

    with conn() as c:

        c.execute("DELETE FROM admin_recovery_codes WHERE admin_id=?", (g.user["id"],))

        c.executemany("INSERT INTO admin_recovery_codes(admin_id,code_hash) VALUES(?,?)",

                      [(g.user["id"], hashlib.sha256(v.encode("utf-8")).hexdigest()) for v in recovery_codes])

    audit_event("recovery_codes_regenerated", g.user["id"], "One-time recovery codes regenerated")

    return render_template("two_factor_recovery.html", recovery_codes=recovery_codes)

@app.route("/admin")

@logged_in

def admin():

    with conn() as c:

        photos = c.execute("SELECT * FROM photos ORDER BY sort_order,id DESC").fetchall()

        services = c.execute("SELECT * FROM services ORDER BY sort_order,id").fetchall()

        testimonials = c.execute("SELECT * FROM testimonials ORDER BY sort_order,id").fetchall()
        pending_testimonials = c.execute(
            "SELECT * FROM testimonials WHERE status = 'Pending' ORDER BY created_at DESC,id DESC"
        ).fetchall()
        approved_testimonial_count = c.execute(
            "SELECT COUNT(*) FROM testimonials WHERE status = 'Approved'"
        ).fetchone()[0]
        rejected_testimonial_count = c.execute(
            "SELECT COUNT(*) FROM testimonials WHERE status = 'Rejected'"
        ).fetchone()[0]

        quotes = c.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()

        occasions = c.execute("SELECT * FROM occasions ORDER BY sort_order,id").fetchall()

    counts = {"photos": len(photos), "services": len(services), "testimonials": len(testimonials),

              "quotes": len(quotes), "new_quotes": sum(1 for q in quotes if q["status"] == "New"), "occasions": len(occasions), "pending_testimonials": len(pending_testimonials), "approved_testimonials": approved_testimonial_count, "rejected_testimonials": rejected_testimonial_count}

    return render_template("admin.html", photos=photos, services=services, testimonials=testimonials,

                           occasions=occasions, quotes=quotes, counts=counts, pending_testimonials=pending_testimonials, message=request.args.get("message"),

                           mail_ready=bool(os.environ.get("SMTP_HOST")))

@app.route("/admin/settings", methods=["POST"])

@logged_in

def update_settings():

    form = request.form.to_dict()

    form["show_map"] = "1" if form.get("show_map") else "0"

    for k in DEFAULT_SETTINGS:

        if k in form and k not in LOCKED_KEYS:

            set_setting(k, form[k].strip()[:5000])

    return redirect(url_for("admin", message="Website settings saved."))

@app.route("/admin/asset/<kind>", methods=["POST"])

@logged_in

def set_asset(kind):

    key = ASSETS.get(kind) or abort(404)

    name, err = save_image(request.files.get("image"))

    if err:

        return redirect(url_for("admin", message=err))

    remove_upload(get_settings()[key])

    set_setting(key, name)

    return redirect(url_for("admin", message="Image saved."))

@app.route("/admin/asset/<kind>/delete", methods=["POST"])

@logged_in

def delete_asset(kind):

    key = ASSETS.get(kind) or abort(404)

    remove_upload(get_settings()[key])

    set_setting(key, "")

    return redirect(url_for("admin", message="Image removed."))

@app.route("/admin/upload", methods=["POST"])

@logged_in

def upload():

    name, err = save_image(request.files.get("photo"))

    if err:

        return redirect(url_for("admin", message=err))

    with conn() as c:

        first = c.execute("SELECT COALESCE(MIN(sort_order),0)-1 FROM photos").fetchone()[0]

        c.execute("INSERT INTO photos(filename,caption,alt,layout,sort_order) VALUES(?,?,?,?,?)",

                  (name, request.form.get("caption", "").strip(), request.form.get("alt", "").strip(),

                   request.form.get("layout", "normal"), first))

    return redirect(url_for("admin", message="Photo uploaded."))

@app.route("/admin/photo/<int:item_id>/edit", methods=["POST"])

@logged_in

def edit_photo(item_id):

    with conn() as c:

        c.execute("UPDATE photos SET caption=?,alt=?,layout=? WHERE id=?",

                  (request.form.get("caption", "").strip(), request.form.get("alt", "").strip(),

                   request.form.get("layout", "normal"), item_id))

    return redirect(url_for("admin", message="Photo details updated."))

@app.route("/admin/delete/photo/<int:item_id>", methods=["POST"])

@logged_in

def delete_photo(item_id):

    with conn() as c:

        row = c.execute("SELECT filename FROM photos WHERE id=?", (item_id,)).fetchone()

        if row:

            remove_upload(row["filename"])

            c.execute("DELETE FROM photos WHERE id=?", (item_id,))

    return redirect(url_for("admin", message="Photo deleted."))

@app.route("/admin/photos/reorder", methods=["POST"])

@logged_in

def reorder_photos():

    ids = (request.get_json(silent=True) or {}).get("order", [])

    with conn() as c:

        for pos, pid in enumerate(ids):

            c.execute("UPDATE photos SET sort_order=? WHERE id=?", (pos, to_int(pid)))

    return jsonify(ok=True)

def crud(name, table, fields, required, label, bools=()):

    """Create add/edit/delete routes for a simple table."""

    cols = list(fields)

    def vals():

        return [to_int(request.form.get(c)) if c == "sort_order" else request.form.get(c, "").strip() for c in cols]

    def add():

        v = vals()

        if any(not v[cols.index(r)] for r in required):

            return redirect(url_for("admin", message="Please fill in all required fields."))

        extra = [1 for _ in bools]

        with conn() as c:

            c.execute(f"INSERT INTO {table}({','.join(cols + list(bools))}) VALUES({','.join('?' * (len(cols) + len(bools)))})", v + extra)

        return redirect(url_for("admin", message=f"{label} added."))

    def edit(item_id):

        v = vals()

        if any(not v[cols.index(r)] for r in required):

            return redirect(url_for("admin", message="Please fill in all required fields."))

        extra = [1 if request.form.get(b) == "on" else 0 for b in bools]

        with conn() as c:

            c.execute(f"UPDATE {table} SET {','.join(x + '=?' for x in cols + list(bools))} WHERE id=?", v + extra + [item_id])

        return redirect(url_for("admin", message=f"{label} updated."))

    def delete(item_id):

        with conn() as c:

            c.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))

        return redirect(url_for("admin", message=f"{label} deleted."))

    for fn, ep, rule in ((add, f"add_{name}", f"/admin/{name}/add"), (edit, f"edit_{name}", f"/admin/{name}/<int:item_id>/edit"),

                         (delete, f"delete_{name}", f"/admin/delete/{name}/<int:item_id>")):

        app.add_url_rule(rule, ep, logged_in(fn), methods=["POST"])

crud("service", "services", ["number", "title", "description", "sort_order"], ["title"], "Service")

crud("testimonial", "testimonials", ["client", "quote", "role", "sort_order"], ["client", "quote"], "Testimonial")

crud("occasion", "occasions", ["name", "sort_order"], ["name"], "Occasion", bools=("active",))


@app.route("/admin/testimonial/<int:item_id>/moderate", methods=["POST"])
@logged_in
def moderate_testimonial(item_id):
    status = request.form.get("status", "")
    if status not in {"Approved", "Rejected", "Pending"}:
        abort(400)
    with conn() as c:
        cur = c.execute(
            "UPDATE testimonials SET status=? WHERE id=?",
            (status, item_id)
        )
        if cur.rowcount == 0:
            return redirect(url_for("admin", message="Testimonial not found."))
    return redirect(url_for("admin", message=f"Testimonial marked {status.lower()}."))

@app.route("/admin/quote/<int:item_id>/status", methods=["POST"])

@logged_in

def update_quote_status(item_id):

    status = request.form.get("status", "New")

    with conn() as c:

        c.execute("UPDATE quotes SET status=? WHERE id=?", (status if status in {"New", "Contacted", "Closed"} else "New", item_id))

    return redirect(url_for("admin", message="Quote status updated."))

@app.route("/admin/delete/quote/<int:item_id>", methods=["POST"])

@logged_in

def delete_quote(item_id):

    with conn() as c:

        c.execute("DELETE FROM quotes WHERE id=?", (item_id,))

    return redirect(url_for("admin", message="Quote request deleted."))

@app.route("/admin/backup")

@logged_in

def backup():

    buf = io.BytesIO()

    build_backup(buf)

    buf.seek(0)

    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"chef-backup-{datetime.utcnow():%Y-%m-%d}.zip")

@app.route("/admin/test-email", methods=["POST"])

@logged_in

def test_email():

    s = get_settings()

    to = s["notify_email"] or s["email"]

    ok = _send_mail_now(to, "Test email from your website", "Email notifications are working.")

    message = (

        f"Your SMTP server accepted the test email for {to}. Check your inbox (and spam)."

        if ok else

        "The test email could not be sent. Check the SMTP host, port, username, password and recipient in your hosting settings."

    )

    return redirect(url_for("admin", message=message))

init_db()

# One-time, safe branding/SEO migration:
# update only values that still match the old Chef Amara defaults.
# Custom owner-written content is left untouched.
_LEGACY_BRAND_VALUES = {
    "site_name": ("CHEF AMARA", "CHEF MELUSI"),
    "chef_name": ("Chef Amara", "Chef Melusi"),
    "seo_title": ("Chef Amara — Private Chef", "Chef Melusi | Private Chef & Catering in Johannesburg"),
    "seo_description": (
        "Private chef experiences, celebrations and events in Johannesburg.",
        "Experience exceptional private dining and catering with Chef Melusi in Johannesburg. Discover personalised menus for celebrations, special occasions and events."
    ),
    "about_text": (
        "Chef Amara creates food that feels considered without feeling complicated. Seasonal ingredients, confident technique and a strong sense of place sit at the centre of every menu.",
        "Chef Melusi creates food that feels considered without feeling complicated. Seasonal ingredients, confident technique and a strong sense of place sit at the centre of every menu."
    ),
}
with conn() as _c:
    for _key, (_old, _new) in _LEGACY_BRAND_VALUES.items():
        _row = _c.execute("SELECT value FROM settings WHERE key=?", (_key,)).fetchone()
        if _row and _row["value"] == _old:
            _c.execute("UPDATE settings SET value=? WHERE key=?", (_new, _key))

_hours = to_int(os.environ.get("AUTO_BACKUP_HOURS", "24" if PROD else "0"))

if _hours > 0:

    threading.Thread(target=_backup_loop, args=(_hours,), daemon=True).start()

if __name__ == "__main__":

    app.run(debug=not PROD)
