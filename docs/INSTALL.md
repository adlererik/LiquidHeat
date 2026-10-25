# Beginner installation guide

Think of the server as the computer that stays on and does the work. Your other computer needs only a browser. Docker runs four separate pieces together: a database, a market-data/model worker, an API and a web server. You install the release **on the server**.

## 1. Check the server

The prebuilt bundle supports Linux x86-64 (also called amd64, including Intel and AMD CPUs). Check with `uname -m`: `x86_64` is supported. Recommended: 4 GB RAM, 2+ cores, 10 GB free storage plus growth/backup headroom. At installation the checks require 1,400 MiB available RAM and 6 GiB free disk. Debian with systemd is the verified platform. Other Linux distributions may work; they are not equally tested.

You need SSH access if the server has no keyboard/display. From your own computer, connect using `ssh YOUR_USER@YOUR_SERVER`; replace both placeholders. Everything below runs in that server shell. No exchange account, API key, credit card, registry login or paid service is required.

### Shared-server checks

Before adding anything, inspect the machine:

```bash
cat /etc/os-release
uname -m
free -h
df -h
ss -lntup
command -v docker && docker ps
command -v podman && podman ps
systemctl --type=service --state=running
ip -brief address
```

An administrator can also inspect existing firewall rules read-only with `sudo nft list ruleset` or the firewall's status command. Do not change them simply to install LiquidHeat. Inventory existing Docker contexts/projects with `docker context ls` and `docker compose ls` if available. An inaccessible daemon is not proof that Docker is absent.

The application uses an unused high port and its own Compose project, network and volume. Its installer never installs or restarts Docker, invokes host firewall commands, prunes volumes, alters a proxy or stops unrelated containers. An existing rootful Docker daemon can add its own managed network/firewall rules when creating a bridge network; review that policy on a shared host. Rootless Docker confines this networking to its namespace. **Do not replace an existing runtime on a shared server.** First understand it and use its existing supported access, or set up a separately isolated rootless daemon with the administrator.

## 2. Prepare Docker once

Already have working Docker Engine and Compose? Skip to step 3. Verify:

```bash
docker info
docker compose version
```

If Docker is missing, prefer [Docker's official rootless installation guide](https://docs.docker.com/engine/security/rootless/) on a compatible systemd Linux host. Docker Engine Community and Compose are open-source; Docker Desktop is unnecessary. Follow the official distribution-specific instructions, not an arbitrary third-party script.

On a **fresh Debian server** with no conflicting runtime, typical prerequisites are:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl python3 uidmap dbus-user-session slirp4netns
```

On that fresh host, download Docker's official rootless installer, inspect it, and run it as your regular user:

```bash
curl -fsSL https://get.docker.com/rootless -o install-docker-rootless.sh
less install-docker-rootless.sh   # Press q to leave the viewer.
sh install-docker-rootless.sh
```

This downloads Docker's free official binaries into your user account. Do not run it as root. Follow its prerequisite messages if it stops; some systems need administrator configuration of subordinate UID/GID ranges. Do not alter existing mappings blindly or follow instructions to disable a daemon serving other workloads. The current [official guide](https://docs.docker.com/engine/security/rootless/) is the authority for kernel/distribution differences.

After a working rootless installation:

```bash
export PATH="$HOME/bin:$PATH"
docker context use rootless
systemctl --user enable --now docker
sudo loginctl enable-linger "$USER"
docker info
docker compose version
```

`enable-linger` keeps your user services running after logout and lets them start after reboot. This is deliberate administrator setup; LiquidHeat itself does not run sudo. If Compose is missing, install its free [official CLI plugin](https://docs.docker.com/compose/install/linux/); do not install a paid desktop application. Keep the Docker CLI's path available; the project startup unit records it.

If you deliberately use an existing rootful Docker daemon, make sure its service already starts at boot. Access to a rootful Docker socket is highly privileged. LiquidHeat does not change group membership or daemon settings. For regular-user backup timers at boot, that user's systemd manager also needs lingering. The installer can skip timers using `--no-startup`, but you must configure an equivalent startup/backup arrangement yourself before relying on unattended operation.

## 3. Download the full release

The download commands need `curl`, `tar`, `sha256sum` and Python 3. On Debian, install missing prerequisites with `sudo apt-get install -y curl python3`. Alternatively, download the two release assets in your browser and copy them to your server. Use the commands in the main README. Choose **LiquidHeat-v1.1.0-linux-amd64.tar.gz**, not GitHub's automatically generated source archive, if you want prebuilt images. Download `SHA256SUMS` into the same directory and run `sha256sum --check SHA256SUMS`. An `OK` result detects corruption against the published checksum; it is not an independent code-signing identity.

Extract it, change into `LiquidHeat`, and run `./install.sh`. No build or registry login is needed. Keep the installation folder: it contains Compose, your private generated settings, operation helpers and backups. Do not put it in a temporary directory.

## 4. Choose access and open the page

Find the server's LAN IPv4 with `ip -brief address`. Use its private address on your local network, not your router's public address. The installer asks for this address. An empty answer uses `127.0.0.1`, accessible only from the server itself or through a tunnel.

Port `18443` serves **HTTP**, despite its digits. If occupied, choose another unused port:

```bash
./install.sh --bind YOUR_SERVER_LAN_IP --port 18444
```

The installer refuses public addresses and `0.0.0.0`, generates `.env` with a unique database password, verifies bundled image checksums, loads images and waits up to 15 minutes for healthy containers. It adds project-only user startup/backup units where the session supports them. A message about lingering or Docker startup needs to be resolved before unattended operation. Repeating the installer with the same settings preserves its password and volume.

Open the printed URL in a browser on the same LAN. If the server firewall already blocks that port, LiquidHeat does not change it. Use an SSH tunnel without opening a firewall port:

```bash
ssh -N -L 18443:127.0.0.1:18443 YOUR_USER@YOUR_SERVER
```

This tunnel example assumes you installed with `--bind 127.0.0.1`. Open `http://127.0.0.1:18443` on your own computer while the SSH command stays open.

## 5. Wait for genuine data

A responsive page can precede a complete chart. Public historical backfills, current OI/mark pairing and venue rate limits determine when states appear. Some feeds may be unavailable from your region; no paid fallback or artificial market history is inserted. Backfill requests up to 30 days where endpoints genuinely allow it. Historical aggregate-trade profiles and reported liquidation coverage are not a complete 30-day archive.

Click **Fit price** to bring visible candles into view. Read the guide before treating any bright band as meaningful. Learning starts collecting future evidence automatically; it is not ready on the first day.

## 6. Verify and maintain

```bash
./liquidheat.sh status
./liquidheat.sh doctor
./liquidheat.sh test
./liquidheat.sh backup
./liquidheat.sh verify-backup
systemctl --user list-timers
```

Close your browser and disconnect SSH: the server keeps running. Existing daemon boot configuration, lingering and generated project units provide startup; container policies are `unless-stopped`. A deliberately stopped container stays stopped until you start it. We tested container recovery, not an intrusive host reboot of a shared machine.

Never run `docker compose down -v` if you want to keep history. Never run global Docker prune as routine maintenance. The stop helper preserves the project's database. See [operations](../OPERATIONS.md) for backup, upgrades and space limits.

## Common problems

| Symptom | What to check |
|---|---|
| `docker: command not found` | Docker prerequisites and your CLI PATH |
| Cannot connect to Docker | Correct context/socket and existing user access; do not restart the global daemon blindly |
| Compose missing | Install the official Compose CLI plugin |
| Port unavailable | Choose a different high port; do not stop the existing listener |
| Address not available | Bind to an address actually assigned to the server |
| Empty chart | Feed coverage, engine logs, exchange connectivity, and enough paired historical inputs |
| Candles off screen | **Fit price**, then drag vertically or use **Price + / −** |
| Learning stays Collecting | Eligible/positive counts, audit gaps and fixed future-test requirements |
| Storage paused | Inspect disk and database size; preserve a backup before deciding what to remove |
| Works before logout only | Rootless Docker startup, lingering and user units |

For help, share versions and a redacted error. Do not post `.env`, database dumps, full private diagnostics, server addresses or shell history.
