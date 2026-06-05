"""Stellaris Tech Scanner — reads all technologies from game files.

Scans the Stellaris game directory for all tech definitions, extracts
their metadata (tier, area, prerequisites, cost, DLC), and produces
a config file the player can edit to choose which techs to randomize.

Usage:
    python tech_scanner.py scan              # Scan game files, create config
    python tech_scanner.py scan --game-dir "D:/Steam/..."  # Custom path
    python tech_scanner.py show              # Show current config
    python tech_scanner.py apply             # Generate mod files from config

The config file (ap_tech_config.json) lists every tech with a
"randomize" flag. Edit it to true/false, then run "apply" to
regenerate the mod's tech overrides and update the apworld.
"""

import argparse
import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from dataclasses import asdict, fields
from classes import TechEntry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TechScanner")


def find_game_dir() -> Optional[Path]:
    """Find Stellaris game installation by searching common Steam paths."""
    import string
    import sys

    candidates: List[Path] = [Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Stellaris"]

    # Always check Linux/Mac Steam location first
    
    # On Windows, dynamically check all available drives
    if sys.platform == "win32":
        # Get all available drive letters
        available_drives = ["C", "D", "E", "F"]
        with contextlib.suppress(BaseException):
            available_drives = [d for d in string.ascii_uppercase if Path(f"{d}:/").exists()]
            
        steam_patterns = [
            "{drive}:/Program Files (x86)/Steam/steamapps/common/Stellaris",
            "{drive}:/Program Files/Steam/steamapps/common/Stellaris",
            "{drive}:/SteamLibrary/steamapps/common/Stellaris",
            "{drive}:/Steam/steamapps/common/Stellaris",
        ]

        for drive in available_drives:
            for pattern in steam_patterns:
                candidates.append(Path(pattern.format(drive=drive)))
    else:
        # Fallback paths for non-Windows systems
        candidates.extend([
            Path.home() / "Games" / "Stellaris",
            Path("/opt/Stellaris"),
            Path("/usr/local/games/Stellaris"),
        ])

    # Search for Stellaris tech directory in candidates
    for p in candidates:
        if p.exists() and (p / "common" / "technology").exists():
            return p

    return None


def find_user_dir() -> Path:
    home = Path.home()
    for p in [
        home / "Documents" / "Paradox Interactive" / "Stellaris",
        home / "OneDrive" / "Documents" / "Paradox Interactive" / "Stellaris",
    ]:
        if p.exists():
            return p
    return home / "Documents" / "Paradox Interactive" / "Stellaris"


def extract_tech_block(content: str, tech_name: str) -> Optional[str]:
    """Extract a full tech definition block using brace counting."""
    pattern = re.compile(rf'^{re.escape(tech_name)}' + r'\s*=\s*\{', re.MULTILINE)
    m = pattern.search(content)
    if not m:
        return None
    start = m.start()
    pos = m.end()
    depth = 1
    while pos < len(content) and depth > 0:
        if content[pos] == '{':
            depth += 1
        elif content[pos] == '}':
            depth -= 1
        pos += 1
    if depth != 0:
        logger.warning(f"Unmatched braces in tech block for {tech_name}")
        return None
    return content[start:pos]


def parse_tech_files(game_dir: Path) -> List[TechEntry]:
    """Parse all technology files and extract metadata."""
    tech_dir = game_dir / "common" / "technology"

    # Mapping: tech_key -> TechEntry
    techs: List[TechEntry] = []
    
    DLC_MARKERS = {
        "ancient_relics": "Ancient Relics",
        "apocalypse": "Apocalypse",
        "distant_stars": "Distant Stars",
        "federations": "Federations",
        "megacorp": "Megacorp",
        "nemesis": "Nemesis",
        "overlord": "Overlord",
        "toxoids": "Toxoids",
        "first_contact": "First Contact",
        "astral": "Astral Planes",
        "machine_age": "Machine Age",
        "cosmic_storms": "Cosmic Storms",
    }

    for tech_file in sorted(tech_dir.glob("*.txt")):
        # Skip repeatable techs (infinite, not suitable for randomization)
        if "repeatable" in tech_file.name:
            continue

        try:
            content = tech_file.read_text(encoding="utf-8-sig", errors="replace")
        except Exception as e:
            logger.warning(f"Could not read {tech_file.name}: {e}")
            continue

        # Determine DLC from filename
        fname = tech_file.name.lower()
        dlc: str | None = None
        for marker, name in DLC_MARKERS.items():
            if marker in fname:
                dlc = name
                break

        # Find all tech definitions
        for m in re.finditer(r'^(tech_\w+)\s*=\s*\{', content, re.MULTILINE):
            tech_key : str = m[1]
            block : str | None = extract_tech_block(content, tech_key)
            if not block:
                continue

            # Parse metadata
            tier_m = re.search(r'\btier\s*=\s*(\d+)', block)
            area_m = re.search(r'\barea\s*=\s*(\w+)', block)
            cost_m = re.search(r'\bcost\s*=\s*(\d+)', block)
            cost_var_m = re.search(r'\bcost\s*=\s*@tier(\d+)cost', block)

            if cost_m:
                cost = int(cost_m[1])
            elif cost_var_m:
                # Approximate cost from tier variable: @tier1cost ≈ 500, etc.
                tier_ref = int(cost_var_m[1])
                cost = max(500, tier_ref * 600)
            else:
                cost = 0
            cat_m = re.search(r'\bcategory\s*=\s*\{\s*(\w+)', block)
            prereq_m = re.search(r'prerequisites\s*=\s*\{([^}]*)}', block)
            start_m = re.search(r'\bstart_tech\s*=\s*yes', block)
            rare_m = re.search(r'\bis_rare\s*=\s*yes', block)
            dangerous_m = re.search(r'\bis_dangerous\s*=\s*yes', block)

            tier : int = int(tier_m[1]) if tier_m else 0
            area : str = area_m[1] if area_m else "engineering"
            category : str = cat_m[1] if cat_m else ""
            prerequisites : List[str] = []
            if prereq_m:
                prerequisites = [
                    p.strip().strip('"')
                    for p in prereq_m[1].split()
                    if p.strip().strip('"').startswith("tech_")
                ]
            is_start : bool = bool(start_m)
            is_rare : bool = bool(rare_m)
            is_dangerous : bool = bool(dangerous_m)

            # Determine a human-readable name from the key
            display_name : str = tech_key.replace("tech_", "").replace("_", " ").title()

            techs.append(TechEntry(
                key=tech_key,
                name=display_name,
                tier=tier,
                area=area,
                cost=cost,
                category=category,
                prerequisites=prerequisites,
                is_start_tech=is_start,
                is_rare=is_rare,
                is_dangerous=is_dangerous,
                dlc=dlc,
                source_file=tech_file.name,
                randomize=False,
            ))

    return techs


def auto_select_defaults(techs: List[TechEntry]) -> List[TechEntry]:
    """Auto-select a sensible default set of techs to randomize.
    
    Conservative by default — selects ~60-80 landmark techs that have
    meaningful gameplay impact. Players can enable more in the config.
    """

    # Never randomize: start techs, things handled by AP progressive items,
    # minor component upgrades, bio ship parts, etc.
    skip_patterns = [
        "tech_starbase_1", "tech_starbase_2",  # start techs
        "tech_basic_",  # fundamental techs
        # AP progressive items handle these
        "tech_colossus", "tech_titans", "tech_juggernaut",
        "tech_destroyers", "tech_cruisers", "tech_battleships",
        "tech_lasers_", "tech_mass_drivers_", "tech_missiles_",
        "tech_shields_", "tech_ship_armor_",
        "tech_hyper_drive_", "tech_jump_drive", "tech_psi_jump",
        "tech_colonization_", "tech_starbase_3", "tech_starbase_4", "tech_starbase_5",
        # Ship components (too granular, not interesting as AP locations)
        "_hull_", "_build_speed", "_fire_rate", "_evasion", "_growth",
        "_healing", "_confuser", "_disruptor", "_weapon",
        "tech_strike_craft", "tech_corvette", "tech_destroyer_",
        "tech_cruiser_", "tech_battleship_",
        "tech_reactor_boosters", "tech_afterburners", "tech_thrusters_",
        "tech_combat_computers", "tech_auxiliary",
        # Storm/bio ship techs (very granular DLC components)
        "storm_", "mauler", "weaver", "harbinger", "mandible",
        "bio_integration", "gravity_snare",
        # Repeatable / L-cluster / special
        "repeatable", "l_cluster", "tech_akx_", "tech_zlb_",
    ]

    # Positive list: categories of techs that make good AP locations
    good_categories = [
        "statecraft", "new_worlds", "biology", "psionics",
        "field_manipulation", "computing", "particles",
        "industry", "materials", "propulsion", "voidcraft",
    ]

    for tech in techs:
        if tech.is_start_tech:
            continue
        if tech.tier < 1 or tech.tier > 4:
            continue
        if any(pat in tech.key for pat in skip_patterns):
            continue
        # Only select techs in good categories
        if tech.category not in good_categories:
            continue
        # Default: tier 1-3 base game techs
        if tech.tier <= 3 and not tech.dlc:
            tech.randomize = True

    return techs


def save_config(techs: List[TechEntry], config_path: Path):
    """Save tech config to JSON."""
    # Sort by area, then tier, then name for readability
    sorted_techs = sorted(
        techs,
        key=lambda x: (x.area, x.tier, x.key)
    )
    randomized_count = sum(bool(t.randomize) for t in sorted_techs)
    by_area = {
        area: sum(bool(t.area == area and t.randomize) for t in sorted_techs)
        for area in ["physics", "society", "engineering"]
    }

    config = {
        "_help": "Set 'randomize' to true/false for each tech. "
                 "Randomized techs will be replaced by AP techs in-game.",
        "_stats": {
            "total_techs": len(sorted_techs),
            "randomized": randomized_count,
            "by_area": by_area,
        },
        "techs": [asdict(t) for t in sorted_techs], # convert to dict for JSON serialization
    }

    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    logger.info(f"Saved config to {config_path}")
    logger.info(f"  Total techs: {len(sorted_techs)}")
    logger.info(f"  Randomized: {randomized_count}")
    for area in ["physics", "society", "engineering"]:
        count = by_area[area]
        logger.info(f"  {area}: {count} randomized")


def load_config(config_path: Path) -> List[TechEntry]:
    """Load tech config from JSON."""
    data = json.loads(config_path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(TechEntry)}
    return [
        TechEntry(**{k: v for k, v in item.items() if k in known})
        for item in data.get("techs", [])
    ]


def generate_overrides(techs: List[TechEntry], game_dir: Path, mod_dir: Path):
    """Generate FIOS tech override file from config."""
    tech_dir = game_dir / "common" / "technology"
    randomized = [t for t in techs if t.randomize]

    if not randomized:
        logger.warning("No techs selected for randomization!")
        return []

    # Read all tech file contents
    all_content = ""
    for tech_file in sorted(tech_dir.glob("*.txt")):
        if "repeatable" in tech_file.name:
            continue
        with contextlib.suppress(BaseException):
            all_content += tech_file.read_text(encoding="utf-8-sig", errors="replace") + "\n"

    output_lines = [
        "# ==========================================================================",
        "# Vanilla tech overrides for Archipelago (auto-generated by tech_scanner.py)",
        "# Each randomized tech has an added potential block that hides it when",
        "# its blocking flag is set by the AP bridge.",
        "# FIOS: this file (00_aaa_*) loads before vanilla files.",
        "# ==========================================================================",
        "",
    ]

    blocked = []
    for tech in sorted(randomized, key=lambda t: t.key):
        tech_key = tech.key
        block = extract_tech_block(all_content, tech_key)
        if not block:
            logger.warning(f"Could not find definition for {tech_key}")
            continue

        # Inject blocking flag into potential
        if pot_match := re.search(r'(\t*)potential\s*=\s*\{', block):
            indent = pot_match[1]
            insert_pos = pot_match.end()
            injection = f"\n{indent}\tNOT = {{ has_country_flag = ap_tech_blocked_{tech_key} }}"
            block = block[:insert_pos] + injection + block[insert_pos:]
        else:
            first_brace = block.index('{')
            injection = (
                f"\n\tpotential = {{\n"
                f"\t\tNOT = {{ has_country_flag = ap_tech_blocked_{tech_key} }}\n"
                f"\t}}\n"
            )
            block = block[:first_brace+1] + injection + block[first_brace+1:]

        output_lines.extend((block, ""))
        blocked.append(tech_key)

    # Write override file
    out_dir = mod_dir / "common" / "technology"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "00_aaa_ap_tech_blocks.txt"
    out_path.write_text("\n".join(output_lines), encoding="utf-8")
    logger.info(f"Generated {len(blocked)} tech overrides in {out_path}")

    return blocked


def generate_vanilla_tech_data(techs: List[TechEntry]) -> str:
    """Generate VANILLA_TECH_DATA dict for slot_generator.py."""
    randomized = [t for t in techs if t.randomize]

    lines = ["VANILLA_TECH_DATA = {"]
    for tech in sorted(randomized, key=lambda t: t.key):
        prerequisites_str = str(tech.prerequisites)
        lines.append(
            f'    "{tech.key}": {{"tier": {tech.tier}, "area": "{tech.area}", "prerequisites": {prerequisites_str}}},'
        )
    lines.extend(("}", "", "LOCATION_TO_VANILLA_TECH = {"))
    for tech in sorted(randomized, key=lambda t: t.key):
        display = tech.name
        loc_name = f"Research {display}"
        lines.append(f'    "{loc_name}": "{tech.key}",')
    lines.append("}")

    return "\n".join(lines)


# =========================================================================
# Commands
# =========================================================================

def cmd_scan(game_dir: Optional[Path] = None):
    """Scan game files and create config."""
    if not game_dir:
        game_dir = find_game_dir()
    if not game_dir:
        print("ERROR: Could not find Stellaris game directory.")
        print("Use: python tech_scanner.py scan --game-dir \"C:/path/to/Stellaris\"")
        return

    logger.info(f"Scanning: {game_dir}")
    techs = parse_tech_files(game_dir)
    logger.info(f"Found {len(techs)} technologies")

    techs = auto_select_defaults(techs)

    config_path = find_user_dir() / "ap_tech_config.json"
    save_config(techs, config_path)

    print(f"\nConfig saved to: {config_path}")
    print(f"\nEdit the file to set 'randomize' to true/false for each tech.")
    print("Then run: python tech_scanner.py apply")

    # Summary by area and tier
    randomized = [t for t in techs if t.randomize]
    print(f"\nDefault selection: {len(randomized)} techs randomized out of {len(techs)}")
    for area in ["physics", "society", "engineering"]:
        area_techs = [t for t in randomized if t.area == area]
        print(f"  {area}: {len(area_techs)}")
        for tier in range(1, 6):
            if tier_techs := [t for t in area_techs if t.tier == tier]:
                print(f"    Tier {tier}: {', '.join(t.name for t in tier_techs[:5])}"
                      + (f" +{len(tier_techs)-5} more" if len(tier_techs) > 5 else ""))


def cmd_show():
    """Show current config."""
    config_path = find_user_dir() / "ap_tech_config.json"
    if not config_path.exists():
        print("No config found. Run: python tech_scanner.py scan")
        return

    techs = load_config(config_path)
    randomized = [t for t in techs if t.randomize]

    print(f"Randomized techs: {len(randomized)} / {len(techs)}\n")
    for area in ["physics", "society", "engineering"]:
        if area_techs := sorted(
            [t for t in randomized if t.area == area], key=lambda x: x.tier
        ):
            print(f"  {area.upper()} ({len(area_techs)}):")
            for tech in area_techs:
                dlc = f" [{tech.dlc}]" if tech.dlc else ""
                prereq = f" (requires: {', '.join(tech.prerequisites)})" if tech.prerequisites else ""
                print(f"    T{tech.tier} {tech.name}{dlc}{prereq}")
            print()


def cmd_apply(game_dir: Optional[Path] = None):
    """Generate mod files from config."""
    if not game_dir:
        game_dir = find_game_dir()
    if not game_dir:
        print("ERROR: Could not find Stellaris game directory.")
        return

    config_path = find_user_dir() / "ap_tech_config.json"
    if not config_path.exists():
        print("No config found. Run: python tech_scanner.py scan")
        return

    techs = load_config(config_path)
    user_dir = find_user_dir()
    mod_dir = user_dir / "mod" / "archipelago_multiworld"

    # Generate tech override file
    blocked = generate_overrides(techs, game_dir, mod_dir)

    # Generate data for slot_generator
    data_str = generate_vanilla_tech_data(techs)
    data_path = user_dir / "ap_tech_data.py"
    data_path.write_text(data_str, encoding="utf-8")
    logger.info(f"Saved slot_generator data to {data_path}")
    randomized = [t for t in techs if t.randomize]
    print(f"\nApplied: {len(blocked)} tech overrides generated")
    print(f"Location data: {len(randomized)} techs ready for AP world")
    print(f"\nThe mod's tech overrides have been updated.")
    print("Restart Stellaris for changes to take effect.")


def main():
    parser = argparse.ArgumentParser(description="Stellaris Tech Scanner")
    parser.add_argument("command", choices=["scan", "show", "apply"])
    parser.add_argument("--game-dir", type=Path, default=None, help="Stellaris game directory")
    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args.game_dir)
    elif args.command == "show":
        cmd_show()
    elif args.command == "apply":
        cmd_apply(args.game_dir)


if __name__ == "__main__":
    main()
