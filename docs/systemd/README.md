# systemd (optional)

If you're on a systemd-based Linux and want the ingestion daemon to start on boot:

1) Copy `docs/systemd/tripscore-compose.service` to `~/.config/systemd/user/tripscore-compose.service`
2) Edit `WorkingDirectory` and `DOCKER_CONFIG` paths to match your machine
3) Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now tripscore-compose.service
systemctl --user status tripscore-compose.service
```

For logs:

```bash
journalctl --user -u tripscore-compose.service -f
```

