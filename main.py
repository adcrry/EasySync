from argparse import ArgumentParser
import json
from pathlib import Path

from config import BackupConfig, get_current_computer_identifiers
from file_manager import (
    build_restore_plan,
    build_backup_plan,
    execute_restore_plan,
    execute_backup_plan,
    format_diff_git_style,
    get_conflict_diff,
    summarize_restore_plan,
    summarize_plan,
)

ANSI_RESET = "\033[0m"
ANSI_YELLOW = "\033[33m"


def _resolve_computer_id(explicit_computer_id):
    if explicit_computer_id:
        return explicit_computer_id

    detected_ids = get_current_computer_identifiers()
    if not detected_ids:
        raise ValueError(
            "Unable to detect a computer id. Provide one with --computer-id."
        )
    return detected_ids[0]


def _load_config_json(config_path):
    config_file = Path(config_path)
    if not config_file.exists():
        return {"profiles": []}

    with open(config_file, "r", encoding="utf-8") as file_handle:
        json_data = json.load(file_handle)

    if not isinstance(json_data, dict):
        raise ValueError("Config root must be a JSON object")

    profiles = json_data.get("profiles")
    if profiles is None:
        json_data["profiles"] = []
    elif not isinstance(profiles, list):
        raise ValueError("Config field 'profiles' must be a JSON array")

    return json_data


def _save_config_json(config_path, json_data):
    config_file = Path(config_path)
    with open(config_file, "w", encoding="utf-8") as file_handle:
        json.dump(json_data, file_handle, indent=2)
        file_handle.write("\n")


def _find_profile_json(json_data, profile_name):
    for profile in json_data.get("profiles", []):
        if profile.get("name") == profile_name:
            return profile
    return None


def _add_profile(args):
    if not args.source_folder:
        print("--source-folder is required with --add-profile")
        return
    if not args.destination_folder:
        print("At least one --destination-folder is required with --add-profile")
        return

    computer_id = _resolve_computer_id(args.computer_id)
    json_data = _load_config_json(args.config)
    existing_profile = _find_profile_json(json_data, args.add_profile)
    if existing_profile is not None:
        print(f"Profile already exists: {args.add_profile}")
        return

    unique_destinations = []
    for destination in args.destination_folder:
        if destination not in unique_destinations:
            unique_destinations.append(destination)

    missing_destinations = [
        destination
        for destination in unique_destinations
        if not Path(destination).exists() or not Path(destination).is_dir()
    ]

    json_data["profiles"].append(
        {
            "name": args.add_profile,
            "computers": {
                computer_id: {
                    "source": args.source_folder,
                    "destinations": unique_destinations,
                }
            },
        }
    )

    _save_config_json(args.config, json_data)
    print(f"Added profile '{args.add_profile}' for computer '{computer_id}'.")
    if missing_destinations:
        print(
            f"{ANSI_YELLOW}Warning: the following target folders do not exist yet:{ANSI_RESET}"
        )
        for destination in missing_destinations:
            print(f"- {destination}")


def _add_source_folder(args):
    computer_id = _resolve_computer_id(args.computer_id)
    json_data = _load_config_json(args.config)

    profile = _find_profile_json(json_data, args.profile)
    if profile is None:
        print(f"Profile not found: {args.profile}")
        return

    computers = profile.get("computers")
    if not isinstance(computers, dict):
        print(f"Profile '{args.profile}' has invalid 'computers' structure")
        return

    computer_section = computers.get(computer_id)
    if computer_section is None:
        print(
            f"Computer section '{computer_id}' not found in profile '{args.profile}'. "
            "Use --add-profile to create it first."
        )
        return

    computer_section["source"] = args.add_source_folder
    destinations = computer_section.get("destinations")
    if not isinstance(destinations, list):
        computer_section["destinations"] = []

    _save_config_json(args.config, json_data)
    print(
        f"Updated source folder for profile '{args.profile}' "
        f"on computer '{computer_id}'."
    )


def _add_destination_folder(args):
    computer_id = _resolve_computer_id(args.computer_id)
    json_data = _load_config_json(args.config)

    profile = _find_profile_json(json_data, args.profile)
    if profile is None:
        print(f"Profile not found: {args.profile}")
        return

    computers = profile.get("computers")
    if not isinstance(computers, dict):
        print(f"Profile '{args.profile}' has invalid 'computers' structure")
        return

    computer_section = computers.get(computer_id)
    if computer_section is None:
        print(
            f"Computer section '{computer_id}' not found in profile '{args.profile}'. "
            "Use --add-profile to create it first."
        )
        return

    destinations = computer_section.get("destinations")
    if not isinstance(destinations, list):
        destinations = []
        computer_section["destinations"] = destinations

    if args.add_destination_folder in destinations:
        print(
            f"Destination already exists for profile '{args.profile}' "
            f"on computer '{computer_id}'."
        )
        return

    destinations.append(args.add_destination_folder)
    _save_config_json(args.config, json_data)
    print(
        f"Added destination folder to profile '{args.profile}' "
        f"on computer '{computer_id}'."
    )
    destination_path = Path(args.add_destination_folder)
    if not destination_path.exists() or not destination_path.is_dir():
        print(
            f"{ANSI_YELLOW}Warning: target folder does not exist yet: "
            f"{args.add_destination_folder}{ANSI_RESET}"
        )

def main():
    parser = ArgumentParser(description="A simple backup tool.")
    parser.add_argument(
        "--config",
        type=str,
        default="config.easybackup.json",
        help="Path to JSON backup configuration file",
    )
    parser.add_argument("--profile", type=str, help="Profile name")
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available profiles and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Apply backup without interactive overwrite confirmation",
    )
    parser.add_argument(
        "--showconflicts",
        action="store_true",
        default=True,
        help="Show textual diffs for files that would be overwritten",
    )
    parser.add_argument(
        "--no-showconflicts",
        dest="showconflicts",
        action="store_false",
        help="Disable textual diffs for files that would be overwritten",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Transfer data from destination folders back to source",
    )
    parser.add_argument(
        "--add-profile",
        type=str,
        help="Create a new profile for one computer section",
    )
    parser.add_argument(
        "--computer-id",
        type=str,
        help="Computer id to edit in config (defaults to current machine id)",
    )
    parser.add_argument(
        "--source-folder",
        type=str,
        help="Source folder path (used with --add-profile)",
    )
    parser.add_argument(
        "--destination-folder",
        action="append",
        default=[],
        help="Destination folder path (repeatable, used with --add-profile)",
    )
    parser.add_argument(
        "--add-source-folder",
        type=str,
        help="Set or create source folder for an existing profile/computer",
    )
    parser.add_argument(
        "--add-destination-folder",
        type=str,
        help="Add one destination folder to an existing profile/computer",
    )

    args = parser.parse_args()

    try:
        if args.add_profile:
            _add_profile(args)
            return
        if args.add_source_folder:
            if not args.profile:
                print("--profile is required with --add-source-folder")
                return
            _add_source_folder(args)
            return
        if args.add_destination_folder:
            if not args.profile:
                print("--profile is required with --add-destination-folder")
                return
            _add_destination_folder(args)
            return
    except ValueError as error:
        print(f"Invalid config: {error}")
        return

    try:
        backup_config = BackupConfig.from_file(args.config)
    except FileNotFoundError:
        print(f"Config file not found: {args.config}")
        return
    except ValueError as error:
        print(f"Invalid config: {error}")
        return

    if args.list_profiles:
        if not backup_config.profiles:
            print("No profiles configured.")
            return

        print("Available profiles:")
        for profile in backup_config.profiles:
            print(f"- {profile.name}")
        return

    if not args.profile:
        print("Please provide --profile or use --list-profiles")
        return

    profile = backup_config.get_profile(args.profile)
    if profile is None:
        print(f"Profile not found: {args.profile}")
        return

    try:
        computer_profile = profile.get_current_computer_profile()
    except ValueError as error:
        print(error)
        return

    valid_destinations = []
    missing_destinations = []
    for destination in computer_profile.destinations:
        if destination.exists() and destination.is_dir():
            valid_destinations.append(destination)
        else:
            missing_destinations.append(destination)

    if missing_destinations:
        print(
            f"{ANSI_YELLOW}Warning: the following destination folders do not exist "
            f"and will be skipped:{ANSI_RESET}"
        )
        for destination in missing_destinations:
            print(f"- {destination}")

    if not valid_destinations:
        print("No valid destination folders found for this profile. Backup cancelled.")
        return

    print(f"Profile: {profile.name}")
    print(f"Computer: {computer_profile.computer_id}")
    print(f"Source: {computer_profile.source}")
    print(f"Destinations: {', '.join(str(path) for path in valid_destinations)}")

    if args.restore:
        plan = build_restore_plan(computer_profile, destinations=valid_destinations)
        summary = summarize_restore_plan(plan)

        print(f"New files in source: {summary.new_files}")
        print(f"Files to overwrite in source: {summary.overwritten_files}")
        print(f"Unchanged files in source: {summary.unchanged_files}")
        if summary.destination_conflicts:
            print(
                f"{ANSI_YELLOW}Warning: {summary.destination_conflicts} file(s) differ "
                "between destinations; newest version will be used for each file."
                f"{ANSI_RESET}"
            )

        overwrite_actions = [
            action for action in plan if action.action_type == "overwrite"
        ]
        if overwrite_actions:
            print("\nWarning: the following source files will be overwritten:")
            for action in overwrite_actions:
                print(f"- {action.target_source_file}")

        if args.showconflicts and overwrite_actions:
            print("\nConflict details:")
            for action in overwrite_actions:
                print(
                    f"\n=== {action.target_source_file} "
                    f"(from {action.selected_destination_file}) ==="
                )
                diff_output = get_conflict_diff(
                    action.selected_destination_file,
                    action.target_source_file,
                )
                if diff_output:
                    print(format_diff_git_style(diff_output))
                else:
                    print("No textual differences to show.")

        if summary.actionable_files == 0:
            print("Nothing to restore. Source files are already up to date.")
            return

        if overwrite_actions and not args.force:
            answer = input("Proceed with restore? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Restore cancelled.")
                return

        result = execute_restore_plan(plan)
        print(
            f"Restore complete. Copied {result.copied_files} files "
            f"({result.overwritten_files} overwritten)."
        )
        return

    try:
        plan = build_backup_plan(computer_profile, destinations=valid_destinations)
    except ValueError as error:
        print(error)
        return

    summary = summarize_plan(plan)

    print(f"New files: {summary.new_files}")
    print(f"Files to overwrite: {summary.overwritten_files}")
    print(f"Unchanged files: {summary.unchanged_files}")

    overwrite_actions = [action for action in plan if action.action_type == "overwrite"]
    if overwrite_actions:
        print("\nWarning: the following files will be overwritten:")
        for action in overwrite_actions:
            print(f"- {action.destination_file}")

    if args.showconflicts and overwrite_actions:
        print("\nConflict details:")
        for action in overwrite_actions:
            print(f"\n=== {action.destination_file} ===")
            diff_output = get_conflict_diff(action.source_file, action.destination_file)
            if diff_output:
                print(format_diff_git_style(diff_output))
            else:
                print("No textual differences to show.")

    if summary.actionable_files == 0:
        print("Nothing to backup. All destination files are already up to date.")
        return

    if overwrite_actions and not args.force:
        answer = input("Proceed with backup? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Backup cancelled.")
            return

    result = execute_backup_plan(plan)
    print(
        f"Backup complete. Copied {result.copied_files} files "
        f"({result.overwritten_files} overwritten)."
    )


if __name__ == "__main__":    
    main()