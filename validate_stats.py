import json
import os
from collections import defaultdict

def validate():
    if not os.path.exists("stats.json"):
        print("stats.json not found")
        return

    with open("stats.json", "r") as f:
        data = json.load(f)

    categories = ["Characters", "Friends", "Fast Friends", "Trails"]
    
    issues = []
    missing_fused = []

    for cat in categories:
        for item in data.get(cat, []):
            name = item.get("name")
            rarity = item.get("rarity", "")
            
            # Check for HTML in rarity
            if rarity and ("<" in rarity or ">" in rarity):
                issues.append(f"HTML in rarity: {name} ({cat}) - '{rarity}'")
            
            # Check Avatar
            if name == "Avatar" and cat == "Characters":
                max_stats = item.get("max", {})
                if max_stats.get("xp") != "unknown":
                    issues.append(f"Avatar stats not marked unknown: {name}")

            # Check for empty max_fused in Friends and Trails
            if cat in ["Friends", "Trails"]:
                max_fused = item.get("max_fused", {})
                # Only check if it's NOT an error item
                if not max_fused and "reason" not in item:
                    missing_fused.append(f"{name} ({cat})")

            # Check image existence
            img_path = item.get("image")
            if img_path and not os.path.exists(img_path):
                issues.append(f"Image missing: {name} ({img_path})")

    if issues:
        print(f"Found {len(issues)} critical issues:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("No critical issues found (HTML rarity, Avatar stats, Missing images).")

    if missing_fused:
        print(f"\nObservation: {len(missing_fused)} items are missing 'max_fused' stats.")
        for m in missing_fused:
            print(f"  - {m}")

    # Check Errors
    errors = data.get("Errors", [])
    if errors:
        print(f"\nErrors found in stats.json: {len(errors)}")
        
        # Group errors by category and then by reason
        error_groups = defaultdict(lambda: defaultdict(list))
        for err in errors:
            cat = err.get("type") or "Unknown Category"
            reason = err.get("reason") or "Unknown reason"
            name = err.get("name", "Unknown")
            error_groups[cat][reason].append(name)
            
        for cat, reasons in error_groups.items():
            print(f"\nCategory: {cat}")
            for reason, names in reasons.items():
                print(f"  - {len(names)} items failed because: {reason}")
                print(f"    Names: {', '.join(names)}")

if __name__ == "__main__":
    validate()
