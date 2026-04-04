import os
import logging
import bleach
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

# ===== EXISTING IMPORTS (KEEP YOUR LOGIC) =====
from logic import fact_check, get_breaking_news

# =========================
# INIT
# =========================
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB upload limit

logging.basicConfig(level=logging.INFO)


# =========================
# UTILITIES
# =========================
def sanitize_input(text):
    return bleach.clean(text or "", strip=True)


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return render_template("index.html")


# =========================
# FACT CHECK (TEXT)
# =========================
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        message = sanitize_input(data.get("message", ""))

        if not message:
            return jsonify({"responses": []})

        claims = message.split(".")[:5]

        responses = []
        for claim in claims:
            claim = claim.strip()
            if not claim:
                continue

            result = fact_check(claim)

            responses.append({
                "claim": claim,
                "result": result
            })

        return jsonify({"responses": responses})

    except Exception as e:
        logging.error(f"Chat error: {e}")
        return jsonify({"responses": []}), 500


# =========================
# BREAKING NEWS
# =========================
@app.route("/breaking-news")
def breaking_news():
    try:
        return jsonify(get_breaking_news())
    except Exception as e:
        logging.error(f"Breaking news error: {e}")
        return jsonify([])


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(debug=True)