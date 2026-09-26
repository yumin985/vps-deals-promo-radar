# ILANG
# [TYPE:collector][PROJECT:vps-deals]
# ::BOUNDARY{only_public_robot_allowed_sources; never_invent_offer_data}

"""Fetch verifiable offer data from the public official sources in site.ilang."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / ".ilang" / "site.ilang"
OUTPUT = ROOT / "data" / "offers.json"
USER_AGENT = "vps-deals-public-offer-index/1.0 (contact: repository README)"
MAX_BYTES = 8_000_000


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_jsonld = False
        self._parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            values = dict(attrs)
            self._in_jsonld = values.get("type", "").lower().split(";")[0] == "application/ld+json"
            if self._in_jsonld:
                self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self.documents.append("".join(self._parts))
            self._parts = []
            self._in_jsonld = False


class VisibleTextParser(HTMLParser):
    """Collect visible text for official pages that do not publish JSON-LD offers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def read_config() -> tuple[dict[str, str], list[dict[str, str]]]:
    text = CONFIG.read_text(encoding="utf-8")
    state_match = re.search(r"::STATE\{@SITE,(.*?)\}", text, re.S)
    if not state_match:
        raise ValueError("Missing @SITE state in .ilang/site.ilang")
    state = {k.strip(): v.strip() for k, v in re.findall(r"([\w-]+)\s*:\s*([^,]+)", state_match.group(1))}
    providers: list[dict[str, str]] = []
    in_providers = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("::MODULE{PROVIDERS"):
            in_providers = True
            continue
        if in_providers and stripped == "::END":
            break
        if not in_providers or not stripped or stripped.startswith("#"):
            continue
        cols = [part.strip() for part in line.split("|")]
        if len(cols) != 4:
            raise ValueError(f"Invalid provider row in site.ilang: {line}")
        name, home_url, source_url, affiliate_url = cols
        for url in (home_url, source_url):
            if urlparse(url).scheme != "https":
                raise ValueError(f"Only HTTPS provider URLs are accepted: {url}")
        providers.append({"name": name, "home_url": home_url, "source_url": source_url, "affiliate_url": affiliate_url})
    if not providers:
        raise ValueError("No providers configured in .ilang/site.ilang")
    return state, providers


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"})
    with urlopen(request, timeout=20) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Source exceeded the 8 MB response limit")
    return body


def robots_allows(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        rules = fetch(robots_url).decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"Could not verify robots.txt ({type(exc).__name__}); skipped") from exc
    active_agents: list[str] = []
    directives: list[tuple[str, str]] = []
    for raw in rules.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if directives:
                active_agents = []
                directives = []
            active_agents.append(value.lower())
        elif key in {"allow", "disallow"} and active_agents:
            directives.append((key, value))
    matching = [rule for agent in active_agents if agent in {"*", USER_AGENT.lower()} for rule in directives]
    # Most servers use User-agent: *; evaluate longest matching path rule.
    if not matching:
        return True
    path = parsed.path or "/"
    applicable = [(len(rule), action) for action, rule in matching if rule and path.startswith(rule)]
    if not applicable:
        return True
    longest = max(length for length, _ in applicable)
    return any(action == "allow" for length, action in applicable if length == longest)


def parse_jsonld(html: bytes, provider: dict[str, str], fetched_at: str) -> list[dict[str, str]]:
    parser = JsonLdParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    results: list[dict[str, str]] = []

    def walk(node: object, inherited_name: str = "") -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, inherited_name)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("@type", "")
        types = node_type if isinstance(node_type, list) else [node_type]
        name = str(node.get("name") or inherited_name).strip()
        if any(str(t).lower() == "offer" for t in types):
            raw_price = node.get("price")
            currency = str(node.get("priceCurrency") or "").upper()
            try:
                price = float(str(raw_price).replace(",", ""))
            except (TypeError, ValueError):
                price = 0
            if name and price > 0 and re.fullmatch(r"[A-Z]{3}", currency):
                offer_url = node.get("url")
                offer: dict[str, str] = {
                    "provider": provider["name"],
                    "title": name,
                    "price": str(raw_price),
                    "currency": currency,
                    "offer_url": str(offer_url if isinstance(offer_url, str) and offer_url.startswith("https://") else provider["source_url"]),
                    "source_url": provider["source_url"],
                    "fetched_at": fetched_at,
                }
                expiry = node.get("priceValidUntil") or node.get("validThrough")
                if isinstance(expiry, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
                    offer["valid_until"] = expiry
                availability = node.get("availability")
                if isinstance(availability, str) and availability.startswith("https://schema.org/"):
                    offer["availability"] = availability.rsplit("/", 1)[-1]
                results.append(offer)
        for key, value in node.items():
            if key in {"offers", "itemOffered", "hasOfferCatalog", "@graph", "mainEntity", "mainEntityOfPage"}:
                walk(value, name or inherited_name)

    for document in parser.documents:
        try:
            walk(json.loads(document))
        except (json.JSONDecodeError, RecursionError):
            continue
    # Deduplicate identical offer cards without modifying the source values.
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for offer in results:
        key = (offer["provider"], offer["title"], offer["price"], offer["currency"])
        unique[key] = offer
    return list(unique.values())


def parse_visible_offers(html: bytes, provider: dict[str, str], fetched_at: str) -> list[dict[str, str]]:
    """Parse only explicit plan/price text from known public official layouts.

    This is intentionally narrow: it supports IONOS's public VPS+ cards, where
    the page publishes no Offer JSON-LD. No value is inferred when the card
    text or price is missing.
    """
    if provider["name"] != "IONOS":
        return []
    parser = VisibleTextParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    text = " ".join(parser.parts)
    results: list[dict[str, str]] = []
    for plan in ("S+", "M+", "L+", "XL+", "XXL+"):
        match = re.search(
            rf"VPS\s+{re.escape(plan)}.*?\$\s*([0-9]+(?:\.[0-9]+)?)\s*/month\s+for\s+3\s+months",
            text,
            re.I,
        )
        if not match:
            continue
        results.append({
            "provider": provider["name"],
            "title": f"VPS {plan} (3-month promotion)",
            "price": match.group(1),
            "currency": "USD",
            "offer_url": provider["source_url"],
            "source_url": provider["source_url"],
            "fetched_at": fetched_at,
            "availability": "InStock",
        })
    return results


def main() -> int:
    _, providers = read_config()
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    offers: list[dict[str, str]] = []
    source_status: list[dict[str, str]] = []
    for provider in providers:
        try:
            if not robots_allows(provider["source_url"]):
                raise RuntimeError("robots.txt disallows the configured path; skipped")
            source_html = fetch(provider["source_url"])
            records = parse_jsonld(source_html, provider, fetched_at)
            if not records:
                records = parse_visible_offers(source_html, provider, fetched_at)
            offers.extend(records)
            source_status.append({"provider": provider["name"], "status": "offers_found" if records else "no_structured_offers", "fetched_at": fetched_at})
            print(f"{provider['name']}: {len(records)} verifiable Offer record(s)")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            source_status.append({"provider": provider["name"], "status": "skipped", "detail": str(exc), "fetched_at": fetched_at})
            print(f"{provider['name']}: skipped ({exc})")
        time.sleep(1)
    payload = {"generated_at": fetched_at, "offers": offers, "sources": source_status}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(offers)} offer(s) to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
