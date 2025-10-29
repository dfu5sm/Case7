import os, re, mimetypes, logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# --- Load environment variables (safe for local dev) ---
load_dotenv()

STORAGE_ACCOUNT_URL = os.getenv("STORAGE_ACCOUNT_URL", "")  # ok if blank locally
CONTAINER_NAME      = os.getenv("IMAGES_CONTAINER", "lanternfly-images")
CONN_STR            = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")

if not STORAGE_ACCOUNT_URL or not CONN_STR:
    print("⚠️  WARNING: Azure env vars not fully set; uploads may fail locally.")

# --- Azure Blob setup ---
bsc = BlobServiceClient.from_connection_string(CONN_STR) if CONN_STR else None
cc  = bsc.get_container_client(CONTAINER_NAME) if bsc else None

# --- Flask setup ---
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB limit
logging.basicConfig(level=logging.INFO)

# --- Helpers ---
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff"}

def _is_image_mt(mt: str) -> bool:
    return bool(mt) and mt.startswith("image/")

def _is_allowed_file(name: str) -> bool:
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def _sanitize(name: str) -> str:
    # secure_filename then extra tighten (keeps extensions)
    name = secure_filename(name or "image")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

def _blob_url(blob_name: str) -> str:
    # Prefer container client's URL if available, otherwise compose from account URL
    if cc is not None and getattr(cc, "url", None):
        return f"{cc.url}/{blob_name}"
    return f"{STORAGE_ACCOUNT_URL.rstrip('/')}/{CONTAINER_NAME}/{blob_name}"

# --- Frontend Route ---
@app.route("/")
def home():
    urls = []
    try:
        if cc:
            urls = [_blob_url(b.name) for b in cc.list_blobs()]
            # newest first because names are prefixed with timestamp
            urls.sort(reverse=True)
    except Exception as e:
        app.logger.error(f"List error: {e}")
    return render_template("index.html", gallery=urls)

# --- Upload Endpoint ---
@app.post("/api/v1/upload")
def upload():
    try:
        if not cc:
            return jsonify(ok=False, error="Storage not configured"), 503

        f = request.files.get("file")
        if not f:
            return jsonify(ok=False, error="missing 'file'"), 400
        if not f.filename:
            return jsonify(ok=False, error="empty filename"), 400
        if not _is_allowed_file(f.filename):
            return jsonify(ok=False, error="invalid file type (must be an image)"), 400

        mt = f.mimetype or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
        if not _is_image_mt(mt):
            return jsonify(ok=False, error=f"not an image (got {mt})"), 415

        blob_name = f"{_ts()}-{_sanitize(f.filename)}"
        # Read the stream so we can set content settings reliably
        data = f.read()

        cc.upload_blob(
            name=blob_name,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=mt),
        )

        url = _blob_url(blob_name)
        app.logger.info(f"Uploaded: {url}")
        return jsonify(ok=True, url=url), 200

    except Exception as e:
        app.logger.error(str(e))
        return jsonify(ok=False, error=str(e)), 500

# --- Gallery Endpoint ---
@app.get("/api/v1/gallery")
def gallery():
    try:
        if not cc:
            return jsonify(ok=False, error="Storage not configured"), 503
        urls = [_blob_url(b.name) for b in cc.list_blobs()]
        urls.sort(reverse=True)
        return jsonify(ok=True, gallery=urls), 200
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# --- Health Check (accepts with/without trailing slash) ---
@app.route("/api/v1/health", methods=["GET"], strict_slashes=False)
def health():
    """Health check endpoint that verifies Azure Blob Storage connectivity."""
    if not cc:
        return jsonify(status="UNHEALTHY", message="Storage client not initialized"), 503
    try:
        # Try to get container properties to verify Azure connection
        cc.get_container_properties()
        return jsonify(status="OK", message="Azure Storage connection successful"), 200
    except Exception as e:
        return jsonify(status="DEGRADED", message=f"Storage connection failed: {e}"), 503

# --- Entry point ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
