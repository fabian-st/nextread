'use strict';

// ─── Item ID Conversion ──────────────────────────────────────────────────────

function toLongFormId(ncId) {
  const hex = ncId.toString(16).padStart(16, '0');
  return `tag:google.com,2005:reader/item/${hex}`;
}

function fromAnyItemId(id) {
  if (typeof id === 'number') return id;
  const s = String(id);
  if (s.startsWith('tag:google.com,2005:reader/item/')) {
    return parseInt(s.split('/').pop(), 16);
  }
  return parseInt(s, 10);
}

// ─── Stream ID Parsing ───────────────────────────────────────────────────────

function parseStreamId(streamId) {
  if (!streamId) return { type: 3, id: 0 };

  if (/\/state\/com\.google\/reading-list/.test(streamId)) {
    return { type: 3, id: 0 };
  }
  if (/\/state\/com\.google\/starred/.test(streamId)) {
    return { type: 2, id: 0 };
  }
  if (/\/state\/com\.google\/(read|kept-unread)/.test(streamId)) {
    return { type: 3, id: 0, onlyUnread: /kept-unread/.test(streamId) };
  }
  const labelMatch = streamId.match(/\/label\/(.+)$/);
  if (labelMatch) {
    return { type: 1, labelName: decodeURIComponent(labelMatch[1]) };
  }
  if (streamId.startsWith('feed/')) {
    return { type: 0, feedUrl: streamId.slice('feed/'.length) };
  }
  return { type: 3, id: 0 };
}

// ─── Feed / Folder Transformers ──────────────────────────────────────────────

function ncFolderToTag(folder, userId) {
  return {
    id: `user/${userId}/label/${folder.name}`,
    sortid: `B${String(folder.id).padStart(7, '0')}`,
  };
}

function ncFeedToSubscription(feed, folders, userId) {
  const folder = folders.find(f => f.id === feed.folderId);
  const categories = folder
    ? [{ id: `user/${userId}/label/${folder.name}`, label: folder.name }]
    : [];
  return {
    id: `feed/${feed.url}`,
    title: feed.title || '',
    htmlUrl: feed.link || '',
    firstitemmsec: `${(feed.added || 0) * 1000}`,
    sortid: `C${String(feed.id).padStart(7, '0')}`,
    categories,
  };
}

function ncFeedsToUnreadCounts(feeds, folders, userId) {
  const counts = [];

  for (const feed of feeds) {
    if (feed.unreadCount > 0) {
      counts.push({
        id: `feed/${feed.url}`,
        count: feed.unreadCount,
        newestItemTimestampUsec: '0',
      });
    }
  }

  // Aggregate per folder
  const folderCounts = {};
  for (const feed of feeds) {
    if (feed.folderId && feed.unreadCount > 0) {
      folderCounts[feed.folderId] = (folderCounts[feed.folderId] || 0) + feed.unreadCount;
    }
  }
  for (const folder of folders) {
    if (folderCounts[folder.id]) {
      counts.push({
        id: `user/${userId}/label/${folder.name}`,
        count: folderCounts[folder.id],
        newestItemTimestampUsec: '0',
      });
    }
  }

  const total = feeds.reduce((sum, f) => sum + (f.unreadCount || 0), 0);
  counts.push({
    id: `user/${userId}/state/com.google/reading-list`,
    count: total,
    newestItemTimestampUsec: '0',
  });

  return counts;
}

// ─── Item Transformer ────────────────────────────────────────────────────────

function ncItemToReaderItem(item, feeds, userId) {
  const feed = feeds.find(f => f.id === item.feedId);
  const categories = [`user/${userId}/state/com.google/reading-list`];
  if (!item.unread) {
    categories.push(`user/${userId}/state/com.google/read`);
  }
  if (item.starred) {
    categories.push(`user/${userId}/state/com.google/starred`);
  }

  const pubDateSec = item.pubDate || 0;

  return {
    id: toLongFormId(item.id),
    crawlTimeMsec: `${(item.lastModified || 0) * 1000}`,
    timestampUsec: `${pubDateSec * 1000000}`,
    published: pubDateSec,
    updated: pubDateSec,
    title: item.title || '',
    author: item.author || '',
    content: {
      direction: 'ltr',
      content: item.body || '',
    },
    alternate: item.url ? [{ href: item.url, type: 'text/html' }] : [],
    categories,
    origin: feed
      ? { streamId: `feed/${feed.url}`, title: feed.title || '', htmlUrl: feed.link || '' }
      : {},
  };
}

module.exports = {
  toLongFormId,
  fromAnyItemId,
  parseStreamId,
  ncFolderToTag,
  ncFeedToSubscription,
  ncFeedsToUnreadCounts,
  ncItemToReaderItem,
};
