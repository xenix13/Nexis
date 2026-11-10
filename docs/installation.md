# Installation

[← Back to the README](../README.md)

Nexis is a single Python program plus ffmpeg. Choose the method that matches your setup; they all give the same server, with the same features and the same update mechanism.

| Method | Best for | Updates |
|---|---|---|
| [Proxmox LXC in one command](#proxmox-lxc-in-one-command) | A Proxmox host, the easiest way | From the interface |
| [Install script](#install-script-debian--ubuntu) | An existing Debian / Ubuntu VM, LXC or PC | From the interface |
| [`.deb` package](#deb-package) | You prefer `apt` to manage software | `apt install` the new package, or from the interface |
| [Docker](#docker) | You already run everything in containers | `docker compose pull` |

**Contents:** [Requirements](#requirements) · [Proxmox LXC](#proxmox-lxc-in-one-command) · [Install script](#install-script-debian--ubuntu) · [.deb](#deb-package) · [Docker](#docker) · [Access to your media](#access-to-your-media) · [Graphics card](#hardware-transcoding-graphics-card) · [HTTPS and remote access](#https-and-remote-access) · [Updates](#updates) · [Configuration file](#configuration-file) · [Uninstall](#uninstall)

## Requirements

- **System:** Ubuntu 22.04 / 24.04 or Debian 12 / 13, amd64 or arm64 (the Docker image runs anywhere Docker does).
- **Software:** Python 3.9 or later and ffmpeg. The installers add them for you. Nexis has **no Python dependencies**: no `pip install`, no virtualenv.
- **CPU:** 2 to 4 cores. Playing files as they are (direct play) or remuxing costs almost nothing; only conversions are heavy, about 1 to 2 cores for a 1080p stream without a graphics card.
- **Memory:** 1 GB is enough without conversions. Plan about 2 GB per simultaneous 4K conversion, and 1 to 2 GB of swap.
- **Disk:** 8 GB for the system, the image cache and temporary video segments. Your media stays where it is.
- **Ports:** `8420` (web interface and apps). `8421` only if you [link several servers](linked-servers.md). UDP `1900` only for [DLNA](more-libraries.md#dlna-server).

## Proxmox LXC in one command

On the **Proxmox host**, as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/proxmox-lxc.sh)"
```

The script downloads the Ubuntu 24.04 template, creates the container (4 cores, 4 GB RAM, 2 GB swap, 16 GB disk, DHCP on `vmbr0`, starts at boot), installs Nexis inside it and prints the address and the **setup code**.

The container is **privileged** by default with the `mount=cifs` feature, so that Nexis can mount your NAS shares itself (see [Access to your media](#access-to-your-media)). Every setting can be changed with variables, for example:

```bash
CTID=120 MEMORY=6144 STORAGE=local-zfs PRIVILEGED=0 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/proxmox-lxc.sh)"
```

| Variable | Default | Meaning |
|---|---|---|
| `CTID` | next free id | Container id |
| `CT_HOSTNAME` | `nexis` | Host name |
| `STORAGE` / `TEMPLATE_STORAGE` | `local-lvm` / `local` | Where the disk and the template go |
| `BRIDGE` | `vmbr0` | Network bridge |
| `CORES`, `MEMORY`, `SWAP`, `DISK` | `4`, `4096` MB, `2048` MB, `16` GB | Resources |
| `PRIVILEGED` | `1` | `0` for an unprivileged container (mount shares on the host instead) |
| `SEANCE_BRANCH` | `main` | `test` to install the test channel |

<details>
<summary>Creating the container by hand instead</summary>

```bash
pveam update && pveam available | grep ubuntu-24.04
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
pct create 120 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname nexis --cores 4 --memory 1024 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --features nesting=1 --onboot 1
pct start 120
pct exec 120 -- bash -c "apt-get update && apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash"
```

`nesting=1` lets the service keep its systemd sandbox; without it the installer sets up a lighter version of the service.
</details>

## Install script (Debian / Ubuntu)

In a VM, an LXC container or on a physical machine, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash
```

The script:

1. installs Python, git, ffmpeg, openssl and the SMB tools (`cifs-utils`, `smbclient`);
2. installs Nexis in `/opt/seance` and creates the system user `seance` (Nexis never runs as root);
3. creates the services `seance.service` (Nexis) and `seance-mountd.service` (a tiny root helper that can only mount network shares read-only under `/mnt/seance`);
4. detects an NVIDIA, Intel or AMD graphics card and gives the service access to it;
5. prints the address and the **setup code**.

Running the same command again updates Nexis and keeps your data (`/var/lib/seance`) and configuration (`/etc/seance/seance.env`).

> Internally the program is called `seance` (its Python package, user, service and folders). "Nexis" is the product name.

## .deb package

Every [release](https://github.com/xenix13/Nexis/releases/latest) includes `nexis_<version>_all.deb`. On Debian 12 / 13 or Ubuntu 22.04 / 24.04:

```bash
apt install ./nexis_4.0.0_all.deb
```

apt pulls in ffmpeg, Python and the SMB tools. The package prepares the system exactly like the script (user, configuration file, services, graphics card access). `apt remove nexis` keeps your data and configuration; `apt purge nexis` deletes them. To build the package yourself: `deploy/build-deb.sh`.

## Docker

The image `ghcr.io/xenix13/nexis` is published for **amd64 and arm64** with every release. Tags: `latest` (latest stable release), `4.0.0` (a fixed version) and `test` (rebuilt from the test branch).

```bash
curl -fsSLO https://raw.githubusercontent.com/xenix13/Nexis/main/docker-compose.yml
# edit the volumes so that they point at your media
mkdir -p data && chown 1000:1000 data     # Nexis runs as uid 1000 inside the container
docker compose up -d
docker compose logs nexis                 # the setup code of the first start
```

The [`docker-compose.yml`](../docker-compose.yml) file is commented. The important parts:

```yaml
services:
  nexis:
    image: ghcr.io/xenix13/nexis:latest
    restart: unless-stopped
    ports:
      - "8420:8420"
      - "8421:8421"          # only for linked servers
    environment:
      SEANCE_COOKIE_SECURE: "0"   # "1" behind an HTTPS reverse proxy
      SEANCE_TRUST_PROXY: "0"     # "1" behind a reverse proxy
    volumes:
      - ./data:/data
      - /mnt/movies:/media/movies:ro   # one line per media folder, read-only
      - /mnt/shows:/media/shows:ro
```

- **Media:** mount each folder **read-only** and create libraries on `/media/...`. Network shares are mounted on the Docker host; the *Network shares* page of Nexis is not available in Docker.
- **Update:** `docker compose pull && docker compose up -d`.
- **Graphics card:** uncomment `devices: /dev/dri` and `group_add` (Intel / AMD), or the `nvidia` reservation (requires the NVIDIA Container Toolkit on the host).
- **DLNA:** use `network_mode: host`, otherwise TVs cannot see the server.
- **Backups on another disk:** add a volume such as `/mnt/nas/nexis:/backups` and choose `/backups` in *Settings → Maintenance → Backups*.

## Access to your media

Nexis only needs to **read** your media. It never writes to these folders, and the Libraries page warns you if a folder is writable.

### Method 1: let Nexis mount your NAS (VM or privileged LXC)

*Settings → Libraries → Network shares* mounts SMB / CIFS shares without the command line: type the NAS address and credentials, pick a share, and Nexis mounts it **read-only** under `/mnt/seance/<name>`, remounts it at boot and every 5 minutes if it drops, and offers to create the library. Passwords are stored in `/etc/seance/smb/` (readable by root only), never in the Nexis database.

> **Linux forbids SMB mounts inside an unprivileged LXC container.** Method 1 needs a **privileged** container with `mount=cifs`:
> - new container: `pct create … --unprivileged 0 --features mount=cifs,nesting=1` (the default of the one-command installer);
> - existing container: *Backup* → *Restore* with "Unprivileged" unticked, then `pct set <ID> -features mount=cifs,nesting=1` and restart.
>
> A privileged container is less isolated from the host: keep it for a trusted network. Otherwise use method 2.

### Method 2: mount on the Proxmox host (unprivileged LXC)

1. On the host, store the credentials:

   ```bash
   cat > /root/.smb-nas <<'EOF'
   username=media
   password=YOUR_PASSWORD
   EOF
   chmod 600 /root/.smb-nas
   mkdir -p /mnt/nas/movies /mnt/nas/shows
   ```

2. Add the shares to `/etc/fstab` (`file_mode` lets the container's user read them):

   ```
   //192.168.1.5/Movies /mnt/nas/movies cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   //192.168.1.5/Shows  /mnt/nas/shows  cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   ```

   ```bash
   apt install -y cifs-utils && systemctl daemon-reload && mount -a && ls /mnt/nas/movies
   ```

3. Pass the folders to the container (replace `120` with its id) and restart it:

   ```bash
   pct set 120 -mp0 /mnt/nas/movies,mp=/mnt/movies,ro=1
   pct set 120 -mp1 /mnt/nas/shows,mp=/mnt/shows,ro=1
   pct reboot 120
   ```

Your media is now in `/mnt/movies` and `/mnt/shows` inside the container. The install script tells you if the `seance` user cannot read them.

### Local disks and NFS

Any folder the `seance` user can read works: a local disk, a USB drive, an NFS mount set up in `/etc/fstab`. Mount it read-only when you can.

## Hardware transcoding (graphics card)

A graphics card is **optional**. It only matters when a file has to be converted, and lets one server handle many more conversions at once. Choose it in *Settings → Conversion and audio → Transcoding → Graphics card*, then **Test and save**: Nexis runs a real encode and only saves the engine if it works. If the card refuses a file during playback, the CPU takes over without interrupting the video.

| Engine | Cards | Notes |
|---|---|---|
| **NVIDIA NVENC** | GeForce GTX 10xx and later | Up to 8 simultaneous encodes on consumer cards (NVIDIA limit). GTX 10xx (Pascal) cards need the **580 driver branch**: 590 and later dropped them. |
| **Intel / AMD (VAAPI)** | Intel with integrated graphics or Arc, AMD Radeon | Device `/dev/dri/renderD128` (selectable if there are several). |

The card is only used for conversions: direct play and remux do not need it. Resizing and burning image subtitles still run on the CPU.

### NVIDIA card in a Proxmox LXC container

1. On the host: install the kernel headers (`apt install proxmox-default-headers build-essential`), then the NVIDIA driver (`NVIDIA-Linux-x86_64-580.xx.run`). Accept disabling `nouveau` and reboot. `nvidia-smi` must show the card.
2. Still on the host, make sure `/dev/nvidia-uvm` exists at boot:

   ```bash
   echo -e "nvidia\nnvidia_uvm" > /etc/modules-load.d/nvidia.conf
   echo 'KERNEL=="nvidia_uvm", RUN+="/usr/bin/nvidia-modprobe -c0 -u"' > /etc/udev/rules.d/70-nvidia.rules
   ```

   Reboot and note the major numbers with `ls -l /dev/nvidia*` (195 for `nvidia0` / `nvidiactl`, another one for `nvidia-uvm`).
3. In `/etc/pve/lxc/<id>.conf` (container stopped), replacing `5xx` with the `nvidia-uvm` number:

   ```
   lxc.cgroup2.devices.allow: c 195:* rwm
   lxc.cgroup2.devices.allow: c 5xx:* rwm
   lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
   lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
   lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
   lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
   lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
   ```

4. In the container, install **exactly the same driver version** without the kernel module: `sh NVIDIA-Linux-x86_64-580.xx.run --no-kernel-module`. `nvidia-smi` must show the card.
5. Run the Nexis installer again: it detects the card and gives the service access to it. Then pick **NVIDIA NVENC** and **Test and save**.

### Intel or AMD in a Proxmox LXC container

Same idea with `/dev/dri`: add `lxc.cgroup2.devices.allow: c 226:* rwm` and `lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir` to the container configuration, then install `intel-media-va-driver-non-free` (Intel) or `mesa-va-drivers` (AMD) inside the container and run the Nexis installer again.

## HTTPS and remote access

On your home network, `http://<ip>:8420` is all you need. To watch from outside, put Nexis behind an **HTTPS reverse proxy** (Caddy, nginx, Nginx Proxy Manager, Traefik…). HTTPS is also what browsers require for installing the web app, offline downloads and push notifications.

Then tell Nexis it is behind a proxy, in `/etc/seance/seance.env` (Docker: the `environment` section):

```
SEANCE_COOKIE_SECURE=1
SEANCE_TRUST_PROXY=1
```

and restart it (`systemctl restart seance`). A minimal Caddy example:

```
media.example.com {
    reverse_proxy 192.168.1.30:8420
}
```

With nginx, add `proxy_read_timeout 120s;` and `proxy_buffering off;` (needed for the live updates of *Watch together*).

> Do not put the linked-servers port (8421) behind an HTTP proxy: it uses its own pinned TLS certificate. See [Linked servers](linked-servers.md#network).

## Updates

**From the interface:** *Settings → Maintenance → Update*. Nexis checks for a new version every 6 hours (**Check for updates** forces it) and shows an icon in the top bar to administrators when one is available. The update runs the installer from the repository and branch Nexis was installed from (stored in `/etc/seance/source`), then restarts the service: one to two minutes, and playing videos are interrupted. A progress bar follows each step, and the page reloads by itself. The log is kept in `/var/log/seance-update.log`.

**By hand:** run the install command again. Docker: `docker compose pull && docker compose up -d`.

**Update channel:** *Stable* (the `main` branch, released versions) or *Test* (the `test` branch, new features being validated), on the same page. New features land on *Test* first, then on *Stable*. Switching back to *Stable* and updating returns the server to the latest stable version. To install the test channel directly:

```bash
curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/test/deploy/install.sh | SEANCE_BRANCH=test bash
```

Apps (desktop, Android, Android TV) update themselves from your server; see [Apps](apps.md#updates).

## Configuration file

Most settings live in the interface. A few server-level options are environment variables, set in `/etc/seance/seance.env` (then `systemctl restart seance`) or in the `environment` section of Docker:

| Variable | Default | Meaning |
|---|---|---|
| `SEANCE_PORT` | `8420` | Web port |
| `SEANCE_HOST` | `0.0.0.0` | Listening address |
| `SEANCE_DATA` | `/var/lib/seance` (Docker: `/data`) | Data folder (database, caches, subtitles, backups) |
| `SEANCE_COOKIE_SECURE` | `0` | `1` when Nexis is reached over HTTPS |
| `SEANCE_TRUST_PROXY` | `0` | `1` behind a reverse proxy (real client IP from `X-Forwarded-For`) |
| `SEANCE_FED_PORT` | `8421` | Linked-servers port |
| `SEANCE_DEBUG` | `0` | `1` logs the ffmpeg commands and more details |

Logs: `journalctl -u seance -f` (Docker: `docker logs -f nexis`).

## Uninstall

- Script install: `systemctl disable --now seance seance-mountd`, then remove `/opt/seance`, `/etc/seance`, `/var/lib/seance` (your data) and the `seance` user.
- `.deb`: `apt purge nexis`.
- Docker: `docker compose down` and delete the `data` folder.

Your media folders are never touched.

---

Next: [Getting started →](getting-started.md)
