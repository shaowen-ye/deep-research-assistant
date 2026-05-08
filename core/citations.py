import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from urllib import parse, request

from .common import read_json, write_json
from .state import job_dir


class CitationMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs if key and value}
        if tag.lower() == "title":
            self.in_title = True
            return
        if tag.lower() != "meta":
            return
        key = attrs.get("name") or attrs.get("property") or attrs.get("itemprop")
        content = attrs.get("content")
        if key and content:
            self.meta.setdefault(key.lower(), []).append(clean_meta_text(content))

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    @property
    def title(self):
        return clean_meta_text(" ".join(self.title_parts))


def clean_meta_text(value):
    value = unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def source_domain(url):
    try:
        host = parse.urlparse(url).netloc.lower()
    except Exception:
        host = ""
    return host.removeprefix("www.") or "Unknown source"


def first_meta(meta, *keys):
    for key in keys:
        values = meta.get(key.lower())
        if values:
            return values[0]
    return ""


def extract_year(value):
    match = re.search(r"\b(19|20)\d{2}\b", value or "")
    return match.group(0) if match else "n.d."


def clean_author(value, fallback):
    value = clean_meta_text(value)
    if not value:
        value = fallback
    value = re.split(r"\s+[|–—-]\s+", value)[0].strip()
    if len(value) > 90:
        value = value[:87].rstrip() + "..."
    return value or fallback


def fetch_source_metadata(url, label):
    fallback_site = clean_meta_text(label) or source_domain(url)
    fallback_author = fallback_site
    metadata = {
        "url": url,
        "final_url": url,
        "label": fallback_site,
        "title": fallback_site,
        "site": fallback_site,
        "author": fallback_author,
        "year": "n.d.",
    }

    try:
        req = request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 DeepResearchLocalApp/0.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with request.urlopen(req, timeout=5) as response:
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(240000)
    except Exception:
        return metadata

    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = CitationMetadataParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    site = first_meta(parser.meta, "og:site_name", "application-name", "twitter:site") or source_domain(final_url)
    title = (
        first_meta(parser.meta, "citation_title", "dc.title", "og:title", "twitter:title")
        or parser.title
        or fallback_site
    )
    author = first_meta(
        parser.meta,
        "citation_author",
        "author",
        "article:author",
        "dc.creator",
        "parsely-author",
        "sailthru.author",
    )
    date = first_meta(
        parser.meta,
        "citation_publication_date",
        "citation_date",
        "article:published_time",
        "datepublished",
        "date",
        "dc.date",
        "pubdate",
    )
    year = extract_year(date) if date else extract_year(title)

    metadata.update(
        {
            "final_url": final_url,
            "title": clean_meta_text(title),
            "site": clean_meta_text(site),
            "author": clean_author(author, clean_meta_text(site) or fallback_author),
            "year": year,
        }
    )
    return metadata


def parse_sources(markdown):
    match = re.search(r"(?ims)\n(?:\*\*Sources:\*\*|#+\s*Sources)\s*\n(.+)\s*$", markdown)
    if not match:
        return None, []

    sources = []
    for line in match.group(1).splitlines():
        item = re.match(r"\s*(\d+)\.\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if item:
            sources.append(
                {
                    "index": int(item.group(1)),
                    "label": clean_meta_text(item.group(2)),
                    "url": item.group(3).strip(),
                }
            )
    return match, sources


def citation_metadata_for_sources(job_id, sources):
    cache_path = job_dir(job_id) / "citation_metadata.json"
    cache = read_json(cache_path, {}) or {}
    metadata_by_index = {}
    for source in sources:
        cache_key = source["url"]
        metadata = cache.get(cache_key)
        if not metadata:
            metadata = fetch_source_metadata(source["url"], source["label"])
            cache[cache_key] = metadata
        metadata_by_index[source["index"]] = metadata
    write_json(cache_path, cache)
    return metadata_by_index


def citation_label(metadata):
    return f"{metadata.get('author') or metadata.get('site')}, {metadata.get('year') or 'n.d.'}"


def accessed_date():
    return datetime.now().strftime("%Y-%m-%d")


def numbered_reference(index, metadata):
    title = metadata.get("title") or metadata.get("label") or "Untitled"
    site = metadata.get("site") or source_domain(metadata.get("final_url") or metadata.get("url") or "")
    url = metadata.get("final_url") or metadata.get("url") or ""
    title = clean_meta_text(title).rstrip(".。；;")
    if not title or title == "Untitled":
        title = clean_meta_text(site) or source_domain(url)
    return f"{index}. {title}。<{url}>。访问时间：{accessed_date()}。"


def normalize_citations(markdown, metadata_by_index):
    sources_match, sources = parse_sources(markdown)
    if not sources:
        return markdown

    def replace_cite(match):
        labels = []
        for number in re.findall(r"\d+", match.group(1)):
            metadata = metadata_by_index.get(int(number))
            if metadata:
                url = metadata.get("final_url") or metadata.get("url") or ""
                labels.append(f"[{number}]({url})")
        if not labels:
            return match.group(0)
        return f"（{'；'.join(labels)}）"

    body = markdown[: sources_match.start()].rstrip()
    body = re.sub(r"\s*\[cite:\s*([0-9,\s]+)\]", replace_cite, body)
    references = "\n".join(
        numbered_reference(source["index"], metadata_by_index[source["index"]])
        for source in sources
        if source["index"] in metadata_by_index
    )
    return f"{body}\n\n## 参考文献\n\n{references}\n"
