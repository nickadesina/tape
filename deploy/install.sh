#!/usr/bin/env bash
# One-time setup on the Lightsail box (Ubuntu). Run from the repo checkout as root.
set -euo pipefail
id tape >/dev/null 2>&1 || useradd --system --home /opt/tape --shell /usr/sbin/nologin tape
mkdir -p /opt/tape /etc/tape
rsync -a --delete --exclude .venv --exclude site --exclude .git ./ /opt/tape/
python3 -m venv /opt/tape/.venv
/opt/tape/.venv/bin/pip install -q -e "/opt/tape[s3]"
chown -R tape:tape /opt/tape
[ -f /etc/tape/tape.env ] || { cp deploy/tape.env.example /etc/tape/tape.env; echo "edit /etc/tape/tape.env, then re-run"; exit 1; }
sudo -u tape bash -c 'set -a; . /etc/tape/tape.env; /opt/tape/.venv/bin/tape migrate'
cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tape-indexer.service tape-metrics.timer tape-daily.timer tape-stress.timer
systemctl --no-pager status tape-indexer.service | head -5
