import requests
import re
import json
import time
import os

API_URL = "https://sonic-speed-simulator.fandom.com/api.php"
HEADERS = {"User-Agent": "SSS-Stats-Scraper/1.0"}
IMAGES_DIR = "images"


def get_category_members(category):
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": "max",
        "format": "json",
    }
    members = []
    while True:
        response = requests.get(API_URL, params=params, headers=HEADERS).json()
        if "query" not in response:
            break
        members.extend(response["query"]["categorymembers"])
        if "continue" in response:
            params.update(response["continue"])
        else:
            break
    return [m["title"] for m in members]


def parse_stat_string(s):
    stats = {}
    # Handles: +167, 1,234, 1.5K, 2M, etc.
    # Matches: VALUE [Optional Icons/File] KEYWORD
    # Keywords are XP, Rings, Damage, Power, Luck, Magnet, Stamina, Event, Steps, Air, Speed
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
            # Match: [+-]?VALUE followed by either:
            # 1. A File tag that contains the keyword in its name or alt text
            # 2. An {{Icons|keyword}} template
            # 3. The keyword itself
            pattern = (
                r"([+-]?[\d,.]+K?M?)\s*(?:"
                r"\[\[File:[^\]]*?" + keyword + r"[^\]]*?\]\]|"
                r"\{\{Icons\|" + keyword + r"\}\}|"
                r"" + keyword + r""
                r")"
            )
            # Use re.IGNORECASE for both value suffixes and keyword matches
            matches = re.findall(pattern, s, re.IGNORECASE)
            for val_str in matches:
                val_str = val_str.replace(",", "").strip()
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


def extract_rarity(wikitext, field_dict):
    r = field_dict.get("rarity", "").strip()
    if r:
        # Remove templates and icons
        r = re.sub(r"\{\{Icons\|(.*?)\}\}", r"\1", r)
        r = re.sub(r"\[\[.*?\|(.*?)\]\]", r"\1", r)
        r = re.sub(r"\[\[(.*?)\]\]", r"\1", r)
        r = r.replace("'''", "").replace("''", "").strip()
        if r and "{{" not in r:
            return r

    # Try {{Rarity|...}}
    match = re.search(r"\{\{Rarity\|(.*?)\}\}", wikitext, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Special case for Fast Friends category
    if "[[Category:Fast Friends]]" in wikitext:
        return "Fast Friend"

    # Try Categories like [[Category:Legendary Characters]]
    cat_match = re.search(
        r"\[\[Category:(Legendary|Epic|Rare|Common|Event|Special|Exclusive|Holiday)\s+(?:Characters|Friends|Trails)\]\]",
        wikitext,
        re.IGNORECASE,
    )
    if cat_match:
        return cat_match.group(1).strip()

    return "Common"


def extract_image_filename(wikitext):
    # Try common fields
    for field in ["character_image", "friend_image", "chao_picture", "image"]:
        # Find the field value. We use a more robust regex for infobox fields
        pattern = rf"\|\s*{field}\s*=\s*(.*?)(?=\s*(?:\||}}}}|$))"
        val = re.search(pattern, wikitext, re.DOTALL | re.IGNORECASE)
        if val:
            content = val.group(1).strip()
            # Check for gallery or tabber
            galleries = re.findall(r"<gallery>(.*?)</gallery>", wikitext, re.DOTALL)

            all_files = []
            for gallery in galleries:
                lines = gallery.strip().split("\n")
                for line in lines:
                    if not line.strip():
                        continue
                    parts = line.split("|")
                    fname = parts[0].strip()
                    # Remove "File:" prefix if present
                    fname = re.sub(
                        r"^(?:File|Image):", "", fname, flags=re.IGNORECASE
                    ).strip()
                    caption = parts[1].strip() if len(parts) > 1 else ""
                    all_files.append({"file": fname, "caption": caption})

            if all_files:
                # Prioritize Portrait
                for f in all_files:
                    if (
                        "portrait" in f["caption"].lower()
                        or "portrait" in f["file"].lower()
                    ):
                        return f["file"]
                # Fallback to Render
                for f in all_files:
                    if "render" in f["caption"].lower() or "render" in f["file"].lower():
                        return f["file"]
                return all_files[0]["file"]

            # Not a gallery, maybe a single filename
            fname_match = re.search(
                r"([\w\s._-]+\.(?:png|jpg|webp|gif|svg))", content, re.IGNORECASE
            )
            if fname_match:
                return fname_match.group(1).strip()

    return None


def get_image_url(filename):
    if not filename:
        return None
    params = {
        "action": "query",
        "prop": "imageinfo",
        "titles": f"File:{filename}",
        "iiprop": "url",
        "format": "json",
    }
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS).json()
        pages = response.get("query", {}).get("pages", {})
        for p in pages.values():
            if "imageinfo" in p:
                return p["imageinfo"][0]["url"]
    except Exception:
        pass
    return None


def download_image(filename, name):
    if not filename:
        return None

    url = get_image_url(filename)
    if not url:
        return None

    # Sanitize name for filename
    safe_name = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
    ext = filename.split(".")[-1]
    local_filename = f"{safe_name}.{ext}"
    local_path = os.path.join(IMAGES_DIR, local_filename)

    if os.path.exists(local_path):
        return local_path

    try:
        resp = requests.get(url, headers=HEADERS, stream=True)
        if resp.status_code == 200:
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(1024):
                    f.write(chunk)
            return local_path
    except Exception:
        pass
    return None


def get_page_stats(title):
    params = {"action": "parse", "page": title, "prop": "wikitext", "format": "json"}
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS).json()
        if "error" in response:
            return None
        wikitext = response["parse"]["wikitext"]["*"]
    except Exception:
        return None

    stats = {
        "name": title,
        "rarity": "Common",
        "base": {},
        "max": {},
        "max_fused": {},
        "image": None,
    }

    # Extract all pipe-separated fields from infoboxes
    fields = re.findall(
        r"\|\s*([\w\d\s_]+)\s*=\s*((?:[^{}|\[\{]|{{.*?}}|\[\[.*?\]\])*)", wikitext
    )
    field_dict = {k.strip().lower(): v.strip() for k, v in fields}

    stats["rarity"] = extract_rarity(wikitext, field_dict)
    img_filename = extract_image_filename(wikitext)
    stats["image"] = download_image(img_filename, title)

    def get_val(key):
        val = field_dict.get(key.lower(), "0").replace(",", "").replace("+", "").strip()
        if not val or val == "&mdash;" or val == "-" or val == "0":
            return 0
        try:
            # Handle K/M suffixes in direct fields too
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

    # Map standard fields
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

    for s_key, s_aliases in stat_map.items():
        for alias in s_aliases:
            # Check base_X_stat, max_X_stat, max_fused_X_stat
            b = get_val(f"base_{alias}_stat")
            m = get_val(f"max_{alias}_stat")
            mf = (
                get_val(f"max_fused_{alias}_stat")
                or get_val(f"base_fused_{alias}_stat")
                or get_val(f"max_level_fused_{alias}_stat")
            )

            # Also check direct alias for legacy/simple pages (e.g. | power = 6)
            if not b and s_key in ["damage", "xp", "rings"]:
                # Only do this for common base stats to avoid false positives
                b_direct = get_val(alias)
                if b_direct:
                    b = b_direct

            if b and s_key not in stats["base"]:
                stats["base"][s_key] = b
            if m and s_key not in stats["max"]:
                stats["max"][s_key] = m
            if mf and s_key not in stats["max_fused"]:
                stats["max_fused"][s_key] = mf

    # Fallback/Supplemental parsing from level_X strings
    for key, val in field_dict.items():
        target = None
        k_lower = key.lower()
        if k_lower == "tier":
            target = "base"
        elif "level_1" in k_lower or "level 1" in k_lower:
            target = "base"
        elif "level_6" in k_lower or "level 6" in k_lower:
            target = "max"
        elif "level_25" in k_lower or "level 25" in k_lower:
            if "fused" in k_lower:
                target = "max_fused"
            else:
                target = "max"

        if target:
            parsed = parse_stat_string(val)
            target_dict = stats[target]
            if isinstance(target_dict, dict):
                target_dict.update(parsed)
            if k_lower == "tier":
                max_dict = stats["max"]
                if isinstance(max_dict, dict):
                    max_dict.update(parsed)

    # If we still have no stats, maybe it's a different field name or not an item page
    if not stats["base"] and not stats["max"] and not stats["max_fused"]:
        # Characters like Sonic might just be a base character without unique stats sometimes,
        # but usually they have 'tier'. If nothing found, return None to avoid clutter.
        return None

    return stats


def main():
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

    for cat_key, cat_name in categories.items():
        print(f"Fetching {cat_name} list from Wiki...")
        items_list = get_category_members(cat_name)
        items_list = [i for i in items_list if not i.startswith("Category:")]
        expected = len(items_list)

        print(f"Processing {expected} {cat_name}...")
        for i, title in enumerate(items_list):
            if i % 20 == 0:
                print(f"  Progress: {i}/{expected}")
            res = get_page_stats(title)
            if res:
                results[cat_key].append(res)
            else:
                print(f"  Warning: No stats found for {cat_key}: {title}")
                errors.append({"name": title, "type": cat_key})
                has_error = True
            time.sleep(0.05)

        scraped = len(results[cat_key])
        if scraped != expected:
            # One trail being a stub is expected to cause a mismatch
            print(f"Notice: {cat_name} count mismatch ({scraped}/{expected})")

    print("Saving to stats.json...")
    data = {**results, "Errors": errors, "error": has_error}
    with open("stats.json", "w") as f:
        json.dump(data, f, indent=2)

    total_scraped = sum(len(results[k]) for k in categories)
    print(f"Successfully scraped {total_scraped} items across all categories.")
    if has_error:
        print("ALERT: Scraping was incomplete (expected for stubs).")


if __name__ == "__main__":
    main()
