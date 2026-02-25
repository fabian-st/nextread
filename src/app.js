'use strict';

const express = require('express');
const accountsRouter = require('./routes/accounts');
const readerRouter = require('./routes/reader');

function createApp() {
  const app = express();

  // Parse URL-encoded bodies (FeedHQ POST requests use application/x-www-form-urlencoded)
  app.use(express.urlencoded({ extended: true }));
  // Parse JSON bodies
  app.use(express.json());

  // Mount routers
  app.use('/accounts', accountsRouter);
  app.use('/reader/api/0', readerRouter);

  // 404 fallback
  app.use((req, res) => {
    res.status(404).send('Not Found');
  });

  return app;
}

module.exports = { createApp };
