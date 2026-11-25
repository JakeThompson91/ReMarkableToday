import datetime
import html
import json
import random
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import feedparser
from fpdf import FPDF
from PIL import Image
import trafilatura

# 1. Full-text, unpaywalled news sources
FEEDS = {
    "NPR News": "https://feeds.npr.org/1001/rss.xml",
    "BBC World News": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/partner/subscriber-only-tech/rss.xml",
}

ARTICLES_PER_SECTION = 4
NUM_XKCD = 3
KEEP_DAYS = 7
OUTPUT_DIR = Path.home() / "daily-newspaper" / "pdfs"
RMAPI_BIN = Path.home() / "go/bin/rmapi"
REMOTE_FOLDER = "/Daily Newspaper"


def normalize_text(raw: str) -> str:
    """Strip HTML tags, decode entities, and remove unsupported emoji/variation glyphs."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(
        r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\uFE0F]",
        "",
        text,
    )
    return " ".join(text.split()).strip()


def html_to_paragraphs(raw_html: str) -> list[str]:
    text = re.sub(r"</p\s*>|<br\s*/?>", "\n\n", raw_html, flags=re.IGNORECASE)
    paragraphs = []
    for para in text.split("\n\n"):
        cleaned = normalize_text(para)
        if cleaned and not cleaned.startswith(("Continue reading...", "Read full story", "Comments")):
            paragraphs.append(cleaned)
    return paragraphs


def clean_footer_noise(paragraphs: list[str]) -> list[str]:
    """Remove trailing 'Related topics' sections and '- Published...' footer items."""
    stop_headings = {
        "related topics",
        "related stories",
        "related content",
        "more from npr",
        "more on this story",
        "more like this",
    }
    cleaned = []
    for para in paragraphs:
        low = para.strip().lower().rstrip(":")
        if low in stop_headings:
            break
        if re.match(r"^-?\s*published\s*\d{1,2}\s+[a-z]+\s+\d{4}$", low):
            continue
        cleaned.append(para)
    return cleaned


def prepare_image(raw_path: Path, clean_path: Path, max_width: int = 1200) -> tuple[Path, int, int]:
    """Convert RGBA/WebP/palette images cleanly to RGB JPEG, resize if huge, and return dimensions."""
    with Image.open(raw_path) as im:
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            rgb = bg
        else:
            rgb = im.convert("RGB")

        w_px, h_px = rgb.size
        if w_px > max_width:
            ratio = max_width / w_px
            rgb = rgb.resize((max_width, int(h_px * ratio)), Image.Resampling.LANCZOS)
            w_px, h_px = rgb.size

        rgb.save(clean_path, "JPEG", quality=88)
        return clean_path, w_px, h_px


def find_entry_image_url(entry, raw_html: str, page_html: Optional[str]) -> str:
    """Locate the best lead image URL from RSS media tags, inline HTML, or OpenGraph meta tags."""
    # 1. Check RSS media:content / media:thumbnail
    for key in ("media_content", "media_thumbnail"):
        items = entry.get(key) or []
        for item in items:
            url = item.get("url", "")
            if url.startswith("http") and not url.lower().endswith(".svg"):
                return url

    # 2. Check RSS enclosures
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url") or ""
        if url.startswith("http") and enc.get("type", "").startswith("image/"):
            return url

    # 3. Check <img> tags inside RSS HTML
    if raw_html:
        soup = BeautifulSoup(raw_html, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src.startswith("http") and not any(x in src.lower() for x in ("pixel", "tracker", "1x1", ".svg")):
                return src

    # 4. Fallback to og:image / twitter:image from the fetched article webpage
    if page_html:
        soup = BeautifulSoup(page_html, "html.parser")
        for prop in ("og:image", "twitter:image", "twitter:image:src"):
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                return urljoin(entry.get("link", ""), tag["content"].strip())

    return ""


def download_article_image(img_url: str, temp_dir: Path, slug: str) -> Optional[dict]:
    """Download and normalize an article lead image; skip tiny icons/banners."""
    if not img_url:
        return None
    try:
        req = urllib.request.Request(
            img_url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        raw_path = temp_dir / f"{slug}_raw"
        clean_path = temp_dir / f"{slug}.jpg"
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_path.write_bytes(resp.read())

        final_path, w_px, h_px = prepare_image(raw_path, clean_path)
        if w_px < 180 or h_px < 100:
            return None
        return {"path": final_path, "w_px": w_px, "h_px": h_px}
    except Exception:
        return None


def extract_story_content(entry, title: str, temp_dir: Path, slug: str) -> tuple[list[str], Optional[dict]]:
    """Extract full article paragraphs, deduplicate headlines/subheads, and download a lead image."""
    raw_html = ""
    if "content" in entry and entry.content:
        raw_html = entry.content[0].get("value", "")
    if not raw_html:
        raw_html = entry.get("summary", "") or entry.get("description", "")

    paragraphs = html_to_paragraphs(raw_html)
    total_chars = sum(len(p) for p in paragraphs)
    last_para = paragraphs[-1] if paragraphs else ""

    looks_truncated = total_chars < 2500 or last_para.endswith(("...", "…", "[…]"))
    img_url = find_entry_image_url(entry, raw_html, None)
    downloaded_page = None

    if (looks_truncated or not img_url) and entry.get("link"):
        downloaded_page = trafilatura.fetch_url(entry["link"])
        if downloaded_page:
            if looks_truncated:
                extracted = trafilatura.extract(
                    downloaded_page,
                    include_comments=False,
                    include_tables=False,
                    favor_recall=True,
                )
                if extracted:
                    web_paragraphs = [
                        normalize_text(para)
                        for para in extracted.split("\n")
                        if normalize_text(para)
                    ]
                    if sum(len(p) for p in web_paragraphs) > total_chars:
                        paragraphs = web_paragraphs

            if not img_url:
                img_url = find_entry_image_url(entry, raw_html, downloaded_page)

    # 1. Remove leading paragraph(s) if they duplicate or closely match the article title
    norm_title = re.sub(r"\W+", "", title).lower()
    while paragraphs and norm_title:
        norm_first = re.sub(r"\W+", "", paragraphs[0]).lower()
        if (
            norm_first == norm_title
            or norm_first.startswith(norm_title)
            or norm_title.startswith(norm_first)
        ):
            paragraphs.pop(0)
        else:
            break

    # 2. Remove consecutive duplicate paragraphs (e.g., repeated subheads or RSS decks)
    deduped_paragraphs = []
    prev_norm = ""
    for para in paragraphs:
        curr_norm = re.sub(r"\W+", "", para).lower()
        if curr_norm and curr_norm == prev_norm:
            continue
        deduped_paragraphs.append(para)
        prev_norm = curr_norm

    image_info = download_article_image(img_url, temp_dir, slug)
    return clean_footer_noise(deduped_paragraphs), image_info


def fetch_random_xkcd(count: int, temp_dir: Path) -> list[dict]:
    """Fetch random xkcd comics and save their images locally for PDF embedding."""
    comics = []
    try:
        req = urllib.request.Request(
            "https://xkcd.com/info.0.json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            latest_num = json.loads(resp.read().decode("utf-8"))["num"]

        candidates = [n for n in range(1, latest_num + 1) if n != 404]
        chosen = random.sample(candidates, min(count * 2, len(candidates)))

        for num in chosen:
            if len(comics) >= count:
                break
            try:
                c_req = urllib.request.Request(
                    f"https://xkcd.com/{num}/info.0.json",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(c_req, timeout=10) as c_resp:
                    data = json.loads(c_resp.read().decode("utf-8"))

                img_url = data.get("img", "")
                if not img_url.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue

                raw_path = temp_dir / f"xkcd_{num}_raw"
                clean_path = temp_dir / f"xkcd_{num}.jpg"
                urllib.request.urlretrieve(img_url, raw_path)

                final_path, w_px, h_px = prepare_image(raw_path, clean_path)
                comics.append({
                    "header": f"xkcd #{num}: {normalize_text(data.get('title', ''))}",
                    "caption": normalize_text(data.get("alt", "")),
                    "path": final_path,
                    "w_px": w_px,
                    "h_px": h_px,
                })
            except Exception:
                continue
    except Exception as exc:
        print(f"Warning: could not fetch xkcd comics ({exc})")
    return comics


def setup_unicode_fonts(pdf: FPDF) -> str:
    """Register macOS Georgia fonts with Arial Unicode fallback for arrows/symbols."""
    supp = Path("/System/Library/Fonts/Supplemental")
    georgia_reg = supp / "Georgia.ttf"
    georgia_bold = supp / "Georgia Bold.ttf"
    georgia_ital = supp / "Georgia Italic.ttf"
    arial_uni = supp / "Arial Unicode.ttf"

    if arial_uni.exists():
        pdf.add_font("Fallback", "", str(arial_uni))

    if georgia_reg.exists() and georgia_bold.exists() and georgia_ital.exists():
        pdf.add_font("Editorial", "", str(georgia_reg))
        pdf.add_font("Editorial", "B", str(georgia_bold))
        pdf.add_font("Editorial", "I", str(georgia_ital))
        if arial_uni.exists():
            pdf.set_fallback_fonts(["Fallback"])
        return "Editorial"

    if arial_uni.exists():
        pdf.add_font("Editorial", "", str(arial_uni))
        pdf.add_font("Editorial", "B", str(arial_uni))
        pdf.add_font("Editorial", "I", str(arial_uni))
        return "Editorial"

    return "Times"


def render_toc_column_block(pdf: FPDF, font_family: str, col_x: float, col_w: float, sec: dict):
    """Render a single broadsheet-style section block inside a front-page column."""
    pdf.set_draw_color(30, 30, 30)
    pdf.set_line_width(0.35)
    pdf.line(col_x, pdf.get_y(), col_x + col_w, pdf.get_y())
    pdf.ln(1.2)

    pdf.set_x(col_x)
    pdf.set_font(font_family, "B", 8.5)
    pdf.cell(col_w, 4.5, sec["name"].upper(), new_x="LMARGIN", new_y="NEXT", link=sec["link"])

    pdf.set_draw_color(140, 140, 140)
    pdf.set_line_width(0.15)
    pdf.line(col_x, pdf.get_y(), col_x + col_w, pdf.get_y())
    pdf.ln(1.5)

    if not sec["stories"]:
        pdf.set_x(col_x)
        pdf.set_font(font_family, "I", 7.8)
        pdf.cell(col_w, 4, "No dispatches retrieved.", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        return

    for idx, story in enumerate(sec["stories"]):
        pdf.set_x(col_x)
        pdf.set_font(font_family, "", 7.8)
        pdf.multi_cell(
            col_w,
            3.6,
            story["title"],
            new_x="LMARGIN",
            new_y="NEXT",
            link=story["link"],
        )
        if idx < len(sec["stories"]) - 1:
            pdf.ln(1.0)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(col_x, pdf.get_y(), col_x + col_w * 0.35, pdf.get_y())
            pdf.ln(1.0)

    pdf.ln(3.0)


def build_pdf(output_path: Path, date_str: str):
    pdf = FPDF(format="A5")
    pdf.set_margins(left=11, top=12, right=11)
    pdf.set_auto_page_break(auto=True, margin=13)
    font_family = setup_unicode_fonts(pdf)
    pdf.add_page()

    top_link = pdf.add_link()
    pdf.set_link(top_link, page=1, y=0)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        fetched_sections = []
        total_stories = 0
        for sec_idx, (section, url) in enumerate(FEEDS.items()):
            print(f"Fetching {section}...")
            feed = feedparser.parse(url, agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
            entries = feed.entries[:ARTICLES_PER_SECTION]
            stories = []
            for art_idx, entry in enumerate(entries):
                title = normalize_text(entry.get("title", "Untitled"))
                slug = f"sec{sec_idx}_art{art_idx}"
                paragraphs, image_info = extract_story_content(entry, title, tmp_path, slug)
                stories.append({
                    "title": title,
                    "paragraphs": paragraphs,
                    "image": image_info,
                    "link": pdf.add_link(),
                })
            total_stories += len(stories)
            fetched_sections.append({
                "name": section,
                "link": pdf.add_link(),
                "stories": stories,
            })

        print("Fetching xkcd cartoons...")
        all_comics = fetch_random_xkcd(NUM_XKCD, tmp_path)
        funnies_link = pdf.add_link() if all_comics else None

        # --- FRONT PAGE BROADSHEET MASTHEAD ---
        pdf.set_draw_color(20, 20, 20)
        pdf.set_line_width(0.6)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1.5)

        pdf.set_font(font_family, "B", 21)
        pdf.cell(pdf.epw, 9.5, "REMARKABLE TODAY", new_x="LMARGIN", new_y="NEXT", align="C")

        pdf.set_line_width(0.25)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(0.8)

        pdf.set_font(font_family, "I", 7.8)
        folio_y = pdf.get_y()
        pdf.set_xy(pdf.l_margin, folio_y)
        pdf.cell(pdf.epw * 0.58, 4.5, f"LATEST EDITION  •  {date_str}", align="L")
        pdf.set_xy(pdf.l_margin + pdf.epw * 0.58, folio_y)
        extras = f"  •  {len(all_comics)} CARTOONS" if all_comics else ""
        pdf.cell(pdf.epw * 0.42, 4.5, f"{total_stories} FULL REPORTS{extras}", align="R")
        pdf.ln(4.8)

        pdf.set_line_width(0.6)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(0.8)
        pdf.set_line_width(0.2)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(3.5)

        pdf.set_font(font_family, "B", 8)
        pdf.cell(pdf.epw, 4, "TODAY'S FRONT-PAGE INDEX  (TAP ANY HEADLINE TO READ)", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(2.5)

        # --- TWO-COLUMN BROADSHEET TABLE OF CONTENTS ---
        gutter = 5.0
        col_w = (pdf.epw - gutter) / 2
        left_x = pdf.l_margin
        right_x = pdf.l_margin + col_w + gutter
        cols_start_y = pdf.get_y()

        split_idx = (len(fetched_sections) + 1) // 2
        left_sections = fetched_sections[:split_idx]
        right_sections = fetched_sections[split_idx:]

        pdf.set_y(cols_start_y)
        for sec in left_sections:
            render_toc_column_block(pdf, font_family, left_x, col_w, sec)
        left_end_y = pdf.get_y()

        pdf.set_y(cols_start_y)
        for sec in right_sections:
            render_toc_column_block(pdf, font_family, right_x, col_w, sec)

        if all_comics and funnies_link is not None:
            box_y = pdf.get_y()
            pdf.set_draw_color(40, 40, 40)
            pdf.set_fill_color(246, 246, 246)
            pdf.set_line_width(0.3)
            pdf.rect(right_x, box_y, col_w, 13.5, style="DF")

            pdf.set_xy(right_x + 2, box_y + 1.5)
            pdf.set_font(font_family, "B", 8.2)
            pdf.cell(col_w - 4, 4.2, "THE FUNNIES  →", new_x="LMARGIN", new_y="NEXT", link=funnies_link)

            pdf.set_x(right_x + 2)
            pdf.set_font(font_family, "I", 7.5)
            pdf.multi_cell(
                col_w - 4,
                3.4,
                f"Featuring {len(all_comics)} classic xkcd cartoons on the back page.",
                link=funnies_link,
            )
            pdf.set_y(box_y + 15)

        right_end_y = pdf.get_y()

        divider_x = pdf.l_margin + col_w + (gutter / 2)
        divider_bottom_y = min(max(left_end_y, right_end_y), pdf.h - pdf.b_margin)
        pdf.set_draw_color(170, 170, 170)
        pdf.set_line_width(0.2)
        pdf.line(divider_x, cols_start_y, divider_x, divider_bottom_y)

        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.2)
        pdf.add_page()

        # --- RENDER NEWS SECTIONS ---
        max_article_img_h = pdf.eph * 0.36
        for idx, sec in enumerate(fetched_sections):
            if idx > 0 and pdf.get_y() > pdf.h * 0.65:
                pdf.add_page()

            pdf.set_link(sec["link"], page=pdf.page_no(), y=pdf.get_y())
            pdf.start_section(sec["name"], level=0)

            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "B", 13)
            pdf.cell(pdf.epw, 8, sec["name"].upper(), new_x="LMARGIN", new_y="NEXT")
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)

            if not sec["stories"]:
                pdf.set_x(pdf.l_margin)
                pdf.set_font(font_family, "I", 9.5)
                pdf.cell(pdf.epw, 5, "No articles retrieved for this section today.", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(3)
                continue

            for story in sec["stories"]:
                if pdf.get_y() > pdf.h * 0.72:
                    pdf.add_page()

                pdf.set_link(story["link"], page=pdf.page_no(), y=pdf.get_y())
                pdf.start_section(story["title"], level=1)

                pdf.set_x(pdf.l_margin)
                pdf.set_font(font_family, "B", 12)
                pdf.multi_cell(pdf.epw, 6, story["title"], new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)

                img = story.get("image")
                if img:
                    aspect = img["h_px"] / max(img["w_px"], 1)
                    render_w = pdf.epw
                    render_h = render_w * aspect
                    if render_h > max_article_img_h:
                        render_h = max_article_img_h
                        render_w = render_h / aspect

                    if pdf.get_y() + render_h + 6 > pdf.h - pdf.b_margin:
                        pdf.add_page()

                    x_centered = pdf.l_margin + (pdf.epw - render_w) / 2
                    pdf.image(str(img["path"]), x=x_centered, w=render_w, h=render_h)
                    pdf.ln(3)

                pdf.set_font(font_family, "", 10)
                for para in story["paragraphs"]:
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(pdf.epw, 4.8, para, new_x="LMARGIN", new_y="NEXT")
                    pdf.ln(2)

                pdf.ln(2)
                pdf.line(pdf.l_margin + 20, pdf.get_y(), pdf.w - pdf.r_margin - 20, pdf.get_y())
                pdf.ln(4)

        # --- RENDER FUNNIES & CARTOONS SECTION ---
        if all_comics and funnies_link is not None:
            pdf.add_page()
            pdf.set_link(funnies_link, page=pdf.page_no(), y=pdf.get_y())
            pdf.start_section("The Funnies", level=0)

            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "B", 13)
            pdf.cell(pdf.epw, 8, "THE FUNNIES", new_x="LMARGIN", new_y="NEXT")
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(4)

            max_img_h = pdf.eph * 0.55
            for comic in all_comics:
                aspect = comic["h_px"] / max(comic["w_px"], 1)
                render_w = pdf.epw
                render_h = render_w * aspect
                if render_h > max_img_h:
                    render_h = max_img_h
                    render_w = render_h / aspect

                needed_h = render_h + 28
                if pdf.get_y() + needed_h > pdf.h - pdf.b_margin:
                    pdf.add_page()

                pdf.set_x(pdf.l_margin)
                pdf.set_font(font_family, "B", 11)
                pdf.multi_cell(pdf.epw, 5.5, comic["header"], new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)

                x_centered = pdf.l_margin + (pdf.epw - render_w) / 2
                pdf.image(str(comic["path"]), x=x_centered, w=render_w, h=render_h)
                pdf.ln(2)

                if comic["caption"]:
                    pdf.set_x(pdf.l_margin)
                    pdf.set_font(font_family, "I", 9.5)
                    pdf.multi_cell(pdf.epw, 4.5, comic["caption"], new_x="LMARGIN", new_y="NEXT")

                pdf.ln(4)
                pdf.line(pdf.l_margin + 20, pdf.get_y(), pdf.w - pdf.r_margin - 20, pdf.get_y())
                pdf.ln(5)

        # Render "Back to Front Page" button on the final page
        if pdf.get_y() + 18 > pdf.h - pdf.b_margin:
            pdf.add_page()
        pdf.ln(4)
        btn_w = 64
        btn_h = 10
        btn_x = pdf.l_margin + (pdf.epw - btn_w) / 2
        pdf.set_xy(btn_x, pdf.get_y())
        pdf.set_font(font_family, "B", 10)
        pdf.set_draw_color(40, 40, 40)
        pdf.set_fill_color(242, 242, 242)
        pdf.cell(
            btn_w,
            btn_h,
            "↑ Back to Front Page",
            border=1,
            align="C",
            fill=True,
            link=top_link,
        )

        pdf.output(str(output_path))


def cleanup_old_editions(keep_days: int = KEEP_DAYS):
    """Keep only the most recent `keep_days` newspapers locally and in reMarkable Cloud."""
    local_files = sorted(OUTPUT_DIR.glob("Remarkable-Today-*.pdf"))
    for old_file in local_files[:-keep_days]:
        old_file.unlink(missing_ok=True)
        print(f"Removed old local edition: {old_file.name}")

    res = subprocess.run(
        [str(RMAPI_BIN), "ls", REMOTE_FOLDER],
        capture_output=True,
        text=True,
        check=True,
    )
    remote_editions = []
    for line in res.stdout.splitlines():
        if line.startswith("[f]"):
            name = line.split("\t", 1)[-1].strip()
            if name.startswith("Remarkable-Today-"):
                remote_editions.append(name)

    remote_editions.sort()
    for old_remote in remote_editions[:-keep_days]:
        subprocess.run(
            [str(RMAPI_BIN), "rm", f"{REMOTE_FOLDER}/{old_remote}"],
            check=True,
        )
        print(f"Removed old tablet edition: {old_remote}")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    pretty_date = datetime.date.today().strftime("%A, %B %d, %Y")
    pdf_path = OUTPUT_DIR / f"Remarkable-Today-{today}.pdf"

    build_pdf(pdf_path, pretty_date)
    subprocess.run([str(RMAPI_BIN), "put", "--force", str(pdf_path), REMOTE_FOLDER], check=True)
    print(f"Successfully generated and uploaded {pdf_path.name} to {REMOTE_FOLDER}")

    cleanup_old_editions(keep_days=KEEP_DAYS)


if __name__ == "__main__":
    main()
