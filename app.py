import os, sqlite3
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "change-this-secret-before-deploying"
DB = "/tmp/booknest.db" if os.environ.get("VERCEL") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "booknest.db")
CATS = ["Fiction", "Non-fiction", "Science", "Technology", "History", "Self-help", "Kids", "Other"]
COLORS = ["#2F6F62", "#14213D", "#7A5C9E", "#3D6A9E", "#B5475B", "#8A6A00"]
app.jinja_env.globals.update(CATS=CATS, color=lambda t: COLORS[sum(map(ord, t)) % len(COLORS)])

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL, role TEXT DEFAULT 'member', city TEXT DEFAULT '', bio TEXT DEFAULT '',
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS books(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT NOT NULL,
  author TEXT NOT NULL, category TEXT, cond TEXT, description TEXT DEFAULT '', available INTEGER DEFAULT 1,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, borrower_id INTEGER NOT NULL,
  status TEXT DEFAULT 'pending', created TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def init_db():
    con = sqlite3.connect(DB); con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM users").fetchone():
        add = lambda n, e, p, r, c: con.execute("INSERT INTO users(name,email,password,role,city) VALUES(?,?,?,?,?)", (n, e, generate_password_hash(p), r, c))
        add("Admin", "admin@booknest.com", "admin123", "admin", "Dhaka")
        add("Ayesha Rahman", "ayesha@demo.com", "demo1234", "member", "Dhaka")
        add("Imran Hossain", "imran@demo.com", "demo1234", "member", "Chattogram")
        books = [(2, "The Alchemist", "Paulo Coelho", "Fiction", "Good"), (2, "Clean Code", "Robert C. Martin", "Technology", "Like new"),
                 (2, "A Brief History of Time", "Stephen Hawking", "Science", "Fair"), (3, "Sapiens", "Yuval Noah Harari", "History", "Good"),
                 (3, "Atomic Habits", "James Clear", "Self-help", "Like new"), (3, "Padma Nadir Majhi", "Manik Bandopadhyay", "Fiction", "Fair")]
        for b in books:
            con.execute("INSERT INTO books(owner_id,title,author,category,cond,description) VALUES(?,?,?,?,?,?)", (*b, "Happy to lend for two weeks."))
        con.execute("INSERT INTO requests(book_id,borrower_id) VALUES(1,3)")
        con.commit()
    con.close()

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

def q(sql, args=(), one=False):
    rows = db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows

def run(sql, args=()):
    cur = db().execute(sql, args); db().commit(); return cur.lastrowid

@app.teardown_appcontext
def close_db(e=None):
    d = g.pop("db", None)
    if d: d.close()

@app.before_request
def load_user():
    g.user = q("SELECT * FROM users WHERE id=?", (session["uid"],), one=True) if "uid" in session else None

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            flash("Log in to continue.", "err"); return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user: return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin": abort(403)
        return f(*a, **k)
    return w

BOOK_SQL = "SELECT b.*, u.name owner, u.city FROM books b JOIN users u ON u.id=b.owner_id "

# ---------- public pages ----------
@app.route("/")
def home():
    stats = {"books": q("SELECT COUNT(*) c FROM books", one=True)["c"], "members": q("SELECT COUNT(*) c FROM users", one=True)["c"],
             "loans": q("SELECT COUNT(*) c FROM requests WHERE status IN ('approved','returned')", one=True)["c"]}
    return render_template("home.html", books=q(BOOK_SQL + "ORDER BY b.id DESC LIMIT 12"), stats=stats)

@app.route("/books")
def books():
    term, cat, only = request.args.get("q", "").strip(), request.args.get("cat", ""), request.args.get("avail")
    sql, args = BOOK_SQL + "WHERE 1=1", []
    if term: sql += " AND (b.title LIKE ? OR b.author LIKE ?)"; args += [f"%{term}%"] * 2
    if cat: sql += " AND b.category=?"; args.append(cat)
    if only: sql += " AND b.available=1"
    return render_template("books.html", books=q(sql + " ORDER BY b.id DESC", args), term=term, cat=cat, only=only)

@app.route("/books/<int:bid>")
def book(bid):
    b = q(BOOK_SQL + "WHERE b.id=?", (bid,), one=True) or abort(404)
    mine = None
    if g.user:
        mine = q("SELECT * FROM requests WHERE book_id=? AND borrower_id=? AND status IN ('pending','approved')", (bid, g.user["id"]), one=True)
    return render_template("book.html", b=b, mine=mine)

# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        n, e, p = request.form["name"].strip(), request.form["email"].strip().lower(), request.form["password"]
        if not n or "@" not in e or len(p) < 6: flash("Enter a name, a valid email and a password of 6+ characters.", "err")
        elif q("SELECT 1 FROM users WHERE email=?", (e,), one=True): flash("That email already has an account.", "err")
        else:
            session["uid"] = run("INSERT INTO users(name,email,password,city) VALUES(?,?,?,?)", (n, e, generate_password_hash(p), request.form.get("city", "").strip()))
            flash("Welcome to BookNest! Add your first book.", "ok"); return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM users WHERE email=?", (request.form["email"].strip().lower(),), one=True)
        if u and check_password_hash(u["password"], request.form["password"]):
            session["uid"] = u["id"]; nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("dashboard"))
        flash("Email or password is incorrect.", "err")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("home"))

# ---------- book CRUD ----------
def save_book(b=None):
    if request.method == "POST":
        f = [request.form.get(k, "").strip() for k in ("title", "author", "category", "cond", "description")]
        if not f[0] or not f[1]: flash("Title and author are required.", "err")
        elif b:
            run("UPDATE books SET title=?,author=?,category=?,cond=?,description=? WHERE id=?", (*f, b["id"]))
            flash("Book updated.", "ok"); return redirect(url_for("book", bid=b["id"]))
        else:
            bid = run("INSERT INTO books(owner_id,title,author,category,cond,description) VALUES(?,?,?,?,?,?)", (g.user["id"], *f))
            flash("Book added to your shelf.", "ok"); return redirect(url_for("book", bid=bid))
    return render_template("book_form.html", b=b)

@app.route("/books/new", methods=["GET", "POST"])
@login_required
def book_new(): return save_book()

@app.route("/books/<int:bid>/edit", methods=["GET", "POST"])
@login_required
def book_edit(bid):
    b = q("SELECT * FROM books WHERE id=?", (bid,), one=True) or abort(404)
    if b["owner_id"] != g.user["id"] and g.user["role"] != "admin": abort(403)
    return save_book(b)

@app.route("/books/<int:bid>/delete", methods=["POST"])
@login_required
def book_delete(bid):
    b = q("SELECT * FROM books WHERE id=?", (bid,), one=True) or abort(404)
    if b["owner_id"] != g.user["id"] and g.user["role"] != "admin": abort(403)
    run("DELETE FROM requests WHERE book_id=?", (bid,)); run("DELETE FROM books WHERE id=?", (bid,))
    flash("Book removed.", "ok"); return redirect(request.referrer or url_for("dashboard"))

# ---------- borrow workflow ----------
@app.route("/books/<int:bid>/request", methods=["POST"])
@login_required
def request_book(bid):
    b = q("SELECT * FROM books WHERE id=?", (bid,), one=True) or abort(404)
    if b["owner_id"] == g.user["id"]: flash("That's your own book.", "err")
    elif not b["available"]: flash("This book is on loan right now.", "err")
    elif q("SELECT 1 FROM requests WHERE book_id=? AND borrower_id=? AND status='pending'", (bid, g.user["id"]), one=True): flash("You already asked for this book.", "err")
    else: run("INSERT INTO requests(book_id,borrower_id) VALUES(?,?)", (bid, g.user["id"])); flash("Request sent to the owner.", "ok")
    return redirect(url_for("book", bid=bid))

@app.route("/requests/<int:rid>/<action>", methods=["POST"])
@login_required
def request_action(rid, action):
    r = q("SELECT r.*, b.owner_id FROM requests r JOIN books b ON b.id=r.book_id WHERE r.id=?", (rid,), one=True) or abort(404)
    if action == "cancel" and r["borrower_id"] == g.user["id"] and r["status"] == "pending":
        run("DELETE FROM requests WHERE id=?", (rid,)); flash("Request cancelled.", "ok")
    elif r["owner_id"] == g.user["id"] and action in ("approve", "decline", "return"):
        if action == "approve" and r["status"] == "pending":
            run("UPDATE requests SET status='approved' WHERE id=?", (rid,)); run("UPDATE books SET available=0 WHERE id=?", (r["book_id"],))
            run("UPDATE requests SET status='declined' WHERE book_id=? AND status='pending'", (r["book_id"],)); flash("Approved. Other pending requests were declined.", "ok")
        elif action == "decline" and r["status"] == "pending":
            run("UPDATE requests SET status='declined' WHERE id=?", (rid,)); flash("Request declined.", "ok")
        elif action == "return" and r["status"] == "approved":
            run("UPDATE requests SET status='returned' WHERE id=?", (rid,)); run("UPDATE books SET available=1 WHERE id=?", (r["book_id"],)); flash("Marked as returned.", "ok")
    else: abort(403)
    return redirect(url_for("dashboard"))

# ---------- member area ----------
@app.route("/dashboard")
@login_required
def dashboard():
    uid = g.user["id"]
    mine = q("SELECT * FROM books WHERE owner_id=? ORDER BY id DESC", (uid,))
    incoming = q("SELECT r.*, b.title, u.name who, u.city FROM requests r JOIN books b ON b.id=r.book_id JOIN users u ON u.id=r.borrower_id "
                 "WHERE b.owner_id=? AND r.status IN ('pending','approved') ORDER BY r.status='pending' DESC, r.id DESC", (uid,))
    outgoing = q("SELECT r.*, b.title, u.name who FROM requests r JOIN books b ON b.id=r.book_id JOIN users u ON u.id=b.owner_id WHERE r.borrower_id=? ORDER BY r.id DESC", (uid,))
    return render_template("dashboard.html", mine=mine, incoming=incoming, outgoing=outgoing)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        n, p = request.form["name"].strip(), request.form.get("password", "")
        if not n: flash("Name can't be empty.", "err")
        elif p and len(p) < 6: flash("New password needs 6+ characters.", "err")
        else:
            run("UPDATE users SET name=?, city=?, bio=? WHERE id=?", (n, request.form.get("city", "").strip(), request.form.get("bio", "").strip(), g.user["id"]))
            if p: run("UPDATE users SET password=? WHERE id=?", (generate_password_hash(p), g.user["id"]))
            flash("Profile saved.", "ok"); return redirect(url_for("profile"))
    return render_template("profile.html")

# ---------- admin ----------
@app.route("/admin")
@admin_required
def admin():
    cats = q("SELECT category, COUNT(*) n FROM books GROUP BY category ORDER BY n DESC")
    lenders = q("SELECT u.name, COUNT(*) n FROM requests r JOIN books b ON b.id=r.book_id JOIN users u ON u.id=b.owner_id "
                "WHERE r.status IN ('approved','returned') GROUP BY u.id ORDER BY n DESC LIMIT 5")
    users = q("SELECT u.*, (SELECT COUNT(*) FROM books WHERE owner_id=u.id) nb FROM users u ORDER BY u.id")
    return render_template("admin.html", cats=cats, maxn=max([c["n"] for c in cats] or [1]), lenders=lenders, users=users,
                           books=q(BOOK_SQL + "ORDER BY b.id DESC"), pending=q("SELECT COUNT(*) c FROM requests WHERE status='pending'", one=True)["c"])

@app.route("/admin/users/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_user(uid, action):
    if uid == g.user["id"]: flash("You can't change your own account here.", "err")
    elif action == "role":
        run("UPDATE users SET role = CASE role WHEN 'admin' THEN 'member' ELSE 'admin' END WHERE id=?", (uid,)); flash("Role updated.", "ok")
    elif action == "delete":
        run("DELETE FROM requests WHERE borrower_id=? OR book_id IN (SELECT id FROM books WHERE owner_id=?)", (uid, uid))
        run("DELETE FROM books WHERE owner_id=?", (uid,)); run("DELETE FROM users WHERE id=?", (uid,)); flash("User and their books deleted.", "ok")
    return redirect(url_for("admin"))

@app.errorhandler(403)
@app.errorhandler(404)
def err(e): return render_template("error.html", e=e), e.code

init_db()
if __name__ == "__main__":
    app.run(debug=True)
