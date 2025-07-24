from flask import Flask, render_template, request, redirect, url_for, jsonify
import mysql.connector
from config import MYSQL_CONFIG
from scholarly import scholarly
import re, json

app = Flask(__name__)

# ✅ MySQL connection helper
def get_db():
    return mysql.connector.connect(**MYSQL_CONFIG)

# ✅ Extract scholar_id from Google Scholar URL
def get_scholar_id(link):
    match = re.search(r"user=([a-zA-Z0-9_-]+)", link)
    return match.group(1) if match else None


def fetch_scholar_publications(scholar_link):
    scholar_id = get_scholar_id(scholar_link)
    if not scholar_id:
        print("❌ No scholar_id extracted from:", scholar_link)
        return []

    try:
        # Fetch author and publications list
        author = scholarly.search_author_id(scholar_id)
        author = scholarly.fill(author, sections=["publications"])
        print(f"✅ Fetched Author: {author.get('name')}")

        publications = []
        for pub in author["publications"]:
            try:
                full_pub = scholarly.fill(pub)
                bib = full_pub.get("bib", {})

                publications.append({
                    "title": bib.get("title", ""),
                    "authors": bib.get("author", ""),
                    "year": bib.get("pub_year", ""),
                    "citations": full_pub.get("num_citations", 0)
                })

            except Exception as e:
                print("⚠️ Skipping one publication due to error:", e)
                continue

        print(f"✅ Total Valid Publications Fetched: {len(publications)}")
        return publications

    except Exception as e:
        print("❌ Error fetching publications:", e)
        return []



# ✅ Home route
@app.route("/")
def home():
    return "<h2>Welcome to Innovation Portal</h2><a href='/register'>Register</a>"

# ✅ User Registration with auto-create department
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        department = request.form["department"]
        scholar_link = request.form["scholar_link"]

        db = get_db()
        cursor = db.cursor(dictionary=True)

        # Auto-create department if not exists
        cursor.execute("SELECT id FROM departments WHERE name=%s", (department,))
        dept = cursor.fetchone()

        if not dept:
            cursor.execute("INSERT INTO departments(name) VALUES (%s)", (department,))
            db.commit()
            cursor.execute("SELECT id FROM departments WHERE name=%s", (department,))
            dept = cursor.fetchone()

        dept_id = dept["id"]

        # Insert user
        cursor.execute("""
            INSERT INTO users(name, email, department_id, scholar_link)
            VALUES (%s, %s, %s, %s)
        """, (name, email, dept_id, scholar_link))
        db.commit()
        user_id = cursor.lastrowid

        db.close()

        # Redirect to fetch publications
        return redirect(url_for("fetch_publications", user_id=user_id))

    return render_template("register.html")

# ✅ Fetch publications and show read-only table + Edit button
@app.route("/fetch_publications/<int:user_id>")
def fetch_publications(user_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Get user's Google Scholar link
    cursor.execute("SELECT scholar_link FROM users WHERE id=%s", (user_id,))
    user = cursor.fetchone()
    db.close()

    if not user or not user["scholar_link"]:
        return "❌ No Google Scholar link found for this user."

    # Fetch publications from Google Scholar
    publications = fetch_scholar_publications(user["scholar_link"])

    # Show table + Edit button
    return render_template("review_publications.html", user_id=user_id, publications=publications)

# ✅ Show editable table page
@app.route("/edit_publications/<int:user_id>", methods=["POST"])
def edit_publications(user_id):
    publications_json = request.form.get("publications")
    publications = json.loads(publications_json)

    return render_template("edit_publications.html", user_id=user_id, publications=publications)

@app.route("/save_publications/<int:user_id>", methods=["POST"])
def save_publications(user_id):
    total = int(request.form["total"])

    db = get_db()
    cursor = db.cursor()

    for i in range(1, total + 1):
        title = request.form.get(f"title_{i}", "")
        authors = request.form.get(f"authors_{i}", "")
        year = request.form.get(f"year_{i}", "")
        citations = request.form.get(f"citations_{i}", "")

        cursor.execute("""
            INSERT INTO publications(user_id, title, authors, year, citations)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, title, authors, year, citations))

    db.commit()
    db.close()

    return render_template("success.html")


@app.route("/candidates")
def list_candidates():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Fetch all users with department name
    cursor.execute("""
        SELECT u.id, u.name, u.email, u.scholar_link, d.name AS department
        FROM users u
        JOIN departments d ON u.department_id = d.id
    """)
    candidates = cursor.fetchall()

    # For each user, get publications count
    for cand in candidates:
        cursor.execute("SELECT COUNT(*) AS total FROM publications WHERE user_id=%s", (cand["id"],))
        pub_count = cursor.fetchone()["total"]
        cand["publication_count"] = pub_count

    db.close()

    return render_template("candidates.html", candidates=candidates)

@app.route("/view_publications/<int:user_id>")
def view_publications(user_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Get user info
    cursor.execute("""
        SELECT u.name, u.email, d.name AS department
        FROM users u
        JOIN departments d ON u.department_id = d.id
        WHERE u.id=%s
    """, (user_id,))
    user = cursor.fetchone()

    # Get all publications for this user
    cursor.execute("""
        SELECT title, authors, year, citations
        FROM publications
        WHERE user_id=%s
    """, (user_id,))
    publications = cursor.fetchall()

    db.close()

    return render_template("view_publications.html", user=user, publications=publications)



if __name__ == "__main__":
    app.run(debug=True)
