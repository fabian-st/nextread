'use strict';

const { v4: uuidv4 } = require('uuid');

// Maps auth token → { username, password }
const authTokens = new Map();

// Maps post token → expiry timestamp (ms)
const postTokens = new Map();

const POST_TOKEN_TTL_MS = 30 * 60 * 1000; // 30 minutes

function createAuthToken(username, password) {
  const token = uuidv4().replace(/-/g, '');
  authTokens.set(token, { username, password });
  return token;
}

function getCredentials(token) {
  return authTokens.get(token) || null;
}

function createPostToken() {
  const token = uuidv4().replace(/-/g, '');
  postTokens.set(token, Date.now() + POST_TOKEN_TTL_MS);
  return token;
}

function validatePostToken(token) {
  if (!token) return false;
  const expiry = postTokens.get(token);
  if (!expiry) return false;
  if (Date.now() > expiry) {
    postTokens.delete(token);
    return false;
  }
  return true;
}

function extractAuthToken(req) {
  const authHeader = req.headers.authorization;
  if (!authHeader) return null;
  const match = authHeader.match(/GoogleLogin\s+auth=(\S+)/i);
  return match ? match[1] : null;
}

module.exports = {
  createAuthToken,
  getCredentials,
  createPostToken,
  validatePostToken,
  extractAuthToken,
};
