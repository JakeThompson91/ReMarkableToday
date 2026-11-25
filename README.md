# Daily Newspaper for reMarkable

Generate a daily, broadsheet-style A5 PDF newspaper (**REMARKABLE TODAY**) from RSS feeds and random [xkcd](https://xkcd.com/) comics, then automatically push it to your reMarkable tablet via [`rmapi`](https://github.com/ddvk/rmapi).

## Features

- **Broadsheet A5 Layout**: Designed for comfortable reading on e-ink screens with a classic front-page masthead, two-column table of contents, and editorial typography (uses macOS Georgia with Arial Unicode fallback, or falls back to Times on other systems).
- **Tappable Navigation**: Every front-page section and headline links directly to the corresponding article, complete with PDF outline bookmarks and a **↑ Back to Front Page** button at the end of the edition.
- **Full-Text Extraction**: Fetches full articles from RSS feeds and automatically uses `trafilatura` to retrieve the complete web article when an RSS feed is truncated, while stripping duplicate headlines and trailing "related stories" noise.
- **Lead Images**: Finds and embeds article lead images (from RSS media tags, enclosures, inline `<img>` tags, or OpenGraph metadata), converting and resizing them cleanly for e-ink.
- **The Funnies**: Includes 3 random xkcd comics with their titles and hover captions on the back pages.
- **Automatic Cloud Upload & Cleanup**: Uploads each edition to your reMarkable tablet and automatically prunes editions older than 7 days both locally (`pdfs/`) and in reMarkable Cloud.

## Default News Sources

- NPR News
- BBC World News
- Ars Technica
- TechCrunch
- The Verge

## Prerequisites

- **Python 3.9+**
- **Go** (for installing `rmapi`) or a prebuilt [`rmapi`](https://github.com/ddvk/rmapi) binary
- A reMarkable account synced with reMarkable Cloud

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/JakeThompson91/ReMarkableToday.git ~/daily-newspaper
cd ~/daily-newspaper
```

### 2. Install Python dependencies

```bash
pip3 install beautifulsoup4 feedparser fpdf2 Pillow trafilatura
```

### 3. Install and authenticate `rmapi`

Install `rmapi` (the script expects it at `~/go/bin/rmapi` by default):

```bash
go install github.com/ddvk/rmapi@latest
```

Run `rmapi` once to link your reMarkable account (you will be prompted for a one-time code from [my.remarkable.com/device/browser/connect](https://my.remarkable.com/device/browser/connect)), then create the target folder on your tablet:

```bash
~/go/bin/rmapi
~/go/bin/rmapi mkdir "/Daily Newspaper"
```

## Usage

Generate today's edition and push it to your reMarkable tablet:

```bash
python3 make_paper.py
```

Generated PDFs are saved locally to `pdfs/Remarkable-Today-YYYY-MM-DD.pdf` and uploaded to `/Daily Newspaper` on your reMarkable tablet.

## Configuration

All settings are defined at the top of [`make_paper.py`](make_paper.py):

| Constant | Default | Description |
| --- | --- | --- |
| `FEEDS` | 5 feeds | Dictionary mapping section names to RSS feed URLs |
| `ARTICLES_PER_SECTION` | `4` | Maximum number of articles per feed |
| `NUM_XKCD` | `3` | Number of random xkcd comics in *The Funnies* |
| `KEEP_DAYS` | `7` | Number of recent editions to keep locally and on the tablet |
| `OUTPUT_DIR` | `~/daily-newspaper/pdfs` | Local directory where generated PDFs are stored |
| `RMAPI_BIN` | `~/go/bin/rmapi` | Path to the `rmapi` executable |
| `REMOTE_FOLDER` | `"/Daily Newspaper"` | Destination folder path in reMarkable Cloud |

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

### Linux (`cron`)

Add an entry via `crontab -e` to run every morning at 6:30 AM:

```cron
30 6 * * * /usr/bin/python3 $HOME/daily-newspaper/make_paper.py >> $HOME/daily-newspaper/cron.log 2>&1
```

## Notes

- **Fonts on Linux/Windows**: On macOS, the script loads `Georgia` and `Arial Unicode` from `/System/Library/Fonts/Supplemental`. On other platforms it automatically falls back to built-in `Times` (you can adjust `setup_unicode_fonts()` in `make_paper.py` to point to local TTF fonts if you need full Unicode glyph coverage).
- **Sharing / Publishing**: Avoid committing generated `pdfs/`, `launchd.log`, `launchd.err`, or `.DS_Store` when pushing to GitHub.
