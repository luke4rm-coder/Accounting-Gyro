from dotenv import load_dotenv
load_dotenv()
import os
print("TEMPLATE FOLDER:", os.path.abspath("templates"))
import json
import fitz
from flask import Flask, render_template, request, jsonify
from groq import Groq
from tavily import TavilyClient
from dotenv import load_dotenv

from flask import send_file
from werkzeug.utils import secure_filename
import io
import csv
import pandas as pd
from datetime import datetime


load_dotenv()
GROQ_KEY = os.getenv("GROQ_API_KEY")
TAVILY_KEY = os.getenv("TAVILY_API_KEY")


if not GROQ_KEY:
    print("ERROR: Missing GROQ_API_KEY in .env")
if not TAVILY_KEY:
    print("ERROR: Missing TAVILY_API_KEY in .env")

app = Flask(__name__, template_folder="templates")

try:
    with open("memory.json", "r") as f:
        conversation = json.load(f)
except:
    conversation = []

# GLOBALS
ALLOWED_EXTENSIONS = {"pdf", "csv", "xls", "xlsx"}
EXTRACTED_ROWS = []  

# Initialize Tavily + Groq
tavily = TavilyClient(api_key=TAVILY_KEY)
client = Groq(api_key=GROQ_KEY)


try:
    doc = fitz.open("C:/Users/mendo/OneDrive/Documents/Gyro ai accounting/invoice_template.pdf")
    pdf_text = ""
    for page in doc:
        pdf_text += page.get_text()[:2000]
except Exception as e:
    print("PDF load error:", e)
    pdf_text = ""

pdf_message = {
    "role": "user",
    "content": "Here is a document for you to reference: " + pdf_text
}

pdf_response = {
    "role": "assistant",
    "content": "I have read the document and will use it to answer your questions."
}

system_prompt = {
    "role": "system",
    "content": (
        "You are Gyro, an AI accounting assistant. You help accountants without replacing their jobs. "
        "You MUST understand invoices, balance sheets, income statements, tax returns, AP, AR, payroll, etc. "
        "You automate routine accounting tasks but never give legal or CPA-level advice."
    )
}

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def process_tabular_file(file_storage):
    """
    Read CSV/Excel into rows and append to EXTRACTED_ROWS.
    """
    global EXTRACTED_ROWS
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[1].lower()

    if ext == "csv":
        df = pd.read_csv(file_storage)
    else:
        df = pd.read_excel(file_storage)

    rows = df.to_dict(orient="records")
    EXTRACTED_ROWS.extend(rows)

    return {
        "filename": filename,
        "rows": len(rows),
        "columns": list(df.columns),
    }


def extract_invoice_from_pdf(file_storage):
    pdf_bytes = file_storage.read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    text = ""
    for page in doc:
        text += page.get_text()

    prompt = (
        "Extract structured invoice data from the text below. "
        "Return ONLY valid JSON using this schema:\n"
        "{\n"
        '  "vendor_name": str,\n'
        '  "invoice_number": str,\n'
        '  "invoice_date": str,\n'
        '  "due_date": str,\n'
        '  "currency": str,\n'
        '  "subtotal": float,\n'
        '  "tax": float,\n'
        '  "total": float,\n'
        '  "line_items": [\n'
        "    {\n"
        '      "description": str,\n'
        '      "quantity": float,\n'
        '      "unit_price": float,\n'
        '      "line_total": float\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Invoice text:\n{text}"
    )

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[system_prompt, {"role": "user", "content": prompt}],
        temperature=0.1,
    )

    raw = completion.choices[0].message.content

    try:
        return json.loads(raw)
    except:
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except:
            return {"error": "Could not parse JSON", "raw": raw}

@app.route("/extract-invoice", methods=["POST"])
def extract_invoice():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    if not allowed_file(f.filename) or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF invoices allowed"}), 400

    data = extract_invoice_from_pdf(f)
    return jsonify({"invoice": data})



def categorize_transactions(rows):
    prompt = (
        "Categorize each accounting transaction into a chart-of-accounts category. "
        "Return ONLY JSON list of objects:\n"
        "{\n"
        '  "original": {...},\n'
        '  "category": str\n'
        "}\n\n"
        "Categories include: Office Supplies, Rent, Utilities, Travel, Meals, Payroll, "
        "Professional Services, Software, Taxes, Other.\n\n"
        f"Rows:\n{json.dumps(rows)}"
    )

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[system_prompt, {"role": "user", "content": prompt}],
        temperature=0.2,
    )

    raw = completion.choices[0].message.content

    try:
        return json.loads(raw)
    except:
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            return json.loads(raw[start:end])
        except:
            return {"error": "Could not parse JSON", "raw": raw}

@app.route("/categorize", methods=["POST"])
def categorize():
    if not EXTRACTED_ROWS:
        return jsonify({"error": "No uploaded rows to categorize"}), 400

    data = categorize_transactions(EXTRACTED_ROWS)
    return jsonify({"categorized": data})



@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    global conversation

    data = request.get_json()
    message = data.get("message", "")

    results = ""
    if len(message) <= 300:
        try:
            results = tavily.search(message)
        except Exception as e:
            results = f"Search error: {str(e)}"
    else:
        results = "Search skipped (query too long)."

    conversation.append({
        "role": "user",
        "content": message + " here is some research: " + str(results)
    })

    conversation = conversation[-10:]

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[system_prompt, pdf_message, pdf_response] + conversation
    )

    reply = response.choices[0].message.content
    conversation.append({"role": "assistant", "content": reply})

    with open("memory.json", "w") as f:
        json.dump(conversation, f)

    return jsonify({"reply": reply})

@app.route("/upload", methods=["POST"])
def upload():
    global EXTRACTED_ROWS
    if "files" not in request.files:
        return jsonify({"error": "No files part in request"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    uploaded_info = []

    for f in files:
        if f.filename == "":
            continue
        if not allowed_file(f.filename):
            uploaded_info.append({
                "filename": f.filename,
                "status": "rejected",
                "reason": "File type not allowed",
            })
            continue

        filename = secure_filename(f.filename)
        ext = filename.rsplit(".", 1)[1].lower()

        info = {"filename": filename, "type": ext, "status": "processed"}

        if ext in {"csv", "xls", "xlsx"}:
            try:
                details = process_tabular_file(f)
                info["rows"] = details["rows"]
                info["columns"] = details["columns"]
            except Exception as e:
                info["status"] = "error"
                info["error"] = str(e)

        uploaded_info.append(info)

    return jsonify({"uploaded": uploaded_info})

@app.route("/export", methods=["GET"])
def export():
    global EXTRACTED_ROWS
    if not EXTRACTED_ROWS:
        return jsonify({"error": "No extracted data to export"}), 400

    output = io.StringIO()
    fieldnames = sorted(EXTRACTED_ROWS[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in EXTRACTED_ROWS:
        writer.writerow(row)

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8"))
    mem.seek(0)

    filename = f"gyro_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )

@app.route("/reset", methods=["POST"])
def reset():
    global EXTRACTED_ROWS, conversation
    EXTRACTED_ROWS = []
    conversation = []
    with open("memory.json", "w") as f:
        json.dump(conversation, f)
    return jsonify({"status": "reset"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
