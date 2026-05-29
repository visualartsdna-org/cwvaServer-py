# CWVA Server — Installation & Deployment Options

Maintained in `~/cwva/`. Applies to any cwva-compatible server instance.

---

## Overview

Three deployment options are available, ranked by fit for ongoing production use:

| Option | Best for | Update method |
|---|---|---|
| **Git clone** | Production, ongoing development | `git pull` |
| **rsync** | Pre-git deployment, quick sync | re-run rsync |
| **zip** | One-time transfer only | re-zip and re-upload |

---

## Option 1 — Git Clone (Recommended)

The cleanest path once the project is on GitHub. Gives version history,
rollback, and one-command updates.

### Initial deployment

```bash
# On the target server (GCP Debian or WSL)
git clone https://github.com/you/cwva-server.git ~/cwva/main
cd ~/cwva/main
pip install -r requirements.txt

# Create config from template
cp config/serverCwva.example.rson serverCwva.rson
# Edit serverCwva.rson for this environment

# Set environment variables
export GCP_BUCKET=your-bucket-name
export ANTHROP_KEY=your-anthropic-key

# Run
python main.py -cfg serverCwva.rson
```

### Updating

```bash
git pull && sudo systemctl restart cwva
```

---

## Option 2 — rsync (Good Bridge Before Git)

Fast and incremental — only changed files transfer. Safe to re-run;
never overwrites rson config files on the target.

### Initial deployment

```bash
# From WSL development machine
rsync -av --exclude='*.rson' \
          --exclude='.loadLock' \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          --exclude='/stage/' \
          --exclude='/temp/' \
          ~/cwva/main/ user@gcp-instance:~/cwva/main/
```

Then on the target server:

```bash
cd ~/cwva/main
pip install -r requirements.txt
cp config/serverCwva.example.rson serverCwva.rson
# Edit serverCwva.rson for this environment
export GCP_BUCKET=your-bucket-name
export ANTHROP_KEY=your-anthropic-key
python main.py -cfg serverCwva.rson
```

### Updating

Re-run the rsync command — only changed files are transferred:

```bash
rsync -av --exclude='*.rson' \
          --exclude='.loadLock' \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          --exclude='/stage/' \
          --exclude='/temp/' \
          ~/cwva/main/ user@gcp-instance:~/cwva/main/
sudo systemctl restart cwva
```

---

## Option 3 — Zip (Simple, Not Recommended for Updates)

Works for a one-time transfer but updating requires re-zipping and
re-uploading the whole project every time.

### Create zip (on WSL)

```bash
cd ~/cwva
zip -r cwva-main.zip main/ \
    --exclude "*.rson" \
    --exclude "*__pycache__*" \
    --exclude "*.pyc" \
    --exclude "main/stage/*" \
    --exclude "main/temp/*"
```

### Deploy

```bash
# Copy to target
scp cwva-main.zip user@gcp-instance:~/

# On target server
cd ~
unzip cwva-main.zip
cd ~/cwva/main
pip install -r requirements.txt
cp config/serverCwva.example.rson serverCwva.rson
# Edit serverCwva.rson for this environment
export GCP_BUCKET=your-bucket-name
export ANTHROP_KEY=your-anthropic-key
python main.py -cfg serverCwva.rson
```

---

## Production: systemd Service

Run the server as a managed systemd service so it starts automatically
on boot and restarts on crash. Applies to any deployment option.

### Create the service file

```bash
sudo nano /etc/systemd/system/cwva.service
```

```ini
[Unit]
Description=CWVA Python Server
After=network.target

[Service]
User=your-user
WorkingDirectory=/home/your-user/cwva/main
Environment=GCP_BUCKET=your-bucket-name
Environment=ANTHROP_KEY=your-anthropic-key
ExecStart=/usr/bin/python3 main.py -cfg serverCwva.rson
Restart=on-failure
StandardOutput=append:/home/your-user/cwva/main/cwva.log
StandardError=append:/home/your-user/cwva/main/cwva_err.log

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable cwva
sudo systemctl start cwva
sudo systemctl status cwva
```

### Common commands

```bash
sudo systemctl start cwva       # start
sudo systemctl stop cwva        # stop
sudo systemctl restart cwva     # restart (e.g. after git pull)
sudo systemctl status cwva      # check running status
journalctl -u cwva -f           # follow systemd journal
```

---

## Config and Secrets

### serverCwva.rson

Never committed to git. Created from `config/serverCwva.example.rson`
on each new deployment. Key fields to update per environment.

`metacontent/model/` and `metacontent/vocab/` are populated automatically
from `referenceModel` on first startup — no action needed for most deployments.
Advanced users can optionally clone `cwvaMetacontent` into `~/cwva/metacontent/`
for version control or participation in ontology development.

```json
{
  "port": 80,
  "host": "http://your-server-ip-or-domain:80",
  "dir": ".",
  "model":      "/home/user/cwva/metacontent/model",
  "vocab":      "/home/user/cwva/metacontent/vocab",
  "data":       "/home/user/cwva/content/data",
  "tags":       "/home/user/cwva/content/tags",
  "documents":  "/home/user/cwva/content/documents",
  "images":     "/home/user/cwva/content/images",
  "thumbnails": "/home/user/cwva/thumbnails",
  "domain":     "http://visualartsdna.org",
  "sparql":     true,
  "agentUrl":   "http://localhost:8090"
}
```

### ~/.secrets.rson

Token phrase for `/cmd` authentication. Never committed.
Must exist on every server and on any machine running `cwva_cmd.py`.

```json
{
  "secrets": {
    "phrase": "your-secret-phrase"
  }
}
```

### Environment variables

Set in the shell before running, or in the `[Service]` section of
the systemd unit file:

| Variable | Purpose |
|---|---|
| `GCP_BUCKET` | GCP bucket name for TTL/image/document sync |
| `ANTHROP_KEY` | Anthropic API key (only needed if `agentUrl` configured) |

---

## Admin Tool

`~/cwva/main/tools/cwva_cmd.py` — standalone utility for server administration.
No dependency on the server codebase. Reads `~/.secrets.rson` for token generation.

```bash
# With explicit host and port
python ~/cwva/main/tools/cwva_cmd.py refresh  -H http://192.168.1.71 -p 8081
python ~/cwva/main/tools/cwva_cmd.py cestfini -H https://visualartsdna.org
python ~/cwva/main/tools/cwva_cmd.py status   -H http://192.168.1.71 -p 8081

# With config file
python ~/cwva/main/tools/cwva_cmd.py refresh --cfg ~/cwva/main/config/serverCwva.rson

# With env var (set once per session)
export CWVA_CFG=~/cwva/main/config/serverCwva.rson
python ~/cwva/main/tools/cwva_cmd.py refresh
python ~/cwva/main/tools/cwva_cmd.py status
```

---

## Recommended Deployment Path

1. **Now**: use rsync to get deployed quickly on GCP
2. **In parallel**: initialize git repo, push to GitHub
3. **Going forward**: `git pull && sudo systemctl restart cwva` for all updates

---

*Last updated: May 2026*
