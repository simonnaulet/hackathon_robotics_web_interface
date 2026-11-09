// Sert web/ sur http://localhost:8000 (localhost = contexte sécurisé : micro et manette autorisés).
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';

const root = join(import.meta.dirname, 'web');
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css' };

createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;  // URL résout déjà les « .. »
  const file = join(root, path === '/' ? 'index.html' : path);
  try {
    if (!file.startsWith(root)) throw new Error();
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' }).end(body);
  } catch {
    res.writeHead(404).end('introuvable');
  }
}).listen(8000, '127.0.0.1', () => console.log('Interface : http://localhost:8000'));
