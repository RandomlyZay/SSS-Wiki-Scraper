import httpx
import asyncio
import re
import json
import os

BASE_URL = "https://sonic-speed-simulator.fandom.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
}
IMAGES_DIR = "images"
CONCURRENCY = 20


async def fetch_json(client, params, description="API request"):
    try:
        response = await client.get("api.php", params=params)
        response.raise_for_status()

        text = response.text.strip()
        if not text:
            print(f"Error: Empty response for {description}")
            return None

        try:
            return response.json()
        except json.JSONDecodeError:
            print(f"Error: Non-JSON response for {description} (Status: {response.status_code})")
            print(f"Response snippet: {text[:200]}...")
            return None
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error for {description}: {e.response.status_code}")
        return None
    except Exception as e:
        print(f"Unexpected error for {description}: {e}")
        return None


def parse_stat_string(s):
    stats = {}
    # Handles: +167, 1,234, 1.5K, 2M, ∞, etc.
    stat_keywords = {
        "xp": ["XP"],
        "rings": ["Rings?", "Ring"],
        "damage": ["Damage", "Power"],
        "luck": ["Luck"],
        "magnet": ["Magnet"],
        "stamina": ["Stamina"],
        "event": ["Event"],
        "steps": ["Steps"],
        "air": ["Air"],
        "speed": ["Speed"],
    }

    for stat, keywords in stat_keywords.items():
        for keyword in keywords:
            # Match: [+-]?VALUE or ∞ followed by either:
            # 1. A File tag that contains the keyword in its name or alt text
            # 2. An {{Icons|keyword}} template
            # 3. The keyword itself
            pattern = (
                r"([+-]?[\d,.]+K?M?|∞)\s*(?:"
                r"\[\[File:[^\]]*?" + keyword + r"[^\]]*?\]\]|"
                r"\{\{Icons\|" + keyword + r"\}\}|"
                r"" + keyword + r""
                r")"
            )
            # Use re.IGNORECASE for both value suffixes and keyword matches
            matches = re.findall(pattern, s, re.IGNORECASE)
            for val_str in matches:
                val_str = val_str.replace(",", "").strip()
                if val_str == "∞":
                    stats[stat] = "Infinity"
                    continue

                multiplier = 1
                if "K" in val_str.upper():
                    multiplier = 1000
                    val_str = val_str.upper().replace("K", "")
                elif "M" in val_str.upper():
                    multiplier = 1000000
                    val_str = val_str.upper().replace("M", "")

                try:
                    val = float(val_str) * multiplier
                    stats[stat] = int(val)
                except ValueError:
                    pass
    return stats


def clean_wikitext(s):
    if not s:
        return ""
    # Remove templates like {{Icons|...}} or {{Rarity|...}}
    s = re.sub(r"\{\{.*?\|(.*?)\}\}", r"\1", s)
    # Remove links [[Link|Text]] -> Text or [[Text]] -> Text
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    # Remove HTML tags
    s = re.sub(r"<[^>]*>", " ", s)
    # Remove bold/italic
    s = s.replace("'''", "").replace("''", "")
    # Clean whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_rarity(wikitext, field_dict):
    r = field_dict.get("rarity", "").strip()
    if r:
        r = clean_wikitext(r)
        if r and "{{" not in r:
            return r

    # Try {{Rarity|...}}
    match = re.search(r"\{\{Rarity\|(.*?)\}\}", wikitext, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Special case for Fast Friends category
    if "[[Category:Fast Friends]]" in wikitext:
        return "Fast Friend"

    # Try Categories
    cat_match = re.search(
        r"\[\[Category:(Legendary|Epic|Rare|Common|Event|Special|Exclusive|Holiday)\s+(?:Characters|Friends|Trails)\]\]",
        wikitext,
        re.IGNORECASE,
    )
    if cat_match:
        return cat_match.group(1).strip()

    return "Common"


def extract_image_filename(wikitext):
    for field in ["character_image", "friend_image", "chao_picture", "image"]:
        pattern = rf"\|\s*{field}\s*=\s*(.*?)(?=\s*(?:\||}}}}|$))"
        val = re.search(pattern, wikitext, re.DOTALL | re.IGNORECASE)
        if val:
            content = val.group(1).strip()
            galleries = re.findall(r"<gallery>(.*?)</gallery>", wikitext, re.DOTALL)
            tabbers = re.findall(r"<tabber>(.*?)</tabber>", wikitext, re.DOTALL)

            all_content = content + "\n" + "\n".join(galleries) + "\n" + "\n".join(tabbers)

            # Find all filenames in the content
            files = re.findall(r"([\w\s._-]+\.(?:png|jpg|webp|gif|svg))", all_content, re.IGNORECASE)
            if files:
                # Prioritize Portrait
                for f in files:
                    if "portrait" in f.lower():
                        return f.strip()
                # Fallback to Render
                for f in files:
                    if "render" in f.lower():
                        return f.strip()
                return files[0].strip()

    return None


async def get_image_url(filename, client):
    if not filename:
        return None
    params = {
        "action": "query",
        "prop": "imageinfo",
        "titles": f"File:{filename}",
        "iiprop": "url",
        "format": "json",
    }
    response = await fetch_json(client, params, f"image URL for {filename}")
    if response:
        pages = response.get("query", {}).get("pages", {})
        for p in pages.values():
            if "imageinfo" in p:
                return p["imageinfo"][0]["url"]
    return None


async def download_image(filename, name, client):
    if not filename:
        return None

    safe_name = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
    ext = filename.split(".")[-1]
    local_filename = f"{safe_name}.{ext}"
    local_path = os.path.join(IMAGES_DIR, local_filename)

    if os.path.exists(local_path):
        return local_path

    url = await get_image_url(filename, client)
    if not url:
        return None

    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code == 200:
                with open(local_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(1024):
                        f.write(chunk)
                return local_path
    except Exception:
        pass
    return None


async def get_page_stats(title, category, client, semaphore):
    async with semaphore:
        params = {"action": "parse", "page": title, "prop": "wikitext", "format": "json"}
        response = await fetch_json(client, params, f"stats for {title}")
        if not response or "error" in response:
            return None
        wikitext = response["parse"]["wikitext"]["*"]

    stats = {
        "name": title,
        "rarity": "Common",
        "image": None,
    }

    fields = re.findall(
        r"\|\s*([\w\d\s_]+)\s*=\s*((?:[^{}|\[\{]|{{.*?}}|\[\[.*?\]\])*)", wikitext
    )
    field_dict = {k.strip().lower(): v.strip() for k, v in fields}

    stats["rarity"] = extract_rarity(wikitext, field_dict)
    img_filename = extract_image_filename(wikitext)
    stats["image"] = await download_image(img_filename, title, client)

    if category == "Characters":
        stats["max"] = {}
        stats["abilities"] = []

        tier_val = field_dict.get("tier", "")
        if tier_val:
            stats["max"].update(parse_stat_string(tier_val))

        abilities_val = field_dict.get("abilities", "")
        if abilities_val:
            cleaned = clean_wikitext(abilities_val)
            if cleaned:
                stats["abilities"] = [a.strip() for a in re.split(r",|;| and ", cleaned, flags=re.IGNORECASE) if a.strip()]

    elif category == "Fast Friends":
        stats["levels"] = {}
        for i in range(1, 7):
            lvl_val = field_dict.get(f"level_{i}") or field_dict.get(f"level {i}")
            if lvl_val:
                stats["levels"][str(i)] = parse_stat_string(lvl_val)

    else: # Friends or Trails
        stats["max"] = {}
        stats["max_fused"] = {}

        stat_map = {
            "xp": ["xp"],
            "rings": ["rings", "ring"],
            "damage": ["damage", "power"],
            "luck": ["luck"],
            "magnet": ["magnet"],
            "stamina": ["stamina"],
            "event": ["event"],
            "steps": ["steps"],
            "air": ["air"],
            "speed": ["speed"],
        }

        def get_val(key):
            val = field_dict.get(key.lower(), "0").replace(",", "").replace("+", "").strip()
            if not val or val == "&mdash;" or val == "-" or val == "0":
                return 0
            try:
                multiplier = 1
                if "K" in val.upper():
                    multiplier = 1000
                    val = val.upper().replace("K", "")
                elif "M" in val.upper():
                    multiplier = 1000000
                    val = val.upper().replace("M", "")
                return int(float(val) * multiplier)
            except (ValueError, TypeError):
                return 0

        for s_key, s_aliases in stat_map.items():
            for alias in s_aliases:
                m = get_val(f"max_{alias}_stat")
                mf = (
                    get_val(f"max_fused_{alias}_stat")
                    or get_val(f"base_fused_{alias}_stat")
                    or get_val(f"max_level_fused_{alias}_stat")
                )
                if m: stats["max"][s_key] = m
                if mf: stats["max_fused"][s_key] = mf

        # Supplemental: Check level_6 for max if empty
        if not stats["max"]:
            lvl6 = field_dict.get("level_6") or field_dict.get("level 6")
            if lvl6:
                stats["max"].update(parse_stat_string(lvl6))

    # Basic validation: ensure we got something
    if not any(stats.get(k) for k in ["max", "levels", "abilities", "max_fused"]):
        return None

    return stats


async def get_category_members(category, client):
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": "max",
        "format": "json",
    }
    members = []
    while True:
        response = await fetch_json(client, params, f"category members for {category}")
        if not response or "query" not in response:
            break
        members.extend(response["query"]["categorymembers"])
        if "continue" in response:
            params.update(response["continue"])
        else:
            break
    return [m["title"] for m in members if not m["title"].startswith("Category:")]


async def main():
    if not os.path.exists(IMAGES_DIR):
        os.makedirs(IMAGES_DIR)

    categories = {
        "Friends": "Friends",
        "Trails": "Trails",
        "Characters": "Characters",
        "Fast Friends": "Fast Friends",
    }

    results = {cat_key: [] for cat_key in categories}
    errors = []
    has_error = False

    async with httpx.AsyncClient(base_url=BASE_URL, headers=HEADERS, timeout=60.0) as client:
        semaphore = asyncio.Semaphore(CONCURRENCY)

        for cat_key, cat_name in categories.items():
            print(f"Fetching {cat_name} list from Wiki...")
            items_list = await get_category_members(cat_name, client)
            expected = len(items_list)

            print(f"Processing {expected} {cat_name} concurrently...")

            async def wrapped_task(title, category):
                res = await get_page_stats(title, category, client, semaphore)
                return title, res

            tasks = [wrapped_task(title, cat_key) for title in items_list]
            completed = 0
            for task in asyncio.as_completed(tasks):
                title, res = await task
                completed += 1
                if completed % 25 == 0:
                    print(f"  Progress: {completed}/{expected}")

                if res:
                    results[cat_key].append(res)
                else:
                    errors.append({"name": title, "type": cat_key})
                    has_error = True

    print("Saving to stats.json...")
    data = {**results, "Errors": errors, "error": has_error}
    with open("stats.json", "w") as f:
        json.dump(data, f, indent=2)

    total_scraped = sum(len(results[k]) for k in categories)
    print(f"Successfully scraped {total_scraped} items across all categories.")


if __name__ == "__main__":
    asyncio.run(main())
