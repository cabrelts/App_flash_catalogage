import os
import queue
import requests
import threading
import json
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from flask import send_from_directory, send_file

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "appliCatalogagenew")

# --- Config & dossiers ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(APP_DIR, "config.json"), encoding="utf-8"))

# Chemin vers le dossier catalogage
books_folder = os.path.join(APP_DIR, "catalogage")
os.makedirs(books_folder, exist_ok=True)  # Création du dossier s'il n'existe pas
export_folder = cfg["export_folder"]
os.makedirs(export_folder, exist_ok=True)

SAVE_FILE = os.path.join(APP_DIR, "bibliotheque.json")
books_list = json.load(open(SAVE_FILE, "r", encoding="utf-8")) if os.path.exists(SAVE_FILE) else []
save_lock = threading.Lock()
result_queue = queue.Queue()

def save_books():
    with save_lock:
        json.dump(books_list, open(SAVE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def fetch_data(isbn):
    isbn = isbn.strip()
    # OpenLibrary
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json().get(f"ISBN:{isbn}")
        if data:
            return {
                "title": data.get("title", ""),
                "authors": ", ".join(a["name"] for a in data.get("authors", [])),
                "series": data.get("series", ""),
                "genres": ", ".join(s["name"] for s in data.get("subjects", [])),
                "publish_date": data.get("publish_date", ""),
                "publisher": data.get("publishers", [{}])[0].get("name", ""),
                "pages": data.get("number_of_pages", ""),
                "identifier": isbn,
                "summary": "",
                "cover_url": data.get("cover", {}).get("large", "")
            }
    except:
        pass

    # Google Books fallback
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        info = r.json().get("items", [])[0].get("volumeInfo", {})
        return {
            "title": info.get("title", ""),
            "authors": ", ".join(info.get("authors", [])),
            "series": "",
            "genres": ", ".join(info.get("categories", [])),
            "publish_date": info.get("publishedDate", ""),
            "publisher": info.get("publisher", ""),
            "pages": info.get("pageCount", ""),
            "identifier": isbn,
            "summary": info.get("description", ""),
            "cover_url": info.get("imageLinks", {}).get("thumbnail", "")
        }
    except:
        pass

    return {"identifier": isbn, "error": f"Aucun résultat pour {isbn}"}

def download_cover(cover_url):
    if not cover_url:
        print("Aucune URL de couverture fournie.")
        return ""
    try:
        print(f"Téléchargement de l'image depuis : {cover_url}")
        r = requests.get(cover_url, timeout=10)
        r.raise_for_status()
        ext = cover_url.split('.')[-1].split('?')[0][:4]
        filename = f"{len(os.listdir(books_folder)) + 1}.{ext}"
        path = os.path.join(books_folder, filename)
        with open(path, "wb") as f:
            f.write(r.content)
        print(f"Couverture enregistrée à : {path}")
        return url_for("cover_image", filename=filename)  # Retourner l'URL
    except Exception as e:
        print(f"Erreur lors du téléchargement de la couverture : {e}")
        return ""

@app.route("/")
def index():
    return render_template("index.html", books=books_list, new_books=[])

@app.route("/search", methods=["POST"])
def search():
    print(request.form)
    isbns = request.form["isbns"].replace(",", "\n").split()
    results = []
    
    for isbn in isbns:
        book = fetch_data(isbn)
        if not book.get("error"):
            book["cover"] = download_cover(book.get("cover_url", ""))  # Passer le chemin
            books_list.append(book)
        results.append(book)
    
    save_books()
    return render_template("index.html", books=books_list, new_books=results)

@app.route("/manual", methods=["POST"])
def manual():
    data = {k: request.form[k] for k in request.form}
    data.update({"identifier": data.get("isbn","")+data.get("title",""),
                 "cover": "", "summary": "", "read":"", "reading_periods":"", "comments":""})
    books_list.append(data)
    save_books()
    flash("Livre ajouté manuellement")
    return redirect(url_for("index"))

@app.route("/export", methods=["POST"])
def export():
    code = request.form["code"]
    cat = request.form["category"]
    sub = request.form["subcategory"]

    if not all([code, cat, sub]):
        flash("Tous les champs sont obligatoires", "danger")
        return redirect(url_for("index"))
    
    # Création du DataFrame
    df = pd.DataFrame(books_list)
    df["series"], df["genres"] = code, f"{cat}; {sub}"
    fname = f"{cat};{sub};{code}"

    # Enregistrement des fichiers dans le dossier spécifié
    csv_path = os.path.join(export_folder, fname + ".csv")
    excel_path = os.path.join(export_folder, fname + ".xlsx")
    
    # Exportation des données
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(excel_path, index=False, engine="openpyxl")
    
    flash(f"Export réussi: {fname}.csv & .xlsx")
    return send_file(excel_path, as_attachment=True)

@app.route("/clear", methods=["POST"])
def clear():
    books_list.clear()
    save_books()
    flash("Bibliothèque vidée")
    return redirect(url_for("index"))

@app.route("/edit/<identifier>", methods=["GET", "POST"])
def edit(identifier):
    book = next((b for b in books_list if b["identifier"] == identifier), None)
    if not book:
        flash("Livre introuvable", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        for key in request.form:
            book[key] = request.form[key]

        # Gérer l'image uploadée (photo prise ou choisie)
        file = request.files.get("cover_upload")
        if file and file.filename:
            ext = file.filename.split('.')[-1]
            filename = f"{len(os.listdir(books_folder)) + 1}.{ext}"
            save_path = os.path.join(books_folder, filename)
            file.save(save_path)
            book["cover"] = url_for("cover_image", filename=filename)

        save_books()
        flash("Livre modifié avec succès", "success")
        return redirect(url_for("index"))

    return render_template("edit.html", book=book)

@app.route('/covers/<path:filename>')
def cover_image(filename):
    return send_from_directory(books_folder, filename)

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        export_folder = request.form["export_folder"]
        books_folder = request.form["books_folder"]

        # Enregistrez les chemins dans le fichier de configuration
        cfg = {
            "export_folder": export_folder,
            "books_folder": books_folder
        }
        with open(os.path.join(APP_DIR, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        flash("Configuration enregistrée avec succès!")
        return redirect(url_for("index"))

    return render_template("setup.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)