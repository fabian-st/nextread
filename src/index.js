'use strict';

const { createApp } = require('./app');

const PORT = parseInt(process.env.PORT, 10) || 3000;

if (!process.env.NEXTCLOUD_URL) {
  console.warn('Warning: NEXTCLOUD_URL environment variable is not set.');
  console.warn('Set it to your Nextcloud base URL, e.g. https://cloud.example.com');
}

const app = createApp();
app.listen(PORT, () => {
  console.log(`nextread listening on port ${PORT}`);
  if (process.env.NEXTCLOUD_URL) {
    console.log(`Proxying to Nextcloud at: ${process.env.NEXTCLOUD_URL}`);
  }
});
