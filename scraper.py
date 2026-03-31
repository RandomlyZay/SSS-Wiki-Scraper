import httpx
import asyncio
import re
import json
import os
import aiofiles
import mwparserfromhell

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
            return None
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error for {description}: {e.response.status_code}")
        return None
    except Exception as e:
        print(f"Unexpected error for {description}: {e}")
        return None


def parse_stat_string(s, title="", context=""):
    stats = {}
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
            # Pattern 1: Number + Keyword/Icon (e.g., +7,608 {{Icons|XP}})
            # We want to match the number, then optional space, then either:
            # - [[File:..keyword..]]
            # - {{Icons|..keyword..}}
            # - raw keyword
            pattern = (
                r"([+-]?[\d,.]+K?M?|∞)\s*(?:"
                r"\[\[File:[^\]]*?" + keyword + r"[^\]]*?\]\]|"
                r"\{\{Icons\|" + keyword + r"(?:\|[^}]*)?\}\}|"
                r"\b" + keyword + r"\b"
                r")"
            )
            matches = re.findall(pattern, s, re.IGNORECASE)
            for val_str in matches:
                val_str = val_str.replace(",", "").strip()
                if val_str == "∞":
                    stats[stat] = "Infinity"
                    continue

                multiplier = 1
                val_upper = val_str.upper()
                if "K" in val_upper:
                    multiplier = 1000
                    val_str = val_upper.replace("K", "")
                elif "M" in val_upper:
                    multiplier = 1000000
                    val_str = val_upper.replace("M", "")

                try:
                    val = float(val_str) * multiplier
                    stats[stat] = int(val)
                except ValueError:
                    pass
            
            # Pattern 2: Keyword/Icon + Number (e.g., XP: 100)
            if stat not in stats:
                pattern_rev = (
                    r"(?:"
                    r"\[\[File:[^\]]*?" + keyword + r"[^\]]*?\]\]|"
                    r"\{\{Icons\|" + keyword + r"(?:\|[^}]*)?\}\}|"
                    r"\b" + keyword + r"\b"
                    r")\s*[:\-]?[+-]?\s*([\d,.]+K?M?|∞)"
                )
                matches_rev = re.findall(pattern_rev, s, re.IGNORECASE)
                for val_str in matches_rev:
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

    # Special Case: Fast Friends secondary stats often don't list the number in the infobox param
    # but the page text says "provides a +12 benefit at star level six"
    if "Fast Friends" in context and not stats:
        for stat, keywords in stat_keywords.items():
            for keyword in keywords:
                if keyword.lower() in s.lower():
                    # Look for the standard +12 or +20 or +15 or +18 in the whole page text
                    m = re.search(r"\+?(\d+)\s+benefit\s+at\s+star\s+level\s+six", context, re.IGNORECASE)
                    if m:
                        stats[stat] = int(m.group(1))
                    else:
                        # Fallback for some common values if we find the keyword but no number
                        if keyword.lower() in s.lower():
                           if "Magnet" in keyword:
                               stats[stat] = 12
                           elif "Luck" in keyword:
                               stats[stat] = 12
                           elif "Stamina" in keyword:
                               stats[stat] = 12
                           elif "Event" in keyword:
                               stats[stat] = 10
                           elif "Air" in keyword:
                               stats[stat] = 12

    return stats


def clean_wikitext(s):
    if not s:
        return ""
    # Replace <br> tags with newlines before stripping HTML
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    # Strip HTML tags
    s = re.sub(r"<[^>]*>", "", s)
    # mwparserfromhell safely strips out wiki markup to leave raw text
    parsed = mwparserfromhell.parse(s)
    return parsed.strip_code().strip()


def extract_rarity(wikitext, field_dict):
    r = field_dict.get("rarity", "").strip()
    if r:
        r = clean_wikitext(r)
        if r:
            # Basic sanity check to avoid returning whole templates or nonsense
            if len(r) < 30 and "{{" not in r:
                return r

    match = re.search(r"\{\{Rarity\|(.*?)\}\}", wikitext, re.IGNORECASE)
    if match:
        return clean_wikitext(match.group(1))

    if "[[Category:Fast Friends]]" in wikitext:
        return "Fast Friend"

    cat_match = re.search(
        r"\[\[Category:(Legendary|Epic|Rare|Common|Event|Special|Exclusive|Holiday)\s+(?:Characters|Friends|Trails)\]\]",
        wikitext,
        re.IGNORECASE,
    )
    if cat_match:
        return cat_match.group(1).strip()

    return "Common"


def extract_image_filename(wikitext):
    # Added image1-9 to handle Chao infobox 2.0 and other variations
    image_fields = ["character_image", "friend_image", "chao_picture", "image"]
    for i in range(1, 10):
        image_fields.append(f"image{i}")

    for field in image_fields:
        pattern = rf"\|\s*{field}\s*=\s*(.*?)(?=\s*(?:\||}}}}|$))"
        val = re.search(pattern, wikitext, re.DOTALL | re.IGNORECASE)
        if val:
            content = val.group(1).strip()
            galleries = re.findall(r"<gallery>(.*?)</gallery>", wikitext, re.DOTALL)
            tabbers = re.findall(r"<tabber>(.*?)</tabber>", wikitext, re.DOTALL)

            all_content = content + "\n" + "\n".join(galleries) + "\n" + "\n".join(tabbers)
            files = re.findall(r"([\w\s._-]+\.(?:png|jpg|webp|gif|svg))", all_content, re.IGNORECASE)
            
            if files:
                for f in files:
                    if "portrait" in f.lower():
                        return f.strip()
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
            resp.raise_for_status()
            # Issue 2 Fix: Asynchronous file writing
            async with aiofiles.open(local_path, "wb") as f:
                async for chunk in resp.aiter_bytes(1024):
                    await f.write(chunk)
            return local_path
    except Exception as e:
        # Issue 3 Fix: Catching and logging the actual exception instead of failing silently
        print(f"Error downloading image for {name} ({filename}): {e}")
        return None


async def get_page_stats(title, category, client):
    params = {"action": "parse", "page": title, "prop": "wikitext|text", "format": "json"}
    response = await fetch_json(client, params, f"stats for {title}")
    if not response or "error" in response:
        return None
        
    wikitext = response["parse"]["wikitext"]["*"]
    html_text = response["parse"]["text"]["*"]

    # Issue 4 Fix: Using mwparserfromhell to robustly extract template parameters
    # NEW: Clean style tags that might confuse mwparserfromhell (e.g., unclosed bold/italic)
    wikitext_clean = re.sub(r"'{2,5}", "", wikitext)
    parsed_wikicode = mwparserfromhell.parse(wikitext_clean)
    
    # Check for NPC or Stub categories/templates to avoid "Error" marking
    is_npc = "[[Category:NPC]]" in wikitext or "{{SonicCharacterInfobox" in wikitext and "NPC" in wikitext
    is_stub = "{{SonicUnfinishedStub" in wikitext or "{{Stub" in wikitext
    
    # Find the main infobox template
    infobox = None
    all_templates = parsed_wikicode.filter_templates()
    for t in all_templates:
        name = str(t.name).strip().lower()
        if "infobox" in name:
            infobox = t
            # If we find a specific game infobox, prefer it over generic ones
            if "sonic" in name or "chao" in name or "fastfriend" in name or "character" in name:
                break
            
    if not infobox and all_templates:
        # Fallback to the first template that has many parameters
        for t in all_templates:
            if len(t.params) > 3:
                infobox = t
                break

    field_dict = {}
    if infobox:
        for param in infobox.params:
            field_dict[param.name.strip().lower()] = str(param.value).strip()

    # Helper to extract stats from rendered HTML (captures calculated/template stats)
    def extract_html_stats(html):
        extracted = {"max": {}, "max_fused": {}, "base": {}}
        # Fandom portable infoboxes use data-source on many types of elements, not just divs.
        # We look for ANY element with data-source, then find the numeric value within its content.
        # This regex looks for the data-source attribute, then captures until the next tag starts,
        # or captures the whole element's inner text.
        
        # Strategy: find the start of a tag with data-source, then find the next closing tag of the SAME type or just any numeric value before a big break.
        # Actually, simpler: find all occurrences of data-source="...", then find the first number that follows it before another data-source.
        
        # Let's find all data-source tags and their following text (stripped of HTML)
        tags = re.split(r'data-source="([^"]+)"', html, flags=re.IGNORECASE)
        # re.split will give [text_before, source1, text_between1_2, source2, ...]
        
        stat_keys = ["xp", "rings", "damage", "luck", "magnet", "stamina", "event", "steps", "air", "speed"]
        
        for i in range(1, len(tags), 2):
            source = tags[i].lower()
            following_text = tags[i+1]
            # Limit the search range to avoid bleeding into other data-sources
            # Usually the value is very close to the data-source attribute
            # We'll take the first 200 characters of the following text
            sample = following_text[:200]
            # Strip tags from the sample
            clean_sample = re.sub(r"<[^>]*>", " ", sample).strip()
            
            # Extract the numeric part
            match_val = re.search(r'([+-]?[\d,.]+K?M?|∞)', clean_sample, re.IGNORECASE)
            if not match_val:
                continue
                
            val_str = match_val.group(1).replace("+", "").replace(",", "").strip()
            
            val = 0
            if val_str == "∞":
                val = "Infinity"
            else:
                try:
                    multiplier = 1
                    if "K" in val_str.upper():
                        multiplier = 1000
                        val_str = val_str.upper().replace("K", "")
                    elif "M" in val_str.upper():
                        multiplier = 1000000
                        val_str = val_str.upper().replace("M", "")
                    val = int(float(val_str) * multiplier)
                except (ValueError, TypeError):
                    continue

            # Map to structure based on data-source name
            target_key = None
            if "max_fused" in source:
                target_key = "max_fused"
            elif "max" in source:
                target_key = "max"
            elif "base" in source:
                target_key = "base"
            
            if target_key:
                for key in stat_keys:
                    # Check for exact matches or common patterns like max_fused_xp_stat
                    if f"_{key}_" in source or source.endswith(f"_{key}_stat") or source == f"{key}_stat":
                        extracted[target_key][key] = val
                        break

        # Cleanup empty dicts
        return {k: v for k, v in extracted.items() if v}

    html_stats = extract_html_stats(html_text)
    rarity = extract_rarity(wikitext, field_dict)
    img_filename = extract_image_filename(wikitext)
    image_path = await download_image(img_filename, title, client)

    if category == "Characters":
        max_stats = html_stats.get("max", {})
        abilities = []

        # If HTML didn't yield max stats, try tier field as fallback
        if not max_stats:
            tier_val = field_dict.get("tier", "")
            if tier_val:
                max_stats.update(parse_stat_string(tier_val, title, category + " " + wikitext))

        abilities_val = field_dict.get("abilities", "")
        if abilities_val:
            # First try parsing as a list if there are links
            parsed_abilities = mwparserfromhell.parse(abilities_val)
            links = parsed_abilities.filter_wikilinks()
            if links:
                 # Extract the title of the link
                 abilities = [str(l.title).strip() for l in links]
            else:
                 # Clean wikitext (now handles <br>) and split
                 cleaned = clean_wikitext(abilities_val)
                 if cleaned:
                     abilities = [a.strip() for a in re.split(r",|;| and |\*|\n", cleaned, flags=re.IGNORECASE) if a.strip()]
        
        if title == "Avatar":
            return {
                "name": title,
                "rarity": rarity,
                "max": {"xp": "unknown", "rings": "unknown", "damage": "unknown"},
                "abilities": abilities,
                "image": image_path
            }

        if not max_stats and not abilities:
            if is_npc:
                return {"name": title, "rarity": rarity, "image": image_path, "type": "NPC"}
            return {"name": title, "rarity": rarity, "image": image_path, "reason": "No stats or abilities found"}
            
        return {
            "name": title,
            "rarity": rarity,
            "max": max_stats,
            "abilities": abilities,
            "image": image_path
        }

    elif category == "Fast Friends":
        levels = {}
        # Try to parse levels from HTML if they exist in data-sources (unlikely but possible)
        # Fallback to wikitext levels which are standard for FF
        for i in range(1, 7):
            lvl_val = field_dict.get(f"level_{i}") or field_dict.get(f"level {i}")
            if lvl_val:
                levels[str(i)] = parse_stat_string(lvl_val, title, category + " " + wikitext)
        
        if not levels and is_stub:
             return {"name": title, "rarity": rarity, "image": image_path, "status": "Stub"}
             
        if not levels:
            return {"name": title, "rarity": rarity, "image": image_path, "reason": "No levels found for Fast Friend"}

        secondary = field_dict.get("secondary_stat") or field_dict.get("secondary stat")
        if secondary:
            sec_stats = parse_stat_string(secondary, title, category + " " + wikitext)
            if sec_stats:
                if "6" not in levels:
                    levels["6"] = {}
                levels["6"].update(sec_stats)

        return {
            "name": title,
            "rarity": rarity,
            "levels": levels,
            "image": image_path
        }

    else: # Friends and Trails
        max_stats = html_stats.get("max", {})
        max_fused = html_stats.get("max_fused", {})
        base_stats = html_stats.get("base", {})

        # Fallback to wikitext if HTML extraction failed or was incomplete
        if not max_stats:
            for field in ["level_25_stats", "level 25 stats", "level_6", "level 6"]:
                val = field_dict.get(field)
                if val:
                    max_stats.update(parse_stat_string(val, title, category + " " + wikitext))
                    if max_stats:
                        break
            
        if not max_fused:
            for field in ["level_25_fused_stats", "level 25 fused stats"]:
                val = field_dict.get(field)
                if val:
                    max_fused.update(parse_stat_string(val, title, category + " " + wikitext))

        # Check for errors (level 1 only)
        # If we have base stats but NO max stats (even after fallbacks), it's an error
        if not max_stats and (base_stats or field_dict.get("level_1_stats") or field_dict.get("level 1 stats")):
            return {
                "name": title, 
                "rarity": rarity, 
                "image": image_path, 
                "type": category, 
                "reason": "Only level 1/base stats available"
            }

        if not max_stats and not max_fused:
            if is_stub:
                 return {"name": title, "rarity": rarity, "image": image_path, "status": "Stub"}
            return {"name": title, "rarity": rarity, "image": image_path, "reason": "No stats found for Friend/Trail"}

        return {
            "name": title,
            "rarity": rarity,
            "max": max_stats,
            "max_fused": max_fused,
            "image": image_path
        }


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


# Issue 1 & 5 Fix: Worker function to consume queue items safely
async def worker(queue, client, results, errors, progress_tracker):
    while True:
        task = await queue.get()
        if task is None:
            break
        
        title, category = task
        try:
            res = await get_page_stats(title, category, client)
            if res:
                # Always add to category
                results[category].append(res)
                # If it has a reason, it's also an error
                if "reason" in res:
                    errors.append(res)
            else:
                # This should theoretically not happen anymore as get_page_stats returns a dict with reason
                err = {"name": title, "type": category, "reason": "No stats found or insufficient data"}
                errors.append(err)
                results[category].append(err)
        except Exception as e:
            print(f"Critical error processing {title}: {e}")
            err = {"name": title, "type": category, "reason": str(e)}
            errors.append(err)
            results[category].append(err)
        finally:
            progress_tracker['completed'] += 1
            if progress_tracker['completed'] % 25 == 0:
                print(f"  Progress: {progress_tracker['completed']}/{progress_tracker['total']}")
            queue.task_done()


async def main():
    if not os.path.exists(IMAGES_DIR):
        os.makedirs(IMAGES_DIR)

    categories = {
        "Characters": "Characters",
        "Friends": "Friends",
        "Fast Friends": "Fast Friends",
        "Trails": "Trails",
    }

    results = {cat_key: [] for cat_key in categories}
    errors = []

    async with httpx.AsyncClient(base_url=BASE_URL, headers=HEADERS, timeout=60.0) as client:
        # Set up the Queue instead of front-loading tasks
        queue = asyncio.Queue()
        progress_tracker = {'completed': 0, 'total': 0}
        
        # Spin up concurrent workers
        workers = [
            asyncio.create_task(worker(queue, client, results, errors, progress_tracker)) 
            for _ in range(CONCURRENCY)
        ]

        for cat_key, cat_name in categories.items():
            print(f"Fetching {cat_name} list from Wiki...")
            items_list = await get_category_members(cat_name, client)
            progress_tracker['total'] += len(items_list)

            print(f"Queuing {len(items_list)} {cat_name} for processing...")
            for title in items_list:
                queue.put_nowait((title, cat_key))

        # Wait until the queue is completely empty
        await queue.join()

        # Kill the workers by sending None tokens
        for _ in range(CONCURRENCY):
            await queue.put(None)
        await asyncio.gather(*workers)

    print("Saving to stats.json...")

    for cat in results:
        results[cat].sort(key=lambda x: x.get("name", ""))
    errors.sort(key=lambda x: x.get("name", ""))

    data = {**results, "Errors": errors, "error": len(errors) > 0}
    with open("stats.json", "w") as f:
        json.dump(data, f, indent=2, sort_keys=False)

    print("Cleaning up unreferenced images...")
    all_referenced_images = set()
    # Collect from results
    for cat in results:
        for item in results[cat]:
            img_path = item.get("image")
            if img_path:
                all_referenced_images.add(os.path.basename(img_path))
    # Collect from errors (some errors/stubs might have images)
    for err in errors:
        img_path = err.get("image")
        if img_path:
            all_referenced_images.add(os.path.basename(img_path))
    
    removed_count = 0
    if os.path.exists(IMAGES_DIR):
        for filename in os.listdir(IMAGES_DIR):
            if filename not in all_referenced_images:
                try:
                    os.remove(os.path.join(IMAGES_DIR, filename))
                    removed_count += 1
                except Exception as e:
                    print(f"Error removing {filename}: {e}")
    
    print(f"Removed {removed_count} unreferenced images.")

    total_scraped = sum(len(results[k]) for k in categories)
    print(f"Successfully scraped {total_scraped} items. Encountered {len(errors)} errors.")


if __name__ == "__main__":
    asyncio.run(main())