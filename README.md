# nextread

A Python service that translates [Google Reader API](https://feedhq.readthedocs.io/en/latest/api/) requests to a [Nextcloud News](https://nextcloud.github.io/news/api/api-v1-3/) instance, allowing any Reader-compatible client to use Nextcloud News as its back-end.

## How it works

```
Reader client  →  nextread (Flask)  →  Nextcloud News API
```

The service acts as a proxy:
1. The client authenticates via the Reader `ClientLogin` endpoint.
2. nextread verifies the credentials against the configured Nextcloud instance.
3. On success it issues a stateless auth token (Base64-encoded credentials).
4. Every subsequent Reader API call is translated into the equivalent Nextcloud News API call, and the response is converted back to the Reader format.

## Implemented endpoints

| Reader API endpoint | Description |
|---|---|
| `POST /accounts/ClientLogin` | Authentication |
| `GET /reader/api/0/token` | CSRF token |
| `GET /reader/api/0/user-info` | User information |
| `GET /reader/api/0/subscription/list` | List subscribed feeds |
| `POST /reader/api/0/subscription/edit` | Subscribe / unsubscribe / rename / move feed |
| `GET /reader/api/0/tag/list` | List tags / folders |
| `GET /reader/api/0/stream/contents/<stream_id>` | Stream contents |
| `GET /reader/api/0/stream/items/ids` | Stream item IDs |
| `POST /reader/api/0/stream/items/contents` | Fetch items by ID |
| `POST /reader/api/0/mark-all-as-read` | Mark all items as read |
| `POST /reader/api/0/edit-tag` | Star/unstar or mark read/unread |
| `GET /reader/api/0/preference/list` | Preference list (stub) |
| `GET /reader/api/0/unread-count` | Unread counts |

## Setup

### Requirements

- Python 3.10+
- A running Nextcloud instance with the [News app](https://github.com/nextcloud/news) installed

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configuration

| Environment variable | Required | Description |
|---|---|---|
| `NEXTCLOUD_URL` | Optional* | Base URL of the Nextcloud instance, e.g. `https://cloud.example.com`. When set, all clients share this server. When omitted the client must supply `HostUrl` at login time. |
| `HOST` | No | Bind address (default `0.0.0.0`) |
| `PORT` | No | Port to listen on (default `5000`) |
| `DEBUG` | No | Enable Flask debug mode (`true`/`false`, default `false`) |

### Run

```bash
NEXTCLOUD_URL=https://cloud.example.com python app.py
```

### Client configuration

In your Reader-compatible client set the server URL to `http://<nextread-host>:<port>` and log in with your Nextcloud username and password (or an [app password](https://docs.nextcloud.com/server/latest/user_manual/en/session_token.html)).

If `NEXTCLOUD_URL` is **not** set in the environment, supply the Nextcloud base URL in the `HostUrl` login field (supported by some clients).

## Running tests

```bash
pytest tests/
```
