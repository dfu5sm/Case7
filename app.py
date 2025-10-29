import os, re, mimetypes, logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()

STORAGE_ACCOUNT_URL = os.getenv("STORAGE_ACCOUNT_URL")
CONTAINER_NAME      = os.getenv("IMAGES_CONTAINER", "lanternfly-images")
CONN_STR            = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

# --- Check that environment variables are set ---
missing = [k for k, v in {
    "STORAGE_ACCOUNT_URL": STORAGE_ACCOUNT_URL,
    "AZURE_STORAGE_CONNECTION_STRING": CONN_STR
}.items() if not v]
if missing:
    raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

# --- Azure Blob setup ---
bsc = BlobServiceClient.from_connection_string(CONN_STR)
cc  = bsc.get_container_client(CONTAINER_NAME)

# --- Flask setup ---
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB limit
logging.basicConfig(level=logging.INFO)


# --- Helpers ---
def _is_image(mt: str) -> bool:
    return mt and mt.startswith("image/")

def _sanitize(name: str) -> str:
    name = os.path.basename(name or "image")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


# --- Frontend Route ---
@app.route("/")
def home():
    """Render the main page with all uploaded images."""
    blobs = cc.list_blobs()
    urls = [f"{STORAGE_ACCOUNT_URL}/{CONTAINER_NAME}/{blob.name}" for blob in blobs]
    return render_template("index.html", gallery=urls)


# --- Upload Endpoint ---
@app.post("/api/v1/upload")
def upload():
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"ok": False, "error": "missing 'file'"}), 400

        mt = f.mimetype or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
        if not _is_image(mt):
            return jsonify({"ok": False, "error": f"not an image (got {mt})"}), 415

        blob_name = f"{_ts()}-{_sanitize(f.filename)}"
        cc.upload_blob(
            name=blob_name,
            data=f.stream,
            overwrite=True,
            content_settings=ContentSettings(content_type=mt)
        )

        url = f"{STORAGE_ACCOUNT_URL}/{CONTAINER_NAME}/{blob_name}"
        app.logger.info(f"Uploaded: {url}")
        return jsonify({"ok": True, "url": url}), 200

    except Exception as e:
        app.logger.error(str(e))
        return jsonify({"ok": False, "error": str(e)}), 500


# --- Gallery Endpoint ---
@app.get("/api/v1/gallery")
def gallery():
    try:
        urls = [
            f"{STORAGE_ACCOUNT_URL}/{CONTAINER_NAME}/{blob.name}"
            for blob in cc.list_blobs()
        ]
        return jsonify({"ok": True, "gallery": urls}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --- Health Check ---
@app.route("/api/v1/health", methods=["GET"], strict_slashes=False)
def health():
    return jsonify({"status": "ok"}), 200

# --- Entry point ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
