"""GCP bucket sync — port of util/Gcp.groovy."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from google.cloud import storage
from util.logging import log_out, log_err


def _get_project() -> str | None:
    """Return GCP project ID from env var or ADC credentials file."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if project:
        return project
    # Fall back to quota_project_id in the ADC credentials file
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc.exists():
        import json
        data = json.loads(adc.read_text())
        return data.get("quota_project_id")
    return None


def _get_client() -> storage.Client:
    return storage.Client(project=_get_project())


def _get_bucket() -> storage.Bucket:
    bucket_name = os.environ.get("GCP_BUCKET")
    if not bucket_name:
        raise EnvironmentError("GCP_BUCKET environment variable is not set")
    client = _get_client()
    return client.bucket(bucket_name)


def folder_cleanup(g_dir: str, f_dir: str, pattern: str, clobber: bool = True):
    """Download files from GCP bucket subfolder g_dir to local folder f_dir,
    filtering by regex pattern."""
    bucket = _get_bucket()
    local_path = Path(f_dir)
    local_path.mkdir(parents=True, exist_ok=True)

    blobs = bucket.list_blobs(prefix=g_dir)
    regex = re.compile(pattern)

    for blob in blobs:
        filename = Path(blob.name).name
        if not regex.search(filename):
            continue
        dest = local_path / filename
        if dest.exists() and not clobber:
            continue
        blob.download_to_filename(str(dest))


def _copy_blob(blob, tgt: str, clobber: bool):
    filename = Path(blob.name).name
    dest = Path(tgt) / filename
    if dest.exists() and not clobber:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(dest))


def gcp_cp_dir_recurse(src: str, path_map: dict, clobber: bool = True, multithreaded: bool = True):
    """Recursively copy GCP bucket prefix src, routing blobs via path_map.

    path_map maps the first path segment after src to a local directory, e.g.:
      {"model": "/home/user/cwva/metacontent/model",
       "vocab": "/home/user/cwva/metacontent/vocab",
       "data":  "/home/user/cwva/content/data",
       "tags":  "/home/user/cwva/content/tags"}

    A blob named 'ttl/model/cwva.ttl' with src='ttl' lands at
    path_map['model'] + '/cwva.ttl'. Blobs whose segment is not in path_map
    are skipped. Zero-byte blobs (GCS directory markers) are skipped.
    """
    bucket = _get_bucket()
    blobs = list(bucket.list_blobs(prefix=src))
    prefix = src.rstrip("/") + "/"

    def _dest(blob) -> Path | None:
        if not blob.name.startswith(prefix):
            return None
        rel = blob.name[len(prefix):]
        parts = rel.split("/", 1)
        if len(parts) != 2 or not parts[1]:
            return None  # top-level or directory marker
        segment, filename = parts
        if segment not in path_map:
            log_out(f"[sync] skipping unknown segment: {blob.name}")
            return None
        return Path(path_map[segment]) / filename

    def _should_download(dest: Path) -> bool:
        if not dest.exists():
            log_out(f"[sync] new: {dest}")
            return True
        if clobber:
            log_out(f"[sync] overwrite: {dest}")
            return True
        log_out(f"[sync] skip (exists, clobber=false): {dest}")
        return False

    if multithreaded:
        with ThreadPoolExecutor() as executor:
            for blob in blobs:
                if blob.size == 0:
                    continue
                dest = _dest(blob)
                if dest and _should_download(dest):
                    executor.submit(_download_blob, blob, str(dest))
    else:
        for blob in blobs:
            if blob.size == 0:
                continue
            dest = _dest(blob)
            if dest and _should_download(dest):
                _download_blob(blob, str(dest))


def _download_blob(blob, dest_path: str):
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(dest_path)


def evict_stale_images(local_dir: str, thumbnails_dir: str = None,
                       bucket_prefix: str = "images") -> int:
    """Delete local images older than their GCS counterpart.

    Fetches all blob metadata in one list call, then compares mtime locally.
    If thumbnails_dir is given, the matching thumbnail is also deleted.
    Returns the number of full images deleted.
    """
    local_path = Path(local_dir)
    if not local_path.exists():
        return 0
    local_files = [f for f in local_path.rglob("*") if f.is_file()]
    if not local_files:
        return 0

    bucket = _get_bucket()
    blobs = {b.name: b for b in bucket.list_blobs(prefix=bucket_prefix + "/")}

    deleted = 0
    for local_file in local_files:
        rel = local_file.relative_to(local_path)
        blob = blobs.get(f"{bucket_prefix}/{rel}")
        if blob and blob.updated:
            local_mtime = datetime.fromtimestamp(local_file.stat().st_mtime, tz=timezone.utc)
            if blob.updated > local_mtime:
                local_file.unlink()
                deleted += 1
                if thumbnails_dir:
                    thumb = Path(thumbnails_dir) / rel
                    if thumb.exists():
                        thumb.unlink()
    return deleted


_RASTER_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_THUMBNAIL_MAX_WIDTH = 700  # 2× the 336 px gallery display width


def fetch_thumbnail(filename: str, images_dir: str, thumbnails_dir: str) -> "Path | None":
    """Return a thumbnail Path for filename, creating it if necessary.

    Full image is downloaded to images_dir first (via fetch_image), then
    resized to at most _THUMBNAIL_MAX_WIDTH px wide and saved to thumbnails_dir.
    For non-raster files (glb, usdz, ico) the full image path is returned directly.
    Returns None if the source image cannot be obtained.
    """
    thumb_path = Path(thumbnails_dir) / filename
    if thumb_path.exists():
        return thumb_path

    suffix = Path(filename).suffix.lower()
    full_path = fetch_image(filename, images_dir)
    if full_path is None:
        return None

    if suffix not in _RASTER_SUFFIXES:
        return full_path

    try:
        from PIL import Image
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(full_path) as img:
            if img.width > _THUMBNAIL_MAX_WIDTH:
                ratio = _THUMBNAIL_MAX_WIDTH / img.width
                new_size = (_THUMBNAIL_MAX_WIDTH, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            if img.mode in ("RGBA", "P") and suffix in (".jpg", ".jpeg"):
                img = img.convert("RGB")
            img.save(str(thumb_path))
        log_out(f"[thumbnail] created {filename}")
        return thumb_path
    except Exception as e:
        log_err(f"[thumbnail] failed for {filename}: {e}")
        return full_path  # fall back to full-res


def fetch_document(filename: str, local_dir: str, bucket_prefix: str = "documents") -> "Path | None":
    """Return the local path for a document, downloading from GCS if not cached."""
    local_path = Path(local_dir) / filename
    if local_path.exists():
        return local_path
    try:
        bucket = _get_bucket()
        blob = bucket.blob(f"{bucket_prefix}/{filename}")
        blob.reload()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_path))
        log_out(f"[document] fetched {filename} from bucket")
        return local_path
    except Exception as e:
        log_err(f"[document] fetch failed for {filename}: {e}")
        return None


def fetch_image(filename: str, local_dir: str, bucket_prefix: str = "images") -> "Path | None":
    """Return the local path for filename, downloading from GCS if not cached.

    filename may include subdirectory components (e.g. 'sub/foo.jpg').
    Returns None if the blob does not exist in the bucket.
    """
    local_path = Path(local_dir) / filename
    if local_path.exists():
        return local_path

    try:
        bucket = _get_bucket()
        blob = bucket.blob(f"{bucket_prefix}/{filename}")
        blob.reload()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_path))
        log_out(f"[image] fetched {filename} from bucket")
        return local_path
    except Exception as e:
        log_err(f"[image] fetch failed for {filename}: {e}")
        return None


def push_metrics(metrics_dict: dict, bucket_name: str, started_at: str):
    """Upload a metrics snapshot JSON to gs://<bucket>/stats/metrics-<timestamp>.json."""
    import json
    try:
        client = storage.Client(project=_get_project())
        bucket = client.bucket(bucket_name)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        filename = f"stats/metrics-{timestamp}.json"
        payload = {
            "started": started_at,
            "stopped": datetime.now().isoformat(),
            "metrics": metrics_dict,
        }
        blob = bucket.blob(filename)
        blob.upload_from_string(json.dumps(payload, indent=2), content_type="application/json")
        log_out(f"Metrics snapshot written to gs://{bucket_name}/{filename}")
    except Exception as e:
        log_err(f"Failed to write metrics snapshot to GCP: {e}")
