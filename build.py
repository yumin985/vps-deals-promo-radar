# ILANG
# [TYPE:renderer][PROJECT:vps-deals]
# ::BOUNDARY{read_site_config_from_.ilang/site.ilang; render_only_verified_data}

"""Render the static offer site from the I-Lang config and scraper output."""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / ".ilang" / "site.ilang"
DATA = ROOT / "data" / "offers.json"
TEMPLATES = ROOT / "templates"
OUT = ROOT / "site"


def read_config() -> tuple[dict[str, str], list[dict[str, str]]]:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r"::STATE\{@SITE,(.*?)\}", text, re.S)
    if not match:
        raise ValueError("Missing @SITE state in .ilang/site.ilang")
    state = {k.strip(): v.strip() for k, v in re.findall(r"([\w-]+)\s*:\s*([^,]+)", match.group(1))}
    providers: list[dict[str, str]] = []
    inside = False
    for line in text.splitlines():
        row = line.strip()
        if row.startswith("::MODULE{PROVIDERS"):
            inside = True
            continue
        if inside and row == "::END":
            break
        if not inside or not row or row.startswith("#"):
            continue
        fields = [part.strip() for part in line.split("|")]
        if len(fields) != 4:
            raise ValueError(f"Invalid provider row in .ilang/site.ilang: {line}")
        name, home_url, source_url, affiliate_url = fields
        if not name or urlparse(home_url).scheme != "https" or urlparse(source_url).scheme != "https":
            raise ValueError(f"Invalid provider entry: {line}")
        providers.append({"name": name, "home_url": home_url, "source_url": source_url, "affiliate_url": affiliate_url})
    if not providers:
        raise ValueError("No providers configured in .ilang/site.ilang")
    if not state.get("brand") or not state.get("domain"):
        raise ValueError("The @SITE state must provide brand and domain")
    return state, providers


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "provider"


def format_price(value: str, currency: str) -> str:
    try:
        amount = f"{float(value):,.2f}".rstrip("0").rstrip(".")
    except ValueError:
        amount = value
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(currency, f"{currency} ")
    return f"{symbol}{amount}"


def iso_date(value: str) -> str | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except (ValueError, AttributeError):
        return None


def active_offer(offer: dict[str, object]) -> bool:
    expiry = str(offer.get("valid_until") or "")
    if not expiry:
        return True
    try:
        return date.fromisoformat(expiry) >= datetime.now(timezone.utc).date()
    except ValueError:
        return False


def render_template(name: str, values: dict[str, str]) -> str:
    template_path = TEMPLATES / name
    return Template(template_path.read_text(encoding="utf-8")).substitute(values)


def json_ld(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def reset_output() -> None:
    output = OUT.resolve()
    root = ROOT.resolve()
    if output.parent != root or output.name != "site" or OUT.is_symlink():
        raise ValueError("Refusing to clean an unexpected output path")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    config, providers = read_config()
    base = config["domain"].rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Canonical domain must be an https URL")
    dataset = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"offers": [], "sources": [], "generated_at": ""}
    provider_names = {item["name"] for item in providers}
    offers = [item for item in dataset.get("offers", []) if item.get("provider") in provider_names and active_offer(item)]
    for offer in offers:
        for required in ("provider", "title", "price", "currency", "offer_url", "source_url", "fetched_at"):
            if not offer.get(required):
                raise ValueError(f"Offer is missing required factual field: {required}")

    reset_output()
    routes: list[tuple[str, str | None]] = []
    deal_routes: dict[int, str] = {}
    provider_routes: dict[str, str] = {p["name"]: f"providers/{slug(p['name'])}/index.html" for p in providers}
    latest_source_date = max((iso_date(str(o.get("fetched_at", ""))) or "" for o in offers), default="") or None

    def save(relative: str, content: str, lastmod: str | None = None) -> None:
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        routes.append((relative, lastmod))

    cards = []
    for index, offer in enumerate(offers):
        route = f"deals/{slug(str(offer['provider']))}-{slug(str(offer['title']))}-{index + 1}/index.html"
        deal_routes[index] = route
        href = f"/{route.removesuffix('index.html')}"
        fetched = esc(offer["fetched_at"])
        cards.append(
            '<article class="offer-card"><div class="offer-card__top"><span class="eyebrow">'
            + esc(offer["provider"]) + '</span><span class="price">' + esc(format_price(str(offer["price"]), str(offer["currency"])))
            + '</span></div><h3><a href="' + esc(href) + '">' + esc(offer["title"]) + '</a></h3><p>Source checked <time datetime="'
            + fetched + '">' + fetched + '</time></p><a class="text-link" href="' + esc(offer["offer_url"])
            + '" rel="nofollow noopener">View official offer ↗</a></article>'
        )
    if not cards:
        cards_html = '<div class="empty-state"><h3>No verifiable live offers yet</h3><p>Offer cards appear only when a public provider page exposes a structured price that the collector can verify. Browse the official sources below.</p></div>'
    else:
        cards_html = "\n".join(cards)

    provider_cards = []
    for provider in providers:
        count = sum(1 for o in offers if o["provider"] == provider["name"])
        provider_cards.append(
            '<article class="provider-card"><span class="eyebrow">' + esc(provider["name"]) + '</span><p>'
            + str(count) + (' verified offer' if count == 1 else ' verified offers') + '</p><a class="text-link" href="/'
            + esc(provider_routes[provider["name"]].removesuffix("index.html")) + '">Provider details ↗</a><a class="source-link" href="'
            + esc(provider["source_url"]) + '" rel="nofollow noopener">Official source</a></article>'
        )

    item_list = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": n + 1, "url": base + "/" + deal_routes[n].removesuffix("index.html")}
        for n in range(len(offers))
    ]}
    latest_text = esc(dataset.get("generated_at") or "No successful collection yet")
    common = {"brand": esc(config["brand"]), "base": esc(base), "description": "Public VPS plan and offer data, linked to official provider sources.", "styles": "/styles.css"}
    index_values = {**common, "title": "VPS deals and plans | " + esc(config["brand"]), "canonical": esc(base + "/"), "description": "Compare verifiable VPS plans and offers from official provider pages.", "content": cards_html, "providers": "\n".join(provider_cards), "updated": latest_text, "schema": json_ld(item_list)}
    save("index.html", render_template("index.html", index_values), latest_source_date)

    compare_rows = []
    for offer in offers:
        compare_rows.append("<tr><td>" + esc(offer["provider"]) + "</td><td>" + esc(offer["title"]) + "</td><td>" + esc(format_price(str(offer["price"]), str(offer["currency"]))) + "</td><td><a href=\"" + esc(offer["offer_url"]) + "\" rel=\"nofollow noopener\">Official page ↗</a></td></tr>")
    compare_html = "\n".join(compare_rows) or '<tr><td colspan="4">No structured, verifiable prices were available at the last check.</td></tr>'
    compare_schema = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": n + 1, "url": base + "/" + deal_routes[n].removesuffix("index.html")}
        for n in range(len(offers))
    ]}
    save("compare/index.html", render_template("compare.html", {**common, "title": "Compare VPS plans | " + esc(config["brand"]), "canonical": esc(base + "/compare/"), "description": "Compare current VPS plan prices collected from official provider pages.", "rows": compare_html, "schema": json_ld(compare_schema)}), latest_source_date)

    for provider in providers:
        selected = [(i, o) for i, o in enumerate(offers) if o["provider"] == provider["name"]]
        provider_offer_html = []
        for index, offer in selected:
            route = "/" + deal_routes[index].removesuffix("index.html")
            provider_offer_html.append('<article class="offer-card"><div class="offer-card__top"><span class="eyebrow">' + esc(offer["provider"]) + '</span><span class="price">' + esc(format_price(str(offer["price"]), str(offer["currency"]))) + '</span></div><h3><a href="' + esc(route) + '">' + esc(offer["title"]) + '</a></h3><p>Fetched ' + esc(offer["fetched_at"]) + '</p><a class="text-link" href="' + esc(offer["offer_url"]) + '" rel="nofollow noopener">View official offer ↗</a></article>')
        provider_content = "\n".join(provider_offer_html) or '<div class="empty-state"><p>No current structured offer was verified. Check the provider source directly.</p></div>'
        provider_href = provider["affiliate_url"] or provider["home_url"]
        product_schema: dict[str, object] = {"@context": "https://schema.org", "@type": "Service", "name": provider["name"] + " VPS hosting", "provider": {"@type": "Organization", "name": provider["name"], "url": provider["home_url"]}, "url": provider_href}
        currencies = {str(o["currency"]) for _, o in selected}
        if selected and len(currencies) == 1:
            amounts = [float(str(o["price"])) for _, o in selected]
            product_schema["offers"] = {"@type": "AggregateOffer", "priceCurrency": next(iter(currencies)), "lowPrice": f"{min(amounts):g}", "highPrice": f"{max(amounts):g}", "offerCount": len(amounts), "url": base + "/" + provider_routes[provider["name"]].removesuffix("index.html")}
        href = "/" + provider_routes[provider["name"]].removesuffix("index.html")
        source_time = max((iso_date(str(o.get("fetched_at", ""))) or "" for _, o in selected), default="") or None
        save(provider_routes[provider["name"]], render_template("provider.html", {**common, "title": provider["name"] + " VPS offers | " + esc(config["brand"]), "canonical": esc(base + href), "description": "VPS prices and official sources from " + provider["name"] + ".", "provider": esc(provider["name"]), "official_url": esc(provider_href), "source_url": esc(provider["source_url"]), "content": provider_content, "schema": json_ld(product_schema)}), source_time)

    for index, offer in enumerate(offers):
        relative = deal_routes[index]
        canonical = base + "/" + relative.removesuffix("index.html")
        offer_schema: dict[str, object] = {"@context": "https://schema.org", "@type": "Offer", "name": str(offer["title"]), "price": str(offer["price"]), "priceCurrency": str(offer["currency"]), "url": str(offer["offer_url"]), "seller": {"@type": "Organization", "name": str(offer["provider"])}}
        if offer.get("valid_until"):
            offer_schema["priceValidUntil"] = offer["valid_until"]
        if offer.get("availability"):
            offer_schema["availability"] = "https://schema.org/" + str(offer["availability"])
        validity = '<p class="factual-note">Valid until ' + esc(offer["valid_until"]) + '</p>' if offer.get("valid_until") else '<p class="factual-note">The provider page did not publish a verified expiration date.</p>'
        offer_content = '<article class="detail-card"><span class="eyebrow">' + esc(offer["provider"]) + '</span><h2>' + esc(offer["title"]) + '</h2><p class="price price--large">' + esc(format_price(str(offer["price"]), str(offer["currency"]))) + '</p>' + validity + '<p>Source checked: <time datetime="' + esc(offer["fetched_at"]) + '">' + esc(offer["fetched_at"]) + '</time></p><p><a class="button" href="' + esc(offer["offer_url"]) + '" rel="nofollow noopener">See provider offer</a></p><p class="source-link">Source: <a href="' + esc(offer["source_url"]) + '" rel="nofollow noopener">' + esc(offer["source_url"]) + '</a></p></article>'
        save(relative, render_template("deal.html", {**common, "title": str(offer["title"]) + " | " + esc(config["brand"]), "canonical": esc(canonical), "description": str(offer["provider"]) + " VPS price: " + format_price(str(offer["price"]), str(offer["currency"])) + ". View source and check time.", "content": offer_content, "schema": json_ld(offer_schema)}), iso_date(str(offer["fetched_at"])))

    save("styles.css", (ROOT / "templates" / "styles.css").read_text(encoding="utf-8"))
    sitemap_items = []
    for relative, lastmod in routes:
        if not relative.endswith(".html"):
            continue
        path = "/" + relative.removesuffix("index.html") if relative.endswith("index.html") else "/" + relative
        lastmod_xml = f"<lastmod>{esc(lastmod)}</lastmod>" if lastmod else ""
        sitemap_items.append(f"<url><loc>{esc(base + path)}</loc>{lastmod_xml}</url>")
    save("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(sitemap_items) + "\n</urlset>\n", latest_source_date)
    save("robots.txt", "User-agent: *\nAllow: /\nSitemap: " + base + "/sitemap.xml\n")
    print(f"Rendered {len(routes)} static pages using {len(providers)} providers and {len(offers)} verified offer(s) into site/")
    print(f"Canonical base: {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
