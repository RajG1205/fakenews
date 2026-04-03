from flask import Flask, request, jsonify, send_from_directory
from logic import extract_claims, fact_check, get_breaking_news
import logging
import os
from bleach import clean
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================
# CACHE: BREAKING NEWS (30-SECOND TTL)
# ============================================
breaking_news_cache = {"data": None, "timestamp": None}

def get_cached_breaking_news():
    """Get breaking news from cache or fetch fresh if expired"""
    now = datetime.now()
    
    # Return cached data if still valid (30 seconds)
    if (breaking_news_cache["data"] is not None and 
        breaking_news_cache["timestamp"] is not None and
        (now - breaking_news_cache["timestamp"]) < timedelta(seconds=30)):
        return breaking_news_cache["data"]
    
    # Fetch fresh data
    try:
        data = get_breaking_news()
        
        # Validate: ensure it's always a list
        if not isinstance(data, list):
            data = []
        
        # Validate each item has required fields
        validated = []
        for item in data:
            if isinstance(item, dict) and "title" in item:
                validated.append({
                    "title": str(item.get("title", "")).strip()[:200],  # Limit length
                    "category": str(item.get("category", "")).strip()[:50],
                    "url": str(item.get("url", "")).strip()[:500]
                })
        
        # Cache the validated data
        breaking_news_cache["data"] = validated
        breaking_news_cache["timestamp"] = now
        
        return validated
        
    except Exception as e:
        logger.error(f"Error fetching breaking news: {e}")
        # Return empty list on any error
        return []

# ============================================
# SECURITY: Input sanitization
# ============================================
def sanitize_input(text: str, max_length: int = 5000) -> str:
    """Sanitize user input to prevent XSS and injection"""
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length]
    # Remove HTML tags and scripts
    text = clean(text, tags=[], strip=True)
    return text

# ============================================
# ROUTES
# ============================================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(".", path)

# ============================================
# API: CHAT / FACT CHECK
# ============================================
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON provided", "responses": []}), 400
        
        message = data.get("message", "")
        
        # Validate and sanitize input
        message = sanitize_input(message)
        if not message:
            return jsonify({"error": "Empty message", "responses": []}), 400
        
        # Extract and fact-check claims
        claims = extract_claims(message)
        responses = []
        
        for claim in claims:
            claim = sanitize_input(claim)
            if not claim:
                continue
            
            result = fact_check(claim)
            
            # fact_check now ALWAYS returns dict, never None
            if result and isinstance(result, dict):
                responses.append({
                    "claim": claim,
                    "result": result
                })
        
        return jsonify({
            "success": len(responses) > 0,
            "responses": responses
        }), 200
    
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        return jsonify({
            "error": "Internal server error",
            "responses": []
        }), 500


# ============================================
# API: BREAKING NEWS (LIVE)
# ============================================
@app.route("/breaking-news", methods=["GET"])
def breaking_news():
    """Return latest breaking news claims"""
    try:
        news = get_cached_breaking_news()
        
        # Ensure we always return a valid JSON array
        if not isinstance(news, list):
            news = []
        
        return jsonify(news), 200
        
    except Exception as e:
        logger.error(f"Breaking news endpoint error: {e}")
        # Always return empty array on error - never crash
        return jsonify([]), 500


# ============================================
# ERROR HANDLERS
# ============================================
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ============================================
# MAIN: DEPLOYMENT READY
# ============================================
if __name__ == "__main__":
    # Get config from environment
    HOST = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT = int(os.getenv("FLASK_PORT", 5000))
    DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    
    logger.info(f"Starting TruthGPT on {HOST}:{PORT} (debug={DEBUG})")
    
    app.run(
        host=HOST,
        port=PORT,
        debug=DEBUG,
        use_reloader=False
    )