import os
import sqlite3
import hashlib
from datetime import datetime
from difflib import SequenceMatcher
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lostfound.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "campus-lost-and-found-development-key"
)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ----------------------------- Database ------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            description TEXT,
            colour_brand TEXT,
            location TEXT,
            item_datetime TEXT,
            photo_path TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lost_item_id INTEGER,
            found_item_id INTEGER,
            claimant_user_id INTEGER NOT NULL,
            claim_photo_path TEXT,
            bill_path TEXT,
            additional_details TEXT,
            match_score REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            FOREIGN KEY (claimant_user_id) REFERENCES users(id)
        )
    """)

    conn.commit()

    # Create/update the built-in admin account.
    admin_email = "admin@campus.in"
    admin_password = "Admin#987"
    admin = c.execute(
        "SELECT * FROM users WHERE role='admin' LIMIT 1"
    ).fetchone()

    if not admin:
        c.execute(
            """INSERT INTO users
               (name, email, password_hash, role, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "Campus Admin",
                admin_email,
                generate_password_hash(admin_password),
                "admin",
                datetime.now().isoformat(),
            ),
        )
    elif admin["email"] != admin_email:
        c.execute(
            "UPDATE users SET email=?, password_hash=? WHERE id=?",
            (
                admin_email,
                generate_password_hash(admin_password),
                admin["id"],
            ),
        )

    conn.commit()
    conn.close()


# ----------------------------- AI Matching ---------------------------------

def text_similarity(a, b):
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio() * 100


def location_similarity(a, b):
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    return SequenceMatcher(None, a, b).ratio() * 100


def average_hash(image_path, hash_size=8):
    try:
        img = Image.open(image_path).convert("L").resize(
            (hash_size, hash_size), Image.LANCZOS
        )
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        return "".join("1" if p > avg else "0" for p in pixels)
    except Exception:
        return None


def photo_similarity(path_a, path_b):
    if not path_a or not path_b:
        return 0.0

    full_a = os.path.join(BASE_DIR, "static", path_a)
    full_b = os.path.join(BASE_DIR, "static", path_b)

    if not os.path.exists(full_a) or not os.path.exists(full_b):
        return 0.0

    hash_a = average_hash(full_a)
    hash_b = average_hash(full_b)

    if not hash_a or not hash_b:
        return 0.0

    distance = sum(a != b for a, b in zip(hash_a, hash_b))
    return round((1 - distance / len(hash_a)) * 100, 2)


def compute_match_score(item_a, item_b):
    photo_sim = photo_similarity(
        item_a["photo_path"], item_b["photo_path"]
    )

    text_a = (
        f'{item_a["name"]} {item_a["description"] or ""} '
        f'{item_a["colour_brand"] or ""}'
    )
    text_b = (
        f'{item_b["name"]} {item_b["description"] or ""} '
        f'{item_b["colour_brand"] or ""}'
    )

    desc_sim = text_similarity(text_a, text_b)
    loc_sim = location_similarity(item_a["location"], item_b["location"])

    cat_a = (item_a["category"] or "").strip().lower()
    cat_b = (item_b["category"] or "").strip().lower()
    category_sim = 100.0 if cat_a and cat_a == cat_b else 0.0

    overall = (
        photo_sim * 0.35
        + desc_sim * 0.30
        + loc_sim * 0.20
        + category_sim * 0.15
    )

    return {
        "overall": round(overall, 1),
        "photo": round(photo_sim, 1),
        "description": round(desc_sim, 1),
        "location": round(loc_sim, 1),
        "category": round(category_sim, 1),
    }


def find_matches_for(item):
    opposite = "found" if item["item_type"] == "lost" else "lost"

    conn = get_db()
    candidates = conn.execute(
        """SELECT * FROM items
           WHERE item_type=? AND status='active' AND id != ?
           ORDER BY created_at DESC""",
        (opposite, item["id"]),
    ).fetchall()
    conn.close()

    results = []
    for candidate in candidates:
        scores = compute_match_score(item, candidate)
        if scores["overall"] >= 40:
            results.append({"item": candidate, "scores": scores})

    results.sort(
        key=lambda result: result["scores"]["overall"],
        reverse=True,
    )
    return results


# ----------------------------- Authentication ------------------------------

def current_user():
    if "user_id" not in session:
        return None

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    conn.close()
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


# ----------------------------- Uploads -------------------------------------

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT
    )


def save_upload(file_storage, prefix):
    if not file_storage or not file_storage.filename:
        return None

    if not allowed_file(file_storage.filename):
        return None

    filename = secure_filename(file_storage.filename)
    unique = hashlib.md5(
        f"{prefix}{datetime.now().timestamp()}".encode()
    ).hexdigest()[:10]

    final_name = f"{prefix}_{unique}_{filename}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], final_name)
    file_storage.save(path)

    return f"uploads/{final_name}"


# ----------------------------- Common context ------------------------------

@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "year": datetime.now().year,
    }


# ----------------------------- Main routes ---------------------------------

@app.route("/")
def index():
    # Always show the supplied landing image when the website is opened.
    # The entire image is clickable and leads to the login page.
    return render_template("landing.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            flash("Login successful.", "success")

            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill all required fields.", "danger")
            return render_template("register.html")

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE email=?", (email,)
        ).fetchone()

        if existing:
            conn.close()
            flash("An account with that email already exists.", "danger")
            return render_template("register.html")

        conn.execute(
            """INSERT INTO users
               (name, email, password_hash, role, created_at)
               VALUES (?, ?, ?, 'user', ?)""",
            (
                name,
                email,
                generate_password_hash(password),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ----------------------------- User routes ---------------------------------

def create_item(item_type, admin=False):
    if item_type not in ("lost", "found"):
        return redirect(url_for("dashboard"))

    user = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Item name is required.", "danger")
            return render_template(
                "report_item.html",
                item_type=item_type,
                admin=admin,
            )

        photo = request.files.get("photo")
        photo_path = save_upload(photo, "item")

        # Item photos are recommended but not mandatory.
        conn = get_db()
        cur = conn.execute(
            """INSERT INTO items
               (user_id, item_type, name, category, description,
                colour_brand, location, item_datetime, photo_path,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (
                user["id"],
                item_type,
                name,
                request.form.get("category", "").strip(),
                request.form.get("description", "").strip(),
                request.form.get("colour_brand", "").strip(),
                request.form.get("location", "").strip(),
                request.form.get("item_datetime", "").strip(),
                photo_path,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()

        flash(
            f"{item_type.capitalize()} item reported. "
            "AI matching is checking possible matches.",
            "success",
        )

        if admin:
            return redirect(url_for("admin_dashboard"))

        return redirect(url_for("view_matches", item_id=new_id))

    return render_template(
        "report_item.html",
        item_type=item_type,
        admin=admin,
    )


@app.route("/report/<item_type>", methods=["GET", "POST"])
@login_required
def report_item(item_type):
    return create_item(item_type, admin=False)


@app.route("/matches/<int:item_id>")
@login_required
def view_matches(item_id):
    conn = get_db()
    item = conn.execute(
        "SELECT * FROM items WHERE id=?", (item_id,)
    ).fetchone()
    conn.close()

    if not item:
        flash("Item not found.", "danger")
        return redirect(url_for("dashboard"))

    matches = find_matches_for(item)
    return render_template(
        "view_matches.html",
        item=item,
        matches=matches,
    )


@app.route("/claim/<int:item_id>/<int:matched_item_id>", methods=["GET", "POST"])
@login_required
def claim_item(item_id, matched_item_id):
    user = current_user()

    conn = get_db()
    my_item = conn.execute(
        "SELECT * FROM items WHERE id=?", (item_id,)
    ).fetchone()
    other_item = conn.execute(
        "SELECT * FROM items WHERE id=?", (matched_item_id,)
    ).fetchone()
    conn.close()

    if not my_item or not other_item:
        flash("Item not found.", "danger")
        return redirect(url_for("dashboard"))

    scores = compute_match_score(my_item, other_item)

    if request.method == "POST":
        claim_photo = save_upload(
            request.files.get("claim_photo"), "claim"
        )

        if not claim_photo:
            flash("Please upload the ownership proof photo.", "danger")
            return render_template(
                "claim.html",
                my_item=my_item,
                other_item=other_item,
                scores=scores,
            )

        bill_path = save_upload(request.files.get("bill"), "bill")
        details = request.form.get("additional_details", "").strip()

        lost_id = (
            my_item["id"]
            if my_item["item_type"] == "lost"
            else other_item["id"]
        )
        found_id = (
            my_item["id"]
            if my_item["item_type"] == "found"
            else other_item["id"]
        )

        conn = get_db()
        conn.execute(
            """INSERT INTO claims
               (lost_item_id, found_item_id, claimant_user_id,
                claim_photo_path, bill_path, additional_details,
                match_score, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                lost_id,
                found_id,
                user["id"],
                claim_photo,
                bill_path,
                details,
                scores["overall"],
                datetime.now().isoformat(),
            ),
        )

        conn.execute(
            """UPDATE items SET status='matched'
               WHERE id IN (?, ?)""",
            (my_item["id"], other_item["id"]),
        )
        conn.commit()
        conn.close()

        flash(
            "Claim submitted. An admin will verify your ownership proof.",
            "success",
        )
        return redirect(url_for("dashboard"))

    return render_template(
        "claim.html",
        my_item=my_item,
        other_item=other_item,
        scores=scores,
    )


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    conn = get_db()
    items = conn.execute(
        """SELECT * FROM items
           WHERE user_id=?
           ORDER BY created_at DESC""",
        (user["id"],),
    ).fetchall()

    claims = conn.execute(
        """SELECT claims.*,
                  li.name AS lost_name,
                  fi.name AS found_name
           FROM claims
           LEFT JOIN items li ON claims.lost_item_id=li.id
           LEFT JOIN items fi ON claims.found_item_id=fi.id
           WHERE claims.claimant_user_id=?
           ORDER BY claims.created_at DESC""",
        (user["id"],),
    ).fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        user=user,
        items=items,
        claims=claims,
    )


# ----------------------------- Admin routes --------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    admin_user = current_user()

    conn = get_db()
    lost_items = conn.execute(
        """SELECT items.*, users.name AS owner_name
           FROM items
           JOIN users ON items.user_id=users.id
           WHERE item_type='lost'
           ORDER BY created_at DESC"""
    ).fetchall()

    found_items = conn.execute(
        """SELECT items.*, users.name AS owner_name
           FROM items
           JOIN users ON items.user_id=users.id
           WHERE item_type='found'
           ORDER BY created_at DESC"""
    ).fetchall()

    stats = {
        "total_lost": conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE item_type='lost'"
        ).fetchone()["c"],
        "total_found": conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE item_type='found'"
        ).fetchone()["c"],
        "pending_claims": conn.execute(
            "SELECT COUNT(*) AS c FROM claims WHERE status='pending'"
        ).fetchone()["c"],
        "returned": conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE status='returned'"
        ).fetchone()["c"],
    }
    conn.close()

    return render_template(
        "admin_dashboard.html",
        admin_user=admin_user,
        lost_items=lost_items,
        found_items=found_items,
        stats=stats,
    )


@app.route("/admin/report/<item_type>", methods=["GET", "POST"])
@admin_required
def admin_report(item_type):
    return create_item(item_type, admin=True)


@app.route("/admin/claims")
@admin_required
def admin_claims():
    conn = get_db()
    claims = conn.execute(
        """SELECT claims.*,
                  u.name AS claimant_name,
                  u.email AS claimant_email,
                  li.name AS lost_name,
                  li.photo_path AS lost_photo,
                  li.location AS lost_location,
                  fi.name AS found_name,
                  fi.photo_path AS found_photo,
                  fi.location AS found_location
           FROM claims
           JOIN users u ON claims.claimant_user_id=u.id
           LEFT JOIN items li ON claims.lost_item_id=li.id
           LEFT JOIN items fi ON claims.found_item_id=fi.id
           ORDER BY claims.created_at DESC"""
    ).fetchall()
    conn.close()

    return render_template(
        "admin_claims.html",
        claims=claims,
    )


@app.route("/admin/claims/<int:claim_id>/<action>")
@admin_required
def admin_claim_action(claim_id, action):
    if action not in ("approve", "reject", "return"):
        return redirect(url_for("admin_claims"))

    conn = get_db()
    claim = conn.execute(
        "SELECT * FROM claims WHERE id=?", (claim_id,)
    ).fetchone()

    if not claim:
        conn.close()
        flash("Claim not found.", "danger")
        return redirect(url_for("admin_claims"))

    if action == "approve":
        conn.execute(
            "UPDATE claims SET status='approved' WHERE id=?",
            (claim_id,),
        )
        flash(
            "Claim approved. You can now mark the item as returned.",
            "success",
        )

    elif action == "reject":
        conn.execute(
            "UPDATE claims SET status='rejected' WHERE id=?",
            (claim_id,),
        )
        conn.execute(
            """UPDATE items SET status='active'
               WHERE id IN (?, ?)""",
            (claim["lost_item_id"], claim["found_item_id"]),
        )
        flash("Claim rejected. Items are available for matching again.", "info")

    elif action == "return":
        conn.execute(
            """UPDATE items SET status='returned'
               WHERE id IN (?, ?)""",
            (claim["lost_item_id"], claim["found_item_id"]),
        )
        flash("Item marked as returned to the owner.", "success")

    conn.commit()
    conn.close()
    return redirect(url_for("admin_claims"))


# ----------------------------- Static uploads ------------------------------

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.errorhandler(413)
def too_large(_error):
    flash("File is too large. Maximum size is 10 MB.", "danger")
    return redirect(request.referrer or url_for("dashboard"))


init_db()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
