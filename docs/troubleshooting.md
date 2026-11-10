# Troubleshooting

[← Back to the README](../README.md) · Previous: [Administration](administration.md)

## Where to look first

- **Settings → Overview → Server status:** memory, swap, load and, for each stream, the mode (direct, remux, transcode), its reason, the **conversion speed** (below ×1 the CPU cannot keep up) and the last ffmpeg messages.
- **⋯ → File information** on a movie or episode lists the file's tracks (codecs, languages, HDR…).
- **The ⚙ menu of the player** shows how the current video is played and why.
- **Logs:** `journalctl -u seance -f` (Docker: `docker logs -f noxa`). Set `SEANCE_DEBUG=1` in `/etc/seance/seance.env` and restart to log the ffmpeg commands.
- **Updates:** `/var/log/seance-update.log`.

## Common problems

| Symptom | What to do |
|---|---|
| "Linux forbids SMB mounts…" | Unprivileged LXC container: see [Access to your media](installation.md#access-to-your-media) |
| "Mount refused by the system…" | Privileged container without the feature: `pct set <ID> -features mount=cifs,nesting=1`, then restart it |
| Share "Not mounted" | **Remount** button; log: `journalctl -u seance-mountd -n 50` |
| "Folder not accessible" during a scan | The mount is not visible in the container: `ls /mnt/movies`, then `runuser -u seance -- ls /mnt/movies` |
| A movie is matched to the wrong title | Title page → **⋯ → Identify…**, or rename it `Title (Year)` |
| Episodes "not recognized" | Add `S01E02` or ` - 02` to the file name |
| "Too many simultaneous transcodes" | Raise the limit if the CPU allows it (*Conversion and audio → Transcoding*), set up a graphics card, or use H.264 sources |
| Playback does not start | `journalctl -u seance -f` while trying; `SEANCE_DEBUG=1` shows the ffmpeg commands |
| Stuttering, swap full (LXC) | A 4K transcode uses 0.5 to 0.7 GB: plan 2 GB of RAM per simultaneous conversion and 1 to 2 GB of swap |
| Colours washed out on an HDR movie | Check that tone mapping is not off (*Conversion and audio → Transcoding → HDR movies*) |
| Slow pages | Requests over 5 s are logged ("Requête lente : GET /api/…", the server log is in French): `journalctl -u seance \| grep 'Requête lente'`. On a distant server (high latency), the first load is the slowest; later loads come from the device cache |
| Old interface after an update | Reload the page: the interface code changes address with every version |
| Cannot install the web app / no downloads / no notifications | These need HTTPS (or the desktop / Android app on a local `http://` server) |
| Lost admin 2FA | `sudo noxa reset-2fa <username>` on the server |
| Lost setup code | `cat /var/lib/seance/setup-code.txt` (only until the first account exists) |
| Linked server "unreachable" | Only one of the two needs to be reachable on port 8421; it must not go through an HTTP(S) proxy. See [Network](linked-servers.md#network) |
| Cold storage copy failed | The error is shown with its reason on *Cold storage*; **Retry** once fixed. SFTP: confirm the key fingerprint again if the server was reinstalled |

## Asking for help

Open an [issue](https://github.com/xenix13/Noxa/issues/new/choose) with:

- the Noxa version (*Settings → Overview*) and the install method (script, `.deb`, Docker, Proxmox LXC);
- what you did, what you expected and what happened;
- the relevant lines of `journalctl -u seance` (remove addresses, names and tokens);
- for playback problems, the *File information* of the title and the device / browser used.

Security problems should **not** be reported in public issues: see [SECURITY.md](../SECURITY.md).

---

Next: [Development →](development.md)
