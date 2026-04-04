import sqlite3
import uuid
import json
import feedparser
import re
from urllib.parse import urlparse
from datetime import datetime
from groq import Groq
from tavily import TavilyClient
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
import os
from dotenv import load_dotenv
import logging
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# ENV (NEW - replaces st.secrets)
# ============================================
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

# ============================================
# CACHE (IN-MEMORY)
# ============================================
fact_check_cache = {}

# ============================================
# CONFIG (UNCHANGED)
# ============================================
MODEL = "llama-3.3-70b-versatile"
MAX_SOURCES = 40
TRUSTED_NEWS = [
    "reuters.com","bbc.com","apnews.com","nytimes.com",
    "theguardian.com","aljazeera.com","dw.com",
    "hindustantimes.com","washingtonpost.com","bloomberg.com",
    "france24.com","npr.org","wsj.com","cnbc.com"
]
NEWS_FEEDS = {
    "World":["http://feeds.bbci.co.uk/news/world/rss.xml","https://rss.nytimes.com/services/xml/rss/nyt/World.xml"],
    "Science":["http://feeds.bbci.co.uk/news/science_and_environment/rss.xml"],
    "Technology":["http://feeds.bbci.co.uk/news/technology/rss.xml"],
    "Health":["http://feeds.bbci.co.uk/news/health/rss.xml"],
    "Economy":["https://www.cnbc.com/id/100003114/device/rss/rss.html"]
}

# ============================================
# DATABASE (UNCHANGED)
# ============================================
class TruthDB:
    def __init__(self):
        self.conn = sqlite3.connect("truthgpt.db", check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, title TEXT, pinned INTEGER DEFAULT 0)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, message TEXT)")
        self.conn.commit()

    def create_chat(self):
        cid = str(uuid.uuid4())[:8]
        self.conn.execute("INSERT INTO chats(id,title,pinned) VALUES(?,?,?)", (cid,"New Chat",0))
        self.conn.commit()
        return cid

    def update_title(self,cid,title):
        clean = title[:50] + "..." if len(title)>50 else title
        self.conn.execute("UPDATE chats SET title=? WHERE id=?", (clean,cid))
        self.conn.commit()

    def toggle_pin(self,cid):
        row=self.conn.execute("SELECT pinned FROM chats WHERE id=?",(cid,)).fetchone()
        new_val=0 if row[0] else 1
        self.conn.execute("UPDATE chats SET pinned=? WHERE id=?", (new_val,cid))
        self.conn.commit()

    def delete_chat(self,cid):
        self.conn.execute("DELETE FROM chats WHERE id=?",(cid,))
        self.conn.execute("DELETE FROM messages WHERE chat_id=?",(cid,))
        self.conn.commit()

    def get_chats(self):
        return self.conn.execute("SELECT id,title,pinned FROM chats ORDER BY pinned DESC,rowid DESC").fetchall()

    def save_message(self,cid,role,msg):
        self.conn.execute("INSERT INTO messages(chat_id,role,message) VALUES(?,?,?)", (cid,role,msg))
        self.conn.commit()

    def load_messages(self,cid):
        return self.conn.execute("SELECT role,message FROM messages WHERE chat_id=? ORDER BY rowid", (cid,)).fetchall()

# ============================================
# UTILITIES (UNCHANGED)
# ============================================
def get_groq():
    return groq_client

def get_tavily():
    return tavily_client

def trusted_domain(url):
    try:
        domain=urlparse(url).netloc.lower()
        return any(d in domain for d in TRUSTED_NEWS)
    except: return False

def safe_json(text):
    try: return json.loads(text)
    except:
        try:
            text=re.search(r"\{.*\}",text,re.S).group()
            return json.loads(text)
        except: return None

def extract_claims(text):
    parts=re.split(r'[.!?\n]',text)
    claims=[p.strip() for p in parts if len(p.strip())>10]
    return claims if claims else [text]

def relevant_source(content,claim):
    claim_words=set(claim.lower().split())
    text=set(content.lower().split())
    overlap=len(claim_words & text)
    return overlap>=1

def get_breaking_news():
    headlines=[]
    for category,feeds in NEWS_FEEDS.items():
        for feed in feeds:
            try:
                data=feedparser.parse(feed)
                for entry in data.entries[:3]:
                    headlines.append({"title":entry.title, "category":category})
            except: continue
    return headlines[:20]

# ============================================
# SEARCH (UNCHANGED)
# ============================================
def search_sources(claim):
    client=get_tavily()
    queries=[claim, f"{claim} news", f"fact check {claim}"]
    sources=[]; seen=set()

    for q in queries:
        try:
            r=client.search(query=q,max_results=10)
            for res in r.get("results",[]):
                url=res.get("url","")
                content=res.get("content","")

                if not trusted_domain(url) or not relevant_source(content,claim) or url in seen:
                    continue

                seen.add(url)
                sources.append({
                    "title":res.get("title",""),
                    "url":url,
                    "content":content
                })
        except: continue

    return sources[:MAX_SOURCES]

# ============================================
# CLUSTER (UNCHANGED)
# ============================================
def cluster_sources(sources):
    if len(sources)<6: return sources
    texts=[s["content"] for s in sources]
    vec=TfidfVectorizer(stop_words="english")
    X=vec.fit_transform(texts)
    km=KMeans(n_clusters=4,random_state=42, n_init=10)
    labels=km.fit_predict(X)
    grouped={}
    for label,src in zip(labels,sources):
        grouped.setdefault(label,[]).append(src)
    clustered=[]
    for items in grouped.values():
        combined=" ".join([i["content"] for i in items])[:2000]
        clustered.append({"title":items[0]["title"], "url":items[0]["url"], "content":combined})
    return clustered

# ============================================
# FACT CHECK (RELIABLE - NEVER RETURNS NONE)
# ============================================
def fact_check(claim: str) -> dict:
    """
    Fact-check a claim and return structured response.
    NEVER returns None. Always returns valid dict with verdict, explanation, sources.
    """
    if not claim or not isinstance(claim, str):
        return get_fallback_response(claim, "UNCERTAIN", "Invalid claim format")
    
    claim = claim.strip()[:1000]  # Limit length
    
    # Check cache first
    cache_key = hashlib.md5(claim.lower().encode()).hexdigest()
    if cache_key in fact_check_cache:
        logger.info(f"Cache hit for: {claim[:50]}")
        return fact_check_cache[cache_key]
    
    try:
        # Search for sources
        sources = search_sources(claim)
        
        # If no sources found, return uncertain verdict
        if not sources:
            logger.warning(f"No sources found for: {claim[:50]}")
            result = get_fallback_response(claim, "UNCERTAIN", 
                "No reliable sources found to verify this claim.")
            fact_check_cache[cache_key] = result
            return result
        
        clustered = cluster_sources(sources)
        context = ""
        for i, s in enumerate(clustered[:8], 1):
            context += f"\nSOURCE {i}\n{s['title']}\n{s['content'][:500]}\n"

        prompt = f"""
    Analyze the claim using the provided evidence. 
    Be extremely strict. If the evidence says a claim is false or there is no evidence, mark it FALSE.

    CLAIM: {claim}
    EVIDENCE: {context}

    Return JSON with:
    - verdict: "TRUE", "FALSE", "MISLEADING", or "UNCERTAIN"
    - explanation: A detailed summary (2-3 sentences)
    - highlights: 3-4 bullet points as a list
    - quotes: List of direct snippets from sources (max 2-3)
    """
        
        try:
            r = get_groq().chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "You are a fact-checker. Return valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=30
            )
            data = safe_json(r.choices[0].message.content)
            
            # Ensure data is valid
            if not data or not isinstance(data, dict):
                logger.error("Invalid JSON from Groq")
                return get_fallback_response(claim, "UNCERTAIN", 
                    "Unable to analyze claim at this time.")
            
            # Validate required fields
            data["verdict"] = normalize_verdict(data.get("verdict", "UNCERTAIN"))
            data["explanation"] = str(data.get("explanation", "")).strip() or "No explanation available."
            data["sources"] = clustered or []
            data["highlights"] = data.get("highlights", []) or []
            data["quotes"] = data.get("quotes", []) or []
            
            # Cache the result
            fact_check_cache[cache_key] = data
            return data
            
        except Exception as groq_err:
            logger.error(f"Groq API error: {groq_err}")
            return get_fallback_response(claim, "UNCERTAIN", 
                "API error while fact-checking. Please try again.")
    
    except Exception as e:
        logger.error(f"Unexpected error in fact_check: {e}")
        return get_fallback_response(claim, "UNCERTAIN", 
            "An unexpected error occurred. Please try again.")


def get_fallback_response(claim: str, verdict: str, explanation: str) -> dict:
    """Return a safe fallback response when fact-check fails"""
    return {
        "verdict": normalize_verdict(verdict),
        "explanation": explanation,
        "sources": [],
        "highlights": [],
        "quotes": [],
        "fallback": True
    }


def normalize_verdict(verdict: str) -> str:
    """Normalize verdict to standard format"""
    if not isinstance(verdict, str):
        return "UNCERTAIN"
    
    verdict = verdict.strip().upper()
    valid = ["TRUE", "FALSE", "MISLEADING", "UNCERTAIN"]
    
    if verdict in valid:
        return verdict
    if "TRUE" in verdict and "FALSE" not in verdict:
        return "TRUE"
    if "FALSE" in verdict:
        return "FALSE"
    if "MISLEAD" in verdict:
        return "MISLEADING"
    return "UNCERTAIN"

