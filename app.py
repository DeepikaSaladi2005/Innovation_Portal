# app.py
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import mysql.connector
from config import MYSQL_CONFIG
from scholarly import scholarly
import re, json
from werkzeug.security import generate_password_hash, check_password_hash
from models import db
from flask_migrate import Migrate
# Add io, csv, and datetime to your imports
import io
import csv
from datetime import datetime
# Make sure Response is imported from Flask
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:root@localhost/innovation_portal'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "CHANGE_THIS_TO_A_SECURE_RANDOM_STRING"

# Initialize db
db.init_app(app)

# Initialize Flask-Migrate
migrate = Migrate(app, db)

# If you want to auto-create tables (without migrations) - optional
with app.app_context():
    db.create_all()

# ---------------------------
# MySQL connection helper
# ---------------------------
def get_db():
    return mysql.connector.connect(**MYSQL_CONFIG)

# ---------------------------
# Initialize base tables + dynamic_fields meta table
# ---------------------------
def ensure_base_tables():
    db_conn = get_db()
    cur = db_conn.cursor()

    # departments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) UNIQUE
        )
    """)

    # users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users_new (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            role ENUM('admin','user','faculty') DEFAULT 'user',
            department_id INT NULL,
            scholar_link TEXT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
                ON UPDATE CASCADE ON DELETE SET NULL
        )
    """)

    # publications
    cur.execute("""
        CREATE TABLE IF NOT EXISTS publications (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            title TEXT NOT NULL,
            authors TEXT,
            year VARCHAR(16),
            citations VARCHAR(16),
            FOREIGN KEY (user_id) REFERENCES users_new(id)
                ON UPDATE CASCADE ON DELETE CASCADE
        )
    """)

    # patents (base columns only; dynamic columns will be ALTER-ed later)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS patents (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            title VARCHAR(255) NOT NULL,
            inventors TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users_new(id)
                ON UPDATE CASCADE ON DELETE CASCADE
        )
    """)

    # commercializations (base columns only)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS commercializations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            project_name VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users_new(id)
                ON UPDATE CASCADE ON DELETE CASCADE
        )
    """)

    # optional admin-defined "standard" fields table (legacy in your code)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS form_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            form_type VARCHAR(64) NOT NULL,
            field_label VARCHAR(255) NOT NULL,
            field_name VARCHAR(128) NOT NULL,
            field_type VARCHAR(64) NOT NULL,
            is_required TINYINT(1) NOT NULL DEFAULT 0,
            options TEXT
        )
    """)

    # dynamic meta table (for ALTER TABLE driven fields)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dynamic_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            table_name VARCHAR(128) NOT NULL,
            field_name VARCHAR(128) NOT NULL,
            field_label VARCHAR(255) NOT NULL,
            field_type VARCHAR(64) NOT NULL,
            is_required TINYINT(1) NOT NULL DEFAULT 0,
            options TEXT,
            UNIQUE KEY uq_table_field (table_name, field_name)
        )
    """)

    db_conn.commit()
    cur.close()
    db_conn.close()

# call once on startup
ensure_base_tables()

# ---------------------------
# Utility: validate identifiers and types
# ---------------------------
VALID_TABLES = {"patents", "commercializations"}
# allowed human-friendly types -> actual SQL type
VALID_TYPES = {
    "text": "VARCHAR(255)",
    "number": "INT",  #  <-- ADD THIS LINE
    "checkbox": "TINYINT(1)", # <-- ADD THIS LINE to match your form
    "longtext": "TEXT",
    "textarea": "TEXT",
    "select": "VARCHAR(255)",
    "date": "DATE",
    "int": "INT",
    "float": "DOUBLE",
    "bool": "TINYINT(1)"
}

# mapping to safe HTML input types for templates
HTML_INPUT_MAP = {
    "text": "text",
    "longtext": "textarea",
    "textarea": "textarea",
    "select": "select",
    "date": "date",
    "int": "number",
    "float": "number",
    "bool": "checkbox",
}

IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")  # start letter, then letters/digits/underscore

def valid_identifier(name: str) -> bool:
    return bool(IDENTIFIER_RE.match(name))

def valid_table(name: str) -> bool:
    return name in VALID_TABLES

def sql_type_from_key(key: str) -> str:
    return VALID_TYPES.get(key)

def html_type_from_key(key: str) -> str:
    return HTML_INPUT_MAP.get(key, "text")

def coerce_form_value(value_raw, type_key):
    """Cast request value to correct Python type for MySQL driver."""
    if type_key == "bool":
        # checkbox is present => "on", absent => None
        return 1 if (value_raw in ("on", "1", "true", "True", 1, True)) else 0
    if value_raw in (None, ""):
        return None
    if type_key == "int":
        try:
            return int(value_raw)
        except Exception:
            return None
    if type_key == "float":
        try:
            return float(value_raw)
        except Exception:
            return None
    # date/text/select/longtext/textarea -> leave as-is (MySQL will validate DATE)
    return value_raw

# ---------------------------
# Helper: dynamic fields fetch
# ---------------------------
def get_dynamic_fields(table_name, map_for_form=True):
    """Return list of dicts for dynamic fields; when map_for_form=True,
    field_type is converted to HTML-safe type and 'orig_type' is provided."""
    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT field_name, field_label, field_type, is_required, IFNULL(options, '') AS options
        FROM dynamic_fields
        WHERE table_name=%s
        ORDER BY id
    """, (table_name,))
    rows = cursor.fetchall()
    cursor.close()
    db_conn.close()

    for r in rows:
        r["is_required"] = bool(r.get("is_required", 0))
        r["orig_type"] = r["field_type"]
        if map_for_form:
            r["field_type"] = html_type_from_key(r["field_type"])
    return rows  # list of dicts

# ---------------------------
# Google Scholar helpers
# ---------------------------
def get_scholar_id(link):
    match = re.search(r"user=([a-zA-Z0-9_-]+)", link)
    return match.group(1) if match else None

def fetch_scholar_publications(scholar_link):
    scholar_id = get_scholar_id(scholar_link)
    if not scholar_id:
        app.logger.warning("No scholar_id extracted from: %s", scholar_link)
        return []

    try:
        author = scholarly.search_author_id(scholar_id)
        author = scholarly.fill(author, sections=["publications"])
        app.logger.info("Fetched Author: %s", author.get("name"))

        publications = []
        for pub in author.get("publications", []):
            try:
                full_pub = scholarly.fill(pub)
                bib = full_pub.get("bib", {})

                publications.append({
                    "title": bib.get("title", "").strip(),
                    "authors": bib.get("author", "").strip(),
                    "year": bib.get("pub_year", ""),
                    "citations": str(full_pub.get("num_citations", 0))
                })
            except Exception as e:
                app.logger.warning("Skipping one publication due to error: %s", e)
                continue

        app.logger.info("Total Valid Publications Fetched: %d", len(publications))
        return publications

    except Exception as e:
        app.logger.error("Error fetching publications: %s", e)
        return []

# --- Delete Publication ---
@app.route("/user/delete_publication/<int:pub_id>", methods=["POST"])
def delete_publication(pub_id):
    # Ensure a user is logged in
    if "user_id" not in session or session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session["user_id"]
    db_conn = get_db()
    cursor = db_conn.cursor()

    try:
        # Security Check: Ensure the publication belongs to the logged-in user
        cursor.execute("SELECT user_id FROM publications WHERE id = %s", (pub_id,))
        pub = cursor.fetchone()

        if pub and pub[0] == user_id:
            # If ownership is confirmed, delete the record
            cursor.execute("DELETE FROM publications WHERE id = %s", (pub_id,))
            db_conn.commit()
            flash("Publication deleted successfully.", "success")
        else:
            flash("Publication not found or you do not have permission to delete it.", "danger")

    except Exception as e:
        db_conn.rollback()
        app.logger.error(f"Error deleting publication: {e}")
        flash("An error occurred while deleting the publication.", "danger")
    finally:
        cursor.close()
        db_conn.close()

    return redirect(url_for("user_dashboard"))


#edit Publications
# --- Edit Publication ---
@app.route("/user/edit_publication/<int:pub_id>", methods=["GET", "POST"])
def edit_publication(pub_id):
    if "user_id" not in session or session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session["user_id"]
    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    # Security Check: Fetch the publication and ensure it belongs to the current user
    cursor.execute("SELECT * FROM publications WHERE id = %s AND user_id = %s", (pub_id, user_id))
    publication = cursor.fetchone()

    if not publication:
        cursor.close()
        db_conn.close()
        flash("Publication not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("user_dashboard"))

    if request.method == "POST":
        # Handle the form submission
        title = request.form.get("title", "").strip()
        authors = request.form.get("authors", "")
        year = request.form.get("year", "")

        if not title:
            flash("Title is required.", "warning")
            return render_template("edit_publication.html", publication=publication)

        try:
            # Update the record in the database
            cursor.execute("""
                UPDATE publications SET title = %s, authors = %s, year = %s
                WHERE id = %s AND user_id = %s
            """, (title, authors, year, pub_id, user_id))
            db_conn.commit()
            flash("Publication updated successfully.", "success")
            cursor.close()
            db_conn.close()
            return redirect(url_for("user_dashboard"))
        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error updating publication: {e}")
            flash("An error occurred while updating.", "danger")
    
    # For a GET request, just show the form
    cursor.close()
    db_conn.close()
    return render_template("edit_publication.html", publication=publication)

# ---------------------------
# Routes: home / auth
# ---------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    role = request.args.get("role", "user")  # default to user
    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        department = request.form.get("department") or request.form.get("department_id")

        if not (name and email and password):
            flash("Please fill required fields.", "danger")
            cursor.close(); db_conn.close()
            return redirect(url_for("register", role=role))

        # auto-create department if text provided
        dept_id = None
        if department:
            try:
                int(department)
                cursor.execute("SELECT id FROM departments WHERE id=%s", (department,))
                d = cursor.fetchone()
                if d:
                    dept_id = d["id"]
            except ValueError:
                cursor.execute("SELECT id FROM departments WHERE name=%s", (department,))
                d = cursor.fetchone()
                if not d:
                    cursor.execute("INSERT INTO departments (name) VALUES (%s)", (department,))
                    db_conn.commit()
                    cursor.execute("SELECT id FROM departments WHERE name=%s", (department,))
                    d = cursor.fetchone()
                dept_id = d["id"] if d else None

        hashed = generate_password_hash(password)

        cursor.execute("""
            INSERT INTO users_new (name, email, department_id, password, role)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, email, dept_id, hashed, role))
        db_conn.commit()
        cursor.close()
        db_conn.close()
        flash("Registration successful. Please login.", "success")
        return redirect(url_for("login", role=role))

    cursor.execute("SELECT id, name FROM departments ORDER BY name")
    departments = cursor.fetchall()
    cursor.close()
    db_conn.close()
    return render_template("register_new.html", role=role, departments=departments)

@app.route("/login", methods=["GET", "POST"])
def login():
    role = request.args.get("role", "user")
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        db_conn = get_db()
        cursor = db_conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users_new WHERE email=%s", (email,))
        user = cursor.fetchone()
        cursor.close()
        db_conn.close()

        if not user:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login", role=role))

        user_role = user.get("role")
        if user_role == "faculty":
            user_role = "user"

        if user_role != role:
            flash("Please login with the correct role.", "warning")
            return redirect(url_for("login", role=role))

        if check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["role"] = user_role
            session["name"] = user.get("name")
            flash("Login successful.", "success")
            if user_role == "admin":
                return redirect(url_for("admin_dashboard"))
            else:
                return redirect(url_for("user_dashboard"))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login", role=role))

    return render_template("login_new.html", role=role)

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))

# ---------------------------
# User dashboard (renders dynamic fields)
# ---------------------------
@app.route("/user/dashboard", methods=["GET"])
def user_dashboard():
    if session.get("role") != "user":
        return redirect(url_for("home"))

    user_id = session.get("user_id")
    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    # Scholar link
    cursor.execute("SELECT scholar_link FROM users_new WHERE id=%s", (user_id,))
    user = cursor.fetchone()
    scholar_link = user["scholar_link"] if user else None

    # Publications
    cursor.execute("""
        SELECT id, title, authors, year, citations
        FROM publications WHERE user_id=%s
        ORDER BY year DESC
    """, (user_id,))
    publications = cursor.fetchall()

    # Patents (saved)
    cursor.execute("SELECT * FROM patents WHERE user_id=%s ORDER BY id DESC", (user_id,))
    patents = cursor.fetchall()

    # Commercializations (saved)
    cursor.execute("SELECT * FROM commercializations WHERE user_id=%s ORDER BY id DESC", (user_id,))
    commercializations = cursor.fetchall()

    cursor.close()
    db_conn.close()

    # dynamic fields for form rendering (with HTML-safe types)
    patent_fields = get_dynamic_fields("patents", map_for_form=True)
    commercialization_fields = get_dynamic_fields("commercializations", map_for_form=True)

    return render_template("user_dashboard_tabs.html",
                           scholar_link=scholar_link,
                           publications=publications,
                           patents=patents,
                           commercializations=commercializations,
                           patent_fields=patent_fields,
                           commercialization_fields=commercialization_fields)

#add publications
@app.route('/add_publication', methods=['POST'])
def add_publication():
    # Ensure a user is logged in
    if "user_id" not in session or session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))
    
    if request.method == 'POST':
        user_id = session["user_id"]
        title = request.form.get('title', '').strip()
        authors = request.form.get('authors', '') 
        year = request.form.get('year', '')
        # The 'journal' field has been removed

        if not title:
            flash("Publication title is required.", "warning")
            return redirect(url_for('user_dashboard'))

        db_conn = get_db()
        cursor = db_conn.cursor()
        
        try:
            # ✅ Updated INSERT statement without 'journal'
            cursor.execute("""
                INSERT INTO publications (user_id, title, authors, year)
                VALUES (%s, %s, %s, %s)
            """, (user_id, title, authors, year))
            db_conn.commit()
            flash("Publication added successfully!", "success")
        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error adding publication: {e}")
            flash("An error occurred while adding the publication.", "danger")
        finally:
            cursor.close()
            db_conn.close()

        return redirect(url_for('user_dashboard'))


# ---------------------------
# Update Scholar link + fetch publications
# ---------------------------
@app.route("/user/update_publications", methods=["POST"])
def update_publications():
    if session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session.get("user_id")
    scholar_link = request.form.get("scholar_link", "").strip()
    if not scholar_link:
        flash("Please enter a valid Google Scholar link.", "warning")
        return redirect(url_for("user_dashboard"))

    db_conn = get_db()
    cursor = db_conn.cursor()
    cursor.execute("UPDATE users_new SET scholar_link=%s WHERE id=%s", (scholar_link, user_id))
    db_conn.commit()

    publications = fetch_scholar_publications(scholar_link)

    saved = 0
    skipped = 0
    for pub in publications:
        title = pub.get("title", "").strip()
        authors = pub.get("authors", "")
        year = pub.get("year", "")
        citations = pub.get("citations", "0")

        if not title:
            skipped += 1
            continue

        cursor.execute("SELECT id FROM publications WHERE user_id=%s AND title=%s", (user_id, title))
        exists = cursor.fetchone()
        if exists:
            skipped += 1
            continue

        cursor.execute("""INSERT INTO publications (user_id, title, authors, year, citations)
                          VALUES (%s, %s, %s, %s, %s)""",
                       (user_id, title, authors, year, citations))
        saved += 1

    db_conn.commit()
    cursor.close()
    db_conn.close()
    flash(f"Fetched {len(publications)} publications. Saved: {saved}, Skipped: {skipped}", "success")
    return redirect(url_for("user_dashboard"))

# ---------------------------
# Add patent (handles dynamic fields)
# ---------------------------
@app.route("/user/add_patent", methods=["POST"])
def add_patent():
    if session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session.get("user_id")
    title = request.form.get("title", "").strip()
    inventors = request.form.get("inventors", "").strip()

    if not title:
        flash("Patent title is required.", "warning")
        return redirect(url_for("user_dashboard"))

    db_conn = get_db()
    cursor = db_conn.cursor()

    dyn_fields = get_dynamic_fields("patents", map_for_form=False)  # need orig types here
    dynamic_col_names = []
    dynamic_values = []

    for f in dyn_fields:
        fname = f["field_name"]
        orig_type = f["orig_type"]
        raw = request.form.get(fname)  # checkbox may be None when not checked
        val = coerce_form_value(raw, orig_type)
        dynamic_col_names.append(fname)
        dynamic_values.append(val)

    base_cols = ["user_id", "title", "inventors"]
    base_vals = [user_id, title, inventors]

    all_cols = base_cols + dynamic_col_names
    placeholders = ", ".join(["%s"] * len(all_cols))
    query = f"INSERT INTO patents ({', '.join(all_cols)}) VALUES ({placeholders})"

    try:
        cursor.execute(query, tuple(base_vals + dynamic_values))
        db_conn.commit()
        flash("Patent added successfully.", "success")
    except mysql.connector.Error as e:
        db_conn.rollback()
        app.logger.error("Error inserting patent: %s", e)
        flash(f"Error saving patent: {e}", "danger")
    finally:
        cursor.close()
        db_conn.close()

    return redirect(url_for("user_dashboard"))

# ---------------------------
# Add commercialization (handles dynamic fields)
# ---------------------------
@app.route("/user/add_commercialization", methods=["POST"])
def add_commercialization():
    if session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session.get("user_id")
    project_name = request.form.get("project_name", "").strip()
    if not project_name:
        flash("Project name is required.", "warning")
        return redirect(url_for("user_dashboard"))

    db_conn = get_db()
    cursor = db_conn.cursor()

    dyn_fields = get_dynamic_fields("commercializations", map_for_form=False)
    dynamic_col_names = []
    dynamic_values = []
    for f in dyn_fields:
        fname = f["field_name"]
        orig_type = f["orig_type"]
        raw = request.form.get(fname)
        val = coerce_form_value(raw, orig_type)
        dynamic_col_names.append(fname)
        dynamic_values.append(val)

    base_cols = ["user_id", "project_name"]
    base_vals = [user_id, project_name]

    all_cols = base_cols + dynamic_col_names
    placeholders = ", ".join(["%s"] * len(all_cols))
    query = f"INSERT INTO commercializations ({', '.join(all_cols)}) VALUES ({placeholders})"

    try:
        cursor.execute(query, tuple(base_vals + dynamic_values))
        db_conn.commit()
        flash("Commercialization project added successfully.", "success")
    except mysql.connector.Error as e:
        db_conn.rollback()
        app.logger.error("Error inserting commercialization: %s", e)
        flash(f"Error saving commercialization: {e}", "danger")
    finally:
        cursor.close()
        db_conn.close()

    return redirect(url_for("user_dashboard"))

# ---------------------------
# Admin: add new column to a table (dynamic)
# ---------------------------
@app.route("/admin/add_column", methods=["POST"])
def add_column():
    if session.get("role") != "admin":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    table_name = request.form.get("table_name", "").strip()
    field_name = request.form.get("field_name", "").strip()
    field_label = request.form.get("field_label", "").strip() or field_name
    field_type_key = request.form.get("field_type", "").strip()  # one of keys in VALID_TYPES
    is_required = request.form.get("is_required") == "on"
    options = request.form.get("options", "").strip() or None

    # validation
    if not (table_name and field_name and field_type_key):
        flash("Please provide table, field name and type.", "warning")
        return redirect(url_for("admin_dashboard"))

    if not valid_table(table_name):
        flash("Invalid table selected.", "danger")
        return redirect(url_for("admin_dashboard"))

    if not valid_identifier(field_name):
        flash("Invalid field name. Use letters, numbers and underscores, start with a letter.", "danger")
        return redirect(url_for("admin_dashboard"))

    sql_type = sql_type_from_key(field_type_key)
    if not sql_type:
        flash("Invalid field type.", "danger")
        return redirect(url_for("admin_dashboard"))

    # perform ALTER TABLE
    db_conn = get_db()
    cursor = db_conn.cursor()
    try:
        # check if column already exists: query INFORMATION_SCHEMA
        cursor.execute("""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """, (MYSQL_CONFIG["database"], table_name, field_name))
        exists = cursor.fetchone()[0]
        if exists:
            flash("Column already exists in the selected table.", "warning")
            cursor.close()
            db_conn.close()
            return redirect(url_for("admin_dashboard"))

        # safe to alter
        alter_sql = f"ALTER TABLE `{table_name}` ADD COLUMN `{field_name}` {sql_type} DEFAULT NULL"
        cursor.execute(alter_sql)
        db_conn.commit()

        # insert into dynamic_fields meta table (store is_required as tinyint)
        cursor.execute("""
            INSERT INTO dynamic_fields (table_name, field_name, field_label, field_type, is_required, options)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (table_name, field_name, field_label, field_type_key, 1 if is_required else 0, options))
        db_conn.commit()
        flash(f"Added column `{field_name}` to `{table_name}`.", "success")
    except mysql.connector.Error as e:
        db_conn.rollback()
        app.logger.error("Error adding column: %s", e)
        flash(f"Database error: {e}", "danger")
    finally:
        cursor.close()
        db_conn.close()

    return redirect(url_for("admin_dashboard"))

# ---------------------------
# Admin Dashboard (with search)
# ---------------------------
@app.route("/admin_dashboard")
def admin_dashboard():
    # Only allow admin access
    if "user_id" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))

    search = (request.args.get("search") or "").strip()

    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    # ✅ GET DYNAMIC FIELD DEFINITIONS (to use as table headers)
    patent_dynamic_fields = get_dynamic_fields("patents", map_for_form=False)
    comm_dynamic_fields = get_dynamic_fields("commercializations", map_for_form=False)

    # Fetch registered users with department JOIN
    base_sql = """
        SELECT u.id, u.name, u.email, u.role, d.name AS department
        FROM users_new u
        LEFT JOIN departments d ON u.department_id = d.id
    """
    params = []
    if search:
        base_sql += " WHERE u.name LIKE %s OR u.email LIKE %s "
        like = f"%{search}%"
        params.extend([like, like])
    base_sql += " ORDER BY u.id DESC"

    cursor.execute(base_sql, tuple(params))
    users = cursor.fetchall()

    # Attach related counts/details
    for u in users:
        user_id = u["id"]

        # Publications (no changes here)
        cursor.execute("""
            SELECT id, title, authors, year, citations
            FROM publications
            WHERE user_id=%s
            ORDER BY year DESC
        """, (user_id,))
        u["publications"] = cursor.fetchall()
        u["publication_count"] = len(u["publications"])

        # ✅ Patents (fetch ALL columns with *)
        cursor.execute("SELECT * FROM patents WHERE user_id=%s ORDER BY id DESC", (user_id,))
        u["patents"] = cursor.fetchall()
        u["patent_count"] = len(u["patents"])

        # ✅ Commercializations (fetch ALL columns with *)
        cursor.execute("SELECT * FROM commercializations WHERE user_id=%s ORDER BY id DESC", (user_id,))
        u["commercializations"] = cursor.fetchall()
        u["commercialization_count"] = len(u["commercializations"])

    cursor.close()
    db_conn.close()

    # ✅ PASS THE DYNAMIC FIELD DEFINITIONS TO THE TEMPLATE
    return render_template("admin_dashboard_new.html",
                           users=users,
                           search=search,
                           patent_dynamic_fields=patent_dynamic_fields,
                           comm_dynamic_fields=comm_dynamic_fields)

# ---------------------------
# View publications
# ---------------------------
@app.route("/view_publications/<int:user_id>")
def view_publications(user_id):
    if session.get("role") != "admin" and session.get("user_id") != user_id:
        flash("Not authorized.", "danger")
        return redirect(url_for("home"))

    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT u.id, u.name, u.email, d.name AS department
        FROM users_new u LEFT JOIN departments d ON u.department_id=d.id
        WHERE u.id=%s
    """, (user_id,))
    user = cursor.fetchone()

    cursor.execute("""
        SELECT title, authors, year, citations
        FROM publications WHERE user_id=%s ORDER BY year DESC
    """, (user_id,))
    publications = cursor.fetchall()

    cursor.close()
    db_conn.close()
    return render_template("view_publications.html", user=user, publications=publications)

# ---------------------------
# Edit & Save publications
# ---------------------------
@app.route("/edit_publications/<int:user_id>", methods=["GET"])
def edit_publications(user_id):
    if session.get("user_id") != user_id and session.get("role") != "admin":
        flash("Not authorized.", "danger")
        return redirect(url_for("home"))

    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM publications WHERE user_id = %s", (user_id,))
    publications = cursor.fetchall()
    cursor.close()
    db_conn.close()

    return render_template("edit_publications.html", user_id=user_id, publications=publications)

@app.route("/save_publications/<int:user_id>", methods=["POST"])
def save_publications(user_id):
    if session.get("user_id") != user_id and session.get("role") != "admin":
        flash("Not authorized.", "danger")
        return redirect(url_for("home"))

    total = int(request.form["total"])

    db_conn = get_db()
    cursor = db_conn.cursor()

    # Delete old
    cursor.execute("DELETE FROM publications WHERE user_id = %s", (user_id,))

    # Insert new
    for i in range(1, total + 1):
        title = request.form.get(f"title_{i}", "")
        authors = request.form.get(f"authors_{i}", "")
        year = request.form.get(f"year_{i}", "")
        citations = request.form.get(f"citations_{i}", "")

        cursor.execute("""
            INSERT INTO publications(user_id, title, authors, year, citations)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, title, authors, year, citations))

    db_conn.commit()
    cursor.close()
    db_conn.close()

    flash("Publications updated successfully.", "success")

    # back to user_dashboard
    return redirect(url_for("user_dashboard"))

# --- Edit Patent ---
@app.route("/edit_patent/<int:patent_id>", methods=["GET", "POST"])
def edit_patent(patent_id):
    if session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    # Fetch patent record
    cursor.execute("SELECT * FROM patents WHERE id=%s AND user_id=%s",
                   (patent_id, session["user_id"]))
    patent = cursor.fetchone()
    if not patent:
        cursor.close()
        db_conn.close()
        flash("Patent not found.", "danger")
        return redirect(url_for("user_dashboard"))

    # Fetch dynamic fields
    patent_fields = get_dynamic_fields("patents", map_for_form=True)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        inventors = request.form.get("inventors", "").strip()

        if not title:
            flash("Title is required.", "warning")
            return redirect(url_for("edit_patent", patent_id=patent_id))

        # Update base + dynamic fields
        updates = {"title": title, "inventors": inventors}
        for f in get_dynamic_fields("patents", map_for_form=False):
            fname = f["field_name"]
            orig_type = f["orig_type"]
            raw = request.form.get(fname)
            updates[fname] = coerce_form_value(raw, orig_type)

        set_clause = ", ".join([f"{col}=%s" for col in updates.keys()])
        query = f"UPDATE patents SET {set_clause} WHERE id=%s AND user_id=%s"
        values = list(updates.values()) + [patent_id, session["user_id"]]

        cursor.execute(query, values)
        db_conn.commit()

        cursor.close()
        db_conn.close()
        flash("Patent updated successfully.", "success")
        return redirect(url_for("user_dashboard"))

    cursor.close()
    db_conn.close()
    return render_template("edit_patent.html", patent=patent, patent_fields=patent_fields)

# --- Delete Patent ---
@app.route("/user/delete_patent/<int:patent_id>", methods=["POST"])
def delete_patent(patent_id):
    # Ensure a user is logged in
    if "user_id" not in session or session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session["user_id"]
    db_conn = get_db()
    cursor = db_conn.cursor()

    try:
        # Security Check: Ensure the patent belongs to the logged-in user before deleting
        cursor.execute("SELECT user_id FROM patents WHERE id = %s", (patent_id,))
        patent = cursor.fetchone()

        if patent and patent[0] == user_id:
            # If ownership is confirmed, delete the patent
            cursor.execute("DELETE FROM patents WHERE id = %s", (patent_id,))
            db_conn.commit()
            flash("Patent deleted successfully.", "success")
        else:
            flash("Patent not found or you do not have permission to delete it.", "danger")

    except Exception as e:
        db_conn.rollback()
        app.logger.error(f"Error deleting patent: {e}")
        flash("An error occurred while deleting the patent.", "danger")
    finally:
        cursor.close()
        db_conn.close()

    return redirect(url_for("user_dashboard"))

# --- Delete Commercialization ---
@app.route("/user/delete_commercialization/<int:comm_id>", methods=["POST"])
def delete_commercialization(comm_id):
    # Ensure a user is logged in
    if "user_id" not in session or session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    user_id = session["user_id"]
    db_conn = get_db()
    cursor = db_conn.cursor()

    try:
        # Security Check: Ensure the record belongs to the logged-in user
        cursor.execute("SELECT user_id FROM commercializations WHERE id = %s", (comm_id,))
        comm = cursor.fetchone()

        if comm and comm[0] == user_id:
            # If ownership is confirmed, delete the record
            cursor.execute("DELETE FROM commercializations WHERE id = %s", (comm_id,))
            db_conn.commit()
            flash("Commercialization record deleted successfully.", "success")
        else:
            flash("Record not found or you do not have permission to delete it.", "danger")

    except Exception as e:
        db_conn.rollback()
        app.logger.error(f"Error deleting commercialization: {e}")
        flash("An error occurred while deleting the record.", "danger")
    finally:
        cursor.close()
        db_conn.close()

    return redirect(url_for("user_dashboard"))
# --- Edit Commercialization ---
@app.route("/edit_commercialization/<int:comm_id>", methods=["GET", "POST"])
def edit_commercialization(comm_id):
    if session.get("role") != "user":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    # Fetch commercialization record
    cursor.execute("SELECT * FROM commercializations WHERE id=%s AND user_id=%s",
                   (comm_id, session["user_id"]))
    commercialization = cursor.fetchone()
    if not commercialization:
        cursor.close()
        db_conn.close()
        flash("Commercialization not found.", "danger")
        return redirect(url_for("user_dashboard"))

    # Fetch dynamic fields (correct plural table name)
    commercialization_fields = get_dynamic_fields("commercializations", map_for_form=True)

    if request.method == "POST":
        project_name = request.form.get("project_name", "").strip()

        if not project_name:
            flash("Project Name is required.", "warning")
            return redirect(url_for("edit_commercialization", comm_id=comm_id))

        # Update base + dynamic fields
        updates = {
            "project_name": project_name
        }
        for f in get_dynamic_fields("commercializations", map_for_form=False):
            fname = f["field_name"]
            orig_type = f["orig_type"]
            raw = request.form.get(fname)
            updates[fname] = coerce_form_value(raw, orig_type)

        set_clause = ", ".join([f"{col}=%s" for col in updates.keys()])
        query = f"UPDATE commercializations SET {set_clause} WHERE id=%s AND user_id=%s"
        values = list(updates.values()) + [comm_id, session["user_id"]]

        cursor.execute(query, values)
        db_conn.commit()

        cursor.close()
        db_conn.close()
        flash("Commercialization updated successfully.", "success")
        return redirect(url_for("user_dashboard"))

    cursor.close()
    db_conn.close()
    return render_template("edit_commercialization.html",
                           commercialization=commercialization,
                           commercialization_fields=commercialization_fields)

# ---------------------------
# Add/delete standard form fields (legacy endpoints kept)
# ---------------------------
@app.route("/admin/add_field", methods=["POST"])
def add_field():
    if session.get("role") != "admin":
        return redirect(url_for("home"))

    form_type = request.form.get("form_type")
    field_label = request.form.get("field_label")
    field_name = request.form.get("field_name")
    field_type = request.form.get("field_type")
    is_required = request.form.get("is_required") == "on"
    options = request.form.get("options", "")

    db_conn = get_db()
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO form_fields (form_type, field_label, field_name, field_type, is_required, options)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (form_type, field_label, field_name, field_type, is_required, options))
    db_conn.commit()
    cursor.close()
    db_conn.close()
    flash("Field added.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete_field/<int:field_id>", methods=["POST"])
def delete_field(field_id):
    if session.get("role") != "admin":
        return redirect(url_for("home"))

    db_conn = get_db()
    cursor = db_conn.cursor()
    cursor.execute("DELETE FROM form_fields WHERE id=%s", (field_id,))
    db_conn.commit()
    cursor.close()
    db_conn.close()
    flash("Field deleted.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route('/delete_column', methods=['POST'])
def delete_column():
    if session.get("role") != "admin":
        flash("Unauthorized", "danger")
        return redirect(url_for("home"))

    table_name = request.form.get('table_name')
    field_name = request.form.get('field_name')

    if not table_name or not field_name:
        flash("Table name and field name are required.", "danger")
        return redirect(url_for('admin_dashboard'))

    protected_fields = ["id", "user_id", "created_at", "updated_at"]

    try:
        conn = get_db()
        cursor = conn.cursor()

        # Validate column exists
        cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
        columns = [col[0] for col in cursor.fetchall()]

        if field_name not in columns:
            flash("Invalid column name.", "danger")
            cursor.close()
            conn.close()
            return redirect(url_for('admin_dashboard'))

        if field_name in protected_fields:
            flash(f"Column '{field_name}' is protected and cannot be deleted.", "warning")
            cursor.close()
            conn.close()
            return redirect(url_for('admin_dashboard'))

        # Drop column from actual table
        cursor.execute(f"ALTER TABLE `{table_name}` DROP COLUMN `{field_name}`")
        conn.commit()

        # Also clean up from dynamic_fields meta table
        cursor.execute("DELETE FROM dynamic_fields WHERE table_name=%s AND field_name=%s",
                       (table_name, field_name))
        conn.commit()

        cursor.close()
        conn.close()

        flash(f"Column '{field_name}' deleted successfully from {table_name}.", "success")
    except Exception as e:
        flash(f"Error deleting column: {str(e)}", "danger")

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/download/<report_type>')
def download_report(report_type):
    # Ensure only admin can download
    if session.get("role") != "admin":
        flash("Unauthorized access.", "danger")
        return redirect(url_for("home"))

    db_conn = get_db()
    cursor = db_conn.cursor(dictionary=True)

    output = io.StringIO()
    writer = csv.writer(output)
    filename = f"{report_type}_report_{datetime.now().strftime('%Y-%m-%d')}.csv"
    
    if report_type == 'publications':
        header_labels = ['User Name', 'User Email', 'Title', 'Authors', 'Year', 'Citations']
        # ✅ Use the new alias 'user_name' and 'user_email'
        header_keys = ['user_name', 'user_email', 'title', 'authors', 'year', 'citations']
        writer.writerow(header_labels)
        
        # ✅ Use aliases (AS) in the SQL query
        cursor.execute("""
            SELECT 
                u.name AS user_name, 
                u.email AS user_email, 
                p.title, p.authors, p.year, p.citations
            FROM publications p JOIN users_new u ON p.user_id = u.id
            ORDER BY u.name, p.year DESC
        """)
        records = cursor.fetchall()
        for record in records:
            writer.writerow([record.get(key) for key in header_keys])

    elif report_type in ['patents', 'commercializations']:
        dyn_fields = get_dynamic_fields(report_type, map_for_form=False)
        
        if report_type == 'patents':
            base_header_labels = ['User Name', 'User Email', 'Title', 'Inventors']
            # ✅ Use the new alias 'user_name' and 'user_email'
            base_header_keys = ['user_name', 'user_email', 'title', 'inventors']
        else:  # commercializations
            base_header_labels = ['User Name', 'User Email', 'Project Name']
            # ✅ Use the new alias 'user_name' and 'user_email'
            base_header_keys = ['user_name', 'user_email', 'project_name']
        
        header_labels = base_header_labels + [f['field_label'] for f in dyn_fields]
        header_keys = base_header_keys + [f['field_name'] for f in dyn_fields]
        writer.writerow(header_labels)

        # ✅ Use aliases (AS) in the SQL query
        cursor.execute(f"""
            SELECT 
                u.name AS user_name, 
                u.email AS user_email, 
                t.*
            FROM {report_type} t JOIN users_new u ON t.user_id = u.id
            ORDER BY u.name
        """)
        records = cursor.fetchall()

        for record in records:
            row_data = [record.get(key) for key in header_keys]
            writer.writerow(row_data)
    else:
        flash("Invalid report type.", "warning")
        return redirect(url_for('admin_dashboard'))

    cursor.close()
    db_conn.close()

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

if __name__ == "__main__":
    app.run(debug=True)
