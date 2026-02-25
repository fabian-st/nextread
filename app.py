"""
nextread – Google Reader API → Nextcloud News translation service.

Clients speak the (Google/FeedHQ) Reader API; this service translates those
requests into Nextcloud News API (v1-3) calls and converts the responses back.

Authentication flow
-------------------
1. The client POSTs credentials to /accounts/ClientLogin.
   The service tries those credentials against the configured Nextcloud server.
   On success it returns an Auth token (Base-64 encoded "<user>:<password>@<host>").
2. Every subsequent request carries the token in the Authorization header
   ("GoogleLogin auth=<token>") or as a query parameter ("T=<token>").
3. The /reader/api/0/token endpoint returns a short-lived CSRF token (we reuse
   the auth token to keep the implementation stateless).

Configuration (environment variables)
--------------------------------------
NEXTCLOUD_URL   – Base URL of the Nextcloud instance, e.g. https://cloud.example.com
                  When set, all clients must use this server regardless of what
                  they send at login time.  Leave unset to derive the server from
                  the Auth token (useful for self-hosted setups where each user
                  supplies their own server).
"""

import base64
import json
import os
import time
from functools import wraps

import requests
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NEXTCLOUD_URL_ENV = "NEXTCLOUD_URL"


def _encode_token(username: str, password: str, host: str) -> str:
    """Encode credentials into a stateless auth token."""
    raw = f"{username}:{password}@{host}"
    return base64.b64encode(raw.encode()).decode()


def _decode_token(token: str):
    """Return (username, password, host) from an auth token or None."""
    try:
        raw = base64.b64decode(token.encode()).decode()
        at = raw.rfind("@")
        if at == -1:
            return None
        host = raw[at + 1:]
        creds = raw[:at]
        colon = creds.find(":")
        if colon == -1:
            return None
        username = creds[:colon]
        password = creds[colon + 1:]
        return username, password, host
    except Exception:
        return None


def _get_auth_token():
    """Extract the auth token from the request (header or query string)."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("GoogleLogin auth="):
        return auth_header[len("GoogleLogin auth="):]
    return request.args.get("T") or request.form.get("T")


def _nextcloud_credentials():
    """
    Return (base_url, username, password) for the current request or None.

    The base_url may be overridden by the NEXTCLOUD_URL environment variable.
    """
    token = _get_auth_token()
    if not token:
        return None
    decoded = _decode_token(token)
    if not decoded:
        return None
    username, password, host = decoded
    base_url = os.environ.get(NEXTCLOUD_URL_ENV, host).rstrip("/")
    return base_url, username, password


def _nc_get(path: str, params: dict = None):
    """Perform an authenticated GET against the Nextcloud News API."""
    creds = _nextcloud_credentials()
    if not creds:
        return None, 401
    base_url, username, password = creds
    url = f"{base_url}/index.php/apps/news/api/v1-3{path}"
    resp = requests.get(url, params=params, auth=(username, password), timeout=30)
    return resp, resp.status_code


def _nc_post(path: str, data: dict = None):
    """Perform an authenticated POST against the Nextcloud News API."""
    creds = _nextcloud_credentials()
    if not creds:
        return None, 401
    base_url, username, password = creds
    url = f"{base_url}/index.php/apps/news/api/v1-3{path}"
    resp = requests.post(url, json=data, auth=(username, password), timeout=30)
    return resp, resp.status_code


def _nc_put(path: str, data: dict = None):
    """Perform an authenticated PUT against the Nextcloud News API."""
    creds = _nextcloud_credentials()
    if not creds:
        return None, 401
    base_url, username, password = creds
    url = f"{base_url}/index.php/apps/news/api/v1-3{path}"
    resp = requests.put(url, json=data, auth=(username, password), timeout=30)
    return resp, resp.status_code


def _nc_delete(path: str):
    """Perform an authenticated DELETE against the Nextcloud News API."""
    creds = _nextcloud_credentials()
    if not creds:
        return None, 401
    base_url, username, password = creds
    url = f"{base_url}/index.php/apps/news/api/v1-3{path}"
    resp = requests.delete(url, auth=(username, password), timeout=30)
    return resp, resp.status_code


def require_auth(f):
    """Decorator that returns 401 when no valid auth token is present."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _nextcloud_credentials():
            return make_response("Unauthorized", 401)
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Item ID conversion helpers
#
# Google Reader item IDs look like:
#   tag:google.com,2005:reader/item/<hex16>
# or as a signed 64-bit decimal integer.
# We store Nextcloud item IDs directly as integers and encode/decode them.
# ---------------------------------------------------------------------------

def _nc_id_to_reader_id(nc_id: int) -> str:
    """Convert a Nextcloud integer item ID to the Reader long-form tag URI."""
    hex_id = format(nc_id & 0xFFFFFFFFFFFFFFFF, "016x")
    return f"tag:google.com,2005:reader/item/{hex_id}"


def _reader_id_to_nc_id(reader_id: str) -> int:
    """Convert a Reader item ID (tag URI or decimal/hex string) to an integer."""
    reader_id = reader_id.strip()
    if reader_id.startswith("tag:google.com,2005:reader/item/"):
        hex_part = reader_id.split("/")[-1]
        return int(hex_part, 16)
    # plain decimal or hex integer
    if reader_id.startswith("0x") or reader_id.startswith("0X"):
        return int(reader_id, 16)
    return int(reader_id)


# ---------------------------------------------------------------------------
# Stream-ID parsing helpers
# ---------------------------------------------------------------------------

def _parse_stream_id(stream_id: str):
    """
    Return a dict describing the stream:
        {"type": "reading-list"}
        {"type": "starred"}
        {"type": "read"}
        {"type": "feed", "url": <feed_url>}
        {"type": "label", "name": <label_name>}
    """
    stream_id = stream_id.strip()
    if stream_id in (
        "user/-/state/com.google/reading-list",
        "user/-/state/com.google/broadcast-friends",
    ):
        return {"type": "reading-list"}
    if stream_id == "user/-/state/com.google/starred":
        return {"type": "starred"}
    if stream_id == "user/-/state/com.google/read":
        return {"type": "read"}
    if stream_id.startswith("feed/"):
        return {"type": "feed", "url": stream_id[5:]}
    if stream_id.startswith("user/-/label/"):
        return {"type": "label", "name": stream_id[len("user/-/label/"):]}
    return {"type": "reading-list"}


# ---------------------------------------------------------------------------
# Response formatting helpers
# ---------------------------------------------------------------------------

def _format_item(item: dict, feeds_by_id: dict) -> dict:
    """Convert a Nextcloud News item dict into a Reader API item dict."""
    nc_id = item.get("id", 0)
    feed_id = item.get("feedId", 0)
    feed = feeds_by_id.get(feed_id, {})
    feed_url = feed.get("url", "")
    feed_title = feed.get("title", "")

    is_read = item.get("unread") is False
    is_starred = item.get("starred", False)

    categories = [
        "user/-/state/com.google/reading-list",
    ]
    if is_read:
        categories.append("user/-/state/com.google/read")
    if is_starred:
        categories.append("user/-/state/com.google/starred")
    if feed.get("folderId"):
        # we can add label category but we'd need folder name
        pass

    author = item.get("author", "")
    pub_date = item.get("pubDate", int(time.time()))

    return {
        "id": _nc_id_to_reader_id(nc_id),
        "crawlTimeMsec": str(pub_date * 1000),
        "timestampUsec": str(pub_date * 1_000_000),
        "published": pub_date,
        "title": item.get("title", ""),
        "canonical": [{"href": item.get("url", "")}],
        "alternate": [{"href": item.get("url", ""), "type": "text/html"}],
        "categories": categories,
        "origin": {
            "streamId": f"feed/{feed_url}",
            "title": feed_title,
            "htmlUrl": feed.get("link", feed_url),
        },
        "summary": {"content": item.get("body", "")},
        "author": author,
    }


def _build_feeds_by_id(feeds: list) -> dict:
    """Return a dict mapping feed ID to feed dict."""
    return {f["id"]: f for f in feeds}


def _format_subscription(feed: dict, folders_by_id: dict) -> dict:
    """Convert a Nextcloud News feed to a Reader API subscription entry."""
    folder_id = feed.get("folderId")
    categories = []
    if folder_id and folder_id in folders_by_id:
        folder_name = folders_by_id[folder_id]["name"]
        categories.append({
            "id": f"user/-/label/{folder_name}",
            "label": folder_name,
        })
    return {
        "id": f"feed/{feed.get('url', '')}",
        "title": feed.get("title", ""),
        "categories": categories,
        "sortid": str(feed.get("id", "")),
        "firstitemmsec": str(feed.get("added", 0) * 1000),
        "htmlUrl": feed.get("link", feed.get("url", "")),
        "iconUrl": feed.get("faviconLink", ""),
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@app.route("/accounts/ClientLogin", methods=["POST"])
def client_login():
    """
    Authenticate a client and return auth tokens.

    Expected POST fields:
        Email    – Nextcloud username
        Passwd   – Nextcloud password or app-password
        accountType, service, source – ignored

    The Nextcloud server URL is taken from the NEXTCLOUD_URL environment
    variable.  If that is not set the client must supply it via the
    ``HostUrl`` POST field (non-standard extension).
    """
    email = request.form.get("Email", "")
    passwd = request.form.get("Passwd", "")
    host_url = request.form.get("HostUrl", "").rstrip("/")

    base_url = os.environ.get(NEXTCLOUD_URL_ENV, host_url).rstrip("/")
    if not base_url:
        return make_response(
            "Error=BadAuthentication\nInfo=NEXTCLOUD_URL not configured\n",
            400,
        )

    # Verify credentials against Nextcloud
    check_url = f"{base_url}/index.php/apps/news/api/v1-3/feeds"
    try:
        resp = requests.get(check_url, auth=(email, passwd), timeout=15)
    except requests.RequestException as exc:
        return make_response(f"Error=BadAuthentication\nInfo={exc}\n", 403)

    if resp.status_code == 401:
        return make_response("Error=BadAuthentication\n", 403)
    if not resp.ok:
        return make_response(
            f"Error=BadAuthentication\nInfo=Nextcloud returned {resp.status_code}\n",
            403,
        )

    token = _encode_token(email, passwd, base_url)
    body = f"SID={token}\nLSID={token}\nAuth={token}\n"
    return make_response(body, 200, {"Content-Type": "text/plain"})


# ---------------------------------------------------------------------------
# Token (CSRF)
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/token", methods=["GET"])
@require_auth
def get_token():
    """Return a CSRF token.  We reuse the auth token to stay stateless."""
    token = _get_auth_token()
    return make_response(token, 200, {"Content-Type": "text/plain"})


# ---------------------------------------------------------------------------
# User info
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/user-info", methods=["GET"])
@require_auth
def user_info():
    creds = _nextcloud_credentials()
    _, username, _ = creds
    output_fmt = request.args.get("output", "json")
    data = {
        "userId": username,
        "userName": username,
        "userProfileId": username,
        "userEmail": "",
        "isBloggerUser": False,
        "signupTimeSec": 0,
        "publicUserName": username,
        "isMultiLoginEnabled": False,
    }
    if output_fmt == "json":
        return jsonify(data)
    # atom/xml not implemented – fall back to JSON
    return jsonify(data)


# ---------------------------------------------------------------------------
# Subscription list
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/subscription/list", methods=["GET"])
@require_auth
def subscription_list():
    feeds_resp, status = _nc_get("/feeds")
    if status != 200:
        return make_response("Error fetching feeds", status)
    folders_resp, f_status = _nc_get("/folders")
    if f_status != 200:
        return make_response("Error fetching folders", f_status)

    feeds = feeds_resp.json().get("feeds", [])
    folders = folders_resp.json().get("folders", [])
    folders_by_id = {f["id"]: f for f in folders}

    subscriptions = [_format_subscription(f, folders_by_id) for f in feeds]
    return jsonify({"subscriptions": subscriptions})


# ---------------------------------------------------------------------------
# Subscription edit (subscribe / unsubscribe / rename / set-label)
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/subscription/edit", methods=["POST"])
@require_auth
def subscription_edit():
    action = request.form.get("ac", "")
    stream_id = request.form.get("s", "")
    title = request.form.get("t", "")
    add_label = request.form.get("a", "")
    remove_label = request.form.get("r", "")

    parsed = _parse_stream_id(stream_id)
    feed_url = parsed.get("url", "")

    if action == "subscribe":
        # Find or create folder from the label
        folder_id = None
        if add_label and add_label.startswith("user/-/label/"):
            label_name = add_label[len("user/-/label/"):]
            folders_resp, _ = _nc_get("/folders")
            folders = folders_resp.json().get("folders", []) if folders_resp else []
            for folder in folders:
                if folder["name"] == label_name:
                    folder_id = folder["id"]
                    break
            if folder_id is None:
                cr, _ = _nc_post("/folders", {"name": label_name})
                if cr and cr.ok:
                    folder_id = cr.json().get("folders", [{}])[0].get("id")

        payload = {"url": feed_url}
        if folder_id:
            payload["folderId"] = folder_id
        if title:
            payload["title"] = title
        _nc_post("/feeds", payload)
        return make_response("OK", 200)

    if action == "unsubscribe":
        # Find the feed by URL
        feeds_resp, _ = _nc_get("/feeds")
        feeds = feeds_resp.json().get("feeds", []) if feeds_resp else []
        for feed in feeds:
            if feed.get("url") == feed_url:
                _nc_delete(f"/feeds/{feed['id']}")
                break
        return make_response("OK", 200)

    if action == "edit":
        feeds_resp, _ = _nc_get("/feeds")
        feeds = feeds_resp.json().get("feeds", []) if feeds_resp else []
        for feed in feeds:
            if feed.get("url") == feed_url:
                feed_id = feed["id"]
                if title:
                    _nc_put(f"/feeds/{feed_id}/rename", {"feedTitle": title})
                if add_label and add_label.startswith("user/-/label/"):
                    label_name = add_label[len("user/-/label/"):]
                    folders_resp, _ = _nc_get("/folders")
                    folders = folders_resp.json().get("folders", []) if folders_resp else []
                    for folder in folders:
                        if folder["name"] == label_name:
                            _nc_put(f"/feeds/{feed_id}/move", {"folderId": folder["id"]})
                            break
                if remove_label:
                    _nc_put(f"/feeds/{feed_id}/move", {"folderId": 0})
                break
        return make_response("OK", 200)

    return make_response("OK", 200)


# ---------------------------------------------------------------------------
# Tag list (folders + special tags)
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/tag/list", methods=["GET"])
@require_auth
def tag_list():
    folders_resp, status = _nc_get("/folders")
    if status != 200:
        return make_response("Error fetching folders", status)

    folders = folders_resp.json().get("folders", [])
    tags = [
        {"id": "user/-/state/com.google/starred", "sortid": "0000"},
        {"id": "user/-/state/com.google/read", "sortid": "0001"},
        {"id": "user/-/state/com.google/reading-list", "sortid": "0002"},
    ]
    for folder in folders:
        tags.append({
            "id": f"user/-/label/{folder['name']}",
            "sortid": str(folder["id"]).zfill(8),
        })
    return jsonify({"tags": tags})


# ---------------------------------------------------------------------------
# Stream contents
# ---------------------------------------------------------------------------

def _get_items_for_stream(stream_id: str, count: int, start_index: int,
                           exclude_target: str, older_first: bool):
    """
    Fetch items from Nextcloud News matching *stream_id*.

    Returns a list of (item_dict, feeds_by_id) or raises on error.
    """
    parsed = _parse_stream_id(stream_id)
    stype = parsed["type"]

    feeds_resp, f_status = _nc_get("/feeds")
    if f_status != 200:
        return None, None, f_status
    feeds = feeds_resp.json().get("feeds", [])
    feeds_by_id = _build_feeds_by_id(feeds)

    # Build Nextcloud query parameters
    params = {
        "batchSize": count,
        "offset": 0,  # NC News uses last-seen ID, not offset; we simplify
        "type": 3,    # 3=all; overridden below
        "id": 0,
        "getRead": "true" if stype == "read" else "false",
        "oldestFirst": "true" if older_first else "false",
    }

    if stype == "reading-list":
        params["type"] = 3  # all feeds
        params["getRead"] = "true"
    elif stype == "starred":
        params["type"] = 2  # starred
        params["getRead"] = "true"
    elif stype == "read":
        params["type"] = 3
        params["getRead"] = "true"
    elif stype == "feed":
        feed_url = parsed["url"]
        feed_id = next(
            (f["id"] for f in feeds if f.get("url") == feed_url), None
        )
        if feed_id is None:
            return [], feeds_by_id, 200
        params["type"] = 0  # single feed
        params["id"] = feed_id
        params["getRead"] = "true"
    elif stype == "label":
        label_name = parsed["name"]
        folders_resp, _ = _nc_get("/folders")
        folders = folders_resp.json().get("folders", []) if folders_resp else []
        folder_id = next(
            (f["id"] for f in folders if f.get("name") == label_name), None
        )
        if folder_id is None:
            return [], feeds_by_id, 200
        params["type"] = 1  # folder
        params["id"] = folder_id
        params["getRead"] = "true"

    # Apply exclude filter for starred stream
    if exclude_target == "user/-/state/com.google/read":
        params["getRead"] = "false"

    items_resp, i_status = _nc_get("/items", params=params)
    if i_status != 200:
        return None, None, i_status

    items = items_resp.json().get("items", [])

    # Client-side filter for starred
    if stype == "starred":
        items = [it for it in items if it.get("starred")]
    if stype == "read":
        items = [it for it in items if not it.get("unread")]

    return items, feeds_by_id, 200


@app.route("/reader/api/0/stream/contents/", methods=["GET"])
@app.route("/reader/api/0/stream/contents/<path:stream_id>", methods=["GET"])
@require_auth
def stream_contents(stream_id="user/-/state/com.google/reading-list"):
    count = int(request.args.get("n", 20))
    start_index = int(request.args.get("r", 0))
    exclude_target = request.args.get("xt", "")
    older_first = request.args.get("r", "") == "o"
    output_fmt = request.args.get("output", "json")

    items, feeds_by_id, status = _get_items_for_stream(
        stream_id, count, start_index, exclude_target, older_first
    )
    if items is None:
        return make_response("Error fetching items", status)

    reader_items = [_format_item(it, feeds_by_id) for it in items]

    parsed = _parse_stream_id(stream_id)
    continuation = None
    if len(items) == count:
        # Use the last item ID as the continuation token
        continuation = str(items[-1].get("id", ""))

    data = {
        "id": stream_id,
        "title": stream_id,
        "self": [{"href": request.url}],
        "updated": int(time.time()),
        "items": reader_items,
    }
    if continuation:
        data["continuation"] = continuation

    return jsonify(data)


# ---------------------------------------------------------------------------
# Stream item IDs
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/stream/items/ids", methods=["GET"])
@require_auth
def stream_item_ids():
    stream_id = request.args.get("s", "user/-/state/com.google/reading-list")
    count = int(request.args.get("n", 20))
    exclude_target = request.args.get("xt", "")
    older_first = request.args.get("r", "") == "o"

    items, feeds_by_id, status = _get_items_for_stream(
        stream_id, count, 0, exclude_target, older_first
    )
    if items is None:
        return make_response("Error fetching items", status)

    item_refs = [
        {
            "id": _nc_id_to_reader_id(it["id"]),
            "directStreamIds": [],
            "timestampUsec": str(it.get("pubDate", 0) * 1_000_000),
        }
        for it in items
    ]

    data = {"itemRefs": item_refs}
    if len(items) == count and items:
        data["continuation"] = str(items[-1]["id"])
    return jsonify(data)


# ---------------------------------------------------------------------------
# Stream item contents (by ID)
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/stream/items/contents", methods=["POST", "GET"])
@require_auth
def stream_item_contents():
    if request.method == "POST":
        reader_ids = request.form.getlist("i")
    else:
        reader_ids = request.args.getlist("i")

    feeds_resp, f_status = _nc_get("/feeds")
    if f_status != 200:
        return make_response("Error fetching feeds", f_status)
    feeds = feeds_resp.json().get("feeds", [])
    feeds_by_id = _build_feeds_by_id(feeds)

    reader_items = []
    for reader_id in reader_ids:
        try:
            nc_id = _reader_id_to_nc_id(reader_id)
        except (ValueError, TypeError):
            continue
        # Nextcloud News doesn't have a single-item endpoint; we fetch via
        # the "updated" endpoint filtered by ID as a workaround.
        # We use /items/updated with a lastModified of 0 and filter locally.
        # A lighter approach: try to find the item in a recent all-items fetch.
        # For correctness we query with the item id as the offset.
        resp, status = _nc_get("/items", params={
            "batchSize": 1,
            "offset": nc_id,
            "type": 3,
            "id": 0,
            "getRead": "true",
            "oldestFirst": "false",
        })
        if status == 200:
            for it in resp.json().get("items", []):
                if it.get("id") == nc_id:
                    reader_items.append(_format_item(it, feeds_by_id))

    data = {
        "id": "user/-/state/com.google/reading-list",
        "updated": int(time.time()),
        "items": reader_items,
    }
    return jsonify(data)


# ---------------------------------------------------------------------------
# Mark all as read
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/mark-all-as-read", methods=["POST"])
@require_auth
def mark_all_as_read():
    stream_id = request.form.get("s", "")
    older_than = request.form.get("ts")  # timestamp in microseconds

    parsed = _parse_stream_id(stream_id)
    stype = parsed["type"]

    if stype == "feed":
        feed_url = parsed["url"]
        feeds_resp, _ = _nc_get("/feeds")
        feeds = feeds_resp.json().get("feeds", []) if feeds_resp else []
        for feed in feeds:
            if feed.get("url") == feed_url:
                _nc_put(f"/feeds/{feed['id']}/read")
                break
    elif stype == "label":
        label_name = parsed["name"]
        folders_resp, _ = _nc_get("/folders")
        folders = folders_resp.json().get("folders", []) if folders_resp else []
        for folder in folders:
            if folder.get("name") == label_name:
                _nc_put(f"/folders/{folder['id']}/read")
                break
    else:
        # reading-list → mark all feeds as read
        feeds_resp, _ = _nc_get("/feeds")
        if feeds_resp:
            for feed in feeds_resp.json().get("feeds", []):
                _nc_put(f"/feeds/{feed['id']}/read")

    return make_response("OK", 200)


# ---------------------------------------------------------------------------
# Edit tag (star / unstar / mark read / mark unread)
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/edit-tag", methods=["POST"])
@require_auth
def edit_tag():
    """
    POST fields:
        i   – one or more item IDs
        a   – tag to add (e.g. user/-/state/com.google/starred)
        r   – tag to remove
    """
    reader_ids = request.form.getlist("i")
    add_tag = request.form.get("a", "")
    remove_tag = request.form.get("r", "")

    feeds_resp, f_status = _nc_get("/feeds")
    feeds = feeds_resp.json().get("feeds", []) if feeds_resp and f_status == 200 else []
    feeds_by_id = _build_feeds_by_id(feeds)

    for reader_id in reader_ids:
        try:
            nc_id = _reader_id_to_nc_id(reader_id)
        except (ValueError, TypeError):
            continue

        # Find the feed for this item to get feedId (needed for star)
        feed_id = None
        resp, status = _nc_get("/items", params={
            "batchSize": 1,
            "offset": nc_id,
            "type": 3,
            "id": 0,
            "getRead": "true",
            "oldestFirst": "false",
        })
        if status == 200:
            for it in resp.json().get("items", []):
                if it.get("id") == nc_id:
                    feed_id = it.get("feedId")

        if add_tag == "user/-/state/com.google/starred":
            if feed_id is not None:
                _nc_put(f"/items/{feed_id}/{nc_id}/star")
        elif remove_tag == "user/-/state/com.google/starred":
            if feed_id is not None:
                _nc_put(f"/items/{feed_id}/{nc_id}/unstar")

        if add_tag == "user/-/state/com.google/read":
            _nc_post("/items/read/multiple", {"itemIds": [nc_id]})
        elif remove_tag == "user/-/state/com.google/read":
            _nc_post("/items/unread/multiple", {"itemIds": [nc_id]})

        if add_tag == "user/-/state/com.google/kept-unread":
            _nc_post("/items/unread/multiple", {"itemIds": [nc_id]})

    return make_response("OK", 200)


# ---------------------------------------------------------------------------
# Preference list (stub)
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/preference/list", methods=["GET"])
@require_auth
def preference_list():
    return jsonify({
        "prefs": [
            {"id": "lc-nav-expanded", "value": "false"},
            {"id": "lc-nav-filters", "value": "true"},
            {"id": "lc-nav-pane-state", "value": "false"},
            {"id": "lc-show-global-shared-links", "value": "false"},
            {"id": "lc-show-social", "value": "false"},
            {"id": "lc-start-page", "value": "overview"},
            {"id": "lc-theme", "value": ""},
        ]
    })


# ---------------------------------------------------------------------------
# Unread count
# ---------------------------------------------------------------------------

@app.route("/reader/api/0/unread-count", methods=["GET"])
@require_auth
def unread_count():
    feeds_resp, f_status = _nc_get("/feeds")
    if f_status != 200:
        return make_response("Error fetching feeds", f_status)
    feeds = feeds_resp.json().get("feeds", [])

    total = sum(f.get("unreadCount", 0) for f in feeds)
    counts = [
        {
            "id": "user/-/state/com.google/reading-list",
            "count": total,
            "newestItemTimestampUsec": str(int(time.time()) * 1_000_000),
        }
    ]
    for feed in feeds:
        counts.append({
            "id": f"feed/{feed.get('url', '')}",
            "count": feed.get("unreadCount", 0),
            "newestItemTimestampUsec": str(feed.get("lastModified", 0) * 1_000_000),
        })

    return jsonify({"max": 1000, "unreadcounts": counts})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
