// Serves the built frontend (frontend/dist) on a free local port for the browser tests.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async function serveDist() {
  const root = path.resolve(__dirname, '../dist');
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://localhost').pathname;
    const file = path.join(root, name === '/' ? 'index.html' : name);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return server;
};
