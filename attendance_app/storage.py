from io import BytesIO
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import current_app, send_file


def supabase_enabled():
    return bool(
        current_app.config.get("SUPABASE_URL")
        and current_app.config.get("SUPABASE_SERVICE_ROLE_KEY")
        and current_app.config.get("SUPABASE_STORAGE_BUCKET")
    )


def _object_url(object_path):
    base_url = current_app.config["SUPABASE_URL"].rstrip("/")
    bucket = current_app.config["SUPABASE_STORAGE_BUCKET"]
    return f"{base_url}/storage/v1/object/{bucket}/{object_path}"


def _headers(content_type=None):
    service_key = current_app.config["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def upload_file(folder, stored_name, file_storage):
    object_path = f"{folder}/{stored_name}"
    file_storage.stream.seek(0)
    request = Request(
        _object_url(object_path),
        data=file_storage.stream.read(),
        headers={**_headers(file_storage.mimetype or "application/octet-stream"), "x-upsert": "true"},
        method="POST",
    )
    with urlopen(request, timeout=30):
        pass
    return stored_name


def upload_bytes(folder, stored_name, data, content_type="text/plain"):
    object_path = f"{folder}/{stored_name}"
    request = Request(
        _object_url(object_path),
        data=data,
        headers={**_headers(content_type), "x-upsert": "true"},
        method="POST",
    )
    with urlopen(request, timeout=30):
        pass
    return object_path


def storage_status():
    status = {
        "enabled": supabase_enabled(),
        "url_set": bool(current_app.config.get("SUPABASE_URL")),
        "service_key_set": bool(current_app.config.get("SUPABASE_SERVICE_ROLE_KEY")),
        "bucket": current_app.config.get("SUPABASE_STORAGE_BUCKET") or "",
    }
    if not status["enabled"]:
        status["ok"] = False
        status["message"] = "Supabase storage environment variables are incomplete."
        return status

    try:
        test_name = f"diagnostics/{uuid4().hex}.txt"
        upload_bytes("diagnostics", test_name.split("/", 1)[1], b"storage-ok")
        status["ok"] = True
        status["message"] = f"Supabase upload test passed: {test_name}"
    except Exception as exc:
        status["ok"] = False
        status["message"] = f"Supabase upload test failed: {exc}"
    return status


def send_stored_file(folder, stored_name, download_name=None, as_attachment=False):
    object_path = f"{folder}/{stored_name}"
    request = Request(_object_url(object_path), headers=_headers(), method="GET")
    try:
        response = urlopen(request, timeout=30)
    except HTTPError:
        raise
    content = response.read()
    return send_file(
        BytesIO(content),
        mimetype=response.headers.get("content-type") or "application/octet-stream",
        as_attachment=as_attachment,
        download_name=download_name or stored_name,
    )
