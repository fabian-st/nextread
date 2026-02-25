'use strict';

const axios = require('axios');

function createClient(ncUrl, username, password) {
  const base = `${ncUrl.replace(/\/$/, '')}/index.php/apps/news/api/v1-3`;
  const auth = { username, password };

  async function get(path, params) {
    const { data } = await axios.get(`${base}${path}`, { auth, params });
    return data;
  }

  async function post(path, body) {
    const { data } = await axios.post(`${base}${path}`, body, { auth });
    return data;
  }

  async function put(path, body) {
    const { data } = await axios.put(`${base}${path}`, body, { auth });
    return data;
  }

  async function del(path) {
    const { data } = await axios.delete(`${base}${path}`, { auth });
    return data;
  }

  return {
    getFolders: () => get('/folders').then(d => d.folders || []),
    createFolder: name => post('/folders', { name }).then(d => (d.folders || [])[0]),
    deleteFolder: id => del(`/folders/${id}`),
    renameFolder: (id, name) => put(`/folders/${id}`, { name }),
    markFolderRead: (id, newestItemId) => post(`/folders/${id}/read`, { newestItemId }),

    getFeeds: () => get('/feeds'),
    createFeed: (url, folderId) => post('/feeds', { url, folderId: folderId || null }),
    deleteFeed: id => del(`/feeds/${id}`),
    moveFeed: (id, folderId) => post(`/feeds/${id}/move`, { folderId: folderId || null }),
    renameFeed: (id, feedTitle) => post(`/feeds/${id}/rename`, { feedTitle }),
    markFeedRead: (id, newestItemId) => post(`/feeds/${id}/read`, { newestItemId }),

    getItems: params => get('/items', params).then(d => d.items || []),
    getUpdatedItems: params => get('/items/updated', params).then(d => d.items || []),

    markItemRead: id => post(`/items/${id}/read`, {}),
    markItemUnread: id => post(`/items/${id}/unread`, {}),
    markItemStarred: id => post(`/items/${id}/star`, {}),
    markItemUnstarred: id => post(`/items/${id}/unstar`, {}),

    markMultipleRead: itemIds => post('/items/read/multiple', { itemIds }),
    markMultipleUnread: itemIds => post('/items/unread/multiple', { itemIds }),
    markMultipleStarred: itemIds => post('/items/star/multiple', { itemIds }),
    markMultipleUnstarred: itemIds => post('/items/unstar/multiple', { itemIds }),
    markAllRead: newestItemId => post('/items/read', { newestItemId }),

    getUser: () => get('/user'),
    getVersion: () => get('/version'),
  };
}

module.exports = { createClient };
