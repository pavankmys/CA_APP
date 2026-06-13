"""
Phase B publishing helpers for Audio Notes: Cloudflare R2 (S3-compatible) storage upload
+ podcast RSS feed.

R2 is used instead of Supabase Storage because its free tier (10GB storage, zero egress
fees) comfortably covers a full syllabus of audio episodes, whereas Supabase Storage's
free tier (1GB storage, ~5GB/month egress) would be exhausted after roughly 30 chapters'
worth of episodes.
"""
from datetime import datetime, timedelta, timezone

import boto3
from feedgen.feed import FeedGenerator

FEED_PATH = "feed.xml"


def make_client(account_id, access_key_id, secret_access_key):
    """Returns a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def public_url(public_base_url, storage_path):
    return f"{public_base_url.rstrip('/')}/{storage_path}"


def upload_audio_file(client, bucket, public_base_url, local_path, storage_path):
    """Uploads (or overwrites) an MP3 to R2. Returns its public URL."""
    client.upload_file(local_path, bucket, storage_path, ExtraArgs={"ContentType": "audio/mpeg"})
    return public_url(public_base_url, storage_path)


def upload_text_file(client, bucket, public_base_url, content, storage_path):
    """Uploads (or overwrites) a UTF-8 text file (e.g. markdown notes) to R2. Returns its public URL."""
    client.put_object(Bucket=bucket, Key=storage_path, Body=content.encode("utf-8"), ContentType="text/markdown")
    return public_url(public_base_url, storage_path)


def download_text_file(client, bucket, storage_path):
    """Downloads a UTF-8 text file from R2. Returns its content, or None if it doesn't exist."""
    try:
        obj = client.get_object(Bucket=bucket, Key=storage_path)
    except client.exceptions.NoSuchKey:
        return None
    return obj["Body"].read().decode("utf-8")


def storage_path_from_url(public_base_url, url):
    """Returns the storage path (bucket key) for a public R2 URL, or None if `url` is
    empty or doesn't match `public_base_url`."""
    if not url:
        return None
    prefix = public_base_url.rstrip("/") + "/"
    return url[len(prefix):] if url.startswith(prefix) else None


def upload_feed_xml(client, bucket, public_base_url, xml_bytes, storage_path=FEED_PATH):
    """Uploads (or overwrites) feed.xml to R2. Returns its public URL."""
    client.put_object(Bucket=bucket, Key=storage_path, Body=xml_bytes, ContentType="application/rss+xml")
    return public_url(public_base_url, storage_path)


def build_feed_xml(episodes, feed_url):
    """
    episodes: ordered list of dicts with keys: title, audio_url, duration_seconds,
              file_size_bytes, episode_num, subject, chapter — in the intended listening
              order (first = listen first).
    feed_url: public URL where feed.xml itself will be hosted.

    Returns the RSS XML as bytes. Episodes are given descending pubDates so that
    podcast apps' default "newest first" ordering matches the intended listening order.
    """
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title("CA Inter Audio Notes")
    fg.link(href=feed_url, rel="self")
    fg.description("Commute-friendly audio explainers generated from CA Inter study material.")
    fg.language("en")
    fg.podcast.itunes_category("Education")
    fg.podcast.itunes_explicit("no")

    base_time = datetime.now(timezone.utc)
    for i, ep in enumerate(episodes):
        fe = fg.add_entry()
        fe.id(ep["audio_url"])
        fe.title(f"{ep['subject']} — {ep['chapter']}: {ep['title']}")
        fe.description(f"{ep['subject']} › {ep['chapter']} — Episode {ep['episode_num']}")
        fe.enclosure(ep["audio_url"], str(ep.get("file_size_bytes") or 0), "audio/mpeg")
        fe.published(base_time - timedelta(minutes=i))
        if ep.get("duration_seconds"):
            fe.podcast.itunes_duration(ep["duration_seconds"])

    return fg.rss_str(pretty=True)


def publish_feed(client, bucket, public_base_url, episodes):
    """Builds the RSS feed for `episodes` and uploads it to R2. Returns the feed URL."""
    feed_url = public_url(public_base_url, FEED_PATH)
    xml_bytes = build_feed_xml(episodes, feed_url)
    return upload_feed_xml(client, bucket, public_base_url, xml_bytes)
