"""GCP bucket sync — port of util/Gcp.groovy."""

import contextlib
import os
import re
import threading
import uuid
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

    # Every download's outcome is checked.  Exceptions raised in submitted
    # futures used to be dropped, so a sync where every download failed still
    # logged "[sync] new: ..." for each file and the server came up on an empty
    # store with no error anywhere.
    jobs = []
    for blob in blobs:
        if blob.size == 0:
            continue
        dest = _dest(blob)
        if dest and _should_download(dest):
            jobs.append((blob, dest))

    if multithreaded:
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(_download_blob, blob, str(dest)): blob
                       for blob, dest in jobs}
            outcomes = [(futures[f], f.exception()) for f in futures]
    else:
        outcomes = []
        for blob, dest in jobs:
            try:
                _download_blob(blob, str(dest))
                outcomes.append((blob, None))
            except Exception as e:
                outcomes.append((blob, e))

    failed = [(blob, e) for blob, e in outcomes if e is not None]
    for blob, e in failed:
        log_err(f"[sync] download failed for {blob.name}: {e}")
    if failed:
        log_err(f"[sync] {len(failed)} of {len(jobs)} downloads failed")


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


# Most pixels a thumbnail may decode at once (~150 MB as RGB).  JPEGs are
# decoded at reduced scale first (draft mode, up to 1/8 per side), so even a
# 384 MP ontology poster decodes at ~6 MP; this cap only bites on a huge
# PNG/GIF/WebP, which Pillow can only decode at full size.
_THUMBNAIL_MAX_DECODE_PIXELS = 50_000_000


def thumbnail_is_current(thumb_path: Path, full_path: Path) -> bool:
    """True if thumb_path exists and is at least as new as its source image.

    Catches an image replaced on disk (copy, rsync, "cloud": null
    deployments), which evict_stale_images() never sees: that runs only at
    startup and on refresh, and only for images whose bucket copy is newer.
    A replacement carrying an *older* mtime than the thumbnail (cp -p or rsync
    -a of an older file) is not detected; delete the thumbnail by hand then.
    """
    try:
        return thumb_path.stat().st_mtime >= full_path.stat().st_mtime
    except OSError:  # either file missing
        return False


def make_thumbnail(full_path: Path, thumb_path: Path) -> "Path | None":
    """Resize a local image into thumb_path; no bucket involved.

    Resizes to at most _THUMBNAIL_MAX_WIDTH px wide, and rebuilds a thumbnail
    older than its source. Non-raster files (glb, usdz, ico) have no
    thumbnail, so full_path is returned as-is.

    Returns None if a raster image cannot be thumbnailed.  It used to fall back
    to the original, but the images that fail are the very large ones (200-400
    MP, up to 130 MB on disk), so every gallery view then read the whole file
    into memory and sent it as a "thumbnail" — and nothing was cached, so it
    repeated on every request.
    """
    suffix = full_path.suffix.lower()
    if suffix not in _RASTER_SUFFIXES:
        return full_path
    if thumbnail_is_current(thumb_path, full_path):
        return thumb_path

    # Written under a temporary name and renamed into place, so a concurrent
    # request never serves a half-written file, and two requests rebuilding
    # the same thumbnail never interleave their writes.
    tmp_path = thumb_path.with_name(f".{thumb_path.stem}.{uuid.uuid4().hex}{thumb_path.suffix}")
    try:
        from PIL import Image
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        # Image.open() reads only the header.  Pillow's decompression-bomb
        # guard would refuse anything over ~179 MP at this point, before draft()
        # gets the chance to shrink the decode; the explicit pixel cap below
        # replaces it for this one call.
        with _bomb_guard_lifted():
            img = Image.open(full_path)
        with img:
            if img.width > _THUMBNAIL_MAX_WIDTH:
                target = (_THUMBNAIL_MAX_WIDTH,
                          max(1, round(img.height * _THUMBNAIL_MAX_WIDTH / img.width)))
                # JPEG: decode at the smallest 1/2^n scale still >= target.
                # Other formats ignore this and keep their full size.
                img.draft(None, target)
                if img.width * img.height > _THUMBNAIL_MAX_DECODE_PIXELS:
                    raise ValueError(
                        f"{img.width}x{img.height} {img.format} is too large to "
                        f"decode for a thumbnail")
                img = img.resize(target, Image.LANCZOS)
            if img.mode in ("RGBA", "P") and suffix in (".jpg", ".jpeg"):
                img = img.convert("RGB")
            img.save(str(tmp_path))
        os.replace(tmp_path, thumb_path)
        log_out(f"[thumbnail] created {thumb_path.name}")
        return thumb_path
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        log_err(f"[thumbnail] failed for {full_path.name}: {e}")
        return None


_bomb_lock = threading.Lock()


@contextlib.contextmanager
def _bomb_guard_lifted():
    """Disable Pillow's decompression-bomb check while opening one image.

    MAX_IMAGE_PIXELS is a module global, so the swap is serialized; it covers
    only Image.open() (a header read), never the decode.
    """
    from PIL import Image
    with _bomb_lock:
        saved = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            yield
        finally:
            Image.MAX_IMAGE_PIXELS = saved


def fetch_thumbnail(filename: str, images_dir: str, thumbnails_dir: str) -> "Path | None":
    """Return a thumbnail Path for filename, creating it if necessary.

    Full image is downloaded to images_dir first (via fetch_image), then
    resized by make_thumbnail(). Returns None if the source image cannot be
    obtained or cannot be thumbnailed.
    """
    thumb_path = Path(thumbnails_dir) / filename
    if thumb_path.exists():
        return thumb_path

    full_path = fetch_image(filename, images_dir)
    if full_path is None:
        return None
    return make_thumbnail(full_path, thumb_path)


# (connect, read) seconds for on-demand fetches.  The read timeout is the gap
# allowed between received bytes, not a cap on the whole download; the
# library's retry policy bounds a stalled connection to a few minutes.
_FETCH_TIMEOUT = (10, 60)


def _fetch_to_cache(blob_name: str, local_path: Path):
    """Download blob_name to local_path, which appears only when complete.

    Written under a temporary name and renamed into place.  Downloading
    straight to local_path left a truncated file behind whenever a download
    died part-way (the library deletes it only on NotFound or a checksum
    error): callers treat an existing file as cached, so the broken copy was
    served from then on, and evict_stale_images() never removed it, because
    its mtime was newer than the bucket's.  The rename also keeps a concurrent
    request from serving a file still being written.  The library sets the
    temp file's mtime to the blob's upload time, and os.replace() keeps it.
    """
    blob = _get_bucket().blob(blob_name)
    blob.reload(timeout=_FETCH_TIMEOUT)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = local_path.with_name(f".{local_path.name}.{uuid.uuid4().hex}.part")
    try:
        blob.download_to_filename(str(tmp_path), timeout=_FETCH_TIMEOUT)
        os.replace(tmp_path, local_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def fetch_document(filename: str, local_dir: str, bucket_prefix: str = "documents") -> "Path | None":
    """Return the local path for a document, downloading from GCS if not cached."""
    local_path = Path(local_dir) / filename
    if local_path.exists():
        return local_path
    try:
        _fetch_to_cache(f"{bucket_prefix}/{filename}", local_path)
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
        _fetch_to_cache(f"{bucket_prefix}/{filename}", local_path)
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
