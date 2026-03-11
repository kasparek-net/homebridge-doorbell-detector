/**
 * Post-install script — fully automatic Python environment setup.
 * Installs system packages (apt-get), creates virtualenv, installs pip deps.
 *
 * Designed for Raspberry Pi / Debian / Ubuntu where Homebridge runs as root.
 * Never crashes npm install — all errors are warnings.
 */

import { execSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

const PYTHON_DIR = path.resolve(__dirname, '..', 'python');
const VENV_DIR = path.join(PYTHON_DIR, '.venv');
const REQUIREMENTS = path.join(PYTHON_DIR, 'requirements.txt');

function log(msg: string): void {
  console.log(`[doorbell-detector] ${msg}`);
}

function warn(msg: string): void {
  console.warn(`[doorbell-detector] WARNING: ${msg}`);
}

function run(cmd: string, silent = false): boolean {
  try {
    execSync(cmd, { stdio: silent ? 'pipe' : 'inherit' });
    return true;
  } catch {
    return false;
  }
}

function hasCommand(cmd: string): boolean {
  try {
    execSync(`which ${cmd}`, { stdio: 'pipe' });
    return true;
  } catch {
    return false;
  }
}

function isDebian(): boolean {
  try {
    return fs.existsSync('/etc/debian_version');
  } catch {
    return false;
  }
}

function isRoot(): boolean {
  return process.getuid?.() === 0;
}

function installSystemDeps(): void {
  if (!isDebian()) {
    log('Not a Debian/Ubuntu system — skipping apt-get');
    return;
  }

  const needed: string[] = [];

  if (!hasCommand('python3')) needed.push('python3');

  // Check if python3-venv is available
  if (!run('python3 -m venv --help', true)) needed.push('python3-venv');

  // Check for portaudio (needed by PyAudio)
  const portaudioExists =
    fs.existsSync('/usr/include/portaudio.h') ||
    fs.existsSync('/usr/lib/arm-linux-gnueabihf/libportaudio.so') ||
    fs.existsSync('/usr/lib/aarch64-linux-gnu/libportaudio.so');
  if (!portaudioExists) needed.push('portaudio19-dev');

  // python3-dev needed for building native extensions
  const pythonDevExists =
    fs.existsSync('/usr/include/python3/Python.h') ||
    run('python3-config --includes', true);
  if (!pythonDevExists) needed.push('python3-dev');

  if (needed.length === 0) {
    log('System dependencies already installed');
    return;
  }

  log(`Installing system packages: ${needed.join(', ')}`);

  if (!isRoot() && !hasCommand('sudo')) {
    warn(`Need root to install: ${needed.join(', ')}`);
    warn('Run manually: sudo apt-get install -y ' + needed.join(' '));
    return;
  }

  const prefix = isRoot() ? '' : 'sudo ';
  run(`${prefix}apt-get update -qq`);

  if (!run(`${prefix}apt-get install -y ${needed.join(' ')}`)) {
    warn('apt-get install failed. Run manually:');
    warn(`  sudo apt-get install -y ${needed.join(' ')}`);
  }
}

function findPython(): string | null {
  for (const cmd of ['python3', 'python']) {
    try {
      const version = execSync(`${cmd} --version 2>&1`, { encoding: 'utf8' });
      if (version.includes('Python 3')) return cmd;
    } catch { /* skip */ }
  }
  return null;
}

function main(): void {
  log('Setting up Python environment...');

  // Skip on macOS during development — only auto-install on Linux/RPi
  if (os.platform() === 'linux') {
    installSystemDeps();
  }

  const python = findPython();
  if (!python) {
    warn('Python 3 not found after setup attempt.');
    return;
  }
  log(`Using: ${python}`);

  // Create virtualenv
  if (!fs.existsSync(path.join(VENV_DIR, 'bin', 'python'))) {
    log('Creating virtualenv...');
    if (!run(`${python} -m venv "${VENV_DIR}"`)) {
      warn('Failed to create virtualenv.');
      return;
    }
  }

  const pip = path.join(VENV_DIR, 'bin', 'pip');

  // Upgrade pip
  run(`"${pip}" install --upgrade pip`);

  // Install Python dependencies
  log('Installing Python dependencies (this may take a few minutes on RPi)...');
  if (!run(`"${pip}" install --no-cache-dir -r "${REQUIREMENTS}"`)) {
    warn('pip install failed. Some packages may need compilation tools.');
    return;
  }

  log('Python environment ready!');
}

// Never crash npm install
try {
  main();
} catch (err) {
  warn(`Unexpected error: ${err}`);
  warn('Python setup incomplete — plugin will retry on Homebridge restart.');
}
