import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python = path.join(
  repository,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
if (!existsSync(python))
  throw new Error(
    'Create the project .venv and install the Python package before browser integration tests.',
  );
const workspace = path.join(repository, 'tmp', `browser-${Date.now()}`).replaceAll('\\', '/');
const code = `import uvicorn; from sensorfusion.api import create_app; uvicorn.run(create_app(${JSON.stringify(workspace)}), host='127.0.0.1', port=8001)`;
const backend = spawn(python, ['-c', code], {
  cwd: repository,
  stdio: 'inherit',
  env: { ...process.env, PYTHONPATH: path.join(repository, 'src'), PYTHONUTF8: '1' },
  windowsHide: true,
});
backend.on('error', (error) => {
  throw error;
});
backend.on('exit', (code) => process.exit(code ?? 1));
process.on('SIGTERM', () => backend.kill());
process.on('SIGINT', () => backend.kill());
