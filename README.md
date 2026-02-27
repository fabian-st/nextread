# nextread

A service that translates Google Reader API requests into [Nextcloud News](https://github.com/nextcloud/news) API calls, allowing RSS reader clients that support the Google Reader API to connect to a Nextcloud News instance.

## How it works

nextread sits between a Google Reader–compatible client and a Nextcloud instance:

```
RSS Client  ──Google Reader API──►  nextread  ──NC News API──►  Nextcloud News
```

It handles authentication, request translation, and response mapping transparently.

## Configuration

| Environment variable | Description                             | Default |
|----------------------|-----------------------------------------|---------|
| `NEXTCLOUD_URL`      | Base URL of your Nextcloud instance     | *(required)* |
| `PORT`               | Port to listen on                       | `3000` |

## Running

```sh
npm install
NEXTCLOUD_URL=https://cloud.example.com npm start
```

With Docker

```sh
docker build -t nextread .
docker run -e NEXTCLOUD_URL=https://cloud.example.com -p 3000:3000 nextread
```

Or with Docker Compose:

```sh
NEXTCLOUD_URL=https://cloud.example.com docker compose up
```

## Authentication

Clients authenticate with their Nextcloud username and password via the Google Reader API `ClientLogin` endpoint:

```
POST /accounts/ClientLogin
Content-Type: application/x-www-form-urlencoded

Email=<nextcloud-username>&Passwd=<nextcloud-password>
```

The returned `Auth` token is then sent in the `Authorization` header:

```
Authorization: GoogleLogin auth=<token>
```

## Supported API endpoints

| Google Reader API endpoint              | Nextcloud News equivalent         |
|-------------------------------------------|-----------------------------------|
| `POST /accounts/ClientLogin`              | Validates via NC `/version`       |
| `GET  /reader/api/0/token`                | Issues a POST token               |
| `GET  /reader/api/0/user-info`            | `GET /user`                       |
| `GET  /reader/api/0/unread-count`         | `GET /feeds`                      |
| `GET  /reader/api/0/tag/list`             | `GET /folders`                    |
| `POST /reader/api/0/disable-tag`          | `DELETE /folders/{id}`            |
| `POST /reader/api/0/rename-tag`           | `PUT /folders/{id}`               |
| `GET  /reader/api/0/subscription/list`    | `GET /feeds`, `GET /folders`     |
| `POST /reader/api/0/subscription/edit`    | `POST\|DELETE /feeds`, `POST /feeds/{id}/move` |
| `POST /reader/api/0/subscription/quickadd` | `POST /feeds`                   |
| `GET  /reader/api/0/subscribed`           | `GET /feeds`                      |
| `GET  /reader/api/0/stream/contents/*`    | `GET /items`                      |
| `GET  /reader/api/0/stream/items/ids`     | `GET /items`                      |
| `GET\|POST /reader/api/0/stream/items/contents` | `GET /items`               |
| `POST /reader/api/0/edit-tag`             | `POST /items/{id}/read\|unread\|star\|unstar` |
| `POST /reader/api/0/mark-all-as-read`     | `POST /feeds/{id}/read`, `/folders/{id}/read`, `/items/read` |
| `GET  /reader/api/0/preference/list`      | Static response                   |
| `GET  /reader/api/0/preference/stream/list` | Static response                |
| `GET  /reader/api/0/friend/list`          | `GET /user`                       |

