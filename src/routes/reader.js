'use strict';

const express = require('express');
const {
  extractAuthToken,
  getCredentials,
  createPostToken,
  validatePostToken,
} = require('../auth');
const { createClient } = require('../nc-client');
const {
  toLongFormId,
  fromAnyItemId,
  parseStreamId,
  ncFolderToTag,
  ncFeedToSubscription,
  ncFeedsToUnreadCounts,
  ncItemToReaderItem,
} = require('../transformers');

const router = express.Router();
const NEXTCLOUD_URL = process.env.NEXTCLOUD_URL || '';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sendResult(req, res, data) {
  const output = req.query.output || '';
  const accept = req.headers.accept || '';
  if (output === 'json' || accept.includes('application/json')) {
    return res.json(data);
  }
  // Default to JSON for modern clients (most clients use JSON)
  res.json(data);
}

function requireAuth(req, res) {
  const token = extractAuthToken(req);
  if (!token) {
    res.status(401).send('Unauthorized');
    return null;
  }
  const creds = getCredentials(token);
  if (!creds) {
    res.status(401).send('Unauthorized');
    return null;
  }
  return creds;
}

function requirePostToken(req, res) {
  const t = req.body.T || req.query.T;
  if (!validatePostToken(t)) {
    res.set('X-Reader-Google-Bad-Token', 'true').status(401).send('Invalid token');
    return false;
  }
  return true;
}

function ncClient(creds) {
  return createClient(NEXTCLOUD_URL, creds.username, creds.password);
}

async function resolveStreamToNcParams(stream, nc, { getRead = true } = {}) {
  // The NC API does not support getRead=false for already-filtered list types
  // (starred, feed, folder). For the starred stream (type=2), always fetch all
  // starred items regardless of read state to avoid an NC API error response.
  const effectiveGetRead = stream.type === 2 ? true : getRead;
  const params = { getRead: effectiveGetRead, batchSize: -1, type: stream.type || 3, id: stream.id || 0 };

  if (stream.type === 0) {
    // Feed stream — find feed by URL
    const feedsData = await nc.getFeeds();
    const feeds = feedsData.feeds || [];
    const feed = feeds.find(f => f.url === stream.feedUrl);
    if (!feed) return null;
    params.type = 0;
    params.id = feed.id;
  } else if (stream.type === 1) {
    // Folder stream — find folder by name
    const folders = await nc.getFolders();
    const folder = folders.find(f => f.name === stream.labelName);
    if (!folder) return null;
    params.type = 1;
    params.id = folder.id;
  }

  return params;
}

// ─── GET /reader/api/0/token ──────────────────────────────────────────────────

router.get('/token', (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  res.type('text/plain').send(createPostToken());
});

// ─── GET /reader/api/0/user-info ─────────────────────────────────────────────

router.get('/user-info', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  try {
    const nc = ncClient(creds);
    const user = await nc.getUser();
    sendResult(req, res, {
      userId: String(user.userId || creds.username),
      userName: user.displayName || creds.username,
      userProfileId: String(user.userId || '1'),
      userEmail: creds.username,
      isBloggerUser: false,
      signupTimeSec: Math.floor((user.lastLoginTimestamp || Date.now() / 1000)),
      isMultiLoginEnabled: false,
    });
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching user info');
  }
});

// ─── GET /reader/api/0/unread-count ──────────────────────────────────────────

router.get('/unread-count', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  try {
    const nc = ncClient(creds);
    const [feedsData, folders] = await Promise.all([nc.getFeeds(), nc.getFolders()]);
    const feeds = feedsData.feeds || [];
    const userId = creds.username;
    sendResult(req, res, {
      max: 1000,
      unreadcounts: ncFeedsToUnreadCounts(feeds, folders, userId),
    });
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching unread count');
  }
});

// ─── GET /reader/api/0/tag/list ───────────────────────────────────────────────

router.get('/tag/list', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  try {
    const nc = ncClient(creds);
    const folders = await nc.getFolders();
    const userId = creds.username;
    const tags = [
      { id: `user/${userId}/state/com.google/starred`, sortid: 'A0000001' },
      { id: `user/${userId}/state/com.google/broadcast`, sortid: 'A0000002' },
      ...folders.map(f => ncFolderToTag(f, userId)),
    ];
    sendResult(req, res, { tags });
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching tags');
  }
});

// ─── POST /reader/api/0/disable-tag ──────────────────────────────────────────

router.post('/disable-tag', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  if (!requirePostToken(req, res)) return;

  const streamId = req.body.s || '';
  const labelName = req.body.t || '';

  try {
    const nc = ncClient(creds);
    const folders = await nc.getFolders();
    let folder;
    if (streamId) {
      const parsed = parseStreamId(streamId);
      folder = folders.find(f => f.name === parsed.labelName);
    } else if (labelName) {
      folder = folders.find(f => f.name === labelName);
    }
    if (folder) {
      await nc.deleteFolder(folder.id);
    }
    res.type('text/plain').send('OK');
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error deleting tag');
  }
});

// ─── POST /reader/api/0/rename-tag ───────────────────────────────────────────

router.post('/rename-tag', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  if (!requirePostToken(req, res)) return;

  const streamId = req.body.s || '';
  const labelName = req.body.t || '';
  const dest = req.body.dest || '';

  // dest is like "user/-/label/<new name>"
  const destMatch = dest.match(/\/label\/(.+)$/);
  const newName = destMatch ? destMatch[1] : dest;

  if (!newName) {
    res.status(400).send('Missing destination label name');
    return;
  }

  try {
    const nc = ncClient(creds);
    const folders = await nc.getFolders();
    let folder;
    if (streamId) {
      const parsed = parseStreamId(streamId);
      folder = folders.find(f => f.name === parsed.labelName);
    } else if (labelName) {
      folder = folders.find(f => f.name === labelName);
    }
    if (!folder) {
      res.status(404).send('Tag not found');
      return;
    }
    await nc.renameFolder(folder.id, newName);
    res.type('text/plain').send('OK');
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error renaming tag');
  }
});

// ─── GET /reader/api/0/subscription/list ─────────────────────────────────────

router.get('/subscription/list', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  try {
    const nc = ncClient(creds);
    const [feedsData, folders] = await Promise.all([nc.getFeeds(), nc.getFolders()]);
    const feeds = feedsData.feeds || [];
    const userId = creds.username;
    sendResult(req, res, {
      subscriptions: feeds.map(f => ncFeedToSubscription(f, folders, userId)),
    });
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching subscriptions');
  }
});

// ─── POST /reader/api/0/subscription/edit ────────────────────────────────────

router.post('/subscription/edit', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  if (!requirePostToken(req, res)) return;

  const action = req.body.ac || '';
  const streamId = req.body.s || '';
  const title = req.body.t || '';
  const addCategory = req.body.a || '';
  const removeCategory = req.body.r || '';

  try {
    const nc = ncClient(creds);

    if (action === 'subscribe') {
      if (!streamId.startsWith('feed/')) {
        res.status(400).send('Invalid stream ID');
        return;
      }
      const feedUrl = streamId.slice('feed/'.length);
      let folderId = null;
      if (addCategory) {
        const catMatch = addCategory.match(/\/label\/(.+)$/);
        if (catMatch) {
          const folders = await nc.getFolders();
          let folder = folders.find(f => f.name === catMatch[1]);
          if (!folder) {
            folder = await nc.createFolder(catMatch[1]);
          }
          folderId = folder ? folder.id : null;
        }
      }
      await nc.createFeed(feedUrl, folderId);

      // Rename if title provided
      if (title) {
        const feedsData = await nc.getFeeds();
        const feed = (feedsData.feeds || []).find(f => f.url === feedUrl);
        if (feed) await nc.renameFeed(feed.id, title);
      }
    } else if (action === 'edit') {
      const feedUrl = streamId.startsWith('feed/') ? streamId.slice('feed/'.length) : '';
      const feedsData = await nc.getFeeds();
      const feed = (feedsData.feeds || []).find(f => f.url === feedUrl);
      if (!feed) {
        res.status(404).send('Feed not found');
        return;
      }
      if (title) {
        await nc.renameFeed(feed.id, title);
      }
      if (addCategory) {
        const catMatch = addCategory.match(/\/label\/(.+)$/);
        if (catMatch) {
          const folders = await nc.getFolders();
          let folder = folders.find(f => f.name === catMatch[1]);
          if (!folder) {
            folder = await nc.createFolder(catMatch[1]);
          }
          if (folder) await nc.moveFeed(feed.id, folder.id);
        }
      } else if (removeCategory) {
        await nc.moveFeed(feed.id, null);
      }
    } else if (action === 'unsubscribe') {
      const feedUrl = streamId.startsWith('feed/') ? streamId.slice('feed/'.length) : '';
      const feedsData = await nc.getFeeds();
      const feed = (feedsData.feeds || []).find(f => f.url === feedUrl);
      if (feed) {
        await nc.deleteFeed(feed.id);
      }
    } else {
      res.status(400).send('Unknown action');
      return;
    }

    res.type('text/plain').send('OK');
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error editing subscription');
  }
});

// ─── POST /reader/api/0/subscription/quickadd ────────────────────────────────

router.post('/subscription/quickadd', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  if (!requirePostToken(req, res)) return;

  let feedUrl = req.body.quickadd || '';
  if (feedUrl.startsWith('feed/')) feedUrl = feedUrl.slice('feed/'.length);

  try {
    const nc = ncClient(creds);
    await nc.createFeed(feedUrl, null);
    sendResult(req, res, {
      numResults: 1,
      query: feedUrl,
      streamId: `feed/${feedUrl}`,
    });
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error adding subscription');
  }
});

// ─── GET /reader/api/0/subscribed ────────────────────────────────────────────

router.get('/subscribed', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;

  const streamId = req.query.s || '';
  if (!streamId.startsWith('feed/')) {
    res.type('text/plain').send('false');
    return;
  }
  const feedUrl = streamId.slice('feed/'.length);

  try {
    const nc = ncClient(creds);
    const feedsData = await nc.getFeeds();
    const feeds = feedsData.feeds || [];
    const found = feeds.some(f => f.url === feedUrl);
    res.type('text/plain').send(found ? 'true' : 'false');
  } catch (err) {
    res.type('text/plain').send('false');
  }
});

// ─── GET /reader/api/0/stream/contents/*path ─────────────────────────────────

router.get('/stream/contents/*path', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;

  const streamId = Array.isArray(req.params.path)
    ? req.params.path.join('/')
    : req.params.path || '';

  const count = parseInt(req.query.n, 10) || 20;
  const continuation = req.query.c || null;
  const exclude = req.query.xt || '';
  const sortOrder = req.query.r || '';

  // Determine getRead: if xt includes 'read', only return unread
  const getRead = !exclude.includes('com.google/read');
  const oldestFirst = sortOrder === 'o';

  try {
    const nc = ncClient(creds);
    const [feedsData, folders] = await Promise.all([nc.getFeeds(), nc.getFolders()]);
    const feeds = feedsData.feeds || [];
    const userId = creds.username;

    const stream = parseStreamId(streamId);
    const ncParams = await resolveStreamToNcParams(stream, nc, { getRead });
    if (!ncParams) {
      sendResult(req, res, { id: streamId, items: [], direction: 'ltr', author: userId, title: streamId, updated: Math.floor(Date.now() / 1000) });
      return;
    }

    ncParams.batchSize = count;
    ncParams.oldestFirst = oldestFirst;
    if (continuation) {
      ncParams.offset = parseInt(continuation, 10) || 0;
    }

    const items = await nc.getItems(ncParams);
    const readerItems = items.map(item => ncItemToReaderItem(item, feeds, userId));

    // Determine continuation: lowest item ID in the batch (for newest-first pagination)
    let cont;
    if (items.length === count) {
      const ids = items.map(i => i.id);
      cont = oldestFirst
        ? String(Math.max(...ids))
        : String(Math.min(...ids));
    }

    const response = {
      direction: 'ltr',
      id: streamId,
      title: streamId,
      author: userId,
      updated: Math.floor(Date.now() / 1000),
      items: readerItems,
    };
    if (cont) response.continuation = cont;

    sendResult(req, res, response);
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching stream contents');
  }
});

// ─── GET /reader/api/0/stream/items/ids ──────────────────────────────────────

router.get('/stream/items/ids', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;

  const streamId = req.query.s || '';
  const count = parseInt(req.query.n, 10) || 20;
  const continuation = req.query.c || null;
  const exclude = req.query.xt || '';
  const getRead = !exclude.includes('com.google/read');

  try {
    const nc = ncClient(creds);
    const stream = parseStreamId(streamId);
    const ncParams = await resolveStreamToNcParams(stream, nc, { getRead });
    if (!ncParams) {
      sendResult(req, res, { itemRefs: [], continuation: undefined });
      return;
    }

    ncParams.batchSize = count;
    if (continuation) ncParams.offset = parseInt(continuation, 10) || 0;

    const items = await nc.getItems(ncParams);
    const userId = creds.username;
    const itemRefs = items.map(i => {
      const directStreamIds = [`user/${userId}/state/com.google/reading-list`];
      if (i.unread === false || i.unread === 0) {
        directStreamIds.push(`user/${userId}/state/com.google/read`);
      }
      if (i.starred === true || i.starred === 1) {
        directStreamIds.push(`user/${userId}/state/com.google/starred`);
      }
      return { id: toLongFormId(i.id), directStreamIds, timestampUsec: `${(i.pubDate || 0) * 1000000}` };
    });

    const response = { itemRefs };
    if (items.length === count) {
      const ids = items.map(i => i.id);
      response.continuation = String(Math.min(...ids));
    }

    sendResult(req, res, response);
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching item IDs');
  }
});

// ─── GET|POST /reader/api/0/stream/items/contents ────────────────────────────

async function handleStreamItemsContents(req, res) {
  const creds = requireAuth(req, res);
  if (!creds) return;

  const body = req.method === 'POST' ? req.body : req.query;
  const rawIds = Array.isArray(body.i) ? body.i : body.i ? [body.i] : [];
  const ncIds = rawIds.map(fromAnyItemId).filter(id => !isNaN(id));

  if (!ncIds.length) {
    sendResult(req, res, { direction: 'ltr', id: 'stream/items/contents', items: [] });
    return;
  }

  try {
    const nc = ncClient(creds);
    const [feedsData, allItems] = await Promise.all([
      nc.getFeeds(),
      nc.getItems({ type: 3, id: 0, getRead: true, batchSize: -1 }),
    ]);
    const feeds = feedsData.feeds || [];
    const userId = creds.username;

    const ncIdSet = new Set(ncIds);
    const matchedItems = allItems.filter(i => ncIdSet.has(i.id));
    const readerItems = matchedItems.map(i => ncItemToReaderItem(i, feeds, userId));

    sendResult(req, res, {
      direction: 'ltr',
      id: 'stream/items/contents',
      items: readerItems,
    });
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error fetching item contents');
  }
}

router.get('/stream/items/contents', handleStreamItemsContents);
router.post('/stream/items/contents', handleStreamItemsContents);

// ─── POST /reader/api/0/edit-tag ──────────────────────────────────────────────

router.post('/edit-tag', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  if (!requirePostToken(req, res)) return;

  const rawIds = Array.isArray(req.body.i) ? req.body.i : req.body.i ? [req.body.i] : [];
  const addTags = Array.isArray(req.body.a) ? req.body.a : req.body.a ? [req.body.a] : [];
  const removeTags = Array.isArray(req.body.r) ? req.body.r : req.body.r ? [req.body.r] : [];

  const ncIds = rawIds.map(fromAnyItemId).filter(id => !isNaN(id));
  if (!ncIds.length) {
    res.status(400).send('Missing item IDs');
    return;
  }

  const markRead = addTags.some(t => t.includes('com.google/read'));
  const markUnread = addTags.some(t => t.includes('com.google/kept-unread'))
    || removeTags.some(t => t.includes('com.google/read'));
  const markStarred = addTags.some(t => t.includes('com.google/starred'));
  const markUnstarred = removeTags.some(t => t.includes('com.google/starred'));

  try {
    const nc = ncClient(creds);
    const ops = [];
    if (markRead) ops.push(nc.markMultipleRead(ncIds));
    if (markUnread) ops.push(nc.markMultipleUnread(ncIds));
    if (markStarred) ops.push(nc.markMultipleStarred(ncIds));
    if (markUnstarred) ops.push(nc.markMultipleUnstarred(ncIds));
    await Promise.all(ops);
    res.type('text/plain').send('OK');
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error editing tags');
  }
});

// ─── POST /reader/api/0/mark-all-as-read ─────────────────────────────────────

router.post('/mark-all-as-read', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  if (!requirePostToken(req, res)) return;

  const streamId = req.body.s || '';

  try {
    const nc = ncClient(creds);
    const [feedsData, folders] = await Promise.all([nc.getFeeds(), nc.getFolders()]);
    const feeds = feedsData.feeds || [];

    // Get the newest item ID for the stream so we don't over-mark
    const stream = parseStreamId(streamId);
    let newestItemId;

    const ncParams = await resolveStreamToNcParams(stream, nc, { getRead: true });
    if (ncParams) {
      const items = await nc.getItems({ ...ncParams, batchSize: 1, oldestFirst: false });
      newestItemId = items.length ? items[0].id : null;
    }

    if (!newestItemId) {
      res.type('text/plain').send('OK');
      return;
    }

    if (stream.type === 0) {
      const feed = feeds.find(f => f.url === stream.feedUrl);
      if (feed) await nc.markFeedRead(feed.id, newestItemId);
    } else if (stream.type === 1) {
      const folder = folders.find(f => f.name === stream.labelName);
      if (folder) await nc.markFolderRead(folder.id, newestItemId);
    } else {
      await nc.markAllRead(newestItemId);
    }

    res.type('text/plain').send('OK');
  } catch (err) {
    const status = err.response ? err.response.status : 500;
    res.status(status).send('Error marking all as read');
  }
});

// ─── GET /reader/api/0/preference/list ───────────────────────────────────────

router.get('/preference/list', (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  sendResult(req, res, {
    prefs: [
      { id: 'lhn-prefs', value: '{"subscriptions":{"ssa":"true"}}' },
    ],
  });
});

// ─── GET /reader/api/0/preference/stream/list ─────────────────────────────────

router.get('/preference/stream/list', (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  sendResult(req, res, { streamprefs: {} });
});

// ─── GET /reader/api/0/friend/list ───────────────────────────────────────────

router.get('/friend/list', async (req, res) => {
  const creds = requireAuth(req, res);
  if (!creds) return;
  try {
    const nc = ncClient(creds);
    const user = await nc.getUser();
    const userId = String(user.userId || creds.username);
    const displayName = user.displayName || creds.username;
    sendResult(req, res, {
      friends: [
        {
          p: '',
          contactId: '-1',
          flags: 1,
          stream: `user/${userId}/state/com.google/broadcast`,
          hasSharedItemsOnProfile: false,
          profileIds: [userId],
          userIds: [userId],
          givenName: displayName,
          displayName,
          n: '',
        },
      ],
    });
  } catch (_err) {
    sendResult(req, res, { friends: [] });
  }
});

module.exports = router;
