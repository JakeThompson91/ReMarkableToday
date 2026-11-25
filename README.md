# Daily Newspaper for reMarkable

Generate a daily, broadsheet-style A5 PDF newspaper (**REMARKABLE TODAY**) from RSS feeds and random [xkcd](https://xkcd.com/) comics, then automatically push it to your reMarkable tablet via [`rmapi`](https://github.com/ddvk/rmapi).

## Features

- **Broadsheet A5 Layout**: Designed for comfortable reading on e-ink screens with a classic front-page masthead, two-column table of contents, and editorial typography (macOS Georgia or Linux Liberation Serif, with Unicode symbol fallback).
- **Tappable Navigation**: Every front-page section and headline links directly to the corresponding article, complete with PDF outline bookmarks and a **↑ Back to Front Page** button at the end of the edition.
- **Full-Text Extraction**: Fetches full articles from RSS feeds and automatically uses `trafilatura` to retrieve the complete web article when an RSS feed is truncated, while stripping duplicate headlines and trailing "related stories" noise.
- **Lead Images**: Finds and embeds article lead images (from RSS media tags, enclosures, inline `<img>` tags, or OpenGraph metadata), converting and resizing them cleanly for e-ink.
- **The Funnies**: Includes 3 random xkcd comics with their titles and hover captions on the back pages.
- **Automatic Cloud Upload & Cleanup**: Uploads each edition to your reMarkable tablet and automatically prunes editions older than 7 days both locally (`pdfs/`) and in reMarkable Cloud.
- **Containerized**: Ready-to-run multi-stage `Dockerfile` and `docker-compose.yml` bundling `rmapi`, Python dependencies, and serif/Unicode fonts.

## Default News Sources

- NPR News
- BBC World News
- Ars Technica
- TechCrunch
- The Verge

## Option 1: Run with Docker (Recommended)

The multi-stage [`Dockerfile`](Dockerfile) builds `rmapi` from source and packages Python + Liberation Serif / DejaVu fonts so no host Go or Python setup is required.

### Using Docker Compose

1. **Build the image:**

   ```bash
   docker compose build
   ```

2. **One-time reMarkable authentication & folder creation:**

   Run `rmapi` interactively inside the container (you will be prompted for a one-time code from [my.remarkable.com/device/browser/connect](https://my.remarkable.com/device/browser/connect); credentials are persisted in the `rmapi_config` Docker volume):

   ```bash
   docker compose run --rm newspaper rmapi
   docker compose run --rm newspaper rmapi mkdir "/Daily Newspaper"
   ```

3. **Generate today's newspaper and push to your tablet:**

   ```bash
   docker compose run --rm newspaper
   ```

   Generated PDFs are saved to `./pdfs/` on the host and uploaded to `/Daily Newspaper` on your reMarkable. To generate a PDF locally without uploading to reMarkable Cloud, pass `-e SKIP_UPLOAD=1`:

   ```bash
   docker compose run --rm -e SKIP_UPLOAD=1 newspaper
   ```

### Using `docker` CLI directly

```bash
# Build
docker build -t remarkable-today .

# Authenticate once & create the remote folder
docker run -it --rm -v rmapi_config:/config/rmapi remarkable-today rmapi
docker run --rm -v rmapi_config:/config/rmapi remarkable-today rmapi mkdir "/Daily Newspaper"

# Generate and push today's paper
docker run --rm \
  -e TZ=America/New_York \
  -v "$(pwd)/pdfs:/app/pdfs" \
  -v rmapi_config:/config/rmapi \
  remarkable-today
```

## Option 2: Local Setup (macOS / Linux)

### Prerequisites

- **Python 3.9+**
- **Go** (for building `rmapi`) or a prebuilt [`rmapi`](https://github.com/ddvk/rmapi) binary
- A reMarkable account synced with reMarkable Cloud

### 1. Clone the repository

```bash
git clone https://github.com/JakeThompson91/ReMarkableToday.git ~/daily-newspaper
cd ~/daily-newspaper
```

### 2. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Install and authenticate `rmapi`

Build `rmapi` (`~/go/bin/rmapi` by default):

```bash
git clone --depth 1 https://github.com/ddvk/rmapi.git /tmp/rmapi-build
cd /tmp/rmapi-build && go build -o ~/go/bin/rmapi .
cd ~/daily-newspaper
```

Link your reMarkable account once, then create the target folder on your tablet:

```bash
~/go/bin/rmapi
~/go/bin/rmapi mkdir "/Daily Newspaper"
```

### 4. Run

```bash
python3 make_paper.py
```

Generated PDFs are saved locally to `pdfs/Remarkable-Today-YYYY-MM-DD.pdf` and uploaded to `/Daily Newspaper` on your reMarkable tablet.

## Configuration

Settings can be customized via environment variables (in Docker or shell) or directly at the top of [`make_paper.py`](make_paper.py):

| Variable / Constant | Default (Local) | Default (Docker) | Description |
| --- | --- | --- | --- |
| `ARTICLES_PER_SECTION` | `4` | `4` | Maximum number of articles per feed |
| `NUM_XKCD` | `3` | `3` | Number of random xkcd comics in *The Funnies* |
| `KEEP_DAYS` | `7` | `7` | Number of recent editions to keep locally and on the tablet |
| `OUTPUT_DIR` | `~/daily-newspaper/pdfs` | `/app/pdfs` | Directory where generated PDFs are stored |
| `RMAPI_BIN` | `rmapi` on `PATH` or `~/go/bin/rmapi` | `/usr/local/bin/rmapi` | Path to the `rmapi` executable |
| `REMOTE_FOLDER` | `"/Daily Newspaper"` | `"/Daily Newspaper"` | Destination folder path in reMarkable Cloud |
| `SKIP_UPLOAD` | `false` | `false` | Set to `1` / `true` to generate the PDF locally without calling `rmapi` |
| `TZ` | System timezone | `UTC` (`America/New_York` in Compose) | Timezone used for the morning edition date |

News feeds are configured in the `FEEDS` dictionary at the top of [`make_paper.py`](make_paper.py).

## Scheduling Daily Delivery

### macOS (`launchd`)

Create `~/Library/LaunchAgents/com.user.daily-newspaper.plist` to generate and deliver the paper every morning at 6:30 AM (replace `<username>` with your macOS username):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.daily-newspaper</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/&lt;username&gt;/daily-newspaper/make_paper.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/&lt;username&gt;/daily-newspaper/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/&lt;username&gt;/daily-newspaper/launchd.err</string>
</dict>
</plist>
```

Load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.user.daily-newspaper.plist
```

### Linux (`cron` with Docker Compose)

Add an entry via `crontab -e` to run every morning at 6:30 AM:

```cron
30 6 * * * cd $HOME/daily-newspaper && docker compose run --rm newspaper >> $HOME/daily-newspaper/cron.log 2>&1
```
