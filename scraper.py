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
    patterns = {
        "xp": r"([+-]?[\d,.]+K?M?)\s*(?:\[\[File:.*?\]\]\s*)?XP",
        "rings": r"([+-]?[\d,.]+K?M?)\s*(?:\[\[File:.*?\]\]\s*)?Rings?",
        "damage": r"([+-]?[\d,.]+K?M?)\s*(?:\[\[File:.*?\]\]\s*)?Damage",
        "luck": r"([+-]?[\d,.]+K?M?)\s*(?:\[\[File:.*?\]\]\s*)?Luck",
    }

    for stat, pattern in patterns.items():
        match = re.search(pattern, s, re.IGNORECASE)
        if match:
            val_str = match.group(1).replace(",", "").strip()
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
    fields = re.findall(r"\|\s*([\w\d_]+)\s*=\s*([^|}]*)", wikitext)
    field_dict = {k.strip(): v.strip() for k, v in fields}

    def get_val(key):
        val = field_dict.get(key, "0").replace(",", "").replace("+", "").strip()
        if not val or val == "&mdash;" or val == "-":
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
        "damage": ["damage"],
        "luck": ["luck"],
    }

    for s_key, s_aliases in stat_map.items():
        for alias in s_aliases:
            b = get_val(f"base_{alias}_stat")
            m = get_val(f"max_{alias}_stat")
            mf = (
                get_val(f"max_fused_{alias}_stat")
                or get_val(f"base_fused_{alias}_stat")
                or get_val(f"max_level_fused_{alias}_stat")
            )

            if b and s_key not in stats["base"]:
                stats["base"][s_key] = b
            if m and s_key not in stats["max"]:
                stats["max"][s_key] = m
            if mf and s_key not in stats["max_fused"]:
                stats["max_fused"][s_key] = mf

    # Fallback to level_X_stats strings
    if "level_1_stats" in field_dict:
        stats["base"].update(parse_stat_string(field_dict["level_1_stats"]))
    if "level_25_stats" in field_dict:
        stats["max"].update(parse_stat_string(field_dict["level_25_stats"]))
    if "level_25_fused_stats" in field_dict:
        stats["max_fused"].update(parse_stat_string(field_dict["level_25_fused_stats"]))

    # Final fallback: If max exists but max_fused doesn't, apply 5x multiplier
    if stats["max"] and not stats["max_fused"]:
        for s, v in stats["max"].items():
            stats["max_fused"][s] = v * 5

    # If we still have no stats, maybe it's a different field name or not an item page
    if not stats["base"] and not stats["max"]:
        return None

    return stats


def main():
    print("Fetching lists from Wiki...")
    friends_list = get_category_members("Friends")
    trails_list = get_category_members("Trails")

    # Filter out categories or meta-pages if any
    friends_list = [f for f in friends_list if not f.startswith("Category:")]
    trails_list = [t for t in trails_list if not t.startswith("Category:")]

    data = {"Friends": [], "Trails": []}

    print(f"Processing {len(friends_list)} Friends...")
    for i, title in enumerate(friends_list):
        if i % 20 == 0:
            print(f"  Progress: {i}/{len(friends_list)}")
        res = get_page_stats(title)
        if res:
            data["Friends"].append(res)
        time.sleep(0.05)

    print(f"Processing {len(trails_list)} Trails...")
    for i, title in enumerate(trails_list):
        if i % 20 == 0:
            print(f"  Progress: {i}/{len(trails_list)}")
        res = get_page_stats(title)
        if res:
            data["Trails"].append(res)
        time.sleep(0.05)

    print("Saving to stats.json...")
    with open("stats.json", "w") as f:
        json.dump(data, f, indent=2)

    print(
        f"Successfully scraped {len(data['Friends'])} Friends and {len(data['Trails'])} Trails."
    )


if __name__ == "__main__":
    main()
