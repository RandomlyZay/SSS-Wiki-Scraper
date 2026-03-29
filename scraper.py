import requests
import re
import json
import time

API_URL = "https://sonic-speed-simulator.fandom.com/api.php"
HEADERS = {"User-Agent": "SSS-Stats-Scraper/1.0"}


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


def get_page_stats(title):
    params = {"action": "parse", "page": title, "prop": "wikitext", "format": "json"}
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS).json()
        if "error" in response:
            return None
        wikitext = response["parse"]["wikitext"]["*"]
    except Exception:
        return None

    stats = {"name": title, "base": {}, "max": {}, "max_fused": {}}

    # Extract all pipe-separated fields from infoboxes
    # Keys can have spaces, values can have nested templates like {{Icons|...}} or [[File:...]]
    # We exclude { and [ from the general character match to force the specialized template/file matches
    fields = re.findall(r"\|\s*([\w\d\s_]+)\s*=\s*((?:[^{}|\[\{]|{{.*?}}|\[\[.*?\]\])*)", wikitext)
    field_dict = {k.strip().lower(): v.strip() for k, v in fields}


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
    # Keys like level_1_stats, level 25 stats, level_25_fused_stats, etc.
    # Also handle Fast Friends (level_1 to level_6) and transformations (tier)
    for key, val in field_dict.items():
        target = None
        k_lower = key.lower()
        if k_lower == "tier":
            # For transformations, tier stats are often both base and max
            target = "base" # Also add to max below
        elif "level_1" in k_lower or "level 1" in k_lower:
            target = "base"
        elif "level_6" in k_lower or "level 6" in k_lower:
             # Fast Friends max at level 6
            target = "max"
        elif "level_25" in k_lower or "level 25" in k_lower:
            if "fused" in k_lower:
                target = "max_fused"
            else:
                target = "max"
        
        if target:
            parsed = parse_stat_string(val)
            stats[target].update(parsed)
            if k_lower == "tier":
                stats["max"].update(parsed)

    # If we still have no stats, maybe it's a different field name or not an item page
    if not stats["base"] and not stats["max"] and not stats["max_fused"]:
        return None

    return stats


def main():
    print("Fetching lists from Wiki...")
    friends_list = get_category_members("Friends")
    trails_list = get_category_members("Trails")

    # Filter out categories or meta-pages if any
    friends_list = [f for f in friends_list if not f.startswith("Category:")]
    trails_list = [t for t in trails_list if not t.startswith("Category:")]

    expected_friends = len(friends_list)
    expected_trails = len(trails_list)

    data = {"Friends": [], "Trails": [], "Errors": [], "error": False}

    print(f"Processing {expected_friends} Friends...")
    for i, title in enumerate(friends_list):
        if i % 20 == 0:
            print(f"  Progress: {i}/{expected_friends}")
        res = get_page_stats(title)
        if res:
            data["Friends"].append(res)
        else:
            print(f"  Warning: No stats found for Friend: {title}")
            data["Errors"].append({"name": title, "type": "Friend"})
            data["error"] = True
        time.sleep(0.05)

    print(f"Processing {expected_trails} Trails...")
    for i, title in enumerate(trails_list):
        if i % 20 == 0:
            print(f"  Progress: {i}/{expected_trails}")
        res = get_page_stats(title)
        if res:
            data["Trails"].append(res)
        else:
            print(f"  Warning: No stats found for Trail: {title}")
            data["Errors"].append({"name": title, "type": "Trail"})
            data["error"] = True
        time.sleep(0.05)

    scraped_friends = len(data["Friends"])
    scraped_trails = len(data["Trails"])

    if scraped_friends != expected_friends or scraped_trails != expected_trails:
        print("Error: Scraped counts do not match expected counts!")
        print(f"Friends: {scraped_friends}/{expected_friends}")
        print(f"Trails: {scraped_trails}/{expected_trails}")
        data["error"] = True

    print("Saving to stats.json...")
    with open("stats.json", "w") as f:
        json.dump(data, f, indent=2)

    print(
        f"Successfully scraped {scraped_friends} Friends and {scraped_trails} Trails."
    )
    if data["error"]:
        print("ALERT: Scraping was incomplete. Check stats.json 'error' flag.")


if __name__ == "__main__":
    main()
