'use strict';

const express = require('express');
const axios = require('axios');
const { createAuthToken } = require('../auth');

const router = express.Router();
const NEXTCLOUD_URL = process.env.NEXTCLOUD_URL || '';

// POST /accounts/ClientLogin — authenticate and issue a Google Reader-style token.
// Clients may also use GET (the spec notes it is "strongly recommended to use POST").
async function handleClientLogin(req, res) {
  const body = (req.method === 'POST' ? req.body : req.query) || {};
  const username = body.Email || body.email || '';
  const password = body.Passwd || body.passwd || body.Password || '';

  if (!username || !password) {
    res.status(400).send('Error=BadAuthentication');
    return;
  }

  if (!NEXTCLOUD_URL) {
    res.status(500).send('Error=ServiceUnavailable');
    return;
  }

  // Validate credentials against Nextcloud News API
  try {
    await axios.get(`${NEXTCLOUD_URL.replace(/\/$/, '')}/index.php/apps/news/api/v1-3/version`, {
      auth: { username, password },
    });
  } catch (err) {
    const status = err.response ? err.response.status : 0;
    if (status === 401 || status === 403) {
      res.status(403).send('Error=BadAuthentication');
    } else {
      res.status(500).send('Error=ServiceUnavailable');
    }
    return;
  }

  const token = createAuthToken(username, password);
  res.type('text/plain').send(`SID=${token}\nLSID=${token}\nAuth=${token}\n`);
}

router.post('/ClientLogin', handleClientLogin);
router.get('/ClientLogin', handleClientLogin);

module.exports = router;
