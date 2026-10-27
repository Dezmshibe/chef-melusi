"""Chef website with owner CMS — v2 (production-ready basics).

Environment variables (see .env.example):
  PRODUCTION=1            enables secure cookies, HTTPS-only headers, strict secrets
  SECRET_KEY              session secret (auto-generated and saved in DATA_DIR if absent in dev)
  DATA_DIR                folder for chef.db, uploads/ and backups/ (point at a persistent disk)
  ADMIN_USER/ADMIN_PASSWORD   first-run owner account (then change it inside /admin)
  SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/MAIL_FROM   quote email notifications
  FORCE_HTTPS=1, TRUST_PROXY=1, AUTO_BACKUP_HOURS=24, DEFAULT_COUNTRY_CODE=27
"""
import io, os, re, json, time, shutil, zipfile, sqlite3, secrets, smtplib, tempfile, threading, logging
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
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
)
if os.environ.get("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

ALLOWED = {"png", "jpg", "jpeg", "webp"}
ASSETS = {"hero": "hero_file", "og": "og_file", "favicon": "favicon_file"}
LOCKED_KEYS = set(ASSETS.values())

DEFAULT_SETTINGS = {
    "site_name": "CHEF AMARA", "chef_name": "Chef Amara", "location": "Johannesburg, South Africa",
    "email": "hello@chefamara.example", "phone": "", "whatsapp": "", "instagram": "", "facebook": "",
    "hero_eyebrow": "", "hero_title": "Food made for your table.",
    "hero_text": "Thoughtful menus, beautiful plates and memorable meals for private dinners, celebrations and special events.",
    "statement_eyebrow": "", "statement_title": "Less ceremony. More memory.",
    "statement_text": "Every menu is shaped around the people, place and occasion — from intimate dinners at home to larger gatherings.",
    "about_title": "Cooking with curiosity, not a formula.",
    "about_text": "Chef Amara creates food that feels considered without feeling complicated. Seasonal ingredients, confident technique and a strong sense of place sit at the centre of every menu.",
    "about_location": "Based in Johannesburg and available for private bookings and selected events.",
    "quote_title": "Tell me about your table.",
    "quote_text": "Share a few details about your event and the chef will get back to you with a tailored quote.",
    "seo_title": "Chef Amara — Private Chef",
    "seo_description": "Private chef experiences, celebrations and events in Johannesburg.",
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
        """)
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

_attempts = defaultdict(list)
def too_many_logins():
    now, ip = time.time(), request.remote_addr or "?"
    _attempts[ip] = [t for t in _attempts[ip] if now - t < 600]
    return len(_attempts[ip]) >= 8

def logged_in(f):
    @wraps(f)
    def w(*a, **k):
        uid = session.get("uid")
        if not uid:
            return redirect(url_for("login"))
        with conn() as c:
            u = c.execute("SELECT * FROM admin_users WHERE id=?", (uid,)).fetchone()
        if not u:
            session.clear()
            return redirect(url_for("login"))
        g.user = u
        if u["must_change"] and request.endpoint not in ("password_page", "update_password", "logout"):
            return redirect(url_for("password_page"))
        return f(*a, **k)
    return w

# ---------- helpers ----------
def to_int(v):
    try:
        return int(v or 0)
    except ValueError:
        return 0

def save_image(file):
    """Save an uploaded image. Returns (filename, error)."""
    if not file or not file.filename:
        return None, "Please choose an image."
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED:
        return None, "Only JPG, PNG and WEBP images are allowed."
    head = file.stream.read(12)
    file.stream.seek(0)
    ok = head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    if not ok:
        return None, "That file is not a valid image."
    name = secure_filename(secrets.token_hex(12) + "." + ext)
    file.save(UPLOAD_DIR / name)
    return name, None

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

def send_mail(to, subject, body, reply_to=None):
    host = os.environ.get("SMTP_HOST")
    if not host or not to:
        log.warning("Email not sent (SMTP_HOST or recipient missing): %s", subject)
        return False
    def job():
        try:
            port = int(os.environ.get("SMTP_PORT", "587"))
            user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
            m = EmailMessage()
            m["Subject"] = re.sub(r"[\r\n]+", " ", subject)
            m["From"] = os.environ.get("MAIL_FROM") or user or "no-reply@localhost"
            m["To"] = to
            if reply_to:
                m["Reply-To"] = reply_to
            m.set_content(body)
            s = smtplib.SMTP_SSL(host, port, timeout=20) if port == 465 else smtplib.SMTP(host, port, timeout=20)
            with s:
                if port != 465:
                    s.starttls()
                if user:
                    s.login(user, pw or "")
                s.send_message(m)
        except Exception:
            log.exception("Email failed: %s", subject)
    threading.Thread(target=job, daemon=True).start()
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
        testimonials = c.execute("SELECT * FROM testimonials ORDER BY sort_order,id").fetchall()
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

@app.route("/robots.txt")
def robots():
    return Response(f"User-agent: *\nDisallow: /admin\nSitemap: {request.url_root}sitemap.xml\n", mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{request.url_root}</loc></url></urlset>', mimetype="application/xml")

@app.route("/quote", methods=["POST"])
def submit_quote():
    if request.form.get("company_website"):  # honeypot: bots fill this in
        return render_template("quote_received.html")
    f = {k: request.form.get(k, "").strip()[:2000] for k in ("name", "email", "date", "guests", "occasion", "message")}
    if not f["name"] or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", f["email"]):
        return render_template("quote_received.html", error="Please enter your name and a valid email address."), 400
    with conn() as c:
        c.execute("INSERT INTO quotes(name,email,event_date,guests,occasion,message) VALUES(?,?,?,?,?,?)",
                  (f["name"], f["email"], f["date"], f["guests"], f["occasion"], f["message"]))
    s = get_settings()
    send_mail(s["notify_email"] or s["email"], f"New quote request from {f['name']}",
              f"Name: {f['name']}\nEmail: {f['email']}\nOccasion: {f['occasion']}\nDate: {f['date']}\nGuests: {f['guests']}\n\n{f['message']}\n\nManage it in your dashboard: {request.url_root}admin",
              reply_to=f["email"])
    if os.environ.get("AUTO_REPLY", "1") == "1":
        send_mail(f["email"], f"We received your request — {s['chef_name']}",
                  f"Hi {f['name']},\n\nThank you for your enquiry. {s['chef_name']} has received your details and will reply with a tailored quote soon.\n\n{s['chef_name']}\n{s['location']}")
    return render_template("quote_received.html")

# ---------- owner ----------
@app.route("/admin/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if too_many_logins():
            return render_template("login.html", error="Too many attempts. Wait 10 minutes and try again."), 429
        with conn() as c:
            u = c.execute("SELECT * FROM admin_users WHERE username=?", (request.form.get("username", "").strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear(); session.permanent = True; session["uid"] = u["id"]
            return redirect(url_for("admin"))
        _attempts[request.remote_addr or "?"].append(time.time())
        error = "Incorrect username or password."
    return render_template("login.html", error=error)

@app.route("/admin/logout", methods=["POST"])
def logout():
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
    return redirect(url_for("admin", message="Login details updated."))

@app.route("/admin")
@logged_in
def admin():
    with conn() as c:
        photos = c.execute("SELECT * FROM photos ORDER BY sort_order,id DESC").fetchall()
        services = c.execute("SELECT * FROM services ORDER BY sort_order,id").fetchall()
        testimonials = c.execute("SELECT * FROM testimonials ORDER BY sort_order,id").fetchall()
        quotes = c.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
        occasions = c.execute("SELECT * FROM occasions ORDER BY sort_order,id").fetchall()
    counts = {"photos": len(photos), "services": len(services), "testimonials": len(testimonials),
              "quotes": len(quotes), "new_quotes": sum(1 for q in quotes if q["status"] == "New"), "occasions": len(occasions)}
    return render_template("admin.html", photos=photos, services=services, testimonials=testimonials,
                           occasions=occasions, quotes=quotes, counts=counts, message=request.args.get("message"),
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
    ok = send_mail(to, "Test email from your website", "Email notifications are working.")
    return redirect(url_for("admin", message=f"Test email sent to {to}. Check your inbox (and spam)." if ok else "Email is not set up yet: add the SMTP settings on your server."))

init_db()
_hours = to_int(os.environ.get("AUTO_BACKUP_HOURS", "24" if PROD else "0"))
if _hours > 0:
    threading.Thread(target=_backup_loop, args=(_hours,), daemon=True).start()

if __name__ == "__main__":
    app.run(debug=not PROD)
