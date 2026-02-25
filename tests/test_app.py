"""
Tests for the nextread Google Reader API → Nextcloud News translation service.
"""

import base64
import json
import os

import pytest
import responses as responses_lib

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app, _encode_token, _decode_token, _nc_id_to_reader_id, _reader_id_to_nc_id, _parse_stream_id


NC_BASE = "https://cloud.example.com"
NC_USER = "testuser"
NC_PASS = "testpass"
AUTH_TOKEN = _encode_token(NC_USER, NC_PASS, NC_BASE)

SAMPLE_FEEDS = [
    {"id": 1, "url": "https://example.com/feed.xml", "title": "Example Feed",
     "link": "https://example.com", "folderId": 10, "unreadCount": 3,
     "added": 1700000000, "lastModified": 1700100000, "faviconLink": ""},
    {"id": 2, "url": "https://other.com/rss", "title": "Other Feed",
     "link": "https://other.com", "folderId": None, "unreadCount": 0,
     "added": 1700000000, "lastModified": 1700100000, "faviconLink": ""},
]

SAMPLE_FOLDERS = [
    {"id": 10, "name": "Tech"},
]

SAMPLE_ITEMS = [
    {
        "id": 42,
        "feedId": 1,
        "title": "Test Article",
        "url": "https://example.com/article",
        "body": "<p>Hello</p>",
        "author": "Alice",
        "pubDate": 1700050000,
        "unread": True,
        "starred": False,
    },
    {
        "id": 99,
        "feedId": 2,
        "title": "Starred Article",
        "url": "https://other.com/starred",
        "body": "<p>World</p>",
        "author": "Bob",
        "pubDate": 1700060000,
        "unread": False,
        "starred": True,
    },
]


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def auth_headers():
    return {"Authorization": f"GoogleLogin auth={AUTH_TOKEN}"}


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestEncodeDecodeToken:
    def test_roundtrip(self):
        token = _encode_token("user", "pass", "https://cloud.example.com")
        result = _decode_token(token)
        assert result == ("user", "pass", "https://cloud.example.com")

    def test_invalid_token_returns_none(self):
        assert _decode_token("notbase64!!!") is None

    def test_missing_at_sign(self):
        token = base64.b64encode(b"userpassword").decode()
        assert _decode_token(token) is None

    def test_missing_colon(self):
        token = base64.b64encode(b"userpassword@host").decode()
        assert _decode_token(token) is None


class TestItemIdConversion:
    def test_nc_to_reader_id(self):
        reader_id = _nc_id_to_reader_id(42)
        assert reader_id == "tag:google.com,2005:reader/item/000000000000002a"

    def test_reader_id_roundtrip(self):
        nc_id = 12345
        reader_id = _nc_id_to_reader_id(nc_id)
        assert _reader_id_to_nc_id(reader_id) == nc_id

    def test_decimal_id(self):
        assert _reader_id_to_nc_id("42") == 42

    def test_hex_id(self):
        assert _reader_id_to_nc_id("0x2a") == 42


class TestParseStreamId:
    def test_reading_list(self):
        assert _parse_stream_id("user/-/state/com.google/reading-list") == {"type": "reading-list"}

    def test_starred(self):
        assert _parse_stream_id("user/-/state/com.google/starred") == {"type": "starred"}

    def test_read(self):
        assert _parse_stream_id("user/-/state/com.google/read") == {"type": "read"}

    def test_feed(self):
        result = _parse_stream_id("feed/https://example.com/feed.xml")
        assert result == {"type": "feed", "url": "https://example.com/feed.xml"}

    def test_label(self):
        result = _parse_stream_id("user/-/label/Tech")
        assert result == {"type": "label", "name": "Tech"}

    def test_unknown_defaults_to_reading_list(self):
        result = _parse_stream_id("something/unknown")
        assert result == {"type": "reading-list"}


# ---------------------------------------------------------------------------
# Integration tests using the Flask test client
# ---------------------------------------------------------------------------

class TestClientLogin:
    @responses_lib.activate
    def test_successful_login(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": []},
            status=200,
        )
        resp = client.post("/accounts/ClientLogin", data={
            "Email": NC_USER,
            "Passwd": NC_PASS,
            "HostUrl": NC_BASE,
        })
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Auth=" in body
        assert "SID=" in body

    @responses_lib.activate
    def test_bad_credentials(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            status=401,
        )
        resp = client.post("/accounts/ClientLogin", data={
            "Email": NC_USER,
            "Passwd": "wrong",
            "HostUrl": NC_BASE,
        })
        assert resp.status_code == 403
        assert b"BadAuthentication" in resp.data

    def test_missing_host(self, client):
        # No NEXTCLOUD_URL env and no HostUrl → 400
        old = os.environ.pop("NEXTCLOUD_URL", None)
        try:
            resp = client.post("/accounts/ClientLogin", data={
                "Email": NC_USER,
                "Passwd": NC_PASS,
            })
            assert resp.status_code == 400
        finally:
            if old:
                os.environ["NEXTCLOUD_URL"] = old


class TestTokenEndpoint:
    def test_returns_token(self, client):
        resp = client.get("/reader/api/0/token", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.data.decode() == AUTH_TOKEN

    def test_unauthorized(self, client):
        resp = client.get("/reader/api/0/token")
        assert resp.status_code == 401


class TestUserInfo:
    def test_returns_user_info(self, client):
        resp = client.get("/reader/api/0/user-info", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["userId"] == NC_USER
        assert data["userName"] == NC_USER


class TestSubscriptionList:
    @responses_lib.activate
    def test_lists_subscriptions(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/folders",
            json={"folders": SAMPLE_FOLDERS},
            status=200,
        )
        resp = client.get("/reader/api/0/subscription/list", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert "subscriptions" in data
        assert len(data["subscriptions"]) == 2
        sub = data["subscriptions"][0]
        assert sub["id"] == "feed/https://example.com/feed.xml"
        assert sub["title"] == "Example Feed"
        # Feed in folder "Tech"
        assert any(c["label"] == "Tech" for c in sub["categories"])

    def test_unauthorized(self, client):
        resp = client.get("/reader/api/0/subscription/list")
        assert resp.status_code == 401


class TestTagList:
    @responses_lib.activate
    def test_lists_tags(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/folders",
            json={"folders": SAMPLE_FOLDERS},
            status=200,
        )
        resp = client.get("/reader/api/0/tag/list", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tags" in data
        tag_ids = [t["id"] for t in data["tags"]]
        assert "user/-/state/com.google/starred" in tag_ids
        assert "user/-/label/Tech" in tag_ids


class TestStreamContents:
    @responses_lib.activate
    def test_reading_list(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items",
            json={"items": SAMPLE_ITEMS},
            status=200,
        )
        resp = client.get(
            "/reader/api/0/stream/contents/user/-/state/com.google/reading-list",
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert len(data["items"]) == 2
        item = data["items"][0]
        assert item["title"] == "Test Article"
        assert "tag:google.com,2005:reader/item/" in item["id"]

    @responses_lib.activate
    def test_starred_stream(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items",
            json={"items": SAMPLE_ITEMS},
            status=200,
        )
        resp = client.get(
            "/reader/api/0/stream/contents/user/-/state/com.google/starred",
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Only the starred item should appear
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Starred Article"

    def test_unauthorized(self, client):
        resp = client.get("/reader/api/0/stream/contents/user/-/state/com.google/reading-list")
        assert resp.status_code == 401


class TestStreamItemIds:
    @responses_lib.activate
    def test_returns_item_refs(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items",
            json={"items": SAMPLE_ITEMS},
            status=200,
        )
        resp = client.get("/reader/api/0/stream/items/ids", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert "itemRefs" in data
        assert len(data["itemRefs"]) == 2
        assert data["itemRefs"][0]["id"] == _nc_id_to_reader_id(42)


class TestEditTag:
    @responses_lib.activate
    def test_star_item(self, client):
        reader_id = _nc_id_to_reader_id(42)
        # GET /items to find feed_id
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items",
            json={"items": [SAMPLE_ITEMS[0]]},
            status=200,
        )
        responses_lib.add(
            responses_lib.PUT,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items/1/42/star",
            status=200,
        )
        resp = client.post(
            "/reader/api/0/edit-tag",
            headers=auth_headers(),
            data={"i": reader_id, "a": "user/-/state/com.google/starred"},
        )
        assert resp.status_code == 200

    @responses_lib.activate
    def test_mark_read(self, client):
        reader_id = _nc_id_to_reader_id(42)
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items",
            json={"items": [SAMPLE_ITEMS[0]]},
            status=200,
        )
        responses_lib.add(
            responses_lib.POST,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/items/read/multiple",
            status=200,
        )
        resp = client.post(
            "/reader/api/0/edit-tag",
            headers=auth_headers(),
            data={"i": reader_id, "a": "user/-/state/com.google/read"},
        )
        assert resp.status_code == 200


class TestMarkAllAsRead:
    @responses_lib.activate
    def test_mark_all_read(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        for feed in SAMPLE_FEEDS:
            responses_lib.add(
                responses_lib.PUT,
                f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds/{feed['id']}/read",
                status=200,
            )
        resp = client.post(
            "/reader/api/0/mark-all-as-read",
            headers=auth_headers(),
            data={"s": "user/-/state/com.google/reading-list"},
        )
        assert resp.status_code == 200

    @responses_lib.activate
    def test_mark_feed_as_read(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.PUT,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds/1/read",
            status=200,
        )
        resp = client.post(
            "/reader/api/0/mark-all-as-read",
            headers=auth_headers(),
            data={"s": "feed/https://example.com/feed.xml"},
        )
        assert resp.status_code == 200


class TestUnreadCount:
    @responses_lib.activate
    def test_returns_counts(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        resp = client.get("/reader/api/0/unread-count", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert "unreadcounts" in data
        total = next(
            (c for c in data["unreadcounts"]
             if c["id"] == "user/-/state/com.google/reading-list"),
            None,
        )
        assert total is not None
        assert total["count"] == 3  # sum of unreadCount from SAMPLE_FEEDS


class TestPreferenceList:
    def test_returns_prefs(self, client):
        resp = client.get("/reader/api/0/preference/list", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert "prefs" in data


class TestSubscriptionEdit:
    @responses_lib.activate
    def test_subscribe(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/folders",
            json={"folders": SAMPLE_FOLDERS},
            status=200,
        )
        responses_lib.add(
            responses_lib.POST,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": [SAMPLE_FEEDS[0]]},
            status=200,
        )
        resp = client.post(
            "/reader/api/0/subscription/edit",
            headers=auth_headers(),
            data={
                "ac": "subscribe",
                "s": "feed/https://example.com/feed.xml",
                "a": "user/-/label/Tech",
            },
        )
        assert resp.status_code == 200

    @responses_lib.activate
    def test_unsubscribe(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds",
            json={"feeds": SAMPLE_FEEDS},
            status=200,
        )
        responses_lib.add(
            responses_lib.DELETE,
            f"{NC_BASE}/index.php/apps/news/api/v1-3/feeds/1",
            status=200,
        )
        resp = client.post(
            "/reader/api/0/subscription/edit",
            headers=auth_headers(),
            data={
                "ac": "unsubscribe",
                "s": "feed/https://example.com/feed.xml",
            },
        )
        assert resp.status_code == 200
