
import os
import re
from functools import wraps
from datetime import datetime, timedelta
import mysql.connector
from flask import (
    Flask, g, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash


MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", 3306))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "devika@2004")
MYSQL_DB = os.environ.get("MYSQL_DB", "library_mng_system")

SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")

ISSUE_PERIOD_DAYS = 14   # book is due 14 days after issue
FINE_PER_DAY = 5         # Rs. 5 per day late

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _connect(with_db=True):
    #Open a raw connection to MySQL. If with_db is False, connects to the server only
    kwargs = dict(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD)
    if with_db:
        kwargs["database"] = MYSQL_DB
    return mysql.connector.connect(**kwargs)


def get_db():
    #Return a request-scoped MySQL connection (created once per request).
    if "db" not in g:
        g.db = _connect(with_db=True)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None and db.is_connected():
        db.close()


def init_db():
    # 1. Make sure the database itself exists.
    server_conn = _connect(with_db=False)
    cursor = server_conn.cursor()
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    server_conn.commit()
    cursor.close()
    server_conn.close()

    # 2. Connect to the database and create tables.
    conn = _connect(with_db=True)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(120) NOT NULL UNIQUE,
            phone VARCHAR(20),
            address VARCHAR(255),
            password_hash VARCHAR(255) NOT NULL,
            role ENUM('admin', 'member') NOT NULL DEFAULT 'member',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INT AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            author VARCHAR(150) NOT NULL,
            category VARCHAR(100) NOT NULL,
            total_copies INT NOT NULL DEFAULT 1,
            available_copies INT NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            book_id INT NOT NULL,
            member_id INT NOT NULL,
            issue_date DATE NOT NULL,
            due_date DATE NOT NULL,
            return_date DATE NULL,
            fine_amount DECIMAL(8,2) NOT NULL DEFAULT 0.00,
            status ENUM('issued', 'returned') NOT NULL DEFAULT 'issued',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY (member_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    conn.commit()

    # 3. Seed a default admin account if none exists yet.
    cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    if cursor.fetchone() is None:
        default_email = "admin@gmail.com"
        default_password = "admin123"
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, 'admin')",
            ("Library Admin", default_email, generate_password_hash(default_password))
        )
        conn.commit()
        app.logger.info(
            "Seeded default admin account -> email: %s / password: %s (change this after first login)",
            default_email, default_password
        )

    cursor.close()
    conn.close()



@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        # Lets base.html check "if 'some_endpoint' in url_map_endpoints"
        # so the navbar doesn't break before later steps add those routes.
        "url_map_endpoints": {rule.endpoint for rule in app.url_map.iter_rules()},
    }


# ---------------------------------------------------------------------------
# Routes (Step 3: Authentication added below the home page)
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html")


# --- Auth helpers ----------------------------------------------------------
def login_required(role=None):
    """Decorator that redirects to the appropriate login page if the user
    isn't logged in, and optionally enforces a specific role."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to continue.", "warning")
                if role == "admin":
                    return redirect(url_for("admin_login"))
                return redirect(url_for("home"))
            if role and session.get("role") != role:
                flash("You don't have permission to view that page.", "danger")
                return redirect(url_for("home"))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


PASSWORD_RULE_HINT = (
    "Password must be at least 8 characters long and include at least one "
    "uppercase letter, one lowercase letter, one number, and one special "
    "character (e.g. !@#$%)."
)


def is_password_valid(password):
    """Enforce basic password strength constraints."""
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]", password):
        return False
    return True


# --- Member registration ----------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def member_register():
    if session.get("user_id"):
        return redirect(url_for("home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if not name or len(name) < 2:
            errors.append("Please enter your full name.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errors.append("Please enter a valid email address.")
        if not re.match(r"^[6-9][0-9]{9}$", phone):
            errors.append("Please enter a valid 10-digit mobile number (no spaces or symbols).")
        if password != confirm_password:
            errors.append("Password and confirm password do not match.")
        if not is_password_valid(password):
            errors.append(PASSWORD_RULE_HINT)

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form=request.form)

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                flash("An account with that email already exists.", "danger")
                return render_template("register.html", form=request.form)

            cursor.execute(
                "INSERT INTO users (name, email, phone, address, password_hash, role) "
                "VALUES (%s, %s, %s, %s, %s, 'member')",
                (name, email, phone, address, generate_password_hash(password))
            )
            db.commit()
        finally:
            cursor.close()

        flash("Registration successful! You can now log in.", "success")
        return redirect(url_for("home"))

    return render_template("register.html", form={})


# --- Member login ------------------------------------------------------------
@app.route("/login", methods=["POST"])
def member_login():
    if session.get("user_id"):
        return redirect(url_for("home"))

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM users WHERE email = %s AND role = 'member'", (email,)
    )
    user = cursor.fetchone()
    cursor.close()

    if not user or not check_password_hash(user["password_hash"], password):
        flash("Invalid email or password.", "danger")
        return redirect(url_for("home"))

    session.clear()
    session["user_id"] = user["id"]
    session["name"] = user["name"]
    session["role"] = "member"
    flash(f"Welcome back, {user['name']}!", "success")
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    return redirect(url_for("member_dashboard") if "member_dashboard" in endpoints else url_for("home"))


# --- Admin login (separate page from member login) --------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("user_id"):
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users WHERE email = %s AND role = 'admin'", (email,)
        )
        admin = cursor.fetchone()
        cursor.close()

        if not admin or not check_password_hash(admin["password_hash"], password):
            flash("Invalid admin email or password.", "danger")
            return render_template("admin_login.html")

        session.clear()
        session["user_id"] = admin["id"]
        session["name"] = admin["name"]
        session["role"] = "admin"
        flash(f"Welcome back, {admin['name']}.", "success")
        endpoints = {r.endpoint for r in app.url_map.iter_rules()}
        return redirect(url_for("admin_dashboard") if "admin_dashboard" in endpoints else url_for("home"))

    return render_template("admin_login.html")


# --- Logout (shared) ----------------------------------------------------------
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# --- Book Catalog (any logged-in user: member or admin) --------------------
@app.route("/catalog")
@login_required()
def catalog():
    query = request.args.get("q", "").strip()

    db = get_db()
    cursor = db.cursor(dictionary=True)
    if query:
        like = f"%{query}%"
        cursor.execute(
            "SELECT * FROM books WHERE title LIKE %s OR author LIKE %s OR category LIKE %s "
            "ORDER BY title ASC",
            (like, like, like)
        )
    else:
        cursor.execute("SELECT * FROM books ORDER BY title ASC")
    books = cursor.fetchall()
    cursor.close()

    return render_template("catalog.html", books=books, query=query)


# --- Admin: Book management (list / add / edit / delete) -------------------
def _validate_book_form(form):
    """Shared validation for add/edit book forms. Returns (data, errors)."""
    title = form.get("title", "").strip()
    author = form.get("author", "").strip()
    category = form.get("category", "").strip()
    total_copies_raw = form.get("total_copies", "").strip()

    errors = []
    if not title:
        errors.append("Title is required.")
    if not author:
        errors.append("Author is required.")
    if not category:
        errors.append("Category is required.")

    total_copies = None
    if not total_copies_raw.isdigit() or int(total_copies_raw) < 1:
        errors.append("Total copies must be a whole number of 1 or more.")
    else:
        total_copies = int(total_copies_raw)

    return {
        "title": title, "author": author,
        "category": category, "total_copies": total_copies,
    }, errors


@app.route("/admin/books")
@login_required(role="admin")
def admin_books():
    query = request.args.get("q", "").strip()

    db = get_db()
    cursor = db.cursor(dictionary=True)
    if query:
        like = f"%{query}%"
        cursor.execute(
            "SELECT * FROM books WHERE title LIKE %s OR author LIKE %s OR category LIKE %s ORDER BY title ASC",
            (like, like, like)
        )
    else:
        cursor.execute("SELECT * FROM books ORDER BY title ASC")
    books = cursor.fetchall()
    cursor.close()

    return render_template("admin_books.html", books=books, query=query)


@app.route("/admin/books/add", methods=["GET", "POST"])
@login_required(role="admin")
def admin_book_add():
    if request.method == "POST":
        data, errors = _validate_book_form(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin_book_form.html", mode="add", form=request.form, book=None)

        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id FROM books WHERE LOWER(title) = LOWER(%s) AND LOWER(author) = LOWER(%s)",
            (data["title"], data["author"])
        )
        existing = cursor.fetchone()
        if existing:
            cursor.close()
            flash(
                f'"{data["title"]}" by {data["author"]} already exists in the catalog. '
                f'Edit that book instead if you want to change its copy count.',
                "warning"
            )
            return render_template("admin_book_form.html", mode="add", form=request.form, book=None)

        cursor.execute(
            "INSERT INTO books (title, author, category, total_copies, available_copies) "
            "VALUES (%s, %s, %s, %s, %s)",
            (data["title"], data["author"], data["category"],
             data["total_copies"], data["total_copies"])
        )
        db.commit()
        cursor.close()

        flash(f'"{data["title"]}" was added to the catalog.', "success")
        return redirect(url_for("admin_books"))

    return render_template("admin_book_form.html", mode="add", form={}, book=None)


@app.route("/admin/books/edit/<int:book_id>", methods=["GET", "POST"])
@login_required(role="admin")
def admin_book_edit(book_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,))
    book = cursor.fetchone()
    cursor.close()

    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for("admin_books"))

    if request.method == "POST":
        data, errors = _validate_book_form(request.form)

        # How many copies of this book are currently out (issued)?
        currently_issued = book["total_copies"] - book["available_copies"]

        if not errors and data["total_copies"] < currently_issued:
            errors.append(
                f'Total copies can\'t be less than {currently_issued} — '
                f'that many copies are currently issued to members.'
            )

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin_book_form.html", mode="edit", form=request.form, book=book)

        new_available = data["total_copies"] - currently_issued

        cursor = db.cursor()
        cursor.execute(
            "UPDATE books SET title=%s, author=%s, category=%s, "
            "total_copies=%s, available_copies=%s WHERE id=%s",
            (data["title"], data["author"], data["category"],
             data["total_copies"], new_available, book_id)
        )
        db.commit()
        cursor.close()

        flash(f'"{data["title"]}" was updated.', "success")
        return redirect(url_for("admin_books"))

    return render_template("admin_book_form.html", mode="edit", form=book, book=book)


@app.route("/admin/books/delete/<int:book_id>", methods=["POST"])
@login_required(role="admin")
def admin_book_delete(book_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,))
    book = cursor.fetchone()

    if not book:
        cursor.close()
        flash("Book not found.", "danger")
        return redirect(url_for("admin_books"))

    cursor.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE book_id = %s AND status = 'issued'",
        (book_id,)
    )
    issued_count = cursor.fetchone()["c"]
    cursor.close()

    if issued_count > 0:
        flash(
            f'"{book["title"]}" can\'t be deleted — {issued_count} '
            f'cop{"y is" if issued_count == 1 else "ies are"} still issued to members.',
            "danger"
        )
        return redirect(url_for("admin_books"))

    cursor = db.cursor()
    cursor.execute("DELETE FROM books WHERE id = %s", (book_id,))
    db.commit()
    cursor.close()

    flash(f'"{book["title"]}" was removed from the catalog.', "success")
    return redirect(url_for("admin_books"))


# --- Admin: Issue Book -------------------------------------------------------
@app.route("/admin/issue", methods=["GET", "POST"])
@login_required(role="admin")
def admin_issue():
    db = get_db()

    if request.method == "POST":
        member_id = request.form.get("member_id", "").strip()
        book_id = request.form.get("book_id", "").strip()

        errors = []
        if not member_id.isdigit():
            errors.append("Please select a member.")
        if not book_id.isdigit():
            errors.append("Please select a book.")

        book = None
        if not errors:
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,))
            book = cursor.fetchone()
            cursor.close()
            if not book:
                errors.append("Selected book was not found.")
            elif book["available_copies"] < 1:
                errors.append(f'"{book["title"]}" has no copies available to issue.')

        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("admin_issue"))

        issue_date = datetime.now().date()
        due_date = issue_date + timedelta(days=ISSUE_PERIOD_DAYS)

        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO transactions (book_id, member_id, issue_date, due_date, status) "
            "VALUES (%s, %s, %s, %s, 'issued')",
            (book_id, member_id, issue_date, due_date)
        )
        cursor.execute(
            "UPDATE books SET available_copies = available_copies - 1 WHERE id = %s",
            (book_id,)
        )
        db.commit()
        cursor.close()

        flash(
            f'"{book["title"]}" was issued. Due back on {due_date.strftime("%d %b %Y")}.',
            "success"
        )
        return redirect(url_for("admin_issue"))

    # GET: show the issue form + list of currently issued books
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email FROM users WHERE role = 'member' ORDER BY name ASC")
    members = cursor.fetchall()

    cursor.execute(
        "SELECT id, title, author, available_copies FROM books "
        "WHERE available_copies > 0 ORDER BY title ASC"
    )
    available_books = cursor.fetchall()

    cursor.execute("""
        SELECT t.id, t.issue_date, t.due_date, b.title AS book_title,
               u.name AS member_name
        FROM transactions t
        JOIN books b ON b.id = t.book_id
        JOIN users u ON u.id = t.member_id
        WHERE t.status = 'issued'
        ORDER BY t.due_date ASC
    """)
    active_issues = cursor.fetchall()
    cursor.close()

    today = datetime.now().date()
    return render_template(
        "admin_issue.html",
        members=members, available_books=available_books,
        active_issues=active_issues, today=today
    )


# --- Admin: Return Book ------------------------------------------------------
@app.route("/admin/return")
@login_required(role="admin")
def admin_return():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.id, t.issue_date, t.due_date, b.title AS book_title,
               u.name AS member_name
        FROM transactions t
        JOIN books b ON b.id = t.book_id
        JOIN users u ON u.id = t.member_id
        WHERE t.status = 'issued'
        ORDER BY t.due_date ASC
    """)
    active_issues = cursor.fetchall()
    cursor.close()

    today = datetime.now().date()
    return render_template("admin_return.html", active_issues=active_issues, today=today, fine_per_day=FINE_PER_DAY)


@app.route("/admin/return/<int:transaction_id>", methods=["POST"])
@login_required(role="admin")
def admin_return_book(transaction_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.*, b.title AS book_title
        FROM transactions t
        JOIN books b ON b.id = t.book_id
        WHERE t.id = %s AND t.status = 'issued'
    """, (transaction_id,))
    tx = cursor.fetchone()

    if not tx:
        cursor.close()
        flash("That issue record was not found or has already been returned.", "danger")
        return redirect(url_for("admin_return"))

    return_date = datetime.now().date()
    days_late = (return_date - tx["due_date"]).days
    fine_amount = max(days_late, 0) * FINE_PER_DAY

    cursor2 = db.cursor()
    cursor2.execute(
        "UPDATE transactions SET status='returned', return_date=%s, fine_amount=%s WHERE id=%s",
        (return_date, fine_amount, transaction_id)
    )
    cursor2.execute(
        "UPDATE books SET available_copies = available_copies + 1 WHERE id = %s",
        (tx["book_id"],)
    )
    db.commit()
    cursor.close()
    cursor2.close()

    if fine_amount > 0:
        flash(
            f'"{tx["book_title"]}" returned {days_late} day{"s" if days_late != 1 else ""} late. '
            f'Fine: Rs. {fine_amount}.',
            "warning"
        )
    else:
        flash(f'"{tx["book_title"]}" returned on time. No fine.', "success")

    return redirect(url_for("admin_return"))


# --- Member Dashboard ---------------------------------------------------------
@app.route("/member/dashboard")
@login_required(role="member")
def member_dashboard():
    member_id = session["user_id"]
    today = datetime.now().date()

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Currently borrowed books
    cursor.execute("""
        SELECT t.id, t.issue_date, t.due_date, b.title, b.author
        FROM transactions t
        JOIN books b ON b.id = t.book_id
        WHERE t.member_id = %s AND t.status = 'issued'
        ORDER BY t.due_date ASC
    """, (member_id,))
    borrowed = cursor.fetchall()

    # Attach a live "days late" / current fine estimate to each overdue book
    total_fine = 0
    for row in borrowed:
        days_late = (today - row["due_date"]).days
        row["days_late"] = max(days_late, 0)
        row["current_fine"] = row["days_late"] * FINE_PER_DAY
        total_fine += row["current_fine"]

    # Past borrowing history (returned books), most recent first
    cursor.execute("""
        SELECT t.issue_date, t.due_date, t.return_date, t.fine_amount, b.title, b.author
        FROM transactions t
        JOIN books b ON b.id = t.book_id
        WHERE t.member_id = %s AND t.status = 'returned'
        ORDER BY t.return_date DESC
        LIMIT 10
    """, (member_id,))
    history = cursor.fetchall()
    cursor.close()

    return render_template(
        "member_dashboard.html",
        borrowed=borrowed, history=history,
        total_fine=total_fine, today=today, fine_per_day=FINE_PER_DAY
    )


# --- Admin Dashboard ----------------------------------------------------------
@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    today = datetime.now().date()
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS c, COALESCE(SUM(total_copies), 0) AS total_copies FROM books")
    book_stats = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'member'")
    total_members = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM transactions WHERE status = 'issued'")
    books_issued = cursor.fetchone()["c"]

    cursor.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE status = 'issued' AND due_date < %s",
        (today,)
    )
    overdue_count = cursor.fetchone()["c"]

    # Who owes a fine right now (overdue + currently issued)
    cursor.execute("""
        SELECT u.name AS member_name, u.email, b.title AS book_title,
               t.due_date
        FROM transactions t
        JOIN users u ON u.id = t.member_id
        JOIN books b ON b.id = t.book_id
        WHERE t.status = 'issued' AND t.due_date < %s
        ORDER BY t.due_date ASC
    """, (today,))
    overdue_rows = cursor.fetchall()
    cursor.close()

    total_fines_owed = 0
    for row in overdue_rows:
        row["days_late"] = (today - row["due_date"]).days
        row["fine"] = row["days_late"] * FINE_PER_DAY
        total_fines_owed += row["fine"]

    return render_template(
        "admin_dashboard.html",
        total_books=book_stats["c"], total_copies=book_stats["total_copies"],
        total_members=total_members, books_issued=books_issued,
        overdue_count=overdue_count, overdue_rows=overdue_rows,
        total_fines_owed=total_fines_owed
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)